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
.ONESHELL:
.SHELLFLAGS := -o pipefail -c

REPO    ?= Query-farm-haybarn/haybarn-community-extensions
REF     ?= main
DEPLOY  ?= true     # deploy=true → publish each extension to R2 under /<duckdb_version>/
PUBLISH ?= false    # publish_registries (npm/PyPI); R2 deploy is a separate concern
PAR     ?= 6        # max concurrent gh API calls (stay under GH secondary rate limits)

# One dir per extension under extensions/.
EXTS := $(notdir $(patsubst %/,%,$(wildcard extensions/*/)))

.PHONY: help list dispatch cancel resubmit status

help:
	@echo "Community-extension build ops (REPO=$(REPO) REF=$(REF))"
	@echo
	@echo "  make dispatch   Dispatch one build.yml run per extension ($(words $(EXTS)) total),"
	@echo "                  named '🐤 <ext>'. deploy=$(DEPLOY) publish_registries=$(PUBLISH)."
	@echo "  make cancel     Cancel every in-flight (queued/in_progress) '🐤' run."
	@echo "  make resubmit   cancel, then dispatch."
	@echo "  make status     Summarize current '🐤' run states on GitHub."
	@echo "  make list       Print the extensions that would be dispatched."
	@echo
	@echo "Overridable vars: REPO REF DEPLOY PUBLISH PAR"
	@echo "  e.g.  make resubmit DEPLOY=true PUBLISH=false PAR=8"

list:
	@printf '%s\n' $(EXTS)

# Fire one build.yml dispatch per extension, PAR at a time. Results tallied
# from a temp file (subshell counters can't propagate to the parent).
dispatch:
	@echo "Dispatching $(words $(EXTS)) extension(s) → $(REPO)@$(REF) (deploy=$(DEPLOY) publish_registries=$(PUBLISH), $(PAR)-way)"
	tmp=$$(mktemp)
	n=0
	for ext in $(EXTS); do
	  ( gh workflow run build.yml --repo "$(REPO)" --ref "$(REF)" \
	      -f extension_name="$$ext" -f deploy=$(DEPLOY) -f publish_registries=$(PUBLISH) \
	      >/dev/null 2>&1 && echo "OK" || echo "FAIL $$ext" ) >>"$$tmp" &
	  n=$$((n+1)); [ $$((n % $(PAR))) -eq 0 ] && wait
	done
	wait
	echo "  ok=$$(grep -c '^OK' "$$tmp" || true)  fail=$$(grep -c '^FAIL' "$$tmp" || true)"
	grep '^FAIL' "$$tmp" || true
	rm -f "$$tmp"

# Cancel every queued/in_progress per-extension run (titles starting with 🐤).
cancel:
	@echo "Collecting in-flight '🐤' runs in $(REPO)…"
	ids=$$(gh run list --repo "$(REPO)" --workflow build.yml --event workflow_dispatch -L 400 \
	         --json databaseId,status,displayTitle \
	         --jq '.[]|select((.status=="queued" or .status=="in_progress") and (.displayTitle|startswith("🐤")))|.databaseId')
	n=$$(printf '%s\n' "$$ids" | grep -c . || true)
	echo "  $$n run(s) to cancel"
	if [ "$$n" -eq 0 ]; then exit 0; fi
	c=0
	for id in $$ids; do
	  ( gh run cancel "$$id" --repo "$(REPO)" >/dev/null 2>&1 || true ) &
	  c=$$((c+1)); [ $$((c % $(PAR))) -eq 0 ] && wait
	done
	wait
	echo "  cancelled $$n"

resubmit:
	@$(MAKE) --no-print-directory cancel
	@$(MAKE) --no-print-directory dispatch

status:
	@gh run list --repo "$(REPO)" --workflow build.yml --event workflow_dispatch -L 400 \
	  --json status,conclusion,displayTitle \
	  --jq '[.[]|select(.displayTitle|startswith("🐤"))] | "🐤 runs (recent window): \(length)", (group_by(.status)[] | "  \(.[0].status): \(length)")'
