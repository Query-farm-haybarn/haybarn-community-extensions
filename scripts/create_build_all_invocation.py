"""Chunk a `build_all.yml` invocation into batches.

Reasoning: with ~150 extensions × 12-platform matrix, a single `build_all.yml`
run can fan out to 1800 concurrent jobs and saturate org-wide GitHub Actions
concurrency. Split into batches of ~10 (default) and the workflow stays well
under the cap while still completing in reasonable wall-clock.

Usage:
    python3 scripts/create_build_all_invocation.py --batch_size=10 | bash

The output is a series of `gh workflow run …` commands; pipe to `bash` to
fire them in sequence, or eyeball and run selectively.

Two things that bite if you override the defaults:

  * duckdb_tag MUST stay empty for the Haybarn engine. The ci-tools
    `set_duckdb_tag` target runs `cd duckdb && git tag $(DUCKDB_TAG)`, and the
    Haybarn engine checkout already carries its release tag (e.g. v1.5.2), so
    passing a tag makes every platform fail with `fatal: tag already exists`.
    The engine self-reports its version from its own tag; the URL segment is
    driven by duckdb_version, not duckdb_tag. So we never pass duckdb_tag.

  * R2 is the source of truth for a bulk rebuild. npm/PyPI publish is a
    decoupled second step, so the sweep runs with publish_registries=false
    (build_all.yml's default) unless --publish_registries is given.
"""

import argparse
import glob
import json

import yaml


def read_extension_list():
    extensions = []
    for path in sorted(glob.glob('./extensions/*/description.yml')):
        with open(path) as f:
            desc = yaml.safe_load(f)
            extensions.append(desc['extension']['name'])
    return extensions


def split_into_batches(lst, batch_size):
    return [lst[i:i + batch_size] for i in range(0, len(lst), batch_size)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duckdb_version', type=str, default='',
                        help='Engine version / URL segment. Empty lets '
                             'build.yml default it to v1.5.2.')
    parser.add_argument('--batch_size', type=int, default=10,
                        help='Number of extensions per workflow invocation')
    parser.add_argument('--publish_registries', action='store_true',
                        help='Also publish npm/PyPI (default: R2 only).')

    args = parser.parse_args()

    extensions = read_extension_list()
    batches = split_into_batches(extensions, args.batch_size)

    for batch in batches:
        json_str = json.dumps(batch).replace('"', "'")
        cmd = (
            f'gh workflow run build_all.yml '
            f'-f build_extensions="{json_str}" '
            f'-f deploy=true '
            f'-f publish_registries={str(args.publish_registries).lower()}'
        )
        if args.duckdb_version:
            cmd += f' -f duckdb_version={args.duckdb_version}'
        # Intentionally never pass duckdb_tag — see module docstring.
        print(cmd)


if __name__ == '__main__':
    main()
