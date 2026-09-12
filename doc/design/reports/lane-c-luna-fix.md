> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

## Claims established

- **Audit blocker C1 closed:** Pi compaction now dispatches `RestoreRun`, not `GetRun` (`plugins/empirica/adapters/pi/src/index.ts:260-264`; `plugins/empirica/adapters/pi/src/translate.ts:92-95`). `empirica_status` still uses `GetRun`, but its model result now says exactly `no contract yet (no graph)` when absent and never JSON-stringifies the whole result (`index.ts:130-134,145-146`).
- **C2 closed:** `KNOWLEDGE_ACTION_KINDS` now excludes `audit_ticket` and `audit_verdict`, and includes `freeze` (`plugins/empirica/adapters/pi/src/index.ts:33-34`). Audit tickets remain issued only in `tool_call` interception (`index.ts:221-231`). The closed set mirrors Claude builders: graph/route/evidence/attribution at `plugins/empirica/adapters/claude/knowledge.py:35-44,65-67,191-192`, and investigate/route at `plugins/empirica/adapters/claude/route.py:71-83`; `audit_ticket` and `audit_verdict` are deliberately excluded because they are trusted-principal operations.
- **Mutation m8b/m8c closed:** a new regression test supplies a Block *with* a contract and asserts byte-verbatim `renderText(contract)` in `convergenceNotice`, `settledFollowUp`, and `statusNotice` (`plugins/empirica/adapters/pi/test/translate.test.ts:25-33`).
- **Parity masking closed:** parity tests consume real `contracts/fixtures/empirica-block-audit.json` and `empirica-restore-run.json` (`plugins/empirica/adapters/pi/test/parity.test.ts:10-18`), and explicitly test a GetRun result with no contract (`parity.test.ts:19`). Compaction test uses `RestoreRun` and asserts deterministic summary plus JSON `details.contract` (`parity.test.ts:21`).
- **Tool description corrected:** `empirica_status` now accurately returns a handle and either the deterministic contract or the explicit no-graph message (`plugins/empirica/adapters/pi/src/index.ts:145-147`).
- **Audit finding closure map:** C1 blocker → `parity.test.ts:19,21`; C2 model-callable audit verdict → `parity.test.ts:18`; m8b/m8c → `translate.test.ts:25-33`; parity fake masking → fixture reads at `parity.test.ts:10-18`. The prior audit’s B5 and stale SKILL findings are Lane B/parent-owned and were not changed under this lane boundary.

## Claims refuted

- The prior production behavior that compaction used `GetRun` is refuted: it now builds and dispatches `RestoreRun` (`index.ts:260-264`).
- The prior claim that the model-callable knowledge tool accepted audit verdicts/tickets is refuted by the closed set and rejection test (`index.ts:33-34,137-139`; `test/parity.test.ts:18`).
- The prior test claim that all fake commands returned contracts is refuted for the status path by the explicit no-contract response (`test/parity.test.ts:19`).

## Open/blocked (what was tried)

- **UNVERIFIED:** Pi blocked-reason visibility in model context and follow-up-turn reliability still require the parent’s live runtime spike; ADR-0040 and README retain the impossible completion-veto wording.
- B5 per-set-change persistence, B3 decision/budget witness mapping, SKILL.md stale text, and unrelated surviving Lane B/lib mutations remain outside this lane’s permitted write set. No attempt was made to widen scope.
- `make check` completed within timeout and passed (tail below).

## Files changed

- `plugins/empirica/adapters/pi/src/index.ts`
- `plugins/empirica/adapters/pi/src/contract.ts`
- `plugins/empirica/adapters/pi/src/translate.ts`
- `plugins/empirica/adapters/pi/test/parity.test.ts`
- `plugins/empirica/adapters/pi/test/translate.test.ts`

## Requests to other lanes

- Parent/Lane B: retain the application fix making `run.contract` available on StartRun/GetRun. This adapter now correctly handles both present and absent contract views.
- Parent: schedule the live Pi spike for the two explicitly UNVERIFIED runtime claims.

## Residual risks

- Pi cannot veto completion when `report_convergence` is never invoked; enforcement remains tool-invocation-only.
- Spawns outside Pi’s `tool_call` event stream remain ungated.
- Full application B5/B3 and SKILL.md stale-claim findings remain parent/Lane B responsibilities.

## Validation

`make empirica-pi-check` tail:

```text
ℹ tests 66
ℹ pass 66
ℹ fail 0
ok: plugins/empirica/adapters/pi package is well-formed
ok: plugins/empirica/adapters/pi/bridge.py answers a request with a typed envelope
typechecking plugins/empirica/adapters/pi/tsconfig.json via tsc --noEmit
running 7 test file(s) via node --test
```

`make adr-check` tail:

```text
Found 0 error(s), 82 warning(s), 1 info(s)
```

`make check` tail:

```text
Found 0 error(s), 82 warning(s), 1 info(s)
All checks passed.
```

```acceptance-report
{
  "criteriaSatisfied": [
    {"id":"criterion-1","status":"satisfied","evidence":"Only Pi adapter and ADR-0040 scope was changed; C1/C2 and mutation-test fixes are confined to plugins/empirica/adapters/pi and doc/adr/0040."},
    {"id":"criterion-2","status":"satisfied","evidence":"Audit closure is mapped to exact file:line evidence and 66 passing Pi tests, with residual ownership and UNVERIFIED runtime risks explicitly listed."}
  ],
  "changedFiles":["plugins/empirica/adapters/pi/src/index.ts","plugins/empirica/adapters/pi/src/contract.ts","plugins/empirica/adapters/pi/src/translate.ts","plugins/empirica/adapters/pi/test/parity.test.ts","plugins/empirica/adapters/pi/test/translate.test.ts"],
  "testsAddedOrUpdated":["plugins/empirica/adapters/pi/test/parity.test.ts","plugins/empirica/adapters/pi/test/translate.test.ts"],
  "commandsRun":[{"command":"make empirica-pi-check","result":"passed","summary":"66 tests passed; typecheck/package validation/bridge smoke passed."},{"command":"make adr-check","result":"passed","summary":"0 errors; 82 warnings and 1 info."},{"command":"make check","result":"passed","summary":"All checks passed."}],
  "validationOutput":["Real block/restore fixtures drive parity tests; explicit no-contract status case; RestoreRun compaction assertion; Block-with-contract renderer mutation tests."],
  "residualRisks":["UNVERIFIED blocked-reason model visibility and follow-up-turn reliability pending live Pi spike","Pi completion veto is impossible when report tool is not invoked","external spawns outside Pi tool_call stream remain ungated","Lane B B5/B3 and stale SKILL claim remain outside this lane write scope"],
  "noStagedFiles":true,
  "diffSummary":"Changed compaction to RestoreRun, made status no-contract behavior explicit, closed knowledge action set, switched parity tests to real fixtures, and added renderer mutation regressions.",
  "reviewFindings":["closed C1 GetRun/contract visibility blocker on Pi surfaces","closed C2 audit_ticket/audit_verdict model-tool exposure","closed m8b/m8c renderer mutation survivors","no blockers in Pi, ADR, or full checks"],
  "manualNotes":"Parent should preserve Lane B’s run.contract application fix and schedule the two required live Pi runtime spikes."
}
```