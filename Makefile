# Operate the Haybarn community-extension build pipeline.
#
# Each extension is built by its own `build.yml` workflow_dispatch run, named
# "🐤 <ext>" via the extension_name input. That run-name is the ONLY signal the
# status page (haybarn-status.query.farm) can use to attribute a run to an
# extension — the org webhook never receives workflow_dispatch *inputs*, only
# the resulting workflow_run (whose display_title carries "🐤 <ext>"). So:
# ALWAYS pass extension_name; never dispatch build.yml bare.
#
# Requires: `gh` authenticated with `repo` + `workflow` scopes (gh auth status).
# Run from the repo root (targets enumerate ./extensions/*).

SHELL := /bin/bash
# NOTE: .ONESHELL / .SHELLFLAGS are GNU Make >= 3.82 features. macOS ships
# GNU Make 3.81 as /usr/bin/make, which SILENTLY IGNORES them and runs every
# recipe line as its own `bash -c`. So every multi-line recipe below is written
# as a SINGLE backslash-joined logical line — it must work whether or not
# .ONESHELL is honored. Do not "tidy" these into separate lines.
.ONESHELL:
.SHELLFLAGS := -o pipefail -c

REPO    ?= Query-farm-haybarn/haybarn-community-extensions
REF     ?= main
# Keep inline comments OFF the value lines: trailing whitespace before a `#`
# is part of the variable's value in make, and would leak into `gh -f deploy=...`
# and into `$(PAR)` arithmetic.
# deploy=true → publish each extension to R2 under /<duckdb_version>/
DEPLOY  ?= true
# publish_registries (npm/PyPI); R2 deploy is a separate concern
PUBLISH ?= false
# max concurrent gh API calls (stay under GH secondary rate limits)
PAR     ?= 6
# `make republish` window: only consider build.yml runs created after this UTC
# timestamp. Default = today 00:00:00Z (matches scripts/republish_npm.sh).
SINCE   ?= $(shell date -u +%Y-%m-%dT00:00:00Z)
# `make republish` engine pin. MUST match the duckdb_version the source build
# was dispatched with — that string is in the artifact names
# (<ext>-<DV>-extension-<arch>), and _extension_deploy.yml's cross-run
# download-artifact pattern is keyed on it. If empty, republish_npm.yml's
# workflow-level default (currently 'v1.5.3') is used; that default has drifted
# behind the engine pin in main (haybarn-v1.5.3-rc6), so override explicitly
# for any sweep built off main.
DUCKDB_VERSION ?=

# Buildable extensions only. scripts/buildable_extensions.py drops any whose
# descriptor excludes every build platform — the Haybarn "doesn't build yet"
# disable convention (excluded_platforms = the full platform set). Dispatching
# those just burns an empty-matrix no-op CI run, so they're skipped by default.
# Requires python3 + pyyaml (same as the build's parse step). Use ALL_EXTS /
# `make list-all` for the unfiltered set, or `make list-disabled` to see what
# was skipped and why.
EXTS     := $(shell python3 scripts/buildable_extensions.py)
ALL_EXTS := $(notdir $(patsubst %/,%,$(wildcard extensions/*/)))

.PHONY: help list list-all list-disabled dispatch cancel resubmit status republish

help:
	@echo "Community-extension build ops (REPO=$(REPO) REF=$(REF))"
	@echo
	@echo "  make dispatch   Dispatch one build.yml run per buildable extension ($(words $(EXTS)) of $(words $(ALL_EXTS));"
	@echo "                  $(words $(filter-out $(EXTS),$(ALL_EXTS))) fully-disabled extensions skipped),"
	@echo "                  named '🐤 <ext>'. deploy=$(DEPLOY) publish_registries=$(PUBLISH)."
	@echo "  make cancel     Cancel every in-flight (queued/in_progress) '🐤' run."
	@echo "  make resubmit   cancel, then dispatch."
	@echo "  make status     Summarize current '🐤' run states on GitHub."
	@echo "  make republish  Dispatch republish_npm.yml for every extension with a successful"
	@echo "                  build.yml run since SINCE=$(SINCE) (npm leaves only, no rebuild)."
	@echo "  make list       Print the buildable extensions that would be dispatched."
	@echo "  make list-all   Print every extension dir (including disabled)."
	@echo "  make list-disabled  Print the fully-disabled extensions that dispatch skips."
	@echo
	@echo "Overridable vars: REPO REF DEPLOY PUBLISH PAR SINCE DUCKDB_VERSION"
	@echo "  e.g.  make resubmit DEPLOY=true PUBLISH=false PAR=8"
	@echo "  e.g.  make republish SINCE=2026-05-27T00:00:00Z DUCKDB_VERSION=haybarn-v1.5.3-rc6"

list:
	@printf '%s\n' $(EXTS)

list-all:
	@printf '%s\n' $(ALL_EXTS)

list-disabled:
	@python3 scripts/buildable_extensions.py --disabled

# Fire one build.yml dispatch per extension, PAR at a time. Results tallied
# from a temp file (subshell counters can't propagate to the parent).
dispatch:
	@echo "Dispatching $(words $(EXTS)) buildable extension(s) of $(words $(ALL_EXTS)) ($(words $(filter-out $(EXTS),$(ALL_EXTS))) disabled, skipped) → $(REPO)@$(REF) (deploy=$(DEPLOY) publish_registries=$(PUBLISH), $(PAR)-way)"
	@tmp=$$(mktemp); \
	n=0; \
	for ext in $(EXTS); do \
	  ( gh workflow run build.yml --repo "$(REPO)" --ref "$(REF)" \
	      -f extension_name="$$ext" -f deploy=$(DEPLOY) -f publish_registries=$(PUBLISH) \
	      >/dev/null 2>&1 && echo "OK" || echo "FAIL $$ext" ) >>"$$tmp" & \
	  n=$$((n+1)); [ $$((n % $(PAR))) -eq 0 ] && wait; \
	done; \
	wait; \
	echo "  ok=$$(grep -c '^OK' "$$tmp" || true)  fail=$$(grep -c '^FAIL' "$$tmp" || true)"; \
	grep '^FAIL' "$$tmp" || true; \
	rm -f "$$tmp"

# Cancel every queued/in_progress per-extension run (titles starting with 🐤).
cancel:
	@echo "Collecting in-flight '🐤' runs in $(REPO)…"
	@ids=$$(gh run list --repo "$(REPO)" --workflow build.yml --event workflow_dispatch -L 400 \
	         --json databaseId,status,displayTitle \
	         --jq '.[]|select((.status=="queued" or .status=="in_progress") and (.displayTitle|startswith("🐤")))|.databaseId'); \
	n=$$(printf '%s\n' "$$ids" | grep -c . || true); \
	echo "  $$n run(s) to cancel"; \
	if [ "$$n" -eq 0 ]; then exit 0; fi; \
	c=0; \
	for id in $$ids; do \
	  ( gh run cancel "$$id" --repo "$(REPO)" >/dev/null 2>&1 || true ) & \
	  c=$$((c+1)); [ $$((c % $(PAR))) -eq 0 ] && wait; \
	done; \
	wait; \
	echo "  cancelled $$n"

resubmit:
	@$(MAKE) --no-print-directory cancel
	@$(MAKE) --no-print-directory dispatch

status:
	@gh run list --repo "$(REPO)" --workflow build.yml --event workflow_dispatch -L 400 \
	  --json status,conclusion,displayTitle \
	  --jq '[.[]|select(.displayTitle|startswith("🐤"))] | "🐤 runs (recent window): \(length)", (group_by(.status)[] | "  \(.[0].status): \(length)")'

# Fan-out republish_npm.yml: for each extension with a successful build.yml run
# since SINCE, dispatch republish_npm.yml against its most-recent successful
# run_id. Reuses the per-arch artifacts → npm leaves + meta, no rebuild, no R2
# touch. Re-running is safe (npm publish SKIPs already-published versions).
republish:
	@echo "Resolving successful build.yml runs in $(REPO) since $(SINCE)…"
	@tmp=$$(mktemp); \
	rows=$$(gh run list --repo "$(REPO)" --workflow build.yml --event workflow_dispatch -L 400 \
	         --json databaseId,conclusion,displayTitle,createdAt \
	         --jq "[.[]|select(.createdAt > \"$(SINCE)\" and .conclusion==\"success\")] | group_by(.displayTitle) | map(max_by(.createdAt)) | .[] | \"\(.displayTitle|sub(\"^🐤 \";\"\")) \(.databaseId)\""); \
	n=$$(printf '%s\n' "$$rows" | grep -c . || true); \
	if [ "$$n" -eq 0 ]; then echo "  no successful runs found"; rm -f "$$tmp"; exit 0; fi; \
	echo "Dispatching republish_npm.yml for $$n extension(s) → $(REPO)@$(REF) ($(PAR)-way)"; \
	c=0; \
	while read -r ext rid; do \
	  [ -z "$$ext" ] && continue; \
	  ( gh workflow run republish_npm.yml --repo "$(REPO)" --ref "$(REF)" \
	      -f extension_name="$$ext" -f source_run_id="$$rid" \
	      $(if $(DUCKDB_VERSION),-f duckdb_version="$(DUCKDB_VERSION)",) \
	      >/dev/null 2>&1 && echo "OK" || echo "FAIL $$ext" ) >>"$$tmp" & \
	  c=$$((c+1)); [ $$((c % $(PAR))) -eq 0 ] && wait; \
	done <<<"$$rows"; \
	wait; \
	echo "  ok=$$(grep -c '^OK' "$$tmp" || true)  fail=$$(grep -c '^FAIL' "$$tmp" || true)"; \
	grep '^FAIL' "$$tmp" || true; \
	rm -f "$$tmp"
