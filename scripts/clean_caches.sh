#!/usr/bin/env bash
#
# Purge Cloudflare CDN entries for files matching a pattern in the
# haybarn-community-extensions R2 bucket.
#
# Differences from upstream:
#   - Cloudflare zone ID is query.farm's zone (set below).
#   - Purge URL targets the haybarn-community-extensions.query.farm subdomain
#     (host is plain `*.query.farm`, no `<endpoint>.duckdb.org`).
#   - Cloudfront block dropped — Haybarn doesn't use Cloudfront; Cloudflare's
#     CDN is the only edge cache to purge.
#
# Args:
#   $1  R2 bucket name        (e.g. haybarn-community-extensions)
#   $2  CDN subdomain prefix  (e.g. haybarn-community-extensions — becomes haybarn-community-extensions.query.farm)
#   $3  File-pattern glob     (e.g. */linux_amd64/duckpgq.duckdb_extension.*)
#
# Env:
#   CLOUDFLARE_CACHE_PURGE_TOKEN — bearer token with Zone:Cache Purge on query.farm
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_ENDPOINT_URL — R2 credentials
#   DUCKDB_CLEAN_CACHES_SCRIPT_MODE=for_real  — actually purge (otherwise dry-run)

set -eo pipefail

if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ]; then
  echo "Usage: $0 <bucket> <subdomain> <pattern>"
  exit 1
fi

BUCKET="$1"
SUBDOMAIN="$2"
PATTERN="$3"

# query.farm zone ID — set this once Cloudflare hands us the zone. We can
# discover it from the CF API with the management token if it isn't memorable.
# Until then, this is the placeholder that the deploy will refuse to run with.
QUERY_FARM_ZONE_ID="${QUERY_FARM_ZONE_ID:-REPLACE_WITH_QUERY_FARM_ZONE_ID}"

# List the matching keys via `aws s3 sync --dryrun` (no copy, just print).
output=$(aws s3 sync "s3://$BUCKET" . --exclude "*" --include "$PATTERN" --dryrun | awk '{ print $5 }')

if [ -z "$CLOUDFLARE_CACHE_PURGE_TOKEN" ]; then
  echo "##########################################"
  echo "WARNING! CLOUDFLARE INVALIDATION DISABLED!"
  echo "(CLOUDFLARE_CACHE_PURGE_TOKEN not set)"
  echo "##########################################"
  echo "Would have purged:"
  while IFS= read -r path; do
    [ -z "$path" ] && continue
    echo "    http://${SUBDOMAIN}.query.farm/${path}"
  done <<< "$output"
  exit 0
fi

if [ "$QUERY_FARM_ZONE_ID" = "REPLACE_WITH_QUERY_FARM_ZONE_ID" ]; then
  echo "ERROR: QUERY_FARM_ZONE_ID is not set."
  echo "Edit scripts/clean_caches.sh or pass QUERY_FARM_ZONE_ID via env."
  exit 2
fi

if [ "$DUCKDB_CLEAN_CACHES_SCRIPT_MODE" != "for_real" ]; then
  echo "CLOUDFLARE INVALIDATION (DRY RUN)"
  while IFS= read -r path; do
    [ -z "$path" ] && continue
    echo "    http://${SUBDOMAIN}.query.farm/${path}"
  done <<< "$output"
  exit 0
fi

echo "CLOUDFLARE INVALIDATION"
while IFS= read -r path; do
  [ -z "$path" ] && continue
  curl --silent --show-error \
       --request POST \
       --url "https://api.cloudflare.com/client/v4/zones/${QUERY_FARM_ZONE_ID}/purge_cache" \
       --header 'Content-Type: application/json' \
       --header "Authorization: Bearer ${CLOUDFLARE_CACHE_PURGE_TOKEN}" \
       --data "{\"files\": [\"http://${SUBDOMAIN}.query.farm/${path}\"]}"
  echo ""
done <<< "$output"
