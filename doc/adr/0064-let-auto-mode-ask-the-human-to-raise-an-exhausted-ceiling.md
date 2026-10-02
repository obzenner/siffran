---
number: 64
title: Let auto mode ask the human to raise an exhausted ceiling
status: proposed
date: 2026-10-02
tags:
- empirica
- governance
- budgets
- auto
links:
- target: 63
  kind: amends
---

# Let auto mode ask the human to raise an exhausted ceiling

## Context and Problem Statement

ADR-63 gives `--auto` exactly one human approval episode: the author sizes the three ceilings
before investigation, a human approves them once, and the run is unattended afterwards. Auto may
lower a ceiling but never raise one (`governance.auto_ceiling`); on exhaustion it stops honestly
and the only recovery is a fresh run.

The ADR-63 qualification runs on `f83661f` (`auto-adr63-f83661f-*`) showed the cost of that rule.
The Claude author sized 16/0/2, implemented the goal correctly, and failed both audits on a
clerical error: two research citations named line 895 where the current file has line 891. With
`audit_spawns_used == max_audit_spawns`, the run could neither retry nor ask anyone. The author
behaved well: it refused to self-raise, refused to re-report an unchanged argument, and asked the
operator to confirm an honest stop. The run ended `stopped_residual` with the work done and
verified, and every artifact in the run was discarded for want of one audit retry that a human at
the keyboard would have granted in seconds.

Two host symptoms made it worse. The `report_convergence` Block said `audit.failed`, whose
recovery is `child.retry`, although no audit budget remained; `budget.exhausted` was only reachable
by attempting the impossible retry. And the Claude Stop gate blocked every turn end with "address
any findings listed under Audit, then retry" until Claude's own cap stopped it after nine rounds
(the same shape as A1-D1 in `auto-485c85e`).

ADR-63 considered "auto asks the human again when a ceiling runs out" and rejected it because auto
"would be deliberative minus its first dialog, and the modes would differ by accident". The
evidence says the modes can differ deliberately: deliberative interrupts the human for *every*
configuration change; auto interrupts only for a change the agent may not make alone, a raise.

## Decision Drivers

* A run never raises its own ceiling without a distinct principal (ADR-19, ADR-28, ADR-63). This
  must stay true.
* An auto run that has done the work should not be thrown away because the author cannot ask a
  present human for one more audit.
* Unattended auto (nobody answers) must still end honestly and promptly, never loop.
* Delegated auto (`EMPIRICA_AUTO_DELEGATION=1`) stays within its fixed envelope with no human
  episode, as ADR-63 states.
* Hosts must not spam the author with a recovery that the core knows is inadmissible.
* The core stays host-neutral; the human-wait settlement already built for ADR-63 should carry the
  new episode without new host mechanisms.

## Considered Options

* **Status quo (ADR-63).** Auto exhaustion means a fresh run. Rejected by the evidence above.
* **Hosts settle exhaustion without a loop; auto still cannot raise.** Surface
  `budget.exhausted` on `report_convergence` when the audit blocker needs a spawn the budget no
  longer has, and let the Claude Stop gate and Pi `report_convergence` settle that sole blocker
  with a notice instead of blocking. This removes the spam and the misleading `child.retry`, but
  keeps the wasted run. Adopted as the **interim** for 4.0 (see "Interim" below).
* **Auto asks the human to raise an exhausted ceiling.** A raise in interactive auto becomes a
  human approval episode; lowering stays automatic. Chosen as the target design.

## Decision Outcome

Chosen option: **in interactive auto, a raising proposal is a human episode; everything else is
unchanged.**

### Phase rule

`expected_approval_kind` becomes proposal-aware. After the first successful approval:

| mode | proposal relative to the effective approved ceilings | approval kind |
|---|---|---|
| deliberative | any | `host_ui` (unchanged) |
| interactive auto | componentwise ≤ approved | `auto`, no dialog (unchanged) |
| interactive auto | raises any ceiling | `host_ui`: one dialog episode |
| delegated auto | raises any ceiling | refused, `governance.auto_ceiling` (unchanged) |
| delegated auto | ≤ approved and within the envelope | `auto` (unchanged) |

Before the first approval nothing changes. Consumed counters remain a floor
(`governance.budget_invalid`). The author still proposes with a rationale; the human may amend
(ceilings only, locked confirmation) or reject; dismissal, timeout and rejection grant nothing.

The guarantee auto callers get is exact: **after the initial approval, a dialog opens only for a
proposal that raises a ceiling**. The rule does not require exhaustion first; an author may
propose a raise while headroom remains, and the human sees it like any other raise. The skill
tells the author to raise only when a ceiling blocks progress, but that is guidance, not a core
rule, because the core does not judge estimate quality (ADR-63).

**Amendments inside a raise episode.** The episode is reserved for a human to grant a raise. A
human amendment that leaves no ceiling above the ceilings effective at that revision (for
example, author asks audits 3, human edits back to 2) is refused as `governance.auto_ceiling`
with no installation and no automatic acceptance; the dialog shows the refusal and the human may
reject or re-amend. An amendment that still raises at least one ceiling is accepted as a normal
`host_ui` amendment. So every `host_ui` receipt after the first approval in auto binds a raising
proposal, and the restore invariant below holds by construction. The pending state for a
post-approval raise is `revision_pending`, as for every post-approval change today.

**Unanswered episodes.** A raise episode that is dismissed, times out, is cancelled or rejected
grants nothing: the previous ceilings and the consumed counters stay effective, no re-prompt is
scheduled, and the run settles nonterminally. The author's admissible recovery is the honest stop
(`report_convergence intent=stop`). This ADR does not add wall-time or inactivity termination;
ADR-63's termination scope is unchanged.

### Durable facts and restore

The core records each human episode with its approval kind and plan revision as today. The
restore invariant "at most one human episode in auto" becomes "every human episode in auto after
the first approval binds a proposal that raises at least one ceiling above the ceilings effective
at that revision". Lowering or equal proposals approved through `host_ui` after the first
approval in auto are a restore failure, as are raises approved `auto`.

### Exhaustion is named where it bites

When the audit blocker would ask for a new audit (`audit.required`, `audit.failed`, stale
coverage) and `audit_spawns_used >= max_audit_spawns`, `EvaluateRun` returns
`budget.exhausted` with `resource: audit_spawn`, not the audit reason. The audit findings stay
visible in the run view's Audit section. `budget.exhausted` already lists `budget.raise`,
`run.start_fresh` and `residual.accept`; with this ADR the auto projection keeps `budget.raise`
for interactive auto and drops it only for delegated auto.

### Hosts

The raise proposal lands in `revision_pending` with `expected_approval_kind == host_ui`, so the
existing ADR-63 settlement applies: Pi opens the dialog from the observe tool; Claude's MCP
elicitation opens it; the Claude Stop gate and Pi `report_convergence` settle the sole pending
blocker nonterminally with the contract-owned human-wait notice. The dialog shows one extra
contract-owned line naming the raise ("Raise requested after approval: audits 2 → 3").

Hosts also settle a sole `budget.exhausted` blocker on an active run nonterminally with a
contract-owned notice, instead of blocking the turn end. The notice names the exhausted resource
and the admissible recoveries. A gate never repeats a Block whose only listed recovery is one the
core would refuse.

### Interim adopted for 4.0

The exhaustion surfacing and the host settlement above are implemented now, before this ADR is
accepted, because they are correct under ADR-63 as well: they change what the author is told, not
what it may do. The phase-rule change, the restore invariant, the raise line in the dialog, and
the `budget.raise` projection for interactive auto wait for acceptance of this ADR.

### Consequences

* Good: an auto run that fails an audit on a fixable error can be rescued by a present human
  with one dialog, while an absent human leaves the run settled, not looping, with the honest
  stop as the only recovery.
* Good: the author is told the truth at exhaustion and is never asked to retry what the core
  will refuse.
* Bad: auto is no longer "one dialog, then silence". The guarantee becomes "after the initial
  approval, a dialog only for a raise". Documentation and the skill must say so plainly.
* Neutral: the restore layer gains one invariant and loses one; the manifest witness already
  covers the episode chronology.

## Validation

* Core: a raise after the first approval in interactive auto yields `host_ui`; a lowering yields
  `auto`; a raise while every resource still has headroom also yields `host_ui`; delegated auto
  still refuses; restore rejects an `auto` raise receipt and a `host_ui` non-raise receipt after
  the first approval in auto.
* Core, raise episode amendments: amendment equal to the effective ceilings → refused
  `governance.auto_ceiling`, nothing installed; amendment below the effective ceilings but above
  the consumed counters → refused likewise; still-raising amendment → `amend` then `approve`
  receipts; reject and dismiss → nothing installed; restore and exact replay of each receipt
  chain.
* Core: `audit.failed` with no audit budget becomes `budget.exhausted(audit_spawn)`; with budget
  left it stays `audit.failed`; `audit.pending` is never converted.
* Hosts: the Stop gate and Pi `report_convergence` settle a sole `budget.exhausted` blocker with
  the notice and settle the raise's `revision_pending` blocker as a human wait; mixed blockers
  still block. Timeout or dismissal of the raise dialog leaves ceilings and counters unchanged,
  schedules no re-prompt, grants nothing and settles nonterminally; an explicit stop then yields a
  non-converged terminal result.
* Native: the step that today expects `governance.auto_ceiling` after approval expects a dialog
  instead; the human approves or rejects it; an unanswered dialog settles and the author stops
  honestly.
