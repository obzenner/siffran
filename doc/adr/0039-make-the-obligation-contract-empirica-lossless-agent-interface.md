---
number: 39
title: Make the obligation contract Empirica lossless agent interface
status: proposed
date: 2026-09-12
links:
- target: 30
  kind: relatesto
- target: 31
  kind: relatesto
- target: 32
  kind: relatesto
---

# Make the obligation contract Empirica lossless agent interface

## Context and Problem Statement

Empirica derives rich outstanding work from its claim graph, but a count or prose explanation is not an actionable, durable contract at an agent boundary. The host-neutral obligation module provides a deterministic view and verifier; Empirica must supply domain facts without allowing the executing actor to declare its own discharge.

## Decision Drivers

* Preserve each obligation and its witnesses across Block, RestoreRun, rendering, and terminal Allow.
* Keep graph/evidence internals private while exposing a stable agent-facing product interface.
* Keep revisions attributable and resistant to goalpost-moving.

## Considered Options

* Counts and prose only (rejected: agents must infer identity and discharge conditions).
* Expose the GSN claim graph (rejected: it leaks internal argument/evidence machinery rather than an agent-facing contract).
* An Empirica-specific mutable obligation state (rejected: status would have a second source of truth).
* A derived, vendored host-neutral contract view (chosen).

## Decision Outcome

Chosen option: "A derived, vendored host-neutral contract view." The layer model is: claim graph and evidence answer *what is known*; obligations answer *what the next actor must cause or preserve*; observations answer *what happened*; the deterministic verifier answers *what remains*. Every agent-facing result stores the `project()` output in exactly `run.contract`; graph counts remain telemetry only. Empirica traverses the graph, maps recorded evidence leaves, spike records, and audit verdicts into trusted observations, and leaves status computation to the generic verifier. String-only adapters render that same view with `render_text()`.

A contract revision is an append-only Artifact containing `Contract.to_json()`.  On every graph write or freeze, the application appends the causing knowledge artifact first, reloads the persisted revision (or conceptually starts from the empty revision-zero set), projects the canonical live claim obligations, and diffs it.  It calls `revise()` for additions and explicit retirements, appends that revision artifact, then CASes its pointer and revision together with the graph/freeze operational change; a CAS retry repeats the read/project/diff transaction.  A refutation retirement records `reason="refuted by <evidence artifact id>"` and `authority=<that artifact id>`.  Other revision authority is the causing graph/freeze knowledge artifact id: never a bare request and never the executing actor.

A changed claim (reworded text, changed kind, or changed hold) is represented as an explicit retirement of the old obligation plus a replacement obligation whose id is the old id suffixed with `/revision-<n>` (for example `empirica/G7/revision-3`); the generic library forbids reusing a retired id, so this is how history stays visible. Consumers must match obligations by `because` (claim id) when following a claim across revisions, and must read `retired` before concluding an obligation vanished. Run-level budget and stall obligations are view-time additions that are never persisted in a revision.

Every wire view re-verifies the persisted revision against current observations before `project()`; graph recomputation is used only for the diff. When every live gating claim is approved, the view also adds one `empirica/audit/<argument_digest>` requirement with the approved claim ids as `because`; a coverage-valid passing audit observation satisfies it. This audit requirement, and the run-level budget and stall obligations, are deliberately synthetic **view-time** additions, not durable claim-contract revisions, so audit delivery cannot churn B5 revision history. Terminal Allow reuses the latest persisted revision and exposes its pointer as `run.contract_artifact_id`; it does not persist a projection merely because it is terminal.

### Consequences

* Good, because resume and final handoff retain exact witnesses and provenance.
* Good, because ArtifactRepository's content-addressed append envelope makes contract projections auditable without mutating knowledge history.
* Bad, because consumers must understand the additive `run.contract` field and not infer work from counts.
* Good, because every `run` view also carries the persisted resolved `goal` and boolean `modes`, including before the first graph exists; adapters can inject invocation intent without reconstructing it from history.

### Confirmation

`make check` validates schemas and fixtures.  The B5 confirmation set is `T1` graph-write revisions/artifact pointers and supersedence, `T2` evidence-attributed refutation retirement plus preservation, `T3` freeze deferred holds, `T4` preservation of unchanged claims, `T5` the ≥12 mixed open/blocked/deferred wire projection, `T6` semantic `must == graph.nodes[claim].text`, `T7` Codex Block reason containing `render_text(view)`, and `T8` the mutation experiment: replacing the wire view with graph-derived revision-1 data makes T1 fail.  Existing end-to-end tests additionally assert `preserved()` across Block, RestoreRun, Claude rendering/compaction, terminal handoff, and artifact round-trip.

## More Information

This decision relates to ADR-30 (host-neutral contracts), ADR-31 (append-only artifacts), and ADR-32 (host lifecycle gating).
