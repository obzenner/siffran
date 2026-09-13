# Bridge transport retry policy

Status: proposed design

## Scope

This policy applies to a host adapter dispatching one `empirica/v1` request to the host-neutral bridge. It does not retry application decisions. The application remains the sole authority for `Allow`, `Block`, `Inert`, and `Fault`.

## Safety premise

`request_id` is correlation metadata, not a deduplication key. A transport failure after the child starts is an **ambiguous outcome**: the core may have committed the request before its response was lost. Therefore a request may be retried only when replaying the exact command is independently idempotent. Every attempt reuses the exact serialized request, including `request_id`; this aids correlation but does not provide exactly-once execution.

## Failure classification

| Outcome | Classification | Action |
|---|---|---|
| Well-formed `Allow`, `Block`, `Inert`, or `Fault` | Authoritative application response | Return immediately; never retry. |
| Spawn fails with permanent local error (`ENOENT`, `EACCES`, invalid configuration) | Terminal transport failure, known not executed | Return failure immediately. |
| Spawn fails with transient resource error (`EAGAIN`, `EMFILE`, `ENFILE`) | Transient, known not executed | Retry within bounds for any command. |
| Child exits nonzero, is signalled, closes without output, or emits malformed/un-correlatable output | Ambiguous execution | Retry only commands in the replay-safe allowlist. |
| Attempt consumes the overall deadline | Ambiguous timeout | Kill the child, return timeout, and do not retry because no budget remains. |

Unknown errors default to ambiguous, never to transient-known-not-executed.

## Replay-safe allowlist

Replay safety is classified from the command and, for `ObserveAction`, its action kind.

### Safe

- Read-only: `ResolveRun`, `GetRun`, `RestoreRun`, and `EvaluateRun(intent="continue")`.
- `StartRun` while resuming the same active selector/generation. A retry must use the identical selector, goal, modes, and budgets. If the caller cannot ensure the generation remains active, treat it as unsafe.
- Immutable content-addressed knowledge appends with an identical body: `evidence`, `evidence_leaf`, `audit_verdict`, and `attribution`.
- Exact graph replay: graph artifacts are content-addressed and setting an already-current graph is a no-op.
- Idempotent/first-write-wins control operations when the replay payload is identical: `configure_budget`, `phase`, `mode`, `freeze`, `route`, `investigate`, and `consume_audit_ticket`.

First-write-wins means a conflicting concurrent winner remains authoritative; a retry may observe that winner and must not rewrite it.

### Unsafe

- `reserve_spawn`: replay can reserve and charge a second spawn.
- `dispatch`: replay appends a second dispatch record.
- `audit_ticket`: replay issues a fresh nonce/ticket.
- `EvaluateRun(intent="report_convergence" | "stop")`: replay can count another progress/idle stop or cross a terminal bound.
- Any unknown command or action kind.

Unsafe ambiguous outcomes fail closed and surface `outcome_unknown`; they are reconciled with a read (`RestoreRun`/`GetRun`) or a domain-specific lookup, not blind replay. No retry mechanism may convert transport uncertainty into an `Allow`.

## Bounds

- **Maximum attempts:** 3 total (initial attempt plus at most 2 retries).
- **Overall deadline:** one monotonic 30,000 ms budget by default, configurable only at construction. It is not reset per attempt.
- **Backoff:** full jitter, `delay = U(0, min(100 ms × 2^(retry_index-1), 1,000 ms))`, where retry index 1 is the first retry. Tests inject the random source.
- Before sleeping or spawning, stop if the remaining deadline cannot cover the chosen delay plus a 1 ms dispatch margin.
- Each child receives only the remaining overall deadline. A timeout consumes the budget and is terminal.
- Cancellation aborts the current child and all future retries.

These bounds cap process creation, latency, and synchronized retry bursts. The policy uses monotonic time so wall-clock changes cannot extend it.

## Observability

Emit one structured record per attempt with: stable `request_id`, command/action classifier, attempt number, elapsed and remaining milliseconds, outcome class, replay-safe decision, and final disposition. Never log the opaque run handle or request body by default. A final ambiguous unsafe failure must explicitly say that execution may have committed and name the required reconciliation read.

## Deterministic acceptance scenarios

1. A read fails ambiguously twice and succeeds on attempt 3: exactly three attempts, with two bounded sleeps.
2. `reserve_spawn` fails ambiguously on attempt 1: exactly one attempt and `outcome_unknown`.
3. `reserve_spawn` gets transient pre-execution `EAGAIN`, then succeeds: two attempts are allowed because the failed spawn is known not executed.
4. A well-formed `Fault` is returned on attempt 1: exactly one attempt.
5. `audit_ticket` emits malformed output after the child started: exactly one attempt and reconciliation required.
6. Retry delay or execution would exceed the overall deadline: no additional attempt.
7. An unknown action fails ambiguously: fail closed after one attempt.
