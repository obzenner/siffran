# Empirica Pi adapter (v2)

This is the v2-only Pi translation shell. It exposes the same public author/read/report
surface as the Claude and Codex adapters and owns no convergence policy.

## Capability profile

The promoted profile is qualified on Pi `0.84.1` and admits compatible Pi releases
`>=0.84.1,<0.85.0`; receipts still record the exact observed Pi version. The controlled
`pi-subagents` dependency remains pinned at `0.50.0`. The profile tier is `foreground_only`, with
`promotion_status=promoted` after an installed-host foreground trace reached guarded
`Allow(converged=true)`. `pi-subagents` must provide its structured `subagent` tool.
Asynchronous audit execution is not supported and is never silently downgraded.

## Surface

| Pi surface | v2 operation | Behaviour |
|---|---|---|
| `/empirica <goal>` | `StartRun` | Starts a durable run, persists the opaque handle, and injects public-tool guidance. |
| `empirica_observe` | `ObserveAction` | Accepts only canonical public author kinds. Trusted kinds are rejected locally and by schema. |
| `empirica_read` | `GetRun`, `GetArgument`, `GetContract`, `RestoreRun` | Returns the complete typed result; resolves a session handle when needed. |
| `report_convergence` | `EvaluateRun(report_convergence | stop)` | Fails closed unless the guarded response is `Allow`; `intent: stop` records an honest non-converged terminal. |
| `tool_call(subagent)` | `child_reserve` + private lifecycle | Binds the canonical auditor, forces foreground execution, injects the dossier, and records launching/pending facts. |
| `tool_result(subagent)` | private `audit_identity` + `audit_verdict` | Correlates by `toolCallId`, redacts before the first await, binds the verdict to the final native assistant record in the host-generated child session, and admits only one exact fenced verdict. |
| compaction | `RestoreRun` | Carries the opaque handle and restores the selected run. |

The packaged auditor pins `amazon-bedrock/eu.anthropic.claude-opus-4-8`, qualified on stock Pi
`0.84.1` with a provider-qualified registry identity. Deployments may override it with
`EMPIRICA_PI_AUDITOR_MODEL` when their registry uses a concrete private provider alias; the adapter
resolves that launch contract and rejects shadowed agent definitions and author-supplied overrides.
The adapter never trusts `details.results[].model`, which is requested launch configuration.
Instead it reads the exact result row's host-generated `sessionFile` and accepts identity only when
the final native assistant record carries concrete provider/model fields and its sole verdict equals
the admitted tool-result verdict. Missing, malformed, oversized, changed, or ambiguous sessions
remain unverified and block convergence.

The adapter explicitly disables generic writer acceptance gates on this host-owned read-only audit
call. Empirica's bound verdict contract remains authoritative; author-supplied acceptance, model,
context, or tool overrides are rejected before that runtime-owned mutation.

The adapter-private Python subprocess exposes no Pi tool. It is the imperative ingress shell for
host-observed attribution, child events, and audit verdicts. Public tools cannot express these
payloads.

## Hard gate

With a non-null run handle, `report_convergence` permits only a centrally guarded `Allow`.
`Block`, `Inert`, every `Fault`, malformed responses, and transport failures deny. The central
guard enforces `converged=true` iff `run.status=converged`.

Pi has no native completion veto; the model must call `report_convergence` before making a
convergence claim. A turn can otherwise finish without a terminal decision.

## Validation

Run `make check-pi` for deterministic adapter coverage. Profile promotion additionally requires
`make empirica-host-live-check` with a retained installed-Pi receipt.
