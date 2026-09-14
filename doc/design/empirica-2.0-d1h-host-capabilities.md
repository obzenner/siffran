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
| Claude Code 2.1.270 | `foreground_only`; candidate `full_async` | Yes in foreground; async only after promotion | Dossier can be inserted into the admitted Agent input; the child's final answer is host-visible to the parent/author, so output privacy is **not** guaranteed | Exact admitted Agent invocation → native `agent_id` live correlation is required before promotion; serialization, type, timing, transcript path, or launch order are not proof |
| Pi 0.84.1 + pi-subagents 0.50.0 | candidate `full_async`, promoted only by runtime capability/live proof | Yes after promotion; otherwise explicit foreground fallback | Same-process RPC can supply the task; completion/result may be projected to the parent, so output privacy is **not** guaranteed | RPC ping/capability, session ownership, persisted ID/artifact root, async-started/complete, replay/reload, and first-terminal live proof |
| Pi 0.84.1 native without the conformance-listed pi-subagents profile | `foreground_only` | Yes, foreground only | Tool result is parent-visible; output privacy is **not** guaranteed | Forced foreground tool-call/result conformance and explicit async rejection |
| Codex CLI 0.146.0 | `observational` | No | No documented private child-input mutation or trusted child-output callback | Typed capability residual; no audit approval from observed spawn request; parent Stop remains enforceable |

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

1. PreToolUse reserves one child operation, enforces budget, stores the native `tool_use_id` and
   private completion capability in the trusted adapter/application record, and inserts only the
   dossier/instructions—not the capability—into that admitted Agent invocation.
2. The foreground PostToolUse result is correlated by the same native `tool_use_id` and records
   exactly one completion; invalid/missing audit output leaves audit unreadable.
3. PostToolUseFailure before observed start marks `launch_rejected` and refunds once; failure after
   start remains spent.
4. SubagentStart/Stop native IDs are retained as diagnostic observations but MUST NOT bind a
   reservation or receive a dossier unless an exact admitted-launch correlation is proven.
5. A SubagentStart lacking that exact correlation is unrelated/ambiguous. Serialization, matching
   agent type, timing, transcript path, and order are not sufficient.
6. A future candidate full-async promotion requires a live falsifier covering same-type resume and
   unrelated-launch races. Only after that proof may concurrent pending children complete out of
   order by native ID.
7. Application deadlines produce `timed_out`; later output is append-only diagnostic evidence and
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

Empirica binding contract:

1. Adapter verifies runtime RPC ping/capabilities and reserves one Empirica child operation, then
   invokes RPC `spawn` with a correlation token held by the trusted adapter, not model context.
2. RPC async/run ID becomes `host_child_id`; async-start binds it only after session ownership and
   artifact root are validated.
3. Same-process completion accelerates delivery; persisted validated status/replay is authoritative
   after reload and drives one idempotent terminal event.
4. On Pi reload, adapter reconciles every pending operation from Empirica state against the exact
   session-owned pi-subagents status/replay. Missing provider, substituted artifact root/run, or
   unprovable state fails closed as `orphaned`.
5. Stop/timeout controls are translated to the host, but Empirica first-terminal admission remains
   authoritative. Until the required live proof passes, the profile is not advertised full-async.

### Native Pi fallback

The adapter mutates the subagent tool to foreground execution only after explicit foreground
selection. `toolCallId` binds start/result. A tool error becomes `failed` or `launch_rejected`
depending on whether execution started. Background requests return `host.async_unsupported`.

## 5. Codex behavior

Pinned Codex 0.146.0 establishes a PreToolUse request and parent Stop gate but no supported
`updatedInput`, child completion/final-output callback, or transcript reference.

Therefore:

- a spawn request may be budgeted/recorded, but it is not a trusted started/completed audit child;
- automatic audit remains unavailable;
- attempted automatic audit returns `host.audit_output_unobservable` with recovery guidance;
- no nonce/ticket/dossier is exposed to the author to simulate the missing trusted path;
- graph, deterministic evidence, residual reporting, and parent Stop enforcement remain supported;
- a run that otherwise owes audit stops honestly with residual rather than reporting convergence.

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
- Pi+pi-subagents candidate promotion, session/artifact substitution, and replay behavior;
- Pi native foreground behavior;
- Codex observational behavior;
- advertised tier equals the exact runtime-detected conformance profile.

Live probes are mandatory for Claude full async, Pi+pi-subagents full async, Pi native foreground, and
Codex observational behavior before release.

## 8. Stop conditions

Implementation stops for maintainer decision if:

- Claude SubagentStart cannot be unambiguously paired after launch serialization;
- pi-subagents RPC/events differ from 0.50.0 documentation in a real Pi session;
- a host exposes a private completion capability in an author-visible view;
- Codex gains a new child-output API that would change its tier;
- any host cannot preserve terminal or exactly-once admission semantics.
