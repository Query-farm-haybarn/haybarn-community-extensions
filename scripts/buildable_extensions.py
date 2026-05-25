#!/usr/bin/env python3
"""Print the extensions the build pipeline will actually produce binaries for.

An extension is "fully disabled" — and therefore skipped by the Makefile's
dispatch/list targets — when its descriptor excludes *every* build platform.
That's the Haybarn convention for "doesn't build yet" (see commit
"disable 26 extensions that don't yet build against DuckDB 1.5.2"): set
`extension.excluded_platforms` to the full platform set plus a Haybarn-prefixed
comment block. Dispatching such an extension yields an empty build matrix — a
wasted no-op CI run — so we filter those here.

Only `excluded_platforms` is consulted (matching `scripts/build.py`'s
`COMMUNITY_EXTENSION_EXCLUDE_PLATFORMS`). `opt_in_platforms` narrowing is NOT
treated as a disable: an opt-in extension still builds on its opt-in archs.

Usage:
    python3 scripts/buildable_extensions.py              # newline-separated names
    python3 scripts/buildable_extensions.py --json-array # ['a','b',...] (build_all matrix shape)
    python3 scripts/buildable_extensions.py --disabled   # invert: list the skipped ones

The platform set mirrors the upstream `_extension_distribution.yml` build matrix
that haybarn-extension-ci-tools drives. Keep it in sync if that matrix changes;
adding a platform here only affects whether an extension counts as *fully*
excluded (it must exclude every platform listed below to be skipped).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

# The full build matrix. An extension whose excluded_platforms is a superset of
# this set builds nothing, so we consider it disabled.
ALL_PLATFORMS = frozenset({
    "linux_amd64", "linux_arm64", "linux_amd64_musl", "linux_arm64_musl",
    "osx_amd64", "osx_arm64",
    "windows_amd64", "windows_arm64", "windows_amd64_mingw",
    "wasm_mvp", "wasm_eh", "wasm_threads",
})


def excluded_set(desc: dict) -> frozenset[str]:
    raw = (desc.get("extension") or {}).get("excluded_platforms") or ""
    # Descriptors use ';' but tolerate ',' and stray whitespace.
    return frozenset(p for p in raw.replace(",", ";").split(";") if p.strip())


def is_disabled(desc: dict) -> bool:
    return ALL_PLATFORMS.issubset(excluded_set(desc))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--extensions-dir", default=Path("extensions"), type=Path)
    ap.add_argument("--json-array", action="store_true",
                    help="emit ['a','b',...] for the build_all matrix instead of a newline list")
    ap.add_argument("--disabled", action="store_true",
                    help="invert: list the fully-disabled (skipped) extensions instead")
    args = ap.parse_args()

    names = []
    for d in sorted(p for p in args.extensions_dir.iterdir() if p.is_dir()):
        desc_path = d / "description.yml"
        if not desc_path.exists():
            continue
        with desc_path.open() as f:
            desc = yaml.safe_load(f) or {}
        if is_disabled(desc) == args.disabled:
            names.append(d.name)

    if args.json_array:
        print("[" + ",".join(f"'{n}'" for n in names) + "]")
    else:
        for n in names:
            print(n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
