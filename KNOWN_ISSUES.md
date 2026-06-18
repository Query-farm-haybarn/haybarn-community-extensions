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

- extension: faiss
  status: failing
  platforms: [windows_amd64, windows_arm64, windows_amd64_mingw]
  since: 2026-06-17
  upstream_cause: |
    Windows link failure, not a Haybarn regression. faiss requires the
    fortran + omp toolchains and statically links LAPACK; on the MSVC
    toolchain the link dies with LNK1120 (14 unresolved externals) for
    libf2c/Fortran-runtime symbols (d_sign, pow_dd, s_cat, do_fio, ...)
    and "Could NOT find MKL". This is the well-known faiss-on-Windows
    LAPACK/f2c problem — upstream community-extensions has never shipped a
    faiss Windows binary either. Linux/macOS build fine. windows_amd64 is
    the confirmed MSVC failure; the whole Windows family is excluded as
    unsupported for this extension.
  upstream_ref: null
  last_checked: 2026-06-17

- extension: duck_hunt
  status: failing
  platforms: [windows_amd64]
  since: 2026-06-17
  upstream_cause: |
    Compiles cleanly; SQL logic tests fail on Windows only
    (test/sql/config_parser.test:817, test/sql/sample_files.test:148, and
    ignore_errors.test). Test-behavior differences on Windows (path /
    CRLF / file-glob semantics), not an MSVC-toolchain or Haybarn-pipeline
    issue — an upstream Windows build would hit the same test failures.
    Linux/macOS/wasm all pass. Disabled on windows_amd64 (the mingw leg
    was cancelled, not run).
  upstream_ref: null
  last_checked: 2026-06-17
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
