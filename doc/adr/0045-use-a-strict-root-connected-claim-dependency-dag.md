---
number: 45
title: "Use a strict root-connected claim-dependency DAG"
status: accepted
date: 2026-09-20
tags: [empirica, claims, graph, assurance]
links:
  - target: 22
    kind: Supersedes
  - target: 27
    kind: Amends
---

# Use a strict root-connected claim-dependency DAG

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

This decision establishes structural integrity only. A follow-on decision and seam will make
`SupportedBy` traversal determine parent/dependency adjudication and consolidate stranded graph
walkers. Until then existing claim `gating` behavior remains unchanged.

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
