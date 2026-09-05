---
number: 32
title: "Port Claude completion gating to a gated domain operation on Pi"
status: accepted
date: 2026-09-04
tags:
  - pi
  - adapters
  - convergence
links:
  - target: 8
    kind: Amends
  - target: 30
    kind: Depends on
---

# Port Claude completion gating to a gated domain operation on Pi

## Context and Problem Statement

Claude Code can block a Stop hook with exit code 2 and continue the agent loop. Pi extensions can
block tool calls, but `agent_end` and `agent_settled` are observational; they cannot veto completion.
Pretending these lifecycles are equivalent would make the Pi port weaker while presenting the same
guarantee.

## Decision Drivers

* Convergence must have one definition across hosts.
* A host capability gap must be reported honestly.
* Pi should enforce the claim at the point where it can actually deny an operation.

## Decision Outcome

`EvaluateRun(intent: "report_convergence")` is the host-neutral gate. A run may report convergence
only through this operation.

The Claude adapter invokes it from Stop and maps `Block` to exit 2. The Pi adapter exposes a
`report_convergence` tool/command and blocks that tool when the core returns `Block`. At
`agent_settled`, Pi also evaluates the run and may enqueue a follow-up message explaining outstanding
work, but that nudge is not described as a hard completion gate.

Pi adapter spikes must verify whether blocked-tool reasons are included in model context and whether
an extension follow-up reliably starts another turn. Until verified, these are usability mechanisms,
not trust claims.

Methodologist has no equivalent hard-gate requirement. Its Pi adapter registers `/think`, contributes
the host-neutral methodology resources, and renders phase progress using Pi capabilities rather than
Claude task tools.

### Consequences

* Good, because the enforceable guarantee is identical: no successful convergence report without an
  `Allow` decision.
* Good, because Pi's observational lifecycle is not overstated.
* Bad, because an agent can end without invoking the report tool; the run remains explicitly active
  and non-converged rather than being silently certified.
* Bad, because two Pi behaviors require executable spikes before the UX is finalized.

### Confirmation

Shared fixtures must return identical domain decisions through both adapters. Pi integration tests
must separately label tool blocking as enforced and settled-turn follow-up as observed.
