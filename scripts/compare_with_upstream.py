#!/usr/bin/env python3
"""Compare Haybarn build results against the upstream DuckDB community
extension repository.

For each (extension, platform) pair, this tool answers:

  - Did Haybarn build it?
    (success / failure / skipped / cancelled / not-attempted)
  - Does the upstream DuckDB community-extensions site host a binary?
    (HEAD https://community-extensions.duckdb.org/<ver>/<plat>/<ext>.duckdb_extension.gz)

Then categorizes each pair as:

  - both_success       — works on both
  - both_fail          — broken upstream too; safe to disable in Haybarn
  - haybarn_only_fail  — built upstream, fails here: a regression we own
  - haybarn_only_pass  — passes here, missing upstream (rare, usually
                         means upstream excluded the platform via the
                         descriptor's excluded_platforms)
  - not_attempted      — we never tried (excluded_platforms, etc.) and
                         upstream may or may not have it

Roll-up per extension into one of:

  - works              — at least one platform built; no all-platform fail
  - broken_upstream    — fails on all attempted platforms here AND
                         upstream has zero binaries for those platforms
                         (disable candidate)
  - haybarn_regression — fails on platforms where upstream succeeds
                         (deserves investigation, not a disable)
  - mixed              — some upstream-success / some upstream-fail

Usage:
  scripts/compare_with_upstream.py                  # uses latest build_all
  scripts/compare_with_upstream.py --run-id 12345   # specific run
  scripts/compare_with_upstream.py --duckdb-version v1.5.2  # default v1.5.2
  scripts/compare_with_upstream.py --format json    # machine-readable

Requires: gh CLI logged in to the org, network access to
community-extensions.duckdb.org. No external Python deps.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
import urllib.error
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

REPO = "Query-farm-haybarn/haybarn-community-extensions"
UPSTREAM_BASE = "https://community-extensions.duckdb.org"
BUILD_ALL_WORKFLOW = "build_all.yml"

# Job-name pattern of build_all fan-out children:
#   "build_all (h3) / build / Linux (linux_amd64, ubuntu-24.04, ...)"
#   "build_all (h3) / build / DuckDB-Wasm (wasm_eh, wasm32-emscripten, ...)"
PLAT_RE = re.compile(r"build_all \(([\w.-]+)\) / build / [\w.-]+ \(\s*([\w_]+)")


@dataclass
class CellResult:
    haybarn_status: str = "not-attempted"   # success | failure | skipped | cancelled | in_progress | not-attempted
    upstream_present: Optional[bool] = None # True | False | None (network error)

    @property
    def category(self) -> str:
        h = self.haybarn_status
        u = self.upstream_present
        if h == "success" and u is True:    return "both_success"
        if h == "failure" and u is False:   return "both_fail"
        if h == "failure" and u is True:    return "haybarn_only_fail"
        if h == "success" and u is False:   return "haybarn_only_pass"
        return "not_attempted"


@dataclass
class ExtensionRollup:
    name: str
    cells: dict[str, CellResult] = field(default_factory=dict)

    def category(self) -> str:
        cats = [c.category for c in self.cells.values()]
        attempted_plats = [c for c in self.cells.values()
                           if c.haybarn_status in ("success", "failure")]
        if not attempted_plats:
            return "untested"
        # Sort priority high-to-low: regression > broken_upstream > works > mixed
        any_haybarn_fail = any(c.haybarn_status == "failure" for c in attempted_plats)
        upstream_succeeded_on_failures = any(
            c.haybarn_status == "failure" and c.upstream_present is True
            for c in attempted_plats
        )
        all_attempted_fail_haybarn = all(c.haybarn_status == "failure" for c in attempted_plats)
        all_attempted_fail_upstream = all(
            c.haybarn_status == "failure" and c.upstream_present in (False, None)
            for c in attempted_plats
        )

        if upstream_succeeded_on_failures:
            return "haybarn_regression"
        if all_attempted_fail_haybarn and all_attempted_fail_upstream:
            return "broken_upstream"
        if any_haybarn_fail:
            return "mixed"
        return "works"


def gh_api_jobs(run_id: int) -> list[dict]:
    """Pull all jobs from a build_all run via the gh CLI."""
    out = subprocess.check_output(
        ["gh", "run", "view", str(run_id), "-R", REPO, "--json", "jobs"],
        text=True,
    )
    return json.loads(out)["jobs"]


def find_latest_build_all_run_id() -> int:
    out = subprocess.check_output(
        ["gh", "run", "list", "-R", REPO,
         "--workflow", BUILD_ALL_WORKFLOW,
         "--limit", "1", "--json", "databaseId"],
        text=True,
    )
    runs = json.loads(out)
    if not runs:
        sys.exit("no build_all runs found")
    return runs[0]["databaseId"]


def haybarn_status_per_cell(jobs: list[dict]) -> dict[tuple[str, str], str]:
    """Return {(extension, platform): status}. Status is conclusion if
    completed, else status. If multiple jobs for the same (ext, plat),
    'failure' wins over anything else (failures are the signal we care
    about; transient cancellations of duplicates shouldn't mask them)."""
    PRIORITY = {"failure": 4, "cancelled": 3, "in_progress": 2,
                "skipped": 1, "success": 0}
    out: dict[tuple[str, str], str] = {}
    for j in jobs:
        m = PLAT_RE.match(j["name"])
        if not m:
            continue
        ext, plat = m.group(1), m.group(2)
        status = j.get("conclusion") or j.get("status") or "?"
        prev = out.get((ext, plat))
        if prev is None or PRIORITY.get(status, 0) > PRIORITY.get(prev, 0):
            out[(ext, plat)] = status
    return out


def upstream_url(duckdb_version: str, plat: str, ext: str) -> str:
    return f"{UPSTREAM_BASE}/{duckdb_version}/{plat}/{ext}.duckdb_extension.gz"


# The upstream CDN 403s requests with the default urllib User-Agent;
# anything resembling a real client gets through.
_UA = "haybarn-compare/1.0"


def upstream_present(duckdb_version: str, plat: str, ext: str,
                     timeout: float = 6.0) -> Optional[bool]:
    """HEAD request to upstream. True if 200, False if 404, None on
    transport error so the caller can distinguish unknown from absent."""
    url = upstream_url(duckdb_version, plat, ext)
    req = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": _UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        # Other 4xx/5xx: treat as unknown (don't conflate with absence).
        return None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", type=int,
                    help="build_all run id to analyze (default: latest)")
    ap.add_argument("--duckdb-version", default="v1.5.2",
                    help="upstream version path segment (default: v1.5.2)")
    ap.add_argument("--workers", type=int, default=32,
                    help="parallel HEAD requests to upstream (default: 32)")
    ap.add_argument("--format", choices=("text", "markdown", "json"),
                    default="text")
    args = ap.parse_args(argv)

    run_id = args.run_id or find_latest_build_all_run_id()
    print(f"# Comparing build_all run {run_id} vs upstream "
          f"({args.duckdb_version})\n", file=sys.stderr)

    print("fetching haybarn job statuses…", file=sys.stderr)
    jobs = gh_api_jobs(run_id)
    haybarn = haybarn_status_per_cell(jobs)

    # Every (ext, plat) we want to probe upstream for. We include skipped/
    # cancelled too — upstream may still have those, useful signal.
    pairs = sorted(haybarn.keys())
    print(f"probing upstream for {len(pairs)} (extension, platform) pairs "
          f"with {args.workers} workers…", file=sys.stderr)

    rollup: dict[str, ExtensionRollup] = {}
    for ext, plat in pairs:
        rollup.setdefault(ext, ExtensionRollup(name=ext)).cells[plat] = \
            CellResult(haybarn_status=haybarn[(ext, plat)])

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(upstream_present, args.duckdb_version, plat, ext):
                (ext, plat) for ext, plat in pairs}
        for i, f in enumerate(as_completed(futs), 1):
            ext, plat = futs[f]
            rollup[ext].cells[plat].upstream_present = f.result()
            if i % 200 == 0:
                print(f"  …{i}/{len(pairs)}", file=sys.stderr)

    # Group extensions by rollup category
    by_cat: dict[str, list[ExtensionRollup]] = defaultdict(list)
    for ext in rollup.values():
        by_cat[ext.category()].append(ext)

    if args.format == "json":
        out = {
            cat: [
                {
                    "name": e.name,
                    "cells": {
                        plat: {
                            "haybarn": c.haybarn_status,
                            "upstream": c.upstream_present,
                            "category": c.category,
                        } for plat, c in sorted(e.cells.items())
                    },
                }
                for e in sorted(exts, key=lambda x: x.name)
            ]
            for cat, exts in sorted(by_cat.items())
        }
        json.dump(out, sys.stdout, indent=2)
        print()
        return 0

    # Text / Markdown
    md = args.format == "markdown"
    h2 = "## " if md else "=== "
    h2end = "" if md else " ==="

    print(f"\n{h2}Summary{h2end}")
    for cat in ["works", "mixed", "broken_upstream", "haybarn_regression", "untested"]:
        print(f"  {cat:<22} {len(by_cat.get(cat, []))}")

    for cat in ("broken_upstream", "haybarn_regression"):
        exts = by_cat.get(cat, [])
        if not exts:
            continue
        if cat == "broken_upstream":
            blurb = ("These fail on every attempted platform in Haybarn AND "
                     "have no upstream binary either — strong disable candidates.")
        else:
            blurb = ("These fail on at least one platform in Haybarn where "
                     "upstream successfully ships a binary — Haybarn-specific "
                     "regressions worth investigating.")
        print(f"\n{h2}{cat} ({len(exts)} extensions){h2end}")
        print(blurb + "\n")

        for e in sorted(exts, key=lambda x: x.name):
            print(f"  {e.name}")
            for plat, c in sorted(e.cells.items()):
                if c.haybarn_status in ("success", "not-attempted"):
                    continue
                up_marker = ("upstream:OK " if c.upstream_present is True
                             else "upstream:MISSING " if c.upstream_present is False
                             else "upstream:?    ")
                print(f"    {plat:<24} haybarn:{c.haybarn_status:<10} {up_marker}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
