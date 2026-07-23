---
number: 5
title: Model the workflow as one pipeline with an optional empirical prefix
status: accepted
date: 2026-07-17
tags:
  - architecture
  - control-flow
links:
  - target: 6
    kind: Refined by
  - target: 7
    kind: Refined by
  - target: 3
    kind: relatesto
  - target: 12
    kind: relatesto
---

# Model the workflow as one pipeline with an optional empirical prefix

## Context and Problem Statement

The narrative reads as two separate paths: an "unknown" path (staff → spike → docs →
implement) and a "known" path (resolve invariants → implement). Both paths contain a
`/think` finalize step. Are these two pipelines, or one?

## Decision Drivers

* `/think` appears in both paths — a duplicated step hints at a shared module.
* Two pipelines double the surface to build, test, and maintain.
* The router's output (known vs unknown) should select behaviour without forking the flow.

## Considered Options

* **Two pipelines** — a known pipeline and an unknown pipeline, dispatched by the router.
* **One pipeline with an optional empirical prefix** — `classify → [empirical race over
  unknowns]? → finalize → handoff`, where the race runs only when the router reports
  unresolved unknowns.

## Decision Outcome

Chosen option: "One pipeline with an optional empirical prefix", because Phase 4 showed
M5 Finalizer is the confluence both paths pass through — which is exactly why `/think`
appears twice in the narrative (same module, different input). The "known" path is the
degenerate case where the empirical prefix executes zero iterations.

### Consequences

* Good, because there is one flow to build and test; the known path is `prefix = ∅`.
* Good, because it explains the narrative's structure rather than transcribing it.
* Good, because M6 Scribe (ADR emission) attaches only to the prefix — no race, no
  rejected alternatives, so no ADR worth writing on the known path.
* Bad, because "optional prefix" hides a real branch; the router's classification must be
  trustworthy or a genuine unknown skips the spike (mitigated by ADR-7's default-to-unknown).

### Confirmation

Fitness function: a known-territory request and an unknown-territory request must both
traverse the same finalize + handoff modules; only the presence of ledger iterations
differs. Test both and assert the shared modules run identically.

## More Information

Relates to ADR-6 (what the prefix contains) and ADR-7 (the ledger that makes the prefix a
loop rather than a single pass).
