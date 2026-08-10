---
number: 26
title: "Freeze mode: bound discovery so a run can close, not just terminate at the cap"
status: proposed
date: 2026-08-10
tags:
  - workflow
  - termination
  - harness
  - convergence
links:
  - target: 9
    kind: Amends
  - target: 17
    kind: Depends on
  - target: 19
    kind: Depends on
  - target: 20
    kind: relatesto
  - target: 22
    kind: relatesto
---

# Freeze mode: bound discovery so a run can close, not just terminate at the cap

## Context and Problem Statement

empirica has two enforced bounds and neither one ends a run *on purpose*.

`max_spawns` bounds fan-out cost; `max_passes` bounds loop length via the ADR-19 variant
`max_passes − passes` (`manifest.py:374-381`). Both are backstops: they guarantee the loop
**terminates**, and ADR-19's proof is exactly that. What neither provides is a way for a run to
**close** — to decide that the claim set is now fixed and the remaining work is to discharge it.

The gap is in the loop's incentives. Step 4 of the workflow instructs the Assessor to "derive child
claims — resolving one claim reveals others", and that is correct: discovery is the point. But
nothing anywhere tells a run to *stop* discovering. Each pass legitimately adds gating claims, each
new claim owes Fold-1 evidence, and the Stop gate's block message
(`convergence_gate.py:_block_reason`) offers exactly two exits per claim: resolve it, or tag it
`blocked:`. There is no third state for "real, out of scope for this run, hand it forward."

So a run that keeps finding real things has one terminal path available: grind to `max_passes` and
report `stopped_residual` (`convergence_gate.py:344-354`). This is honest — ADR-17's "never fabricate
green" holds — but it is not the same as finishing. The reported failure mode, from an agent that ran
the workflow end to end: *"nothing told me to stop discovering and start closing"*, and the run
continued three rounds past the point where the design was settled, each round adding claims about the
design rather than establishing it.

Note what this is not. It is not a stall — ADR-17's stall check covers passes that derive *nothing
narrower* and move nothing across θ, and the self-check plus the pass counter handle that. This is the
opposite: productive passes, real new claims, no closing condition. And `blocked: needs-*` is the wrong
tool, because those four tags mean "this run cannot resolve this" (`claimgraph.py:109-111`), whereas
the case here is "this run should not *try*."

## Decision Drivers

* **A closing condition must be a commitment, not an escape.** Any mechanism that lets a run shrink
  its own gating set is a bypass unless the shrink is bounded in a way the harness witnesses.
* **Termination must remain proven.** ADR-19's variant is the termination proof; freeze must not
  weaken it, and must not become a second, competing termination argument.
* **Never fabricate green (ADR-17).** A frozen run has known open items by construction. It must not
  report clean convergence.
* **Findings must survive the run.** Deferred claims that vanish are worse than claims that block —
  the value is the honest open-items list.
* **Determinism and fail-closed (ADR-19).** No clock; a corrupt freeze record must not free a
  blocking run.
* **Baseline behaviour unchanged (ADR-24 §5).** A run that never freezes must behave exactly as it
  does today.

## Considered Options

* **A — Freeze as a first-write-wins manifest commitment**: record the gating claim-id set and a
  `freeze_seq`; pre-freeze claims keep gating, post-freeze claims become reported residuals.
* **B — Freeze as a third mode in `modes.json`** alongside `multi_provider` and `cli_exec`.
* **C — A `max_claims` cap**: refuse to gate more than N claims, auto-deferring the overflow.
* **D — Do nothing; teach the stall check to cover it.**

## Decision Outcome

Chosen option: **A — freeze is a first-write-wins commitment recorded in the manifest, naming the
exact set of claims the run will discharge.**

A run freezes by an explicit act, which records into the manifest:

* `frozen_claims` — the ids of the claims **gating at the moment of the freeze**, snapshotted;
* `freeze_seq` — the manifest's own ordering counter, assigned under the lock;
* `freeze_ts` — the caller-supplied stamp (ADR-19: hooks never generate time).

Written through the same `_stamp_event` discipline as `route_ts`/`first_tool_ts`
(`manifest.py:329-345`): **first write wins**. After the freeze, the Stop gate partitions the gating
goals:

| Claim | Treated as |
|---|---|
| in `frozen_claims` | gating, exactly as today — must reach terminal state |
| not in `frozen_claims` (added after the freeze) | **deferred**: does not gate, is reported |

A frozen run may stop as soon as every claim in `frozen_claims` is terminal. It reports
`converged: false` with a `deferred` list naming every post-freeze claim, plus its status as
`stopped_frozen` — a new terminal status distinct from `stopped_residual`, because "closed with a
scoped open-items list" and "ran out of passes" are different outcomes and a report that conflates
them is the kind of dressing-up ADR-17 forbids.

**The anti-bypass property, which is the whole design.** Freeze is only sound because of what the
snapshot is taken *of*: the claim set that was **already gating** when freeze fired. The attack is
"freeze early, then add the hard claims" — and it fails, because a claim added after `freeze_seq` is
never in `frozen_claims`, so it cannot be the thing the run discharges. Freezing early does not let a
run pass with less; it lets it pass with **less scope, declared up front, and the omission printed in
the result**. Freezing at pass 0 with an empty gating set yields a run that discharges nothing and
reports everything deferred — visibly vacuous, which is the correct treatment of a vacuous act, and
the same "make the degenerate case loud rather than illegal" reasoning as the vacuous-`files_hash`
rejection (`evidence.py:322-334`).

Two guards make that hold rather than merely sound plausible:

1. **First-write-wins.** A run cannot re-freeze to enlarge or replace its committed set, exactly as it
   cannot re-stamp its route. Without this, freeze would be re-scopeable per pass, which is
   unbounded shrinking with extra steps.
2. **The auditor is told.** The freeze set and the deferred list go into the audit inputs, and the
   auditor's rubric item 8 ("the claim set covers the intent") is evaluated **against the frozen
   set**, with the deferral itself in scope for review. A freeze that carved out the intent's core is
   an audit FAIL. This is deliberate: freeze bounds what the *harness* gates, and the harness cannot
   judge scope, so the judgement lands on the reviewer that can — the same division of labour as
   ADR-13's "agentic review may block but never approve".

**Termination is unaffected.** `max_passes − passes` still decreases monotonically and still bounds
the run; freeze only lets a run reach a terminal state *sooner*, never later. It adds no new
termination argument and removes none. A frozen run that still cannot discharge its frozen set hits
the cap and reports `stopped_residual` as it does today.

**Fail direction.** An unreadable or malformed freeze record reads as **not frozen** — every claim
gates, which is current behaviour. This is the opposite of `modes.json`'s "corrupt reads as off"
(`modes.py:88-95`) in effect but the same in principle: fall back to the baseline that gates *more*,
never less. Hence option B is rejected below.

**Rejected: freeze as a mode.** `modes.json` is deliberately for *optional capability* whose corrupt
state is safely OFF, because those modes decide which processes run on a user's machine
(`modes.py:106-111`). Freeze decides **what the gate gates**. Putting a gating-scope decision in a
file whose documented failure mode is "unreadable → default" would make the default free a blocking
run, which is the legacy-shape exploit again (`convergence_gate.py:222`). It belongs in the manifest,
with the run's other commitments, under the lock.

### Consequences

* Good, because a run gains a way to *finish*: discharge a declared set, hand the rest forward as a
  categorised open-items list.
* Good, because the deferred findings are recorded rather than lost — today the same claims either
  gate forever or get mis-tagged `blocked:`.
* Good, because it separates three outcomes the reports currently blur: converged, out of passes
  (`stopped_residual`), and closed with declared scope (`stopped_frozen`).
* Good, because the anti-bypass property is structural (snapshot of already-gating claims,
  first-write-wins) rather than a rule the model is asked to respect.
* Bad, because it is a new terminal status, so every reader of run status — `state_restore.py`, the
  doctor, the tests, the skill's reporting — must learn it. A status a reader does not know is a status
  that reads as unrecognised.
* Bad, because a run *can* legitimately under-scope itself by freezing early, and the only check on
  that is the auditor. Stated plainly: freeze moves a scope judgement from "impossible" to
  "reviewable", not to "enforced".
* Neutral, because `max_passes` and `max_spawns` are untouched. Freeze is a closing condition, not a
  third bound.

### Confirmation

Regression tests in `plugins/empirica/tests/test_hooks.py` (`make test`):

1. A frozen run whose frozen claims are all terminal stops, reports `converged: false`, status
   `stopped_frozen`, and lists the deferred claims by id.
2. **The bypass test:** freeze, then add an unresolved claim. The run still stops — and the new claim
   appears in `deferred`, never silently dropped, and never as a reason to report convergence.
3. A second freeze attempt does not change `frozen_claims` (first write wins).
4. A frozen claim that is *not* terminal still blocks — freeze does not weaken the claims it committed
   to.
5. A corrupt/malformed freeze record reads as not-frozen: every claim gates.
6. An unfrozen run's behaviour is byte-identical to today's on every existing test — the baseline
   guarantee.
7. A frozen run that cannot discharge its frozen set still terminates at `max_passes` as
   `stopped_residual`.

## Pros and Cons of the Options

### C — A `max_claims` cap with automatic overflow deferral

* Good, because it needs no explicit act from the run — it cannot be forgotten.
* Bad, because the deferral is chosen by arrival order, not by relevance: claim N+1 might be the
  intent's core and claim N a detail. A cap cannot tell.
* Bad, because it gives the run an incentive to write fewer claims, directly opposing ADR-22's
  "unwritten claims are the one thing no hook can catch".
* Bad, because it is not a commitment anyone made, so there is nothing for the auditor to judge.

### D — Do nothing; extend the stall check

* Good, because it costs nothing and adds no state.
* Bad, because it misdiagnoses the case. A stall is "passes derive nothing narrower"; this is
  "passes derive real new claims indefinitely". The stall heuristic would have to fire on
  *productive* passes to catch it, making it wrong in the case it was written for.
* Bad, because the only available terminal path stays `stopped_residual` at the cap, which reports a
  settled design as an exhausted loop.

## More Information

Reported by an agent that ran the workflow end to end: three extra rounds produced the same design
plus a longer claim list, and the run ended at the cap rather than at a decision. Freeze is the
missing third exit next to *resolve* and *blocked*.

ADR-9 introduced "specialize only" so derivation terminates; that bounds each claim's *depth*. Freeze
bounds the *breadth* of the set, which ADR-9 left to the pass counter alone. Read as an amendment to
that, not a replacement.

Deliberately not decided here: whether freeze should ever be *automatic* (e.g. on a heuristic of
"design unchanged for K passes"). That needs evidence from real runs about what K is, and an automatic
freeze is a scope decision made without a commitment — the property this ADR relies on. Left as a
residual for a later record.
