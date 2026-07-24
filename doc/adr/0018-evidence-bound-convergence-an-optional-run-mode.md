---
number: 18
title: "Evidence-bound convergence"
status: accepted
date: 2026-07-23
tags:
  - architecture
  - verification
  - trust-boundary
links:
  - target: 13
    kind: Refines
  - target: 7
    kind: relatesto
  - target: 17
    kind: relatesto
  - target: 20
    kind: Realized by
  - target: 21
    kind: Realized by
---

# Evidence-bound convergence

## Status

**Accepted.** empirica is a runtime harness whose convergence signal is earned, not
self-attested. Evidence-bound convergence is realized by the mandatory run protocol (ADR-20,
P5) and the enforcement mechanisms (ADR-21, Mechanism 2). The move from *design tool* to
*runtime harness* is taken deliberately: a convergence signal that a model can raise by typing
a number is not a signal, and the plugin exists to make convergence mean something.

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

The decision this ADR makes: **empirica enforces that each `needs-experiment` unknown was
resolved by an ACTUAL passing spike — not a self-assessed score.** This binds design-time
evidence to the unknown without duplicating CI: empirica verifies the checks it designs;
CI runs them on the shipped code.

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

## Considered Options

* **Do nothing.** Keep empirica a pure design tool; convergence stays confidence-scored; CI is
  the only real wall. Cheapest; the review's 2.1 stands as an accepted limitation, documented.
* **Evidence-bound `needs-experiment` (chosen).** The convergence gate requires each
  `needs-experiment` unknown at ≥ θ to reference a harness-written spike-result artifact
  (unknown ID ↔ command hash ↔ `gate: pass` ↔ files-unchanged-since). The model can no longer
  raise such an unknown by typing a number; only a real passing spike can. Human/data
  residuals unchanged.
* **Full runtime harness.** Empirica runs the production suite itself and gates on it.
  Rejected: that IS CI; empirica would duplicate the wall it hands off to (ADR-13). empirica
  verifies the checks it designs; it does not re-run CI.

## Decision Outcome

Chosen: **evidence-bound `needs-experiment` resolution.** A `needs-experiment` unknown reaches
≥ θ only when bound to a real passing spike; the model can no longer raise it by typing a
number. Human (`needs-decision`) and external (`needs-data`) residuals stay confidence-scored,
since they are not machine-verifiable. This requires and is realized by three things:

1. **An active-run manifest** (keyed to Claude `session_id` + canonical project root) so the
   gate knows a run is active and can fail *closed* on a missing/renamed spec — built in
   ADR-19, which also closed the "delete spec.md to bypass" hole.
2. **A harness-owned spike-result store** binding `unknown_id → {command_hash, gate,
   result_hash, files_hash, ts}`, written by the spike path, read by the gate — ADR-21
   Mechanism 2.
3. **Gate validation** that every `needs-experiment` unknown ≥ θ has a matching `gate: pass`
   record whose `files_hash` still matches — else fail closed — ADR-20 P5, enforced per
   harness by ADR-21.

The convergence signal is evidence-earned for the machine-verifiable unknown class. Where a
harness cannot enforce the binding (a bare `pi` session with no orchestrator, ADR-21), the
documentation states plainly that convergence is advisory there — no "deterministic trust
boundary on code" claim for the convergence gate, which remains CI's phrase (ADR-13).

### Consequences

* Good, because convergence stops trusting a model-typed float for the one unknown class that
  is machine-verifiable, closing the review's central finding within empirica's legitimate
  scope.
* Good, because it forces run identity (the manifest), which independently fixes the
  fail-open-on-renamed-spec hole.
* Bad, because it is real work (manifest lifecycle, evidence store, hashing, resume) and moves
  empirica toward harness territory — a deliberate identity shift, not a patch.
* Bad, because over-scoped it becomes a second CI; the guard is "design-time evidence binding
  only, `needs-experiment` only."

## More Information

Source: external review `tmp/empirica-review-gpt-5.6-sol.md` findings 2.1 (evidence linkage)
and 1.2 (run identity). Refines ADR-13 (this is where the "deterministic boundary" extends to
design-time evidence, distinct from CI's production boundary); relates to ADR-7 (the
unknown/confidence substrate the evidence binds to) and ADR-17 (same "enforce only what you can
verify" principle). Realized by ADR-20 (the run protocol, P5) and ADR-21 (per-harness
enforcement). Field precedent for design-time evidence persistence: `modu-ai/moai-adk`
(`verify-diet`).
