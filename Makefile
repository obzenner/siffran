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
EMPIRICA_ACTIVATION_TESTS := $(PLUGINS_DIR)/empirica/adapters/claude/tests/test_activation_lifecycle.py
EMPIRICA_CORE_TESTS := $(PLUGINS_DIR)/empirica/tests/test_core.py
EMPIRICA_APP_TESTS := $(PLUGINS_DIR)/empirica/tests/test_application.py
EMPIRICA_STATE_TESTS := $(PLUGINS_DIR)/empirica/tests/test_state_adapter.py
EMPIRICA_GIT_ADAPTER_TESTS := $(PLUGINS_DIR)/empirica/adapters/git/tests/test_git_artifact_repo.py
EMPIRICA_CLAUDE_ADAPTER_TESTS := $(PLUGINS_DIR)/empirica/adapters/claude/tests/test_claude_adapter.py
EMPIRICA_CODEX_ADAPTER_TESTS := $(PLUGINS_DIR)/empirica/adapters/codex/tests/test_codex_adapter.py
METHODOLOGIST_CORE_TESTS := $(PLUGINS_DIR)/methodologist/tests/test_core.py
METHODOLOGIST_CODEX_TESTS := $(PLUGINS_DIR)/methodologist/adapters/codex/tests/test_mcp_server.py
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
	@printf '$(BOLD)siffran$(RESET) — Claude Code, Codex, and Pi plugin collection\n\n'
	@printf '$(BOLD)Usage:$(RESET) make <target>\n\n'
	@awk 'BEGIN {FS = ":.*?## "} \
		/^## ---/ { printf "\n$(BOLD)%s$(RESET)\n", substr($$0, 8); next } \
		/^[a-zA-Z0-9_-]+:.*?## / { printf "  $(BOLD)%-18s$(RESET) %s\n", $$1, $$2 }' \
		$(MAKEFILE_LIST)
	@printf '\n$(DIM)Plugins: $(PLUGIN_NAMES)$(RESET)\n'

## --- Verify

# `check` is composed of subject-matter suites so a contributor can run the one that matters for
# their change and CI can pick what its runners support. Each suite is self-contained and prints
# its own banner; `check` is simply all of them, `check-ci` is all of them minus the Pi suite
# (CI runners do not carry Node/Pi — set PI_CHECKS=1 there to opt in). Measured locally: static ~3s,
# core ~2s, claude ~30s, codex ~30s, pi ~30s.
.PHONY: check check-ci check-static check-core check-claude check-codex check-pi
check: check-static check-core check-claude check-codex check-pi ## Run every suite (local pre-commit gate)
	@printf '\n$(BOLD)All checks passed.$(RESET)\n'

check-ci: check-static check-core check-claude check-codex ## Every suite except Pi (add PI_CHECKS=1 to include it)
	@if [ "$(PI_CHECKS)" = "1" ]; then $(MAKE) check-pi; else printf '$(DIM)Pi suite skipped in CI (PI_CHECKS=1 to include)$(RESET)\n'; fi
	@printf '\n$(BOLD)CI checks passed.$(RESET)\n'

check-static: lint validate docs-check adr-check contract-check obligations-check vendor-check activation-check ## Lint, manifests, generated docs, ADR health, API/obligation contracts, vendor copy, activation isolation
	@printf '$(BOLD)==> static suite ok$(RESET)\n'

check-core: ## Host-neutral core: obligations lib, Empirica core/application/state/git store, Methodologist core
	@printf '$(BOLD)==> core suite$(RESET)\n'
	@$(PYTHON) lib/obligations/tests/test_obligations.py
	@$(PYTHON) $(EMPIRICA_CORE_TESTS)
	@$(PYTHON) $(EMPIRICA_APP_TESTS)
	@$(PYTHON) $(EMPIRICA_STATE_TESTS)
	@$(PYTHON) $(EMPIRICA_GIT_ADAPTER_TESTS)
	@$(PYTHON) $(METHODOLOGIST_CORE_TESTS)

check-claude: ## Claude Code host: activation lifecycle + Claude adapter tests
	@printf '$(BOLD)==> claude suite$(RESET)\n'
	@$(PYTHON) $(EMPIRICA_ACTIVATION_TESTS)
	@$(PYTHON) $(EMPIRICA_CLAUDE_ADAPTER_TESTS)

check-codex: methodologist-codex-check empirica-codex-check ## Codex host: adapter tests + package/hook validation for both plugins
	@printf '$(BOLD)==> codex suite$(RESET)\n'
	@$(PYTHON) $(EMPIRICA_CODEX_ADAPTER_TESTS)
	@$(PYTHON) $(METHODOLOGIST_CODEX_TESTS)

check-pi: pi-bundle-check methodologist-pi-check empirica-pi-check ## Pi host: bundle + both adapters (static, typecheck, tests, live bridge) — needs Node
	@printf '$(BOLD)==> pi suite ok$(RESET)\n'

.PHONY: test
test: check-core check-claude check-codex ## Run every test suite (core + claude + codex; Pi tests live in check-pi)

.PHONY: lint
lint: ## Lint Python hooks, tests, and scripts (ruff, if installed)
	@printf '$(BOLD)==> lint$(RESET)\n'
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check $(PLUGINS_DIR) lib $(SCRIPTS); \
	else \
		printf '$(DIM)ruff not installed — skipping (pip install ruff)$(RESET)\n'; \
	fi

.PHONY: fmt
fmt: ## Auto-fix what the linter can fix
	@printf '$(BOLD)==> fmt$(RESET)\n'
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check --fix $(PLUGINS_DIR) lib $(SCRIPTS); \
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

.PHONY: contract-check
contract-check: ## Validate host-neutral API schemas and conformance fixtures
	@printf '$(BOLD)==> contracts$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_contracts.py

# Design spike from the first Pi dogfood run (doc/design/bridge-transport-retry-policy.md). It is a
# design model, not a product check, so it is NOT part of `make check`.
.PHONY: bridge-retry-spike
bridge-retry-spike: ## Run the bridge retry-policy design model spike (design evidence, not a release gate)
	@printf '$(BOLD)==> bridge retry-policy spike$(RESET)\n'
	@$(PYTHON) doc/design/spikes/bridge_retry_policy_model.py

.PHONY: obligations-check
obligations-check: ## Validate obligation schemas and substrate-neutral fixtures
	@printf '$(BOLD)==> obligations$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_obligations.py

.PHONY: vendor-check
vendor-check: ## Verify Empirica's obligation package is byte-identical to the generic source
	@printf '$(BOLD)==> obligation vendor$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/check_vendor.py

.PHONY: activation-check
activation-check: ## Verify Empirica runtime isolation and thin Claude hook activation
	@printf '$(BOLD)==> empirica activation$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_empirica_activation.py

# devDependencies (typescript, @types/node) that turn the Pi adapter typecheck
# from a skipped note into an enforced gate. Rebuilt when the lockfile changes;
# graceful when npm is absent — the validator then skips the typecheck itself.
node_modules: package-lock.json
	@if command -v npm >/dev/null 2>&1; then \
		printf '$(BOLD)==> installing pi devDependencies (npm ci)$(RESET)\n'; \
		npm ci --no-audit --no-fund; \
		touch node_modules; \
	else \
		printf '$(DIM)npm not installed — skipping devDependency install (typecheck will be skipped)$(RESET)\n'; \
	fi

.PHONY: methodologist-pi-check
methodologist-pi-check: node_modules ## Validate the Methodologist Pi adapter package (static always; typecheck + tests if node present)
	@printf '$(BOLD)==> methodologist Pi adapter$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_pi_adapter.py plugins/methodologist

.PHONY: methodologist-codex-check
methodologist-codex-check: ## Deterministically validate the Methodologist Codex package and MCP bridge
	@printf '$(BOLD)==> methodologist Codex package$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_codex_plugin.py

.PHONY: methodologist-codex-smoke
methodologist-codex-smoke: ## Run online discovery/invocation smoke tests with codex-cli 0.146.0
	@printf '$(BOLD)==> methodologist Codex 0.146.0 smoke$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/smoke_codex_plugin.py

.PHONY: pi-bundle-check
pi-bundle-check: node_modules ## Validate the repository-root Pi package used by `pi install git:...`
	@printf '$(BOLD)==> Pi bundle$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_pi_adapter.py .

.PHONY: empirica-pi-check
empirica-pi-check: node_modules ## Validate the Empirica Pi adapter package (static + bridge smoke always; typecheck + tests if node present)
	@printf '$(BOLD)==> empirica Pi adapter$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_pi_adapter.py plugins/empirica/adapters/pi

.PHONY: empirica-codex-check
empirica-codex-check: ## Validate the Empirica Codex package, hook payloads, and isolated lifecycle
	@printf '$(BOLD)==> empirica Codex adapter$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_codex_adapter.py

.PHONY: codex-live-check
codex-live-check: ## Smoke Codex 0.146.0 marketplace/plugin loading (set CODEX=... if needed)
	@printf '$(BOLD)==> Codex 0.146.0 live plugin smoke$(RESET)\n'
	@CODEX="$${CODEX:-codex}" $(PYTHON) $(SCRIPTS)/validate_codex_adapter.py --live

## --- Inspect

.PHONY: status
status: ## Show plugin versions, ADR count, and working-tree state
	@printf '$(BOLD)Plugins$(RESET)\n'
	@for m in $(PLUGIN_MANIFESTS); do \
		$(PYTHON) -c "import json,sys; d=json.load(open('$$m')); print(f'  {d[\"name\"]:<16} {d[\"version\"]}')"; \
	done
	@$(PYTHON) scripts/check_installed_version.py
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
doctor: ## empirica preflight: actors reachable; pass ARGS="--multi-provider" to probe (no inference)
	@PYTHONPATH=plugins/empirica $(PYTHON) -c 'from adapters.claude.preflight import main; raise SystemExit(main())' $(ARGS)

.PHONY: migrate-legacy
migrate-legacy: ## Explicitly import a legacy run: make migrate-legacy RUN_DIR=... REPO=...
	@if [ -z "$(RUN_DIR)" ] || [ -z "$(REPO)" ]; then \
		printf 'usage: make migrate-legacy RUN_DIR=<legacy-run-dir> REPO=<git-repo> [SESSION_ID=<id>]\n' >&2; \
		exit 2; \
	fi
	@$(PYTHON) plugins/empirica/adapters/claude/migrate_legacy.py \
		--run-dir "$(RUN_DIR)" --repo "$(REPO)" $(if $(SESSION_ID),--session-id "$(SESSION_ID)",)

# Dogfooding (see docs/packages.md "Scope and Deduplication" in pi): the committed .pi/settings.json
# adds this checkout as a project-local package and applies an autoload:false DELTA over the globally
# installed siffran package that force-excludes its Pi extensions/skills. Everything else in the
# user's Pi setup (pi-subagents, providers, other packages) loads unchanged; only siffran is
# overridden by this tree. Local edits hot-reload with /reload. Pi asks to trust the folder once.
PI ?= pi
.PHONY: pi-dev
pi-dev: ## Run Pi with siffran overridden by THIS checkout (dogfood; other packages unchanged): make pi-dev [ARGS="..."]
	@command -v $(PI) >/dev/null 2>&1 || { printf 'pi-dev: `$(PI)` not found on PATH (set PI=/path/to/pi)\n' >&2; exit 2; }
	@test -f .pi/settings.json || { printf 'pi-dev: .pi/settings.json is missing (it is committed; restore it)\n' >&2; exit 2; }
	@printf '$(BOLD)==> dev pi$(RESET) siffran from %s (project override); answer YES if pi asks to trust this folder\n' "$(CURDIR)"
	@$(PI) $(ARGS)

# Canary: dogfood a pushed PR branch inside a REAL project, not inside siffran. Installs the branch
# as a project-local package there (project wins over the global install; identity is the repo URL,
# so the global entry is shadowed, not duplicated). `pi update --extensions` reconciles the clone.
SIFFRAN_GIT ?= git:github.com/obzenner/siffran
.PHONY: pi-canary pi-canary-remove
pi-canary: ## Install a siffran branch project-locally in DIR for dogfooding: make pi-canary REF=<branch> [DIR=<project>]
	@if [ -z "$(REF)" ]; then printf 'usage: make pi-canary REF=<branch-or-tag> [DIR=<project dir, default: this checkout>]\n' >&2; exit 2; fi
	@cd "$(or $(DIR),$(CURDIR))" && $(PI) install -l "$(SIFFRAN_GIT)@$(REF)"
	@printf '$(BOLD)==> canary$(RESET) %s@%s installed project-locally in %s; run `pi` there (trust the folder when asked); `make pi-canary-remove DIR=...` to undo\n' "$(SIFFRAN_GIT)" "$(REF)" "$(or $(DIR),$(CURDIR))"

pi-canary-remove: ## Remove the project-local siffran canary from DIR: make pi-canary-remove [DIR=<project>]
	@cd "$(or $(DIR),$(CURDIR))" && $(PI) remove -l "$(SIFFRAN_GIT)"

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

.PHONY: docs-sync
docs-sync: ## Deterministically regenerate the marked plugin tables from manifests
	@$(PYTHON) $(SCRIPTS)/check_generated_docs.py --write

.PHONY: docs-check
docs-check: ## Verify the generated plugin tables match the manifests
	@$(PYTHON) $(SCRIPTS)/check_generated_docs.py

.PHONY: release-check
release-check: check ## Pre-release gate: every suite (docs-check is part of check-static)
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
clean-runs: ## Remove machine-local empirica operational runs (never legacy .claude paths)
	@printf '$(BOLD)==> clean-runs$(RESET)\n'
	@home="$${EMPIRICA_HOME:-$$HOME/.empirica-plugin}"; \
	if [ -d "$$home/projects" ]; then \
		n=$$(find "$$home/projects" -name run.json -type f 2>/dev/null | wc -l | tr -d ' '); \
		rm -rf "$$home/projects"; \
		printf '  removed %s operational run(s) from %s\n' "$$n" "$$home"; \
	else \
		printf '  no operational runs to remove\n'; \
	fi
