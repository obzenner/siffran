# Architecture decision records

These are the decisions that describe how Empirica and Methodologist behave today. Each ADR is `accepted` and matches shipped behaviour.

Numbering has gaps because removed ADRs live in git history (`git log --diff-filter=D -- doc/adr`). The rule: an ADR stays only while it describes the shipped behaviour; when its subject is gone or replaced, the file is removed and a surviving ADR carries any context worth keeping.

## Process

The ADR record itself.

- [ADR-0001](0001-record-architecture-decisions.md) — Record architecture decisions

## Contracts and vendoring

Public v2 contracts are vendored into the plugin, served over the MCP revision Claude Code uses, and enforce a strict semantic claim graph with frozen semantic scope.

- [ADR-0042](0042-complete-empirica-v2-through-public-tools-and-host-owned-audit-bindings.md) — Complete Empirica v2 through public tools and host-owned audit bindings
- [ADR-0043](0043-vendor-runtime-contracts-inside-the-empirica-plugin.md) — Vendor runtime contracts inside the Empirica plugin
- [ADR-0044](0044-implement-claude-codes-current-mcp-revision.md) — Implement Claude Code's current MCP revision
- [ADR-0045](0045-use-a-strict-root-connected-claim-dependency-dag.md) — Use a strict root-connected claim-dependency DAG
- [ADR-0046](0046-make-supportedby-a-scoped-conjunctive-prerequisite.md) — Make SupportedBy a scoped conjunctive prerequisite
- [ADR-0047](0047-bind-frozen-scope-to-semantic-identity.md) — Bind frozen scope to semantic identity

## Gate and audit

Deterministic evidence and a distinct, host-observed audit gate convergence; route precedes investigation.

- [ADR-0013](0013-deterministic-gate-is-the-trust-boundary-agentic-review-is-a-secondary-sensor.md) — Deterministic gate is the trust boundary; agentic review is a secondary sensor
- [ADR-0020](0020-mandatory-run-protocol-routing-fan-out-and-evidence-bound-verification.md) — Mandatory run protocol: claim graph, two-fold validation, fan-out, and independent audit
- [ADR-0025](0025-per-claim-audit-verdicts-keyed-on-claim-and-evidence-digests.md) — Per-claim audit verdicts keyed on claim and evidence digests
- [ADR-0027](0027-restore-per-graph-audit-freshness-argument-digest-refutation-coverage-and-sample-counts.md) — Restore per-graph audit freshness: argument digest, refutation coverage, and sample counts
- [ADR-0048](0048-hard-gate-route-before-investigation.md) — Hard-gate route before investigation
- [ADR-0050](0050-bound-stale-pending-audit-replacement-to-audit-capacity.md) — Bound stale pending-audit replacement to audit capacity
- [ADR-0051](0051-separate-managed-audit-execution-from-generic-child-tier.md) — Separate managed-audit execution from the generic child tier
- [ADR-0052](0052-separate-harness-compatibility-from-receipt-provenance.md) — Separate harness compatibility from receipt provenance
- [ADR-0056](0056-align-bootstrap-admission-and-agent-guidance.md) — Align bootstrap admission and agent guidance

## Governance and budgets

Separate ceilings are sized to the task and approved before investigation, with host-owned governance.

- [ADR-0017](0017-bound-the-loop-with-a-harness-enforced-spawn-budget.md) — Bound the loop with a harness-enforced spawn budget
- [ADR-0049](0049-isolate-investigation-and-mandatory-audit-spawn-budgets.md) — Isolate investigation and mandatory-audit spawn budgets
- [ADR-0060](0060-consolidate-empirica-4-0-governance-and-evidence-boundaries.md) — Consolidate Empirica 4.0 governance and evidence boundaries
- [ADR-0063](0063-size-the-run-configuration-to-the-task-and-approve-it-before-investigation-in-both-control-modes.md) — Size the run configuration to the task and approve it before investigation in both control modes

## State and artifacts

Operational run state (active generations, freeze, freshness) and Git-backed knowledge artifacts are distinct planes.

- [ADR-0019](0019-active-run-manifest-for-identity-fail-closed-and-bounded-termination.md) — Active-run manifest: run identity, fail-closed gating, and bounded termination
- [ADR-0026](0026-freeze-mode-bound-discovery-so-a-run-can-close-not-just-terminate-at-the-cap.md) — Freeze mode: bound discovery so a run can close, not just terminate at the cap
- [ADR-0029](0029-record-per-run-exit-codes-and-automate-re-gating-after-a-formatter.md) — Record per-run exit codes and automate re-gating after a formatter
- [ADR-0031](0031-split-operational-state-from-git-backed-knowledge-artifacts.md) — Split operational state from Git-backed knowledge artifacts
- [ADR-0059](0059-batch-git-artifact-reads-without-changing-publication.md) — Batch Git artifact reads without changing publication

## Hosts and compatibility

Adapters share one host-neutral bridge; Claude and Pi translate into it.

- [ADR-0030](0030-put-host-neutral-contracts-between-plugin-cores-and-harness-adapters.md) — Put host-neutral contracts between plugin cores and harness adapters
- [ADR-0032](0032-port-claude-completion-gating-to-a-gated-domain-operation-on-pi.md) — Port Claude completion gating to a gated domain operation on Pi
- [ADR-0033](0033-activate-claude-through-the-host-neutral-bridge.md) — Activate Claude through the host-neutral bridge
- [ADR-0038](0038-warn-when-the-checkout-is-behind-the-installed-plugin.md) — Warn when the checkout is behind the installed plugin

## Empirica 4.0 consolidation

Withdrawn modes stay withdrawn and the legacy theta/unknowns/race models are retired.

- [ADR-0061](0061-withdraw-the-cli-exec-and-multi-provider-run-modes-from-empirica-4-0.md) — Withdraw the cli_exec and multi_provider run modes from Empirica 4.0
- [ADR-0062](0062-retire-confidence-theta-spec-unknowns-and-racing-convergence-models.md) — Retire confidence theta spec unknowns and racing convergence models
