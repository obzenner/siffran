---
number: 10
title: Use first-pass-theta as the default stopping rule
status: accepted
date: 2026-07-17
tags:
  - architecture
  - search
links:
  - target: 6
    kind: Refines
  - target: 11
    kind: relatesto
---

# Use first-pass-theta as the default stopping rule

## Context and Problem Statement

M4 RaceController (ADR-6) resolves an unknown by racing candidate approaches against M3's
prod-shaped test. When does the race for a single unknown stop and declare a winner?

## Decision Drivers

* Cost: each candidate approach is a full spike (build + run a real integration test).
* The goal is to *resolve the uncertainty*, not to find a global optimum.
* Different unknowns have different shapes — binary (works / doesn't) vs. gradient (which
  is best).

## Considered Options

* **First-pass-θ** — an unknown is resolved as soon as one approach's spike clears
  confidence θ. Cheapest; matches "resolve the uncertainty."
* **Race-all-then-best** — try all staffed approaches, pick the best-measured. Thorough,
  more spikes; better when "better" is a gradient.
* **Per-unknown adaptive** — tag each unknown binary vs. gradient and choose the rule
  accordingly. Matches reality but adds classification logic.

## Decision Outcome

Chosen option: "First-pass-θ" (user decision), as the *default*. It is the cheapest rule
and directly matches the stated goal — an unknown clearing θ is resolved, so stop. The
richer rules remain available as opt-in for unknowns where "better" is a measured gradient,
but they are not the default.

### Consequences

* Good, because it minimizes spike cost — the expensive operation runs as few times as
  possible per unknown.
* Good, because it composes cleanly with ADR-9's θ threshold: the same θ that defines
  convergence defines resolution.
* Bad, because for genuine gradient questions (e.g. "which of three designs is fastest")
  first-pass-θ may accept a merely-adequate approach over a better one — accepted as a
  default; race-all-then-best is the documented opt-in escape hatch.

### Confirmation

Fitness function: a test with two candidate approaches where the first clears θ asserts M4
stops after the first (does not run the second). A gradient-tagged unknown with the opt-in
rule asserts all candidates run before selection.

## More Information

Resolves session open-question OQ-2. The remaining open design question OQ-1 (does M3 plug
into existing test infra or generate it) is deliberately *not* recorded here — it is
unresolved and awaiting a decision, so no ADR is warranted yet.
