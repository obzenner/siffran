# Empirica 2.0 D1-H host capability matrix

**Status:** Parent-frozen support decision for implementation and conformance.

**Host versions examined:** Claude Code 2.1.270; Codex CLI 0.146.0; Pi 0.84.1;
pi-subagents 0.50.0.

**Evidence date:** 2026-09-13.

This document describes what Empirica may claim. It does not elevate a host because its schema can
represent a lifecycle; the adapter must observe and prove the lifecycle through native events.

## 1. Capability tiers

### `full_async`

The integration can:

- assign an Empirica child identity before launch;
- bind one native child identity at observed start;
- observe terminal completion/failure or deterministically reconcile absence;
- correlate concurrent children completing out of order;
- recover or explicitly orphan pending children after host/session reload;
- access final output through a native event or documented artifact;
- deduplicate/replay completion into exactly one application transition;
- enforce timeout/cancel/orphan transitions and terminal late-result rules;
- expose structured pending/residual state without exposing private completion capabilities to the author.

A host may synthesize `timed_out` and `orphaned` from the durable Empirica deadline plus native
absence/reconciliation evidence. Those are application decisions over observed facts, not invented
host events.

### `foreground_only`

The integration forces child execution to complete inside one observed tool operation. It records
`reserved -> pending -> completed|failed|timed_out` durably but advertises no background/reload
support. A foreground-only host must reject an async request with `host.async_unsupported`; it may
not silently downgrade except when the caller explicitly accepts foreground execution.

### `observational`

The integration can observe a launch request but cannot bind a native child start and trusted final
output. It records the attempt and the capability residual, but it cannot use that child to satisfy
an audit or evidence obligation. It must not claim automatic independent audit support.

## 2. Support decisions

| Host/profile | Tier | Advertised automatic audit | Privacy statement | Required implementation evidence |
|---|---|---|---|---|
| Claude Code 2.1.270 | `foreground_only`, promoted; candidate `full_async` | Yes in foreground; async only after separate promotion | Dossier can be inserted into the admitted Agent input; the child's final answer is host-visible to the parent/author, so output privacy is **not** guaranteed | Credentialed installed foreground trace binds the exact admitted Agent invocation to native `agent_id`; candidate async still requires its separate correlation probe |
| Pi 0.84.1 + pi-subagents 0.50.0 | `foreground_only`, promoted | Yes when the exact host-generated child session binds a concrete native assistant identity to the admitted verdict | The bound tool result is parent-visible, so output privacy is **not** guaranteed | Credentialed installed foreground trace: canonical packaged agent → `toolCallId` correlation, synchronous closed redaction, bounded session receipt, verdict-bound native identity, private ingress, and guarded `Allow(converged=true)`; async remains unsupported |
| Codex CLI 0.146.0 | `observational`, `wip_unsupported`; candidate `foreground_only` | No supported convergent audit: managed-process resolved model is not natively observed | Final output is observed by the adapter process; output privacy is **not** guaranteed | WIP public MCP and bounded-process conformance only; exact final-output parsing and Stop re-evaluation honestly end in `audit.independence_unverified` until native identity and the candidate probe exist |

Only an exact conformance-listed profile receives its declared tier. An unlisted version is
`observational`—or unavailable when even observation is unproven—until deterministic fixtures and
required live probes are accepted and this table is updated.

## 3. Claude binding protocol

Official hooks establish:

- `PreToolUse` can allow/deny and replace Agent input;
- `SubagentStart` fires for spawn/resume and supplies unique `agent_id`, `agent_type`, and
  `additionalContext` injection before the first prompt;
- `SubagentStop` supplies the same `agent_id`, `agent_type`, child transcript path, and final
  assistant message;
- `PostToolUseFailure` observes failed Agent tool execution;
- `SessionStart` observes resume/compact/fork; Stop carries background task state.

Sources: `https://code.claude.com/docs/en/hooks`, retrieved 2026-09-13, especially the
PreToolUse, SubagentStart, SubagentStop, PostToolUseFailure, SessionStart, and Stop sections.

Empirica foreground binding contract:

1. PreToolUse verifies the exact plugin-scoped auditor, forbids model overrides, reserves exactly
   one audit operation, and replaces the complete child prompt with the immutable dossier/rubric.
2. A second outstanding reserved audit is refused; there is never an order-based choice among
   multiple reservations.
3. SubagentStart is the first native-start observation. Its exact `agent_id` drives
   `reserved -> launching -> pending`; the adapter retains that native ID privately.
4. SubagentStop must carry the same exact `agent_id`. Private correlation resolves that ID to one
   pending child before identity or verdict admission; purpose, timing, and list order are ignored.
5. Parent and child model identities are read from their host transcripts at terminal observation,
   not from requested model aliases. Missing concrete identity remains `unverified`.
6. PostToolUseFailure while the operation is still reserved records `launch_rejected` and refunds
   once; malformed terminal output records `failed` after observed start.
7. A future candidate full-async promotion requires a separate live falsifier for concurrent
   same-type starts, resume, cancellation, and out-of-order terminal delivery.
8. Application deadlines produce `timed_out`; later output is append-only diagnostic evidence and
   cannot reopen or converge a terminal child or run.

## 4. Pi binding protocols

### Pi plus pi-subagents 0.50.0

Documented public integration:

- same-process RPC events `subagents:rpc:v1:ready`, request, and per-request reply;
- detached async `spawn` through the same validated executor as the model tool;
- generated async/run IDs, async directory, status and lifecycle artifacts;
- `subagent:async-started`, `subagent:async-complete`, process-terminal and control events;
- durable `status.json`, `events.jsonl`, output logs, result/replay archives;
- stale-run reconciliation and reload restoration;
- public background-work, external-runs, delegation, control-channel, and shared-types imports;
- duplicate terminal-delivery suppression and replay.

Sources: installed `/Users/dmitry.lambrianov/.pi/agent/npm/node_modules/pi-subagents` 0.50.0,
`docs/extension-api.md`, `docs/observability.md`, `docs/tool-reference.md`, and exported APIs.

Empirica's promoted foreground binding uses the structured model tool rather than claiming the
separate async RPC lifecycle:

1. `tool_call` reserves one canonical audit child and forces `async=false`.
2. The adapter binds the native `toolCallId`, injects the current dossier, and records
   `launching -> pending` through private ingress.
3. `tool_result` retrieves the exact correlated foreground output and redacts the fenced verdict
   synchronously before its first await.
4. The adapter ignores configured result-model metadata. It reads the exact result row's bounded,
   host-generated `sessionFile` and accepts provider/model identity only from the final native
   assistant record when its sole verdict equals the admitted tool-result verdict.
5. The adapter privately records that observation and admits the candidate verdict; missing,
   changed, malformed, or ambiguous session evidence remains `independence_unverified`.
6. Reload restores the opaque run and any persisted correlation entry; async requests remain
   `host.async_unsupported` until the candidate probe is promoted.

## 5. Codex behavior

Pinned Codex 0.146.0 provides public MCP tools, durable hook identity, and a parent Stop gate,
but no native `updatedInput`, child final-output callback, or child transcript reference.
The trusted Stop adapter therefore owns a bounded foreground `codex exec` process:

1. evaluate the located run and proceed only when audit is the current obligation;
2. reserve one foreground child and obtain the current `GetArgument` dossier;
3. record launching/pending and author/auditor attribution privately;
4. invoke `codex exec --ephemeral --sandbox read-only` with an adapter-pinned model and dossier;
5. parse exactly one final fenced verdict, admit it through private ingress, and re-evaluate;
6. permit Stop only for guarded `Allow(converged=true)`.

The MCP tools remain public-only and cannot express trusted ingress. Process failure, malformed
output, same/unverified attribution, or unavailable evaluation blocks Stop.

Sources: `plugins/empirica/adapters/codex/README.md` and pinned official Codex 0.146.0 hook sources
listed there.

## 6. Host event mapping to the canonical child machine

`contracts/empirica/v2/public-contract.json` is the sole owner of child states and allowed
transitions. D1 defines the semantics; this document maps native host evidence to those transitions
and does not redefine a profile-specific state machine.

Host mapping rules:

- one server-generated `child_id` names the single application record containing purpose, state,
  spent/refunded fact, deadline, optional native ID, and private completion capability;
- there is no separate reservation entity, audit-ticket entity, reservation sequence, nonce, or
  ticket-consumption state;
- optional native `host_child_id` binds once through the profile's proven start correlation;
- launch rejection refunds once; any observed start spends the spawn permanently;
- pending work consumes no derivation pass and suppresses duplicate equivalent launches;
- after the first child terminal transition, no later event may admit audit output or alter child,
  audit, budget, or convergence state—even while the run remains active;
- an identical replay of the first terminal event fingerprint is an idempotent no-op; a non-identical
  terminal delivery is recorded as a diagnostic conflict and returns `Fault`;
- child result cannot approve deterministic evidence;
- audit output is admitted only by the first valid `pending -> completed` transition;
- terminal run status is never reopened by a late child result.

## 7. Required conformance cases

Fixtures are capability-conditional:

- `full_async`: complete reserve/start/bind/concurrent out-of-order/terminal/reload/replay list;
- `foreground_only`: reserve/launch/start/result/failure/timeout/duplicate/late-result plus explicit
  async rejection; detached concurrency/reload is asserted unsupported;
- `observational`: request recording, exact capability residual, no fabricated native bind/completion,
  and no audit admission.

The fixture manifest declares each unsupported transition; it may not record it as a passing success.
Each following case is tested positively only for tiers that advertise it; every other tier tests the
exact unsupported capability reason and proves that no native bind, completion, audit admission, or
pending child was fabricated:

- reserve, start/bind, completion;
- two children completing out of order;
- duplicate completion;
- conflicting completion;
- launch rejection/refund exactly once;
- failure after start remains spent;
- cancellation, timeout, and orphan;
- reload between launch and completion;
- pending child consumes no pass and suppresses duplicate spawn;
- private completion capability absent from public GetRun/GetArgument/Restore/PublicContract;
- terminal late completion cannot produce convergence;
- unsupported async or output observation returns the exact capability reason;
- Claude foreground and candidate exact-correlation race behavior;
- Pi+pi-subagents foreground tool-call/result binding and candidate async promotion behavior;
- Codex managed foreground auditor and Stop re-evaluation behavior;
- advertised tier equals the exact conformance-listed profile.

Live probes are mandatory for the supported Claude and Pi foreground profiles before release.
Candidate full-async promotion requires its separate named probe and does not block foreground
support. Codex is WIP and excluded from the supported release set; its managed-foreground probe is
a candidate-promotion prerequisite rather than a release receipt.

## 8. Stop conditions

Implementation stops for maintainer decision if:

- Claude SubagentStart cannot be unambiguously paired after launch serialization;
- pi-subagents RPC/events differ from 0.50.0 documentation in a real Pi session;
- a host exposes a private completion capability in an author-visible view;
- Codex gains a new child-output API that would change its tier;
- any host cannot preserve terminal or exactly-once admission semantics.
