---
number: 4
title: Remove deep-planner as clean-sheet, not a design reference
status: accepted
date: 2026-07-17
tags:
  - architecture
  - scope
links:
  - target: 2
    kind: relatesto
  - target: 16
    kind: Depended on by
---

# Remove deep-planner as clean-sheet, not a design reference

## Context and Problem Statement

The new workflow skill replaces the existing `deep-planner` plugin. deep-planner is an
iterative fixed-point *planning* loop over three domains (business logic / custom code /
external integration) that verifies API surfaces against docs. Should its structure seed
the new skill's design?

## Decision Drivers

* User directive: "deep planner is not at all a reference when it comes to how we should
  be building this."
* deep-planner plans by *reading and re-reading*; the new skill resolves by *running
  prod-shaped tests*. Different epistemics — reading evidence vs. generating evidence.
* Carrying a predecessor's shape into a clean-sheet design imports its assumptions.

## Considered Options

* **Refactor deep-planner** into the new skill (reuse the three-domain + fixed-point loop).
* **Clean-sheet from the narrative**; read deep-planner only to know what is being removed.

## Decision Outcome

Chosen option: "Clean-sheet from the narrative", because the new skill's mechanism
(empirical spikes that *run* code) is categorically different from deep-planner's
(document verification that *reads* code). The only legitimate use of deep-planner was to
understand what capability is leaving.

### Consequences

* Good, because the design is driven by the colleague's workflow and first principles, not
  by a predecessor's structure.
* Good, because deep-planner's genuinely reusable idea (fixed-point iteration) re-entered
  the design on its own merits, applied to a different object — the unknowns ledger (ADR-7)
  — rather than being inherited wholesale.
* Bad, because we forgo any battle-tested wiring deep-planner already had.

### Confirmation

Fitness function: deep-planner is removed from `marketplace.json` and `plugins/` when the
new skill ships. Review check: no file in the new skill imports, references, or copies
deep-planner's SKILL.md structure.

## More Information

deep-planner: `plugins/deep-planner/skills/deep-plan/SKILL.md`. Removal is part of shipping
the replacement, not a separate cleanup.
