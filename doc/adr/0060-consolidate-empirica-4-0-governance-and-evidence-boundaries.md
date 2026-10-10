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
- target: 56
  kind: Amends
- target: 61
  kind: Amended by
---

# Consolidate Empirica 4.0 governance and evidence boundaries

Supersedes ADR-0024, ADR-0041, ADR-0053, ADR-0054, ADR-0055, ADR-0057, ADR-0058 (removed from the tree; see git history). ADR-0024 is removed from the tree; its optional modes were withdrawn by ADR-0061.

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

Canonical digests and content-addressed artifacts use `core/canonical.py`. Operational state files
retain their separate deterministic, pretty-printed encoding in `adapters/state/fsio.py`; this
decision does not change their bytes. Runtime-derived contract
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

## Amendment (2026-10-09): Pi picks the auditor from `enabledModels`

On Pi the auditor model was `agentOverrides["empirica.empirica-auditor"].model`, then `subagents.defaultModel`, then the main model, and `/empirica` was refused when it fell in the author's identity class, which on a one-family default meant every start. Now an explicit per-agent override is still the user's word and is never replaced (a same-class override is still refused). Otherwise, when the resolved model is in the author's class, the adapter takes the first entry of Pi's `enabledModels` (project settings replace user settings; patterns expand over the models available now) that is available and that the same classifier puts in another class, uses it for the start and for the audit launch, and says which model it picked and which it replaced in the start notice. No candidate means the same refusal, now naming `enabledModels`. Identity is still proven only by the observed child session, and an author-supplied model is still rejected. This is what Claude's adapter already does with a reviewer family other than the main one (`adapters/claude/lifecycle.py`, `_auditor_alias`).

The class the auditor must differ from at launch is not only the author's: it is the author's and that of every producer of the evidence the audit would cover. The full set governs selection and reservation at launch. The verdict's independence stays per artifact: it is judged against the producers of the covered evidence (it has always refused an auditor equal to one of them, after the spawn was spent), and a later author switch does not invalidate a verdict already produced by a distinct model. The core owns the launch set (`core.evaluation.audit_exclusions`) and refuses a reservation whose `auditor_identity` is in it (`audit.auditor_excluded`, no spawn consumed; its text names the models and the fix). Pi's pick skips every class in the set at audit launch and refuses an explicit override in it; Claude's alias skips their families and refuses a `CLAUDE_CODE_SUBAGENT_MODEL` in it before launch.

On Claude (G17b) one resolver, `adapters/claude/lifecycle.py::_resolved_identity`, maps an alias (or a concrete model name) to the identity class it runs on: the alias's `ANTHROPIC_DEFAULT_<ALIAS>_MODEL` pin, classified, or `None` when unpinned. One decision, `_effective_identity`, serves both the automatic pick and an explicit `CLAUDE_CODE_SUBAGENT_MODEL`: the resolved identity, or for an unpinned alias the excluded class of its family. A launch is refused exactly when that identity is in the core's set, and the identity is handed to the atomic reservation to judge. Likewise one function, `_audit_outcome`, owns what a Claude audit child delivered: the verdict and the served model of the message(s) that carried it, from one parse of the transcript; the model must be the one non-synthetic model of the whole child transcript.
