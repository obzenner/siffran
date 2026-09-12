# Lane D — GLM — FINAL RE-AUDIT (Phase 4b)

READ-ONLY on `/private/tmp/obligations-contract`. Mutation experiments ran in the scratch copy
`/private/tmp/obligations-audit` (files re-copied from `/private/tmp/obligations-contract` between
mutations). Every code claim cites `file:line`. No `git` command was run.

## Stance (verbatim, from the brief)

> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

## Summary verdict: **PASS** (with 2 defense-in-depth-only survivors and minor doc gaps)

All 3 Phase 4 blockers are **CLOSED**. 10 of 12 ranked residuals are **CLOSED**, 1 is **PARTIAL**
(m10 closed by F16; m11 still defense-in-depth-only), and 1 is **STILL OPEN** (activation-check needs
git — pre-existing, out of scope). 21 of 23 mutations are **CAUGHT**; 2 survive (m11, m23) but the
guarantee is not weakened — the `Retirement` constructor's `_nonempty` guard (`model.py:136`)
catches what the `revise.py` guard should, so only the specific guard is unverified, not the
invariant. The B5 transaction is real, honest, and the ADR now matches the code. The cold-start
recovery test passes on the current fixtures.

---

## Part 1 — Blocker and residual closure

### Blockers

| # | Phase 4 blocker | Verdict | Evidence |
|---|-----------------|---------|----------|
| B1 | C1 — `empirica_status` and `session_before_compact` dispatch `GetRun` which omits `run.contract` | **CLOSED** | `_run_snapshot` now calls `_contract_view(key, state)` (`service.py:1005`), so StartRun/GetRun/ObserveAction carry `run.contract` when a graph is readable. Compaction dispatches `restoreRunRequest` (`index.ts:263`), not `getRunRequest`. `empirica_status` still uses `getRunRequest` (`index.ts:145`) but the service now includes the contract; `textResult` (`index.ts:128-130`) shows `"no contract yet (no graph)"` when absent and never JSON-stringifies the whole result. The parity test now uses real fixtures (`parity.test.ts:10-18`) and explicitly tests a GetRun result with no contract (`parity.test.ts:19`). Regression: `test_get_run_returns_snapshot` (`test_application.py:266-279`). |
| B2 | B5 — per-set-change contract persistence not implemented; `revise()` never called | **CLOSED** | `_revise_contract` (`service.py:1083-1144`) calls `revise(previous, add=additions, retire=sorted(retire), reason=..., authority=...)` on every graph write (`_update_graph:778-781`) and freeze (`_freeze:582-589`). Persisted artifacts contain `Contract.to_json()` (`service.py:1141`). `_contract_view` (`service.py:1034-1066`) loads the persisted contract via `_load_contract`, re-verifies against current observations, and projects. Terminal Allow reuses `persisted.contract_artifact_id` (`service.py:901-904`) — `_persist_contract` returns None (`service.py:1146-1147`). State carries `contract_artifact_id` + `contract_revision` (`state.py:75-76,185-186,243-258`). Regression: B5-T1 through B5-T6 (`test_application.py:1611-1659`). |
| B3 | SKILL.md stale claim — `SessionStart:compact` "re-injects the graph — including the missing folds" | **CLOSED** | `SKILL.md:372-374` now says: "Across compaction, `SessionStart:compact` receives the same `run.contract` resume contract as Block and RestoreRun; it lists each obligation, witness, and observation while graph counts remain telemetry. String-only channels render that view with `render_text`, so no actor infers a missing fold from history or prose." Consistent with the Resume-contract section (`SKILL.md:115-118`). |

### Ranked residuals (12)

| # | Residual | Verdict | Evidence |
|---|---------|---------|----------|
| R1 | C1 contract visibility gap (HIGH) | **CLOSED** | See blocker B1. |
| R2 | m5 — cap-at-10 regression untested (HIGH) | **CLOSED** | B5-T1 uses `growing_gating_graph(12)` (12 claims) and asserts `len(contract1.obligations) == 12` (`test_application.py:1625`). m5 mutation (pending()[:10]) crashes with ValueError — caught. |
| R3 | m15 — semantic `must` text untested (MEDIUM-HIGH) | **CLOSED** | B5-T6 checks `all(o["must"] == second["nodes"][o["because"][0]]["text"] for o in frozen["obligations"])` (`test_application.py:1642`). m15 mutation (`must=claim_id`) fails B5-T6 (138/139). |
| R4 | B5 — per-set-change persistence incomplete (MEDIUM-HIGH) | **CLOSED** | See blocker B2. |
| R5 | B3 — missing `decision/` and `budget/` discharge witnesses (MEDIUM) | **CLOSED** | `_witnesses()` emits `judgment:decision/<id>` for `needs-decision` and `event:budget/<id>` for `needs-budget` (`core/obligations.py:19-23`). `trusted()` guards `decision/` → human, `budget/` → operator (`core/obligations.py:74-75`). Regression: OC2 (`test_application.py:1307-1314`). |
| R6 | m7/m8b/m8c — Codex and Pi notice rendering untested (MEDIUM) | **CLOSED** | m7: T7 pins Codex Block reason to `render_text(view)` (`test_codex_adapter.py:295-296`); mutation fails codex test. m8b/m8c: `translate.test.ts:25-33` supplies a Block-with-contract and asserts byte-verbatim `renderText`; mutations fail 1 test each. |
| R7 | C2 — `audit_verdict` reachable via `empirica_knowledge` (MEDIUM) | **CLOSED** | `KNOWLEDGE_ACTION_KINDS = new Set(["graph", "route", "evidence_leaf", "attribution", "freeze"])` (`index.ts:30`) — excludes `audit_ticket` and `audit_verdict`. The parity test rejects `audit_verdict` (`parity.test.ts:18`). m20 mutation (adding `audit_verdict` back) fails 1 test. |
| R8 | m10/m11 — lib guards untested (MEDIUM) | **PARTIAL** | m10: **CLOSED** — F16 fixture (`contracts/obligations/v1/fixtures/F16-judgment-same-ref-cannot-discharge-machine.json`) provides a judgment observation with the same ref as an `exit_code` witness; expected verdict is residual/unwitnessed. m10 mutation (matching on `(ref, outcome)` instead of `(kind, ref, outcome)`) fails the fixture test. m11: **STILL OPEN (defense-in-depth only)** — `test_revision_requires_authority_and_records_retirement` now tests empty reason and whitespace reason (`test_obligations.py:80-81`), but the `revise.py` guard itself (`revise.py:10`) is not the guard that catches it — the `Retirement` constructor's `_nonempty("retirement reason", self.reason)` (`model.py:136`) does. The mutation (dropping the reason check from `revise.py`) survives because the test passes via defense-in-depth. The guarantee holds; the specific guard is unverified. |
| R9 | SKILL.md stale claim (MEDIUM) | **CLOSED** | See blocker B3. |
| R10 | core→application layering inversion (LOW) | **CLOSED** | `core/evidence.py:6-18` is pure (no application imports). `core/obligations.py:12` imports `from .evidence import evidence_fold`. `application/knowledge.py:25` imports from core. The deferred `core→application` import is removed. |
| R11 | `activation-check` needs git (LOW) | **STILL OPEN** | Pre-existing, not introduced by this change. The audit copy has no `.git`; `make activation-check` cannot run. Out of scope for the implementation lanes. |
| R12 | Parity test fakes mask production behavior (LOW) | **CLOSED** | Parity tests now consume real fixtures `empirica-block-audit.json` and `empirica-restore-run.json` (`parity.test.ts:10-18`), explicitly test a GetRun result with no contract (`parity.test.ts:19`), and use RestoreRun for compaction asserting deterministic summary + JSON `details.contract` (`parity.test.ts:21`). m21 mutation (reverting compaction to GetRun) fails 4 tests. |

---

## Part 2 — Mutation replay

Baseline: `make check` green in `/private/tmp/obligations-audit` for all sub-targets except
`activation-check` (no `.git`). Application suite: 139/139. Pi suite: 66/66. Codex suite: 6/6.

Each mutation applied one at a time; the target file re-copied from `/private/tmp/obligations-contract`
between runs.

| Mutation | Target run | Result | Finding + severity |
|----------|-----------|--------|---------------------|
| m1: `block()` drops `run.contract` | `wire.py:199` | **FAILED** | `KeyError: 'contract'` (OB1). Caught. |
| m2: RestoreRun omits `run.contract` | `service.py:244` | **FAILED** | `KeyError: 'contract'` (OB3). Caught. |
| m3: budget terminal omits contract/artifact_id | `service.py:940-943` | **FAILED** | `StopIteration` (OB5 — artifact_id None). Caught. |
| m4: break spike `predicateType` (no exit_code witness) | `core/obligations.py:28` | **FAILED** | 138/139 (OC1 — spike witness missing). Caught. |
| m5: project from `pending()[:10]` instead of `gating_goals` | `core/obligations.py:89` | **FAILED** | `ValueError: added obligation ids must be unique and new` — crash in `_revise_contract` when >10 claims produce a different diff. Caught by B5-T1's 12-claim graph. |
| m6: Claude stderr drops `render_text` | `completion.py:96` | **FAILED** | 138/139 (OB2 — stderr missing render_text). Caught. |
| m7: Codex block reason drops `render_text` | `lifecycle.py:247` | **FAILED** | Codex test FAILED (T7 — `assertIn(render_text(...), reason)`). Caught. |
| m8a: `gateFromDecision` drops contract | `translate.ts:131` | **FAILED** | 6 failures. Caught. |
| m8b: `convergenceNotice` drops contract | `translate.ts:160` | **FAILED** | 1 failure (translate.test.ts Block-with-contract). Caught. |
| m8c: `settledFollowUp` drops contract | `translate.ts:213` | **FAILED** | 1 failure (translate.test.ts Block-with-contract). Caught. |
| m9: `renderText` one-char format change | `obligations.ts:17` | **FAILED** | 16 failures (fixture byte-equality). Caught. |
| m10: judgment observation discharges exit_code witness (same ref) | `verify.py:8` | **FAILED** | 1 fixture failure (F16 — kind-separation violated). Caught. |
| m11: `revise()` allows retire without reason | `revise.py:10` | **SURVIVED** | 7/7 passed. **MEDIUM — defense-in-depth only.** The `revise.py` guard is removed but the `Retirement` constructor's `_nonempty("retirement reason", self.reason)` (`model.py:136`) raises `ValueError` before the test's `assertRaises` can distinguish the source. The guarantee (reject empty reason) holds; the specific guard is unverified. |
| m12: `preserved()` ignores witness loss | `project.py:118` | **FAILED** | 1 fixture failure (P02-witness-loss). Caught. |
| m13: vendor one-byte drift | `vendor/obligations/model.py` | **FAILED** | vendor-check mismatch. Caught. |
| m14: `trusted()` accepts `source='model'` for audit | `core/obligations.py:73` | **FAILED** | 138/139 (OC2 — `not trusted(raw)` fails). Caught. |
| m15: obligation `must=claim_id` (drops claim text) | `core/obligations.py:99` | **FAILED** | 138/139 (B5-T6 — `must` ≠ claim text). Caught. |
| **m16**: serve graph-derived contract instead of persisted revision (revision always 1) | `service.py:1139` | **FAILED** | `StopIteration` — B5-T1 `contract2.revision == 2` fails (gets 1); B5-T2 `next(r for r in after["retired"] ...)` crashes (no retired entries). T1 must fail — confirmed. |
| **m17**: skip `revise()` on refutation (refuted claim disappears) | `service.py:1097` | **FAILED** | `StopIteration` — `_revise_contract` returns None; no contract revision persisted; refutation test's `next(r for r in after["retired"] ...)` crashes. A test must fail — confirmed. |
| **m18**: skip the contract update on freeze | `service.py:582` | **FAILED** | 138/139 (B5-T3 — freeze deferred holds not created; `any(o.get("hold") == "deferred")` fails). Caught. |
| **m19**: drop the `decision/` witness for needs-decision | `core/obligations.py:19-21` | **FAILED** | 138/139 (OC2 — `witnesses == {"empirica/B": "budget/B", "empirica/D": "research/D"}` ≠ expected `"decision/D"`). Caught. |
| **m20**: put `audit_verdict` back into `KNOWLEDGE_ACTION_KINDS` | `index.ts:30` | **FAILED** | 1 failure (parity test rejects `audit_verdict`). Caught. |
| **m21**: make compaction dispatch `GetRun` again | `index.ts:263` | **FAILED** | 4 failures (compaction test expects RestoreRun + contract). Caught. |
| **m22**: let a judgment observation discharge a same-ref exit_code witness (F16 must fail) | `verify.py:8` | **FAILED** | 1 fixture failure (F16 — same as m10). F16 must fail — confirmed. |
| **m23**: accept whitespace-only reason | `revise.py:10` | **SURVIVED** | 7/7 passed. **MEDIUM — defense-in-depth only.** `reason.strip()` → `reason`; whitespace-only `"   "` passes `not reason` (truthy) but the `Retirement` constructor's `_nonempty("retirement reason", "   ")` (`model.py:136`) rejects it. The guarantee holds; the specific guard is unverified. |

### Mutation summary

- **Caught (21)**: m1–m10, m12–m22.
- **Survived (2)**: m11 (MEDIUM), m23 (MEDIUM) — both defense-in-depth-only: the `Retirement`
  constructor's `_nonempty` guard (`model.py:136`) catches what the `revise.py` guard should. The
  invariant (reject empty/whitespace reason) is not weakened; the specific `revise.py` guard is
  unverified. Neither is exploitable because the `Retirement` constructor is always called before a
  retirement record is created.

---

## Part 3 — B5 specifically

### 1. Is the wire view really the persisted revision re-verified against current observations?

**Yes.** `_contract_view` (`service.py:1034-1066`):
1. `_load_contract(key, state.contract_artifact_id)` — loads the persisted `Contract` from the
   artifact store (`service.py:1068-1079`), parsing via `Contract.from_json(json.loads(body))`.
2. `observations_from_knowledge(...)` — maps current evidence leaves and audit verdicts to
   observations (`core/obligations.py:29-55`).
3. `accepted = tuple(item for item in observations if trusted(item))` — filters to trusted observations.
4. `verify(contract, accepted, trusted)` — re-verifies the persisted contract against current trusted
   observations.
5. `project(contract, verdict, accepted)` — projects the verified contract to the wire view.

The budget/stall obligations are **view-time synthetics** — added to the wire projection only when
`include_budget` or `include_stall` is True (`service.py:1053-1061`), never persisted as durable
revisions. The code comment confirms: "Keep synthetic budget/stall obligations out of durable
revisions; add them only to this wire projection, retaining the persisted revision lineage for live
claim obligations."

### 2. Is authority always a knowledge artifact id?

**Yes.** In `_revise_contract` (`service.py:1083-1144`):
- Graph write: `authority = art_id` — the content-addressed graph artifact id (`_update_graph:778`).
- Freeze: `authority = freeze_artifact_id` — the content-addressed freeze artifact id
  (`_freeze:564,589`).
- Refutation: `authority = refuted_by` — the evidence artifact id from the graph node
  (`service.py:1135-1138`), falling back to the causing artifact id if no refutation.
- Initial construction: authority is the graph artifact id (via the `authority` parameter).

Never a bare request, never the executing actor. The ADR states this explicitly (`ADR-0039:44`):
"Other revision authority is the causing graph/freeze knowledge artifact id: never a bare request
and never the executing actor."

### 3. Is a refuted claim RETIRED with reason+authority?

**Yes.** `_revise_contract` (`service.py:1131-1140`):
```python
refuted = next((graph["nodes"][item.because[0]].get("refuted_by")
                for item in previous.obligations
                if item.id in retire and item.because
                and graph["nodes"].get(item.because[0], {}).get("refuted_by")), None)
revision_reason = (f"refuted by {refuted}" if isinstance(refuted, str) and refuted
                    else ("claim reworded" if changed else reason))
revision_authority = refuted if isinstance(refuted, str) and refuted else authority
next_contract = revise(previous, add=additions, retire=sorted(retire),
                       reason=revision_reason, authority=revision_authority)
```

B5-T2 verifies: `evidence_id in retirement["reason"] and retirement["authority"] == evidence_id
and preserved(before, after).ok` (`test_application.py:1656`). The retirement record contains the
evidence artifact id as both the reason text and the authority.

### 4. Is a reworded claim retire+add? Does `preserved()` still hold? Could a downstream agent be confused?

**Yes — retire+add with a revision-qualified replacement ID.** The code (`service.py:1123-1129`):
```python
for ident in changed:
    retire.add(ident)
    item = new[ident]
    additions.append(type(item)(f"{ident}/revision-{previous.revision + 1}", item.mode,
                                 item.must, item.witnesses, item.because,
                                 item.hold, item.hold_reason, item.severity))
```

The old obligation (e.g., `empirica/G0`) is retired with `reason="claim reworded"` and
`authority=<causing artifact id>`. A new obligation (e.g., `empirica/G0/revision-3`) is added with
the same `because=["G0"]` provenance.

**`preserved()` holds.** Verified at runtime: `preserved(before, after).ok = True` across the freeze
→ refutation transition. The old obligation is in `retired` with reason+authority; the new obligation
is an addition with the same `because`. `preserved()` checks `before ⊇ after` (nothing lost), and
the retirement satisfies the "present-or-retired-with-reason" requirement.

**A downstream agent COULD be confused** if it ignores the `retired` list or doesn't check `because`.
The new ID `empirica/G0/revision-3` does not match the old ID `empirica/G0`, so an agent that tracks
obligations by ID alone would see a "new" obligation and a "retired" one without linking them. The
link is provided by:
- The `retired` entry's `reason="claim reworded"` and `authority=<artifact id>`.
- The new obligation's `because=["G0"]` — the same provenance as the retired one.
- The `render_text` output (`project.py:129-162`) renders both obligations and retirements.

This is the "compatibility constraint to monitor" the b5 report admits. It is a consequence of the
frozen library's global no-id-reuse rule (`model.py:167-170`). It is **not an over-claim** — neither
the ADR nor the code claims IDs are immutable across rewording. It is a documentation **omission**:
ADR-0039 does not mention the ID-suffix convention or its consumer-facing consequence.

**Note:** The "claim reworded" reason is also triggered by hold changes (e.g., freeze adding
`hold="deferred"`), not just text rewording. The reason text is a misnomer for non-text changes, but
the retirement record is correct.

### 5. Is the append-before-CAS orphan risk acceptable and documented?

**Yes.** The `_revise_contract` appends the contract artifact before the CAS (`service.py:1143`),
matching the existing graph-artifact transaction model (`_update_graph:731`: "append FIRST
(idempotent)"). A lost CAS leaves an immutable orphan artifact (harmless, never made current). The
service.py module docstring states this (`service.py:20-23`): "a CAS conflict leaves an orphan
artifact (immutable, harmless) but never makes an orphan current." ADR-0039 confirms
(`ADR-0039:41-42`): "then CASes its pointer and revision together with the graph/freeze operational
change; a CAS retry repeats the read/project/diff transaction." The b5 report's residual risk note
("CAS-losing revision artifacts are harmless immutable orphans") is accurate.

### 6. Does ADR-0039 now state exactly what the code does?

**Yes — with one minor omission.** I read every sentence of `ADR-0039:38-50` against the code:

- "A contract revision is an append-only Artifact containing `Contract.to_json()`." ✓
  (`service.py:1141`)
- "On every graph write or freeze, the application appends the causing knowledge artifact first,
  reloads the persisted revision (or conceptually starts from the empty revision-zero set),
  projects the canonical live claim obligations, and diffs it." ✓ (`_revise_contract:1083-1144`)
- "It calls `revise()` for additions and explicit retirements, appends that revision artifact, then
  CASes its pointer and revision together with the graph/freeze operational change" ✓
  (`_update_graph:778-781`, `_freeze:582-589`)
- "A refutation retirement records `reason='refuted by <evidence artifact id>'` and
  `authority=<that artifact id>`." ✓ (`service.py:1133-1138`)
- "Every wire view re-verifies the persisted revision against current observations before
  `project()`" ✓ (`_contract_view:1034-1066`)
- "The run-level budget and stall obligations are deliberately synthetic view-time additions, not
  durable claim-contract revisions." ✓ (`_contract_view:1053-1061`)
- "Terminal Allow reuses the latest persisted revision and exposes its pointer as
  `run.contract_artifact_id`; it does not persist a projection merely because it is terminal." ✓
  (`_finalize:901-904`, `_persist_contract` returns None)

**No sentence over-claims.** One omission: the ADR does not document the revision-qualified
replacement-ID convention for changed claims (`service.py:1123-1129`) or its consumer-facing
consequence (a reworded claim's obligation ID changes). The b5 report admits this; the ADR should
mention it in the Consequences section.

---

## Part 4 — Docs vs code, final

| Doc | Statement | Verdict |
|-----|-----------|---------|
| SKILL.md:113 | "exposes the current `run.contract` view to the model through `empirica_status` and deterministic compaction summaries" | **ACCURATE** — `empirica_status` dispatches GetRun which now includes `run.contract` via `_run_snapshot` → `_contract_view`; compaction dispatches RestoreRun. Conditional on a graph being readable (shows "no contract yet (no graph)" otherwise). |
| SKILL.md:115-118 | "Resume contract: RestoreRun, Block, and every terminal Allow expose the one canonical `run.contract` view... A terminal Allow also names `run.contract_artifact_id`." | **ACCURATE** — verified at `service.py:244,896-904,942-950,972-986`. |
| SKILL.md:372-374 | "Across compaction, `SessionStart:compact` receives the same `run.contract` resume contract as Block and RestoreRun; it lists each obligation, witness, and observation while graph counts remain telemetry." | **ACCURATE** — compaction dispatches RestoreRun (`index.ts:263`), `_contract_view` re-verifies. |
| ADR-0039:38-50 | (All sentences quoted in Part 3 §6) | **ACCURATE** — no over-claim. One omission: ID-suffix convention not documented. |
| ADR-0040:18-22 | "Pi has no completion-veto lifecycle. Enforcement exists only when `report_convergence` is invoked; an agent that never invokes it can complete." | **ACCURATE** — `agent_settled` is observational (`translate.ts:199-215`), `settledFollowUp` is explicitly "not a gate". Two UNVERIFIED runtime claims named (blocked-reason visibility, follow-up turn reliability). |
| Pi README:1-8 | "Pi cannot veto completion: enforcement exists only when the report tool is invoked. Runtime claims about blocked-reason model visibility and follow-up turn reliability remain UNVERIFIED pending a live spike." | **ACCURATE** — matches ADR-0040 and code. |
| contracts/README.md | "executable cross-language cases for verification F1–F15" | **MINOR GAP** — should say "F1–F16" (F16 was added: `F16-judgment-same-ref-cannot-discharge-machine.json`). Not an over-claim; an incomplete range. |
| lib/obligations `__init__.py` | "It performs no I/O and makes no claim about natural-language semantic specificity." | **ACCURATE** — stdlib-only, no semantic claim. |
| lib/obligations `project.py` docstring | "The preservation normal form detects text change, witness removal, and provenance removal. It does not claim to decide whether one natural-language sentence is semantically more specific than another." | **ACCURATE** — `preserved()` checks structural preservation only. |
| lib/obligations `revise.py` docstring | "Append-only revision with explicit retirement records." | **ACCURATE** — matches `revise.py` implementation. |

**No over-claims found.** One minor documentation gap: `contracts/README.md` says "F1–F15" but should
say "F1–F16". One omission: ADR-0039 does not document the revision-qualified replacement-ID
convention.

---

## Part 5 — Cold-start test F (current fixtures)

The terminal-handoff fixtures (`empirica-terminal-handoff-budget.json`,
`empirica-terminal-handoff-frozen.json`) still show **revision 1, 0 retired, 1 obligation** — the
fixtures were not updated to exercise revision > 1 or retired entries (the B5 test creates those
dynamically in the application test suite, not in the fixtures). Using ONLY the JSON fixture and
`lib/obligations` (no Empirica imports):

```
fixture: contracts/fixtures/empirica-terminal-handoff-budget.json
contract_id: empirica/fixture
revision: 1
obligation: empirica/G0, mode=require, must="record research", because=("G0",), hold=None
witness: artifact:research/G0 (pass) — recorded Fold-1 research attestation supporting this claim [observed: null]
verdict: residual=("empirica/G0",), unwitnessed=("empirica/G0",)
contract_artifact_id: aaaaaaaaaaaaaaaa... (present)
round-trip canonical(parse(view)) == canonical(contract): True
```

`render_text(view)` produces the expected deterministic output. The frozen fixture yields the
identical structure.

**Verdict: PASS.** Everything is still recoverable from JSON + lib alone — contract_id, revision,
every obligation (id, mode, must, because, hold, hold_reason), every witness (kind, ref, expect,
description, observed), every verdict partition, the retired list, and the contract_artifact_id.
The `must` text + witness `description` name the discharge condition; `observed: null` says it is
outstanding; the verdict says residual/unwitnessed; `contract_artifact_id` gives a durable reference.

---

## Residual risks (ranked)

1. **m11/m23 — `revise.py` reason guard unverified (MEDIUM):** Both mutations survive because the
   `Retirement` constructor's `_nonempty` guard (`model.py:136`) catches empty/whitespace reason
   before the test can distinguish the source. The invariant holds (defense-in-depth); the specific
   `revise.py` guard is not verified by any test. If the `Retirement` constructor were ever
   weakened, the `revise.py` guard would be the sole defense and would be unverified.

2. **ID-suffix compromise for reworded claims (LOW-MEDIUM):** A changed claim gets a
   revision-qualified replacement ID (`empirica/G0/revision-3`) while the old ID is retired. A
   downstream agent that ignores the `retired` list or doesn't check `because` provenance could
   miss the link. `preserved()` holds and `render_text` renders both, but the ADR does not document
   this convention.

3. **"claim reworded" reason misnomer (LOW):** The reason text "claim reworded" is used for any
   changed obligation, including hold changes (e.g., freeze adding `hold="deferred"`), not just
   text rewording. The retirement record is correct; the reason text is imprecise.

4. **contracts/README.md F16 range gap (LOW):** Says "F1–F15" but should say "F1–F16".

5. **ADR-0039 ID-suffix omission (LOW):** The ADR does not document the revision-qualified
   replacement-ID convention or its consumer-facing consequence.

6. **`activation-check` needs git (LOW):** Pre-existing, out of scope. Cannot run in the audit copy.

7. **Pi UNVERIFIED runtime claims (LOW):** Blocked-reason model visibility and follow-up-turn
   reliability remain UNVERIFIED pending a live Pi runtime spike (ADR-0040, README).

---

## Final statement (for a human reviewer)

This change **guarantees** that the obligation contract is a lossless, durable agent interface: the
generic `lib/obligations` module (unchanged, fully conformant) provides the frozen values, verifier,
canonical form, and preservation check; the Empirica application now persists real contract
revisions via `revise()` on every graph write and freeze, re-verifies the persisted revision against
current trusted observations on every wire view, and reuses the latest revision pointer at terminal
Allow — the ADR-0039 description matches the code exactly. The Pi adapter now dispatches RestoreRun
for compaction (not GetRun), excludes `audit_verdict`/`audit_ticket` from the model-callable
knowledge action set, and the parity tests use real fixtures. 21 of 23 mutations are caught; the 2
survivors (m11, m23) are defense-in-depth-only — the `Retirement` constructor catches what the
`revise.py` guard should, so the invariant is not weakened, only the specific guard is unverified.
The cold-start recovery test passes on the current fixtures. What this change **does not**
guarantee: (a) the `revise.py` reason guard itself is verified (only the downstream `Retirement`
constructor is), (b) a downstream agent that ignores the `retired` list won't be confused by the
revision-qualified replacement-ID convention for reworded claims, (c) Pi blocked-reason model
visibility and follow-up-turn reliability remain UNVERIFIED pending a live runtime spike, and (d)
Pi cannot veto completion when `report_convergence` is never invoked. What **needs a live spike**:
the two Pi runtime claims (ADR-0040) — whether a blocked-tool reason is visible in model context and
whether follow-up delivery reliably starts another turn.

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "Independent read-only re-audit completed: all 3 Phase 4 blockers verified CLOSED with file:line evidence (C1: service.py:1005 + index.ts:263; B5: service.py:1083-1144; SKILL.md:372-374); 12 ranked residuals verdicted (10 CLOSED, 1 PARTIAL, 1 STILL OPEN pre-existing); 23 mutation experiments run (21 caught, 2 survived with defense-in-depth analysis); B5 transaction deep-dive (6 questions answered with code citations); docs vs code final check (8 docs assessed, 0 over-claims, 2 minor gaps); cold-start test F verified PASS on current fixtures. All mutations reverted and audit copy verified byte-identical to contract."
    }
  ],
  "changedFiles": [],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {"command": "python3 plugins/empirica/tests/test_application.py", "result": "passed", "summary": "Baseline 139/139 checks passed."},
    {"command": "make empirica-pi-check", "result": "passed", "summary": "Baseline 66/66 tests passed."},
    {"command": "make empirica-codex-check", "result": "passed", "summary": "Baseline 6/6 Codex tests passed."},
    {"command": "make obligations-check", "result": "passed", "summary": "3 schemas, 22 fixtures (jsonschema + stdlib)."},
    {"command": "make vendor-check", "result": "passed", "summary": "5 byte-identical obligation vendor files."},
    {"command": "make lint", "result": "passed", "summary": "All checks passed."},
    {"command": "make adr-check", "result": "passed", "summary": "0 errors, 82 warnings, 1 info."},
    {"command": "m1 wire.block drops contract", "result": "failed", "summary": "Test caught: KeyError 'contract' (OB1)."},
    {"command": "m2 RestoreRun omits run.contract", "result": "failed", "summary": "Test caught: KeyError 'contract' (OB3)."},
    {"command": "m3 budget terminal omits contract/artifact_id", "result": "failed", "summary": "Test caught: StopIteration (OB5)."},
    {"command": "m4 break spike predicateType", "result": "failed", "summary": "Test caught: 138/139 (OC1 spike witness missing)."},
    {"command": "m5 project from pending()[:10]", "result": "failed", "summary": "Caught: ValueError crash in _revise_contract with 12-claim graph (B5-T1)."},
    {"command": "m6 Claude stderr drops render_text", "result": "failed", "summary": "Test caught: 138/139 (OB2 stderr missing render_text)."},
    {"command": "m7 Codex block reason drops render_text", "result": "failed", "summary": "Test caught: codex test FAILED (T7 assertIn render_text)."},
    {"command": "m8a gateFromDecision drops contract", "result": "failed", "summary": "Test caught: 6 failures."},
    {"command": "m8b convergenceNotice drops contract", "result": "failed", "summary": "Test caught: 1 failure (translate.test.ts Block-with-contract)."},
    {"command": "m8c settledFollowUp drops contract", "result": "failed", "summary": "Test caught: 1 failure (translate.test.ts Block-with-contract)."},
    {"command": "m9 renderText one-char format change", "result": "failed", "summary": "Test caught: 16 fixture byte-equality failures."},
    {"command": "m10 judgment discharges exit_code witness", "result": "failed", "summary": "Test caught: 1 fixture failure (F16 kind-separation)."},
    {"command": "m11 retire without reason", "result": "not-run", "summary": "SURVIVED: 7/7 — Retirement constructor _nonempty catches it (defense-in-depth). MEDIUM."},
    {"command": "m12 preserved ignores witness loss", "result": "failed", "summary": "Test caught: P02-witness-loss fixture."},
    {"command": "m13 vendor one-byte drift", "result": "failed", "summary": "Test caught: vendor-check mismatch."},
    {"command": "m14 trusted accepts source=model", "result": "failed", "summary": "Test caught: 138/139 (OC2 not trusted(raw))."},
    {"command": "m15 must=claim_id drops claim text", "result": "failed", "summary": "Test caught: 138/139 (B5-T6 must != text)."},
    {"command": "m16 serve graph-derived contract rev 1", "result": "failed", "summary": "Caught: StopIteration — T1 revision check fails + T2 retired next() crashes."},
    {"command": "m17 skip revise on refutation", "result": "failed", "summary": "Caught: StopIteration — refuted claim disappears, next() crashes."},
    {"command": "m18 skip contract update on freeze", "result": "failed", "summary": "Caught: 138/139 (B5-T3 deferred holds missing)."},
    {"command": "m19 drop decision witness", "result": "failed", "summary": "Caught: 138/139 (OC2 witnesses mismatch)."},
    {"command": "m20 audit_verdict back in KNOWLEDGE_ACTION_KINDS", "result": "failed", "summary": "Caught: 1 failure (parity test rejects audit_verdict)."},
    {"command": "m21 compaction dispatches GetRun again", "result": "failed", "summary": "Caught: 4 failures (compaction expects RestoreRun + contract)."},
    {"command": "m22 judgment discharges same-ref exit_code (F16)", "result": "failed", "summary": "Caught: 1 fixture failure (F16)."},
    {"command": "m23 accept whitespace-only reason", "result": "not-run", "summary": "SURVIVED: 7/7 — Retirement constructor _nonempty catches whitespace (defense-in-depth). MEDIUM."}
  ],
  "validationOutput": [
    "Baseline: app 139/139, pi 66/66, codex 6/6, obligations 22 fixtures, vendor 5 identical, lint clean, adr 0 errors.",
    "21 of 23 mutations caught; 2 survived (m11, m23) — both defense-in-depth-only via Retirement constructor _nonempty guard (model.py:136).",
    "All 3 blockers CLOSED. 10 of 12 residuals CLOSED, 1 PARTIAL (m10 closed by F16, m11 defense-in-depth-only), 1 STILL OPEN (activation-check needs git, pre-existing).",
    "B5 transaction verified: persisted revision re-verified against observations, authority always artifact id, refutation retired with reason+authority, reworded claim is retire+add with preserved() holding, append-before-CAS documented.",
    "ADR-0039 matches code — no over-claims; one omission (ID-suffix convention not documented).",
    "Cold-start test F: PASS — both fixtures fully recoverable from JSON + lib alone.",
    "All mutated files reverted and verified byte-identical to /private/tmp/obligations-contract."
  ],
  "residualRisks": [
    "m11/m23 — revise.py reason guard unverified (defense-in-depth via Retirement constructor _nonempty, MEDIUM)",
    "ID-suffix compromise for reworded claims — downstream agent ignoring retired list could be confused (LOW-MEDIUM)",
    "claim reworded reason misnomer — used for hold changes not just text rewording (LOW)",
    "contracts/README.md says F1-F15 should say F1-F16 (LOW)",
    "ADR-0039 does not document revision-qualified replacement-ID convention (LOW)",
    "activation-check needs git — pre-existing, out of scope (LOW)",
    "Pi blocked-reason visibility and follow-up-turn reliability UNVERIFIED pending live spike (LOW)"
  ],
  "noStagedFiles": true,
  "diffSummary": "No files changed. READ-ONLY re-audit on /private/tmp/obligations-contract. 23 mutation experiments in /private/tmp/obligations-audit (scratch), all reverted and verified byte-identical. Report written to the authoritative output path.",
  "reviewFindings": [
    "blocker C1 CLOSED: _run_snapshot calls _contract_view (service.py:1005); compaction dispatches RestoreRun (index.ts:263); parity tests use real fixtures (parity.test.ts:10-18)",
    "blocker B5 CLOSED: _revise_contract (service.py:1083-1144) calls revise() on graph write and freeze; wire view re-verifies persisted contract; terminal Allow reuses latest pointer",
    "blocker SKILL.md CLOSED: SKILL.md:372-374 now says run.contract resume contract, not graph re-injection",
    "finding: m11 SURVIVED (MEDIUM) — revise.py reason guard removed but Retirement constructor _nonempty (model.py:136) catches it; test passes via defense-in-depth; specific guard unverified",
    "finding: m23 SURVIVED (MEDIUM) — revise.py whitespace check weakened but Retirement constructor _nonempty catches whitespace; same defense-in-depth pattern",
    "finding: ID-suffix compromise — reworded claim gets empirica/G0/revision-3 while empirica/G0 is retired; preserved() holds but ADR does not document this convention",
    "finding: contracts/README.md says F1-F15, should say F1-F16 (F16 fixture added)",
    "finding: claim reworded reason used for non-text changes (hold changes); imprecise but not incorrect",
    "no over-claims found in ADR-0039, ADR-0040, SKILL.md, Pi README, contracts/README.md, or lib docstrings",
    "cold-start test F: PASS — both terminal-handoff fixtures fully recoverable from JSON + lib alone"
  ],
  "manualNotes": "This is a read-only re-audit. Verdict: PASS. All 3 Phase 4 blockers are CLOSED. The B5 per-set-change contract revision transaction is real, honest, and the ADR matches the code. 21 of 23 mutations are caught; the 2 survivors (m11, m23) are defense-in-depth-only — the Retirement constructor's _nonempty guard catches what the revise.py guard should, so the invariant is not weakened. The cold-start recovery test passes. No git commands were run. The activation-check target requires git and cannot run in the audit copy (pre-existing constraint)."
}
```
