---
number: 70
title: Record event time beside the record, never inside it
status: accepted
date: 2026-10-11
tags:
- empirica
- state
- observer
- privacy
links:
- target: 31
  kind: Amends
- target: 65
  kind: Amends
---

# Record event time beside the record, never inside it

## Context and Problem Statement

Empirica 5.0.0 recorded no event time. Run state has no time field, artifact commits are dated `@0` so that the same
artifacts yield the same commit on any machine (ADR-31, amended below), and the only wall time a reader saw was the
observer's `updated`: the modification time of `run.json`. That time says nothing about when an artifact was committed, a
copied store loses it (`cp -r` resets it), and a reader cannot tell a missing time from a wrong one.

A human reading the store needs to know when a run started, when it last changed and when it ended, and when each step
of its history happened. Every place inside the record that could hold a time is digested: a key in the run state enters
`state_digest` and every manifest's `next_state_digest`; a key in an artifact enters its id; a time in a host payload or
a governance decision enters artifact ids and receipt fingerprints. A time in any of them would make the same run,
replayed with another clock, a different run.

## Decision Drivers

* Ordering, gating and every digest must not depend on any clock.
* A reader must be able to say "unknown" and never confuse it with zero or with another event's time.
* Times are machine-local facts; nothing about them may leave the machine by way of the artifact refs.

## Considered Options

* A time key in the run state, in artifacts or in commit metadata (changes every digest and the refs).
* The reserved `clock_inputs` slot of the observation basis (a digested slot for policy inputs that depend on a clock,
  not a log).
* An append-only timeline beside each run generation, written by the bridge adapter after each successful write
  (chosen).

## Decision Outcome

### The rule

The time of an event is recorded beside the record, never inside it. One append-only timeline per run generation,
`<home>/projects/<p>/runs/<r>/gen-<n>/timeline.jsonl` (`adapters/state/timeline.py`), next to that generation's
`run.json`, under `EMPIRICA_HOME`. One JSON line per successful write of `run.json`:

| Field | Meaning |
|---|---|
| `seq` | the entry's position, from 1 |
| `previous_revision` | the revision the write replaced; `null` for the create |
| `revision` | the revision it wrote |
| `artifact_head` | the new state's `committed_artifact_head_id`: the manifest that write committed |
| `status` | the run status it wrote |
| `plan_revision`, `proposal_digest` | the written state's governance plan revision and proposal digest (`null` when it holds none) |
| `receipts` | the ids of the written state's governance receipts, in order (`null` when it holds none) |
| `at` | the writer's wall clock, UTC epoch seconds (a float) |

Every field but `at` is read from the value written, as `status` is: display data that names which write proposed a
configuration (the entry that first carries its plan revision and proposal digest, its predecessor's entry known not to)
and which recorded each receipt (the entry that first lists it), so their times are exact. A configuration lives only in
the run's state, so nothing else could time it.

### The owners

| Fact | Owner |
|---|---|
| When each successful run-state write happened | the bridge adapter (`adapters/bridge._RecordingRuns`), after a `create` or `compare_and_set` returns; its clock is injected and defaults to `time.time`, read only there. A lost compare-and-set records nothing |
| Which artifact a write first committed, so each step's time; a run's `started`, `updated` and `ended`; a project's `since` | the observer read model (`application/observer.Times`), from the timeline and the manifest chain |
| Ordering, gating, every digest | unchanged: `stamp_seq` and history order. No time orders or decides anything |
| The observer's cache versions (a row's and a run detail's `version`, the summary's and a page's `token`, the ETag) | the observer read model. They version the **published document**, which holds the times, so they change when a time does (a 304 must never serve stale times). They are not plugin digests: the clock-independence above covers the run state, artifacts, receipts, manifests, `GetRun`, `GetArgument` and the auditor dossier. A version is a function of what was read, never of when it was read: no clock reading or nonce enters one |

`core/` and `application/` read no clock (a static test holds this). The service's `_clock` stays unused. Recording is
display data like the location record (ADR-65): a timeline that cannot be written, a clock that fails or a malformed
timeline never fails or changes a run.

### The no-gap rule

A time is published only for a write whose own entry exists and chains to the entry of the write before it. A timeline
is trusted as one linear chain in the order it was appended: entry 1 is the only create (no `previous_revision`), and
each entry N's `previous_revision` is entry N−1's `revision`, a revision not seen before in the chain. The trusted part
is the longest prefix that holds; at a fork, a create out of order or a second create, a backlink to an earlier
revision, or a missing entry, that entry and every later one are `null` (amended 2026-10-13: a later entry is never
trusted because its predecessor appears somewhere else in the file). Two trusted entries that name the same head are
ambiguous and neither is used. So:

- each history step's `at` is the time of the entry whose `artifact_head` is the manifest that first committed it;
- `started` is the create entry's time; `updated` is the time of the write that committed the current state's head; `ended` is the time
  of the write that set the state's terminal status from an active one; `since` is the project's earliest `started`;
- a write whose entry is missing leaves its own artifacts and those of every later write `null` (the chain is broken
  there). A later or earlier write's time is never attributed to an artifact.

The reader tolerates a torn last line (a crash during an append before the atomic rename cannot leave one, but a
copied or hand-edited file can) and treats anything else malformed as unknown for the whole generation.

### Times are local labels

A time is the writing machine's clock: unsigned, possibly skewed between hosts, possibly going backwards. It is never an
order: attempts are ordered by generation, history by its own order, and `stamp_seq` stays the ordering authority. Each
generation has its own timeline, so a resumed run (`gen-2`) has its own `started`. A copied store keeps its times, which
are as trustworthy as the copy.

### Privacy

The timeline is machine-local, under `EMPIRICA_HOME`, never in `refs/empirica/*` or any Git object. Artifact commits
stay dated `@0`. Pushing or sharing a repository's artifact refs shares no time.

### What it removes

The published `updated` from the state file's modification time, and `since` derived from it. The observer reads no
modification time.

### Consequences

* Good, because the same run driven with two clocks is byte-identical in run state, artifact ids, manifests,
  `state_digest`, `GetRun`, `GetArgument` and the auditor dossier; only the timelines differ.
* Good, because a missing or broken time reads as `null`, never as zero or a neighbour's time.
* Bad, because the timeline is not transactional with the write: a crash between the compare-and-set and the append
  loses that write's time (and the next write's, which cannot chain).
* Neutral, because each poll reads one more small file per run.

### Confirmation

`plugins/empirica/tests/v2/test_timeline.py`, over runs driven through the real v2 service: two-clock invariance;
monotone attribution with each write's entry removed in turn; an unwritable timeline; a torn or corrupt timeline; a
resumed generation; concurrent writers (only the CAS winner is recorded); `ended` exactly for a terminal status and
`started <= updated` under a monotone clock; no clock in `core/` or `application/`. `test_observer.py`
(`FilesystemStoreTests`, `TwoClockFilesystemTests`): the bridge on disk, a copied store with reset modification times,
and byte-identical `run.json` and artifact refs under two clocks. Mutations run when it was added: borrowing the next
entry's time for an orphaned artifact fails the attribution invariant; a time inside the state document makes the
two-clock store fail to build (the strict state schema and the manifest's state witness refuse it).
