# The v3 receipt

After one complete converged trace, `make empirica-host-receipt` captures the operator-attested transcript, state, child
session, host version, command and output paths. The arguments are listed by `make help`: `HOST`, `TRANSCRIPT`, `STATE`,
`CHILD_SESSION`, `VERSION_OUTPUT`, `COMMAND`, `OUTPUT` and `ARTIFACTS_REPO` (the Git repository holding the run's artifacts;
its common directory must be the one the run recorded). Then validate with `make empirica-host-live-check`.

A v3 receipt carries `audit_retrieval` (equal to `invocation.audit_retrieval` in the retained state) and
`evidence_receipts`: every human presentation and confirmation the run's committed manifest chain references, re-derived
from the store. A non-converged run gets no receipt; preserve the partial evidence instead. `python3 scripts/receipt_args.py`
checks that the named paths exist and are outside the checkout without running anything.

Retain the dialogs and logs too: the receipt proves retained state, identity and artifact binding, not each UI checkpoint.
Context economy: extract the ordered Empirica tool results to a newline-delimited JSON slice and run
`make empirica-context-economy-report FILE=<slice>`.
