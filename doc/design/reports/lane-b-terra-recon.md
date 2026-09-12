> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

# Lane B recon — Empirica consumption (v1), read-only

## Claims established

### B-1 — the current application loses actionable `ClaimReason` data at every Block emission

**Statement.** `adjudicate()` produces a `Block.open_claims: tuple[ClaimReason, ...]`, with claim id, text, confidence, and exact evidence-oracle reason, but service Block paths emit only `{type, reason, run}`.

**Evidence.** `ClaimReason` has precisely those four fields at `plugins/empirica/core/decisions.py:44-50`; `Block` carries `open_claims` at `plugins/empirica/core/decisions.py:80-94`; `_blocked_converging` fills it from `evidence(nid, "approve")[1]` at `plugins/empirica/core/convergence.py:111-123`. `wire.block()` returns only reason and run at `plugins/empirica/application/wire.py:195-196`. The three `_finalize_block` calls pass only `decision.reason` and a run view at `plugins/empirica/application/service.py:886-887,908,940`; the spawn-cap Block is likewise prose-only at `plugins/empirica/application/service.py:449-453`.

**Implementation plan.** Add `plugins/empirica/core/obligations.py`, importing the vendored Lane-A obligation types only, with pure functions:

```python
project_graph(graph, theta, evidence, audit, *, frozen_claims, run_status) -> tuple[Obligation, ...]
project_block(block: Block, graph, theta, evidence, *, frozen_claims, run_status) -> tuple[Obligation, ...]
```

The service must retain the loaded `graph`, evidence oracle, audit oracle, and state through `_finalize`, then pass the projected JSON view to every `wire.block(...)` call that reports a convergence decision. The independent spawn-cap denial is a separate operational budget condition, not a claim-graph projection; give it the synthetic operational obligation described under B-4 rather than fabricating a claim id.

### B-2 — every presently observable Empirica-to-agent boundary and its loss

| Boundary/path | Emits today | What is lost | Existing test locking current behavior |
|---|---|---|---|
| Application `Block`, advisory (`EvaluateRun(intent=continue)`) | `wire.block(decision.reason, run)` (`plugins/empirica/application/service.py:886-887`) | All `Block.open_claims` identities, text, fold-specific reasons, witnesses, and state. | No direct Block-payload assertion found. `test_advisory_continue_does_not_write_or_spend_pass` checks only kind/status/pass behavior (`plugins/empirica/tests/test_application.py:494-505`). **UNVERIFIED** whether any test indirectly snapshots its exact shape. |
| Application `Block`, progress below cap | same (`plugins/empirica/application/service.py:905-908`) | Same loss. | `test_cap_converts_block_to_stopped_budget` asserts only `type/status` before the terminal conversion (`plugins/empirica/tests/test_application.py:467-492`). |
| Application `Block`, idle below either residual backstop | same (`plugins/empirica/application/service.py:932-940`) | Same loss. | Idle/backstop tests assert status/counters, not remediation payload: `plugins/empirica/tests/test_application.py:560-596,650-668`. |
| Application spawn-budget `Block` | only reason/run (`plugins/empirica/application/service.py:449-453`, `application/wire.py:195-196`) | No structured `needs-budget` discharge condition or current residual contract. | `test_reserve_spawn_budget` locks `Block`/`reserved:false`, not structured obligations (`plugins/empirica/tests/test_application.py:833-856`); fixture locks current prose-only shape (`contracts/fixtures/empirica-reserve-spawn-denied.json:14-23`). |
| `RestoreRun` service snapshot | Operational snapshot plus `graph: {gating,open,blocked,deferred}` counts (`plugins/empirica/application/service.py:217-242,244-266`). | Claim identities/text, missing folds/witnesses, blocked tag, and exact deferred claims. Count 1 is not actionable. | RS2 explicitly locks counts-only resume: `test_restore_includes_graph_view` (`plugins/empirica/tests/test_application.py:1228-1235`); fixture contains only counts (`contracts/fixtures/empirica-restore-run.json:20-33`). |
| Terminal `Allow` after pass cap (`stopped_budget`) | run status/note only via `_run_view` (`plugins/empirica/application/service.py:897-904`). | The open claim obligations that caused cap termination. | `test_cap_converts_block_to_stopped_budget` locks terminal status but not residual content (`plugins/empirica/tests/test_application.py:487-492`). |
| Terminal `Allow` after idle/stall (`stopped_residual`) | status/note only (`plugins/empirica/application/service.py:920-930`). | Open/audit residual obligations. | `test_stall_deadline_stops_residual` and `test_clockless_idle_stops_residual` lock only status/counter (`plugins/empirica/tests/test_application.py:560-596,650-668`). |
| Terminal `Allow` after frozen decision (`stopped_frozen`) | `_decision_run` exposes only `deferred`, `blocked`, `budget_blocked`, audit and notes (`plugins/empirica/application/service.py:860-867,987-998`). | Text/witness/state of every deferred or residual claim and final revision. | `test_freeze_prevents_late_derived_claim_from_gating` asserts status only (`plugins/empirica/tests/test_application.py:744-770`). |
| Terminal `Allow` already terminal / ordinary run snapshots | `_run_snapshot` gives a bare run view (`plugins/empirica/application/service.py:955-958`). | No final handoff contract, residuals, or persisted contract artifact. | Start/Get lifecycle tests check normal run envelope/status (for example `plugins/empirica/tests/test_application.py:259-287`), not a final contract. |
| Claude Stop rendering (`stop_result`) | Block: exit 2 with `reason` only on stderr; Allow: complete result JSON on stdout (`plugins/empirica/adapters/claude/completion.py:70-99`). | For a Block, `obligations` is omitted even if application adds it; on terminal Allow, no dedicated final handoff view exists. | `test_stop_mapping_inert_terminal_corrupt_cap_and_audit` pins reason-only Block stderr and raw Allow stdout (`plugins/empirica/adapters/claude/tests/test_claude_adapter.py:259-305`). |
| Claude compaction (`restore_context`) | JSON-dumps whole active `snapshot`, framed as untrusted data (`plugins/empirica/adapters/claude/restore.py:45-70`). | Today inherits count-only RestoreRun; no adapter-side drop once `snapshot.obligations` exists. | `test_restore_is_typed_untrusted_and_silent_for_missing_or_corrupt` pins full snapshot embedding (`plugins/empirica/adapters/claude/tests/test_claude_adapter.py:307-329`). |
| Codex spawn denial (`_pre_tool_use`) | Native deny with `result.reason` only (`plugins/empirica/adapters/codex/lifecycle.py:289-303`). | structured budget obligation and discharge condition. | End-to-end Codex test asserts deny decision but not content (`plugins/empirica/adapters/codex/tests/test_codex_adapter.py:318-326`). |
| Codex CLI-dispatch denial | Native deny with `reserved.reason` only (`plugins/empirica/adapters/codex/lifecycle.py:324-337`). | Same. | **UNVERIFIED:** no focused assertion located by the requested grep. |
| Codex Stop block/fault | Native `decision:block` with `reason`/`message` only (`plugins/empirica/adapters/codex/lifecycle.py:354-371`). | Block obligations never reach the agent. | The Codex integration asserts `decision == "block"`, not semantic content (`plugins/empirica/adapters/codex/tests/test_codex_adapter.py:252-255`). |
| Codex terminal Allow | `systemMessage` is JSON of the typed result (`plugins/empirica/adapters/codex/lifecycle.py:368-370`). | It can preserve a proposed `run.handoff`, but it currently does not exist. | Codex integration parses only `converged` from the system message (`plugins/empirica/adapters/codex/tests/test_codex_adapter.py:352-356`). |
| Codex compaction restore | JSON-dumps active `snapshot` (`plugins/empirica/adapters/codex/lifecycle.py:374-393`). | Today inherits counts-only snapshot; it need not transform a new obligations field. | Codex integration verifies the untrusted envelope begins (`plugins/empirica/adapters/codex/tests/test_codex_adapter.py:276-280`) and intentionally expects no terminal reinjection (`:358-360`). |

### B-3 — required projection and exact witnesses

**Statement.** The projection can be deterministic from the normalized graph, `claims` derivations, and the injected evidence/audit oracles; it must not parse prose as its primary decision mechanism.

**Evidence.** Claim state is derived: refuted → discarded; blocked tag → blocked; confidence plus approving evidence → approved; otherwise open (`plugins/empirica/core/claims.py:36-51`). `pending()` supplies open gating claims (`plugins/empirica/core/claims.py:95-99`), and `blocked_residuals()` supplies non-gating human residuals (`plugins/empirica/core/claims.py:101-105`). The evidence oracle returns `(ok, reason)` and supplies the absent-evidence fallback exactly as `"no recorded {purpose} evidence for {node_id}"` (`plugins/empirica/application/knowledge.py:247-282`). The core specifies folds: research for every claim and a passing exit-code spike for `needs-experiment` (`plugins/empirica/core/convergence.py:28-35`). Audit reason strings are deterministic and enumerated (`plugins/empirica/core/audit.py:38-99`).

**Projection algorithm (exact v1 design).** Deterministically sort by obligation id. Each projected obligation has `id="empirica/<claim_id>"`, `must=<node.text>`, `because=[claim_id]`, and a non-empty `witnesses` list. Do not expose GSN edges, digests, artifacts, or claim confidence as the consumer contract. The in-toto/evidence machinery remains internal.

| Source condition | Obligation `state` | Exact witness refs | `because` | `must` / notes |
|---|---|---|---|---|
| `claims.pending()` node of kind `needs-data` and `evidence(claim_id,"approve")` false | `open` | `{"kind":"artifact","ref":"research/<claim_id>","expect":"a recorded Fold-1 research attestation supporting this claim"}` | `[claim_id]` | node text |
| `claims.pending()` node of kind `needs-experiment` and no Fold-1 research | `open` | research witness above **and** `{"kind":"exit_code","ref":"spike/<claim_id>","expect":"0 from the recorded deterministic Fold-2 spike after research"}` | `[claim_id]` | node text. Both witnesses make the two-fold discharge condition explicit. |
| `needs-experiment` with research but no passing/current spike | `partial` | same two witnesses; the projection may include the first in `satisfied_witnesses` only if Lane-A view supports it; do **not** remove the research witness because it is part of the discharge contract | `[claim_id]` | node text |
| `Block(kind="audit_failed")` | `open` | `{"kind":"judgment","ref":"audit/<argument_digest>","expect":"independent audit pass covering the current argument and every approved claim"}` | Sorted approved claim ids; use `[graph["root"]]` only when no approved claims exist | Audit's action is run-level, so create `id="empirica/audit/<argument_digest>"`; argument digest is already computed at `plugins/empirica/core/claims.py:117-142`. Never claim a generic judgment can discharge machine witnesses. |
| `blocked_residuals()` with tag `needs-decision` | `blocked` | `{"kind":"judgment","ref":"decision/<claim_id>","expect":"a human decision recorded for this claim"}` | `[claim_id]` | node text |
| `blocked_residuals()` with `needs-data` | `blocked` | research witness | `[claim_id]` | node text |
| `blocked_residuals()` with `needs-experiment` | `blocked` | research + spike witnesses | `[claim_id]` | node text |
| `blocked_residuals()` with `needs-budget` | `blocked` | `{"kind":"event","ref":"budget/<claim_id>","expect":"an authorized budget increase recorded before further work"}` | `[claim_id]` | node text |
| `deferred = gating - frozen_claims` | `deferred` | derive witnesses by kind exactly as the open cases | `[claim_id]` | node text; deferred is not discharged and must be present in the final handoff. |
| Pass/spawn cap stops a still-open graph | `blocked` (not discharged) | above claim witnesses plus synthetic `{"kind":"event","ref":"budget/run","expect":"authorized pass/spawn budget increase"}` on `id="empirica/run/budget"` | all sorted residual claim ids | This explains `stopped_budget` without inventing a claim. |
| `stopped_residual` due to no progress / audit stall | keep each residual's state; add `id="empirica/run/stall"`, `state:"blocked"`, witness `event:resume/<run-handle>` expecting renewed trusted evidence/audit observation | residual claim ids | This is an operational termination reason; it must not erase the claim-level obligations. |

**State mapping.** Start with claim derivation: pending → `open` (or `partial` only where the projection can establish that Fold 1 is already current); `blocked_residuals` → `blocked`; `deferred` → `deferred`. A frozen run must never relabel deferred obligations as discharged: frozen membership is the committed scope, while `gating - frozen_claims` is deferred (`plugins/empirica/application/service.py:260-265`; `plugins/empirica/core/convergence.py:97-104`). `stopped_budget` adds the run budget blocker but preserves per-claim state; `stopped_residual` adds stall blocker but preserves per-claim state; `stopped_frozen` retains deferred. A converged Allow emits all deliverable/invariant obligations as `discharged` only after trusted observations (including passed audit) have been mapped by the generic verifier. **UNVERIFIED:** exact Lane-A `project()` JSON fields for partial/satisfied witnesses; this report requests a frozen export below.

**Reason strings keyed off only as stable explanatory text, not semantic classification.**

* evidence absence: `no recorded {purpose} evidence for {node_id}` (`plugins/empirica/application/knowledge.py:280-282`);
* block guidance states `FOLD 1`, Fold 2 for `needs-experiment`, and the legal blocked tags (`plugins/empirica/core/convergence.py:28-35`);
* audit: `no independent audit was performed: ...`, `an auditor was spawned but no readable verdict is present; ...`, nonce mismatch, `the independent audit FAILED: ...`, different/missing argument digest, and `the audit does not cover the run's current state: ...` (`plugins/empirica/core/audit.py:38-99`).

### B-4 — wire, persistence, and schema changes

**Statement.** Additive v1 fields are legal because the response `block`, `allow`, and `run` definitions permit extra properties.

**Evidence.** `additionalProperties:true` appears on run at `contracts/empirica/v1/response.schema.json:21-31`, Allow at `:33-41`, and Block at `:43-51`.

**Exact wire change.**

```python
# application/wire.py
def block(reason: str, run: dict, *, obligations: list[dict] | None = None) -> dict:
    result = {"type": "Block", "reason": reason, "run": run}
    if obligations is not None:
        result["obligations"] = obligations
    return result
```

Call it with `obligations=projected_view` in all three convergence `_finalize_block` paths. The cap-denial call takes its synthetic run budget obligation. Never make it positional: existing callers retain source compatibility.

**Required agent-facing shapes.** Use only Lane-A's canonical `project(contract, verdict)` obligation view, byte-for-byte; the following placements are additive:

```json
// Block
{"type":"Block","reason":"...","obligations":[
  {"id":"empirica/G7","must":"...","witnesses":[
    {"kind":"artifact","ref":"research/G7","expect":"..."}],
   "state":"open","because":["G7"]}],"run":{"id":"...","status":"active","revision":7}}

// RestoreRun
{"type":"Allow","converged":false,"run":{"id":"...","status":"active","revision":7,
 "snapshot":{"...":"existing telemetry", "obligations":[/* same project view */]}}}

// Terminal Allow final handoff
{"type":"Allow","converged":false,"run":{"id":"...","status":"stopped_budget","revision":8,
 "handoff":{"contract_id":"empirica/<opaque-run-handle>","revision":1,
             "artifact_id":"<ArtifactRepository id>",
             "obligations":[/* final canonical project view, including residuals */]}}}
```

For `converged`, `stopped_residual`, and `stopped_frozen`, always emit `run.handoff`; for any `Allow` that surfaces an already-terminal state, reload the persisted contract artifact and emit the same handoff. Persist a contract artifact after projection using the existing `ArtifactRepository` append discipline (the repository is explicitly append-only at `plugins/empirica/application/service.py:11-14`). **UNVERIFIED:** artifact body/kind envelope and repository append API have not been specified by the supplied scope; confirm against `core.ports`/`core.records` before implementation.

**Schema edits.** In `contracts/empirica/v1/response.schema.json`, add `$defs.obligationWitness` and `$defs.obligation` as refs to / a mirrored subset of the Lane-A v1 schema, and `obligations: {"type":"array","items":{"$ref":"#/$defs/obligation"}}` to `block`. Add `snapshot` and `handoff` documented definitions to `$defs.run` while preserving `additionalProperties:true`; `snapshot.obligations` and `handoff.obligations` use the same item schema. This turns a currently permissive additive wire placement into an explicit consumer contract.

**Fixtures.** Update `contracts/fixtures/empirica-block-audit.json` to have one `judgment:audit/<digest>` obligation; update `empirica-reserve-spawn-denied.json` with the budget event obligation; update `empirica-restore-run.json` to retain counts and add a full open obligation. Add:

* `contracts/fixtures/empirica-block-open-claim.json` (research + spike witnesses, `because:["G0"]`);
* `contracts/fixtures/empirica-terminal-handoff-budget.json` (`stopped_budget`, final handoff and residual); and
* `contracts/fixtures/empirica-terminal-handoff-frozen.json` (`stopped_frozen`, deferred claim preserved).

The current fixture validator only confirms envelope/schema identity and request-id equality; it does not validate JSON Schema instances (`scripts/validate_contracts.py:35-58`). Therefore add fixture instance-validation in the Lane-A validator or extend that script, otherwise these schema definitions are documentation rather than a deterministic gate.

### B-5 — regression and falsification tests

**Statement.** The existing application test style is top-level functions using `make_service()`, `result()`, and `check()`, so new Python tests should follow it.

**Evidence.** RS2 uses exactly that style at `plugins/empirica/tests/test_application.py:1228-1235`; helpers are exercised throughout the file, for example cap test at `:467-492`.

**Tests to add/update.**

1. **`test_obligations_survive_block_restore_adapters_and_terminal_handoff()`** in `plugins/empirica/tests/test_application.py` (plus focused adapter tests in their existing suites). Build a graph with a `needs-experiment` G0 at confidence 0 and `max_passes=1`; `EvaluateRun(stop)` first returns Block. Assert the canonical `project()` result is `preserved(before, block["obligations"])`; `RestoreRun.snapshot.obligations` is preserved from the same before-view; pass that actual response into Claude `stop_result` and assert stderr contains a JSON-delimited obligations rendering, and into Claude/Codex restore renderers and assert the canonical JSON is present. Drive the next progress stop to `stopped_budget`, assert `run.handoff.obligations` preserves the preceding obligations except an explicit trusted state transition, and assert the handoff's `artifact_id` resolves to the persisted contract artifact. Assert `preserved()` at *each* hop, not merely matching obligation count.

2. **`test_restore_open_claim_mutation_is_detected()`** in the same file. Create two distinguishable open claims (for example G-research and G-spike) so a count cannot substitute for identity. Capture `before = project(...)`, get the real RestoreRun obligations, and require `preserved(before, restored)`; then construct the mutation `mutant = dict(restored); mutant["obligations"] = []` (and separately one with only the first obligation) and `check("...", preserved(before, mutant) is not True, ...)`. This falsifies the exact historical regression: deleting `open_claims`/reverting to counts means RestoreRun no longer has a `snapshot.obligations` view, so the real preservation assertion fails. Do **not** write a tautological test that tests `preserved()` only against manually supplied data.

3. Update **`test_restore_includes_graph_view()`** / RS2 to assert telemetry counts *and* exact actionable obligations: IDs, `because`, `must`, state, and witness `(kind,ref,expect)`, not count only (`plugins/empirica/tests/test_application.py:1228-1235`).

4. Add Claude **`test_stop_block_renders_structured_obligations_after_reason()`**: `stop_result` must retain exit 2 and prose first, then render canonical JSON after it; test two obligations and both witness refs. Update `test_stop_mapping_inert_terminal_corrupt_cap_and_audit` to expect the additional rendering only for a Block with obligations (`plugins/empirica/adapters/claude/tests/test_claude_adapter.py:259-305`).

5. Add Codex **`test_stop_and_deny_render_structured_obligations()`** and **`test_restore_preserves_obligations_in_context()`** in `plugins/empirica/adapters/codex/tests/test_codex_adapter.py`: native decision remains block, but reason/system context includes a delimited serialized obligation view; restore includes it verbatim. This validates the actual adapter boundary rather than assuming `json.dumps(snapshot)` remains unchanged.

### B-6 — SKILL.md lines requiring change for a true resume contract

**Statement.** Four prose regions make promises that the current counts-only output cannot meet or omit the new final contract and Pi boundary.

**Evidence.** Runtime boundary excludes Pi (`plugins/empirica/skills/empirica/SKILL.md:101-111`); Fold requirements and legal human residuals are stated at `:204-250,296-303`; Stop/compaction asserts missing-fold reinjection at `:363-366`; Handoff contains only deliverable/deferred-list prose at `:548-557`; internal-memory language is at `:545-546`.

**Exact line-level edits planned.**

* **101–111:** revise `RestoreRun` guidance to say the returned `snapshot.obligations` is the sole resume contract, contains every residual obligation with `must`, state, `because`, and exact witnesses, and agents must not infer work from counts/history. Preserve the current storage prohibitions. (Lane C owns the separate Pi paragraph; see request.)
* **204–250 and 296–303:** align terms with the projected witnesses: research maps to `artifact:research/<claim_id>`, deterministic spike to `exit_code:spike/<claim_id>`, and human decision to `judgment:decision/<claim_id>`. State that a judgment does not discharge a machine witness.
* **363–366:** replace “block message tells you” / “re-injects the graph” with the precise promise: Block emits `obligations`; compact RestoreRun emits the same canonical obligation view; each lists claim provenance and discharge witnesses; counts are telemetry only.
* **417–475:** add the audit witness: unresolved audit is `judgment:audit/<argument_digest>` and names the audit coverage condition, while the graph/evidence internals stay private.
* **545–557:** change “claim graph ... internal memory” to distinguish private internals from the persisted `run.handoff` contract artifact; require final handoff to include its revision, canonical obligations, accepted witnesses, and explicit residuals for `stopped_budget`, `stopped_residual`, and `stopped_frozen`.

## Claims refuted

1. **Refuted:** “RestoreRun re-injects the graph including missing folds” as an implementation fact. It returns only four graph counts (`plugins/empirica/application/service.py:244-266`), yet SKILL.md promises missing-fold reinjection (`plugins/empirica/skills/empirica/SKILL.md:363-366`).
2. **Refuted:** “The Block’s rich core remediation reaches hosts.” Core calculates `ClaimReason` (`plugins/empirica/core/convergence.py:111-123`), but wire serialization drops it (`plugins/empirica/application/wire.py:195-196`), Claude emits reason only (`plugins/empirica/adapters/claude/completion.py:90-93`), and Codex block/deny paths emit reason only (`plugins/empirica/adapters/codex/lifecycle.py:300-301,334-337,362-367`).
3. **Refuted:** “Terminal non-converged Allows provide a cold-start semantic handoff.” Their paths emit status/note/reporting lists but no obligation contract artifact or handoff (`plugins/empirica/application/service.py:897-904,920-930,987-998`).

## Open/blocked (what was tried)

* **Lane-A API dependency — blocked.** I inspected the mission’s frozen v1 types and this worktree’s Empirica call sites. No `lib/obligations/`, vendor copy, or public Python serialization/projection API exists in the inspected starting code. Therefore names such as `Contract`, `verify`, `project`, and `preserved` are plan-level assumptions from the brief, not verified repository API. Implement only after Lane A supplies the requested exported signatures and fixture shape.
* **Artifact persistence envelope — blocked.** I confirmed the service receives an append-only `ArtifactRepository` port (`plugins/empirica/application/service.py:11-14`) but did not inspect its complete implementation/API in this recon phase. The exact artifact kind/body and id retrieval are **UNVERIFIED**.
* **Partial Fold-1 state — blocked by present oracle shape.** `build_evidence_oracle()` returns one boolean/reason for `purpose="approve"` (`plugins/empirica/application/knowledge.py:247-282`), so it cannot by itself distinguish Fold-1-complete/Fold-2-missing from both-missing. The graph kind plus stored in-toto leaves can establish it, but that needs a pure helper over `Knowledge.evidence_leaves`; do not regex-match human reasons to decide state.
* **No external host claims were used.** No external facts require URL/passages in this report.

## Files changed

* `/Users/dmitry.lambrianov/.pi/agent/sessions/--Users-dmitry.lambrianov-Desktop-code-siffran--/subagent-artifacts/outputs/d4449e59-07fd-4cbe-9a31-7d34689b8da7/lane-b-terra.md` — this read-only recon report.
* No repository source, test, fixture, schema, or documentation file was modified.

## Requests to other lanes (exact diffs)

### Request to Lane A — freeze/importable obligation API before Lane B writes consumption

```diff
+++ lib/obligations/__init__.py
+from .model import Contract, Obligation, Observation, Verdict, Witness
+from .verify import preserved, project, revise, verify
+
+def obligation_to_json(obligation: Obligation) -> dict: ...
+def view_to_json(view: object) -> list[dict]: ...
```

```diff
+++ plugins/empirica/vendor/obligations/__init__.py
+# byte-identical vendored copy of lib/obligations/__init__.py
```

```diff
+++ contracts/obligations/v1/fixtures/empirica-consumption-shape.json
+{"obligation":{"id":"empirica/G0","must":"...","witnesses":[
+ {"kind":"artifact","ref":"research/G0","expect":"..."}],
+ "state":"open","because":["G0"]}}
```

Rationale: Lane B must not locally invent a second obligation JSON encoding. The path is a request because the existing fixture location is **UNVERIFIED**; if Lane A fixes fixtures under `contracts/obligations/v1/fixtures/`, use that canonical location rather than this proposed one.

### Request to Lane C — Pi receives the identical view, without changes to claim projection

```diff
--- plugins/empirica/adapters/pi/src/translate.ts
+++ plugins/empirica/adapters/pi/src/translate.ts
@@ every Block/RestoreRun/terminal-Allow rendering branch
- render(result.reason)
+ render(result.reason, result.obligations ?? result.run?.snapshot?.obligations ??
+                       result.run?.handoff?.obligations ?? [])
```

```diff
--- plugins/empirica/skills/empirica/SKILL.md
+++ plugins/empirica/skills/empirica/SKILL.md
@@ Runtime boundary paragraph (lines 106-111)
+On Pi, the adapter renders this same canonical obligation view where Pi has a notice/context
+surface; Pi's enforcement and lifecycle gaps are named explicitly, not implied away.
```

Lane B’s `core/obligations.py` remains the sole Empirica claim-to-obligation projection; Pi consumes its wire output only.

## Residual risks

1. `ClaimReason` is capped to ten open claims in `_blocked_converging` (`plugins/empirica/core/convergence.py:114-123`); projection must traverse the graph itself rather than derive obligations from `Block.open_claims`, or obligations 11+ disappear.
2. A pass-cap transition turns a Block into Allow (`plugins/empirica/application/service.py:897-904`); without projecting before/after the transition and persisting handoff, this remains a loss boundary.
3. Current `RestoreRun` intentionally does not re-inject terminal runs in Codex (`plugins/empirica/adapters/codex/tests/test_codex_adapter.py:358-360`). The final handoff must therefore travel in terminal Stop Allow, not rely on compaction.
4. Schema permissiveness alone does not validate fixture instances (`scripts/validate_contracts.py:35-58`); instance validation must be added before considering this wire contract enforced.
5. The requested named witness refs are semantic conventions. Their connection to actual evidence artifact IDs is **UNVERIFIED** until the projection and observation mapping define it explicitly.

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "Read-only lane completed the requested code-grounded Empirica consumption implementation plan; no scope-widening repository edit was made."
    }
  ],
  "changedFiles": [
    "/Users/dmitry.lambrianov/.pi/agent/sessions/--Users-dmitry.lambrianov-Desktop-code-siffran--/subagent-artifacts/outputs/d4449e59-07fd-4cbe-9a31-7d34689b8da7/lane-b-terra.md"
  ],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {
      "command": "make help",
      "result": "passed",
      "summary": "Listed repository lifecycle targets as required by CLAUDE.md."
    },
    {
      "command": "git diff --cached --quiet",
      "result": "passed",
      "summary": "Exit 0; no staged files."
    }
  ],
  "validationOutput": [
    "No implementation validation was run because this lane was explicitly READ-ONLY recon.",
    "The report identifies the required future regression and mutation tests."
  ],
  "residualRisks": [
    "Lane-A obligation serialization API and artifact persistence envelope are not yet verified.",
    "Existing contract fixture validation does not validate schema instances."
  ],
  "noStagedFiles": true,
  "diffSummary": "Only the authoritative subagent report was written; repository files were not changed.",
  "reviewFindings": [
    "blocker: current Block, RestoreRun, and terminal Allow paths discard or omit actionable obligation state; planned tests target every identified boundary."
  ],
  "manualNotes": "The user-requested repository report path was not written because the runtime authoritative output-path override required this exact output path."
}
```