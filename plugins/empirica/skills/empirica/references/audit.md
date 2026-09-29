# Independent audit

Read this file only after every in-scope gating claim is approved and the host
preflight confirms a bound audit lifecycle.

## Preconditions

- The selected graph is valid and current; configuration approval is current.
- Covered evidence producer and reviewer identity satisfy the classification requirements under
  [Independence reporting](#independence-reporting).
- Every in-scope gating claim is approved from real evidence.
- Every experiment claim has a current passing spike.
- Freeze scope, if any, is already committed.
- The host can bind and inject `GetArgument`, observe one audit execution,
  and admit its output through private ingress.

If the final condition is false, audit and true convergence are unsupported.
Ordinary conversation with another model is not a substitute.

## Procedure

1. Let the host bind and inject the current typed audit argument. It binds the goal, graph shape,
   evidence, frozen/deferred scope, and reviewed claims by digest; the author does not fetch it.
2. On Claude, invoke exactly one canonical plugin-scoped auditor and let its host-owned async
   execution settle the parent turn while pending; never poll or respawn. On Pi, invoke exactly
   one canonical plugin-scoped auditor in foreground mode. Do not submit `child_reserve`:
   concrete reservation is a host protocol operation and is intentionally absent from
   `empirica_observe`. On Codex, finish the evidence-complete turn so the trusted Stop hook can
   reject the unsupported audit attempt; do not spawn an ordinary child.
3. Let the host inject the dossier and bind native execution. Do not expose or
   manufacture private correlation material.
4. The auditor independently retrieves every citation, checks spike provenance,
   searches for missing material claims, evaluates freeze honesty, and returns a
   structured pass/fail verdict.
5. The host observes child start and first terminal result. It privately records
   lifecycle, attribution, and a verdict only for the bound pending child.
6. Reread the run. Any changed graph, evidence, or scope invalidates stale audit
   coverage and requires a new bound audit.

## Pi invocation shape

Check `subagent({"action":"list"})` for the executable packaged auditor, then submit exactly
this object to the structured `subagent` tool:

```json
{"agent":"empirica.empirica-auditor","task":"Audit the host-provided dossier."}
```

The string `task` is required but is replaced by the host-owned bound dossier; it is not
an author-supplied audit argument. Only `agent` and `task` are allowed. Omit `async` (even
`false`), model/context/acceptance/tool overrides, and workflow wrappers. The host injects
the host-configured reviewer and `async=false` after admission. A bare agent-only call is invalid.
A missing/non-string task reports that a string task is required; extra fields report
that only agent and task are accepted. Stop on rejection rather than trying alternate
shapes. A stopped run stays stopped; corrected guidance does not authorize reopening it.

## Stale pending retry

A new canonical audit request can replace a stale **pending** audit only when audit capacity remains.
The host atomically cancels the obsolete child and reserves a fresh dossier; the old attempt stays
charged to `audit_spawns_used`. No capacity is borrowed or increased automatically. On
`budget.exhausted` for `audit_spawn`, propose capacity and obtain host approval (deliberative only), or accept an honest residual stop;
do not repeatedly retry unchanged state. Current pending, reserved, and launching operations are not
replaced by this path. Cancellation is logical, not proof that native execution stopped.

## Authority

Audit is a blocker, not a machine approver. It cannot turn a missing research
record or failing spike green. The deterministic harness exit code remains the
sole machine authority.

## Independence reporting

Report only what the host observed:

- `distinct` when every covered evidence producer resolves to one concrete identity class and the verdict-producing reviewer is in a different class;
- `same_model` when the reviewer and covered evidence producer have the same identity class;
- `mixed` when covered evidence has more than one producer identity class;
- `unverified` when any required producer or reviewer identity cannot be established.

Only `distinct` is eligible for convergence. `same_model` means the same identity class; `mixed`
and `unverified` fail closed. Do not promise independence from role names, prompts, provider tiers,
requested models, or unknown aliases.

## Terminal replay

The first child terminal event wins. An identical replay is inert; a conflicting
replay faults. A terminal run cannot be reopened by late child output and cannot
later become converged.

The fail-closed identity outcomes are defined under [Independence reporting](#independence-reporting).
No provider difference is required.
