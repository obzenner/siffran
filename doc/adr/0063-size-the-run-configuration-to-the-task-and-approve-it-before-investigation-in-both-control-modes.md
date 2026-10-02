---
number: 63
title: Size the run configuration to the task and approve it before investigation in both control modes
status: accepted
date: 2026-10-01
tags:
- empirica
- governance
- budgets
- auto
links:
- target: 53
  kind: Amends
- target: 60
  kind: Amends
- target: 61
  kind: Amends
- target: 64
  kind: amendedby
---

# Size the run configuration to the task and approve it before investigation in both control modes

## Context and Problem Statement

Every run has three ceilings: `max_passes`, `max_spawns` and `max_audit_spawns`. StartRun seeds
them with constants, 8/1/1, in `application/transaction.py`. Claude and Codex can override them
through `EMPIRICA_MAX_*`. Either way, the values are chosen before anyone has seen the task.

In `--auto` mode (ADR-53, as consolidated by ADR-60 and ADR-61), the adapter accepts the proposal
immediately: there is no human and no dialog (`approval_kind: auto`). Auto may lower a ceiling but
can never raise one (`governance.auto_ceiling`). So a five-claim change and a two-week redesign
start from the same allowance, and nobody evaluates whether it fits.

In deliberative mode the author proposes `configure_run` after building the claim graph, so it
could size the ceilings, but nothing asks it to. In every native 4.0 qualification run the author
proposed the defaults or a small cut to passes. The dialog shows numbers without any reasoning.

The single audit makes the flaw decisive. Both parallel auto runs (`auto-485c85e`) lowered passes
to 6 but kept the single audit. Each failed its first audit on the merits and could not retry, so
both stopped `stopped_residual`. Across the eight native 4.0 runs, six first audits failed.
ADR-34 identified the same failure mode for fixed pass constants.

## Decision Drivers

* The ceilings must be sized for the task by the agent that knows the task, and a human must see
  that sized plan before work begins.
* A run never raises its own ceiling without a distinct principal (ADR-19, ADR-28). The author
  proposes; only a human, or a documented operator policy, accepts.
* The modes must differ in a way that matters: whether the run may interrupt the human after it
  starts.
* Unattended (delegated) use stays bounded by a fixed policy.
* The core stays host-neutral.

## Considered Options

* **Status quo.** Rejected: an ungrounded budget is accepted, and one failed audit ends an auto run.
* **Auto asks the human again when a ceiling runs out.** Rejected: auto would be deliberative minus
  its first dialog, and the modes would differ by accident.
* **The core computes the ceilings from graph shape by formula.** Rejected as the mechanism: graph
  shape does not capture difficulty, and a formula is another hand-picked constant. The core keeps
  only the schema bounds and the authority rules below.
* **The author sizes all three ceilings with a rationale; a human approves that plan before
  investigation in both modes; the modes differ only in later interruptions.** Chosen.

## Decision Outcome

Chosen option: **author-sized configuration, approved before investigation**.

### The sized proposal

StartRun creates an **unapprovable placeholder**: the seed ceilings, with no rationale. No
presentation or approval may bind the placeholder.

Before any presentation or approval, the author must issue a graph-selected `configure_run`
(ADR-56) carrying:

* all three ceilings explicitly;
* a nonblank **rationale** of 1 to 600 characters.

The rationale explains the size from the task and the proposed graph: for example, the number of
gating claims, how many need experiments, and the audit rounds expected. Repository investigation
is not required to justify it. Investigation remains blocked until a configuration is approved.

The rationale is bounded author explanation. It is not a command, an authority claim, evidence, or
an approvable conclusion about the work. The core stores the exact text in the proposal, and the
proposal digest covers it, so an approval binds the numbers together with the stated reasoning.

Hosts render the rationale as escaped literal text under the label "Agent sizing rationale —
unverified". Rendering neutralizes terminal controls, markup, and fence breakouts, deterministically
and without hiding meaningful content. The structured numeric fields and the host-owned controls
remain authoritative. Approval authorizes resource ceilings and control policy, not the truth of
the rationale or of the graph.

A human amendment changes ceilings only, and human submissions can never modify or inject a
rationale. The original rationale is kept and shown with the warning "Written for the original
proposal; not regenerated for these human-edited values". The locked confirmation shows both the
original and the amended values.

The rationale is visible to the author as proposal data. Private presentations and receipts stay
private.

The seed audit ceiling becomes 2, through the single operational default. This permits a first
audit and one retry. It does not guarantee convergence or authorize a third attempt, and authors
may propose a different task-sized audit ceiling.

### Authority: initial approval, then monotone

The core keeps a durable fact: whether this run has had a **first successful approval**. It is never
inferred from `plan_revision`, the current pending state, or the latest approval kind.

**Before the first successful approval:**

* The author may propose ceilings within schema bounds, including above the seed, because a human
  or the fixed delegation policy must still accept them.
* A human may amend them, within schema bounds.
* Only an exact final approval installs ceilings: a human decision, or delegated acceptance under
  the policy below.

**After the first successful approval:**

* An automatic acceptance must be componentwise no greater than the currently effective approved
  ceilings, and no less than the consumed counters.
* Deliberative revisions still return to the human, who may raise ceilings through the dialog.
* Pending, rejected, or revision-pending status never reopens initial authority. Neither do
  rationale changes, context refresh, restore, or capability loss.
* Configuration changes never reset or refund consumed counters.

### Deliberative mode

Unchanged apart from the sized proposal and the rationale display. The host presents the
proposal, and the human may dismiss, amend (with locked confirmation), approve, or reject. Every
later configuration change returns to the human (ADR-55, ADR-57).

### Auto mode: one human approval episode, then unattended

For an interactive invocation, the initial sized proposal is presented in the same host dialog as
deliberative. "One approval" means one successful pre-investigation approval **episode**. An
episode may include bounded presentations and a locked amendment confirmation. It is not limited
to one widget or one receipt.

* Dismissal, timeout, cancellation, rejection, or a declined confirmation grants no consent and
  installs no budget. It never falls back to automatic acceptance. The host settles that human
  wait nonterminally with an explicit not-converged notice, and investigation stays blocked.
  There is no automatic re-prompt. A fresh human-directed review may resume within the existing
  interaction limits, or the run stops honestly.
* An interactive invocation must use human approval. If its approval UI is unavailable, the run
  fails closed. It never silently downgrades to delegation, even when delegation is also recorded.

After the first successful approval, auto never presents again:

* A change that raises any ceiling is refused with `governance.auto_ceiling`.
* A change that raises nothing is accepted automatically (`approval_kind: auto`).
* Revisions are bounded in two separate allowances of **eight** material revisions each:
  * one before the first successful approval, counted from the first complete sized proposal;
  * one after the first successful approval.

  Every distinct change to ceilings or rationale counts, including a human budget amendment.
  The initial complete proposal, presentation reservations, locked confirmation, and exact
  no-op replay do not count. A proposal or amendment that would exceed the applicable allowance
  is refused before mutation. The first successful approval starts the post-approval allowance
  once and never renews the pre-approval one. Exhaustion is a residual stop, not a human wait.
  Presentation limits stay at three per material revision and 128 per run, with bounded
  same-call confirmation.
* Exhausting a ceiling or the revision limit returns a canonical residual-stop recovery: start a
  new run with a larger approved size, or use deliberative mode. It never produces an automatic
  re-prompt or a `budget.raise` loop, and auto projections drop `budget.raise` as a recovery.
* Capability loss after the initial approval cannot create a new human wait.

### Delegated auto: a fixed operator policy

When no human is present and the operator recorded `EMPIRICA_AUTO_DELEGATION=1` (ADR-60), the
delegation provenance authorizes one documented, fixed, immutable policy envelope: **8/1/2**. The
author supplies the sizing and the rationale, but not the authority.

* The envelope is not computed from proposals. StartRun budgets and `EMPIRICA_MAX_*` can only
  narrow it, never enlarge it. Contradictory inputs are refused rather than silently reinterpreted
  as a larger policy.
* The initial delegated acceptance must satisfy the envelope and the schema and consumed-counter
  bounds. Every later delegated revision must also be componentwise non-increasing from the last
  effective approved configuration.
* A proposal above the envelope is refused with `governance.auto_ceiling`. The recovery is to
  reduce scope or to use an approval-capable host interactively.
* Delegated runs hold no human approval receipts. Delegation is never inferred from a missing UI,
  and Codex without delegation remains refused.

The fixed envelope is a bounded launch policy, not evidence that every task fits within it.

### Receipts

Every receipt persists an immutable `approval_kind`. The decision or presentation fingerprint
binds it, and it is validated on every finalization and restore.

* A `host_ui` receipt requires a prior exact presentation reservation and the shared human-submission
  resolver.
* An `auto` receipt has no presentation fingerprint and no human submission. It can approve only
  when the current phase and the immutable invocation authority permit automatic acceptance.
* A presentation or an amendment is never an approval.
* An approved state must be supported by the matching successful approval receipt for its exact
  configuration epoch, digest, and kind.
* Interactive auto permits one successful human approval episode; every later successful
  approval is automatic.

Decision admission, receipt construction, both invariant branches, the persisted and private
schemas, and the validators change together.

Replay is still handled before current policy: an exact historical human replay after a later auto
approval stays inert and never opens a new dialog. Altering a stored receipt's kind, or finalizing
a human reservation with an auto decision, fails closed. Persisted state from earlier unreleased
builds fails closed and requires a fresh run.

### Host-neutral boundary

Adapters observe invocation and delegation provenance and UI capability. They handle native
presentation, cancellation, and lifecycle settlement. The application admits those facts through
trusted private ingress as neutral policy inputs.

The core alone decides:

* the permitted approval kind and phase eligibility;
* the finite limits and componentwise ceiling comparisons;
* exact consent and receipt validity.

The core imports no host SDK, recognizes no host names, and reads no environment variables. Adapter
UI selection never grants authority that core admission would reject. The Claude Stop gate keeps
its exact-reason check, and Pi behaves the same way: only the single legitimate initial-approval
blocker is a human wait. Mixed blockers, malformed responses, exhausted interaction capacity, and
post-start auto revision failures are not.

### Termination scope

These rules bound resources: ceilings, revisions, presentations, and receipts. They do not prove that
every session ends in finite wall time. An indefinite human wait is not investigation or
convergence, and arbitrary host inactivity is out of scope.

### Consequences

* Good, because the budget reflects the task, a human sees and owns it before work starts, and an
  auto run can converge after one round of audit findings.
* Good, because the modes now differ by one honest property: interruptions after the start.
* Bad, because auto is no longer zero-touch on interactive hosts; it asks once.
* Bad, because the governance proposal, receipts, digests, dialogs, fixtures, goldens, and native
  qualification all change.
* Neutral, because delegated use stays bounded by a fixed, documented policy.

### Confirmation

Confirmation is a **transition matrix** covering:

* initial interactive approval;
* initial delegated approval;
* post-approval auto;
* deliberative revision;
* unavailable ingress;
* restored state;
* terminal state.

It must include negative tests for each of these:

* approving the placeholder without sizing;
* a missing, blank, or oversized rationale;
* a missing ceiling;
* an initial human proposal or amendment above the seed (allowed);
* a later raise after a decrease;
* a delegated lower-then-raise;
* inflation through a seed or environment override;
* kind-forged receipts;
* auto decisions without delegated or prior-human authority;
* amendment receipt multiplicity;
* dismissal, rejection, and timeout;
* a lost UI;
* stale and concurrent finalization;
* historical replay after a kind transition;
* a tampered rationale;
* consumed-counter minimums;
* revision and interaction exhaustion;
* a zero-audit proposal producing an honest inability to converge.

The change also covers:

* contract and shared-schema generation;
* the response, private, and persisted schemas;
* strict state validators, fixture digests, and public tool and bootstrap examples;
* all three adapters;
* the skill, governance, and budget documentation;
* author-view privacy assertions;
* an architecture assertion for the core boundary, and cross-adapter conformance cases for
  identical neutral facts.

Native qualification on Claude and Pi exercises a real initial approval, a human amendment with
locked confirmation, cancellation and wait recovery, and an audit retry. Codex exercises delegated
sizing without a dialog, envelope refusal, and non-delegated refusal. Native receipts distinguish
configuration approval from independently audited convergence.
