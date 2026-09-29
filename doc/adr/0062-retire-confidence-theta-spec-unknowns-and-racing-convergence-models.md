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
- target: 6
  kind: Supersedes
- target: 7
  kind: Supersedes
- target: 9
  kind: Supersedes
- target: 10
  kind: Supersedes
- target: 11
  kind: Supersedes
- target: 18
  kind: Supersedes
- target: 3
  kind: Amends
- target: 12
  kind: Amends
- target: 20
  kind: Amends
- target: 24
  kind: Amends
- target: 29
  kind: Amends
---

# Retire confidence theta spec unknowns and racing convergence models

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
