# Budget, stalls, and freeze

Read this file only when configuring limits, handling a stalled run, or closing a
bounded scope.

## Budget

`configure_run` changes the proposal only. A host approval installs the exact proposed ceilings
without resetting counters; auto cannot raise them. See [governance.md](governance.md).

Empirica is bounded by configured pass, investigation-spawn, and mandatory-audit-spawn
ceilings. Passes are charged only when the current derivation changes; repeated identical
evaluation does not spend another pass. Do not invent a scope-derived formula in the skill
and do not lower a ceiling below already consumed work.

A pass is charged only according to the service's observed progress rules. Investigation
children use `max_spawns`; the canonical host-owned auditor uses the independent
`max_audit_spawns` account. Neither account borrows from the other, and purpose text never
selects audit capacity. Each spawn is reserved through the service before child execution.
Only a launch rejected before observed start refunds the child's recorded account; denied or
unsupported launches must not happen outside a reservation.

When a budget is exhausted, accept the typed non-converged terminal result. Never
remove a claim, forge evidence, or bypass audit to fit the budget.

## Stall handling

When no obligation or evidence changed:

1. stop repeating the same action;
2. reread the public obligations and next actions;
3. identify whether the residual needs data, decision, experiment, or host
   capability;
4. either perform that distinct action or allow the service to terminate with an
   honest residual.

Do not claim a wall-clock stall deadline unless the active contract exposes one.

## Freeze

Freeze is an explicit scope commitment, not convergence.

- The first accepted freeze wins and atomically commits both IDs and semantic
  identity.
- Semantic identity binds each committed claim's exact `id`, `text`, `kind`, and
  input `gating`, plus the sorted `SupportedBy` edges whose endpoints are both
  committed.
- Reordering equivalent records/edges is allowed; changing a committed record or
  committed-to-committed edge is `graph.invalid` with no write.
- Every later graph must retain every committed ID; omission fails closed as
  `graph.invalid` and leaves the selected graph unchanged.
- Claims added later are deferred rather than silently included; an edge from a
  frozen parent does not activate a deferred child.
- The committed IDs are never recomputed from filtered or pruned graph paths.
- Node or edge changes still alter the argument binding and make prior audit
  coverage stale even when frozen IDs do not change.
- Every committed claim still needs its evidence and passing audit.
- Deferred claims remain visible in the terminal handoff.
- A frozen result is never relabeled `converged:true`.

Freeze requires a current approved graph and preserves proposal consent.
Before submitting `ObserveAction(kind="freeze")`, show the user the committed and
expected deferred scope. After acceptance, do not mutate the commitment by
rewriting prose or resubmitting freeze. Pre-freeze changes require new proposal approval. Changing committed frozen meaning still
requires a fresh run; even host amendments cannot rewrite that commitment.
