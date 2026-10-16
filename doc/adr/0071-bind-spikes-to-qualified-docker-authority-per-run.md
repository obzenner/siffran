---
number: 71
title: Bind spikes to qualified Docker authority per run
status: accepted
date: 2026-10-16
tags:
- empirica
- sandbox
- docker
- security
- state
links:
- target: 30
  kind: Amends
- target: 67
  kind: Amends
---

# Bind spikes to qualified Docker authority per run

## Context and Problem Statement

Empirica needs to execute deterministic probes without granting model-authored commands ambient host-shell authority.
A mutable "Docker is available now" check cannot authorize a durable run: the CLI, endpoint, image, isolation policy,
implementation, or qualification pointer can change between selection, execution, and audit. A process can also crash
after external execution but before result admission, so exactly-once execution cannot be promised.

Empirica 6 is a plugin major while its public wire remains `empirica/v2`; persisted runs already use the exact
`empirica.run/3` schema. Earlier `empirica.run/2` documents lack the immutable backend binding and cannot be upgraded
without inventing authority they never recorded.

## Decision Drivers

* Model-authored commands must never execute through the ambient host shell or an unqualified fallback.
* Qualification and execution must bind the exact local Unix Docker authority and all security-relevant bytes.
* Existing runs, artifacts, and content-addressed evidence must remain untouched.
* Crash and concurrency behavior must be explicit rather than disguised as exactly-once execution.
* Empirica must not install, pull, start, repair, or reconfigure Docker or permissions.

## Considered Options

* Execute directly with the host shell (rejected: no sandbox boundary and no immutable authority).
* Discover current Docker authority before every request and let existing runs adopt it (rejected: mutable authority
  changes the meaning of a durable run).
* Migrate run/2 state by adding a guessed backend (rejected: it fabricates historical authority).
* Select externally qualified authority once at `StartRun`, persist it in run/3, and execute only against that exact
  record (chosen).

## Decision Outcome

An operator, outside an Empirica run, qualifies one exact local Darwin-to-Unix-socket-to-Docker authority after the
source tree is clean and committed. Qualification is passive and pull-disabled. It binds the canonical Docker CLI and
hash, Unix endpoint/runtime, already-present digest-pinned image and config, executable isolation policy, environment,
and every implementation source or runtime-loaded contract that can select, invoke, or authorize spike execution,
including the located/filesystem repositories that own the run CAS sealing request state before execution.
Empirica never changes daemon state, context, configuration, permissions, images, or installation.

`StartRun` selects authority once. The persisted `spike_backend` is exactly either `spikes_unavailable` or a qualified
`docker-rust-v1` binding with qualification-record and execution-binding digests. Every later request uses only that
immutable record; a changed current qualification applies only to a future new run. `qualified` means bound, not that
the backend is currently healthy. Public projections expose only this closed binding, never local paths, sockets,
mutable pointer state, diagnostics, fallbacks, or repair instructions.

Spike admission is a two-CAS transaction. The first CAS seals and commits the request before external execution. The
harness may then execute once for that application call. A second CAS admits the immutable result only after current
run, route, investigation, claim, prerequisite, file, and backend bindings are revalidated. Result-CAS conflicts retry
admission without rerunning that call. A process crash or a concurrent separate call can execute an already-sealed
request again, so the contract is intentionally **at least once across crashes/separate calls**, not exactly once.
Discarded, timed-out, output-limited, OOM, cleanup-failed, identity-failed, or authority-failed attempts admit no result
evidence.

There is no run/2 compatibility loader, migration, schema, or mutation branch. A run/2 document is unsupported/corrupt
and stays byte-for-byte untouched. The only upgrade path is an explicit fresh `StartRun`, which creates a new run/3
generation. Existing artifacts and refs are neither rewritten nor imported into the new generation.

The final source and contract cutover changes implementation authority and therefore invalidates every earlier installed
qualification binding. Final operator qualification occurs only after this tree is reviewed, clean, and committed; it
is not part of the source cutover.

## Consequences

* Good: admitted spike evidence has one auditable, immutable execution authority and no host fallback.
* Good: unavailable spike capability does not disable graph work, research, reads, or honest residual stopping.
* Good: requalification cannot silently change an existing run.
* Bad: a crash can cause duplicate external execution; probes must be bounded, offline, deterministic, and safe to
  execute at least once.
* Bad: installing a new plugin build invalidates old local qualification and requires a later operator action before new
  runs can bind qualified execution.
* Neutral: plugin `6.0.0`, wire `empirica/v2`, run storage `empirica.run/3`, and observer view
  `empirica-ui-view/v1` are independent version axes.

## Confirmation

The run/3, transaction, Docker harness, production composition, public contract, host parity, observer, and UI suites
prove exact binding, fail-closed execution, two-CAS admission, public closed shapes, and no run/2 mutation. The external
operator qualification remains a separate post-commit activity and is never run by these checks.
