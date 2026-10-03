---
number: 62
title: Retire confidence theta spec unknowns and racing convergence models
status: accepted
date: 2026-09-29
tags:
- empirica
- convergence
- claims
links:
- target: 20
  kind: Amends
- target: 29
  kind: Amends
---

# Retire confidence theta spec unknowns and racing convergence models

Supersedes ADR-0006, ADR-0007, ADR-0009, ADR-0010, ADR-0011, ADR-0018 (removed from the tree; see git history). Amends ADR-0003, ADR-0012, ADR-0024 (removed from the tree; see git history). ADR-0006 is removed from the tree; this ADR retires the remaining race/spike model.

## Context and Problem Statement

Empirica 4.0 derives claim state from the closed claim DAG and current evidence. The earlier confidence/θ, spec-hosted unknown, multi-design race, and `RaceController` model has no runtime representation.

## Considered Options

- Keep the older model nominally accepted.
- Retire it in favor of the current derived-state contract.

## Decision Outcome

Retire the older model. Claims use the closed kinds and evidence rules in public contract 3.0.0; deterministic spikes execute one admitted command and use its exit code rather than racing generated designs.

## Consequences

- Good, because ADR status now matches the single implemented convergence model.
- Bad, because historical readers must follow the supersession links to distinguish the retired model.
