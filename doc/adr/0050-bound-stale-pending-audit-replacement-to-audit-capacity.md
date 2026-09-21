---
number: 50
title: "Bound stale pending-audit replacement to audit capacity"
status: accepted
date: 2026-09-21
tags: [empirica, audit, children, retry, strict-v2]
links:
  - target: 49
    kind: Refines
  - target: 42
    kind: Amends
---

# Bound stale pending-audit replacement to audit capacity

## Context and Problem Statement

The shared audit protocol rejected every active audit before the coordinator could distinguish an
obsolete pending dossier from a current one. Graph, evidence, freeze, or file-freshness changes could
therefore strand a pending audit. Separately, successful terminal ingress returns a typed
`child.terminal` Block; the protocol treated that committed result as a reconciliation failure.
Verdict ingress checked current coverage but did not require the child's original dossier to remain
current, allowing an old pending child to be rebound to newly constructed coverage.

## Decision Drivers

* Keep investigation and audit accounts disjoint and bounded.
* Preserve zero-write exhaustion and first-terminal-wins semantics under CAS races.
* Never rewrite a launch dossier, native identity, or prior artifact to manufacture currency.
* Do not treat corrupt persisted dossiers as recoverable stale work.
* Keep one shared policy; host drivers remain mechanical translators.

## Considered Options

1. Block all active audits forever (rejected: stale pending operations cannot progress).
2. Refund or automatically grow the audit allowance (rejected: retries become unbounded).
3. Rebind the pending child to new coverage (rejected: launch provenance becomes false).
4. Atomically retire and replace only stale pending audits within remaining capacity (chosen,
   human-accepted).

## Decision Outcome

The host-owned `child_reserve(resource_class=audit)` boundary compares the durable launch dossier
with the current goal, argument, frozen/deferred digests, and ordered approved-claim evidence
coverage. The last check also detects file-freshness changes without a graph digest change.

With remaining capacity, one transaction marks an obsolete pending child `cancelled`, fingerprints
that first terminal decision, and reserves a distinct child with a fresh immutable dossier. The old
child stays spent and non-refunded. Its native ID and operation/dossier binding remain unchanged.
Logical cancellation is not an assertion that native execution stopped. Late conflicting results
fault; they cannot affect the replacement. A concurrent terminal winner is reloaded on CAS retry,
not overwritten. History remains append-only and manifest-reachable.

A current pending child and any reserved/launching audit still block replacement. Those earlier
stages retain existing host-observed rejection/orphan recovery. If audit capacity is exhausted,
reservation returns `budget.exhausted(audit_spawn)` with zero writes, even when the pending audit is
stale. The operator may explicitly configure more audit capacity or stop honestly. Defaults remain
one attempt per account; no automatic retries, borrowing, refunds, or ceiling increases are added.

Verdict admission requires the original dossier to remain current as well as the candidate verdict
matching current coverage. Retry cannot inherit a cancelled auditor's identity: independence still
comes from exact-child host observations. Persisted dossiers validate against the canonical
ArgumentView schema before projection or recovery; malformed state yields fixed-safe `run.corrupt`.
No new public tool/action, migration, or state-repair path is introduced.

The shared protocol delegates stale/current reservation policy to the coordinator. Terminal
reconciliation recognizes a single `child.terminal` Block only when its reason state and returned
child ID/state match the exact attempted transition; unrelated Blocks remain errors.

## Consequences

* Good: stale pending audits can progress with an explicit finite allowance.
* Good: replacement, budgeting, and old-terminal preservation share one CAS boundary.
* Good: all three hosts use the same policy and keep host-observed identity gates.
* Cost: spent stale attempts consume capacity; the default one-attempt budget requires explicit
  configuration before a retry.
* Cost: native work may still finish after logical cancellation; its result is no longer admissible.
* Strict rejection: malformed persisted dossiers require a fresh run, never repair.
* The architecture ceiling increases from 9,457 to 9,484 effective runtime lines, an explicit
  27-line exception explicitly approved at the human HITL checkpoint. The implementation adds shared dossier-currency
  checks, bounded cancellation, complete-dossier validation, and exact terminal acknowledgement;
  it removes the obsolete adapter precheck. Tests/docs are not counted, and unrelated deletions
  or line-compression are not used to hide this small safety-related growth.

## Confirmation

`make empirica-stale-audit-check ARGS=` exercises the real coordinator and shared protocol,
including fresh coordinator/protocol restore, negative and malformed terminal acknowledgements,
stale graph/freeze/freshness replacement, unchanged active denial, zero-write exhaustion,
explicit capacity configuration, immutable late results, CAS terminal races, append-only history,
purpose collisions, rebound-verdict rejection, identity non-inheritance, terminal acknowledgement,
and fixed-safe malformed state. Existing D7, host-neutral conformance, host adapter suites, and
`make check ARGS=` remain required. The human explicitly approved both the bounded replacement
policy and the 27-line architecture-ceiling exception after reviewing the teaching summary.
This acceptance does not authorize a commit, establish installed-host release receipts, or reopen
the already-terminal `stopped_residual` Empirica run.
