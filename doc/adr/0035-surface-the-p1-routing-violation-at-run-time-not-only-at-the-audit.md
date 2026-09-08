---
number: 35
title: "Surface the P1 routing violation at run time, not only at the audit"
status: accepted
date: 2026-09-08
tags:
  - workflow
  - harness
  - usability
  - enforcement
links:
  - target: 20
    kind: Refines
  - target: 24
    kind: relatesto
  - target: 33
    kind: relatesto
---

# Surface the P1 routing violation at run time, not only at the audit

## Context and Problem Statement

P1 (route-before-investigate, ADR-20) is *witnessed, not gated*: the application records
`first_investigation_seq` and `route_seq` and derives a `violation` verdict when investigation came
first, but nothing blocks on it — fatality is deliberately delegated to the auditor (a coarse,
witnessed signal must never hard-block a run, ADR-20/24 §3.3).

Dogfooding exposed the cost of that being the *only* feedback: an authoring agent investigated on its
first tool call (before recording a route), and learned it had doomed the run **only at the terminal
audit** — after a full spike and an auditor spawn were already spent. The `route_stamp` hook
(`route_main`, `adapters/claude/lifecycle.py`) already receives, on that very first investigative
tool, an `investigate` response whose `run.route` fragment carries the exact `verdict` and
human-readable `reason` — and discards it. The signal exists at the moment of violation; it is simply
never shown.

A correction surfaced while designing the fix, and it is load-bearing: on Claude Code a **PreToolUse
hook that exits 0 sends stderr to the debug log only — "Claude never sees it"**
(code.claude.com/docs/en/hooks). The adapter's existing exit-0 advisory pattern
(`dispatch_main` → `print(advice, file=sys.stderr)`) is therefore a latent no-op: the model never
receives that dispatch advice. The channels that DO reach the model are
`UserPromptSubmit`/`UserPromptExpansion`/`SessionStart` plain stdout, and PreToolUse
`hookSpecificOutput.additionalContext` (JSON on stdout).

## Considered Options

- **Hard-gate P1 in the Stop/route hook.** Rejected: contradicts ADR-20/24 — a witnessed, possibly
  coarse ordering signal must not fail-close a run; only the auditor may make it fatal.
- **Warn via exit-0 stderr from the PreToolUse route hook** (mirror `dispatch_main`). Rejected on
  evidence: exit-0 PreToolUse stderr is invisible to the model, so the warning would not help it
  self-correct — the same trap the existing code already fell into.
- **Surface it through the model-visible channels, non-blocking.** Chosen.
- **Also inject a route-first reminder at run start (UserPromptExpansion stdout).** Considered as
  *prevention* (before any tool) and rejected: the skill is itself the run's expansion, so the model
  already receives run-start route-first guidance — a second copy is unproven marginal value, and
  emitting it broke the deliberately-silent activation hook (an existing lifecycle test asserts
  `run_start` produces no stdout). Prevention via a duplicated reminder was not worth breaking that
  invariant; the skill text carries the guidance instead.

## Decision Outcome

One model-visible early-detection surface plus one correctness fix, both non-blocking and keeping
every current exit code:

1. **Early detection (`route_main`, PreToolUse).** Capture the `investigate` response already
   returned by `dispatch_investigation`; when `run.route.verdict == "violation"`, emit
   `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": <reason + guidance>}}`
   on stdout at exit 0 (no `permissionDecision`, so the normal permission flow is untouched and the
   tool is not blocked). The model learns at the moment of violation, not at the audit.
2. **Correctness fix.** `dispatch_main`'s exit-0 dispatch advice is moved off the model-invisible
   stderr onto the same `additionalContext` channel, so advice that was silently dropped now reaches
   the model.

P1 remains non-gating and auditor-fatal (ADR-20 unchanged); this record only moves the *feedback*
earlier. No behavioural claim is made — only that the signal is emitted on a channel the model
provably receives; the emission is covered by adapter tests. `run_start` stays silent.
