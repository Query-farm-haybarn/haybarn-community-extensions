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

# Buildable extensions only. scripts/buildable_extensions.py drops any whose
# descriptor excludes every build platform — the Haybarn "doesn't build yet"
# disable convention (excluded_platforms = the full platform set). Dispatching
# those just burns an empty-matrix no-op CI run, so they're skipped by default.
# Requires python3 + pyyaml (same as the build's parse step). Use ALL_EXTS /
# `make list-all` for the unfiltered set, or `make list-disabled` to see what
# was skipped and why.
EXTS     := $(shell python3 scripts/buildable_extensions.py)
ALL_EXTS := $(notdir $(patsubst %/,%,$(wildcard extensions/*/)))

.PHONY: help list list-all list-disabled dispatch cancel resubmit status

help:
	@echo "Community-extension build ops (REPO=$(REPO) REF=$(REF))"
	@echo
	@echo "  make dispatch   Dispatch one build.yml run per buildable extension ($(words $(EXTS)) of $(words $(ALL_EXTS));"
	@echo "                  $(words $(filter-out $(EXTS),$(ALL_EXTS))) fully-disabled extensions skipped),"
	@echo "                  named '🐤 <ext>'. deploy=$(DEPLOY) publish_registries=$(PUBLISH)."
	@echo "  make cancel     Cancel every in-flight (queued/in_progress) '🐤' run."
	@echo "  make resubmit   cancel, then dispatch."
	@echo "  make status     Summarize current '🐤' run states on GitHub."
	@echo "  make list       Print the buildable extensions that would be dispatched."
	@echo "  make list-all   Print every extension dir (including disabled)."
	@echo "  make list-disabled  Print the fully-disabled extensions that dispatch skips."
	@echo
	@echo "Overridable vars: REPO REF DEPLOY PUBLISH PAR"
	@echo "  e.g.  make resubmit DEPLOY=true PUBLISH=false PAR=8"

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
