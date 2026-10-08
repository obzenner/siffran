---
number: 45
title: Use a strict root-connected claim-dependency DAG
status: accepted
date: 2026-09-20
tags:
- empirica
- claims
- graph
- assurance
links:
- target: 27
  kind: Amends
- target: 46
  kind: Amended by
- target: 47
  kind: Amended by
---

# Use a strict root-connected claim-dependency DAG

Supersedes ADR-0022, ADR-0024 (removed from the tree; see git history).

## Context and Problem Statement

Empirica v2 retained a flat claim list and accepted `SupportedBy` and `InContextOf` edges, but its
validator allowed self-support, duplicate edges, cycles, and detached claims. `InContextOf` was
projected but did not participate in scope, state derivation, or the argument digest. The result was
neither the GSN model selected by ADR 22 nor a strict smaller dependency graph: malformed shapes
were accepted and some stored edges were inert.

We need a graph model whose structural promises are deterministic, minimal, and enforceable before
later work makes dependency traversal adjudicative.

## Decision Drivers

* Every selected claim must belong to the argument rooted at the goal.
* An author must not admit self-support, circular support, or duplicate support.
* Stored edge vocabulary must have one live meaning rather than preserve inert GSN remnants.
* Invalid candidate input and corrupt persisted aggregates are different trust boundaries.
* The model must remain smaller than full GSN and avoid compatibility decoding.

## Considered Options

1. Restore full ADR-22 GSN nodes, legal edge pairs, and context elements (rejected as unnecessary
   complexity for the current flat v2 claim model).
2. Keep both edge types and add only cycle checks (rejected because `InContextOf` remains inert and
   detached scope remains possible).
3. Use one strict root-connected `SupportedBy` claim-dependency DAG (chosen).

## Decision Outcome

The v2 graph contains a declared root, closed claim records, and only `SupportedBy` edges directed
from a claim to a claim that supports it. Every endpoint is a known distinct claim, each directed
edge occurs once, the relation is acyclic, and every listed claim is reachable from the root.
`InContextOf` is not a v2 edge type.

A candidate graph is validated before artifact creation. Invalid candidate input returns
`graph.invalid`, performs no write, and cannot replace the selected graph. Once operational state
selects a graph artifact, that artifact is part of the persisted aggregate: absence, malformed
content, or structural invalidity returns fixed-safe `run.corrupt` under the strict-state contract.
No legacy graph is decoded or migrated.

ADR 46 makes `SupportedBy` traversal adjudicative through one scoped conjunctive v2 derivation and
removes the stranded legacy graph walkers without reviving their incompatible claim model.

## Consequences

* Good, because cycles, self-support, duplicate edges, and detached claims fail before persistence.
* Good, because every edge has one dependency meaning and every claim belongs to the rooted case.
* Good, because persisted corruption cannot leak decoded run or graph fields.
* Good, because the validator is linear in claims plus edges and needs no recursive traversal.
* Bad, because clients emitting `InContextOf` must construct supported claims instead; there is no
  compatibility path.
* Bad, because structural integrity alone does not yet make child claim state affect parent state.

## Confirmation

Host-neutral conformance submits unknown endpoints, self-loops, duplicate edges, cycles, detached
claims, and `InContextOf`, and requires sole `graph.invalid` with the prior selected argument
unchanged. Transaction tests remove or corrupt a selected graph artifact and require fixed-safe
`run.corrupt` with no semantic leakage or write. Positive fixtures retain single-node, branching,
and multi-level acyclic rooted graphs. Contract validation and vendor parity bind the public edge
enum and clauses.

## Amendment (2026-10-08): the root claim must be gating

The graph validator checked only that the root is one of the claims. A graph whose root has `gating: false` was accepted, although a claim's state is derived from its **own** evidence plus its scoped children (ADR 46), never from its children alone. Convergence is therefore unreachable from the graph action onward, and nothing said so. A non-gating root with no evidence of its own stays open: qualification Q3 (finding F-Q3-3) showed the views listing it as open with no missing code and no step to take, until `report_convergence` ended the run `stopped_frozen` (ADR-0026). A non-gating root that carries evidence has a state of its own, but any non-gating claim, the root included, makes `report_convergence` end the run `stopped_frozen` (ADR-0026), so such a run can at best end `stopped_frozen` and never converges.

The validator now refuses a graph whose root is not `gating: true`, at graph time, with a closed reason of its own, `graph.root_not_gating` (a contract reason with a recovery row; the prior selected argument is unchanged, as for `graph.invalid`). The root is the goal-level assurance claim and always gates; scope the author wants to defer belongs in non-gating claims below it. `claim-graph.md` states the rule. Existing runs carry no compatibility (ADR-0067: 5.0.0 is unreleased).

* Good, because an unreachable run is refused at the first action instead of discovered at the terminal step.
* Bad, because a goal that is only a container of deferred scope must say so in a gating root claim with its own evidence.

Confirmation: a conformance case submits a graph with a non-gating root and requires sole `graph.root_not_gating` with the prior selected argument unchanged; the author view shows the reason and its recovery row; no fixture or golden graph has a non-gating root.
