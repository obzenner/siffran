---
number: 41
title: "Audit as a contract: dossier in, host-observed verdict out"
status: proposed
date: 2026-09-12
tags: [audit, contract, independence, pi]
links:
  - target: 20
    kind: Amends
  - target: 24
    kind: Depends on
  - target: 25
    kind: Depends on
  - target: 27
    kind: Depends on
  - target: 30
    kind: Depends on
  - target: 39
    kind: relatesto
  - target: 40
    kind: Amends
---

# Audit as a contract: dossier in, host-observed verdict out

## Context and Problem Statement

ADR-20 requires an audit before convergence. The first Pi dogfood showed that an auditor could not
obtain a host-neutral argument dossier and that verdict delivery was adapter-specific. The initial
implementation also overstated its trust boundary. Independent review
`doc/design/reports/review-astra-pr25.md` demonstrated predictable/public ticket nonces, replay,
conditional Pi redaction, unresolved model aliases, and contract/digest lifecycle defects.

## Decision Drivers

* An auditor consumes only the wire dossier, not the run store or adapter internals.
* A ticket is unpredictable, private on supported host paths, persisted, and one-shot.
* Model independence is recorded and reported precisely, never inferred from an agent alias.
* Digests and obligations remain deterministic and lossless across processes and views.
* Spawn accounting binds a ticket to exactly one reservation.

## Considered Options

1. Give the auditor model-callable Empirica tools (rejected: the author can call them too).
2. Dossier in, host-observed verdict out (chosen).
3. Ship store-reading Python helpers to every auditor (rejected: host-coupled and failed live).

## Decision Outcome

The decision remains **dossier in, host-observed verdict out**.

### Contract and ticket trust boundary

`GetArgument` returns the complete nonce-free dossier and uses the same canonical claim/evidence
digests as the convergence gate. An audit ticket nonce is generated once with
`secrets.token_hex(16)`, persisted in the operational ticket, and returned only by the
`audit_ticket` response. Every other read view redacts it, including RestoreRun and compaction data.
A void ticket is unusable. A consumed ticket is one-shot: another verdict is a replay Block.

These properties make accidental or post-hoc disclosure non-authoritative, but do not authenticate a
same-OS-user caller. Deliberate author access to `bridge.py`, the run store, process memory, or host
transcripts is **out of scope**. This is the same local-user trust model as Claude hooks; the bridge
is an application boundary, not an OS principal boundary.

A reservation has an id. A ticket binds to exactly one reservation. Voiding is an idempotent state
transition and cannot decrement another reservation.

### Independence is recorded and reported

StartRun records the author actor when the host supplies a concrete model; tickets record the
resolved auditor actor. Every run view and terminal Allow carries `run.audit.independence`:

* `decorrelated`: both concrete model ids are known and differ;
* `same_model`: concrete ids match (ticket issuance Blocks);
* `unverified`: either identity is absent or is a tier alias.

Thus Empirica reports when audit independence was not obtained; it does not claim the host always
proves it. Pi resolves `agent: <name>` from its agent-definition YAML and never substitutes the agent
alias as a model. Claude records the documented top-level hook payload `model` when present and
otherwise omits the actor.

### Host status

| Host | Dossier/nonce delivery | Verdict ingest and privacy | Status |
|---|---|---|---|
| Pi | `tool_call` rewrites the foreground child task | `tool_result` is correlated by persisted `{toolCallId, runHandle, nonce}`; all result shapes are redacted before await | Complete; real bridge flow covered |
| Claude Code | PreToolUse `updatedInput` rewrites the child prompt | SubagentStop ingests the verdict, but cannot rewrite the parent-visible child result | Ingest complete; private delivery unsupported. The child's fenced block reaches the author only after the verdict is recorded and the ticket consumed, so it is not a forgery vector |
| Codex 0.146.0 | no documented updated-input channel | no child-result ingest channel | Unsupported; obligation remains open |

### Contract correctness

Dossier traversal adapts the evidence oracle to its boolean contract. Evidence leaves are sorted by
the complete canonical form that is hashed. Frozen scope defers claims outside the committed set.
Terminal budget/stall requirements remain on every later view. The audit requirement enters durable
contract history when the argument first becomes all-approved and is explicitly retired/replaced
when argument shape changes. Adjacent boundaries use `same_contract`; `preserved` retains its
non-weakening meaning.

## Consequences

* Good: verdicts made only from the dossier agree with gate recomputation across bridge processes.
* Good: nonce secrecy, replay rejection, exact reservations, and persisted Pi correlation close the
  accidental forgery and reload channels.
* Good: `unverified` makes host limitations visible instead of presenting an alias as independence.
* Bad: Claude cannot provide private result delivery; consumed-ticket replay protection limits the
  impact without pretending the channel is private.
* Bad: same-user direct store/bridge tampering remains outside this plugin's threat model.

## Confirmation and review closure

The independent review triggered these changes:

| Review finding | Closure |
|---|---|
| 1 | unpredictable persisted nonce, read-view redaction, void/replay Block, scoped threat model |
| 2 | Claude row corrected to ingest-complete/private-delivery-unsupported |
| 3 | Pi pre-await redaction, result redaction, persisted run-bound correlation |
| 4 | concrete model resolution and reported independence |
| 5 | boolean dossier oracle |
| 6 | full canonical leaf ordering and cross-process tests |
| 7 | corrected frozen projection and exact-set tests |
| 8 | persistent terminal requirements and durable audit lifecycle |
| 9 | one contract-aware denial renderer per adapter |
| 10 | reservation ids, ticket binding, idempotent exact refund |
| 11 | `same_contract` and adjacent-boundary checks |
| 12 | contract-digest nudge identity, explicit limit parsing/reset, contract-bearing pause |

Run `make check`; it includes docs drift, contract/schema/fixture validation, vendor equality, adapter
typechecks/tests, and ADR health.
