#!/usr/bin/env bash
# Bump an extension's pinned git ref in extensions/<name>/description.yml to the
# current HEAD of its upstream branch, then commit with the house-style message:
#
#     <name>: bump to <branch> <short-sha> (<upstream commit subject(s)>)
#
# Usage:
#   scripts/bump_extension_ref.sh <extension>          # bump from origin's default branch (main)
#   scripts/bump_extension_ref.sh <extension> <branch> # bump from a specific branch
#   scripts/bump_extension_ref.sh vgi
#
# Options (env):
#   NO_COMMIT=1   Update description.yml but skip the git commit.
#
# Requires: git, gh (authenticated), python3 (all already used elsewhere in this repo).

set -eo pipefail

ext="${1:?usage: bump_extension_ref.sh <extension> [branch]}"
branch="${2:-main}"

repo_root="$(git rev-parse --show-toplevel)"
desc="$repo_root/extensions/$ext/description.yml"

[ -f "$desc" ] || { echo "error: $desc not found" >&2; exit 1; }

# Pull repo slug (owner/name) and current ref straight out of description.yml.
github=$(grep -E '^[[:space:]]*github:' "$desc" | head -1 | sed -E 's/.*github:[[:space:]]*//; s/[[:space:]]*$//')
old_ref=$(grep -E '^[[:space:]]*ref:'    "$desc" | head -1 | sed -E 's/.*ref:[[:space:]]*//; s/[[:space:]]*$//')

[ -n "$github" ]  || { echo "error: no repo.github in $desc" >&2; exit 1; }
[ -n "$old_ref" ] || { echo "error: no repo.ref in $desc"    >&2; exit 1; }

# Resolve the upstream branch HEAD.
new_ref=$(git ls-remote "https://github.com/$github" "$branch" | awk '{print $1}')
[ -n "$new_ref" ] || { echo "error: could not resolve $github@$branch" >&2; exit 1; }

if [ "$new_ref" = "$old_ref" ]; then
    echo "$ext already at $branch ${new_ref:0:7} — nothing to do."
    exit 0
fi

echo "$ext: $github  ${old_ref:0:7} -> ${new_ref:0:7}  (branch $branch)"

# Collect the upstream commit subjects we're pulling in, for the commit message.
# Falls back to just the new HEAD subject if the compare API can't range them.
subjects=$(gh api "repos/$github/compare/$old_ref...$new_ref" \
             --jq '[.commits[].commit.message | split("\n")[0]] | join("; ")' 2>/dev/null || true)
[ -n "$subjects" ] || subjects=$(gh api "repos/$github/commits/$new_ref" \
             --jq '.commit.message | split("\n")[0]' 2>/dev/null || echo "ref bump")

# Rewrite only the ref line (preserve the manually-maintained version field).
old_ref="$old_ref" new_ref="$new_ref" desc="$desc" python3 - <<'PY'
import os, re
p = os.environ["desc"]
s = open(p).read()
s = s.replace(f"ref: {os.environ['old_ref']}", f"ref: {os.environ['new_ref']}", 1)
open(p, "w").write(s)
PY

if [ -n "$NO_COMMIT" ]; then
    echo "NO_COMMIT set — description.yml updated, not committed."
    exit 0
fi

git -C "$repo_root" add "extensions/$ext/description.yml"
git -C "$repo_root" commit -m "$(printf '%s: bump to %s %s (%s)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>' \
    "$ext" "$branch" "${new_ref:0:7}" "$subjects")"
