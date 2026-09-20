---
number: 46
title: "Make SupportedBy a scoped conjunctive prerequisite"
status: accepted
date: 2026-09-20
tags: [empirica, claims, graph, adjudication, freeze]
links:
  - target: 45
    kind: Amends
  - target: 26
    kind: Refines
  - target: 27
    kind: Amends
---

# Make SupportedBy a scoped conjunctive prerequisite

## Context and Problem Statement

ADR 45 established a strict root-connected claim DAG but deliberately deferred dependency
adjudication. Claim state still depended only on local evidence, so an evidenced parent projected
approved even when a required supporting child was unresolved. A second legacy graph model carried
incompatible confidence and terminal-state rules, while an unused legacy budget module was its only
remaining traversal consumer.

We need one v2 evaluator that gives `SupportedBy` a precise meaning without implicitly changing the
first-write-wins frozen commitment, propagating logically invalid refutations, or producing vacuous
convergence after pruning.

## Decision Drivers

* `SupportedBy` represents a required dependency, not an unspecified alternative proof.
* Every claim retains its own research, spike, human-decision, and refutation requirements.
* Frozen IDs must never expand or shrink because graph topology changes later.
* Graph changes must still invalidate audit coverage bound to the prior argument.
* Refutation is claim-specific and cannot be inferred upward from a failed support.
* Shared DAG descendants require path-sensitive, not global, pruning.
* Projection, convergence, and audit coverage must consume the same derivation.

## Considered Options

1. Treat outgoing supports as alternatives (rejected: the selected relation explicitly represents
   required dependencies; alternatives need a separate grouping/cardinality model).
2. Conjunctive support with upward refutation propagation (rejected: a failed argument is not evidence
   refuting its conclusion).
3. Conjunctive support over every graph child (rejected: later deferred nodes would silently expand a
   frozen commitment).
4. Conjunctive support on the effective scoped induced graph, with local terminal state preserved and
   path-sensitive pruning (chosen).

## Decision Outcome

Let the effective scope be the ordered `gating: true` IDs before freeze and the exact immutable
`frozen_claim_ids` afterward. For a scoped claim `p`:

```text
approved(p) = local_state(p) == approved
              AND every direct SupportedBy child in effective scope is approved
```

A deferred child is visible and audit-bound but does not become active merely because a frozen
parent points to it. Conversely, a frozen claim remains committed even if its only current graph path
passes through a deferred or discarded claim. Commitment is stored IDs, never recomputed
reachability.

A non-approved dependency turns an otherwise locally approved parent to `open`. It does not
overwrite a parent's own `blocked` or `discarded` state. Refutation never propagates upward. A
discarded node stops dependency traversal below itself on that path only; a shared descendant remains
adjudicative through another live parent. Public blocking output follows the dependency chain to the
actual unresolved supporting claim and reports that claim's existing evidence, decision, conflict,
staleness, or refutation reason.

Approved-only convergence remains unchanged. Blocked and discarded committed claims are residuals,
and no empty/pruned traversal authorizes convergence. Freshness, current audit coverage,
independence, and stopped-frozen behavior remain additional gates.

The v2 derivation replaces the legacy normalized-graph claim subsystem. The unused legacy
scope-derived budget module and its isolated tests are removed; live v2 pass/spawn guards remain.

## Consequences

* Good, because claim edges now affect state, convergence prerequisites, projections, and audit
  coverage through one evaluator.
* Good, because parent evidence cannot hide an unresolved required support.
* Good, because dependency failures name the actionable leaf rather than falsely claiming missing
  parent evidence.
* Good, because frozen commitment and audit currency remain distinct.
* Good, because shared descendants are handled without global subtree deletion.
* Bad, because locally evidenced parent claims may become open under the new required dependency
  semantics.
* Bad, because alternative/threshold support needs a future explicit graph model.

## Confirmation

Conformance covers scoped AND, independent parent evidence, no upward refutation, dependency-leaf
reasons, shared-descendant path sensitivity, discarded-root non-convergence, deferred child scope,
deferred intermediary commitment stability, and audit invalidation after graph expansion. Existing
freshness, audit, frozen, budget, independence, and terminal suites remain green. Architecture checks
confirm removal of the dead legacy claim and budget modules without removal of live v2 budget guards.
