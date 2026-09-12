---
number: 40
title: Pi adapter parity and named gaps
status: proposed
date: 2026-09-12
links:
- target: 30
  kind: relatesto
- target: 32
  kind: relatesto
---

# Pi adapter parity and named gaps

## Context and Problem Statement

Before this change, `plugins/empirica/adapters/pi/src/index.ts:101-220` parsed `/empirica` arguments as an undifferentiated goal, held the run handle only in extension memory, gated a tool name that it did not register, and had no knowledge tools, spawn interception, session reconstruction, or compaction restore. The workflow requires preservation of the handle and missing work across boundaries (`plugins/empirica/skills/empirica/SKILL.md:363-366`), while Pi exposes lifecycle and tool APIs documented in the extension host. The adapter must implement those capabilities without pretending Pi has a completion veto.

## Decision Drivers

* Preserve the lossless `run.contract` view at every Pi model/UI boundary.
* Enforce trusted convergence decisions at tool invocation and fail closed on transport faults.
* Keep knowledge action payloads closed to the exact Claude builders rather than accepting invented operations.
* Name host limitations and runtime uncertainties instead of claiming parity by prose.

## Considered Options

* **Settled-nudge only:** rejected; `agent_settled` is observational and cannot enforce an invoked operation.
* **Bash-only bridge access:** rejected; it leaves model-callable knowledge/status/convergence operations and subagent gates absent.
* **Full Pi lifecycle adapter:** selected; use registered tools, tool interception, session entries, model messages, and custom compaction.

## Decision Outcome

Implement mode parsing in `translate.ts:22-39` and apply it in `index.ts:154-166` (Pi command arguments are the raw string per the host ExtensionAPI command handler). Register `report_convergence`, `empirica_status`, and `empirica_knowledge` in `index.ts:115-151`; the first is still protected by `tool_call`. Validate knowledge kinds against the Claude builders (`plugins/empirica/adapters/claude/knowledge.py:35-44,65-67,172-191`; `route.py:71-83`) in `index.ts:33-34,137-139`. Intercept the configured subagent in `index.ts:221-231`, issue the `audit_ticket` ObserveAction, and fail closed on transport errors. Persist/reconstruct the handle with `appendEntry` and `session_start` (`index.ts:103-114,153-156`), expose the contract to the model, and return a deterministic contract section plus JSON details from `session_before_compact` (`index.ts:253-262`). Every translator notice appends `renderText(run.contract)` (`translate.ts:120-210`). The mirror and fixture loop are `obligations.ts:1-57` and `test/obligations.test.ts:1-14`.

The impossible gap is explicit: Pi has no completion-veto lifecycle. Enforcement exists only when `report_convergence` is invoked; an agent that never invokes it can complete. Two runtime claims remain UNVERIFIED and require a live spike: whether a blocked-tool reason is visible in model context, and whether follow-up delivery reliably starts another turn. A spawn outside Pi's event stream is likewise un-gated.

## Consequences

### Good

* The model receives the opaque handle and deterministic contract rather than counts or prose alone.
* Session and compaction boundaries preserve machine-readable contract details.
* Unknown knowledge actions are rejected before dispatch and bridge failures fail closed for gates.

### Bad

* Tool interception cannot cover hidden/external spawns or completion without tool invocation.
* The adapter maintains a structural TypeScript mirror and depends on the application to supply `run.contract`.
* Custom compaction and follow-up behavior still needs a live Pi runtime spike.

## Confirmation

Run `make empirica-pi-check` (including `plugins/empirica/adapters/pi/test/obligations.test.ts`, `test/translate.test.ts`, `test/gate.test.ts`, `test/lifecycle.test.ts`, and `test/registration.test.ts`) and `make adr-check`. The fixture test enumerates all 21 JSON fixtures and asserts exact renderer bytes for every fixture carrying `expect.text`; preservation fixtures are asserted separately.
