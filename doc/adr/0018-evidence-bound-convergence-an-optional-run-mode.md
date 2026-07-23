---
number: 18
title: "Evidence-bound convergence: an optional run mode"
status: proposed
date: 2026-07-23
tags:
  - architecture
  - verification
  - trust-boundary
  - future
links:
  - target: 13
    kind: Refines
  - target: 7
    kind: relatesto
  - target: 17
    kind: relatesto
---

# Evidence-bound convergence: an optional run mode

## Status

**Proposed — not scheduled.** This records a design direction surfaced by an external
adversarial review (GPT-5.6 Sol, 2026-07-23, `tmp/empirica-review-gpt-5.6-sol.md`). It is
deliberately NOT accepted: it is a real architectural change that would move empirica from a
*design tool* toward a *runtime harness*, and that move should be a separate, deliberate
decision — not smuggled in as a bug fix.

## Context and Problem Statement

The review's central finding: empirica's convergence gate is deterministic *as a parser*, but
its input is a model-authored confidence float. The chain is spike → **model reads gate** →
**model hand-writes confidence in `spec.md`** → Stop hook reads that number. Nothing binds a
passing spike result to the unknown it supposedly resolved — no check ID, result hash, or
artifact reference. So the constrained principal (the model) can raise confidence without
running anything. This is the Goodhart/self-preferential path ADR-13 names as the thing to
avoid.

The reframe that bounds this ADR's scope: **empirica exists to DESIGN the checks, not to be
the production trust boundary that RUNS them.** ADR-13 already places the production wall at
CI ("the wall that does not move"). Empirica's job ends at a converged spec + designed tests,
handed off at M7; CI runs them on the shipped code. Under that contract the review's finding
is a *category* concern — a forged `converged` yields a bad spec that CI still catches — not a
runtime-safety hole. Judged as a design tool, the current behaviour is defensible.

The open question this ADR frames: **should empirica optionally enforce, at design time, that
each `needs-experiment` unknown was resolved by an ACTUAL passing spike — not a self-assessed
score?** That is a smaller, well-bounded version of "run mode": bind design-time evidence to
the unknown, without duplicating CI.

## Decision Drivers

* The single most valuable thing empirica could add is what turns "asks the model to be
  honest" into "checks" — the same standard we applied to the spawn budget (don't enforce
  what you can't verify).
* It must NOT balloon into a general execution engine or a second CI. Scope creep here is the
  ship-or-kill trap.
* Only `needs-experiment` unknowns are runnable-resolvable. `needs-decision`/`needs-data` are
  inherently human/external and must stay confidence-scored residuals.
* Requires run identity (an active-run manifest) to know *when* to enforce — which empirica
  does not have today (review finding 1.2).

## Considered Options (sketch — to be worked when/if this is picked up)

* **Do nothing.** Keep empirica a pure design tool; convergence stays confidence-scored; CI is
  the only real wall. Cheapest; the review's 2.1 stands as an accepted limitation, documented.
* **Evidence-bound `needs-experiment` (the proposed run mode).** The convergence gate requires
  each `needs-experiment` unknown at ≥ θ to reference a harness-written spike-result artifact
  (unknown ID ↔ command hash ↔ `gate: pass` ↔ files-unchanged-since). The model can no longer
  raise such an unknown by typing a number; only a real passing spike can. Human/data
  residuals unchanged.
* **Full runtime harness.** Empirica runs the production suite itself and gates on it.
  Rejected in spirit: that IS CI; empirica would duplicate the wall it hands off to (ADR-13).

## Decision Outcome

**Deferred.** No option chosen. Recorded so the direction is not lost and so the accepted
fixes (the sibling change set that hardens empirica's own integrity) are explicitly scoped
*not* to include this. If picked up, the evidence-bound option is the leading candidate; it
would additionally require:

1. **An active-run manifest** (keyed to Claude `session_id` + canonical project root) so the
   gate knows a run is active and can fail *closed* on a missing/renamed spec (review 1.2).
   This is the prerequisite that also fixes the "delete spec.md to bypass" hole.
2. **A harness-owned spike-result store** binding `unknown_id → {command_hash, gate,
   result_hash, files_hash, ts}`, written by the spike path, read by the gate.
3. **Gate validation** that every `needs-experiment` unknown ≥ θ has a matching `gate: pass`
   record whose `files_hash` still matches — else fail closed.

Until then, empirica is honestly a **design tool with a self-attested convergence signal**,
and its documentation must say so (no "deterministic trust boundary on code" claims for the
convergence gate — that phrase belongs to CI per ADR-13).

### Consequences

* Good (if built), because convergence stops trusting a model-typed float for the one unknown
  class that is machine-verifiable, closing the review's central finding within empirica's
  legitimate scope.
* Good, because it forces run identity (the manifest), which independently fixes the
  fail-open-on-renamed-spec hole.
* Bad, because it is real work (manifest lifecycle, evidence store, hashing, resume) and moves
  empirica toward harness territory — a deliberate identity shift, not a patch.
* Bad, because over-scoped it becomes a second CI; the guard is "design-time evidence binding
  only, `needs-experiment` only."

## More Information

Source: external review `tmp/empirica-review-gpt-5.6-sol.md` findings 2.1 (evidence linkage)
and 1.2 (run identity). Refines ADR-13 (this is where the "deterministic boundary" could
legitimately extend to design-time evidence, distinct from CI's production boundary); relates
to ADR-7 (the unknown/confidence substrate this would bind evidence to) and ADR-17 (same
"enforce only what you can verify" principle). The review's other findings — the `blocked:`
enum bypass, git-ignored tests, budget path/lock hardening, reinjection safety, spike exit
semantics, doc drift — are empirica's own-integrity concerns and are fixed separately, not
deferred here.
