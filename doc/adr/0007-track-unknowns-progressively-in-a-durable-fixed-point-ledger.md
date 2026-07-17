---
number: 7
title: Track unknowns progressively in a durable fixed-point ledger
status: accepted
date: 2026-07-17
tags:
  - architecture
  - state
links:
  - target: 5
    kind: Refines
  - target: 8
    kind: Depended on by
  - target: 9
    kind: Depended on by
---

# Track unknowns progressively in a durable fixed-point ledger

## Context and Problem Statement

The user requirement: the workflow "must track unknowns progressively in a state, reassess
them, derive new unknowns from current knowns and unknowns, until there are no unknowns
left with a confidence score." This turns the router from a one-shot classifier into a
state machine that resolves unknowns over time. How is that state modelled and where does
the reasoning live?

## Decision Drivers

* Unknowns are discovered progressively — resolving one can spawn others.
* Each unknown needs a confidence score; "done" means all unknowns cleared a threshold.
* State must survive context compaction and multiple turns (long-run session failure mode).
* Storage and reasoning are different concerns (one persists, one computes).

## Considered Options

* **In-context list** — keep the unknowns in the conversation; reassess by re-reading.
* **Durable fixed-point ledger split into M8 Ledger (storage) + M9 Assessor (function f)**
  — append-only on-disk ledger of `{id, statement, kind, confidence, derived_from,
  resolved_by}`; M9 applies one pass: score updates + derive new unknowns; `converged()`
  is true iff no unknown sits below threshold θ.

## Decision Outcome

Chosen option: "Durable fixed-point ledger (M8 + M9)", because the requirement is literally
a fixed-point iteration `f(ledger) → ledger'` that must persist. Separating dumb storage
(M8) from the reasoning function (M9) keeps the schema stable while the derivation/scoring
logic evolves. M8 becomes the most-depended-upon module — state is the spine of a
progressive-resolution workflow.

### Consequences

* Good, because the loop is explicit and inspectable: the ledger file is the audit trail.
* Good, because the known/unknown split (ADR-5) becomes a spectrum — the known path is
  just an initial ledger that already satisfies `converged()`.
* Good, because durability enables cross-turn resumption (required by ADR-8's block cap).
* Bad, because deriving new unknowns from knowns can grow the set — termination is not free
  and needs explicit guards (ADR-9).

### Confirmation

Fitness function: after each resolution the ledger file shows the resolved unknown's
confidence raised and any newly-derived unknowns appended with `derived_from` set.
`converged()` returns true only when every unknown's confidence ≥ θ. A test drives several
iterations and asserts the ledger monotonically approaches convergence.

## More Information

Fixed-point iteration lineage: Cousot & Cousot (abstract interpretation, 1977). The
derivation discipline that keeps the iteration well-founded is specified in ADR-9.
