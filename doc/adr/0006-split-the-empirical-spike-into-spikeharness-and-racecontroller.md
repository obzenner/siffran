---
number: 6
title: Split the empirical spike into SpikeHarness and RaceController
status: accepted
date: 2026-07-17
tags:
  - architecture
  - modules
links:
  - target: 5
    kind: Refines
  - target: 10
    kind: Refined by
  - target: 11
    kind: Refined by
---

# Split the empirical spike into SpikeHarness and RaceController

## Context and Problem Statement

The narrative says discovery agents "model the production scenario via an integration test
and test different approaches." That phrase bundles two activities: making a single
approach falsifiable (build a prod-shaped test, run it, score it) and searching over
approaches (which to try, when to stop). Is this one module or two?

## Decision Drivers

* Parnas: decompose on axes of change. Test technique and search economics change for
  different reasons.
* The scoring of one approach must not know when the search stops; the search must not know
  how a test is built.
* The novel core of the skill lives here — getting the seam right matters most.

## Considered Options

* **One SpikeModule** — build test, run candidates, decide winner, decide when to stop.
* **Two modules: M3 SpikeHarness + M4 RaceController** — M3 makes one approach falsifiable
  (`model_scenario`, `run_candidate → Verdict`); M4 searches (`race → RaceResult`,
  owns the stopping rule) and consumes only M3's `Verdict` interface.

## Decision Outcome

Chosen option: "Two modules", because falsifiability (Popper: a prod-shaped test an
approach passes or fails) and search (which candidates, when to stop) change independently.
The `Verdict` contract is the clean seam; M4 orchestrates M2 + M3 without knowing their
internals.

### Consequences

* Good, because M4 is the cleanest module to test — pure control logic over mocked
  `AgentSpec` and `Verdict`.
* Good, because the scoring rubric (M3) and the stopping rule (M4) can each be iterated
  without touching the other.
* Good, because M3 becomes the empirical anchor: an approach is chosen only because a test
  ran and passed, never because it was argued for.
* Bad, because M3 inherently needs the host project's real toolchain to run tests
  (Phase 5 LEAK-2) — a property of empiricism, not a defect; tested against a fixture.

### Confirmation

Fitness function: M4 has a unit test with mocked M2/M3 asserting the stopping rule fires
and the winner is selected correctly. M3 has an integration test against a fixture project
proving `run_candidate` returns a real pass/fail `Verdict` from an actual test run.

## More Information

M3 is most-depended-upon among the empirical modules; the `Verdict` contract must stabilize
first (ADR-informed build order). Stopping rule detailed in ADR-10.
