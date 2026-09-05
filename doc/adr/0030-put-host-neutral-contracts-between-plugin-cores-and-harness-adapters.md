---
number: 30
title: "Put host-neutral contracts between plugin cores and harness adapters"
status: accepted
date: 2026-09-04
tags:
  - architecture
  - portability
  - contracts
links:
  - target: 8
    kind: Amends
  - target: 12
    kind: Amends
  - target: 21
    kind: Depends on
---

# Put host-neutral contracts between plugin cores and harness adapters

## Context and Problem Statement

Empirica currently expresses domain decisions through Claude Code hook payloads, exit codes, stderr,
and paths under `.claude`. Methodologist similarly names Claude tools and `$ARGUMENTS` in the same
document that defines methodology semantics. Porting either plugin by translating those files directly
would make Pi a wrapper around Claude behavior and create two implementations of the rules.

## Decision Drivers

* One convergence and methodology model across hosts.
* Host lifecycle limitations must be visible rather than hidden in core behavior.
* Contracts must be testable without Claude Code, Pi, a filesystem, or Git.
* A second implementation must be judged by observable decisions, not matching source structure.

## Considered Options

* Copy the Claude integration and adapt it independently for Pi.
* Make Claude hooks the canonical subprocess API.
* Define versioned domain requests, responses, ports, and conformance fixtures; make both hosts
  adapters.

## Decision Outcome

Choose versioned domain contracts. `contracts/empirica/v1` and
`contracts/methodologist/v1` own transport-neutral envelopes and typed decisions. Host adapters map
their native events into those requests and map results into native enforcement/UI behavior.

Core decisions do not contain hook names, Pi event names, paths, Git commands, UI operations, or
process exit codes. In particular, Empirica returns `Allow`, `Block`, `Inert`, or `Fault`; exit
`0`/`2` is only the Claude adapter's encoding. Methodologist phase tracking depends on a `TaskTracker`
capability; Claude tasks and a Pi widget/session entry are adapter implementations.

The existing GSN, evidence, digest, audit, budget, and convergence functions remain the semantic
source from which the first core is extracted. Existing behavior is frozen first as substrate-neutral
fixtures. New adapters must pass those fixtures.

### Consequences

* Good, because Claude and Pi cannot silently diverge on convergence.
* Good, because host capability gaps are explicit at adapter boundaries.
* Good, because persistence can change without changing domain envelopes.
* Bad, because adapters require translation code and contract versioning.
* Bad, because the current sibling-loaded Python modules must become a package before they are a clean
  reusable core.

### Confirmation

`make contract-check` validates every schema and fixture envelope. Adapter suites must consume the
same fixtures. Static checks will reject host and persistence concepts in core contract documents.
