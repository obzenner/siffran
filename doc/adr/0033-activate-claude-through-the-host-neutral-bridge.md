---
number: 33
title: "Activate Claude through the host-neutral bridge"
status: accepted
date: 2026-09-05
tags:
  - claude
  - adapters
  - activation
links:
  - target: 30
    kind: Depends on
  - target: 31
    kind: Depends on
  - target: 32
    kind: Amends
---

# Activate Claude through the host-neutral bridge

## Context and Problem Statement

The host-neutral core, application service, global operational repository, Git artifact repository,
and Claude translators existed, but Claude's registered hook scripts still owned a second file-based
runtime beneath `.claude/empirica`. Keeping both active made the adapter architecture advisory and
allowed host implementations to disagree.

## Decision Drivers

* Hook event names, matchers, timeouts, and native exit/stream contracts must remain stable.
* Claude and Pi must adjudicate through the same application/core operations.
* Runtime operation must not inspect legacy `.claude/empirica` state.
* A terminal relaunch must allocate a clean generation, while ordinary later events must only resolve
  the latest generation and never create one.
* Worktree, index, and HEAD must remain untouched by runtime persistence.

## Decision Outcome

The registered Claude scripts are thin process entry points. They translate native payloads in
`adapters/claude`, dispatch through `adapters/bridge.py`, and map typed responses back to Claude's
exit/stdout/stderr contract. Domain policy remains in application/core.

Add `ResolveRun(selector)` to `empirica/v1`. It returns the latest existing generation without
creating or advancing one. `StartRun` remains the only lifecycle operation that may allocate a new
generation. This distinction prevents Stop, PreToolUse, and SessionStart from accidentally reopening
a terminal run.

Operational state is exclusively machine-local under `~/.empirica-plugin/` (or `$EMPIRICA_HOME`).
Knowledge is exclusively append-only under local `refs/empirica/*`. Normal runtime instructions
forbid direct edits to either store and forbid `.claude`/`.pi` runtime state. Legacy state enters only
through the explicit migration adapter.

### Consequences

* Good, because Claude and Pi now share one adjudication and persistence authority.
* Good, because hook registration and native enforcement mechanics stay stable.
* Good, because lifecycle lookup cannot allocate or mutate a generation.
* Bad, because the versioned contract gains one host-lifecycle lookup operation.
* Good, because the legacy runtime and compatibility suite are removed rather than retained as a
  second authority; the major release keeps only the explicit `make migrate-legacy` importer.

### Confirmation

Subprocess lifecycle tests cover start, route observation (including route-announcement
self-stamping), knowledge submission, deterministic spike nonzero/timeout/launch/output bounds,
audit freshness, convergence, relaunch generation isolation, spawn caps, compact restore, and
byte-identical pre-existing dirty worktree state. Contract validation covers `ResolveRun`; source
checks ensure normal Claude entry points do not reference legacy runtime paths and that no duplicate
hook or quarantine authority remains.
