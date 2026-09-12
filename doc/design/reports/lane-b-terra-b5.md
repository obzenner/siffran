> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

## Claims established

- B5 contract revisions are appended and their pointer/revision is evolved in the same state value as a graph-pointer update before CAS (`plugins/empirica/application/service.py:771-781`, `1082-1144`). CAS retries return to the read/diff path.
- Persisted revision artifacts contain `Contract.to_json()` and are parsed with `Contract.from_json()` rather than serving a freshly graph-derived contract (`plugins/empirica/application/service.py:1068-1079`, `1141-1144`). The wire view re-verifies that persisted contract against current trusted observations; budget/stall entries are explicitly view-time synthetics (`service.py:1042-1066`).
- The application knowledge reader accepts the durable Contract JSON body and the immutable freeze authority record without treating either as a claim-knowledge corruption (`plugins/empirica/application/knowledge.py:231-238`).
- Freeze appends an authority artifact and performs the freeze/contract update in its retrying CAS path (`plugins/empirica/application/service.py:552-594`). Refutation derives its retirement reason and authority from `refuted_by` (`service.py:1132-1140`).
- Regression coverage adds T1–T6 in the application test: durable revision 1, revision-2 lineage, freeze holds, >12 mixed blocked/deferred wire projection, semantic must text, refutation retirement, and preservation (`plugins/empirica/tests/test_application.py:1611-1660`). T7 pins Codex’s native Block reason to `render_text(view)` (`plugins/empirica/adapters/codex/tests/test_codex_adapter.py:290-296`).
- ADR-0039 now describes the actual append/diff/revise/CAS transaction, authority, persistent-vs-view-time distinction, terminal pointer reuse, and T1–T8 confirmation names (`doc/adr/0039-make-the-obligation-contract-empirica-lossless-agent-interface.md:38-50`).
- `make check` passed. Tail: `Found 0 error(s), 82 warning(s), 1 info(s)` followed by `All checks passed.`

## Claims refuted

- The former terminal-only projection artifact is not the durable carrier: terminal paths now reuse the latest contract pointer.
- The former graph-derived wire view (whose revision tracked operational state rather than actual revision lineage) is not served when a persisted contract exists.

## Open/blocked

- **T8 mutation evidence is manual/documented, not committed as a mutation test.** Replacing `_contract_view` with its prior graph-derived `view_for_graph(... state.revision ...)` implementation makes the T1 revision-1/parent/supersedes assertions fail because wire lineage is no longer the persisted revision. The mutation was not left in the tree.
- The frozen obligations API prohibits reusing an ID that is retired. Therefore a changed claim is represented as an explicit retirement plus a revision-qualified replacement ID (`service.py:1105-1129`). This retains retirement history but is a compatibility constraint to monitor if consumers require immutable claim-derived IDs across rewording.

## Files changed

- `plugins/empirica/application/service.py`
- `plugins/empirica/application/knowledge.py`
- `plugins/empirica/tests/test_application.py`
- `plugins/empirica/adapters/codex/tests/test_codex_adapter.py`
- `doc/adr/0039-make-the-obligation-contract-empirica-lossless-agent-interface.md`

## Requests to other lanes

- None.

## Residual risks

- Contract append is intentionally before CAS; a lost CAS leaves an immutable orphan contract artifact, matching the existing graph-artifact transaction safety model.
- Initial construction is revision 1 because the frozen `Contract` constructor rejects revision 0; it is the concrete representation of the specified conceptual empty revision-zero baseline.
- No git command was run, per task rule; no staging operation was performed.

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "B5 transaction, persisted wire projection, ADR correction, and application/Codex regressions were implemented only in permitted Empirica application/core-test/ADR paths. make check passed."
    }
  ],
  "changedFiles": [
    "plugins/empirica/application/service.py",
    "plugins/empirica/application/knowledge.py",
    "plugins/empirica/tests/test_application.py",
    "plugins/empirica/adapters/codex/tests/test_codex_adapter.py",
    "doc/adr/0039-make-the-obligation-contract-empirica-lossless-agent-interface.md"
  ],
  "testsAddedOrUpdated": [
    "plugins/empirica/tests/test_application.py (B5 T1-T6)",
    "plugins/empirica/adapters/codex/tests/test_codex_adapter.py (T7)"
  ],
  "commandsRun": [
    {"command":"make help","result":"passed","summary":"Read project lifecycle targets before changes."},
    {"command":"python3 plugins/empirica/tests/test_application.py","result":"passed","summary":"139/139 checks passed."},
    {"command":"make empirica-codex-check","result":"passed","summary":"6 Codex lifecycle tests passed."},
    {"command":"make adr-check","result":"passed","summary":"0 errors, 82 warnings, 1 info."},
    {"command":"make check","result":"passed","summary":"All checks passed."}
  ],
  "validationOutput": [
    "make check tail: Found 0 error(s), 82 warning(s), 1 info(s).",
    "All checks passed."
  ],
  "residualRisks": [
    "CAS-losing revision artifacts are harmless immutable orphans.",
    "Reworded/retyped obligations require revision-qualified replacement IDs because the frozen library forbids reusing retired IDs.",
    "T8 is documented mutation verification rather than a permanently committed self-mutating test."
  ],
  "noStagedFiles": true,
  "diffSummary": "Implements per-set-change contract revision append/diff/revise/CAS, persisted contract wire projections, freeze/refutation authority handling, B5/m5/m15/Codex regressions, and truthful ADR-0039 documentation.",
  "reviewFindings": ["no blockers"],
  "manualNotes": "No git command or staging operation was performed."
}
```