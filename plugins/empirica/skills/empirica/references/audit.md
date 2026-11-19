# Independent audit

Read this file only after every in-scope gating claim is approved and the host
preflight confirms a bound audit lifecycle. If the host cannot bind and inject
`GetArgument`, observe one audit execution, and admit its output through private
ingress, audit and true convergence are unsupported; ordinary conversation with
another model is not a substitute.

An audit may block but cannot manufacture deterministic machine evidence, so it never replaces a needed spike.

## Procedure

1. Let the host bind and inject the current typed audit argument; the author does not supply it.
   Read it first with `GetArgument`: it is exactly what the auditor will judge, so a claim whose
   cited sources do not carry it will fail there.
2. On Claude, invoke exactly one canonical plugin-scoped auditor and let its host-owned async
   execution settle the parent turn while pending; never poll or respawn. On Pi, invoke exactly
   one canonical plugin-scoped auditor in foreground mode, as the view's launch line prints it:

   ```json
   {"agent":"empirica.empirica-auditor","task":"Audit the host-provided dossier."}
   ```

   Audit after the human has confirmed what needs one: the core refuses the launch while a
   human is about to be asked ([evidence.md](evidence.md#what-still-needs-confirming)). What
   the auditor checks and returns is defined in [the auditor](../../../agents/empirica-auditor.md).
3. Reread the run. `GetRun` shows `obligation.audit` once every scoped gating claim is approved,
   including its current blocker and honest next actions. A failed verdict lists any findings under
   the `Audit:` line (`GetArgument` keeps the latest settled verdict's findings unless a re-audit is
   pending); address them before requesting another audit, because hosts may withhold the child's
   raw output. Retry at most within the approved audit ceiling (normally one retry); when the audit
   fails again or `budget.exhausted` appears, call `report_convergence` with `intent=stop`
   instead of looping. A stale pass reads `failed` with no findings: re-audit the current evidence. Any changed graph,
   evidence, or scope invalidates stale audit coverage and requires a new bound audit. Counts and reason strings
   are telemetry, not authority.

## Independence reporting

Report only what the host observed:

- `distinct` when every covered evidence producer resolves to one concrete identity class and the verdict-producing reviewer is in a different class;
- `same_model` when the reviewer and covered evidence producer have the same identity class;
- `mixed` when covered evidence has more than one producer identity class;
- `unverified` when any required producer or reviewer identity cannot be established;
- `human_owned` when every reviewed claim is a human's decision with no evidence behind it: nobody produced it, so there is no producer to be independent of. The audit over the argument is still required.

`distinct` and `human_owned` are eligible for convergence. `same_model` means the same identity class; spawn a
new distinct auditor or accept the residual. `unverified` has the same two honest actions. `mixed`
covered producers cannot be repaired by another auditor or by re-recording research, because all
current research remains active. Only a spike head is superseded: if the sole differing producer is a
claim's latest spike, re-running that spike under the research producer clears `mixed`. Otherwise
accept the residual. All three outcomes fail closed. Do not promise
independence from role names, prompts, provider tiers, requested models, or unknown aliases.
