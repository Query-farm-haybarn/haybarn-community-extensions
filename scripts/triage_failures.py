#!/usr/bin/env python3
"""Triage extensions that fail to build against Haybarn.

For each extension whose most-recent build has any failing platform:

  1. Probe upstream community-extensions.duckdb.org for the same
     (extension, platform) binary at v1.5.2.
  2. Categorize the extension:

     - works              every attempted platform succeeded in Haybarn
                          (nothing to triage)
     - mixed              some platform legs fail in Haybarn AND
                          upstream — partial coverage on both sides
     - broken_upstream    every Haybarn failure also lacks an upstream
                          binary — not a Haybarn-specific issue. Action:
                          disable in our catalog + file a friendly
                          upstream issue ("we'd love to include this
                          when v1.5.2 builds cleanly")
     - haybarn_regression at least one Haybarn failure that upstream
                          DOES ship — a Haybarn-specific regression to
                          investigate

Data source: the haybarn-status worker's /api/r/<tag> JSON, which
aggregates the latest build.yml runs across the catalog (and folds in
build_all when applicable). The triage tool intentionally goes through
the status worker rather than re-deriving from gh-actions raw, because
the status worker already does the run-merging + cancellation handling.

Two output modes:

  --format text    human-readable summary
  --format json    machine-readable structured output written to
                   stdout (or --out <path>)

Usage:
  scripts/triage_failures.py                  # text summary to stdout
  scripts/triage_failures.py --format json --out /tmp/triage.json

Requires: Python 3.10+, stdlib only.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

STATUS_API = "https://haybarn-status.query.farm/api/r"
UPSTREAM_BASE = "https://community-extensions.duckdb.org"
USER_AGENT = "haybarn-triage/1.0"
DEFAULT_DUCKDB_VERSION = "v1.5.2"

# Native platforms that have both a Haybarn build AND an upstream
# community-extensions binary URL. Wasm uses a different file extension.
NATIVE_PLATS = (
    "linux_amd64", "linux_arm64", "linux_amd64_musl", "linux_arm64_musl",
    "osx_amd64", "osx_arm64",
    "windows_amd64", "windows_arm64", "windows_amd64_mingw",
)
WASM_PLATS = ("wasm_mvp", "wasm_eh", "wasm_threads")

# job.name shape we get back from the status worker (the inner part
# preserves the original Actions run.name structure):
#   "Linux (linux_amd64, ubuntu-24.04, x64-linux-release, ...)"
#   "DuckDB-Wasm (wasm_eh, wasm32-emscripten, ...)"
JOB_PLAT_RE = re.compile(r"\(\s*([\w_]+)\b")


@dataclasses.dataclass
class Cell:
    plat: str
    haybarn_status: str           # "success" | "failure" | "cancelled" | ...
    upstream_exists: Optional[bool] = None  # True / False / None=unknown
    @property
    def category(self) -> str:
        s, u = self.haybarn_status, self.upstream_exists
        if s == "success" and u is True:    return "both_success"
        if s == "success" and u is False:   return "haybarn_only_pass"
        if s == "failure" and u is True:    return "haybarn_only_fail"
        if s == "failure" and u is False:   return "both_fail"
        return "indeterminate"


@dataclasses.dataclass
class Ext:
    name: str
    cells: list[Cell] = dataclasses.field(default_factory=list)

    def overall(self) -> str:
        attempted = [c for c in self.cells if c.haybarn_status in ("success", "failure")]
        if not attempted:
            return "untested"
        if all(c.haybarn_status == "success" for c in attempted):
            return "works"
        any_regression = any(c.category == "haybarn_only_fail" for c in attempted)
        if any_regression:
            return "haybarn_regression"
        all_fail = all(c.haybarn_status == "failure" for c in attempted)
        all_upstream_missing = all(c.upstream_exists in (False, None)
                                   for c in attempted if c.haybarn_status == "failure")
        if all_fail and all_upstream_missing:
            return "broken_upstream"
        return "mixed"


def fetch_status_view(tag: str) -> dict:
    url = f"{STATUS_API}/{urllib.parse.quote(tag)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def parse_haybarn_state(view: dict) -> dict[str, dict[str, str]]:
    """Returns {ext_name: {platform: status}}.
    status is "success" | "failure" | "cancelled" | "in_progress" | ..."""
    out: dict[str, dict[str, str]] = {}
    community = ((view.get("sidePanel") or {}).get("community") or {})
    for ext in (community.get("extensions") or []):
        ext_name = ext.get("name")
        if not ext_name:
            continue
        by_plat = out.setdefault(ext_name, {})
        for j in (ext.get("jobs") or []):
            m = JOB_PLAT_RE.search(j.get("name", ""))
            if not m:
                continue
            plat = m.group(1)
            status = j.get("conclusion") or j.get("status") or "?"
            # Prefer "failure" over other states if we see multiple
            # for the same platform.
            if by_plat.get(plat) == "failure":
                continue
            by_plat[plat] = status
    return out


def upstream_url(duckdb_version: str, plat: str, ext: str) -> str:
    if plat.startswith("wasm_"):
        return f"{UPSTREAM_BASE}/{duckdb_version}/{plat}/{ext}.duckdb_extension.wasm"
    return f"{UPSTREAM_BASE}/{duckdb_version}/{plat}/{ext}.duckdb_extension.gz"


def upstream_present(ext: str, plat: str, duckdb_version: str,
                     timeout: float = 8.0) -> Optional[bool]:
    url = upstream_url(duckdb_version, plat, ext)
    req = urllib.request.Request(
        url, method="HEAD",
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        return None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def probe_upstream_bulk(pairs: list[tuple[str, str]],
                        duckdb_version: str,
                        workers: int = 24) -> dict[tuple[str, str], bool]:
    """Parallel HEAD probes. Returns {(ext, plat): True/False/None}."""
    out: dict[tuple[str, str], Optional[bool]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(upstream_present, e, p, duckdb_version): (e, p)
                for e, p in pairs}
        done = 0
        for f in as_completed(futs):
            k = futs[f]
            out[k] = f.result()
            done += 1
            if done % 50 == 0:
                print(f"  …probed {done}/{len(pairs)} upstream URLs", file=sys.stderr)
    return out


def build_extensions(haybarn_state: dict[str, dict[str, str]],
                     duckdb_version: str) -> list[Ext]:
    """Materialise Ext objects with upstream probes filled in."""
    # Pairs to probe: any (ext, plat) where Haybarn FAILED (we care
    # about upstream presence for failure attribution). We also probe
    # successful pairs lightly so the "mixed / haybarn_only_pass"
    # category fills in correctly — but skip cancelled/in-progress to
    # keep the probe count reasonable.
    probe_pairs: list[tuple[str, str]] = []
    for ext_name, plats in haybarn_state.items():
        for plat, status in plats.items():
            if status in ("success", "failure"):
                probe_pairs.append((ext_name, plat))
    print(f"upstream probes: {len(probe_pairs)} (ext, plat) pairs",
          file=sys.stderr)
    upstream_map = probe_upstream_bulk(probe_pairs, duckdb_version)

    extensions: list[Ext] = []
    for ext_name in sorted(haybarn_state):
        plats = haybarn_state[ext_name]
        cells = []
        for plat in sorted(plats):
            cells.append(Cell(
                plat=plat,
                haybarn_status=plats[plat],
                upstream_exists=upstream_map.get((ext_name, plat)),
            ))
        extensions.append(Ext(name=ext_name, cells=cells))
    return extensions


def descriptor_repo(extension: str, descriptors_dir: str) -> str:
    """Return the descriptor's `repo.github` value (owner/repo) or ''
    for use in the issue-filing follow-up."""
    import pathlib
    try:
        import yaml
    except ImportError:
        return ""
    p = pathlib.Path(descriptors_dir) / extension / "description.yml"
    if not p.is_file():
        return ""
    try:
        d = yaml.safe_load(p.read_text()) or {}
    except Exception:
        return ""
    return ((d.get("repo") or {}).get("github") or "").strip()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="haybarn-v1.5.2-rc10",
                    help="Status worker rc tag to source data from "
                         "(default: %(default)s).")
    ap.add_argument("--duckdb-version", default=DEFAULT_DUCKDB_VERSION,
                    help="Upstream path segment (default: %(default)s).")
    ap.add_argument("--descriptors-dir",
                    default="extensions",
                    help="Path to community-extensions/extensions/ for "
                         "looking up each extension's source repo "
                         "(default: %(default)s).")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument("--out", default=None,
                    help="JSON: write to this path instead of stdout.")
    args = ap.parse_args(argv)

    print(f"fetching status view for {args.tag}…", file=sys.stderr)
    view = fetch_status_view(args.tag)
    haybarn_state = parse_haybarn_state(view)
    print(f"  found {len(haybarn_state)} extensions in the matrix",
          file=sys.stderr)

    extensions = build_extensions(haybarn_state, args.duckdb_version)

    by_cat: dict[str, list[Ext]] = defaultdict(list)
    for e in extensions:
        by_cat[e.overall()].append(e)

    if args.format == "json":
        payload = {
            "tag": args.tag,
            "duckdb_version": args.duckdb_version,
            "extensions_probed": len(extensions),
            "categories": {
                cat: [
                    {
                        "name": e.name,
                        "repo": descriptor_repo(e.name, args.descriptors_dir),
                        "cells": [
                            {
                                "plat": c.plat,
                                "haybarn": c.haybarn_status,
                                "upstream": c.upstream_exists,
                                "category": c.category,
                            } for c in e.cells
                        ],
                    } for e in by_cat.get(cat, [])
                ]
                for cat in sorted(by_cat)
            },
        }
        out_text = json.dumps(payload, indent=2)
        if args.out:
            import pathlib
            pathlib.Path(args.out).write_text(out_text)
            print(f"wrote {args.out}", file=sys.stderr)
        else:
            print(out_text)
        return 0

    # Text summary.
    print()
    print("=" * 72)
    print(f"  Triage summary for {args.tag}")
    print("=" * 72)
    for cat in ("works", "mixed", "broken_upstream", "haybarn_regression", "untested"):
        print(f"  {cat:<22} {len(by_cat.get(cat, []))}")
    print()

    for cat in ("broken_upstream", "haybarn_regression"):
        bucket = by_cat.get(cat, [])
        if not bucket:
            continue
        blurb = {
            "broken_upstream":
                "Every Haybarn-side failure also lacks an upstream binary — "
                "not a Haybarn-specific issue. Disable in catalog + open "
                "friendly upstream issue.",
            "haybarn_regression":
                "Haybarn-side failure where upstream DOES ship a binary "
                "— a Haybarn-specific regression worth investigating.",
        }[cat]
        print(f"--- {cat} ({len(bucket)} extensions) " + "-" * (72 - 4 - len(cat) - 14))
        print(blurb + "\n")
        for ext in bucket:
            repo = descriptor_repo(ext.name, args.descriptors_dir)
            print(f"  {ext.name}  ({repo})")
            for c in ext.cells:
                if c.haybarn_status in ("success",):
                    continue
                up = ("upstream:OK" if c.upstream_exists is True else
                      "upstream:MISSING" if c.upstream_exists is False else
                      "upstream:?")
                print(f"    {c.plat:<24} haybarn:{c.haybarn_status:<12} {up}")
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
