---
number: 75
title: Generate conformance tests from the contracts
status: proposed
date: 2026-11-23
tags:
- testing
- contracts
- empirica
links:
- target: 30
  kind: Amends
---

# Generate conformance tests from the contracts

Amends ADR-0030 (host-neutral contracts between plugin cores and adapters). Status *proposed*: accepted with the node
that introduces the harness (E2c of the Empirica 7.2 stack); the project-wide migration is planned in
[the 7.2 delivery plan](../design/plans/empirica-7.2-environment-delivery.md) §6.

## Context and Problem Statement

A closed shape such as the environment proposal was written three times: in the JSON Schema, in the core validator
and in tests that restate its bounds and patterns. A parity guard in `scripts/validate_contracts.py` compared the
first two, but only the members someone taught it. In the E2a review the schema admitted values the core refused
(`not@digest` as an image, control characters in text, a trailing newline in a reason), and the guard compared
numbers but not patterns. The tests passed because they restated the same values. The observer read model already
avoids this: its coverage is generated from its schemas (`scripts/ui_view_strategies.py`, T1).

## Decision Outcome

The contract is the single source of a shape. Tests of a shape are generated from it, not written.

1. A schema definition that the core validates names its validator: `"x-core-validator": "<module>.<function>"`.
2. Core bounds for those definitions are generated from the contract into a module (drift fails `contract-check`),
   so no constant is written twice.
3. One harness, for every annotated definition:
   - draws valid documents from the schema's keywords; the core must accept each;
   - derives invalid documents from the same keywords (bound plus one, a character outside the pattern, a wrong type,
     a missing or extra key, a null row, an unsized iterable); the schema and the core must both refuse each.
   Disagreement in either direction fails.
4. A rule the schema cannot express is listed under `"x-core-only"` with the name of the one test that pins it; the
   harness refuses an entry without one.
5. A static guard fails when a core-validated definition lacks its annotation, a named validator or test does not
   exist, or a schema keyword is unknown to the strategy builder.

Hand-written tests remain for behaviour that is not a shape: transitions, transactions, races, recovery.

### Consequences

- Good: a new member is tested by declaring it; schema/core disagreement is found by construction.
- Good: the mirror half of `validate_contracts.py` and per-value tests are deleted, not maintained.
- Bad: one more generator to keep deterministic (fixed seed, bounded, no example database, as T1 already requires).
- Neutral: until the migration (§6 of the plan) completes, older tests still restate values; the T0 inventory
  counts them and the T6 guard makes the count zero.
