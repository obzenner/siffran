---
number: 60
title: Consolidate Empirica 4.0 governance and evidence boundaries
status: accepted
date: 2026-09-27
tags:
- empirica
- governance
- identity
- contracts
links:
- target: 53
  kind: Supersedes
- target: 54
  kind: Supersedes
- target: 55
  kind: Supersedes
- target: 57
  kind: Supersedes
- target: 58
  kind: Supersedes
- target: 56
  kind: Amends
---

# Consolidate Empirica 4.0 governance and evidence boundaries

## Context and Problem Statement

ADRs 53–58 grew approval from run configuration into control over the claim graph and reviewer,
placed model interpretation in the host-neutral core, and enforced implementation size through a
line-count ceiling. Native investigation and subsequent design review showed that these concerns
protect different trust boundaries: approval authorizes resource configuration, while the audit
must bind current work and host-observed identities independently. The unreleased 4.0.0 contract
can make that separation explicit without migrating historical run state.

## Decision Drivers

* Human approval must not be misrepresented as approval of model-authored work.
* The core must derive decisions from host-neutral, admitted facts and compare identity classes
  without interpreting model names.
* Unknown or mixed producer attribution must fail closed rather than manufacture independence.
* Agent-facing blockers and obligations must be complete, deterministic, and agree with the gate.
* Committed decisions need a durable observation basis without overstating replayability.
* Correctness, clear layering, and repository conventions are the engineering fitness criteria;
  source line count is not one.

## Considered Options

* Preserve graph and reviewer approval while repairing each host independently.
* Remove independent-audit identity checks.
* Separate configuration authority, host identity observation, and core derivation.

## Decision Outcome

Chosen option: **separate configuration authority, host identity observation, and core
derivation**, because it keeps the trusted kernel small while retaining exact consent, audit
coverage, and fail-closed behavior.

### Governance and invocation

Host-mediated approval covers run configuration only: budget ceilings, modes, and control mode.
The immutable goal is displayed as read-only context. The claim graph defines the work and remains
bound by the audit dossier, but neither graph content nor reviewer choice is approvable. Graph
changes therefore invalidate stale audit coverage without revoking configuration approval.
Configuration changes advance a monotonic epoch and require a fresh exact decision. Receipt replay,
CAS, bounded presentations, and same-call locked confirmation remain in force.

Reviewer selection belongs to host configuration. Claude honors an explicit
`CLAUDE_CODE_SUBAGENT_MODEL`; otherwise its adapter supplies only a resolvable alias from a family
different from the observed main family, with third-party family aliases requiring the documented
`ANTHROPIC_DEFAULT_<FAMILY>_MODEL` pin. Pi selects the package scope that resolves this checkout's
canonical auditor and applies host settings in order: the canonical agent override, subagent
default, then main model. Selection and preflight are not identity evidence: each adapter must
observe the model that produced the admitted verdict.

A run requires a nonblank goal and stores its exact argument substring. Trusted host activation
records invocation provenance. Automatic mode is admitted only for an interactive invocation or an
operator-recorded `EMPIRICA_AUTO_DELEGATION=1`; otherwise StartRun returns a structural `Block`
without a run. Reason dispositions in the public contract tell adapters how to present refusal and
recovery. The approval wait limit is a host decision timeout, not a total run deadline.

### Identity, evidence, and audit

Only adapters interpret raw provider/model strings. They retain raw provenance and normalization
policy version while producing a conservative canonical identity-equivalence class. The core treats
that class as opaque and compares it only for equality.

The host-observed producer identity is attached when each evidence artifact is admitted, rather
than inferred from the current author at audit time. Audit coverage must have an observable,
single producer class, and the verdict-producing reviewer class must be observable and different.
Mixed or unobservable producers, an unobservable reviewer, and a same-class reviewer all block.
This establishes only a **distinct** reviewer; it does not claim statistical decorrelation.

One pure blocker derivation supplies both gate decisions and projection. Obligations preserve the
claim text and report Required, Observed, Missing, and Next for every open claim. Terminal residuals
come from the same derivation and do not invent actions for an immutable terminal run.

### Persistence and contract maintenance

Every committed decision links to a content-addressed observation-basis record for the exact
pre-plan workspace capture and policy inputs used by that transaction. Unchanged bases reuse their
prior digest; read-only commands remain read-only. These records make decisions auditable, not
fully replayable: full replay also requires reconstructible historical state, policy
implementations and versions, and decision-affecting clock semantics.

All persisted JSON uses `core/canonical.py` as the canonical serializer. Runtime-derived contract
fixtures and shared schema definitions are generated and checked through `make contract-fixtures`
and `make contract-schemas`; hand-maintained copies are not authoritative.

The ARCH-BUDGET line-count rule and ADR 56's ceiling clause are superseded. Architecture checks
enforce ownership and dependency rules only; they do not measure size. Correctness, tests, clear
responsibilities, and repository conventions determine acceptance. No documentation or checks may
be removed merely to meet a line count.

### Compatibility and retained decisions

The plugin remains 4.0.0 and public contract 3.0.0. Incompatible pre-4.0 governance state fails
closed; a new StartRun creates a fresh generation without migrating or carrying approval.

This ADR supersedes ADRs 53, 54, 55, 57, and 58. It also supersedes only ADR 56's architecture
line-count ceiling; ADR 56's graph-before-configure admission, shared bootstrap guidance, and
host-owned locked confirmation remain in force. ADR 59's Git batching, validation, publication,
and CAS invariants remain entirely in force.

### Consequences

* Good: human authority is narrow and accurately represented.
* Good: model knowledge stays at host edges while the core retains a strict audit rule.
* Good: gate output and agent guidance cannot drift into separate blocker policies.
* Good: persisted decision bases improve auditability without a false full-replay promise.
* Cost: host adapters must observe verdict-bearing identities and reject ambiguous native evidence.
* Cost: incompatible unreleased state requires a fresh generation.

### Confirmation

`make check` validates contracts, generated fixtures and schema definitions, host adapters, and the
fast core. `make empirica-core-integration` exercises governance, strict state, transaction,
observation-basis, and behavioral matrices. `make empirica-host-integration` exercises simulated
host lifecycle composition. Installed-host support still requires the separately authorized,
operator-present native qualification procedure and retained receipts; simulated tests do not
establish it.
