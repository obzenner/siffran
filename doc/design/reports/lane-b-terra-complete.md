> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

## Claims established

- **B3 Fold observation bug fixed.** `application.knowledge.evidence_fold()` is the one pure `predicateType` classifier, recognizing `/research/v1` and `/spike/v1` (`plugins/empirica/application/knowledge.py:375-391`), and `_leaf_digest()` now uses it (`plugins/empirica/application/knowledge.py:409-411`). `observations_from_knowledge()` imports that helper rather than re-implementing the rule and maps research `predicate.result` and spike `predicate.gate` to their distinct witnesses (`plugins/empirica/core/obligations.py:29-55`).
- **Real leaf regression.** `test_real_research_and_spike_leaves_satisfy_fold_witnesses` submits a real research attestation through `build_research_request` and a real deterministic-harness spike through `run_spike`/`build_spike_request`; it asserts `research/G0` and `spike/G0` are both observed pass and the requirement is satisfied (`plugins/empirica/tests/test_application.py:1250-1285`).
- **B7 e2e regression.** `test_obligations_survive_block_restore_adapters_and_terminal_handoff` exercises service decision → Block wire → Claude `stop_result` (`render_text` byte-for-byte) → RestoreRun → Claude compaction JSON `parse()`/`canonical()` → budget terminal Allow → artifact body round-trip, checking `preserved()` at every applicable hop (`plugins/empirica/tests/test_application.py:1295-1331`).
- **Focused adapters.** Claude directly asserts Block prose plus `render_text` and parses compact `run.contract` back to canonical form (`plugins/empirica/adapters/claude/tests/test_claude_adapter.py:307-326`). Codex integration performs the same compact context canonical parse (`plugins/empirica/adapters/codex/tests/test_codex_adapter.py:328-342`). The adapters preserve the one wire location in compaction as `{"run":{"contract":…},"snapshot":…}` (`plugins/empirica/adapters/claude/restore.py:58-62`, `plugins/empirica/adapters/codex/lifecycle.py:390-393`).
- **B8 fixtures and validator.** The three new fixtures are present and the three specified existing Empirica fixtures now carry `run.contract`; `make contract-check` validated all 9 fixture instances. `validate_contracts.py` now supplies locally indexed `$id` schemas to the resolver so the `$ref` to obligations is actually instance-validated offline (`scripts/validate_contracts.py:62-66`).
- **ADR-0039 strengthened.** It now names the rejected counts+prose and exposed-GSN options (`doc/adr/0039-make-the-obligation-contract-empirica-lossless-agent-interface.md:24-28`), states the layer model and revision authority (`:32-35`), and names the exact regressions under Confirmation (`:44`).

## Claims refuted

- The former Fold-2 mapping was wrong: it compared `statement.predicate` (an object in real in-toto leaves) with the string `"spike"`; therefore it could not produce an exit-code observation. The real-record test falsifies that regression (`plugins/empirica/tests/test_application.py:1250-1285`).
- A compaction snapshot alone is not the obligation contract. The adapters now embed the unchanged sole wire location `run.contract`, separately from telemetry snapshot (`plugins/empirica/adapters/claude/restore.py:58-62`).

## Open/blocked (what was tried)

- **B5 remains open — explicit residual.** Assessment established that a `contract_revision` artifact-id pointer *does* fit `OperationalState`: it is an append-only-Artifact pointer analogous to the existing `claim_graph_artifact_id` (`plugins/empirica/application/state.py:70-74`) and the document serializer has extension fields (`plugins/empirica/application/state.py:170-247`). Implementing honest revisions requires a coherent transaction: load current contract artifact, call vendored `revise(add/retire, reason, authority=<graph/refutation/freeze artifact id>)`, append the returned Contract artifact, then CAS its pointer atomically with the graph/freeze transition. This pass did not add that state field/transaction/tests; doing only a pointer or only an append would be the prohibited half-implementation. The proposed design must add `contract_artifact_id: str | None` to `OperationalState`, decode/encode it, and have `_update_graph` and `_freeze` append-before-CAS just as the graph pointer does. A refuted graph write determines `retire` from the old/new obligation IDs and records `reason="refuted by <evidence id>"`; freeze derives deferred holds but retains obligations. This is a real unsatisfied requested item.
- **Parent-approved Pi blocker.** `make check` is green except the named Pi test while Lane C’s worktree is concurrent. Updating `contracts/fixtures/empirica-block-audit.json` correctly adds `run.contract`, but `plugins/empirica/adapters/pi/test/translate.test.ts:115` still hard-codes `{kind:"deny", reason:"independent audit required"}`. Actual output adds `contract`. Exact Lane-C diff:
  ```diff
  -assert.deepEqual(result, { kind: "deny", reason: "independent audit required" });
  +assert.deepEqual(result, { kind: "deny", reason: "independent audit required", contract: fixture.expected.result.run.contract });
  ```
  Per supervisor decision, I did not edit `adapters/pi`.
- `make adr-check` has 4 errors exclusively in concurrently merged `doc/adr/0040-pi-adapter-parity-and-gaps.md`; per instruction I did not touch it. ADR-0039 itself has no doctor error.

## Files changed

- `plugins/empirica/application/knowledge.py`
- `plugins/empirica/core/obligations.py`
- `plugins/empirica/adapters/claude/restore.py`
- `plugins/empirica/adapters/codex/lifecycle.py`
- `plugins/empirica/tests/test_application.py`
- `plugins/empirica/adapters/claude/tests/test_claude_adapter.py`
- `plugins/empirica/adapters/codex/tests/test_codex_adapter.py`
- `contracts/fixtures/empirica-block-audit.json`
- `contracts/fixtures/empirica-reserve-spawn-denied.json`
- `contracts/fixtures/empirica-restore-run.json`
- `contracts/fixtures/empirica-block-open-claim.json`
- `contracts/fixtures/empirica-terminal-handoff-budget.json`
- `contracts/fixtures/empirica-terminal-handoff-frozen.json`
- `scripts/validate_contracts.py`
- `doc/adr/0039-make-the-obligation-contract-empirica-lossless-agent-interface.md`

## Requests to other lanes

- **Lane C:** apply the exact Pi test expected-value diff above at `plugins/empirica/adapters/pi/test/translate.test.ts:115`, then re-run `make check` after its ADR-0040 formatting correction.
- **Parent / follow-up Lane B:** implement the state-backed B5 contract-artifact pointer transaction described under Open/blocked. It must add explicit tests for graph write, refutation, and freeze transitions.

## Residual risks

- B5 is intentionally not represented by a partial or misleading implementation; revision artifacts currently occur at terminal handoff, not every set transition.
- `make check` is not fully green in this worktree only because of the parent-confirmed concurrent Pi fixture-expectation test and ADR-0040 formatting errors.

## Commands run

- `make help` — passed.
- `python3 plugins/empirica/tests/test_application.py` — passed, 131/131 checks.
- `python3 plugins/empirica/adapters/claude/tests/test_claude_adapter.py` — passed, 24 tests.
- `python3 plugins/empirica/adapters/codex/tests/test_codex_adapter.py` — passed, 6 tests.
- `make lint` — passed.
- `make contract-check` — passed: `ok: 7 schemas, 9 fixtures`.
- `make adr-check` — failed only due ADR-0040 (four errors), not modified per instruction.
- `make check` — failed only in Pi test named above (after all Lane B tests and validation passed); parent directed reporting this green-except-Pi state.

## `make check` tail

```text
actual: { kind: 'deny', reason: 'independent audit required', contract: { ... } },
expected: { kind: 'deny', reason: 'independent audit required' },
operator: 'deepStrictEqual'
...
make: *** [pi-bundle-check] Error 1
```

```acceptance-report
{
  "criteriaSatisfied": [
    {"id":"criterion-1","status":"not-satisfied","evidence":"The priority Fold-2 bug, B7 regression/adapter coverage, fixtures, schema validation, and ADR content were implemented in scope; B5 per-set-change honest revisions remains explicitly open rather than being faked."},
    {"id":"criterion-2","status":"satisfied","evidence":"Focused application, Claude, Codex, lint, and contract-check commands passed; exact remaining Pi test and ADR-0040 blockers, root cause, and exact Lane C diff are recorded."}
  ],
  "changedFiles":["plugins/empirica/application/knowledge.py","plugins/empirica/core/obligations.py","plugins/empirica/adapters/claude/restore.py","plugins/empirica/adapters/codex/lifecycle.py","plugins/empirica/tests/test_application.py","plugins/empirica/adapters/claude/tests/test_claude_adapter.py","plugins/empirica/adapters/codex/tests/test_codex_adapter.py","contracts/fixtures/empirica-block-audit.json","contracts/fixtures/empirica-reserve-spawn-denied.json","contracts/fixtures/empirica-restore-run.json","contracts/fixtures/empirica-block-open-claim.json","contracts/fixtures/empirica-terminal-handoff-budget.json","contracts/fixtures/empirica-terminal-handoff-frozen.json","scripts/validate_contracts.py","doc/adr/0039-make-the-obligation-contract-empirica-lossless-agent-interface.md"],
  "testsAddedOrUpdated":["plugins/empirica/tests/test_application.py","plugins/empirica/adapters/claude/tests/test_claude_adapter.py","plugins/empirica/adapters/codex/tests/test_codex_adapter.py"],
  "commandsRun":[{"command":"make lint","result":"passed","summary":"All checks passed."},{"command":"make contract-check","result":"passed","summary":"7 schemas and 9 fixtures instance-validated."},{"command":"python3 plugins/empirica/tests/test_application.py","result":"passed","summary":"131/131 checks."},{"command":"python3 plugins/empirica/adapters/claude/tests/test_claude_adapter.py","result":"passed","summary":"24 tests."},{"command":"python3 plugins/empirica/adapters/codex/tests/test_codex_adapter.py","result":"passed","summary":"6 tests."},{"command":"make check","result":"failed","summary":"Only merged Pi test stale fixture expectation failed; supervisor routed exact fix to Lane C."}],
  "validationOutput":["make contract-check: ok: 7 schemas, 9 fixtures","make lint: All checks passed"],
  "residualRisks":["B5 state-backed revision pointer and transition tests are outstanding.","Lane C must update the one Pi test expectation and ADR-0040 formatting before full make check is green."],
  "noStagedFiles":true,
  "diffSummary":"Reuses one predicateType fold classifier; adds real Fold-1/Fold-2, lossless Block/restore/handoff, renderer, compaction parse, and fixture validation regressions; strengthens ADR-0039.",
  "reviewFindings":["blocker: B5 per-set-change Contract revise/add/retire persistence is incomplete.","blocker: adapters/pi/test/translate.test.ts:115 stale expected fixture value is parent-routed to Lane C."],
  "manualNotes":"No git command was run. ADR-0040 and adapters/pi were not modified as instructed."
}
```