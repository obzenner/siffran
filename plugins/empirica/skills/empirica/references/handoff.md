# Finalization and handoff

Read this file only after a guarded terminal decision or an explicit unsupported
capability result.

## Product artifact

The requested design, implementation, tests, or accepted decision is the product.
Empirica's graph, manifests, evidence, audit bindings, and operational state are
internal assurance artifacts. Do not copy them into the product tree merely to
make the run visible.

Use Methodologist at the confluence only when evidence leaves a genuine design or
formal-reasoning decision. Do not invoke it as ceremony after the answer is fixed.
Accepted architecture decisions belong in the repository's normal ADR/MADR form.

## Terminal report

Report:

```text
Empirica result: <converged | stopped_residual | stopped_frozen |
                  stopped_budget | unsupported | faulted>
Goal: <resolved run goal>
Guarded decision: <Allow/Block/Inert/Fault and relevant reason>
Evidence: <commands, sources, and changed files that support the product result>
Audit: <pass/fail/unavailable; observed independence classification>
Residuals: <typed unresolved obligations or None>
Deferred scope: <claim IDs or None>
```

Only say `converged` after guarded `Allow(converged=true)`. An
`Allow(converged=false)` is successful permission to report non-convergence, not a
weaker convergence claim.

For an unsupported host, state the exact missing capability, observed tool/event
surface, and whether an active handle was created but cannot progress.

## Repository hygiene

Commit only intended product files. Do not commit machine-local operational state,
private ingress material, capability references, or transient audit output. Keep
knowledge in `refs/empirica/*` and operational state under
`~/.empirica-plugin/`.
