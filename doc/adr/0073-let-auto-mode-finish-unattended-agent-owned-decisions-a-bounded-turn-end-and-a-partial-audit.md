---
number: 73
title: "Let auto mode finish unattended: agent-owned decisions, a bounded turn end, and a partial audit"
status: proposed
date: 2026-11-17
tags:
- empirica
- governance
- evidence
- auto
links:
- target: 63
  kind: Amends
- target: 67
  kind: Amends
- target: 20
  kind: Amends
---

# Let auto mode finish unattended: agent-owned decisions, a bounded turn end, and a partial audit

Amends ADR-0063, ADR-0067 and ADR-0020. Status *proposed*: this record grows with each PR of the stack, so that at every merge it describes only behaviour that has shipped, and it is accepted in the top PR of the landing. Only D1 is written so far.

## Context and Problem Statement

ADR-0063 defines auto as "one human approval episode, then unattended" (section "Auto mode: one human approval episode, then unattended"). ADR-0067 and ADR-0020 still make every `needs-decision` claim the human's, in every mode: ADR-0067 §"`needs-decision`" (line 139: "The claim is `blocked` until it is answered"), its scenario row H "Only a `needs-decision` claim" (`evidence.confirmation_required` with `claim_ids`, "Approve converges after an audit as `human_owned`"), and ADR-0020 line 141 ("`needs-decision` claims cannot be validated by the agent; surface to the human, blocked"). So an auto run with one decision claim cannot finish without a human, which contradicts "unattended"; in a delegated run nobody can be asked at all.

## Decision Drivers

* An auto run should end without a human once the configuration is approved.
* A human's answer must keep deciding wherever one exists, and deliberative runs must not change.
* The auditor, not the author, judges whether the author's choice is supported.

## Considered Options

* Refuse the `needs-decision` kind in auto. It erases the decision/fact distinction, and the hand-over loses the agent's choices.
* A human batch at the end of an auto run. It is contrary to the operator's direction.
* Make the claim the author's in auto and derive it by fold 1, after the existing order (chosen).

## Decision Outcome

### D1 In auto, a `needs-decision` claim is author-owned and derived by fold 1

* **State.** `claim_state_cause` keeps its order: conflict, failed spike, refuting research, human answer. A `needs-decision` claim with no answer is then derived like an `ordinary` claim in auto (supporting research that counts approves it; none leaves it open with `claim.research_missing`; supporting plus refuting is a conflict). In deliberative it stays `blocked` on the human, exactly as in ADR-0067. An existing human answer decides in every mode.
* **Batch.** `pending_decisions` is empty in auto, so no decision is in the human batch. A retrievable record that supports a decision still waits for its own confirmation.
* **Independence.** An approved author-decided claim rests on research that has a producer, so it is never `human_owned`; `human_owned` is kept for a human-answered decision with no evidence (ADR-0067 §"Human-owned claims sit outside producer independence").
* **Auditor.** The shared rubric judges that an approved decision without `human_decision` was decided by the author: its cited evidence must support the chosen option over the alternatives the claim or its evidence names, or the claim fails; when neither names an alternative, the claim fails and the auditor says which alternative, or the status quo, needs a comparison. The verdict covers the claims in `reviewed_claims`.
* **Validator.** `scripts/validate_contracts.py` invariant 13 accepts an approved `needs-decision` claim with an approving `human_decision`, or with active supporting research and no refuting or failing evidence, and rejects a non-approving `human_decision` always. The view carries no control mode, so the mode rule is the core's.
* **Contract and registry.** `public-contract.json` qualifies by mode every sentence that made a decision human-only. That text changes the `getcontract-index` golden, which deliberative runs also read: the accepted exception to "deliberative byte-identical". Deliberative notices, author views and turn-end results are unchanged. `host-boundaries.json` scopes EHB-DLG-07 to deliberative runs and adds EHB-DLG-08 for auto decisions.
* **UI view.** `decided_by_author` and `rejected_by_evidence` are added to the decision `status` enum of `empirica-ui-view/v1` (produced from A5 of the stack).

### Additive closed-enum values

An additive value in a closed enum is admissible within `empirica/v2` and `empirica-ui-view/v1`, without a new contract version. The consumers (the core, the observer, the UI and the hosts) ship together and are validated against the contract by `make contract-check`, so a value no consumer knows cannot reach one within one plugin release; an older reader falls back to its `unknown` state.

### Consequences

* Good, because an auto run with decisions converges unattended, and the auditor reviews the choice.
* Bad, because an author can now approve its own decision; the audit rubric and its independence classification are the check, and the reversal condition below watches for it.

### Reversal condition

Field runs or the native qualification show agent-decided claims passing the audit with unsupported choices.

## To be filled by later PRs of the stack

### D2 Auto turn end

An auto run keeps going while the author has a step, and ends honestly when it has none. The core says which (D2.3); a host reads that and nothing else.

#### D2.4 The notices and the cells of the turn-end table

The settlement notice `unattended_residual` is new. It names the core's reason and makes the agent's choice: if no step of the agent's moves the run, end honestly with `report_convergence intent=stop`; otherwise act on the reason, or revise the claim graph and continue, unless the claim graph is frozen: a frozen run admits no revision, so that way on is offered only where it exists. It is the one notice that offers the stop, and it is carried by three kinds of turn end, so it claims only what holds for all of them. It is true for every cause of the actor `none` (a refuted or a conflicted root, frozen or not: the core's reason names the root, and no step of the agent's moves the run) and for the last continuation and the settlement at a bound, where the author still has a step and the notice asks for it first. It never says that the run cannot converge, which is false for an author with a step. The registry's turn-end table (`turn_end` in `host-boundaries.json`) reads the run view's `turn_end_actor` in each auto cell:

| `turn_end_actor` | Auto turn end |
|---|---|
| `author` | continue with the core's reason, then `unattended_continue` (which asks the agent to act on the reason and does not offer the stop) on a later turn end of the chain; the last continuation before a bound carries `unattended_residual`, and the K-th turn end on one basis, or the T-th in the chain, ends with it |
| `auditor` | the pending-audit path settles the turn; the child's result continues the run |
| `human` | settle with `human_wait`: the configuration dialog, or in interactive auto the budget-raise dialog of D3.1. Nothing continues over a dialog |
| `none` | continue, with `unattended_residual` as the directive, at the first turn end and at a later one on a changed basis; a turn end on the basis the previous one saw ends the chain with `unattended_residual` (the no-progress rule), so `end-no-progress` has one meaning on both hosts |

A delegated auto run never has the actor `human`: nobody can be asked, its configuration is approved by the host's policy, and a source that needs a human is inert, so the `human-wait` cells of that mode are `unreachable`. A cell in the core test suite proves it across the states a delegated run reaches, and the observer's state machine asserts it in every state it generates. A deliberative run is unchanged: it pauses.

The actor `none` continues rather than settles because a settled turn hands the run to a human: on Claude a Stop `systemMessage` may never reach the model, and in delegated auto nobody is there, so the run would stay active and stall. The agent has to receive the notice and act on it, and revising the graph is a step it has.

The auto cells hold no evidence row: the `evidence-wait` cells of every auto mode are `unreachable`, and so are the budget cells the core cannot produce (before investigation nothing is exhausted, interactive auto after its first approval is always offered `budget.raise`, and a delegated auto run never is). Each is named by a scenario whose test proves it. `evidence_wait` and `evidence_report` are deliberative notices, and an auto run is handed the claim's own `claim.research_missing`, so both hosts lost the branch that directed an auto run to report. The contract still lists the `evidence_report` notice and no host sends it. A host that reads a run view without `turn_end_actor` (the response schema requires it) does not read it as `none` or as `author`: Claude's transport refuses the view and the Stop fails closed; Pi takes the turn-end path it took before the field.

**The bound on continuations** is defined once, in the registry's `turn_end.bounds`: With K = 3 and T = 16: the K-th turn end of a chain on one observation basis, or the T-th turn end in a chain, ends the chain with the `unattended_residual` notice, so at most K − 1 continuations on one basis and T − 1 in a chain. The last continuation before a bound, the (K − 1)-th on one basis or the (T − 1)-th in a chain, carries `unattended_residual` as its directive, so the model is offered the stop while it is still reading. A changed basis restarts the count on the new basis; any turn end that is not a continuation ends the chain and clears both counts. A continuation that carries the `unattended_residual` directive (the actor `none`) counts like any other. **Both hosts enforce K and T.** Pi's in-memory record is `{basis, count, total}`, kept for one run and one chain: an unchanged basis adds one to `count`, a changed basis restarts it at 1, every continuation adds one to `total`, and the turn end that would reach K on a basis or T in the chain ends the turn instead. The record is cleared by any outcome that is not a continuation, by an error while reading the run, when no run is active, when another run becomes active, and when a person's prompt starts a new chain. A record Pi cannot read (a bare basis from an older version, or a malformed one) reads as a changed basis with zeroed counts. K and T are two constants in the Pi adapter, read against `turn_end.bounds` by a test. Claude keeps the same record, `{run, basis, count, total}`, per session in a file written atomically, and reads it only on a Stop that follows a blocked one (`stop_hook_active` literally `true`), so a Stop that starts a chain begins at 1, and only for the run it was written for. A bare basis from an older version, or any unreadable record, reads as a changed basis with zeroed counts. K and T are the constants `CONTINUATION_K` and `CONTINUATION_T` in `adapters/claude/completion.py`, read against `turn_end.bounds` by the same test as Pi's. Claude's own cap of eight consecutive continuations resets each time Claude calls a tool, so it bounds a run of continuations without a tool call and no productive chain: the bound that holds is Empirica's own. The native session checks the cap and the reset as evidence only. The registry names both hosts in `bounds.enforced_by`.

**The model sees the stop before the bound.** The last continuation, the (K − 1)-th on one basis or the (T − 1)-th in a chain, carries `unattended_residual` as its directive instead of `unattended_continue`. A Claude Stop that settles shows its `systemMessage` to the user, and whether a Pi custom message with no continuation reaches the model is not verified, so the notice that offers the stop goes where the model reads it: the stderr of a blocking Stop on Claude and the appended message of a continuation on Pi. The K-th or T-th turn end then settles with the same notice, for the person or the log.

Only the residual offers the stop. `unattended_continue`, the notice of a later continuation, names the core's reason, says that auto mode asks nobody at this turn end and asks the agent to act on the reason, so an agent that is still working is not invited to quit. The last continuation before a bound is the exception, and it carries the residual.

#### D2.3 A core-owned signal for the turn end

The run view gains `turn_end_actor`, an additive closed enum: `author`, `auditor`, `human` or `none`. The core computes it in `core.evaluation.turn_end_actor` from the actor of the first admissible requirement of the report (`first_admissible`): an agent step is `author` (claim work, and launching an audit, or launching it again after one failed); a human step is `human`; a running audit child (`audit.pending`) is `auditor`, because the turn settles and the child's result continues the run, which is what both hosts already do on a pending async audit and a continuation would only spin until the bound. It is `none` when nothing is owed, when the run has ended, and when the root claim is refuted or conflicted (`claim.refuted`, or `evidence.conflict` on the root) and no human is being asked, because no further research can approve the root as its claims stand and the agent's choices are to stop honestly or revise the claim graph; a human who is being asked is never hidden. The value is core-owned so that both hosts read one answer and derive nothing from reasons, obligations or notices. It is carried by every response that carries the run view and is not rendered in the author view.

### D3 and D6 An auto run asks a human only at its configuration

The rule: an auto run asks a human at its configuration only. It asks nothing else until the run ends: no evidence
confirmation, no decision, no consent to end. A human who wants to confirm evidence starts the run in deliberative mode.

The lock: `core.confirmation.human_route` returns `unavailable` for every auto run, as it already did for a delegated
one, whatever surface the host recorded. Every ask reads that one fact. The batch's `prompt_error` is `unavailable`, so a
host shows no dialog and directs no report to a human, and the core refuses a presentation or a receipt
(`governance.approval_unavailable`). A dialog opened before an upgrade cannot land its answer. Both hosts and the observer
read the core's route and decide nothing themselves. A deliberative run is unchanged, and so is an auto run's first
configuration approval.

The inert rule, a second guard that stands without the lock: in an auto run a research record that needs a human
(`core.confirmation.asks_human` is false in auto) is inert. It stays in the dossier as the author recorded it and in the
evidence digest, so the auditor sees it with `status: pending` and `retrieve: false` and the rubric tells the auditor to
ignore it. It supports no claim, is in no batch, and its producer is not one the audit's independence classifies. A claim
resting only on such records is open and reads `claim.research_missing` with the case `unconfirmed` and the record ids,
and the text says what counts: a workspace file the service checks, a local file the auditor child may read, a spike, or
a stop that leaves the claim as a residual. `claim.research_unbound` (research bound to an earlier wording) ranks before
`unconfirmed`, because rebinding is the more direct fix. `confirmation.counts` reads the route only and never the mode.
The observer shows no wait for an inert record: its `status` already says it was never confirmed. Which records need a
human: a `web` source always (neither host credits the web to the auditor); a `runtime` note with no workspace path; a
local file only where the auditor child may not read it (Claude credits `local` and `workspace`, Pi credits `read`). A
`runtime` record that names a workspace path is checked by the service and counts.

#### D3.1 A budget raise is a further human episode in interactive auto

This amends ADR-0063's "one human approval episode, then unattended". In an interactive auto run, after the first approval, a
proposal that asks for more than the run approved is a second kind of human episode: the human approves or rejects it in the
host's approval dialog. Nothing else is asked. A proposal that raises nothing stays automatic. A delegated auto run has nobody
to ask and still refuses a raise (`governance.auto_ceiling`); a fresh run is its way on.

"Raises" is derived, never stored: `core.governance.raises` compares each proposed ceiling with the approved `state.budgets`,
and the run view publishes the answer as `governance.raises`. `expected_approval_kind` is `host_ui` for an interactive auto
run that has not been approved yet or whose proposal raises, so both hosts read one fact. A rejected raise changes nothing and
`human_wait` is false: the agent continues within the approved ceilings or stops. The stored state holds no new field. The
validator allows a pending, rejected or human-approved raise in interactive auto only. At the standing revision a human
receipt is valid only if that proposal raises; across the history the validator checks one approving `host_ui` receipt per
revision and that every `auto` receipt follows a human approval. It does not reconstruct which earlier revision raised: the
core never writes a human receipt for a revision that raised nothing (`decision_error`), and the validator is the second line.
The limits are unchanged: 3 dialogs per plan revision,
8 post-approval material revisions, 128 interactions. `budget.exhausted` offers `budget.raise` in deliberative and interactive
auto, and `recovery_exclusions` is keyed by the run's mode (`deliberative`, `auto_interactive`, `auto_delegated`). A host does
not settle the turn on an exhausted budget when the core offers `budget.raise` to an auto run: the agent is the one to propose it. A raise that cannot be asked, because no human can be reached or the run has no interaction left to ask with, leaves a proposal within the approved ceilings, which is automatic and spends no interaction, so the view offers that proposal and the stop (`governance.raise_unavailable`). Only interactive auto has such a proposal: every deliberative proposal goes to the human, so a deliberative run with nobody to ask is offered the stop alone.

The raise is legible. The `budget.exhausted` reply words the recovery its run is offered, by the reason's `recovery` parameter
(`raise_dialog`: the human approves the raise in a dialog; `raise_human`: the human decides it; `fresh_run`: no `budget.raise`
is offered, so the stop or a fresh run). The core chooses the case where it makes the reason (`core.evaluation.exhausted`), from the recovery actions the run is left
with after its mode's exclusions (`core.governance.budget_recovery`), so the reply, the run view and the observer's own sentence
carry the same one; where the host has reported that it can show a human nothing (`core.governance.cannot_ask`), a raise that is
still offered reads as `fresh_run`, so no text promises a dialog that cannot come. No decision reads it. The author view marks the `budget.raise` next action with
the contract's `budget_raise_note`, because it shares the `configure_run` call with every other configuration. The raise dialog
says it raises the approved ceilings and shows each changed ceiling (raised or lowered) as `N (was M)`, M being the run view's `governance.budgets`,
read only when `governance.raises` is true; the bound document and its digest are unchanged.

Two deliberate exceptions, both stated rather than hidden: a refuting web source still discards a claim in auto, with no
human to confirm or reject it, and a stored 7.0.x human `confirm` or `reject` still counts in every mode.

What neither guard changes: a stored human receipt still counts in every mode (D5).

Rejected alternatives:

- Let interactive auto ask when it can. It makes the same run finish or wait by who happens to be at the keyboard, and the
  turn end cannot tell whether a dialog will come.
- Hold the confirmation batch until the author's work is done, then ask. It keeps a human in the loop that the rule removes
  and needs a second order in the gate and the view.
- Refuse a citation that needs a human at submission. It adds a rule beside the derivation and cannot reach records
  already stored.

Reversal condition: operators of interactive auto runs need the confirmation dialog and will not use deliberative mode.

### D5 Persisted runs

An auto run is read by this version's rules, whether it is active or ended and whichever version wrote it. D1 applies to
every such run, and a stored fact is never rewritten: the terminal `status`, the audit verdicts, the receipts and the
presentations stay as they were recorded. A human answer in a persisted run (an `evidence_confirmation` item) still
decides its claim, in every mode, as before.

The observer reads a `needs-decision` claim's standing from the run's mode, the claim's derived state and the run's
status. Only an auto run with no human answer changes:

| Claim in an auto run, no human answer | Standing |
|---|---|
| outside the run's scope | `deferred` |
| `approved` by its evidence | `decided_by_author` |
| `discarded` by its evidence | `rejected_by_evidence` |
| open, run active | `not_asked` (the agent owes research) |
| open, run ended | `unanswered` |

A human answer is `approved` or `rejected` in every mode, and a deliberative run keeps its standings. The two new values
are additive in the `empirica-ui-view/v1` standing enum, with copy.

Two consequences follow for runs already persisted: an ended auto run may show a decision that read `unanswered` as
decided by its author, and an active auto run with a decision left on Later becomes author-decidable. The upgrade note
states both.

Rejected alternative: a semantics marker in the run state, so that old runs keep the old rules. It needs a state field
and so a major version, and it leaves two rule sets for one mode.

Reversal condition: a persisted run whose reading changes in a way an operator cannot tell from its stored facts.
