---
number: 12
title: Finalizer delegates to the think router; methodology chosen by intent
status: accepted
date: 2026-07-17
tags:
  - architecture
  - methodology
  - integration
links:
  - target: 3
    kind: Depends on
  - target: 5
    kind: relatesto
  - target: 13
    kind: relatesto
  - target: 14
    kind: relatesto
---

# Finalizer delegates to the think router; methodology chosen by intent

## Context and Problem Statement

M5 Finalizer is the confluence stage both paths pass through (ADR-5) — the "finalize" step
in the colleague's narrative. A requirement states the workflow "should support all
methodologies from think; they depend on intent." So M5's finalize is not a single fixed
reasoning method (e.g. always invariant-analysis). What method does M5 run, and what does
it emit for downstream modules to consume?

## Decision Drivers

* Requirement: support *all* methodologist methodologies, selected by intent — not a subset.
* `/think` already routes to a methodology by matching task intent against its registry's
  `use_when` fields; reimplementing that selection would duplicate a sibling plugin.
* Downstream (M6 Scribe, M7 Handoff) must not re-reason — they should derive from one
  source-of-truth artifact (SSOT).
* Different intents produce different conclusion shapes (invariants, a chosen option, a
  decomposition), so M5's output type must be general, not always an invariant set.

## Considered Options

* **Fixed methodology** — M5 always runs one method (e.g. invariant-analysis). Rejected:
  violates "support all methodologies" and misfits non-invariant intents.
* **Inline subset** — M5 hand-rolls a few methods internally. Rejected: duplicates
  methodologist and drifts from it as the registry grows.
* **Delegate to the `/think` router** — M5 invokes methodologist's `/think`, which selects
  the methodology by intent from its registry. M5 emits a general `ResolvedIntent` =
  { the methodology's structured conclusion + its full reasoning trace }. M6 derives the
  ADR/topic docs from the trace; M7 derives the implementation brief from the conclusion.

## Decision Outcome

Chosen option: "Delegate to the `/think` router", because it is the only option that
satisfies "support all methodologies by intent" without duplicating methodologist. M5
becomes a thin stage that runs `/think` and captures its output as `ResolvedIntent`. The
trace is the SSOT: everything downstream derives from it rather than re-reasoning.
`InvariantSet` is simply the shape `ResolvedIntent` takes when `/think` selected
invariant-analysis; contradiction yields a chosen option, decomposition yields modules, etc.

This makes methodologist a required companion of the workflow plugin (ADR-3).

### Consequences

* Good, because the workflow inherits every current and future `/think` methodology for
  free — no maintenance of a parallel method set.
* Good, because the reasoning trace is a single source of truth; M6 and M7 derive from it,
  matching how this very session produced its ADRs from a `/think decomposition` trace.
* Good, because M5 stays the clean confluence for both paths (ADR-5) — unknown path feeds
  the winning approach into `/think`; known path feeds the raw request in.
* Bad, because the workflow now hard-depends on methodologist being installed; the finalize
  stage cannot run without it.
* Bad, because `ResolvedIntent` is a general (polymorphic) artifact — M6/M7 must handle
  whichever conclusion shape the selected methodology emits.

### Confirmation

Fitness function: (1) M5 invokes `/think` and stores its trace + conclusion as
`ResolvedIntent`; (2) feeding a bug-shaped intent selects invariant-analysis, an
option-choice intent selects contradiction — i.e. selection tracks intent, not a hardcode;
(3) M6's generated docs quote the `/think` trace rather than re-deriving; (4) M7's brief
contains the methodology's conclusion verbatim. Test at least two distinct intents and
assert different methodologies are selected.

## More Information

Depends on ADR-3 (methodologist required companion). Relates to ADR-5 (M5 is the
confluence). `/think` router and its registry: methodologist plugin, `skills/think`.
