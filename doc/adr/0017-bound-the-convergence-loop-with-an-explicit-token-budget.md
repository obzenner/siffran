---
number: 17
title: Bound the convergence loop with an explicit token budget
status: accepted
date: 2026-07-23
tags:
  - architecture
  - budget
  - termination
  - observability
links:
  - target: 9
    kind: Depends on
  - target: 13
    kind: Depends on
  - target: 8
    kind: relatesto
  - target: 16
    kind: relatesto
---

# Bound the convergence loop with an explicit token budget

## Context and Problem Statement

empirica is a loop that spawns subagents (spikes, discovery, optional adversarial review)
until its unknowns converge. Left unbounded, that is exactly the failure the dynamic-workflows
tooling was criticised for: a loop that consumes as many tokens as it can until the context
fills. A convergence loop with no cost ceiling is not deployable — the operator cannot predict
or cap spend, and an over-scoped task can burn indefinitely before the θ / block-cap guards
(ADR-9) even engage.

The design question: what bounds token spend, and — critically — what happens when the bound
is hit *before* the unknowns converge? A budget that silently reports success on exhaustion
would break the termination axiom (ADR-9: convergence ⇔ every unknown ≥ θ or blocked).

This ADR also records two adaptations that fell out of a `contradiction`-methodology pass over
the dynamic-workflows research (2026-07-23): they are the frugality-preserving forms of ideas
that, adopted as-is, would have contradicted empirica's stance.

## Decision Drivers

* Token burn is a first-class cost to minimise, not an afterthought (the operator's explicit
  disqualifying constraint for the workflow tooling).
* The budget must compose with — never override — the proven termination guards (ADR-9).
* Cost/spend must be an observable the loop can read, not a vibe (consistent with structured,
  verifiable state).
* Parallelism/agent-spawning must earn its coordination cost; the default is frugal.
* The deterministic gate stays the trust boundary; nothing here promotes an agent to arbiter
  (ADR-13).

## Considered Options

* **No budget (status quo)** — rely only on θ + block-cap. Rejected: those bound *iterations*
  and *unknowns*, not *tokens*; an expensive single pass is unbounded in spend.
* **Hard kill at N tokens** — abort the run when spend hits N. Rejected: an abort mid-loop
  discards work and, if it reports done, silently violates ADR-9.
* **Budget as a ceiling whose exhaustion is a blocked residual** — the loop takes an explicit
  token target; spend is tracked against it; when remaining budget cannot fund another
  productive pass, every still-open unknown is surfaced as a `blocked: needs-budget` residual
  (ADR-9's existing escape hatch) and the run stops honestly as "did not converge, N unknowns
  open, budget exhausted." Chosen.

## Decision Outcome

Chosen option: **"Budget as a ceiling whose exhaustion is a blocked residual."**

1. **Budget parameter.** empirica accepts an explicit token target for a run (operator-set,
   e.g. a `+Nk` directive or a default). With no target, the loop still runs but logs that it
   is unbounded — the frugal path is to always set one for non-trivial work.

2. **Spend is an observable.** The loop tracks tokens spent and remaining and treats the
   ceiling as hard: once spend reaches the target, no further subagent is spawned. Loop
   decisions (spawn a spike? escalate to `/think`? run adversarial review?) are gated on
   `remaining > cost-estimate`, cheapest-check-first (ADR-13).

3. **Exhaustion = residual, not convergence (the load-bearing rule).** If budget is exhausted
   while unknowns remain sub-θ, those unknowns are re-tagged
   `<!-- confidence: N, blocked: needs-budget -->` and surfaced to the human. The Stop gate
   then allows the stop (blocked residuals do not gate, ADR-9), but the run is reported as
   **non-converged**. Budget never fabricates a green result.

4. **Parallelism earns its cost.** Fan-out is an *exception*, not the default. empirica spawns
   parallel/isolated subagents only when a step is independent **and** breadth-bound **and**
   within remaining budget. Single-agent, sequential is the default shape.

5. **Adversarial review is secondary and budgeted.** An adversarial verification pass (a
   separate agent refuting a finding against a rubric) is opt-in, runs **after** the
   deterministic gate is green, on high-stakes findings only, and only if budget allows. It is
   a secondary sensor, never the trust boundary (ADR-13). (This is the frugal form of the
   dynamic-workflows "adversarially verify every finding" pattern.)

6. **Stall detection, not a second stop rule.** "K passes with no newly-derived narrower
   unknown and nothing crossed θ" is a *stall* signal → escalate once to `/think` (budget
   permitting) or surface the residual. θ remains the sole convergence rule; stall detection
   does not stop the loop on its own. (This is the frugal form of the "loop-until-dry" pattern
   — its real value is detecting a stuck loop, not adding a termination condition.)

Framing adopted alongside (no new mechanism): the loop's three failure modes are named —
*premature-done* (defended by the deterministic Stop gate), *self-preferential bias* (defended
by deterministic verification being the boundary, ADR-13), and *goal drift* (defended by
`SessionStart:compact` state re-injection, ADR-8).

### Consequences

* Good, because spend is predictable and capped — the operator sets a ceiling and the loop
  respects it, which is the precondition for using empirica at all.
* Good, because exhaustion is honest: a non-converged run says so and surfaces exactly which
  unknowns remain, reusing ADR-9's residual mechanism with zero new termination logic.
* Good, because frugal-by-default (fan-out and adversarial review are budgeted exceptions)
  keeps the common case cheap.
* Bad, because accurate pre-spawn cost estimation is imperfect; the ceiling may be
  approached, not hit exactly, so a small overshoot is possible (mitigated by estimating
  conservatively and checking before the expensive tier).
* Bad, because a too-small budget yields many `needs-budget` residuals — a poor experience,
  though an honest one; the fix is operator guidance on sizing, not a softer gate.

### Confirmation

Fitness function: (1) a run with a set budget never spawns a subagent after spend reaches the
target; (2) a run whose budget is exhausted with sub-θ unknowns exits with those unknowns
tagged `blocked: needs-budget` and is reported as non-converged — never as converged;
(3) with budget remaining and unknowns sub-θ, the loop continues (budget does not stop a
healthy loop early); (4) fan-out and adversarial review do not fire when `remaining` is below
their estimated cost; (5) the deterministic gate remains the pass/fail authority in every case
(ADR-13 fitness function still holds).

## More Information

Motivated by the operator's frugality constraint and by a `contradiction`-methodology analysis
(2026-07-23) of the dynamic-workflows research — Anthropic's "A harness for every task"
(Shihipar & Bidasaria, 2026-06-02) and the current Workflow tooling, which exposes a token
target and `spent()`/`remaining()` as first-class values. This ADR adopts that budget shape
while rejecting fan-out-as-default and keeping deterministic verification as the trust
boundary. Depends on ADR-9 (residual/termination mechanism reused for exhaustion) and ADR-13
(adversarial review stays secondary); relates to ADR-8 (durable-resume) and ADR-16 (build).
