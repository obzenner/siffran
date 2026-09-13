> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

## Claims established

- **Audit blocker 1 closed:** every `_run_snapshot` now calls `_contract_view`, therefore StartRun, GetRun, and ObserveAction acknowledgements carry `run.contract` when a graph is readable (`plugins/empirica/application/service.py:974-975`). `test_get_run_returns_snapshot` asserts GetRun/RestoreRun contract preservation in both directions (`plugins/empirica/tests/test_application.py:266-279`).
- **B3 closed:** `needs-decision` emits `judgment:decision/<id>` and `needs-budget` emits `event:budget/<id>` (`plugins/empirica/core/obligations.py:16-23`); OC2 asserts these and the raw-object trust defence (`plugins/empirica/tests/test_application.py:1289-1310`).
- **Layering closed:** the sole fold classifier is now core-pure (`plugins/empirica/core/evidence.py:6-18`); application imports it (`plugins/empirica/application/knowledge.py:25`) and obligation projection imports it from core (`plugins/empirica/core/obligations.py:12`).
- **m14 closed:** OC2 bypasses the frozen Observation constructor with `object.__new__`, sets judgment source `model`, and proves `trusted()` rejects it (`plugins/empirica/tests/test_application.py:1300-1310`).
- **Skill stale resume statement closed:** compacting now documents `run.contract`, telemetry-only counts, and `render_text` on string channels (`plugins/empirica/skills/empirica/SKILL.md:372-375`).
- **Validation:** `make check` passed, with 0 errors, 82 pre-existing ADR warnings, and 1 ADR-0040 info.

## Claims refuted

- The old assertion that compaction “re-injects the graph — including missing folds” was false; it has been replaced with the actual contract projection statement.
- The old core→application deferred import was a layering inversion; it is removed.

## Open/blocked

- **B5 NOT CLOSED.** I added compatible `contract_artifact_id` and `contract_revision` fields to `OperationalState` and its codec (`plugins/empirica/application/state.py:74-76,184-185,242-245,256-258`), but did not complete the required append-before-CAS revision transaction. No application call to vendored `revise()` yet exists. This means the fields are currently inert and B5’s graph/refutation/freeze revision-history tests are not added. It would be misleading to claim otherwise. The correct implementation is: read and parse `state.contract_artifact_id`, calculate old/new live obligation IDs, call `revise(add/retire, reason, authority=<causing artifact>)`, append returned Contract artifact, and CAS the pointer/revision with the graph/freeze state. Terminal persistence must reuse the latest revision unless the set changes.
- **m5/m15/m7 remain open.** This pass did not add the requested ≥12 mixed-open/blocked/deferred projection regression, universal `must == claim.text` regression, or Codex Block `render_text(view)` assertion.
- ADR-0039 still over-claims per-set-change revision persistence until B5 is implemented; it must be amended only together with the real transaction.

## Files changed

- `plugins/empirica/application/service.py`
- `plugins/empirica/application/state.py`
- `plugins/empirica/application/knowledge.py`
- `plugins/empirica/core/evidence.py`
- `plugins/empirica/core/obligations.py`
- `plugins/empirica/tests/test_application.py`
- `plugins/empirica/skills/empirica/SKILL.md`

## Requests to other lanes

- No changes requested from Pi/Lane C.
- Follow-up Lane B must finish B5 using the transaction described under Open/blocked and then add m5/m15/m7 regressions.

## Residual risks

- B5 remains a release blocker: state fields alone do not make revision history real.
- m5 cap-at-10 and m15 semantic `must` mutations remain untested.
- Codex block renderer remains unpinned by a focused test.

## `make check` tail

```text
warning: [asymmetric-link] ADR 40 'Pi adapter parity and named gaps' links to ADR 32 as 'Relates to' but ADR 32 has no link back to ADR 40 [...]
info: [ADR017] Consequences section should use 'Good, because...' and 'Bad, because...' format [...]

Found 0 error(s), 82 warning(s), 1 info(s)

All checks passed.
```

```acceptance-report
{
  "criteriaSatisfied": [
    {"id":"criterion-1","status":"not-satisfied","evidence":"Implemented blocker-1, B3, layering, m14, and SKILL fixes within scope, but B5 per-set-change revise/persistence and m5/m15/m7 tests remain incomplete and are explicitly reported."},
    {"id":"criterion-2","status":"satisfied","evidence":"File:line evidence, focused regression locations, open design, and make check output are included."}
  ],
  "changedFiles":["plugins/empirica/application/service.py","plugins/empirica/application/state.py","plugins/empirica/application/knowledge.py","plugins/empirica/core/evidence.py","plugins/empirica/core/obligations.py","plugins/empirica/tests/test_application.py","plugins/empirica/skills/empirica/SKILL.md"],
  "testsAddedOrUpdated":["plugins/empirica/tests/test_application.py"],
  "commandsRun":[{"command":"python3 plugins/empirica/tests/test_application.py","result":"passed","summary":"133/133 checks passed."},{"command":"make lint","result":"passed","summary":"All checks passed."},{"command":"make contract-check","result":"passed","summary":"7 schemas, 9 fixtures."},{"command":"make check","result":"passed","summary":"All checks passed; ADR doctor 0 errors."}],
  "validationOutput":["make check: Found 0 error(s), 82 warning(s), 1 info(s). All checks passed."],
  "residualRisks":["B5 real revise/persist/CAS transaction is not implemented.","m5, m15, m7 regression gaps remain."],
  "noStagedFiles":true,
  "diffSummary":"Adds universal readable-graph run.contract snapshots, typed blocked witnesses, core fold classification, trust defence coverage, state pointer fields, and corrected compact-resume documentation.",
  "reviewFindings":["blocker: B5 remains incomplete; pointer fields are inert without revise/append/CAS transaction.","finding: m5/m15/m7 regression tests remain absent."],
  "manualNotes":"No git command and no prohibited file edit was performed. make check is green, but acceptance is deliberately marked not-satisfied because B5 was not completed."}
```