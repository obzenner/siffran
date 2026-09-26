---
number: 54
title: "Plain-language governance review with durable change requests"
status: accepted
date: 2026-09-23
tags: [empirica, governance, approval, ux]
links:
  - target: 53
    kind: Refines
---

# Plain-language governance review with durable change requests

## Context and Problem Statement

ADR 53 binds approval to an exact digest, but both host dialogs presented raw JSON and asked the
human to author a complete amendment JSON document. Neither host offered a durable plain-language
request for the author to revise scope. Changing one ceiling required reconstructing internal
configuration instead of editing a number.

## Decision Outcome

Both hosts render the whole proposal in plain language: goal, every claim and dependency, numeric
ceilings next to the counters already used, labeled modes, auditor, inventory and author. Untrusted
quoted text is fenced on `| ` lines; controls, bidi characters (including U+061C), and the literal backslash are
visibly escaped so an actual ESC and the text `\x1b` stay distinguishable and readable Unicode is
kept. Edits use primitive fields only: bounded 1..4-digit integers validated against the canonical
maxima and used counters, Enabled/Disabled mode choices, and an auditor pick from the authorized
inventory. Nobody authors JSON in a dialog. Copy states that approval covers the CURRENT displayed
proposal and that edits are submitted for another review and are NOT approved yet.

Governance gains one required durable field, `change_request = {text, plan_revision,
proposal_digest} | null`, and one private receipt outcome `request_changes` surfaced publicly as
`governance.changes_requested`. The request names the displayed revision/digest (never a future
one), is preserved through graph/configuration/context revisions, restored through the existing
CAS state/codec, and cleared only by a committed approve or reject. It is guidance, never consent
or evidence: excluded from the proposal digest, unreachable from public author actions, refused in
`auto` mode, and blank/whitespace-only text is malformed. Nonblank feedback on an approve
submission becomes a request; combined feedback plus configuration edits are one atomic
`request_changes` decision carrying an optional amendment. Approved state requires a null request.

Unrecognized dialog values (including stale labels) and Escape/cancel at any step dismiss the whole
flow before any authority-bearing branch. Choosing Edit with unchanged values is not approval:
the proposal stays pending. Deadline/abort is checked immediately before each dialog, including
after the reservation returns. Reject and request grant nothing and need no auditor or
inventory affirmation. Fresh singleton consent remains a dedicated positive answer.

## Consequences

* Good: humans review scope they can read, and their feedback survives the author's next revision.
* Good: no dialog path approves anything the human did not see as the current proposal.
* Cost: the Empirica effective runtime grows from 10647 to 10871 lines (+224, within the +250
  allowance); `architecture.json` records the measured total, not the allowance.
* Compatibility: contract 2.1.0 and plugin 3.2.0 are unreleased, so the new required field fails
  closed on old state without migration.

## Confirmation

`make empirica-governance-check` covers the request lifecycle, auto refusal, blank text, future
revision, restore, combined edits, exact text and reject-without-auditor; `make empirica-pi-check`
covers unknown-label dismissal, per-field edits, bidirectional modes, cancel midway, malformed
numbers and reversible escaping through the real private bridge. `make empirica-governance-ui-check`
adds fast UI-control regressions for no-op edits, delayed reservations, every ordinary edit/request
cancellation stage, nonzero usage floors, escaped auditor labels, and complete maximum-sized scope
text. These fast tests use a fake decision recorder, not a substitute for real service tests.
`make pi-validator-unit-check` verifies focused test selection and the runner's finite asynchronous
per-test timeout. Native host rendering remains separately unverified.
