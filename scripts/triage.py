"""Triage community-extension CI runs against KNOWN_ISSUES.md.

Pulls recent `Community Extension Build` runs from GitHub, classifies each
extension's status, and contrasts against the known-issues registry. The goal
is to make "is the sweep going well?" a 30-second human review: scan for
🔥 NEW FAIL rows, ignore everything else.

Usage:
    python3 scripts/triage.py
    python3 scripts/triage.py --limit 500              # look at more runs
    python3 scripts/triage.py --json                   # machine output
    python3 scripts/triage.py --filter h3,prql,scrooge # specific extensions

Requires `gh` CLI authenticated and `pyyaml`.

Outputs a markdown table:

    Extension      Status            Platforms failing     Notes
    ────────────── ───────────────── ───────────────────── ──────────────
    h3             ✓ as-expected
    prql           ✓ as-expected     all                   API drift
    scrooge        ⚠ as-expected     windows_amd64         yahoo flake
    duckpgq        🔥 NEW FAIL       linux_amd64,osx_arm64 ← investigate

Exit code: 0 if no new failures, 1 if any 🔥 NEW FAIL rows present.
This makes the script easy to drop into a check / cron / dashboard later.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

REPO = "Query-farm-haybarn/haybarn-community-extensions"
KNOWN_ISSUES_PATH = Path(__file__).resolve().parent.parent / "KNOWN_ISSUES.md"


def gh(args: list[str]) -> str:
    """Run `gh` and return stdout, raising on non-zero exit."""
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(f"gh {' '.join(args)} failed:\n{result.stderr}\n")
        sys.exit(result.returncode)
    return result.stdout


def load_known_issues() -> dict[str, dict[str, Any]]:
    """Parse the YAML block inside KNOWN_ISSUES.md into {ext_name: entry}.

    Falls back gracefully if the file isn't there yet.
    """
    if not KNOWN_ISSUES_PATH.exists():
        return {}
    text = KNOWN_ISSUES_PATH.read_text()
    # Pull each ```yaml...``` block and parse; concat lists.
    blocks = re.findall(r"```yaml\n(.*?)\n```", text, re.DOTALL)
    entries: list[dict[str, Any]] = []
    for block in blocks:
        parsed = yaml.safe_load(block)
        if isinstance(parsed, list):
            entries.extend(parsed)
    return {e["extension"]: e for e in entries if e and "extension" in e}


def fetch_recent_runs(limit: int) -> list[dict[str, Any]]:
    """Recent Community Extension Build runs, completed only."""
    raw = gh(
        [
            "run",
            "list",
            "--repo",
            REPO,
            "--workflow",
            "Community Extension Build",
            "--limit",
            str(limit),
            "--status",
            "completed",
            "--json",
            "databaseId,conclusion,createdAt,displayTitle,headSha",
        ]
    )
    return json.loads(raw)


def extension_for_run(run_id: int) -> str | None:
    """Map a CI run to the extension it built by reading prepare's env."""
    # The displayTitle includes the run-name we set (🐤 <ext>) when dispatched,
    # but historical runs predate that. Fall back to scanning the prepare job's
    # logs for COMMUNITY_EXTENSION_NAME=…
    jobs_json = gh(
        [
            "api",
            f"repos/{REPO}/actions/runs/{run_id}/jobs",
            "--jq",
            '.jobs[] | select(.name=="prepare") | .id',
        ]
    ).strip()
    if not jobs_json:
        return None
    prepare_job_id = jobs_json.splitlines()[0]
    log = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{REPO}/actions/jobs/{prepare_job_id}/logs",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    m = re.search(r"COMMUNITY_EXTENSION_NAME=([A-Za-z0-9_]+)", log)
    return m.group(1) if m else None


def per_platform_results(run_id: int) -> dict[str, str]:
    """Map platform → conclusion for the build legs of a run."""
    raw = gh(
        [
            "api",
            f"repos/{REPO}/actions/runs/{run_id}/jobs",
            "--jq",
            '[.jobs[] | select(.name | startswith("build / ")) | {name, conclusion}]',
        ]
    )
    out: dict[str, str] = {}
    for j in json.loads(raw):
        # name is e.g. "build / Linux (linux_amd64, ubuntu-24.04, ...)"
        m = re.search(r"\b(linux_amd64|linux_arm64|linux_amd64_musl|linux_arm64_musl|osx_amd64|osx_arm64|windows_amd64|windows_amd64_mingw|wasm_mvp|wasm_eh|wasm_threads)\b", j["name"])
        if m:
            out[m.group(1)] = j["conclusion"] or "?"
    return out


def classify(ext: str, platforms_failed: list[str], known: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """Return (icon, label) for an extension given its actual platform failures.

    Icons map to triage actions:
      ✓  fully green
      ✓  as-expected (all failures match KNOWN_ISSUES.md)
      ⚠  partial-as-expected (some failures match known, some don't)
      🔥 NEW FAIL (failures not in KNOWN_ISSUES.md)
    """
    if not platforms_failed:
        return ("✓", "all green")

    entry = known.get(ext)
    if entry is None:
        return ("🔥", "NEW FAIL — not in KNOWN_ISSUES.md")

    expected_platforms = set(entry.get("platforms", []))
    actual = set(platforms_failed)

    # ['*'] in known_issues means "fails on every platform" — any failures match.
    if "*" in expected_platforms:
        return ("✓", "as-expected (all platforms)")

    unexpected = actual - expected_platforms
    if not unexpected:
        return ("✓", "as-expected")
    return ("⚠", f"partial — NEW on {','.join(sorted(unexpected))}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=200, help="Number of recent runs to consider")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a markdown table")
    parser.add_argument("--filter", type=str, default=None, help="Comma-separated extension names to limit the report to")
    args = parser.parse_args()

    known = load_known_issues()
    runs = fetch_recent_runs(args.limit)

    # Restrict to extensions that currently have a descriptor — phantom CI
    # runs for renamed/deleted extensions (e.g. `quack` → `waddle`) would
    # otherwise show up as "🔥 NEW FAIL" perpetually.
    descriptor_root = Path(__file__).resolve().parent.parent / "extensions"
    live_extensions = {p.name for p in descriptor_root.iterdir() if (p / "description.yml").exists()}

    # Keep only the LATEST meaningful run per extension. Runs are returned in
    # reverse-chronological order so the first one we see for each extension
    # is the freshest. Skip `cancelled` runs entirely — user-initiated
    # cancellation produces partial leg-level "failure" results that the
    # classifier would otherwise read as regressions.
    latest_run_per_ext: dict[str, dict[str, Any]] = {}
    for run in runs:
        if run["conclusion"] == "cancelled":
            continue
        # Display title for dispatched runs is "🐤 <ext>" after recent run-name fix.
        m = re.match(r"🐤\s+(\S+)", run["displayTitle"])
        if m:
            ext = m.group(1)
        else:
            # Fall back to log-scanning the prepare job. Slower; only needed
            # for runs predating the run-name change.
            ext = extension_for_run(run["databaseId"])
        if not ext or ext in latest_run_per_ext:
            continue
        if ext not in live_extensions:
            # Phantom: descriptor was renamed/removed. Skip.
            continue
        latest_run_per_ext[ext] = run

    only = set(args.filter.split(",")) if args.filter else None

    report: list[dict[str, Any]] = []
    for ext, run in sorted(latest_run_per_ext.items()):
        if only and ext not in only:
            continue
        platforms = per_platform_results(run["databaseId"])
        failed = sorted([p for p, c in platforms.items() if c == "failure"])
        cancelled = sorted([p for p, c in platforms.items() if c == "cancelled"])
        succeeded = sorted([p for p, c in platforms.items() if c == "success"])
        icon, label = classify(ext, failed, known)
        report.append(
            {
                "extension": ext,
                "icon": icon,
                "label": label,
                "failed": failed,
                "cancelled": cancelled,
                "succeeded": succeeded,
                "run_id": run["databaseId"],
                "run_conclusion": run["conclusion"],
                "known_cause": (known.get(ext) or {}).get("upstream_cause", "").strip().splitlines()[0] if known.get(ext) else "",
            }
        )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        # Markdown table
        widths = {
            "extension": max(9, max((len(r["extension"]) for r in report), default=9)),
            "status": 22,
            "failed": max(20, max((len(",".join(r["failed"])) for r in report), default=20)),
            "notes": 50,
        }
        header = (
            f"{'Extension':<{widths['extension']}}  "
            f"{'Status':<{widths['status']}}  "
            f"{'Platforms failing':<{widths['failed']}}  "
            f"Notes"
        )
        print(header)
        print("─" * len(header))
        for r in report:
            status = f"{r['icon']} {r['label']}"
            failed_str = ",".join(r["failed"]) if r["failed"] else ""
            print(
                f"{r['extension']:<{widths['extension']}}  "
                f"{status:<{widths['status']}}  "
                f"{failed_str:<{widths['failed']}}  "
                f"{r['known_cause']}"
            )
        print()
        n_new = sum(1 for r in report if r["icon"] == "🔥")
        n_total = len(report)
        print(f"Summary: {n_total} extensions classified · 🔥 {n_new} regressions")

    # Exit code mirrors the verdict: nonzero if any new failures.
    return 1 if any(r["icon"] == "🔥" for r in report) else 0


if __name__ == "__main__":
    sys.exit(main())
