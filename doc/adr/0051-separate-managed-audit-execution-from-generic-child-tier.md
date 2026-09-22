---
number: 51
title: "Separate managed-audit execution from the generic child tier"
status: accepted
date: 2026-09-21
tags: [empirica, audit, children, claude, pi, async, strict-v2]
links:
  - target: 50
    kind: Refines
  - target: 42
    kind: Amends
---

# Separate managed-audit execution from the generic child tier

## Context and Problem Statement

Claude Code 2.1.278 runs plugin agents asynchronously even when Empirica's PreToolUse hook rewrites
`run_in_background` to false. Empirica still registered Claude as foreground-only and mapped every
nonterminal `audit.pending` decision to a blocking Stop result. The parent therefore reached Stop
while its one bound auditor was pending, repeatedly received spawn-oriented completion pressure,
and could neither synchronously wait nor legitimately reserve another audit. Pi's packaged auditor,
by contrast, remains a foreground `pi-subagents` call whose terminal tool result is captured before
the parent resumes.

## Decision Drivers

* Never respawn while one current audit is reserved, launching, or pending.
* Let Claude's parent turn settle without terminalizing or converging the run.
* Preserve SubagentStop as the only Claude terminal/verdict ingress.
* Preserve Pi's foreground tool-result capture and rejection of async audit overrides.
* Do not promote Claude to generic `full_async` without generic child cancellation, timeout,
  retained-output, and correlation evidence.
* Keep the architecture ceiling at 9,484 effective runtime lines.

## Considered Options

1. Keep forcing Claude foreground execution (rejected: the observed host ignores the rewrite).
2. Block Stop while an async audit is pending (rejected: this creates a host-level reasoning loop).
3. Promote Claude to generic `full_async` (rejected: managed audit evidence does not establish
   generic child capability).
4. Declare managed-audit execution separately from the generic child tier (chosen).

## Decision Outcome

Every exact host profile declares `audit_execution` as `async`, `foreground`, or `unavailable`,
orthogonal to its generic child tier. The shared audit protocol reserves the child with that exact
execution mode. Core admission permits an async reservation on a non-`full_async` profile only when
the immutable resource class is `audit` and the registered audit mode is `async`; ordinary async
children remain blocked with `host.async_unsupported`.

Claude explicitly launches the canonical auditor in background mode. A Stop response settles the
parent turn only when it is exactly one `audit.pending` Block over an active run containing exactly
one pending audit-class child with a concrete ID. The typed Block is returned as context; the run
remains active and non-converged. Every malformed, mixed-reason, wrong-class, missing-child, fault,
or other Block remains fail-closed. Claude's native completion notification resumes the session,
and SubagentStop remains the only path that observes identity and admits the exact child's terminal
verdict.

Pi continues to force `async=false` for the canonical auditor and captures its completed native
result before the parent resumes. Process interruption may still create durable orphan recovery,
but normal Pi audit execution does not use Claude's parent-settlement path. Codex remains
unavailable for supported audit convergence.

## Consequences

* Good: Claude no longer loops at Stop while its one legitimate auditor is pending.
* Good: pending settlement cannot mark a run terminal or converged and cannot authorize respawn.
* Good: Pi retains simpler foreground behavior and its existing trusted tool-result boundary.
* Good: managed async audit does not overclaim generic `full_async` support.
* Cost: host-profile data and the shared protocol now carry a separate audit-execution fact.
* Cost: installed-host certification must exercise async launch, parent settlement, completion
  notification, SubagentStop admission, and resumed guarded convergence.
* The runtime reaches the approved 9,484 / 9,484-line ceiling; implementation growth is offset by
  documentation in the modified Claude completion module, not by weakening checks or deleting an
  unrelated invariant.

## Confirmation

Required deterministic checks are the Claude adapter suite, the shared audit protocol suite, D7
transactions, host-neutral conformance, Pi adapter suite, contract/vendor parity, architecture
validation, and full `make check`. Installed-host confirmation must use Claude Code's real async
Agent lifecycle and Pi's real foreground `pi-subagents` lifecycle. Development traces are not
release receipts, and this decision authorizes neither commit nor publication.
