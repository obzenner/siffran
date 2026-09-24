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
EMPIRICA_FRESHNESS_TESTS := $(PLUGINS_DIR)/empirica/tests/test_freshness.py
EMPIRICA_OBSERVATION_TESTS := $(PLUGINS_DIR)/empirica/tests/test_observation.py
EMPIRICA_EXECUTION_ADAPTER_TESTS := $(PLUGINS_DIR)/empirica/tests/test_execution_adapter.py
EMPIRICA_PROTOCOL_ISOLATION_TESTS := $(PLUGINS_DIR)/empirica/tests/test_protocol_isolation.py
EMPIRICA_LIVE_RECEIPT_TESTS := $(SCRIPTS)/tests/test_empirica_live_receipts.py
EMPIRICA_CLAUDE_MCP_LOG_TESTS := $(SCRIPTS)/tests/test_check_claude_mcp_log.py
PI_CANARY_CONFIG_TESTS := $(SCRIPTS)/tests/test_configure_pi_canary.py
EMPIRICA_D6_STRICT_TESTS := $(PLUGINS_DIR)/empirica/tests/test_d6_strict_v2.py
EMPIRICA_LOCATION_TESTS := $(PLUGINS_DIR)/empirica/tests/test_d7_location.py
EMPIRICA_TRANSACTION_TESTS := $(PLUGINS_DIR)/empirica/tests/test_d7_transactions.py
EMPIRICA_BRIDGE_V2_TESTS := $(PLUGINS_DIR)/empirica/tests/test_bridge_v2.py
EMPIRICA_PUBLIC_TOOLS_TESTS := $(PLUGINS_DIR)/empirica/tests/test_public_tools.py
EMPIRICA_AUDIT_PROTOCOL_TESTS := $(PLUGINS_DIR)/empirica/tests/test_audit_protocol.py
EMPIRICA_STATE_TESTS := $(PLUGINS_DIR)/empirica/tests/test_state_adapter.py
EMPIRICA_GIT_ADAPTER_TESTS := $(PLUGINS_DIR)/empirica/adapters/git/tests/test_git_artifact_repo.py
EMPIRICA_GOVERNANCE_TESTS := $(PLUGINS_DIR)/empirica/tests/test_governance.py
EMPIRICA_CLAUDE_ADAPTER_TESTS := $(PLUGINS_DIR)/empirica/adapters/claude/tests/test_claude_adapter.py
EMPIRICA_CLAUDE_ADAPTER_CONFORMANCE_TESTS := $(PLUGINS_DIR)/empirica/adapters/claude/tests/test_claude_adapter_conformance.py
EMPIRICA_CODEX_ADAPTER_TESTS := $(PLUGINS_DIR)/empirica/adapters/codex/tests/test_codex_adapter.py
EMPIRICA_CODEX_ADAPTER_CONFORMANCE_TESTS := $(PLUGINS_DIR)/empirica/adapters/codex/tests/test_codex_adapter_conformance.py
METHODOLOGIST_CORE_TESTS := $(PLUGINS_DIR)/methodologist/tests/test_core.py
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

# `check` is the fast deterministic contributor gate. Expensive filesystem/host-journey matrices
# live under the explicit integration targets below; installed-host qualification is operator-led
# and never runs automatically. `check-ci` omits Pi unless the runner explicitly opts in.
.PHONY: check check-ci check-static check-core check-claude check-codex check-pi
check: check-static check-core check-claude check-codex check-pi ## Fast contributor gate (not integration/native certification)
	@printf '\n$(BOLD)Fast contributor checks passed.$(RESET)\n'

check-ci: check-static check-core check-claude check-codex ## Fast contributor gate except Pi (PI_CHECKS=1 includes it)
	@if [ "$(PI_CHECKS)" = "1" ]; then $(MAKE) check-pi; else printf '$(DIM)Pi suite skipped in CI (PI_CHECKS=1 to include)$(RESET)\n'; fi
	@printf '\n$(BOLD)CI checks passed.$(RESET)\n'

check-static: lint validate docs-check adr-check empirica-architecture-check contract-check obligations-check vendor-check activation-check empirica-host-receipt-unit-check empirica-claude-mcp-log-unit-check pi-canary-unit-check pi-validator-unit-check ## Lint, manifests, docs, ADRs, contracts, vendor copies, activation, receipts, canary config
	@printf '$(BOLD)==> static suite ok$(RESET)\n'

check-core: ## Fast host-neutral contracts, malformed input, state, bridge, and governance boundaries
	@printf '$(BOLD)==> core suite$(RESET)\n'
	@$(PYTHON) lib/obligations/tests/test_obligations.py
	@$(PYTHON) $(EMPIRICA_FRESHNESS_TESTS)
	@$(PYTHON) $(EMPIRICA_OBSERVATION_TESTS)
	@$(PYTHON) $(EMPIRICA_EXECUTION_ADAPTER_TESTS)
	@$(PYTHON) $(EMPIRICA_PROTOCOL_ISOLATION_TESTS)
	@$(PYTHON) $(EMPIRICA_LOCATION_TESTS)
	@$(PYTHON) $(EMPIRICA_PUBLIC_TOOLS_TESTS)
	@$(PYTHON) $(EMPIRICA_AUDIT_PROTOCOL_TESTS)
	@$(PYTHON) $(EMPIRICA_STATE_TESTS)
	@$(PYTHON) $(METHODOLOGIST_CORE_TESTS)
	@cd $(PLUGINS_DIR)/empirica/tests && PYTHONPATH=.. $(PYTHON) -m unittest -q \
		test_governance.GovernanceServiceTests.test_private_exact_replay_conflict_stale_cross_run_and_cas \
		test_governance.GovernanceServiceTests.test_cancel_unknown_inventory_singleton_and_no_public_approval

check-claude: ## Fast Claude payload, lifecycle translation, and fail-closed adapter tests
	@printf '$(BOLD)==> claude suite$(RESET)\n'
	@$(PYTHON) $(EMPIRICA_CLAUDE_ADAPTER_TESTS)

check-codex: methodologist-codex-check empirica-codex-check ## Fast Codex package, hook, payload, and MCP tests
	@cd $(PLUGINS_DIR)/empirica/adapters/codex/tests && $(PYTHON) -m unittest -q \
		test_codex_adapter.ExactV2ProfileTests.test_profile_id_is_the_exact_codex_registry_profile \
		test_codex_adapter.ExactV2ProfileTests.test_stop_hook_deadline_exceeds_managed_audit_deadline \
		test_codex_adapter.ExactV2ProfileTests.test_transport_dispatches_via_bridge_handle_with_profile_and_no_cwd \
		test_codex_adapter.CorrelationTests \
		test_codex_adapter.OfficialShapeTests \
		test_codex_adapter.StartRunTests \
		test_codex_adapter.ResolveRunTests \
		test_codex_adapter.RemovedAndTrustedSurfacesTests \
		test_codex_adapter.UnsupportedLifecycleTests.test_stop_fails_closed_when_evaluation_transport_breaks_after_resolution \
		test_codex_adapter.StoreIsolationTests
	@printf '$(BOLD)==> codex suite ok$(RESET)\n'

check-pi: pi-bundle-check methodologist-pi-check empirica-pi-check ## Fast Pi package, type, unit, guard, and bounded bridge tests — needs Node
	@printf '$(BOLD)==> pi suite ok$(RESET)\n'

.PHONY: empirica-governance-check empirica-core-integration empirica-host-integration
empirica-governance-check: ## Diagnose full real-service governance CAS, replay, consent, and host flows
	@$(PYTHON) $(EMPIRICA_GOVERNANCE_TESTS)
	@$(PYTHON) $(PLUGINS_DIR)/empirica/tests/test_governance_hosts.py

empirica-core-integration: ## Diagnose expensive persistence, transaction, retry, and v2 behavior
	@$(PYTHON) $(EMPIRICA_D6_STRICT_TESTS)
	@$(PYTHON) $(EMPIRICA_TRANSACTION_TESTS)
	@$(PYTHON) $(EMPIRICA_GIT_ADAPTER_TESTS)
	@$(PYTHON) $(EMPIRICA_BRIDGE_V2_TESTS)
	@$(PYTHON) $(PLUGINS_DIR)/empirica/tests/test_stale_audit_retry.py
	@$(MAKE) empirica-v2-check

.PHONY: empirica-v2-check
empirica-v2-check: ## Diagnose v2 behavior with unittest discovery (ARGS="-k test_name" selects a regression)
	@cd $(PLUGINS_DIR)/empirica/tests/v2 && PYTHONPATH=..:../.. $(PYTHON) -m unittest discover -v -p 'test_*.py' $(ARGS)

empirica-host-integration: ## Diagnose expensive simulated Claude/Codex lifecycle conformance
	@$(PYTHON) $(EMPIRICA_ACTIVATION_TESTS)
	@$(PYTHON) $(EMPIRICA_CODEX_ADAPTER_TESTS)
	@$(PYTHON) $(EMPIRICA_CLAUDE_ADAPTER_CONFORMANCE_TESTS)
	@$(PYTHON) $(EMPIRICA_CODEX_ADAPTER_CONFORMANCE_TESTS)

.PHONY: test
test: check ## Alias for the complete fast contributor gate, including validator unit tests

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
	@PYTHONPATH=$(PLUGINS_DIR)/empirica $(PYTHON) -c 'import adapters.public_tools'

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
vendor-check: ## Verify Empirica's obligation and runtime-contract vendor copies
	@printf '$(BOLD)==> Empirica vendor copies$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/check_vendor.py
	@$(PYTHON) $(SCRIPTS)/check_contract_vendor.py

.PHONY: activation-check
activation-check: ## Verify Empirica runtime isolation and thin Claude hook activation
	@printf '$(BOLD)==> empirica activation$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_empirica_activation.py

# devDependencies (typescript, @types/node) that turn the Pi adapter typecheck
# from a skipped note into an enforced gate. Rebuilt when the lockfile changes;
# Missing npm does not imply a successful typecheck: the validator fails without tsc unless
# EMPIRICA_ALLOW_SKIP=1 explicitly acknowledges a partial check.
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

.PHONY: pi-bundle-check
pi-bundle-check: node_modules ## Validate repository-root Pi package composition without rerunning adapter tests
	@printf '$(BOLD)==> Pi bundle$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_pi_adapter.py . --package-only

.PHONY: empirica-pi-check
empirica-pi-check: node_modules ## Validate the Empirica Pi adapter package (static + bridge smoke always; typecheck + tests if node present)
	@printf '$(BOLD)==> empirica Pi adapter$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_pi_adapter.py plugins/empirica/adapters/pi

.PHONY: empirica-governance-ui-check empirica-pi-typecheck pi-validator-unit-check
empirica-governance-ui-check: node_modules ## Typecheck and run fast governance dialog edge-case regressions
	@$(PYTHON) $(SCRIPTS)/validate_pi_adapter.py plugins/empirica/adapters/pi --test-file governance-ui-unit.test.ts

empirica-pi-typecheck: node_modules ## Validate and typecheck the Empirica Pi adapter without running tests
	@$(PYTHON) $(SCRIPTS)/validate_pi_adapter.py plugins/empirica/adapters/pi --typecheck-only

pi-validator-unit-check: ## Test Pi validator test selection and bounded async test invocation
	@$(PYTHON) $(SCRIPTS)/tests/test_validate_pi_adapter.py

.PHONY: empirica-codex-check
empirica-codex-check: ## Validate the Empirica Codex manifest, hooks, and package layout (no host execution)
	@printf '$(BOLD)==> empirica Codex adapter$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_codex_adapter.py

# Empirica 2.0 target-state architecture validator (D3). Structural-only: ownership/dependency
# direction, subtraction (forbidden files/symbols/fields/actions/protocols), effective runtime budget,
# thin hooks, public-contract/profile alignment, and Make lifecycle. ADR 0053 re-establishes
# the measured finite ceiling and composes this target into check-static/check/check-ci.
# Pass ARGS=--self-test to run the committed synthetic suite (GREEN) instead of validating the repo.
.PHONY: empirica-architecture-check
empirica-architecture-check: ## validate Empirica 2.0 target ownership, dependencies, subtraction, and code budget
	@printf '$(BOLD)==> empirica architecture$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/validate_empirica_architecture.py $(ARGS)

.PHONY: empirica-claude-mcp-log-unit-check
empirica-claude-mcp-log-unit-check: ## Test native Claude MCP admission-log verification
	@printf '$(BOLD)==> Claude MCP admission-log unit checks$(RESET)\n'
	@$(PYTHON) $(EMPIRICA_CLAUDE_MCP_LOG_TESTS)

.PHONY: empirica-claude-mcp-log-check
empirica-claude-mcp-log-check: ## Verify one native Claude MCP log: LOG=... [ARGS="--expected-version ... --require-call ..."]
	@test -n "$(LOG)" || { printf 'LOG is required\n' >&2; exit 2; }
	@$(PYTHON) $(SCRIPTS)/check_claude_mcp_log.py "$(LOG)" $(ARGS)

.PHONY: empirica-host-receipt-unit-check
empirica-host-receipt-unit-check: ## test structural installed-host receipt verification
	@printf '$(BOLD)==> empirica receipt verifier$(RESET)\n'
	@$(PYTHON) $(EMPIRICA_LIVE_RECEIPT_TESTS)

.PHONY: pi-canary-unit-check
pi-canary-unit-check: ## Test project-local Pi canary package filtering
	@printf '$(BOLD)==> Pi canary configuration$(RESET)\n'
	@$(PYTHON) $(PI_CANARY_CONFIG_TESTS)

.PHONY: empirica-host-receipt
empirica-host-receipt: ## capture one operator-attested receipt: HOST=... TRANSCRIPT=... STATE=... CHILD_SESSION=... VERSION_OUTPUT=... COMMAND=... OUTPUT=...
	@if [ -z "$(HOST)" ] || [ -z "$(TRANSCRIPT)" ] || [ -z "$(STATE)" ] || [ -z "$(CHILD_SESSION)" ] || [ -z "$(VERSION_OUTPUT)" ] || [ -z "$(COMMAND)" ] || [ -z "$(OUTPUT)" ]; then \
		printf 'usage: make empirica-host-receipt HOST=claude|pi TRANSCRIPT=... STATE=... CHILD_SESSION=... VERSION_OUTPUT=... COMMAND=... OUTPUT=...\n' >&2; exit 2; fi
	@$(PYTHON) $(SCRIPTS)/capture_empirica_live_receipt.py "$(HOST)" --transcript "$(TRANSCRIPT)" --state "$(STATE)" --child-session "$(CHILD_SESSION)" --version-output "$(VERSION_OUTPUT)" --command "$(COMMAND)" --output "$(OUTPUT)" --operator-attested

.PHONY: empirica-host-live-check
empirica-host-live-check: ## require retained installed-host Allow(converged=true) receipts
	@printf '$(BOLD)==> empirica installed-host receipts$(RESET)\n'
	@$(PYTHON) $(SCRIPTS)/verify_empirica_live_receipts.py

## --- Qualify

.PHONY: native-qualification
native-qualification: ## Print the operator-led native qualification skill entrypoint (never launches a host)
	@printf 'Read and follow %s in a supported native host. This target launches nothing.\n' \
		'.claude/skills/native-qualification/SKILL.md'

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

# Dogfooding (see docs/packages.md "Scope and Deduplication" in pi): the committed .pi/settings.json
# adds this checkout as a project-local package and applies autoload:false DELTAs that exclude the
# globally installed siffran resources and the separately installed pi-subagents extension. The
# checkout-bundled exact pi-subagents profile remains active; providers and unrelated packages are
# unchanged. Local edits hot-reload with /reload. Pi asks to trust the folder once.
CLAUDE ?= claude
.PHONY: claude-dev
claude-dev: ## Run interactive Claude with both plugins from THIS checkout; normal user configuration is inherited
	@command -v "$(CLAUDE)" >/dev/null 2>&1 || { printf 'claude-dev: executable not found (set CLAUDE=/path/to/claude)\n' >&2; exit 2; }
	@$(CLAUDE) --plugin-dir "$(CURDIR)/plugins/empirica" --plugin-dir "$(CURDIR)/plugins/methodologist" $(ARGS)

PI ?= pi
.PHONY: pi-dev
pi-dev: ## Run Pi with siffran overridden by THIS checkout (dogfood; other packages unchanged): make pi-dev [ARGS="..."]
	@command -v $(PI) >/dev/null 2>&1 || { printf 'pi-dev: `$(PI)` not found on PATH (set PI=/path/to/pi)\n' >&2; exit 2; }
	@test -f .pi/settings.json || { printf 'pi-dev: .pi/settings.json is missing (it is committed; restore it)\n' >&2; exit 2; }
	@printf '$(BOLD)==> dev pi$(RESET) siffran from %s (project override); answer YES if pi asks to trust this folder\n' "$(CURDIR)"
	@$(PI) $(ARGS)

# Canary: dogfood a pushed PR branch inside a REAL project, not inside siffran. Installs the branch
# as a project-local package there (project wins over the global install; identity is the repo URL,
# so the global entry is shadowed, not duplicated). When pi-subagents is already installed globally,
# filter siffran's bundled copy from the project entry to avoid duplicate tool registration.
# `pi update --extensions` reconciles the clone.
SIFFRAN_GIT ?= git:github.com/obzenner/siffran
.PHONY: pi-canary pi-canary-remove
pi-canary: ## Install a siffran branch project-locally in DIR for dogfooding: make pi-canary REF=<branch> [DIR=<project>]
	@if [ -z "$(REF)" ]; then printf 'usage: make pi-canary REF=<branch-or-tag> [DIR=<project dir, default: this checkout>]\n' >&2; exit 2; fi
	@cd "$(or $(DIR),$(CURDIR))" && $(PI) install -l "$(SIFFRAN_GIT)@$(REF)"
	@$(PYTHON) $(SCRIPTS)/configure_pi_canary.py "$(or $(DIR),$(CURDIR))" "$(SIFFRAN_GIT)@$(REF)"
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

.PHONY: version-set
version-set: ## Set an unreleased plugin version exactly: make version-set PLUGIN=empirica VERSION=2.0.0
	@if [ -z "$(PLUGIN)" ] || [ -z "$(VERSION)" ]; then \
		printf 'usage: make version-set PLUGIN=<name> VERSION=MAJOR.MINOR.PATCH\n' >&2; exit 2; fi
	@$(PYTHON) $(SCRIPTS)/bump_version.py "$(PLUGIN)" "=$(VERSION)"

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
release-check: check empirica-core-integration empirica-governance-check empirica-host-integration empirica-host-live-check ## Deliberate pre-release gate: fast checks, integration diagnostics, installed-host receipts
	@printf '\n$(BOLD)Ready to release.$(RESET) Remaining steps are yours:\n'
	@printf '  1. confirm the version bump is in plugin.json (make status)\n'
	@printf '  2. commit and push\n'
	@printf '  3. open or update the PR\n'

## --- Maintain

.PHONY: vendor-contracts
vendor-contracts: ## Regenerate Empirica's shipped runtime contracts from the repository SSOT
	@$(PYTHON) $(SCRIPTS)/sync_contract_vendor.py

.PHONY: pi-lock
pi-lock: ## Refresh the root Pi package lockfile after dependency changes
	@npm install --package-lock-only --ignore-scripts

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

.PHONY: empirica-public-tools-sync
empirica-public-tools-sync: vendor-contracts ## Regenerate public tool schemas after canonical request edits, then sync vendor copies
	$(PYTHON) scripts/sync_empirica_public_tools.py
	$(MAKE) vendor-contracts
