"""Pull every descriptor from duckdb/community-extensions into this repo.

The "mirror" half of "mirror and rebuild." Designed to run from CI (see
.github/workflows/sync_from_upstream.yml) but is fully usable locally:

    git clone https://github.com/duckdb/community-extensions /tmp/upstream-ce
    python3 scripts/sync_from_upstream.py --upstream /tmp/upstream-ce

Behavior
========

For each `extensions/<name>/description.yml` in upstream:

  * If we don't carry that extension, copy the descriptor in (an "addition").
  * If we do carry it and our descriptor's `repo.ref` (or `repo.haybarn_ref`)
    differs from upstream's `repo.ref`, copy upstream's descriptor over ours
    (an "update").
  * If our descriptor has a `repo.haybarn_fork` / `repo.haybarn_ref`, the
    sync NEVER touches it — Haybarn-side patches stay intact and the
    operator can bump them by hand. We only update upstream's `repo.ref`
    field in our copy to keep the "what's upstream pointing at right now"
    signal accurate, but the actual build will continue to use the
    haybarn_fork/haybarn_ref pair.
  * The `repo.andium` field is stripped — DuckDB v1.4.4 doesn't matter to
    Haybarn (we're on v1.5.2). User confirmed: "we can ignore that."
  * The `repo.ref_next` field is preserved — it's the upstream maintainer's
    pre-release escape hatch and would matter if Haybarn ever tracks a
    pre-1.5 DuckDB. Today's `build.py` only consults it when
    DUCKDB_VERSION != DUCKDB_LATEST_STABLE, which is never in steady state.

For each `extensions/<name>/` in us but not in upstream, the descriptor is
left alone (we may have Haybarn-only entries) and a NOTE is emitted, not a
removal. Removals are a destructive op and humans should decide.

Output
======

A summary on stderr listing additions / updates / unchanged / haybarn-only,
plus a JSON object on stdout suitable for the PR body:

    {
      "additions": ["..."],
      "updates":   ["..."],
      "unchanged": <count>,
      "haybarn_only": ["..."]
    }
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import yaml


def load_descriptor(path: Path) -> dict:
    with path.open('r') as f:
        return yaml.safe_load(f) or {}


def dump_descriptor(path: Path, data: dict) -> None:
    # Preserve key ordering as much as PyYAML can — dict insertion order
    # carries through `default_flow_style=False`.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def strip_andium(desc: dict) -> dict:
    """Drop the DuckDB-v1.4.4 escape-hatch ref from a descriptor."""
    repo = desc.get('repo', {})
    if 'andium' in repo:
        del repo['andium']
    return desc


def merge_for_haybarn(upstream: dict, current: dict | None) -> tuple[dict, bool]:
    """Produce the descriptor to write, plus whether it differs from `current`.

    If `current` carries Haybarn-specific patch pins (`repo.haybarn_fork` /
    `repo.haybarn_ref`), those are preserved on top of upstream's values.
    """
    new = json.loads(json.dumps(upstream))   # deep copy via json round-trip
    new = strip_andium(new)

    if current and 'repo' in current:
        for key in ('haybarn_fork', 'haybarn_ref'):
            if key in current.get('repo', {}):
                new.setdefault('repo', {})[key] = current['repo'][key]

    changed = current is None or current != new
    return new, changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', required=True, type=Path,
                        help='Path to a checkout of duckdb/community-extensions')
    parser.add_argument('--us', default=Path('.'), type=Path,
                        help='Path to this repo (default: cwd)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Report what would change without writing')
    args = parser.parse_args()

    upstream_dir = args.upstream / 'extensions'
    us_dir = args.us / 'extensions'

    if not upstream_dir.is_dir():
        print(f"error: {upstream_dir} does not exist", file=sys.stderr)
        return 2
    us_dir.mkdir(exist_ok=True)

    upstream_names = sorted(p.name for p in upstream_dir.iterdir() if p.is_dir())
    us_names       = sorted(p.name for p in us_dir.iterdir() if p.is_dir())

    additions, updates, unchanged = [], [], 0
    haybarn_only = sorted(set(us_names) - set(upstream_names))

    for name in upstream_names:
        upstream_desc_path = upstream_dir / name / 'description.yml'
        us_desc_path       = us_dir / name / 'description.yml'

        if not upstream_desc_path.exists():
            # extensions/<name>/ exists upstream but no description.yml — odd,
            # but skip rather than misreport.
            continue

        upstream_desc = load_descriptor(upstream_desc_path)
        current_desc  = load_descriptor(us_desc_path) if us_desc_path.exists() else None

        new_desc, changed = merge_for_haybarn(upstream_desc, current_desc)

        if current_desc is None:
            additions.append(name)
        elif changed:
            updates.append(name)
        else:
            unchanged += 1
            continue

        if not args.dry_run:
            dump_descriptor(us_desc_path, new_desc)

    print(f"sync_from_upstream: +{len(additions)}  ~{len(updates)}  ={unchanged}  haybarn-only={len(haybarn_only)}",
          file=sys.stderr)
    if additions:
        print(f"  additions: {', '.join(additions)}", file=sys.stderr)
    if updates:
        print(f"  updates:   {', '.join(updates)}", file=sys.stderr)
    if haybarn_only:
        print(f"  haybarn-only (unchanged): {', '.join(haybarn_only)}", file=sys.stderr)

    summary = {
        'additions': additions,
        'updates': updates,
        'unchanged': unchanged,
        'haybarn_only': haybarn_only,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
