> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

## Claims established

- **ADR format repaired:** ADR-0040 was deleted and regenerated with `adrs --ng new --format madr --status proposed`; it now has valid MADR front matter, links ADR-30 and ADR-32, and substantive Context, Drivers, Options, Outcome, Consequences, and Confirmation (`doc/adr/0040-pi-adapter-parity-and-named-gaps.md:1-76`).
- **C1/C2:** Mode parsing, three registered tools, closed knowledge action validation, subagent interception, and fail-closed transport behavior are implemented in `plugins/empirica/adapters/pi/src/index.ts:33-34,115-151,221-231` and tested in `plugins/empirica/adapters/pi/test/parity.test.ts:8-11`. Knowledge kinds exactly mirror Claude builders: `knowledge.py:35-44,65-67,172-191` and `route.py:71-83`.
- **C3:** The structural TypeScript obligation mirror and deterministic renderer remain in `plugins/empirica/adapters/pi/src/obligations.ts:1-57`. The fixture test enumerates all 21 files (`plugins/empirica/adapters/pi/test/obligations.test.ts:9-14`); shell confirmation: `ls contracts/obligations/v1/fixtures/*.json | wc -l` returned `21`. Exact text fixtures and preservation fixtures both pass; no fixture is silently omitted from the directory loop.
- **C5/C6:** appendEntry/session_start reconstruction and custom compaction summary with `details.contract` are implemented at `plugins/empirica/adapters/pi/src/index.ts:103-114,153-156,253-262`; tests cover persisted entry, reconstruction, deterministic summary, and JSON details (`test/parity.test.ts:10`).
- **C8/B1:** `run.contract` and `run.contract_artifact_id` are represented in `plugins/empirica/adapters/pi/src/contract.ts:60-70`; renderers append the contract in `plugins/empirica/adapters/pi/src/translate.ts:120-210`.

## Claims refuted

- The previous ADR-0040 was not valid MADR and failed ADR001/002/003/006; it was deleted and replaced by a CLI-generated MADR document.
- `empirica_knowledge` accepting arbitrary action objects was not a closed contract; unknown kinds now reject before bridge dispatch (`plugins/empirica/adapters/pi/src/index.ts:137-139`).

## Open/blocked (what was tried)

- Blocked-reason model visibility and follow-up-turn reliability remain **UNVERIFIED**, as required by C4, pending a live Pi runtime spike. Spawn mechanisms outside Pi's event stream remain un-gated.
- `make check` was run and completed successfully within timeout; its tail is recorded below.

## Files changed

- `plugins/empirica/adapters/pi/src/index.ts`
- `plugins/empirica/adapters/pi/src/contract.ts`
- `plugins/empirica/adapters/pi/src/pi-types.ts`
- `plugins/empirica/adapters/pi/src/translate.ts`
- `plugins/empirica/adapters/pi/src/obligations.ts`
- `plugins/empirica/adapters/pi/test/fakes.ts`
- `plugins/empirica/adapters/pi/test/obligations.test.ts`
- `plugins/empirica/adapters/pi/test/parity.test.ts`
- `plugins/empirica/adapters/pi/README.md`
- `doc/adr/0040-pi-adapter-parity-and-named-gaps.md`

## Requests to other lanes

- Parent: retain the verbatim Pi Runtime boundary paragraph from the prior report in SKILL.md; this lane did not modify SKILL.md.
- Parent: schedule the live Pi spike for blocked-reason model visibility and follow-up-turn reliability.

## Residual risks

- Pi completion veto remains impossible; only explicit `report_convergence` invocation is gated.
- Hidden/external subagent execution outside Pi's `tool_call` stream is ungated.
- Runtime model-context and follow-up semantics remain UNVERIFIED.

## Validation tails

`make empirica-pi-check`:

```text
ℹ tests 63
ℹ pass 63
ℹ fail 0
ok: plugins/empirica Pi package is well-formed
... typechecking ... via tsc --noEmit
running 7 test file(s) via node --test
```

`make adr-check`:

```text
Found 0 error(s), 80 warning(s), 1 info(s)
```

The sole info is pre-existing style guidance on ADR017; ADR-0040 has no errors. `make check` tail:

```text
Found 0 error(s), 80 warning(s), 1 info(s)
All checks passed.
```

```acceptance-report
{
  "criteriaSatisfied": [{"id":"criterion-1","status":"satisfied","evidence":"Concrete Pi implementation findings and residuals are documented with file paths and line ranges; deterministic Pi and ADR checks pass."}],
  "changedFiles":["plugins/empirica/adapters/pi/src/index.ts","plugins/empirica/adapters/pi/src/contract.ts","plugins/empirica/adapters/pi/src/pi-types.ts","plugins/empirica/adapters/pi/src/translate.ts","plugins/empirica/adapters/pi/src/obligations.ts","plugins/empirica/adapters/pi/test/fakes.ts","plugins/empirica/adapters/pi/test/obligations.test.ts","plugins/empirica/adapters/pi/test/parity.test.ts","plugins/empirica/adapters/pi/README.md","doc/adr/0040-pi-adapter-parity-and-named-gaps.md"],
  "testsAddedOrUpdated":["plugins/empirica/adapters/pi/test/parity.test.ts","plugins/empirica/adapters/pi/test/obligations.test.ts","plugins/empirica/adapters/pi/test/fakes.ts"],
  "commandsRun":[{"command":"make empirica-pi-check","result":"passed","summary":"63 tests passed, typecheck/package validation/bridge smoke passed."},{"command":"make adr-check","result":"passed","summary":"0 errors; 80 warnings and 1 informational style note."},{"command":"make check","result":"passed","summary":"All checks passed; 0 errors."},{"command":"ls contracts/obligations/v1/fixtures/*.json | wc -l","result":"passed","summary":"21 fixtures."}],
  "validationOutput":["renderText/preserved fixture loop passes all applicable fixture expectations; exact fixture count is 21"],
  "residualRisks":["UNVERIFIED blocked-reason model visibility and follow-up reliability pending live Pi spike","Pi cannot veto completion when report_convergence is never invoked","spawn outside Pi tool_call stream is ungated"],
  "noStagedFiles":true,
  "diffSummary":"Regenerated valid ADR-0040, closed knowledge action validation, added comprehensive parity tests, and covered session/compaction/tool paths.",
  "reviewFindings":["no blockers in make empirica-pi-check, make adr-check, or make check"],
  "manualNotes":"ADR-0040 links ADR-30/32 and is substantive. Parent should retain the SKILL.md paragraph and schedule the live runtime spike."
}
```