---
number: 42
title: "Complete Empirica v2 through public tools and host-owned audit bindings"
status: proposed
date: 2026-09-13
tags: [empirica, hosts, audit, mcp, pi, codex]
links:
  - target: 30
    kind: Depends on
  - target: 40
    kind: Amends
  - target: 41
    kind: Amends
---

# Complete Empirica v2 through public tools and host-owned audit bindings

## Context and Problem Statement

The strict-v2 core could complete the full convergence protocol, but no advertised host could reach
that positive path. Direct application conformance proved policy while Claude lacked several public
author operations, Pi exposed diagnostics only, and Codex remained observational. Shipping that
state as 2.0 would mistake an internal protocol for an executable product.

## Decision Drivers

* Claude Code, Pi, and Codex must each reach `Allow(converged=true)` from a fresh invocation.
* Public model operations must remain distinct from trusted host ingress.
* Deterministic harness exit status remains the sole machine approver.
* Audit output must be bound to one host-observed execution and exact current dossier.
* Host limitations are represented as exact foreground-only profiles, never silent async fallback.
* The public schema is mechanically projected from the canonical v2 contract, not rewritten per host.

## Considered Options

1. Ship Claude-only and advertise Pi/Codex as typed unsupported (rejected: all three are release requirements).
2. Restore the generic v1 knowledge tool (rejected: it mixed author actions with trusted ingress).
3. Add one canonical public surface plus host-owned audit bindings (chosen).
4. Let models submit audit verdicts through MCP (rejected: the author would grade itself).

## Decision Outcome

All hosts expose equivalent public semantics through `empirica_observe`, `empirica_read`, and
`report_convergence`. Their schemas admit only PublicContract author actions. Activation owns
`StartRun`, exact profile binding, request correlation, and the opaque handle.

Claude uses its existing Agent/`SubagentStop` hooks to inject and observe a foreground auditor. Pi
bundles `pi-subagents@0.50.0` and a package-scoped auditor, correlates the foreground result by
`toolCallId`, and redacts the verdict before awaiting private ingress. Codex 0.146.0 cannot observe
an arbitrary native child's final output, so its trusted Stop adapter owns a bounded read-only
`codex exec` auditor, privately admits the exact final verdict, and re-evaluates before permitting
completion.

`evidence_leaf`, attribution, child events, and audit verdicts are absent from every public tool
schema. Same-model or unverified attribution remains visible and blocks according to the canonical
contract. Async execution remains unsupported for these exact profiles.

Private ingress is a host-adapter trust boundary, not an operating-system capability boundary. It is
never registered as a model tool and requires closed schema-valid payloads, but a process with the
same filesystem and execution authority as the host can invoke adapter code. Host sandboxing and
OS credentials remain outside the Empirica protocol's threat model.

## Consequences

* Good, because every advertised host has deterministic adapter conformance below the installed-host boundary.
* Good, because one public schema and one bridge replace host-specific author builders.
* Good, because Codex's real hook limitation is handled by an explicit adapter-owned process rather
  than fabricated native child observation.
* Good, because Pi installation bundles both its required child runtime and auditor definition.
* Bad, because Codex starts another model process at Stop and therefore adds latency and model cost.
* Bad, because Pi still lacks a native completion veto; agents must invoke the guarded report tool.
* Bad, because foreground-only execution does not survive arbitrary host termination mid-audit.

## Confirmation

`make empirica-host-adapter-check` drives public tools and each production adapter through a
fully deterministic trace with fake native execution. It proves translation and policy composition,
not installed-host reachability. `make empirica-host-live-check` separately requires retained
credentialed receipts from installed Claude, Pi, and Codex processes, each ending in
`Allow(converged=true)` with the exact bound child transition trace. `make release-check` requires
both the architecture gate and those live receipts; ordinary `make check` remains deterministic.
