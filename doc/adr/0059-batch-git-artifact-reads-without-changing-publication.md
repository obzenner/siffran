---
number: 59
title: "Batch Git artifact reads without changing publication"
status: accepted
date: 2026-09-26
tags: [empirica, git, performance, integrity]
links:
  - target: 31
    kind: Refines
  - target: 57
    kind: Refines
---

# Batch Git artifact reads without changing publication

## Context and Problem Statement

One graph observation over a small artifact set launched 55 Git processes. Each full-tree read used
one `cat-file` process per blob, and the coordinator read the full physical set before calling an
append port that must read it again for idempotency, collision refusal and concurrent union. The
physical store was small; subprocess and duplicate-read amplification, not stored bytes, explained
the measured path. Raising host deadlines or weakening verification would hide rather than fix it.

## Decision Outcome

Read all blobs listed by one immutable tree through one `git cat-file --batch` process. Parse the
protocol by byte size and require the requested object ID, blob type, exact body length and newline
frame in request order. Reject missing, malformed, truncated, non-UTF-8 or trailing output as store
corruption. Empty trees start no batch process. Text and byte transports share the adapter's single
isolated Git environment, so user configuration, worktree and index remain outside the store.

Make `Coordinator._append_once` delegate directly to `ArtifactRepository.append`. The port already
owns idempotency and collision refusal; its CAS retry refresh remains the concurrency boundary. The
initial physical snapshot, append-side UTF-8/JSON and artifact-ID/path checks, ID/body collision
refusal, and final persisted manifest-history read-back all remain. No request cache, daemon, alternate backend, `append_many`,
tree-metadata shortcut or deadline change is introduced.

## Consequences

A nonempty full-tree read uses one batch body process rather than one process per artifact. The
bootstrap graph path uses four complete validated tree reads instead of six while retaining two
separate append publications and their orphan/crash behavior. The Git tree, blob, commit and shadow
ref formats are unchanged, as are manifest reachability, run-state CAS, replay and corruption
semantics. Existing stores require no migration.

Deterministic process-count tests guard the optimization independently of noisy wall-clock timing.
Fresh installed-host qualification remains necessary before claiming the previous transport timeout
is resolved; synthetic bridge and integration checks are source evidence only.
