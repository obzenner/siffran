---
number: 74
title: Prepare and qualify the spike environment inside the run
status: proposed
date: 2026-11-22
tags:
- empirica
- sandbox
- docker
- governance
links:
- target: 71
  kind: Amends
- target: 72
  kind: Amends
- target: 63
  kind: Amends
- target: 73
  kind: Amends
---

# Prepare and qualify the spike environment inside the run

Amends ADR-0071 (spike authority per run), ADR-0063 and ADR-0072 (approval and the HITL document) and ADR-0073
(auto mode). Status *proposed*: this record grows with each PR of the `e` stack, so that at every merge it describes
only behaviour that has shipped. It is accepted in the top PR (Empirica 7.2.0). The detailed design is
[the 7.2 spec](../design/specs/empirica-7.2-run-scoped-environment-qualification.md).

## Context and Problem Statement

A run's Docker authority is chosen once, from a machine-wide pointer, when `StartRun` creates a generation. It is
labelled `qualified` without contacting the daemon or comparing the record's implementation digest with the running
code. Making an environment usable is an operator procedure outside the run, and nothing in a run can change it. When
the work starts by preparing Docker and images, the order is backwards, and any mid-run environment change forces the
run to be abandoned.

## Decision Drivers

* Spike evidence must rest only on host observations; the agent's preparation is never evidence.
* In a deliberative run the human approves the environment; in auto the step is unattended.
* A run must be able to requalify; every spike keeps the qualification it ran under.
* No machine-wide mutable state that a run silently inherits.

## Considered Options

* Keep operator qualification and add better refusal text (rejected: the order stays backwards).
* Let a running run rebind to a newer machine-wide pointer (rejected: shared mutable authority).
* A run-scoped environment proposal, approved per mode, qualified by the host into a run-owned record (chosen).

## Decision Outcome

Chosen: the run-scoped proposal, as specified. Sections are added here as the stack's PRs ship them.

### To be filled by later PRs of the stack

E1 proposal model · E2 state `run/5` · E3 digest-pinned probe image · E4 authority observation · E5 run-scoped
qualification owner · E6 wire contract · E7 governance · E8 HITL kind · E9–E10 transactions · E11 audit and reads ·
E12–E13 hosts · E14 observer and UI · E15 removal of the pointer and install procedure.
