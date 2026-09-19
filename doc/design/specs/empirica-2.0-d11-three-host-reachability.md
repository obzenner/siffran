# Empirica 2.0 D11 — host-driver conformance and supported live promotion

Status: Claude and Pi foreground profiles promoted; Codex adapter WIP and unsupported

## Decision

Empirica 2.0 supports the exact promoted Claude Code and Pi foreground profiles when each can
complete the same positive lifecycle through surfaces available to a real model:

```text
StartRun → route → graph → research → deterministic spike → current RunView
→ bound independent audit → private verdict ingress → Allow(converged=true)
```

A typed unsupported result is a safe intermediate state, not release support. Codex remains a
conformance-tested WIP adapter at the `observational` tier and is not part of the supported release
set. Direct application dispatch and private conformance seams do not establish host support.

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

The shared MCP server supplies public tools for WIP conformance. Because Codex 0.146.0 cannot
independently observe the resolved model behind a managed `codex exec` subprocess, command/model
configuration remains insufficient identity evidence. The bounded adapter may parse a correlated
final verdict and invoke private ingress, but attribution remains unverified and convergence blocks.
The profile is `observational` and `wip_unsupported`; `foreground_only` is only a future candidate.

## Layered acceptance

`make empirica-host-adapter-check` is the deterministic layer. It was introduced red-first while
the public server and host drivers were absent; it now stays green without claiming to launch an
installed host. `make empirica-host-live-check` is the separate supported-release gate: a trusted
release operator captures retained, candidate-bound Claude and Pi parent/child JSONL, durable state,
and native version output. The verifier checks those structures and their internal correlation; it
does not provide signatures or proof against operator fabrication. Codex is deliberately excluded
while its profile is `wip_unsupported`.

Claude and Pi reached `Allow(converged=true)` in installed foreground probes and their exact
profiles were promoted. Release certification still requires fresh receipts from the exact final
candidate. Codex 0.146.0 must end in `audit.independence_unverified`; this is correct fail-closed WIP
behavior, not supported convergence.

The acceptance matrix is layered rather than duplicated in every positive trace:

- model schemas exclude every trusted action and capability reference;
- graph/research/spike/freeze pass through the real v2 bridge;
- the spike harness runs exactly once against captured bytes;
- audit is bound to current argument and exact child;
- malformed output, stale audit, wrong child, transport failure, and same/unverified
  model identity never converge;
- terminal replay cannot alter the result.
