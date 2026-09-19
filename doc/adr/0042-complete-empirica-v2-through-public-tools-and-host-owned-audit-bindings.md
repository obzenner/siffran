---
number: 42
title: "Complete Empirica v2 through public tools and host-owned audit bindings"
status: accepted
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

* Every promoted release profile must reach `Allow(converged=true)` from a fresh invocation; an
  advertised but incapable profile must be explicitly WIP/unsupported.
* Public model operations must remain distinct from trusted host ingress.
* Deterministic harness exit status remains the sole machine approver.
* Audit output must be bound to one host-observed execution and exact current dossier.
* Host limitations are represented as exact foreground-only profiles, never silent async fallback.
* The public schema is mechanically projected from the canonical v2 contract, not rewritten per host.

## Considered Options

1. Ship only the internal protocol and call adapters future work (rejected: no executable product).
2. Restore the generic v1 knowledge tool (rejected: it mixed author actions with trusted ingress).
3. Add one canonical public surface plus exact host-owned audit bindings and fail-closed capability
   profiles (chosen).
4. Let models submit audit verdicts through MCP (rejected: the author would grade itself).

## Decision Outcome

All hosts expose equivalent public semantics through `empirica_observe`, `empirica_read`, and
`report_convergence`. Their schemas admit only PublicContract author actions. Activation owns
`StartRun`, exact profile binding, request correlation, and the opaque handle.

Claude uses its existing Agent/`SubagentStop` hooks to inject and observe a foreground auditor. Pi
bundles `pi-subagents@0.50.0` and a package-scoped auditor, correlates the foreground result by
`toolCallId`, and redacts the verdict before awaiting private ingress. Codex 0.146.0 cannot observe
the resolved auditor-model identity behind its managed process. Its bounded adapter remains useful
for deterministic conformance, but attribution stays unverified and convergence blocks; the profile
is explicitly `observational` and `wip_unsupported`.

`evidence_leaf`, attribution, child events, and audit verdicts are absent from every public tool
schema. Same-model or unverified attribution remains visible and blocks according to the canonical
contract. Async execution remains unsupported for these exact profiles.

Private ingress is a host-adapter trust boundary, not an operating-system capability boundary. It is
never registered as a model tool and requires closed schema-valid payloads, but a process with the
same filesystem and execution authority as the host can invoke adapter code. Host sandboxing and
OS credentials remain outside the Empirica protocol's threat model.

## Consequences

* Good, because each promoted profile has deterministic conformance plus an installed-host release
  gate, while incapable profiles are explicit.
* Good, because one public schema and one bridge replace host-specific author builders.
* Good, because Codex's real hook limitation is handled by an explicit adapter-owned process rather
  than fabricated native child observation.
* Good, because Pi installation bundles both its required child runtime and auditor definition.
* Bad, because Codex convergence remains unavailable until the host exposes resolved-model identity.
* Bad, because Pi still lacks a native completion veto; agents must invoke the guarded report tool.
* Bad, because foreground-only execution does not survive arbitrary host termination mid-audit.

## Confirmation

`make empirica-host-adapter-check` drives public tools and each production adapter through a
fully deterministic trace with fake native execution. It proves translation and policy composition,
not installed-host reachability. `make empirica-host-live-check` separately requires
operator-attested, candidate-bound structural receipts from the exact supported Claude and Pi
foreground profiles. The verifier parses retained native parent/child JSONL and durable state,
correlates the report result and bound audit child/operation/identity/verdict, and checks retained
file digests plus the release commit and plugin version. The trusted release operator is the trust
root; these receipts are not signatures or proof against operator fabrication. Codex is excluded
while its resolved auditor identity remains unobservable. `make release-check` requires the
architecture gate and those supported-host receipts; ordinary `make check` remains deterministic.
