---
number: 55
title: "Keep amendment confirmation host-owned and human waits nonterminal"
status: accepted
date: 2026-09-24
tags: [empirica, governance, claude, approval]
links:
  - target: 54
    kind: Refines
  - target: 51
    kind: Refines
---

# Keep amendment confirmation host-owned and human waits nonterminal

## Context and Problem Statement

Native qualification of the unreleased 3.2.0 candidate exposed a broken confirmation path.
The operator submitted valid edited values with Approve. The host stored an amendment, as
required by exact-proposal consent, but returned to the author before final confirmation.
The author interpreted the changed values as corruption and sent the original configuration
again. That public action overwrote the amendment before the next form opened. The UI and
storage worked; the human's values never became effective. Repeated Stop vetoes then prevented
the author from pausing to ask the operator for help.

## Decision Outcome

A Claude configuration-only amendment opens exactly one additional, host-owned **FINAL
CONFIRMATION** inside the same mediation call. Configuration is read-only; the human can
approve the exact displayed values, reject, cancel, or supply a plain-language request for
further work. Inventory confirmation and, when applicable, positive same-model consent remain
required. No author call intervenes between the edit and its confirmation. Missing inventory
confirmation receives an actionable explanation rather than only a generic UI error.

The host checks revision AND digest again after refreshing context, reserves a fresh receipt,
and retains the normal final CAS check. A concurrent change (including edit-and-revert), stale
reply, unsupported field, timeout or cancellation cannot approve. Each call has at most two
presentations; both count against existing durable limits. Plain-language change requests still
return to the author for scope revision; they are not automatically approved. Numeric edits
need no prose rationale, and the author must preserve current values when requesting review.

Claude Stop may settle a specific **human approval wait** nonterminally, like its existing
managed-audit wait: the correlated service result must remain Block, with exactly the matching
approval-required reason, an active run, deliberative mode and MCP governance ingress. Pending,
revision-pending and rejected proposals can wait for human direction. The native hook emits a
visible not-converged notice and does not change run status, consent, budgets or evidence.
Auto, approved, malformed, mixed-reason and fault results are not human-wait exceptions.
There is no unconditional `stop_hook_active` bypass. Investigation, child admission and explicit
`report_convergence` still go through unchanged service gates. A settled assistant turn is not
a convergence receipt.

## Consequences

* Human changes no longer depend on the author inferring the correct second configure call.
* The two-stage policy remains explicit; this does not approve edits on their first submission.
* This changes Claude choreography only. Pi retains its existing re-review behavior.
* Effective runtime becomes 10897 lines across82files, +26 over10871. Together with ADR54 this
  is +250 over10647, within the existing allowance; the guard records the measured total.
* No public schema, private state format or consent invariant changes. Plugin3.2.0 and
  contract2.1.0 remain unreleased; no migration or global installation is performed.

## Confirmation

`make check-claude` includes focused real-service confirmation and human-wait regressions plus
mapper fail-closed cases. `make empirica-governance-host-check` covers the complete existing
host suite, including real stdio correlation through a Git-backed service, now with both
presentations under one tools/call. Regressions cover unchanged final confirmation installing
human values, cancellation, stale replies, edit-and-revert, final-form field injection, required
inventory confirmation, rejection, and continued denial of work/convergence after turn settlement.
The native qualification skill separately exercises the actual operator-visible sequence.
Unit/service execution is not a native qualification pass.

Claude mechanics references: [Stop hook behavior](https://code.claude.com/docs/en/hooks-guide)
and [native hook output fields](https://code.claude.com/docs/en/agent-sdk/typescript).
