# Empirica adapter for Codex CLI

This is the exact `codex-cli@0.146.0` adapter over the shared `empirica/v2` service. It
translates native hooks, exposes the canonical public MCP tools, and owns one bounded managed
auditor. Claim, evidence, budget, audit-coverage, and convergence rules remain in the
host-neutral core.

## Work-in-progress status

**Empirica convergence is not supported on Codex CLI 0.146.0.** The packaged adapter exists for
conformance development and fail-closed experimentation only. Its exact profile is
`observational` with `promotion_status=wip_unsupported`; `foreground_only` is a candidate tier,
not an advertised capability.

Codex native hooks cannot mutate a spawned child request or independently observe the resolved
model behind a managed `codex exec` process. The adapter can observe one correlated final message,
but configured argv is not identity evidence. Auditor independence therefore remains `unverified`
and convergence blocks. Async execution is also unsupported. Codex is excluded from the supported
installed-host release receipt set until a native resolved-model observation can be bound to the
managed process and the candidate foreground probe passes.

There is no default auditor model or `EMPIRICA_CODEX_AUDITOR_MODEL` override. The managed
launcher uses the exact visible approved proposal target. Deliberative approval is explicitly
unavailable on this profile. Explicit `$empirica --auto <goal>` requires an author-proposed concrete
known-distinct reviewer and a mapped observed main model. It does not read host model configuration.
Real active slugs such as
`gpt-5.1-codex` are currently unmapped and block with `governance.author_unknown`, not a claimed
identity mismatch. The three dated OpenAI snapshots used in simulated conformance do not prove
installed Codex support. Bounded automatic acceptance is not human approval and cannot prove
the actual auditor model. Unknown observation revokes approval and fails the child closed.
See [governance](../../skills/empirica/references/governance.md) for configuration and limitations.

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
make empirica-host-integration  # expensive simulated conformance, no installed host
make native-qualification       # operator-led sanity/refusal procedure; launches nothing
make empirica-host-live-check   # supported-host receipts; Codex is excluded
```
