---
number: 56
title: "Align bootstrap admission and agent guidance"
status: accepted
date: 2026-09-24
tags: [empirica, governance, contracts, bootstrap]
links:
  - target: 53
    kind: Refines
  - target: 55
    kind: Refines
---

# Align bootstrap admission and agent guidance

## Context and Problem Statement

Native qualification exposed a valid configure_run request made before a claim graph existed.
The service persisted configuration, the host reserved presentation capacity, and the renderer
then failed on the absent scope. The skill required a graph, but current obligations omitted
that prerequisite and graph.missing suggested inspection instead of graph construction.
Fixing only the renderer would preserve the disagreement between admission and agent guidance.

## Decision Outcome

A graphless configure_run is rejected with graph.missing before proposal mutation. New private
presentation decisions require a graph before reserving capacity. Exact historical receipt
replay remains Inert: older valid runs can contain graphless presentation receipts, and new
admission rules do not erase that history. No old operational state is migrated or reopened.

Extend the existing public contract with a finite bootstrap catalogue, not another workflow
registry. It declares operation-to-predicate bindings, obligation wording, action examples and
common tool guidance. Application assembly supplies immutable bindings to pure core functions.
The same primitive facts and prerequisite evaluator serve admission and review readiness;
current obligations and suggested actions derive from the same snapshot. Predicate identifiers
bind to explicit code, never executable expressions. Missing operation metadata fails explicitly.

The initial graph proposes claims and unknowns from supplied context; it needs no investigation
or completed evidence. Graph may precede route, while investigation requires routing and exact
current approval. Terminal runs advertise no preparation actions. Requesting host mediation
is distinct from being ready to display a form: host context can be refreshed after a graph
exists, and unusable refreshed context or exhausted presentation capacity gets appropriate
repair or residual-stop guidance rather than an unconditional reconfigure recommendation.

Graphless convergence requests report the missing graph before approval admission. Approval
failure reasons retain the existing governance policy's distinction between initial approval,
material revision and exhausted revision capacity. They are not flattened to a static label:
Claude's nonterminal human-wait mapping depends on the exact reason. ADR55's host-owned edited
proposal confirmation, consent binding, cancellation and continued work denial are unchanged.

Generate common tool descriptions, bootstrap action descriptions/examples and review recovery
metadata through the existing public-tools artifact. Both Python and Pi consume that artifact;
a missing Pi recovery entry fails explicitly rather than producing an incomplete reason.
Examples are illustrative proposals, never evidence or consent. The three public tools and
private authority boundary remain unchanged.

## Consequences

- Removes independent common tool prose, the old bootstrap obligation branch, the binary
  governance next-action rule and Pi's handwritten recovery map. A duplicate projection copy
  implementation and a redundant routing guard also disappear.
- Keeps native rendering, private consent handling and independently asserted safety tests
  separate. It does not generate host UI choreography from the catalogue.
- The user explicitly authorized a ceiling increase from10897 to10959 counted runtime lines
  (+62). Subsequent removal of the redundant routing guard leaves10957 lines across82files;
  the ceiling remains10959. No code is relocated or explanation stripped merely to fit it.
- Public contract2.1.0 and plugin3.2.0 remain unreleased. Schema/digest fixtures and vendored
  artifacts are synchronized; this change adds no persisted workflow stage.
- Broader skill/reference prose subtraction and native agent effectiveness remain separate
  follow-up work. This finite catalogue is not an exhaustive planner for research or audit.

## Confirmation

The fast core gate includes graphless configuration denial, graphless convergence precedence,
example postconditions, terminal readiness, and material-revision denial alongside existing
consent/replay checks. Focused Make selectors cover governance service and transaction tests.
Pi UI tests require complete recovery fields for each context/capacity error. Contract checks
validate finite bindings and examples; generated artifacts are checked against their source.

Run make check and the full governance service/host suites for the candidate. Native cold-start
discoverability and edited-value confirmation require separately authorized operator-present
qualification. Source review and deterministic test success do not establish native qualification.
