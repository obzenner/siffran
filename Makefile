# Project lifecycle for the siffran plugin marketplace.
#
# This Makefile is the ONE entry point for every lifecycle operation. Agents and humans both
# drive the project through it: `make help` lists everything available. If an operation is worth
# doing twice, it belongs here as a target rather than in a chat message or a README snippet —
# a command that lives only in prose drifts from the command that actually works.
#
# Conventions
#   * Every public target carries a `## description` comment; `help` is GENERATED from those, so
#     help can never drift from the targets. A target with no `##` is internal and stays hidden.
#   * Targets are .PHONY (nothing here builds a file of the same name).
#   * Each check is independently runnable and exits nonzero on failure, so CI and a human get
#     the same verdict from the same command.
#   * No target commits, pushes, or mutates git history. Release plumbing stops at "verify and
#     tell me what to do next" — publishing is a human decision.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

PYTHON ?= python3
PLUGINS_DIR := plugins
SCRIPTS := scripts
ADR_DIR := doc/adr
EMPIRICA_TESTS := $(PLUGINS_DIR)/empirica/tests/test_hooks.py
MARKETPLACE := .claude-plugin/marketplace.json

# All plugin manifests, discovered rather than listed — a new plugin is picked up automatically.
PLUGIN_MANIFESTS := $(wildcard $(PLUGINS_DIR)/*/.claude-plugin/plugin.json)
PLUGIN_NAMES := $(notdir $(patsubst %/.claude-plugin/plugin.json,%,$(PLUGIN_MANIFESTS)))

# Colours, suppressed when not a terminal so CI logs stay readable.
ifneq (,$(findstring xterm,$(TERM)))
  BOLD := $(shell tput bold)
  DIM := $(shell tput dim)
  RESET := $(shell tput sgr0)
else
  BOLD :=
  DIM :=
  RESET :=
endif

.PHONY: help
help: ## Show this help (generated from target descriptions)
	@printf '$(BOLD)siffran$(RESET) — Claude Code plugin marketplace\n\n'
	@printf '$(BOLD)Usage:$(RESET) make <target>\n\n'
	@awk 'BEGIN {FS = ":.*?## "} \
		/^## ---/ { printf "\n$(BOLD)%s$(RESET)\n", substr($$0, 8); next } \
		/^[a-zA-Z0-9_-]+:.*?## / { printf "  $(BOLD)%-18s$(RESET) %s\n", $$1, $$2 }' \
		$(MAKEFILE_LIST)
	@printf '\n$(DIM)Plugins: $(PLUGIN_NAMES)$(RESET)\n'

## --- Verify

.PHONY: check
check: lint test validate adr-check ## Run every check (what CI and pre-commit should run)
	@printf '\n$(BOLD)All checks passed.$(RESET)\n'

.PHONY: test
test: ## Run the plugin test suites
	@printf '$(BOLD)==> tests$(RESET)\n'
	@$(PYTHON) $(EMPIRICA_TESTS)

.PHONY: lint
lint: ## Lint Python hooks, tests, and scripts (ruff, if installed)
	@printf '$(BOLD)==> lint$(RESET)\n'
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check $(PLUGINS_DIR) $(SCRIPTS); \
	else \
		printf '$(DIM)ruff not installed — skipping (pip install ruff)$(RESET)\n'; \
	fi

.PHONY: fmt
fmt: ## Auto-fix what the linter can fix
	@printf '$(BOLD)==> fmt$(RESET)\n'
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check --fix $(PLUGINS_DIR) $(SCRIPTS); \
	else \
		printf '$(DIM)ruff not installed — nothing to do$(RESET)\n'; \
	fi

.PHONY: validate
validate: ## Validate marketplace + plugin manifests (JSON, semver, cross-references)
	@printf '$(BOLD)==> manifests$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_manifests.py
	@if [ -f $(PLUGINS_DIR)/methodologist/scripts/validate.py ]; then \
		$(PYTHON) $(PLUGINS_DIR)/methodologist/scripts/validate.py \
			$(PLUGINS_DIR)/methodologist/skills/think; \
	fi

.PHONY: adr-check
adr-check: ## Check ADR link health and numbering (adrs doctor)
	@printf '$(BOLD)==> ADRs$(RESET)\n'
	@if command -v adrs >/dev/null 2>&1; then \
		adrs --ng doctor; \
	else \
		printf '$(DIM)adrs not installed — skipping$(RESET)\n'; \
	fi

## --- Inspect

.PHONY: status
status: ## Show plugin versions, ADR count, and working-tree state
	@printf '$(BOLD)Plugins$(RESET)\n'
	@for m in $(PLUGIN_MANIFESTS); do \
		$(PYTHON) -c "import json,sys; d=json.load(open('$$m')); print(f'  {d[\"name\"]:<16} {d[\"version\"]}')"; \
	done
	@printf '$(BOLD)ADRs$(RESET)\n'
	@printf '  %s records in $(ADR_DIR)\n' "$$(ls $(ADR_DIR)/*.md 2>/dev/null | wc -l | tr -d ' ')"
	@printf '$(BOLD)Git$(RESET)\n'
	@printf '  branch %s\n' "$$(git branch --show-current)"
	@if [ -n "$$(git status --porcelain)" ]; then \
		printf '  %s file(s) modified\n' "$$(git status --porcelain | wc -l | tr -d ' ')"; \
	else \
		printf '  working tree clean\n'; \
	fi

.PHONY: adr-list
adr-list: ## List all ADRs with their status
	@if command -v adrs >/dev/null 2>&1; then adrs --ng list; else ls -1 $(ADR_DIR)/*.md; fi

.PHONY: doctor
doctor: ## empirica preflight: what actors can this machine reach? (spends no inference)
	@python3 plugins/empirica/hooks/doctor.py $(if $(JSON),--json,)

## --- Release

.PHONY: bump
bump: ## Bump a plugin version: make bump PLUGIN=empirica PART=minor
	@if [ -z "$(PLUGIN)" ]; then \
		printf 'usage: make bump PLUGIN=<name> PART=major|minor|patch\n' >&2; \
		printf 'plugins: $(PLUGIN_NAMES)\n' >&2; exit 2; fi
	@$(PYTHON) $(SCRIPTS)/bump_version.py "$(PLUGIN)" "$(or $(PART),patch)"

.PHONY: docs
docs: ## Explain how to regenerate the generated plugin tables
	@printf '$(BOLD)==> generated docs$(RESET)\n'
	@printf 'The plugin tables in CLAUDE.md and README.md are generated from plugin.json.\n'
	@printf 'They are rewritten by the $(BOLD)checkup$(RESET) skill, which needs a Claude session:\n\n'
	@printf '  $(BOLD)/checkup$(RESET)\n\n'
	@printf 'Do not hand-edit between the BEGIN/END GENERATED markers.\n'
	@$(MAKE) --no-print-directory docs-check

.PHONY: docs-check
docs-check: ## Verify the generated plugin tables match the manifests
	@$(PYTHON) $(SCRIPTS)/check_generated_docs.py

.PHONY: release-check
release-check: check docs-check ## Pre-release gate: all checks plus generated docs in sync
	@printf '\n$(BOLD)Ready to release.$(RESET) Remaining steps are yours:\n'
	@printf '  1. confirm the version bump is in plugin.json (make status)\n'
	@printf '  2. commit and push\n'
	@printf '  3. open or update the PR\n'

## --- Maintain

.PHONY: clean
clean: ## Remove Python caches and stray build artifacts
	@printf '$(BOLD)==> clean$(RESET)\n'
	@find . -name '__pycache__' -type d -not -path './.git/*' -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -name '*.pyc' -not -path './.git/*' -delete 2>/dev/null || true
	@rm -rf .ruff_cache
	@printf '  removed __pycache__, *.pyc, .ruff_cache\n'

.PHONY: clean-runs
clean-runs: ## Remove transient empirica run directories (.claude/empirica/*)
	@printf '$(BOLD)==> clean-runs$(RESET)\n'
	@if [ -d .claude/empirica ]; then \
		n=$$(ls -1 .claude/empirica 2>/dev/null | wc -l | tr -d ' '); \
		rm -rf .claude/empirica; \
		printf '  removed %s transient run directory(ies)\n' "$$n"; \
	else \
		printf '  no run directories to remove\n'; \
	fi
