---
number: 49
title: "Isolate investigation and mandatory-audit spawn budgets"
status: accepted
date: 2026-09-21
tags: [empirica, budget, audit, children, strict-v2]
links:
  - target: 34
    kind: Amends
  - target: 41
    kind: Amends
  - target: 48
    kind: Refines
---

# Isolate investigation and mandatory-audit spawn budgets

## Context and Problem Statement

Strict v2 charged every child reservation to one `max_spawns` / `spawns_used` account. Ordinary
investigation could therefore exhaust the only spawn before the mandatory independent audit, while
audit retries could consume capacity intended for investigation. Free-text `purpose` could not
safely classify children: ordinary host prompts/tasks are model-controlled and may equal `"audit"`.
Persisted counters were bounded but were not reconciled to durable child records.

## Decision Drivers

* Investigation must not consume protected mandatory-audit capacity.
* Audit must not borrow investigation capacity.
* The author/model must not select the privileged audit resource class.
* Refund and CAS behavior must charge exactly one immutable account.
* Persisted accounting mismatches must fail fixed-safe as `run.corrupt`.
* Strict v2 adds no migration, defaults, or repair for the new state shape.

## Considered Options

1. Keep one shared spawn account (rejected: investigation can prevent mandatory audit).
2. Infer audit capacity from `purpose == "audit"` (rejected: model-controlled text can forge it).
3. Reserve one implicit slot in the shared maximum (rejected: ambiguous accounting and refunds).
4. Persist two explicit accounts and a host-owned immutable child resource class (chosen).

## Decision Outcome

Operational state has two independent accounts:

* `max_spawns` / `spawns_used` for `resource_class: investigation`;
* `max_audit_spawns` / `audit_spawns_used` for `resource_class: audit`.

Both maxima may be zero; defaults are one investigation spawn and one audit spawn. No account may
borrow from the other. Public author tools do not expose `child_reserve`. Ordinary Claude, Codex,
and Pi orchestration always emits `investigation`; only the trusted canonical audit protocol emits
`audit`. An audit-class request must have canonical purpose `audit`, but investigation-class purpose
text may be any non-empty string, including `audit`.

Every child persists its immutable `resource_class`, and RunView child summaries expose it so host
lifecycle correlation never infers authority from purpose. Reservation increments that class's
account; only `launch_rejected` refunds it, once, from the same recorded account. Other terminal
remain charged. Used counters must equal the number of non-refunded durable children in their class,
and at most one audit-class child may be active. Missing fields, malformed classes, audit bindings
on investigation children, absent audit bindings on audit children, counter mismatches, and multiple
active audits are current-state corruption. Candidate exhaustion or attempts to lower a maximum
below usage are typed `budget.exhausted` blocks with `spawn` or `audit_spawn` and zero writes.

## Consequences

* Good, because ordinary work cannot strand a run before mandatory audit.
* Good, because audit retry policy cannot silently consume investigation capacity.
* Good, because free-text purpose is no longer authorization.
* Good, because refunds and persisted counters are mechanically attributable to durable records.
* Bad, because strict pre-split persisted states require a fresh run.
* Bad, because every host start surface has one additional explicit audit-budget setting.

## Confirmation

Strict state tests cover required fields, both class/account reconciliations, spoofed purpose,
audit-binding/class consistency, and multiple active audits. Transaction and conformance tests cover
independent exhaustion, no borrowing, lower-limit zero writes, class-specific reservation, and
class-specific one-time refund. Claude, Codex, and Pi adapter suites verify ordinary launches remain
investigation-class and the canonical audit protocol requests audit-class capacity. Contract,
vendor, architecture, and full repository checks are release gates.
