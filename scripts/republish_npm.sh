#!/usr/bin/env bash
# Dispatch republish_npm.yml for every extension that built SUCCESSFULLY in a
# given window — publishing its npm leaves + meta from the build run's retained
# per-arch artifacts, WITHOUT rebuilding.
#
# Why: a bulk sweep (build_all.yml) runs with publish_registries=false, so the
# binaries land on R2 but the npm packages are never produced. The build
# artifacts are still retained on each run, so republish_npm.yml can stage +
# publish the npm leaves straight from them.
#
# Usage:
#   scripts/republish_npm.sh [SINCE_ISO8601] [REF]
#     SINCE   only consider build.yml runs created after this UTC timestamp
#             (default: today 00:00:00Z)
#     REF     branch/ref the republish_npm.yml workflow lives on (default: main)
#
# Re-running is safe: npm publish treats an already-published version as SKIP.
set -uo pipefail

REPO="${REPO:-Query-farm-haybarn/haybarn-community-extensions}"
SINCE="${1:-$(date -u +%Y-%m-%dT00:00:00Z)}"
REF="${2:-main}"
PAR="${PAR:-6}"   # max concurrent dispatches (stay under GH secondary limits)

echo "Resolving successful build.yml runs in $REPO since $SINCE …"

# One line per extension: "<ext> <most-recent-successful-run-id>".
mapfile -t ROWS < <(
  gh run list --repo "$REPO" --workflow build.yml --event workflow_dispatch -L 400 \
    --json databaseId,conclusion,displayTitle,createdAt \
    --jq "[.[]|select(.createdAt > \"$SINCE\" and .conclusion==\"success\")]
          | group_by(.displayTitle) | map(max_by(.createdAt))
          | .[] | \"\(.displayTitle|sub(\"^🐤 \";\"\")) \(.databaseId)\""
)

n=${#ROWS[@]}
if [[ "$n" -eq 0 ]]; then echo "No successful runs found."; exit 0; fi
echo "Dispatching republish_npm.yml for $n extension(s) (ref=$REF, ${PAR}-way)…"

tmp=$(mktemp)
c=0
for row in "${ROWS[@]}"; do
  ext="${row%% *}"
  rid="${row##* }"
  (
    if gh workflow run republish_npm.yml --repo "$REPO" --ref "$REF" \
        -f extension_name="$ext" -f source_run_id="$rid" >/dev/null 2>&1; then
      echo "OK $ext"
    else
      echo "FAIL $ext"
    fi
  ) >>"$tmp" &
  c=$((c+1)); [[ $((c % PAR)) -eq 0 ]] && wait
done
wait

echo "  ok=$(grep -c '^OK' "$tmp" || true)  fail=$(grep -c '^FAIL' "$tmp" || true)"
grep '^FAIL' "$tmp" || true
rm -f "$tmp"
