---
number: 8
title: Enforce the convergence loop with Claude Code hooks
status: accepted
date: 2026-07-17
tags:
  - architecture
  - hooks
links:
  - target: 7
    kind: Depends on
  - target: 9
    kind: Depended on by
  - target: 13
    kind: Depended on by
  - target: 14
    kind: Depended on by
  - target: 16
    kind: Depended on by
---

# Enforce the convergence loop with Claude Code hooks

## Context and Problem Statement

The user requirement mandates the loop run "until there are no unknowns left." Relying on
the model to remember to keep looping is non-deterministic — it may declare done while
unknowns remain. Can Claude Code hooks make the loop non-optional, and can state be
re-injected after context compaction? This assumption was load-bearing and unverified, so
it was checked against the actual hooks documentation before acceptance.

## Decision Drivers

* Determinism: convergence must be enforced by the harness, not model goodwill.
* Durability: the ledger must re-enter context after compaction (ADR-7).
* Assume-broken-until-proven: verify hook capabilities against docs, not training data.

## Considered Options

* **Model-driven loop** — SKILL.md instructs the model to loop; no enforcement.
* **Hook-enforced loop** — a `Stop` hook reads the ledger and blocks completion while
  `converged()` is false; a `SessionStart` (matcher `compact`) hook re-injects the ledger
  after compaction.

## Decision Outcome

Chosen option: "Hook-enforced loop", because verification confirmed the mechanism exists:
a `Stop` hook blocks by writing the reason to **stderr and exiting with code 2** (Claude
reads the stderr and continues). The spike (build step 1) confirmed this is the mechanism
the current Stop spec honors; a stdout top-level `decision: block` field is NOT honored for
Stop and must not be relied on. Hooks receive `cwd` on stdin, so a Stop hook can read the
spec-hosted unknowns it needs. Plugins ship hooks via `hooks/hooks.json` (top-level `hooks`
key → event name → entries; exec form `command` + `args`), scripts referenced with
`${CLAUDE_PLUGIN_ROOT}`. Verified against code.claude.com/docs/en/hooks.md and
plugins-reference.md, and proven end-to-end by the spike at `.claude/spike-m3` (checks A6–A9).

Two corrections from verification are folded in:
1. Context re-injection is NOT `PreCompact` (fires before compaction, no injection path).
   The correct event is `SessionStart` with `matcher: "compact"`, whose stdout is added to
   context after compaction. `UserPromptSubmit` → `hookSpecificOutput.additionalContext`
   (must be nested, not top-level) is the per-prompt injection path.
2. A `Stop` hook is force-overridden after 8 consecutive blocks without progress
   (`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` raises it; input carries `stop_hook_active` so the
   hook knows it is already looping). This makes an unbounded single-session loop
   impossible by platform design — see ADR-9.

### Consequences

* Good, because convergence is enforced deterministically, independent of model memory.
* Good, because ledger durability across compaction is a documented, working path.
* Bad, because the 8-block cap forbids single-session unbounded loops — the design must be
  durable-resumable across turns rather than blocking one turn open (ADR-9).

### Confirmation

Fitness function: with the plugin installed, ending a turn while the ledger has sub-θ
unknowns triggers the Stop hook and Claude continues; after a forced compaction the ledger
content reappears in context. Manual test: seed an unknown, attempt to stop, observe the
block; force compaction, observe re-injection.

## More Information

Verified by a claude-code-guide research pass over the current hooks docs, then proven by
the build-step-1 spike (`.claude/spike-m3`, 15/15 checks): the Python Stop hook parses the
spec's inline confidences, exits 2 with a stderr reason while unconverged, exits 0 once all
unknowns ≥ θ, and `SessionStart:compact` re-injects the unknowns after compaction. The block
cap directly motivates the termination design in ADR-9.
