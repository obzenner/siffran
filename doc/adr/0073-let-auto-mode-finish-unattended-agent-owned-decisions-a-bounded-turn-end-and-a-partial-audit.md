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

To come: continue while author work remains, and a bound on unchanged continuations.

#### D2.3 A core-owned signal for the turn end

The run view gains `turn_end_actor`, an additive closed enum: `author`, `auditor`, `human` or `none`. The core computes it in `core.evaluation.turn_end_actor` from the actor of the first admissible requirement of the report (`first_admissible`): an agent step is `author` (claim work, and launching an audit, or launching it again after one failed); a human step is `human`; a running audit child (`audit.pending`) is `auditor`, because the turn settles and the child's result continues the run, which is what both hosts already do on a pending async audit and a continuation would only spin until the bound. It is `none` when nothing is owed, when the run has ended, and when the root claim is refuted or conflicted (`claim.refuted`, or `evidence.conflict` on the root) and no human is being asked, because no further work can approve the root and the only honest step left is to stop; a human who is being asked is never hidden. The value is core-owned so that both hosts read one answer and derive nothing from reasons, obligations or notices. It is carried by every response that carries the run view and is not rendered in the author view.

### D3 and D6 An auto run asks a human only at its configuration

The rule: an auto run asks a human at its configuration, and later only to approve a budget increase in interactive
auto. It asks nothing else until the run ends: no evidence confirmation, no decision, no consent to end. A human who wants
to confirm evidence starts the run in deliberative mode.

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
