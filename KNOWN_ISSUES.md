# Known issues

This file tracks community extensions whose CI failures are **not regressions on the Haybarn side** — usually upstream API drift, stale upstream test data, or platform-specific quirks. Entries here exist so the triage script can distinguish "as-expected failure" from a new regression that actually needs investigating.

The format below is a strict YAML-in-Markdown block so `scripts/triage.py` can parse it. Don't rearrange the fields per entry.

Schema per entry:

```yaml
extension: <name>           # must match the directory under extensions/
status: failing | flaky     # failing = consistently fails; flaky = passes some runs
platforms: [<arch>, ...]    # platforms it fails on; use ['*'] for "all"
since: YYYY-MM-DD           # when we first observed it
upstream_cause: <short text> # what's actually wrong (not "build fails" — WHY)
upstream_ref: <url or null>  # tracking issue, PR, etc., if any
last_checked: YYYY-MM-DD    # last time we verified the failure mode hasn't changed
```

## Entries

```yaml
- extension: prql
  status: failing
  platforms: ['*']
  since: 2026-05-16
  upstream_cause: |
    Pre-v1.5 DuckDB API drift. duckdb-prql at ref 0b41157 uses
    OperatorExtensionInfo, ParserExtensionInfo, ParserExtensionParseResult,
    ParserExtensionPlanResult, LogicalExtensionOperator — none of which
    exist in DuckDB v1.5.2. Would fail against vanilla DuckDB v1.5.2 too,
    not a Haybarn problem. The extension's maintainers would need to port
    to the post-v1.5 API for this to come unstuck.
  upstream_ref: null
  last_checked: 2026-05-16

- extension: scrooge
  status: flaky
  platforms: [windows_amd64]
  since: 2026-05-16
  upstream_cause: |
    Build succeeds. test/sql/scrooge/test_yahoo.test:36 fails on Windows
    with "Out of Range Error: Overflow in timestamp subtraction". Likely
    stale Yahoo test data: the test references a time range that has
    drifted past a Windows-time-arithmetic boundary. Linux/macOS pass.
    Could be quarantined to windows-only or upstream test refreshed.
  upstream_ref: null
  last_checked: 2026-05-16
```

## Adding a new entry

When a sweep surfaces a new failure that's _not_ a Haybarn-side regression:

1. Look at the actual error and decide if the cause is upstream-attributable (API drift, broken submodule, missing dep upstream forgot, stale test data, etc.).
2. Add a YAML entry above with the diagnostic.
3. If there's an upstream issue or PR tracking it, link it in `upstream_ref`.
4. Re-run `scripts/triage.py` to confirm the entry is recognized and the failure now classifies as "as-expected".

## Removing entries

A known-issue entry is "fixed" when:

- The upstream extension publishes a new pinned `repo.ref` in their `description.yml` that fixes the cause (the sync bot will pick it up automatically).
- Or we land a fork-and-patch via the descriptor's `haybarn_fork` / `haybarn_ref` escape hatch.

Once the next sweep passes for that extension, remove its entry here. Don't leave stale entries — they mask real regressions.
