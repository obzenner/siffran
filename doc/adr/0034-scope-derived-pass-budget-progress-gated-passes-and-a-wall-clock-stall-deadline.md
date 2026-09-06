---
number: 34
title: "Scope-derived pass budget, progress-gated passes, and a wall-clock stall deadline"
status: proposed
date: 2026-09-06
tags:
  - budget
  - termination
  - audit
links:
  - target: 17
    kind: Amends
  - target: 19
    kind: Amends
  - target: 31
    kind: Amends
---

# Scope-derived pass budget, progress-gated passes, and a wall-clock stall deadline

## Context and Problem Statement

ADR-17 bounds the loop with two enforced quantities: `max_spawns` (fan-out cost) and
`max_passes` (loop length, default 8, via the ADR-19 variant `max_passes − passes`).
`max_passes` is an ungrounded a-priori constant. It has two defects, and the second is a
live bug.

First, `8` does not scale with scope. A three-claim goal and a thirty-claim goal get the
same budget; the small run wastes headroom and the large run cannot possibly close inside
it. The constant encodes no relationship to the work.

Second — the observed failure — `max_passes` is incremented on **every stop attempt**
against a blocking run (`application/service.py::_finalize_block`). Waiting for an async
auditor is done by ending turns and letting the Stop gate re-block, so each idle wait
consumes a pass. A measured 825s audit ended the turn repeatedly while it ran and burned
all 8 passes; the run tripped `stopped_budget` before the verdict landed. Worse, once the
run is terminal, the arriving audit verdict is refused (`FAULT_CONFLICT`) — the auditor's
work is discarded. So the mandatory audit that ADR-17/ADR-20 require can, on a slow audit,
be the very thing that exhausts the budget and is then thrown away.

This record supersedes the **budget half** of ADR-17 (`max_passes` as a fixed a-priori
constant incremented per stop) and amends the bounded-termination reasoning of ADR-19 and
the operational-state schema of ADR-31. ADR-17's spawn budget and enforcement trust model,
and ADR-19's fail-closed identity, are unchanged.

## Decision Drivers

* The bound must scale with scope, not sit at a hand-picked constant.
* Waiting on a dispatched actor (the required auditor) must be **safe** — it must not
  consume the loop-length budget.
* Termination must still be guaranteed and provable: every run ends.
* The mandatory audit's verdict must be durable even if it lands after the run terminates.
* No run may extend its own a-priori ceiling — that is the ADR-19 termination guarantee and
  the ADR-28 proposer≠accepter rule; only a distinct principal (a human) raises it.
* All new policy stays in the application service; `core/convergence.py` stays pure.

## Considered Options

* **Keep a fixed a-priori `max_passes` (status quo).** Rejected: it is ungrounded, does not
  scale with scope, and — because it is incremented on every stop against a blocking run —
  is the actual bug. A slow but healthy audit exhausts it and the verdict is then refused.

* **Bound idle waits by a turn/idle-stop COUNT instead of wall-clock.** Cap the number of
  consecutive no-progress stops. Rejected: the count is cadence-dependent, not
  work-dependent. This run measured ~103s per stop only because a per-turn state-check
  happened to run each turn; a bare wait loop re-blocks in seconds. So a count large enough
  to survive a real 825s audit would be enormous and *still* would not track wall-time —
  the same count means minutes on one host and seconds on another. A wall-clock deadline
  tracks the quantity that actually matters (elapsed time with no knowledge progress).
  Verified the hook process can read its own clock: Claude Code hook input carries no
  timestamp, and the docs direct the hook to generate one — so `time.time()` in the adapter
  is the clock source.

* **Scope-derived working cap under an a-priori ceiling, passes gated by knowledge
  progress, idle waits bounded by a wall-clock stall deadline.** Chosen — see below.

## Decision Outcome

Chosen option: the third. Four changes.

**A. Scope-derived working budget under an a-priori ceiling.** `max_passes` becomes the
a-priori **ceiling** (a hard upper bound), and a new **working budget** — a soft cap the
run actually stops at — is derived from the seeded claim graph and re-derived as the graph
grows. New pure module `core/budget.py` (no I/O, unit-testable) counts gating open claims
via existing `core/claims.py` helpers and computes:

```
working_passes  = min(ceiling, open_claims + PASS_RESERVE)     # PASS_RESERVE = 2  (audit round + finalize/convergence check)
working_spawns  = externally_evidenced_open_claims + AUDIT_RESERVE   # AUDIT_RESERVE = 1
ceiling         = ceiling_for(seed_open_claims) = max(CEILING_FLOOR, CEILING_SCOPE_MULTIPLIER × seed_open_claims)
                                                # CEILING_FLOOR = 8, CEILING_SCOPE_MULTIPLIER = 3
stall_deadline  = 1800s                          # ~2.2× the measured 825s worst-case audit
```

The working caps **auto-re-derive as scope grows** — when the graph gains gating claims,
`_update_graph` recomputes `max_passes := max(max_passes, ceiling_for(seed_open_claims))`
and `working_passes := derive(...)[0]` on the same CAS write, and sets `working_spawns`
only if no explicit operator cap exists (never overriding one). The coefficients
(`PASS_RESERVE`, `AUDIT_RESERVE`, `CEILING_FLOOR`, `CEILING_SCOPE_MULTIPLIER`,
`stall_deadline`) are env-overridable defaults. A run may **not** self-raise the ceiling; a
human raises it via `configure_budget` (preserving the ADR-19 termination guarantee and the
ADR-28 proposer≠accepter rule).

**B. A pass counts knowledge PROGRESS, not turn-ends.** At finalize, the service computes a
progress token — a `sha256` over `(pointer, len(evidence), len(evidence_leaves),
len(verdicts), len(audit_tickets))`. If the token differs from `last_stop_digest`, the stop
made progress: `passes += 1`, the token and `last_progress_ts` are recorded, and the run
stops if `passes >= (working_passes or max_passes)` — an honest `stopped_budget`. If the
token is unchanged, the stop is an idle wait: **`passes` is unchanged**. Waiting on a
dispatched auditor therefore costs no passes.

**C. Wall-clock stall deadline via a hook-injected clock.** Idle waits are bounded by
elapsed time, not count. The adapter injects `observed_at` (epoch seconds from the hook's
own clock) into the stop request. On a no-progress stop, if
`(observed_at − last_progress_ts) > stall_deadline_sec` (default 1800s), the run terminates
`stopped_residual` ("no knowledge progress for {T}s; audit did not return or the loop
stalled"). `last_progress_ts` is seeded on the first stop, so the deadline measures from
first stop, not run creation. The auditor writing a verdict *is* progress (verdict count
changes) → it resets the timer, and the run converges on the next stop.

This preserves and sharpens the ADR-19 termination guarantee: **every real stop either
makes progress (→ exactly one pass, bounded by `working_passes ≤ ceiling`) or does not
(→ bounded by the wall-clock stall deadline). The total run is bounded.**

**D. Late audit verdict admissible on a terminal run.** When a `KIND_AUDIT_VERDICT` arrives
after the run is terminal and its `nonce` matches an ISSUED ticket in `state.audit_tickets`,
it is admitted via a pure knowledge append instead of `FAULT_CONFLICT`. It cannot flip a
terminal run to converged (`adjudicate` refuses to re-judge a finished run), so it is safe
and makes the audit's work durable. All other kinds keep today's terminal behavior.

State (ADR-31 `OperationalState`) gains `working_passes`, `last_stop_digest`, and
`last_progress_ts` (all `int | None` / `str | None` / `float | None`, default `None`), with
`encode()`/`decode()` symmetry and fail-closed decode (corrupt ≠ absent). A pre-fix document
missing these fields decodes with the defaults and behaves compatibly: no working cap →
falls back to `max_passes`; no digest → first stop counts as progress; no `last_progress_ts`
→ seeded on first stop.

### Consequences

* Good, because the bound now scales with scope instead of a hand-picked 8.
* Good, because waiting on the mandatory auditor no longer exhausts the loop budget — the
  measured 825s-audit failure cannot recur.
* Good, because a slow audit's verdict is durable even if it lands after termination.
* Good, because termination is still provable and the ceiling is still human-gated.
* Neutral, because `core/convergence.py` stays pure; all new policy is in the service, and
  each adapter gains only a one-line clock injection (`observed_at`).
* Bad, because the wall-clock deadline depends on the host injecting a truthful clock; a
  host that omits `observed_at` falls back to pass-count termination only (still bounded).
* Bad, because operational state gains three fields and a migration-tolerant decode path.

### Confirmation

Pure service/state tests (no host) must prove: N consecutive idle stops leave `passes`
unchanged; one evidence/verdict append between two stops counts exactly one pass and resets
the stall timer; exceeding `stall_deadline_sec` with no progress yields `stopped_residual`
(not `stopped_budget`); a `KIND_AUDIT_VERDICT` with a matching ticket nonce on a terminal
run is appended and readable with status unchanged while a non-matching nonce still faults;
`budget.derive` is monotone in claim count and always `≤ ceiling`, and `ceiling_for ≥ 8`;
total stops are bounded by `working_passes + a finite stall window` (no infinite loop); and
`decode()` of a pre-fix document (missing the new fields) succeeds with defaults and runs.
