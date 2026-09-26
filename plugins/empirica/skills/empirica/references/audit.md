# Independent audit

Read this file only after every in-scope gating claim is approved and the host
preflight confirms a bound audit lifecycle.

## Preconditions

- The selected graph is valid, current, and covered by current governance approval.
- The selected reviewer is known, distinct from the host-observed main model, and covered by current governance approval.
- Every in-scope gating claim is approved from real evidence.
- Every experiment claim has a current passing spike.
- Freeze scope, if any, is already committed.
- The host can obtain `GetArgument`, bind one audit execution, observe its lifecycle,
  and admit its output through private ingress.

If the final condition is false, audit and true convergence are unsupported.
Ordinary conversation with another model is not a substitute.

## Procedure

1. Request the current typed audit argument. It binds the goal, graph shape,
   evidence, frozen/deferred scope, and reviewed claims by digest.
2. On Claude, invoke exactly one canonical plugin-scoped auditor and let its host-owned async
   execution settle the parent turn while pending; never poll or respawn. On Pi, invoke exactly
   one canonical plugin-scoped auditor in foreground mode. Do not submit `child_reserve`:
   concrete reservation is a host protocol operation and is intentionally absent from
   `empirica_observe`. On Codex, finish the evidence-complete turn so the trusted Stop hook can
   reserve and run its managed foreground auditor; do not spawn an ordinary child.
3. Let the host inject the dossier and bind native execution. Do not expose or
   manufacture private correlation material.
4. The auditor independently retrieves every citation, checks spike provenance,
   searches for missing material claims, evaluates freeze honesty, and returns a
   structured pass/fail verdict.
5. The host observes child start and first terminal result. It privately records
   lifecycle, attribution, and a verdict only for the bound pending child.
6. Reread the run. Any changed graph, evidence, or scope invalidates stale audit
   coverage and requires a new bound audit.

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

The author never submits `child_event`, `attribution`, or `audit_verdict`. A public
request resembling one of those payloads must fail closed.

## Independence reporting

Report only what the host observed:

- `decorrelated` when concrete author and auditor identities are distinct;
- `same_model` when they are the same;
- `unverified` when either identity cannot be established.

Do not promise independence from role names, prompts, provider tiers, or requested
models alone.

## Terminal replay

The first child terminal event wins. An identical replay is inert; a conflicting
replay faults. A terminal run cannot be reopened by late child output and cannot
later become converged.

Observed `same_model` always blocks. Unknown aliases and selected/observed substitution never pass.
No provider difference is required; the host-observed selected reviewer must still match the actual reviewer.
