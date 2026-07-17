---
number: 2
title: Formalize the workflow with the decomposition methodology
status: accepted
date: 2026-07-17
tags:
  - meta
  - process
links:
  - target: 4
    kind: relatesto
---

# Formalize the workflow with the decomposition methodology

## Context and Problem Statement

A colleague described a two-path development workflow (unknown-territory → discovery
agents + empirical spikes → docs → implement; known-territory → resolve invariants →
implement). We want to build it as a new siffran workflow skill. Before writing any
skill we must formally define the algorithm: its parts, boundaries, decision points,
and invariants. Which formal reasoning method should govern that definition?

## Decision Drivers

* The ask is "carve a narrative into parts with clean seams," not "pick a detail level."
* The known/unknown routing is a module boundary, not a flowchart step.
* The novel core (empirical spikes) must be isolatable from the commodity glue.

## Considered Options

* **Functional decomposition** (Parnas/Dijkstra) — decompose on axes of change.
* **Abstraction-refinement** (Wirth) — start abstract, refine to concrete.

## Decision Outcome

Chosen option: "Functional decomposition", because the primary uncertainty is *what are
the parts and where are the seams*, not *what level of detail* — which is what
decomposition answers and refinement does not. Refinement assumes the structure and
drills; we needed to discover the structure.

### Consequences

* Good, because it surfaced that the two "paths" are one pipeline (ADR-5) and that the
  spike is really two modules (ADR-6) — structural findings refinement would miss.
* Good, because Parnas's change-axis criterion gave an objective module test.
* Bad, because decomposition alone does not validate runtime assumptions — those had to
  be discharged separately against docs (ADR-8).

### Confirmation

The methodology ran through all six phases (state the whole → change axes → modules →
dependencies → validation → build order), each emitting its required structured artifact.
The transcript is the fitness function: every phase output is present and the final
module set passed the three-check independence validation in Phase 5.

## More Information

Methodology: methodologist plugin, `decomposition` (registry.json). Lineage: Parnas
(information hiding, 1972), Dijkstra (structured programming), Simon (hierarchy in
complex systems). This ADR records the *choice of method*; ADRs 3–10 record the
decisions the method produced.
