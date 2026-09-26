---
number: 57
title: "Centralize private human decision policy"
status: accepted
date: 2026-09-24
tags: [empirica, governance, consent, adapters]
links:
  - target: 55
    kind: Supersedes
  - target: 56
    kind: Refines
---

# Centralize private human decision policy

## Context and Problem Statement

Claude's combined form exposed decision, configuration and feedback together. Its adapter changed
an explicit Approve into Request changes whenever feedback was nonblank. Native responses selecting
Approve with `nothing` and then `approved` therefore remained pending. The core correctly refused
work, but could not detect that the adapter had replaced the chosen control because it received only
the adapter-derived outcome. Pi used separate controls but returned edited proposals to the author
instead of the host-owned confirmation required by ADR55.

## Decision Outcome

Declare a finite governance-decision policy in the existing public contract and bind it at protocol
load. Supported host UIs send a closed raw submission through private ingress: chosen action,
submitted configuration, explicit affirmations and optional dedicated feedback. A single pure core
resolver derives approve, amend, request-changes, reject or dismiss. Deliberative semantic outcomes
cannot bypass that resolver; mechanical present/dismiss and bounded auto remain distinct. Exact raw
receipt fingerprints are checked before current resolution so historical and revised exact replay
remain inert while conflicts and stale submissions fail closed.

Approval/edit and feedback are structurally separate. Request changes opens its own feedback input,
stores exact nonblank text as guidance, and grants no consent. Unknown or contradictory submissions
are rejected rather than keyword-parsed, dropped, or reinterpreted. Approve or Edit with changed
configuration remains pending and immediately receives one host-owned locked final confirmation in
both Claude and Pi, with no author action between amendment and confirmation. Request changes with
configuration edits instead stores the proposal and feedback together, without seeking approval.

The locked confirmation offers only Confirm or Decline. Decline keeps the edited proposal pending,
opens no third dialog, and grants no authority; Request changes is available on the next ordinary
review. This deliberately supersedes ADR55's allowance for feedback at final confirmation while
preserving its maximum of two Claude forms per call and fresh singleton consent requirement.

One host-neutral escaped review text is projected from the same public snapshot and consumed by both
adapters. Structured fields remain authoritative and the text is never parsed as policy. A missing
graph renders explicitly rather than crashing. Native widgets, inventory collection, timeouts,
transport and cancellation stay host-specific. Pi solicits a separate inventory affirmation before
approval, carries the exact amended revision/digest into confirmation, and shares the original review
deadline across that confirmation. An already-cancelled call consumes no presentation capacity.
Entering and submitting text on Pi's dedicated Request changes input sends the request; a redundant
second Send confirmation is not required for non-authoritative guidance. Proposing a same-model
exception while editing is separate from fresh positive consent when approving it.

## Consequences

- Human choice meaning has one executable owner behind private ingress; adapters render controls and
  follow authoritative state transitions rather than inventing outcome precedence.
- The raw submission is cryptographically covered by receipt replay identity but is not added as a
  separately queryable persisted field.
- Shared decision/control metadata and review text replace duplicated Python/TypeScript semantics and
  renderers. This is a finite binding, not a general policy language or persisted UI phase.
- Existing proposal digests, effective-budget installation, CAS, revision revocation, presentation
  limits, auto ceilings, singleton rules and public-tool authority boundaries remain unchanged.
- Pi's same-call locked confirmation has deterministic coverage but still requires native
  qualification. The prior failed f9e60b6 operational run is preserved and never repaired.
