---
number: 36
title: "Make the doctor mode-aware, and state what multi_provider does not do"
status: accepted
date: 2026-09-08
tags:
  - workflow
  - harness
  - usability
  - configuration
links:
  - target: 24
    kind: Refines
  - target: 28
    kind: relatesto
---

# Make the doctor mode-aware, and state what multi_provider does not do

## Context and Problem Statement

`--multi-provider` (ADR-24/28) parses and persists correctly — a dogfood run recorded
`modes.multi_provider: true` in its operational document — yet had **no observable effect**, silently.
Two proven causes:

1. `make doctor` runs `preflight.main()`, which calls `diagnose({})` with no invocation and no run
   snapshot. `diagnose` already prefers `invocation.modes`, then a run snapshot, then default — but
   given neither, it can only ever report every mode OFF. So the one tool a user runs to ask "what
   can this machine reach?" answers as if no mode were set, regardless of what they typed.
2. `multi_provider` is read for behaviour in exactly one place — `preflight.py`, the doctor's
   probe gate. **Nothing consumes `run.modes.multi_provider` at dispatch or audit time** (dispatch
   keys on `cli_exec`). The flag's entire runtime meaning is "let the doctor probe codex/pi and allow
   external actors" — it does not itself route the auditor to another provider.

The silence is the defect: a user reasonably expects `--multi-provider` to change *something* they can
see, and nothing tells them what it does or does not do.

## Considered Options

- **Route the auditor to codex/pi when `multi_provider` is on.** The behaviour users likely imagine,
  but a much larger change: it needs provider enablement, reachability, and `cli_exec` for witnessed
  attribution, and it changes the trust story of the audit. Deferred to its own ADR.
- **Leave doctor standalone-only and document the gap.** Rejected: keeps the silent, misleading
  "off" report.
- **Make the doctor reflect the modes a run would actually use, and state the flag's true scope.**
  Chosen — small, honest, and uses plumbing that already exists.

## Decision Outcome

- `preflight.main()` parses `--multi-provider`/`--cli-exec` (and honours `EMPIRICA_MODE_*`, already
  handled by `parse_invocation`) into an `Invocation` and passes it to `diagnose`, so
  `make doctor ARGS="--multi-provider"` (or the env var) probes codex/pi and reports the modes the
  run would see. Bare `make doctor` is unchanged.
- The doctor's recommendation text and the skill state plainly that `multi_provider` *allows and
  probes* external actors but does **not** route the audit to them by itself.

Routing the audit across providers remains future work under a separate decision. This record makes
the mode honest and inspectable; it does not widen its runtime effect.
