---
number: 14
title: Split artifacts into transient scratch and committable records
status: accepted
date: 2026-07-22
tags:
  - architecture
  - artifacts
  - state
links:
  - target: 7
    kind: Depends on
  - target: 8
    kind: Depends on
  - target: 12
    kind: relatesto
  - target: 15
    kind: relatesto
---

# Split artifacts into transient scratch and committable records

## Context and Problem Statement

The workflow produces many artifacts: the unknowns ledger, spike scratch and test output,
`/think` reasoning traces, and — on the unknown path — ADRs, a spec, and an implementation
task, plus the eventual code. Which of these are durable records that belong in git, and
which are throwaway working state that must never be committed? Without an explicit split
the workflow either pollutes the repo with scratch or, worse, treats transient state as
authoritative. This is the workflow's data-model boundary, and it was previously undecided.

## Decision Drivers

* Durable state must survive context compaction and cross-turn resumption (ADR-7, ADR-8) —
  "the agent forgets, the repo doesn't."
* Committing scratch reasoning pollutes history and misleads future readers.
* Some agent-generated prose (e.g. PR/commit descriptions) is actively worse than useless
  and must not become the record of intent.
* A single source of truth per fact (SSOT) — the durable artifact, not the prompt or trace.

## Considered Options

* **Commit everything** — ledger, traces, spikes, docs all in git. Rejected: pollutes
  history; makes transient state look authoritative.
* **Commit nothing but code** — keep all reasoning in-conversation. Rejected: loses the
  decision record and the spec; state dies at compaction.
* **Two-tier split** — TRANSIENT artifacts live outside git (in `.claude/` scratch,
  durable enough to resume a run but never committed); COMMITTABLE artifacts are the
  decision/spec/test/code records that go in git and are the SSOT.

## Decision Outcome

Chosen option: "Two-tier split", with this assignment:

**Transient (working state; in `.claude/` scratch, git-ignored; may be re-injected across
compaction but never committed):**
- the unknowns ledger (M8) — durable across turns, but working state, not a record
- spike scratch and raw test output (M3)
- `/think` reasoning traces (the trace informs the durable artifact, then is discarded)
- staffing briefs (M2), intermediate SpikeResults, race bookkeeping (M4)

**Committable (durable records; git; SSOT):**
- ADRs (M6) — the decision record with rejected alternatives
- the spec (M6) — self-contained: names files/interfaces, states out-of-scope, ends with
  an end-to-end verification step
- the implementation task (M6)
- tests and code (M7 handoff → implementation)

The rule of thumb (Anthropic, "treat CLAUDE.md like code"; would its removal cause future
mistakes?) decides edge cases. Agent-generated PR/commit *descriptions* are transient by
default — the human-authored intent/framing is the durable record (Willison, Jul 8 2026).

### Consequences

* Good, because the repo carries only records that a future engineer needs; scratch stays
  out of history.
* Good, because the ledger can be durable-resumable (ADR-8) without ever being a committed
  artifact — resumption reads `.claude/` scratch, not git.
* Good, because it gives the data model a clear boundary: modules know whether their output
  is scratch or record, which fixes the earlier "no agreed data model" gap.
* Bad, because the workflow must manage `.claude/` scratch lifecycle (creation, keying by
  task, cleanup) and ensure it is git-ignored — an operational burden.
* Bad, because the transient/committable line for a given artifact (e.g. a spike that
  turns out worth keeping) is a judgment call the workflow must make explicitly.

### Confirmation

Fitness function: (1) after a run, `git status` shows only committable artifacts (ADRs,
spec, task, tests, code) staged — no ledger, traces, or spike scratch; (2) the ledger and
`/think` traces are written under a git-ignored scratch path; (3) a resumed run reconstructs
state from scratch, not from git; (4) generated PR descriptions are not committed as the
intent record.

## More Information

Grounded in convergent primary sources (Apr–Jul 2026): Osmani, *Loop Engineering* (Jun 8,
"a markdown file… that lives outside the single conversation"); Anthropic Claude Code
best-practices docs (CLAUDE.md checked into git, checkpoints/rewind "not a replacement for
git", spec-to-SPEC.md); Willison, *Kenton Varda* (Jul 8, AI-written change descriptions
"worse than useless"). Depends on ADR-7 (ledger) and ADR-8 (durable resumption); relates to
ADR-12 (the `/think` trace is the transient source the committable docs derive from).
