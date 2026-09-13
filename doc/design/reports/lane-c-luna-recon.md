> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

## Claims established

### C1 — Pi can hard-block a tool call, but cannot hard-veto turn completion
- **Statement:** A Pi extension can return `{block: true, reason}` from `tool_call`, so a registered convergence-report tool can be hard-gated. Pi's `agent_end`/`agent_settled` lifecycle does not provide a completion-veto return; `agent_settled` must remain observational. This is also the adopted repository decision.
- **Evidence:** `plugins/empirica/adapters/pi/src/index.ts:178-201` currently fails closed for the configured `report_convergence` tool; `plugins/empirica/adapters/pi/src/index.ts:203-220` explicitly labels settled observational. `plugins/empirica/adapters/pi/src/pi-types.ts:60-80` models blocking only for `tool_call` and void for `agent_settled`. `doc/adr/0032-port-claude-completion-gating-to-a-gated-domain-operation-on-pi.md:21-24,34-44` says Claude Stop can block but Pi settled/end cannot veto. Existing test run confirms the current tool gate (`make empirica-pi-check`, 37 passed).
- **Host evidence:** https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/docs/extensions.md — **Tool Events / `tool_call`**: “Fired ... before the tool executes. **Can block**”; return values control blocking with `{ block: true, reason?, terminate? }`. **Agent Events / `agent_start / agent_end / agent_settled`**: `agent_settled` is for knowing Pi will not continue automatically. Therefore a hard Stop equivalent is **impossible on Pi v1**; only the report tool operation is enforceable.

### C2 — Compaction has two hooks; custom summary is the reliable model-visible injection point
- **Statement:** `/compact` and automatic compaction fire `session_before_compact`, then `session_compact`. The before hook can cancel or return a custom summary containing obligations; it can persist custom JSON `details`. The after hook is notification-only in the documented event shape. A `session_start` hook handles startup/resume/new/fork, not a distinct “compaction restore” event.
- **Evidence:** `plugins/empirica/adapters/pi/src/index.ts:203-220` currently registers neither compaction nor session-start. `plugins/empirica/skills/empirica/SKILL.md:363-366` promises graph/missing-fold reinjection, while current Pi code has no such path (absence is a repo observation; the absence claim is based on complete source read). `plugins/empirica/adapters/pi/src/pi-types.ts:91-104` omits both events and message APIs, so its structural subset must grow.
- **Host evidence:** https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/docs/extensions.md — **Session Events / `session_before_compact / session_compact`**: before hook can return `{ compaction: { summary, firstKeptEntryId, tokensBefore, usage? } }`; after hook receives `event.compactionEntry`, `fromExtension`, `reason`, `willRetry`. https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/docs/compaction.md — **Custom Summarization via Extensions**: `preparation` exposes messages/kept boundary and the returned summary is stored as a `CompactionEntry`; `details` is JSON-serializable implementation data. The obligations must be included in the custom summary text (model-visible), with a machine-readable obligations payload in `details`; `session_compact` can trigger a follow-up/context injection but should not be treated as the sole persistence mechanism.
- **Classification:** observational hook (`session_compact`); custom-summary path is a durable projection mechanism, not a hard gate. `session_before_compact` can cancel compaction, but cancellation is not a convergence veto.

### C3 — Custom model-callable tools are supported, but the current adapter registers none
- **Statement:** Pi supports `pi.registerTool()` for model-callable tools. The current adapter only registers commands and event handlers; its `gatedTools` set names `report_convergence` but no tool registration exists.
- **Evidence:** `plugins/empirica/adapters/pi/src/index.ts:98,153-176` gates a tool name and registers only `/report-convergence`; no `registerTool` call appears in the complete file. `plugins/empirica/adapters/pi/src/pi-types.ts:91-104` has no `registerTool` member. Brief verified fact: `plugins/empirica/adapters/pi/src/index.ts` (absence).
- **Host evidence:** https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/docs/extensions.md — **ExtensionAPI Methods / `pi.registerTool(definition)`**: “Register a custom tool callable by the LLM”; **Custom Tools / Tool Definition** specifies `name`, `description`, `parameters`, and `execute(...)` returning content/details. Thus register `report_convergence`, `submit_knowledge` (or the contract-approved knowledge operation), and `stamp_route` tools. Each execute function should dispatch the corresponding typed request and return structured details plus human-readable content.
- **Classification:** model-callable tools are available; their execution is a hard gate only where `tool_call` interception denies. Knowledge and route tools are not intrinsically gates unless the core operation says so.

### C4 — `tool_call` can intercept `subagent`, but current contract/adapter lacks the ticket operation
- **Statement:** A `tool_call` handler can identify `event.toolName === "subagent"`, inspect mutable `event.input`, and return a block. This is sufficient for a spawn-budget gate at the Pi boundary. Issuing an audit ticket requires a host-neutral request operation; current TS `Command` has no dedicated spawn-ticket command, so the adapter must use the contract shape Lane A/B provide (likely `ObserveAction` with an explicit spawn/audit action) rather than inventing a local ticket.
- **Evidence:** `plugins/empirica/adapters/pi/src/pi-types.ts:53-67` supplies `toolName`, `toolCallId`, mutable input, and block result. `plugins/empirica/adapters/pi/src/index.ts:182-200` shows the existing interception pattern. `plugins/empirica/adapters/pi/src/contract.ts:34-39` has only generic `ObserveAction`; `plugins/empirica/adapters/pi/src/contract.ts:59-63` enumerates no spawn-ticket command. `plugins/empirica/skills/empirica/SKILL.md:47-49` says spawn budget is denied at the boundary, and `SKILL.md:451-460` requires a real audit spawn/ticket before convergence.
- **Host evidence:** https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/docs/extensions.md — **Tool Events / `tool_call`**: event has `toolName`, `toolCallId`, mutable `input`; mutations affect execution; return `{ block, reason, terminate }` controls blocking. The docs also list `examples/extensions/subagent/` as a spawn-agent extension, confirming the tool is extension-defined rather than a universal fixed built-in.
- **Classification:** hard gate for the actual `subagent` call (if registered/observable); ticket issuance is a trusted observational request to the core. If the active subagent implementation uses another tool name or hides spawn execution outside Pi's event stream, enforcement is **UNVERIFIED/impossible on that path**.

### C5 — Run handle is currently extension-only; Pi can expose and persist it
- **Statement:** Current code sets `runHandle` from the StartRun response but never sends it to the model; `/empirica-status` only displays it indirectly in a human UI notice. Pi can make it model-visible with `pi.sendMessage()` or `before_agent_start`'s returned persistent message, and can preserve it across reload/resume with `pi.appendEntry()` plus `session_start` reconstruction.
- **Evidence:** `plugins/empirica/adapters/pi/src/index.ts:102-104,118-125` proves in-memory assignment and notify-only behavior. `plugins/empirica/adapters/pi/src/contract.ts:90-102` exposes `run.id` in Allow/Block, but there is no model-facing projection. `plugins/empirica/skills/empirica/SKILL.md:101-104` requires the handle to be consumed through the adapter API, not inferred from runtime files.
- **Host evidence:** https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/docs/extensions.md — **ExtensionAPI Methods / `pi.sendMessage(message, options?)`**: custom messages participate in LLM context; **`pi.appendEntry(customType, data?)`** persists extension data but custom entries do not participate in LLM context; **Agent Events / `before_agent_start`** can return a persistent message injected into the session/model context; **Session Events / `session_start`** exposes `ctx.sessionManager.getEntries()` for reconstruction. Recommended sequence: append `{runHandle}` custom entry, then send a compact model-visible “Empirica run handle/status” message; on `session_start`, read latest entry and re-inject the current snapshot/obligations.
- **Classification:** context projection/observational persistence, not a hard gate. `/empirica-status` should be expanded to show the handle and obligations to both UI and model (e.g. `pi.sendMessage` with `deliverAs: "nextTurn"` or a status tool result).

### C6 — Slash command args arrive as the raw string after the command name
- **Statement:** A command handler receives `args: string`; current `/empirica` does only `args.trim()`, so mode flags become part of the goal and ADR-28 is violated on Pi.
- **Evidence:** `plugins/empirica/adapters/pi/src/index.ts:113-120` takes `args` and assigns `args.trim()` directly to `goal`. `doc/adr/0028-set-run-modes-from-the-invocation-parsed-by-the-harness.md:34-43,79-85` requires flags to be parsed from invocation before the agent can silently drop them. `plugins/empirica/skills/empirica/SKILL.md:96-104` says leading flags are modes and must be stripped before reading the goal.
- **Host evidence:** https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/docs/extensions.md — **ExtensionAPI Methods / `pi.registerCommand(name, options)`**: the handler signature is `handler: async (args, ctx)`, and the example uses `args` as command arguments. Implement a pure `parseModeFlags(args)` that consumes leading `--cli-exec`, `--multi-provider`, `--no-cli-exec`, `--no-multi-provider`, returns `{ goal, modes, unknownFlags }`; pass merged modes into `startRunRequest`; notify unknown flags plainly and do not enable them.

### C7 — Existing adapter has a broad parity gap beyond the listed Pi v1 items
- **Statement:** It contributes skills, starts/statuses/gates a report command/tool name, and emits a settled nudge, but does not implement mode parsing, model-callable tool registration, run-handle projection/persistence, spawn interception/ticketing, compaction/session-start restore, knowledge submission, route stamp, or obligations rendering.
- **Evidence:** `plugins/empirica/adapters/pi/src/index.ts:108-220` contains only `resources_discover`, three commands, `tool_call`, and `agent_settled`. `plugins/empirica/adapters/pi/src/pi-types.ts:91-104` confirms the missing registration/message/session APIs. `plugins/empirica/skills/empirica/SKILL.md:106-111` describes runtime adapters only for Claude/Codex, with no Pi paragraph. `plugins/empirica/adapters/pi/src/translate.ts:114-185` renders reason/status text only; no obligations field is read or emitted.
- **Classification:** mode parsing, model-facing handle, registered tools, `tool_call` spawn gate, and compaction/session restoration are implementable Pi capabilities. Route/knowledge are implementable only once core request operations are exposed. Hard Stop veto remains impossible.

### C8 — Obligations mirror and rendering shape
- **Statement:** Add `src/obligations.ts` as a strict structural mirror of `contracts/obligations/v1` fixtures, then have `translate.ts` render the same lossless `obligations[]` array in Block denials, convergence notices, and settled follow-up nudges (and status notices to satisfy “every notice”).
- **Proposed types (must be checked against Lane A fixtures; do not silently diverge):**
  ```ts
  export type ObligationState =
    | "open" | "partial" | "discharged" | "violated" | "blocked" | "deferred";
  export type WitnessKind =
    | "test" | "exit_code" | "event" | "artifact" | "predicate" | "judgment";
  export interface Witness { kind: WitnessKind; ref: string; expect: unknown; }
  export interface Obligation {
    id: string; must: string; must_not?: string;
    witnesses: Witness[]; state: ObligationState;
    because: unknown[]; severity?: string;
  }
  export type ObservationOutcome = "pass" | "fail";
  export interface Observation {
    kind: WitnessKind; ref: string; outcome: ObservationOutcome;
    source: string; at: string; payload?: unknown;
  }
  export interface Contract {
    contract_id: string; revision: number; parent_revision?: number;
    obligations: Obligation[]; provenance: unknown[];
  }
  export interface Verdict {
    satisfied: Obligation[]; violated: Obligation[]; residual: Obligation[];
    unwitnessed: Obligation[];
  }
  ```
  `expect` and `because` are intentionally opaque; v1 has exact `(kind, ref)` matching and no predicate evaluator. If the schemas choose a narrower JSON type for `expect`, `at`, `provenance`, or verdict members, mirror the schema exactly rather than this provisional broad typing.
- **Concrete adapter diff:** extend `contract.ts` `Allow`/`Block` (or a shared result projection) with `obligations?: Obligation[]`, and/or `RunSnapshot` with `obligations?: Obligation[]`, exactly where Lane B emits them. Import the mirror in `contract.ts`/`translate.ts`; add `obligationsText(obligations)` that serializes every field (stable order, no counts-only compression). `GateDecision` becomes `{kind:"permit"}` or `{kind:"deny"; reason:string; obligations:Obligation[]}`. `gateFromDecision` carries Block obligations and a fallback run obligations array. `convergenceNotice` appends a machine-readable, lossless JSON block or deterministic field-by-field text to both Allow and Block paths; `settledFollowUp` includes the same `obligations[]` plus the explicit “reminder, not a gate” wording. `statusNotice` includes it too. Tests in `test/translate.test.ts` should assert IDs, `must`, witness `(kind,ref)`, `state`, `because`, and `must_not` survive each renderer; `test/gate.test.ts` should assert block reason plus obligations; `test/lifecycle.test.ts` should assert the nudge includes the full array.
- **Evidence:** `plugins/empirica/adapters/pi/src/translate.ts:92-110,119-185` currently carries only reason and prose; `plugins/empirica/adapters/pi/src/contract.ts:90-102` currently permits additive response properties via index signatures. Brief freezes the contract in `doc/design/obligations-contract-brief.md` under “v1 scope of the generic module” and mandates Pi rendering under “Pi parity (v1)”.

## Claims refuted

- **R1 (refuted):** “Pi's current `gatedTools` set means `report_convergence` is model-callable.” `plugins/empirica/adapters/pi/src/index.ts:98` only names the tool; no `registerTool` exists (`index.ts:101-220`). The command is `/report-convergence` (`index.ts:153-175`), a separate host command.
- **R2 (refuted):** “Pi can provide the same hard completion guarantee as Claude Stop.” ADR-0032 explicitly distinguishes Claude exit-code Stop from Pi observational settled/end (`doc/adr/0032-port-claude-completion-gating-to-a-gated-domain-operation-on-pi.md:21-24`). Pi can guarantee only that an invoked report tool is denied; an agent can stop without invoking it (`ADR-0032:52-57`).
- **R3 (refuted):** “The current Pi adapter preserves the skill’s compaction promise.” Current code has no `session_before_compact`, `session_compact`, `session_start`, `context`, `before_agent_start`, or obligations rendering (`plugins/empirica/adapters/pi/src/index.ts:101-220`); the promise is explicit at `plugins/empirica/skills/empirica/SKILL.md:363-366`.

## Open/blocked (what was tried)

1. **Exact Lane A schema names/JSON scalar types:** blocked pending Lane A's `contracts/obligations/v1/*.schema.json`. I searched `contracts/obligations/v1` and found no files in this worktree at recon time; the brief says Lane A owns them. The proposed interfaces above are therefore explicitly provisional where the brief leaves opaque values.
2. **Spawn/audit request shape:** blocked pending Lane B/core exposure. `contract.ts:34-39` only has generic `ObserveAction`; no ticket-specific command exists. I did not invent a transport protocol in read-only recon.
3. **Whether a blocked-tool reason is actually injected into model context and whether follow-up always starts another turn:** not established by docs alone. ADR-0032 requires executable spikes (`doc/adr/0032-port-claude-completion-gating-to-a-gated-domain-operation-on-pi.md:42-44`). Existing tests only test the fake adapter and dispatch (`make empirica-pi-check`), not a real Pi runtime/model context.
4. **Whether the repository’s installed Pi version exposes the exact subagent tool name:** not established; the docs list a subagent extension, not a universal built-in. The interception is conditional on the observed name.
5. **Full examples directory:** the extension examples were inspected for the documented patterns (custom compaction, dynamic tools, permission/tool gates, send-user-message, stateful todo/session persistence, subagent); examples are host implementation evidence but not a substitute for a runtime spike. No files were modified.

## Files changed

- None. This was explicitly read-only recon. The authoritative artifact is this report at the runtime output path.

## Requests to other lanes (exact diffs)

1. **Lane A (contract):** expose and freeze the exact obligations schema/fixtures; publish the TS mirror mapping (including scalar types for `expect`, `because`, `provenance`, `at`, and Verdict member shapes). Add a response fixture where a `Block` and `Allow` carry `obligations[]`.
2. **Lane B (Python/application):** add `obligations[]` to `Block`, `Allow`/final handoff, and RestoreRun snapshots without dropping it from the run view; expose a typed spawn/audit-ticket operation (or document the exact `ObserveAction.action.kind` and payload) and knowledge/route observation operations for Pi. **Exact adapter-facing shape requested:** `result.obligations?: Obligation[]` and/or `result.run.obligations?: Obligation[]`, consistently in all response fixtures; do not require Pi to infer from `reason`.
3. **Parent / ADR-0040 owner:** record the hard Stop-veto gap as impossible on Pi, plus the two unverified usability claims (blocked reason model visibility and follow-up turn reliability), and state that Pi enforcement is only at explicit report-tool invocation. Update SKILL.md with a Pi Runtime boundary paragraph: mode parsing, handle projection, registered tools, spawn interception conditional on visible `subagent`, compaction reinjection, obligations rendering, and named impossible gap.
4. **Pi implementation lane (future write phase):** extend `pi-types.ts` with `registerTool`, `sendMessage`, `appendEntry`, `session_start`, `session_before_compact`, `session_compact`, and (if needed) `before_agent_start`/`context` types; implement pure parser/formatters first and test them under the existing `node:test` package script.

## Residual risks

- **Losslessness risk:** serializing only `id`/`must` or counts still violates the invariant. Render all `must_not`, witnesses including `expect`, state, severity, and opaque `because` provenance; stable JSON is safest.
- **Model-context risk:** `pi.appendEntry` is persisted but not model-visible; use custom summary/message/context injection separately. `ctx.ui.notify` is user UI, not model context.
- **Enforcement risk:** no extension can stop an agent that simply never calls the report tool. This cannot be fixed in v1 without a host completion-veto API.
- **Subagent risk:** `tool_call` only intercepts tools Pi dispatches through its event stream. A hidden/external spawn mechanism is unverified and may evade the gate; issue ticket before allowing the call where possible, then fail closed on transport/core errors.
- **State risk:** in-memory `runHandle` is lost on extension reload/session replacement. Persist an opaque handle in Pi session entries and reconstruct on `session_start`; never derive it from prose or workspace counts.
- **Compaction risk:** custom summary text can be LLM-generated and therefore semantically lossy if obligations are not rendered deterministically. Append a deterministic obligations section to the summary and retain JSON in `details`; test preservation after simulated compaction.
- **Scope risk:** adding a broad generic evaluator or Pi-specific convergence judgment would violate the brief’s layer separation; adapter must translate and render only.

## Acceptance report

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "Recon completed read-only within the assigned Pi adapter scope; no implementation scope was widened."
    }
  ],
  "changedFiles": [],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {
      "command": "make empirica-pi-check",
      "result": "passed",
      "summary": "Existing Pi adapter checks passed: 37 tests, static package validation, bridge smoke, and TypeScript typecheck."
    }
  ],
  "validationOutput": [
    "make empirica-pi-check: 37 passed, 0 failed; package well-formed; bridge smoke passed; tsc --noEmit passed."
  ],
  "residualRisks": [
    "No real Pi runtime spike was run for blocked-tool model-context visibility or follow-up reliability.",
    "Lane A obligations schemas and Lane B response/ticket shapes were unavailable during recon.",
    "Pi cannot hard-veto completion when the model never invokes the gated report tool."
  ],
  "noStagedFiles": true,
  "diffSummary": "No files changed; report-only recon.",
  "reviewFindings": [
    "no blockers in the existing read-only checks; implementation remains pending the Lane A/B contract shapes"
  ],
  "manualNotes": "The requested repository report path conflicts with the runtime-authoritative output override; this report was written to the authoritative path only."
}
```
