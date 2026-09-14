# Empirica Pi adapter (v2)

This is the **v2-only** Pi adapter. It is a *translator* over the `empirica/v2`
contract: it maps Pi's native events into requests and maps the guarded typed
decision back onto Pi's enforcement/UI. It holds no convergence rules — those
live in the host-neutral core, reached through the injected `dispatch` seam.

## D6 surface (what the adapter does)

| Pi surface | v2 request | Notes |
|---|---|---|
| `resources_discover` | — | Contributes the shared Empirica skill directory. |
| `/empirica <goal>` | `StartRun` | Attempts StartRun; D6 strict shell returns unsupported. Truthful UX: reports the StartRun attempt and any unsupported result — never relabels a start as a status read. |
| `empirica_status` tool | `ResolveRun` | Reports the run id and status only — no goal/modes/contract rendering. |
| `report_convergence` tool | `EvaluateRun(report_convergence)` | Hard gate: the tool is blocked unless the core returns a **guarded Allow**. |
| `tool_call` interception | `EvaluateRun(report_convergence)` | The hard gate (registered tool execute and `tool_call`). Also denies executable `subagent` launches locally. |
| `session_before_compact` | `RestoreRun` | Only if a real handle exists. |

## report_convergence hard gate (nonnull handle)

With a **nonnull run handle**, the gate permits **only** a guarded `Allow`:

| Result | Gate |
|---|---|
| `Allow` (converged true *or* false) | **permit** |
| `Block` | **deny** |
| `Inert` (run gone, handle exists) | **deny** |
| `Fault` (open *or* closed) | **deny** |
| transport error | **deny** (fail closed) |

`Allow` with `converged=false` is a valid, guarded **permit** — it is a
machine-approved non-convergence report, not a denial.

### Allow cross invariant (central guard)

The central inbound runtime guard (`guard.ts`) enforces: `converged` is `true`
**iff** `run.status` is `converged`; `converged` is `false` only for
`active` / `stopped_residual` / `stopped_frozen` / `stopped_budget`. A
mismatched `Allow` (e.g. `converged=true` with `status=active`) is rejected by
the guard so the hard gate fails closed.

## Subagent local fail-closed (D8-owned)

The conservative `pi@0.84.1` profile is **foreground-only** — no subagent
extension, no child-admission (D8) capability, no async child protocol. When a
**real Empirica run handle exists**, an executable `subagent` tool launch
(exactly one of `agent`, `workflowScript`, or `resume`) is **denied locally** as
unsupported — no dispatch, no child protocol/state. The denial is D8-owned.

Read-only management calls (`subagent { action: "list" }`, `subagent { action:
"status" }`) and malformed multi-key launches are **inert** (no denial, no
dispatch). A launch with **no handle** is also inert (nothing to deny against).

## Host profile

The exact conservative profile is `pi@0.84.1` (foreground-only). There is no
capability detection and no default — the bridge requires this exact profile and
fails closed if it is absent.

## Gaps (D6 boundary, not implemented by this adapter)

- **D7** owns v2 identity/location and reconnects the hardened repository.
- **D8** owns child admission (subagent launches); this adapter only denies
  locally.
- **D11** owns full generated parity (the mechanical + jsonschema subset checks
  here are the adapter's contribution).
- No audit/ticket/nonce/spawn pipeline, no nudge, no knowledge tool, no v1
  obligations contract. State lives only behind the transport; the only
per-session state is the active run's opaque handle, held in memory.
