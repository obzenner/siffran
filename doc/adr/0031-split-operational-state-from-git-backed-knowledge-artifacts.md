---
number: 31
title: "Split operational state from Git-backed knowledge artifacts"
status: accepted
date: 2026-09-04
tags:
  - storage
  - git
  - evidence
links:
  - target: 14
    kind: Supersedes
  - target: 19
    kind: Amends
  - target: 22
    kind: Depends on
---

# Split operational state from Git-backed knowledge artifacts

## Context and Problem Statement

ADR-14 puts both control state and knowledge artifacts in
`.claude/empirica/<run_id>`. That couples the domain to one host, pollutes repositories, fragments
identity across worktrees, and discards claim/evidence provenance when scratch is removed.

## Decision Drivers

* No plugin runtime files in a project worktree.
* One machine-local namespace shared by Claude Code and Pi.
* Claims and evidence must be durable, content-addressed, and reviewable.
* Artifact writes must not switch HEAD or modify the user's worktree or index.
* Concurrent adapters must not lose updates.

## Decision Outcome

Split persistence into two implementation-independent ports:

* `RunRepository` owns operational state under `~/.empirica-plugin/` (overridable by
  `EMPIRICA_HOME`): active-run pointers, status, phases, budgets, modes, tickets, locks, and recovery
  journals.
* `ArtifactRepository` owns the knowledge plane: claim graphs, in-toto evidence, audit verdicts, and
  run summaries. Its Git adapter writes commits beneath `refs/empirica/*` using plumbing or an
  isolated index, never the user's checkout.

The full run key is `(project_id, run_id, generation)`. For Git projects, `project_id` derives from
the resolved Git common directory, which unifies linked worktrees but keeps independent clones
independent. A terminal run reopened in the same host session receives a new generation, preventing
stale budgets, claims, or verdicts from becoming current.

Both repositories expose opaque revisions and compare-and-swap writes. Artifact append retries are
set unions by artifact identity; a retry may not replace an independently appended artifact. Shadow
refs are local by default and are never pushed implicitly.

Legacy `.claude/empirica` state is not a runtime fallback. It is accepted only by an explicit,
idempotent migration operation. Normal reads and writes never inspect `.claude` or `.pi`.

### Consequences

* Good, because host-neutral runtime state no longer pollutes repositories.
* Good, because evidence survives as inspectable Git history.
* Good, because worktrees share control identity without sharing independent clones.
* Bad, because the home namespace concentrates machine-local control metadata and needs retention
  tooling.
* Bad, because repositories without Git need a non-durable artifact adapter and cannot claim
  Git-backed provenance.
* Neutral, because Git history is tamper-evident, not access isolation; independent audit and digests
  remain necessary.

### Confirmation

Integration tests must prove concurrent CAS behavior, append commutativity, generation isolation,
crash recovery, no-remote operation, and byte-identical user HEAD/index/worktree before and after an
artifact write.
