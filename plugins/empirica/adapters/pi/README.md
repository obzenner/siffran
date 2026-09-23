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

## Governed initialization

3.2.0 adds required exact proposal consent before investigation. Deliberative mode uses
`ctx.hasUI` and documented select/input/confirm dialogs; cancel/no UI fails closed. Inventory
comes from the configured authenticated model registry and is revalidated before admission.
`configure_run` opens the dialog, and amendments need a second proposal review. Explicit
`--auto` is bounded automatic acceptance, not a human decision. See
[governance](../../skills/empirica/references/governance.md) for limits, singleton exceptions,
and fresh-run-only compatibility. These new UI flows have simulated-host integration coverage;
the historical profile receipt does not certify native human approval for 3.2.0.

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

The canonical auditor has no plugin model pin. The host injects the exact approved visible
`provider_id/model_id` and rejects preflight substitution, shadowed agent definitions, and
author-supplied overrides. `EMPIRICA_PI_AUDITOR_MODEL` no longer selects the auditor.
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
