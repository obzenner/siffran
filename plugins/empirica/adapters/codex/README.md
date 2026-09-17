# Empirica adapter for Codex CLI

This is the exact `codex-cli@0.146.0` adapter over the shared `empirica/v2` service. It
translates native hooks, exposes the canonical public MCP tools, and owns one bounded managed
auditor. Claim, evidence, budget, audit-coverage, and convergence rules remain in the
host-neutral core.

## Exact profile

The profile is `codex-cli@0.146.0`, tier `foreground_only`. Codex native hooks cannot mutate a
spawned child request or observe arbitrary child output. Rather than pretending otherwise, the
Stop adapter launches a host-owned foreground `codex exec` subprocess, observes its exact final
message, and privately admits the candidate verdict. Async execution remains unsupported.
The profile remains `pending_live` until its installed-host receipt passes the release gate.

The auditor model defaults to `gpt-5.1-codex-mini` and can be pinned with
`EMPIRICA_CODEX_AUDITOR_MODEL`. Codex 0.146.0 exposes the requested process argv but no
independently observed resolved-model identity, so the auditor identity remains `unverified` and
convergence blocks. The exact profile cannot be promoted until a native resolved-model observation
is available and bound to the managed process.

## Surface

| Codex surface | Mapping | Behaviour |
|---|---|---|
| explicit `$empirica ...` | `StartRun` | Best-effort activation; injects the opaque handle and public-tool instructions. |
| MCP `empirica_observe` | `ObserveAction` | Public route, graph, research, spike request, freeze, and configuration only; concrete reservation is host-owned. |
| MCP `empirica_read` | `GetRun`, `GetArgument`, `GetContract`, `RestoreRun` | Complete typed public read surface. |
| MCP `report_convergence` | `EvaluateRun` | Public guarded decision; never trusted ingress. |
| `Stop` | resolve → evaluate → managed audit when due → re-evaluate | Blocks on any unavailable or non-converged result; permits only `Allow(converged=true)`. |
| `SessionStart:compact` | `ResolveRun` | Reconnects the durable selected run. |

The deterministic spike harness remains the sole machine approver. Audit can block but cannot
manufacture evidence. `evidence_leaf`, attribution, child events, and audit verdicts have no MCP
schema and are admitted only by adapter-private calls after host observation.

## Hook trust

Installing a Codex plugin does not automatically trust changed command hooks. Review and trust
the normalized commands in Codex `/hooks`; modified commands require renewed trust. An
untrusted or disabled Stop hook is visible but is not an enforcement boundary.

## Pinned sources

The adapter targets the tagged 0.146.0 generated hook schemas and implementation:

- <https://github.com/openai/codex/tree/rust-v0.146.0/codex-rs/hooks/schema/generated>
- <https://github.com/openai/codex/blob/rust-v0.146.0/codex-rs/hooks/src/events/stop.rs>
- <https://github.com/openai/codex/blob/rust-v0.146.0/codex-rs/hooks/src/engine/discovery.rs>

Hosted Responses API WebSearch is not necessarily routed through ordinary `PreToolUse`; no
ordering claim is inferred from that sensor gap.

## Validation

```sh
make check-codex
make empirica-host-adapter-check   # deterministic adapter conformance only
make empirica-host-live-check      # retained installed-host promotion receipts
make codex-live-check CODEX='npx -y @openai/codex@0.146.0'
```
