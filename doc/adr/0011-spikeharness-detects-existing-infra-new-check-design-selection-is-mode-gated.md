---
number: 11
title: SpikeHarness detects existing infra; new-check design selection is mode-gated
status: accepted
date: 2026-07-17
tags:
  - architecture
  - modes
  - spike
links:
  - target: 6
    kind: Refines
  - target: 10
    kind: relatesto
---

# SpikeHarness detects existing infra; new-check design selection is mode-gated

## Context and Problem Statement

Session open question OQ-1: does M3 SpikeHarness plug into the host project's existing
integration-test infrastructure, or stand a harness up from scratch? Neither alone is
right — a target repo may or may not already have prod-shaped integration tests. And when
M3 must propose a *new* check, who chooses among the candidate designs: the AI, or a human?

## Decision Drivers

* Target repos vary: some have integration-test infra, some don't. M3 must handle both.
* Assessing existing work (approve/reject/improve) is epistemically different from
  inventing a new check — the branches produce different value.
* Proposing a new check is a bigger, harder-to-reverse commitment than tuning an existing
  one, so the automation posture should differ.
* Candidate diversity: three variations of one idea is false diversity — it confirms a
  local optimum and never tests whether the idea itself is wrong.

## Considered Options

* **Adapter only** — assume existing infra; wire the candidate in. Fails in repos without it.
* **Generator only** — always build a harness from scratch. Wasteful and risky where infra
  already exists; ignores the existing check's authority.
* **Detect-then-branch, with mode-gated selection** — M3 detects whether infra exists:
  - *exists* → ADAPTER+ASSESS: run the candidate against the existing check and return a
    Verdict of approve / reject / suggest-improvements on the work being done.
  - *absent* → GENERATOR: propose 3 designs for a new check — 2 similar (variations on the
    likely-right idea) + 1 alternative (hedge against being in the wrong neighborhood) —
    which become the candidate approaches M4 races.
  Selection among the 3 designs is mode-gated: **default mode** = human reviews and
  approves; **auto mode** = the AI decides via M4's empirical race (first-pass-θ, ADR-10).

## Decision Outcome

Chosen option: "Detect-then-branch, with mode-gated selection", because it is the only
option that handles both repo states and matches the automation posture to the stakes.
Both branches converge on the same `Verdict` contract, so M3 stays one module. The
generator's 3 designs feed straight into M4 as candidates — no new interface. The
default/auto split follows the established ecosystem convention (e.g. dagents `dg-run` vs
`dg-auto`): human-in-the-loop by default, fully autonomous under an explicit auto mode.

### Consequences

* Good, because M3 works in any repo — it adapts when it can, generates when it must.
* Good, because the "2 similar + 1 alternative" heuristic buys real diversity cheaply,
  hedging both "which tuning" and "is this the right idea at all."
* Good, because a new check — a durable, hard-to-reverse artifact — gets a human gate by
  default, while auto mode preserves the fully-empirical path for those who opt in.
* Good, because both branches reuse the `Verdict` contract, so M4 and downstream modules
  are unchanged.
* Bad, because M3 now carries infra-detection logic, and the two branches must be tested
  independently (adapter against a repo-with-infra fixture, generator against a bare one).

### Confirmation

Fitness function: (1) against a fixture repo WITH integration infra, M3 returns an
assess-Verdict (approve/reject/improve) without generating a new harness; (2) against a
bare fixture repo, M3 emits exactly 3 designs (2 similar + 1 alternative); (3) in default
mode the 3 designs are surfaced for human approval before any race; (4) in auto mode M4
races the 3 and selects by first-pass-θ with no human gate.

## More Information

Resolves OQ-1. Refines ADR-6 (which defines M3) by specifying M3's detect-then-branch
entry, and relates to ADR-10 (the race that selects designs in auto mode). Mode convention
consistent with dagents `dg-run`/`dg-auto`.
