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
  - target: 14
    kind: Depended on by
  - target: 15
    kind: Refined by
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
* **Bespoke durable ledger** — a custom append-only on-disk store of
  `{id, statement, kind, confidence, derived_from, resolved_by}`. (Rejected once the
  artifact standard was checked: this reinvents state that the industry represents in the
  living spec — see ADR-15.)
* **Fixed-point convergence over spec-hosted unknowns (M9 Assessor as function f)** — the
  progressive-resolution *behavior* stays: M9 applies one pass — score updates + derive new
  unknowns — and `converged()` is true iff no unknown sits below threshold θ. The *substrate*
  is the living spec (spec-kit `spec.md`, ADR-15), where each unknown is an open item
  carrying a confidence score, not a separate bespoke file.

## Decision Outcome

Chosen option: "Fixed-point convergence over spec-hosted unknowns." The requirement is
literally a fixed-point iteration `f(state) → state'` that must persist across turns
(ADR-8). What persists is the living spec's unknowns section (ADR-15) — the industry-standard
substrate — with our confidence + θ layer on top. M9 Assessor is the reasoning function; the
spec is the storage. The confidence-scored convergence is the one piece no standard provides
and is our genuine contribution; the storage is adopted, not invented (ADR-15).

### Consequences

* Good, because the loop is explicit and inspectable: the spec's unknowns section is the
  audit trail, and it is the same artifact humans already read.
* Good, because the known/unknown split (ADR-5) becomes a spectrum — the known path is
  just an initial spec whose unknowns already satisfy `converged()`.
* Good, because durability + cross-turn resumption (ADR-8) ride on the run's living spec in
  the run directory (transient scratch, ADR-14), with no bespoke store to maintain.
* Bad, because deriving new unknowns from knowns can grow the set — termination is not free
  and needs explicit guards (ADR-9).
* Bad, because "unknowns as spec items with a confidence score" is less structured than a
  typed store — the build must define precisely how a confidence score attaches to a spec
  item and how it is parsed (deferred to the build; noted in ADR-15).

### Confirmation

Fitness function: after each resolution the spec shows the resolved unknown's confidence
raised and any newly-derived unknowns added with their origin recorded. `converged()`
returns true only when every unknown's confidence ≥ θ. A test drives several iterations and
asserts the unknowns set monotonically approaches convergence. No bespoke ledger file exists
(ADR-15 fitness function).

## More Information

Fixed-point iteration lineage: Cousot & Cousot (abstract interpretation, 1977). The
derivation discipline that keeps the iteration well-founded is specified in ADR-9; the
spec-hosted storage substrate is specified in ADR-15.
