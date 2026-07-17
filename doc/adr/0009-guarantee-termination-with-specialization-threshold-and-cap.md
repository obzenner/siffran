---
number: 9
title: Guarantee termination with specialization, threshold, and cap
status: accepted
date: 2026-07-17
tags:
  - architecture
  - termination
links:
  - target: 8
    kind: Depends on
  - target: 7
    kind: Depends on
---

# Guarantee termination with specialization, threshold, and cap

## Context and Problem Statement

Deriving new unknowns from current knowns (ADR-7) can *grow* the unknown set, not only
shrink it. A naive "loop until the set is empty" may never terminate. The platform also
force-overrides a `Stop` hook after 8 consecutive blocks (ADR-8). What guarantees the
convergence loop terminates?

## Decision Drivers

* The unknown set can grow via derivation — no monotone-decrease guarantee for free.
* The user asked for a *confidence score*, which is a natural termination measure.
* The 8-block hook cap forbids an unbounded single-session loop regardless.

## Considered Options

* **Loop until zero unknowns, no guards** — simplest, may not terminate.
* **Iteration cap only** — bounds worst case but discards work at the cap arbitrarily.
* **Three composed guards** — (1) well-founded derivation: M9 may only *specialize* an
  unknown into more concrete, closer-to-resolvable sub-unknowns, never *generalize*;
  (2) threshold θ: convergence is "every unknown ≥ θ confidence," not "zero questions
  imaginable" — sub-θ-leverage unknowns are pruned as out-of-scope; (3) hard iteration cap:
  non-convergence at the cap is reported as signal (under-scoped task / too-large surface),
  not hidden.

## Decision Outcome

Chosen option: "Three composed guards", because each closes a distinct escape from
termination. Specialization-only derivation is the load-bearing one: it makes the recursion
well-founded (every derived unknown is strictly closer to resolvable-by-evidence, so there
is a floor). θ defines "done" via the confidence score the user asked for. The cap bounds
the worst case and aligns with the platform's 8-block reality — the loop checkpoints to the
durable ledger (ADR-7) and resumes across turns rather than grinding one session.

### Consequences

* Good, because the loop has an actual stopping proof, not a hope.
* Good, because the durable-resumable shape fits both the hook cap and long-session energy
  management — work checkpoints instead of running a session indefinitely.
* Good, because non-convergence becomes information (surfaced open unknowns), not silent
  failure.
* Bad, because "specialize, never generalize" is a discipline M9 must enforce; a derivation
  that broadens a question violates the guarantee and must be rejected.

### Confirmation

Fitness function: (1) a test feeds M9 a knowns+unknowns state and asserts every derived
unknown is strictly more concrete than its parent (no generalization); (2) convergence is
reached when all unknowns ≥ θ; (3) a deliberately under-scoped task hits the iteration cap
and the skill reports the still-open unknowns rather than claiming done.

## More Information

Well-founded recursion / termination measure: standard from program-termination theory.
Motivated jointly by ADR-7 (set can grow) and ADR-8 (8-block cap).
