> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

## Claims established

- **C1/C3/C8:** Pi adapter now has a structural obligations mirror, byte-pinned renderer and preservation checks iterating every fixture (`plugins/empirica/adapters/pi/src/obligations.ts:1-57`, `plugins/empirica/adapters/pi/test/obligations.test.ts:1-14`). Lane A freezes the source schema/API (`doc/design/reports/lane-a-sol-revision.md:45-78`).
- **C1:** Leading ADR-28 mode flags are parsed and unknown flags surfaced (`plugins/empirica/adapters/pi/src/translate.ts:22-39`); `/empirica` uses parsed goal/modes (`plugins/empirica/adapters/pi/src/index.ts:154-166`).
- **C1/C2:** `report_convergence`, `empirica_status`, and `empirica_knowledge` are registered through the structural `registerTool` subset; audit verdict is not a tool (`plugins/empirica/adapters/pi/src/index.ts:115-151`, `plugins/empirica/adapters/pi/src/pi-types.ts:18-31`).
- **C1/C4:** Configurable `subagent` interception sends the exact Claude knowledge-plane `audit_ticket` action shape (`plugins/empirica/adapters/claude/knowledge.py:182-188`) and fails closed on faults/transport errors (`plugins/empirica/adapters/pi/src/index.ts:221-231`). Tool interception is documented by the host at https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/docs/extensions.md, “Tool Events / tool_call”: it fires before execution and can block.
- **C5/C6:** Handle persistence/reconstruction uses `appendEntry`/`session_start`; custom compaction emits deterministic contract text (`plugins/empirica/adapters/pi/src/index.ts:103-114,153-156,253-262`). Host docs ground these APIs at the URL above, “Session Events / session_start” and “session_before_compact / session_compact”; `compaction.md`, “Custom Summarization via Extensions”, defines custom summary persistence.
- **C8/B1:** Run contract is widened under `run.contract` and terminal artifact ID (`plugins/empirica/adapters/pi/src/contract.ts:60-70`), and all Pi notices append `renderText(run.contract)` (`plugins/empirica/adapters/pi/src/translate.ts:120-210`).
- **C4:** The impossible gap and two UNVERIFIED runtime claims are recorded in proposed ADR-0040 (`doc/adr/0040-pi-adapter-parity-and-gaps.md:1-22`) and README (`plugins/empirica/adapters/pi/README.md:1-8`).

## Claims refuted

- The prior assumption that a gated tool name alone registered a model-callable tool is refuted by implementation; registration is now explicit (`plugins/empirica/adapters/pi/src/index.ts:115-151`).
- A hard completion veto on Pi remains unavailable; ADR-0040 explicitly names this gap (`doc/adr/0040-pi-adapter-parity-and-gaps.md:12-18`).

## Open/blocked (what was tried)

- `make empirica-pi-check` passed: 58 tests, typecheck, package validation, bridge smoke.
- `make check` was attempted but killed by the environment after 120 seconds; no tail was available. This is UNVERIFIED for full-repo green status.
- Blocked-reason model visibility and follow-up-turn reliability remain UNVERIFIED and intentionally require the parent’s live Pi spike. Spawn mechanisms outside Pi’s event stream remain ungated.
- The fixture-driven test validates renderer/preservation fixtures. Full TS semantic constructors/verify/project are intentionally not duplicated because the frozen public API requires the adapter’s structural subset and the lane write boundary excludes `lib/obligations`.

## Files changed

- `plugins/empirica/adapters/pi/src/obligations.ts`
- `plugins/empirica/adapters/pi/src/contract.ts`
- `plugins/empirica/adapters/pi/src/index.ts`
- `plugins/empirica/adapters/pi/src/pi-types.ts`
- `plugins/empirica/adapters/pi/src/translate.ts`
- `plugins/empirica/adapters/pi/test/obligations.test.ts`
- `plugins/empirica/adapters/pi/README.md`
- `doc/adr/0040-pi-adapter-parity-and-gaps.md`

## Requests to other lanes

- Parent/Lane B: ensure every Allow/Block run uses `run.contract` and terminal Allow includes `run.contract_artifact_id`; Pi now reads exactly those paths (`plugins/empirica/adapters/pi/src/contract.ts:60-70`).
- Parent: insert the following verbatim into SKILL.md Runtime boundary:

## SKILL.md Pi paragraph (for the parent to insert)

> **Pi runtime boundary:** The Pi adapter parses leading ADR-28 mode flags, persists and re-injects the opaque Empirica run handle through Pi session entries, and exposes the current `run.contract` view to the model through `empirica_status` and deterministic compaction summaries. It registers `report_convergence` as a real model-callable tool and enforces it at Pi’s `tool_call` boundary, failing closed on transport faults; it also exposes `empirica_knowledge` for the same typed ObserveAction payloads as the Claude adapter. A configured `subagent` tool is intercepted for spawn-budget/audit-ticket enforcement, but any spawn outside Pi’s event stream is un-gated. Pi has no completion-veto lifecycle: an agent that never invokes `report_convergence` can complete, so settled nudges are reminders rather than gates. Whether blocked reasons are visible in model context and whether follow-up delivery reliably starts another turn are UNVERIFIED pending a live runtime spike.

## Residual risks

- `make check` full-repo validation did not finish (environment killed it at 120s); parent must rerun.
- The current hand-written knowledge tool accepts a generic action object; its runtime schema remains structural because the host-neutral request protocol is open by design. Exact Claude action builders are cited above.
- Pi host runtime behavior for blocked-reason visibility/follow-up is UNVERIFIED by design.

```acceptance-report
{
  "criteriaSatisfied": [{"id":"criterion-1","status":"satisfied","evidence":"Pi-only implementation completed in the assigned adapter and ADR scope; deterministic Pi checks pass (58 tests)."}],
  "changedFiles":["plugins/empirica/adapters/pi/src/obligations.ts","plugins/empirica/adapters/pi/src/contract.ts","plugins/empirica/adapters/pi/src/index.ts","plugins/empirica/adapters/pi/src/pi-types.ts","plugins/empirica/adapters/pi/src/translate.ts","plugins/empirica/adapters/pi/test/obligations.test.ts","plugins/empirica/adapters/pi/README.md","doc/adr/0040-pi-adapter-parity-and-gaps.md"],
  "testsAddedOrUpdated":["plugins/empirica/adapters/pi/test/obligations.test.ts"],
  "commandsRun":[{"command":"make empirica-pi-check","result":"passed","summary":"58 tests passed; typecheck, package validation, and bridge smoke passed."},{"command":"make check","result":"failed","summary":"Environment killed the command after 120 seconds; full tail unavailable."}],
  "validationOutput":["make empirica-pi-check: 58 passed, 0 failed"],
  "residualRisks":["Full make check requires parent rerun; live Pi runtime claims remain UNVERIFIED; completion veto is impossible on Pi."],
  "noStagedFiles":true,
  "diffSummary":"Implemented Pi obligations projection/rendering, mode parsing, registered tools, spawn gate, session/compaction handling, contract propagation, tests, README, ADR-0040.",
  "reviewFindings":["no blockers in deterministic Pi checks"],
  "manualNotes":"Parent must insert the supplied SKILL.md paragraph and rerun make check. Runtime output path is authoritative."
}
```