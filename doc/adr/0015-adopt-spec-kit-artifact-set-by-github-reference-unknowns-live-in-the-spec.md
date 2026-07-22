---
number: 15
title: Adopt spec-kit artifact set by GitHub reference; unknowns live in the spec
status: accepted
date: 2026-07-22
tags:
  - architecture
  - artifacts
  - standards
links:
  - target: 7
    kind: Refines
  - target: 13
    kind: relatesto
  - target: 14
    kind: relatesto
  - target: 3
    kind: relatesto
  - target: 16
    kind: Depended on by
---

# Adopt spec-kit artifact set by GitHub reference; unknowns live in the spec

## Context and Problem Statement

The workflow's data model (which artifacts it produces, how state and unknowns are
represented) was never agreed. Rather than invent bespoke schemas (`Ledger`, `SpikeResult`,
`RaceResult`), we checked what the field has already standardized. A research pass over
current standards and practitioners (2026-07-22) found strong convergence on an artifact set
— and confirmed there is no standard for a structured "unknowns state." What artifact model
do we adopt, and how do we reference it without carrying stale copies?

## Decision Drivers

* Standards over invention: the artifact set is already defined by a live, maintained
  upstream — inventing our own is reinvention.
* SSOT: point at the source of truth, do not fork it; a vendored copy goes stale.
* No cross-marketplace runtime dependency (consistent with ADR-3): reference, don't require.
* The one genuinely novel piece (confidence-scored convergence over unknowns) has no
  standard and must be kept.

## Considered Options

* **Invent our own schemas** — bespoke `Ledger`/`SpikeResult`/`RaceResult` JSON. Rejected:
  reinvents a solved problem; the research found a live standard.
* **Vendor spec-kit templates into this repo** — copy the template files in. Rejected:
  stale copies drift from the maintained upstream (the exact failure of the dead
  "loop-kit" derivative we already rejected).
* **Depend on spec-kit at runtime** — require its `/speckit.*` commands. Rejected: hard
  external dependency on a separate toolchain (same objection as inkrot in ADR-3).
* **Adopt spec-kit's artifact set by GitHub reference** — the workflow reads the current
  templates from the upstream repo when it needs them; we store no copy. Unknowns are
  represented as open items *in the living spec*, plus our confidence/θ layer. Tests are
  the machine-checkable spec (the deterministic gate, ADR-13).

## Decision Outcome

Chosen option: "Adopt spec-kit's artifact set by GitHub reference." The committable artifact
backbone is the spec-kit set — `spec.md`, `research.md`, `data-model.md`, `contracts/`,
`plan.md`, `tasks.md` — plus MADR ADRs (this repo already uses them; they map to arc42 §9).
The agent reads the current templates from `github.com/github/spec-kit` at use time rather
than from a vendored copy, so we never carry a stale template.

Consequences for the data model:
1. **Drop the bespoke `Ledger` schema.** Unknowns live as open items in the living spec
   (practitioner convergence: Osmani folds decisions/unknowns back into `spec.md` as the
   source of truth). Our addition on top is the confidence score + θ threshold + specialize-
   only derivation (ADR-7, ADR-9) — the one piece no standard provides.
2. **`SpikeResult`, `RaceResult`, `/think` traces are transient scratch** (ADR-14), not
   committable artifacts — the disposable layer all practitioners agree is thrown away.
   They need no formal published schema; they are internal, in-memory/`.claude/` only.
3. **`data-model.md` is a per-feature artifact** produced from spec-kit's template, feeding
   `contracts/`. It is NOT a single global schema we hand-author once.
4. **Tests/contracts/invariants are the machine-checkable spec** (Hollman "over-test
   everything"; Willison test-suite-as-SoT), consistent with the deterministic gate (ADR-13).

### Consequences

* Good, because we adopt a maintained, ~123k-star standard shipping actively (spec-kit
  v0.13.4, 2026-07-22) instead of inventing and maintaining our own schemas.
* Good, because referencing the upstream avoids stale copies — the SSOT failure that killed
  loop-kit as a usable source.
* Good, because it isolates our genuine contribution (confidence-scored convergence) from
  the commodity artifact plumbing.
* Bad, because reading templates from GitHub at use time needs network access and pins us to
  the upstream's current shape — if spec-kit makes a breaking change, our workflow must
  adapt (mitigated by referencing a pinned release tag, not `main`).
* Bad, because "unknowns live in the spec" is less structured than a dedicated store —
  querying/scoring them requires parsing the living spec, which our confidence layer must
  define precisely (deferred to the build).

### Confirmation

Fitness function: (1) the repo stores NO copy of spec-kit templates — a grep for vendored
spec/plan/tasks templates returns nothing; (2) the workflow fetches templates from a pinned
`github.com/github/spec-kit` release at use time; (3) committable output matches the
spec-kit file set + MADR ADRs; (4) `SpikeResult`/`RaceResult`/traces never appear as
committed files (ADR-14 fitness function already covers this); (5) unknowns are represented
in `spec.md` with an attached confidence score, and convergence = all unknowns ≥ θ.

## More Information

Grounded in convergent sources (2026-07-22 research): GitHub **spec-kit** (v0.13.4, live
upstream — the maintained standard the dead "loop-kit" was derived from) defines
spec/research/data-model/contracts/plan/tasks; **MADR 4.0** for decisions; **arc42 §9** as
their home; **C4** for diagrams. Practitioners: **Osmani** (spec.md as source of truth,
Jan 19 2026; decision-log-on-PR, Jun 15 2026); **Willison** (test suite as SoT, regenerate
code by fixing the generator, Jul 8 2026); **Hollman** (over-test everything;
comments/contracts/invariants as durable artifacts; fence disposable code — ACCU/CppCon
2025). Honest gap: no source (Karpathy included) prescribes a structured unknowns-state
schema — that layer is ours. Supersedes the implied bespoke data model; relates to ADR-7
(ledger behavior, now spec-hosted), ADR-13 (tests as gate), ADR-14 (transient vs
committable).
