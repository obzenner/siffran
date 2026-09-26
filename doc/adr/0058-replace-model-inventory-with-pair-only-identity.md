---
number: 58
title: "Replace model inventory with pair-only identity"
status: accepted
date: 2026-09-24
tags: [empirica, governance, identity, compatibility]
links:
  - target: 53
    kind: Supersedes
  - target: 54
    kind: Refines
  - target: 55
    kind: Refines
  - target: 57
    kind: Refines
---

# Replace model inventory with pair-only identity

## Context and Problem Statement

Empirica transported a user's configured model inventory into governance context, proposal digests,
review text and audit-child metadata. Claude read an operator inventory file and Pi enumerated its
model registry. This made unrelated user configuration affect approval and exposed a catalog as if
configuration implied availability or authorization. A singleton exception could also admit a
same-model review. The product requirement is narrower: the main model and reviewer model must be
known and different, while user configuration remains outside plugin governance.

## Decision Outcome

The finite core policy accepts only a host-observed main identity and a selected concrete reviewer.
Both must normalize through the conservative exact identity map and their normalized identities
must differ. Unknown equivalence and same-model pairs always block. The host-observed actual
reviewer must match the approved selected reviewer, and covered-actor attribution remains the
observed main identity plus evidence provenance. Proposal, graph, evidence, child, operation and
snapshot bindings remain exact.

Delete model inventories, operator configuration loading, inventory affirmation and reasons,
`allow_same_model`, singleton consent, automatic catalog selection, `audit_inventory_digest`, and
the audit-plan inventory digest. Claude and Pi edit reviewer provider/model through bounded readable
scalar controls. They expose no catalog or JSON control. Ordinary edits remain pending until one
same-call read-only Confirm/Decline dialog; feedback stays separate, with two forms at most. Raw
receipt replay, CAS and the original deadline continue across the locked confirmation.

Explicit auto accepts an author-proposed known-distinct reviewer within the existing budgets, modes
and finite revision bounds. Null, unknown and same-model reviewers block without fallback. Selection
makes no claim of availability, separate operator authorization or successful execution; actual
host observation still has to match before audit admission.

## Compatibility

The public contract changes from 2.1.0 to 3.0.0 and the plugin changes from 3.x to 4.0.0. Wire and
state family constants remain `empirica/v2` and `empirica.run/2`. Strict current-state decoding means
inventory-shaped stored documents are `current_corrupt`/`run.corrupt`; the runtime does not migrate,
repair or mutate them. A subsequent `StartRun` allocates a fresh generation without carrying
approval. Historical bytes remain preserved, but receipts inside incompatible old documents are not
replayable by the new runtime. Exact raw replay remains supported for current-schema documents.

## Consequences

Unrelated configured models cannot change a proposal digest or identity decision. Pi's governance
path no longer depends on registry size and Claude has no operator model file dependency. The
accepted reduction is that auto's reviewer is author-proposed rather than selected from a purportedly
authorized set. Host execution-contract resolution may still determine whether the selected model
can run, but its catalog is neither persisted nor projected as governance and cannot grant approval.

This supersedes ADR53's inventory and singleton clauses, ADR54's inventory display/affirmation,
ADR55's singleton reconfirmation, and ADR57's explicit affirmation/inventory adapter duties. Their
remaining exact-binding, readable-review, host-owned confirmation, replay and CAS decisions stand.
