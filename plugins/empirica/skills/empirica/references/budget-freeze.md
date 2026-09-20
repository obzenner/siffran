# Budget, stalls, and freeze

Read this file only when configuring limits, handling a stalled run, or closing a
bounded scope.

## Budget

Empirica is bounded by pass and spawn ceilings. The service derives the working
pass allowance from current open scope but clamps it to the configured ceiling.
Do not invent a fixed formula in the skill and do not lower a ceiling below
already consumed work.

A pass is charged only according to the service's observed progress rules. Spawn
budget is reserved through the service before child execution. Denied or
unsupported launches must not happen outside that reservation.

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

- The first accepted freeze wins.
- It commits the currently gating claim IDs.
- Claims added later are deferred rather than silently included.
- Every committed claim still needs its evidence and passing audit.
- Deferred claims remain visible in the terminal handoff.
- A frozen result is never relabeled `converged:true`.

Before submitting `ObserveAction(kind="freeze")`, show the user the committed and
expected deferred scope. After acceptance, do not mutate the commitment by
rewriting prose or resubmitting freeze.
