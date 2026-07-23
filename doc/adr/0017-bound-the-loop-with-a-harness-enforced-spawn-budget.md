---
number: 17
title: Bound the loop with a harness-enforced spawn budget
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

# Bound the loop with a harness-enforced spawn budget

## Context and Problem Statement

empirica is a loop that spawns subagents (spikes, discovery, optional adversarial review)
until its unknowns converge. Left unbounded, that is exactly the failure the dynamic-workflows
tooling was criticised for: a loop that consumes as much as it can until the context fills.
The operator's requirement is a hard, predictable cost ceiling.

The obvious design — a *token* budget — was investigated and found **unenforceable**, which
reframes the whole decision. Enforcement demands a currency the harness can both COUNT
truthfully and DENY at the moment of spending. Verified against code.claude.com/docs
(2026-07-23):

* **Token spend is NOT readable mid-session.** Hook payloads explicitly exclude token counts;
  `SubagentStop` carries none; the transcript is undocumented, async-lagged, and version-
  fragile; there is no `/cost` command; OTEL is opt-in and external (post-hoc, not
  preventative). So any "tokens spent" figure a hook could act on would be a self-tracked
  guess — advisory, not enforced. That is precisely the "please behave" theater empirica
  exists to reject.
* **Subagent spawns ARE both countable and denyable.** A `PreToolUse` hook with
  `matcher: "Agent"` fires exactly once per spawn and can deny it (exit 2 + stderr reason).
  Fire-once-per-spawn makes the count ground truth; deny makes the ceiling real.

So the enforceable budget's currency is **spawns, not tokens**. Since the mechanism changed,
the name changed (this ADR is "spawn budget," not "token budget"). The remaining question is
what happens when the ceiling is hit before the unknowns converge — a budget that silently
reported success on exhaustion would break the termination axiom (ADR-9).

This ADR also records the frugality-preserving adaptations from a `contradiction`-methodology
pass over the dynamic-workflows research (2026-07-23).

## Decision Drivers

* Enforcement over advice: a budget the model may ignore is worthless; it must be denied by
  the harness (same trust model as the Stop gate, ADR-8).
* The budget must compose with — never override — the proven termination guards (ADR-9).
* The enforced quantity must be one the harness can measure without guessing.
* Parallelism/agent-spawning must earn its coordination cost; the default is frugal.
* The deterministic gate stays the trust boundary; nothing here promotes an agent to arbiter
  (ADR-13).

## Considered Options

* **No budget (status quo)** — rely only on θ + block-cap. Rejected: those bound *iterations*
  and *unknowns*, not the fan-out that actually drives cost.
* **Token budget, self-tracked** — the loop estimates tokens spent and stops at a target.
  Rejected: actual spend is unreadable mid-session (see Context), so this is advisory only —
  the model can spawn regardless. Unenforceable = not a budget.
* **Token budget via OTEL** — enable telemetry, read spend from an external collector.
  Rejected: opt-in, requires external infrastructure, and is post-hoc — it cannot deny a
  spawn at spawn time. Kept only as an optional informational `cost_usd` audit that never
  gates.
* **Harness-enforced spawn budget** — a `PreToolUse` hook denies subagent spawns past a
  `max_spawns` cap; exhaustion surfaces still-open unknowns as `blocked: needs-budget`
  residuals (ADR-9's existing escape hatch). Chosen: spawns are the enforceable, countable
  proxy for cost, and denial is real.

## Decision Outcome

Chosen option: **"Harness-enforced spawn budget."**

1. **Spawn cap.** A run sets `max_spawns` in a transient ledger
   `.claude/empirica/<run>/budget.json` (ADR-14 scratch, git-ignored; `hooks/budget.py`
   owns it). `null` = unbounded (allowed, logged). The frugal path sets a finite cap for
   non-trivial work.

2. **Enforcement is a PreToolUse gate.** `hooks/spawn_gate.py` (matcher `Agent`) fires before
   every subagent spawn, atomically reserves one slot against the cap (OS file lock, so
   parallel spawns cannot race past it), and **denies the spawn (exit 2 + reason) once the cap
   is reached.** The count is ground truth because the hook fires once per spawn. This is
   harness-enforced, not model goodwill (the trust model of ADR-8).

3. **The deterministic gate is never spawn-gated.** `spike_harness.py` runs a subprocess, not
   a subagent — it is ~free and IS the trust boundary (ADR-13), so it always runs regardless
   of the spawn budget. The budget only ever throttles *agent spawning*.

4. **Exhaustion never fabricates convergence.** When the cap denies a spawn and unknowns are
   still sub-θ, each is marked `<!-- confidence: N, blocked: needs-budget -->`. The Stop gate
   then allows the stop (blocked residuals do not gate, ADR-9) but reports `converged: false`
   — an honest "did not converge, spawn budget exhausted, N open." Raising `max_spawns`
   resumes the loop.

5. **Parallelism earns its cost.** Fan-out is a budgeted exception, not the default;
   single-agent sequential is the default shape. Each fanned-out agent consumes a spawn slot,
   so the cap directly bounds fan-out width.

6. **Adversarial review is secondary, post-gate, and spawn-budgeted.** It runs after the
   deterministic gate is green, on high-stakes findings only, and each reviewer is a spawn
   subject to the cap. Never the trust boundary (ADR-13). (Frugal form of "adversarially
   verify every finding".)

7. **Stall detection, not a second stop rule.** K passes with no newly-derived narrower
   unknown and nothing crossing θ = a stall → escalate once to `/think` (a spawn, budget
   permitting) or surface the residual. θ remains the sole convergence rule. (Frugal form of
   "loop-until-dry".)

8. **Token cost is post-hoc only.** An optional `cost_usd` field (from OTEL, if the operator
   enabled it) may be recorded for auditing. It is informational and NEVER gates a spawn.

Framing adopted alongside (no new mechanism): the loop's three failure modes are named —
*premature-done* (defended by the deterministic Stop gate), *self-preferential bias*
(defended by deterministic verification being the boundary, ADR-13), and *goal drift*
(defended by `SessionStart:compact` state re-injection, ADR-8).

### Consequences

* Good, because the budget is genuinely enforced: the PreToolUse gate denies over-cap spawns,
  it is not a request the model can disregard.
* Good, because spawns are countable without guessing (fire-once-per-spawn), so the ceiling
  is exact — unlike a token estimate.
* Good, because exhaustion is honest: a non-converged run says so and surfaces which unknowns
  remain, reusing ADR-9's residual mechanism with no new termination logic.
* Good, because frugal-by-default (fan-out and adversarial review are spawn-budgeted
  exceptions) keeps the common case cheap.
* Bad, because spawns are a coarse proxy for cost: one expensive long-context agent counts the
  same as one cheap one. The cap bounds *breadth*, not depth-of-context spend. An operator who
  needs $-precision must add OTEL post-hoc; this ADR does not give real-time token control
  (because the platform does not expose it).
* Bad, because a too-small cap yields many `needs-budget` residuals — honest, but a poor
  experience; the fix is operator guidance on sizing, not a softer gate.
* Bad, because the file-lock reservation is best-effort where `fcntl` is absent (Windows);
  POSIX (the repo's target) is fully guarded.

### Confirmation

Fitness function (all pinned by `.claude/spike-m3` regression checks): (1) a run with a finite
`max_spawns` has its (cap+1)th `Agent` spawn DENIED by the PreToolUse gate (exit 2) — D9/D10;
(2) a denied spawn does not increment the counter, and concurrent reservations cannot exceed
the cap — D4–D6; (3) non-`Agent` tools are never gated, even at a 0 cap — D12; (4) no ledger
/ unbounded cap allows every spawn (fail-open) — D8/D13; (5) exhaustion with sub-θ unknowns
makes the Stop gate report `converged: false`, never green — E1–E3; (6) a healthy sub-θ loop
still blocks regardless of budget — E5; (7) the deterministic gate remains pass/fail authority
(ADR-13 fitness unchanged).

## More Information

Motivated by the operator's demand that an unenforced budget is worthless, and by two
claude-code-guide research passes over the current hooks docs (2026-07-23): PreToolUse can
deny an `Agent` spawn with a reason; token spend is not exposed to any hook. Contrasts with
Anthropic's "A harness for every task" (Shihipar & Bidasaria, 2026-06-02) and the Workflow
tooling, whose `budget.spent()`/`remaining()` are available *inside* that runtime but not to a
plugin hook. This ADR adopts the enforceable spawn-cap form, rejects fan-out-as-default, and
keeps deterministic verification as the trust boundary. Depends on ADR-9 (residual/termination
mechanism reused for exhaustion) and ADR-13 (adversarial review stays secondary); relates to
ADR-8 (harness enforcement, durable-resume) and ADR-16 (build).
