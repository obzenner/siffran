---
number: 47
title: "Bind frozen scope to semantic identity"
status: accepted
date: 2026-09-20
tags: [empirica, claims, freeze, integrity]
links:
  - target: 26
    kind: Refines
  - target: 45
    kind: Amends
  - target: 46
    kind: Amends
---

# Bind frozen scope to semantic identity

## Context and Problem Statement

Frozen scope preserved claim IDs but allowed an author to reuse those IDs with different text,
kind, input gating, or required edges between frozen claims. Evidence and audit would become stale,
but the committed meaning itself had already changed under the same scope identity. ID membership
alone is therefore insufficient for first-write-wins commitment.

We need an immutable semantic witness that does not duplicate the graph, does not turn deferred
additions into committed work, and preserves the strict no-migration state boundary.

## Decision Drivers

* Frozen commitment must preserve meaning, not only identifiers.
* Candidate rejection must occur before graph artifact or state writes.
* Persisted state/history disagreement must be corruption, not repairable graph invalidity.
* Equivalent claim/edge ordering must not create false semantic changes.
* Deferred claims and cross-scope edges must remain admissible and audit-invalidating.
* Human-authorized scope revision is unavailable until hosts provide trusted human-origin ingress.

## Considered Options

1. Preserve only IDs and rely on evidence/audit staleness (rejected: committed meaning can change).
2. Persist a second frozen graph snapshot (rejected: duplicates semantic authority).
3. Persist one canonical semantic digest beside frozen IDs and validate it against the selected graph
   on every assembly (chosen).
4. Allow author-submitted scope revision (rejected: the author is not trusted human ingress).

## Decision Outcome

Operational state adds required nullable `frozen_semantic_digest`. It is null exactly when
`frozen_claim_ids` is null. The first freeze atomically stores ordered gating IDs and a digest over:

* exact `{id,text,kind,gating}` records in frozen-ID order; and
* the sorted set of `SupportedBy` edges whose endpoints are both frozen.

Claim-list and edge-list reordering does not change this semantic digest. Non-frozen records, edges
with a non-frozen endpoint, and the graph root are not part of this witness. They remain represented
in the full argument digest and therefore invalidate old audit coverage when changed.

After freeze, a candidate graph whose recomputed semantic digest differs returns `graph.invalid`
before artifact creation or state replacement. A persisted selected graph that does not match the
stored witness, or persisted state containing only one member of the frozen ID/digest pair, is
`run.corrupt` with the fixed-safe projection and no repair path.

The state schema remains strict: documents predating the required field are corrupt and must start a
fresh generation. No migration/default is added. Until a trusted human-origin revision protocol is
implemented, changing committed semantics requires a fresh run.

## Consequences

* Good, because frozen claim wording, kind, gating, and internal required dependencies cannot drift.
* Good, because one digest witnesses semantics without creating a second graph authority.
* Good, because equivalent ordering remains admissible.
* Good, because deferred branches remain possible while making old audit coverage stale.
* Bad, because all persisted pre-field runs fail closed and need a fresh generation.
* Bad, because legitimate committed-scope revision is unavailable until trusted human ingress exists.

## Confirmation

Conformance rejects frozen text, kind, gating, edge addition, edge removal, and edge reparenting while
preserving the selected graph. It accepts equivalent ordering and deferred cross-scope additions.
Transaction tests prove first-freeze atomicity, repeated-freeze idempotence, zero-write candidate
rejection, and fixed-safe corruption for a structurally valid persisted semantic mismatch. Strict
codec tests reject missing, malformed, or unpaired digest state. Contract/vendor/state fixtures and
architecture checks remain synchronized.
