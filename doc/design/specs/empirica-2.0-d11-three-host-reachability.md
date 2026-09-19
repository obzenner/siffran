# Empirica 2.0 D11 — three-host driver conformance and live promotion

Status: implementation in progress; profile promotion blocked on installed-host receipts

## Decision

Empirica 2.0 supports Claude Code, Pi, and Codex only when each exact advertised
profile can complete the same positive lifecycle through surfaces available to a
real model:

```text
StartRun → route → graph → research → deterministic spike → current RunView
→ bound independent audit → private verdict ingress → Allow(converged=true)
```

A typed unsupported result is a safe intermediate state, not release support.
Direct application dispatch and private conformance seams do not establish host
support.

## Invariants

1. Every active run is progressable through the active host; otherwise activation
   refuses before `StartRun`.
2. Public model operations admit only actions listed in
   `PublicContract.actions.author`.
3. `evidence_leaf`, `attribution`, `child_event`, and `audit_verdict` remain private
   host ingress and never appear in model-callable schemas.
4. The process exit code remains the sole machine approver.
5. Audit input is the current `GetArgument` dossier. The author never supplies the
   verdict ingress or grades convergence.
6. Child correlation is bound to one run, one reserved child, one native execution,
   and the first terminal result.
7. Host/model independence is derived from observed attribution and may not be
   invented from an alias or requested model.
8. A profile is promoted only by a positive production-adapter acceptance trace.

## Shared public surface

All hosts expose equivalent semantics:

- `empirica_read`: `GetRun`, `GetArgument`, `GetContract`, or `RestoreRun`;
- `empirica_observe`: one public `ObserveAction` whose kind belongs to
  `PublicContract.actions.author`;
- `report_convergence`: `EvaluateRun(report_convergence)`.

Host activation owns `StartRun`, protocol version, request correlation, exact
profile selection, and the current opaque handle. An adapter may inject the handle
instead of accepting it from the model, but it may not silently target another run.
Responses remain canonical v2 results; rendering is presentation only.

The public schema is a mechanical projection of `request.schema.json` partitioned
by `public-contract.json.actions`. It is not a second policy model.

## Host audit bindings

### Claude Code

The existing `PreToolUse:Agent` and `SubagentStop` lifecycle binds the current
argument to the canonical auditor and privately records child lifecycle,
attribution, and verdict. A shared MCP server supplies the missing public author
and read operations. Activation injects the opaque handle into model context.

### Pi

The extension supplies the public tools and uses the installed, exact
pi-subagents profile. `tool_call` reserves/binds the canonical auditor and injects
the dossier; `tool_result` observes and redacts the candidate result before any
await, reads the exact result row's bounded host-generated child session, binds the
final native assistant provider/model to the matching verdict, then calls private
bridge ingress. Durable correlation survives reload; missing or ambiguous session
evidence remains unverified.

### Codex

The shared MCP server supplies public tools. Because Codex 0.146.0 cannot inject a
native child prompt or observe child final output, the trusted Stop adapter owns a
bounded `codex exec` auditor subprocess. It obtains the dossier, reserves the child,
observes the process and concrete model configuration, parses one closed verdict,
and invokes private ingress. The MCP tools remain public-only. The command/model
configuration is fixed by the host adapter, never by tool input. Missing or
same-model attribution blocks honestly.

## Red-first acceptance

`make empirica-host-adapter-check` is the deterministic red-first layer. It must initially fail
because the public server and host drivers are absent, but it never claims to launch an installed
host. `make empirica-host-live-check` is the separate promotion gate: it requires retained receipts
from credentialed, installed Claude, Pi, and Codex processes. `make release-check` requires both.

The deterministic adapter traces become green when each adapter translates its available native
facts correctly. Claude and Pi can reach injected `Allow(converged=true)`; Codex 0.146.0 must end
in `audit.independence_unverified` because its managed process does not expose an observed resolved
model. Promotion still waits for the live gate and cannot override that typed limitation.

The acceptance matrix is layered rather than duplicated in every positive trace:

- model schemas exclude every trusted action and capability reference;
- graph/research/spike/freeze pass through the real v2 bridge;
- the spike harness runs exactly once against captured bytes;
- audit is bound to current argument and exact child;
- malformed output, stale audit, wrong child, transport failure, and same/unverified
  model identity never converge;
- terminal replay cannot alter the result.
