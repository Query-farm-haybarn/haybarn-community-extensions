"""Chunk a `build_all.yml` invocation into batches.

Reasoning: with ~150 extensions × 12-platform matrix, a single `build_all.yml`
run can fan out to 1800 concurrent jobs and saturate org-wide GitHub Actions
concurrency. Split into batches of ~10 (default) and the workflow stays well
under the cap while still completing in reasonable wall-clock.

Usage:
    python3 scripts/create_build_all_invocation.py \\
        --batch_size=10 \\
        --duckdb_tag=v1.5.2 \\
        --duckdb_version=v1.5.2

The output is a series of `gh workflow run …` commands; pipe to `bash` to
fire them in sequence, or eyeball and run selectively.
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
    parser.add_argument('--duckdb_version', type=str, required=True,
                        help='DuckDB version hash or tag (engine version)')
    parser.add_argument('--duckdb_tag', type=str, required=True,
                        help='DuckDB version tag (URL segment)')
    parser.add_argument('--batch_size', type=int, default=10,
                        help='Number of extensions per workflow invocation')

    args = parser.parse_args()

    extensions = read_extension_list()
    batches = split_into_batches(extensions, args.batch_size)

    for batch in batches:
        json_str = json.dumps(batch).replace('"', "'")
        cmd = (
            f'gh workflow run build_all.yml '
            f'-f build_extensions="{json_str}" '
            f'-f duckdb_version={args.duckdb_version} '
            f'-f duckdb_tag={args.duckdb_tag} '
            f'-f deploy=true'
        )
        print(cmd)


if __name__ == '__main__':
    main()
