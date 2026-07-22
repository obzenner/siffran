---
number: 13
title: Deterministic gate is the trust boundary; agentic review is a secondary sensor
status: accepted
date: 2026-07-22
tags:
  - architecture
  - validation
  - verification
links:
  - target: 8
    kind: Depends on
  - target: 6
    kind: Refines
  - target: 11
    kind: relatesto
  - target: 12
    kind: relatesto
  - target: 15
    kind: relatesto
  - target: 16
    kind: relatesto
---

# Deterministic gate is the trust boundary; agentic review is a secondary sensor

## Context and Problem Statement

The workflow validates work at two points: inside the loop (M3 SpikeHarness scores an
approach) and at the end (before handoff/acceptance). Two validation mechanisms are
available: deterministic checks (tests, type-check, lint, CI) and agentic/LLM review. Which
one is authoritative — which one is allowed to say "this is done"? And a related cost
question: `/think` is a heavy call (a full multi-phase methodology run); at how many of the
loop's decision points should it fire?

This decision is grounded in current industry practice rather than invented (a research
pass over primary sources from Apr–Jul 2026), because validation and cost-tiering are the
highest-leverage outputs and the field has already converged on them.

## Decision Drivers

* An LLM can be "talked out of" its judgment and is subject to Goodhart failure (it will
  rewrite tests to match broken behaviour); a deterministic check cannot.
* An agent can ship more than a human can review, so the "done" signal must be automatable
  and trustworthy, not a self-assessment.
* `/think` is expensive; firing it at every decision point is O(iterations × points) of
  heavy reasoning — the opposite of token-efficient.
* Convergence among independent practitioners is strong evidence, not opinion.

## Considered Options

* **LLM review is authoritative** — the agent decides when work is done. Rejected: no
  independent check; Goodhart and optimism failures go undetected.
* **Deterministic gate is authoritative; agentic review is advisory** — a deterministic
  check (tests/types/lint/CI) is the load-bearing pass/fail signal; LLM review is a fast
  triage sensor that can flag but cannot approve. `/think` sits at the top of a
  cheap→expensive gating ladder and fires only when cheap checks are inconclusive.
* **Human reviews every diff** — rejected: does not scale to agent output volume; the human
  moves up to sampling/ownership (ADR: outer loop), not line-by-line audit.

## Decision Outcome

Chosen option: "Deterministic gate is authoritative; agentic review is advisory." The
deterministic check is the trust boundary — "the wall that does not move" (Osmani, *Agentic
Code Review*, Jun 16 2026). Agentic review is "a sensor, not a verdict": it can surface
problems but cannot declare done. This mirrors Anthropic's Claude Code gating hierarchy
(cheap→expensive: in-prompt check → separate evaluator → **deterministic Stop hook that
blocks the turn until it passes** → verification subagent) and the "show evidence rather
than assert success" rule.

Two structural consequences:
1. **M3's SpikeResult is anchored on the deterministic gate.** Its load-bearing field is
   `gate: pass|fail` from a real check run; the agentic `assessment` is a secondary
   annotation. (Renames the former "Verdict"; see ADR-6, ADR-11.)
2. **`/think` is the expensive tier.** Cheap inline reasoning is the default at loop
   decision points; `/think` escalates only on a stall (no convergence gain), an ambiguous
   gate result, an unknown stuck below θ, or a high-stakes/irreversible decision. This
   resolves the open "how conservatively to use `/think`" question — conservatively, by
   trigger, not by schedule.

### Consequences

* Good, because the "done" signal is an independent, non-negotiable check — Goodhart and
  optimism failures are caught by construction.
* Good, because it composes with the Stop-hook gate (ADR-8): the hook blocks completion
  until the deterministic gate passes, which is exactly Anthropic's documented pattern.
* Good, because cheap→expensive `/think` gating keeps token burn proportional to difficulty.
* Bad, because a task with no deterministic check available forces M3 into its
  design-proposal branch (ADR-11) — the gate must be built before it can be trusted.
* Bad, because "escalate `/think` on stall/ambiguity/high-stakes" needs a concrete trigger
  predicate; a vague trigger either over- or under-fires the expensive call.

### Confirmation

Fitness function: (1) M3 returns `gate: fail` whenever the underlying deterministic check
fails, regardless of the agentic assessment — a test asserts an LLM "looks good" cannot
override a failing gate; (2) the Stop hook blocks completion while the gate is failing;
(3) a trace shows `/think` firing on a stalled unknown but NOT on a routine one — i.e.
escalation is trigger-driven, verifiable in the run log.

## More Information

Grounded in convergent primary sources (Apr–Jul 2026): Osmani, *Agentic Code Review*
(Jun 16) and *Own the Outer Loop* (Jul 9); Anthropic Claude Code best-practices docs and
*How we contain Claude* (May 25, "containment at the environment layer first, then steer at
the model layer"); Willison, *Rewriting Bun in Rust* (Jul 8, test suite as primary
verification); Joshi/Fowler, *DSLs Enable Reliable Use of LLMs* (Jul 14, deterministic
validator in the generate→validate→repair loop); Karpathy, Sequoia Ascent summary (Apr 30,
"automate what you can verify"). Depends on ADR-8 (Stop-hook gate); reshapes ADR-6 and
ADR-11 (SpikeResult anchored on the gate).
