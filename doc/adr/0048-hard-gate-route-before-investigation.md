---
number: 48
title: "Hard-gate route before investigation"
status: accepted
date: 2026-09-21
tags: [empirica, routing, enforcement, claude, pi]
links:
  - target: 20
    kind: Refines
  - target: 35
    kind: Supersedes
  - target: 42
    kind: Amends
---

# Hard-gate route before investigation

## Context and Problem Statement

Empirica exposed first-write route and investigation stamps, but they were advisory. Research,
spikes, child execution, trusted audit facts, and convergence could proceed without them. Claude's
native investigation hook discarded the application decision and failed open; Pi gated only the
convergence tool and child reservation. An agent could therefore complete most of a run before
learning that route order was absent.

ADR 35 deliberately kept routing non-gating because its historical signal was coarse. Strict v2 now
has exact CAS-ordered witnesses, public actions, fixed-safe corruption handling, and supported native
pre-tool enforcement. The former tradeoff no longer applies.

## Decision Drivers

* No evidence, child budget, harness execution, or convergence may precede routing.
* A denied native tool must not execute.
* Candidate rejection must be zero-write and action-local.
* Persisted evidence inconsistent with witnesses must be corruption, not repairable ordering.
* Graph/configuration/freeze preparation and honest stop remain available without investigation.
* Claude and Pi must demonstrate the same behavior in real interactive sessions.

## Considered Options

1. Keep advisory feedback and rely on audit (rejected: detects failure after resources are spent).
2. Gate only evidence submissions (rejected: native reads/searches/commands can investigate first).
3. Gate native investigation and every evidence/execution admission with strict persisted witnesses
   (chosen).
4. Automatically invent a route from the first tool call (rejected: destroys route-before-observation).

## Decision Outcome

A valid active run progresses through explicit `route` then `investigate` actions. Research, spike
request/result, every executable child reservation, trusted attribution/verdict, and convergence
require both witnesses. Missing route yields `route.required`; route without investigation yields
`investigation.required`. Rejection precedes artifacts, child/budget mutation, harness execution,
manifest append, and CAS. Explicit honest stop remains available.

Route and investigation stamps are exact integers, positive, and strictly ordered:

```text
route_stamp < investigation_stamp <= stamp_seq
```

Investigation implies route. Children and converged state require investigation. Every persisted
research, spike request/result, attribution, and audit verdict carries the exact integer
route/investigation stamp pair; missing, boolean, or conflicting witnesses make reachable history
`run.corrupt` with fixed-safe, zero-write handling across public ingress, trusted mutation, and
trusted audit-dossier reads.

Claude's `PreToolUse` hook denies investigative native tools when the core denies or is unavailable.
Agent launch records investigation before reservation. Untrusted Bash marker text cannot exempt a
command. Pi records investigation in `tool_call` before native tools, evidence-producing public
tools, or executable subagents; management calls and public reads/reporting remain non-investigative. The Pi pre-tool gate preserves
an explicit honest-stop intent and its guarded response is consumed exactly once by tool execution.
A verified converged/stopped response retires the in-memory handle and appends a terminal marker, so
later native tools and restored sessions do not mistake a terminal run for unavailable active state.
Unknown, malformed, or unavailable responses still fail closed.

The checkout Pi development profile suppresses the separately installed global `pi-subagents`
extension so the candidate-bundled exact `0.50.0` extension is loaded once. Other global packages
remain unchanged.

## Consequences

* Good, because route order is enforced where work would actually begin.
* Good, because rejected work spends no evidence, child, budget, harness, manifest, or state writes.
* Good, because selected persisted history cannot claim route order without matching witnesses.
* Good, because Claude and Pi share one host-neutral admission policy.
* Bad, because strict pre-field/inconsistent histories require a fresh run.
* Bad, because every supported host adapter must classify and intercept native investigative tools.
* Bad, because interactive failed-first probes can leave an active no-graph run whose Stop hook keeps
  refusing ordinary turn completion until the run is honestly stopped or discarded.

## Confirmation

Host-neutral tests cover unrouted, route-only, and routed+investigating research/child admission;
spike and convergence gates; strict stamp ordering; zero-write/harness rejection; and fixed-safe
persisted evidence mismatch. Claude tests cover deny/allow/unavailable native hooks and marker
non-bypass. Pi tests cover native dispatch and denial before execution. Interactive Orca-driven
Claude Code 2.1.278 and Pi 0.84.1 + pi-subagents 0.50.0 sessions both denied a native package read
with `Record a route before investigation.`, then admitted the same read after public route and
investigate actions with both obligations satisfied.
