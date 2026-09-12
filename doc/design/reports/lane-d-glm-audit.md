# Lane D — Independent audit (Phase 4) — GLM (decorrelated-vendor auditor, ADR-24)

READ-ONLY on `/private/tmp/obligations-contract`. Mutation experiments ran in the scratch copy
`/private/tmp/obligations-audit` (byte copy without `.git`; files re-copied between mutations).
Every code claim cites `file:line`. No `git` command was run.

## Stance (verbatim, from the brief)

> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

## Summary verdict: **FAIL** (blockers listed first)

The generic module (`lib/obligations`, §A) is **sound and fully conformant** — it is the strongest
part of this implementation. The Empirica consumption layer (§B) is mostly conformant but B5
(per-set-change contract persistence) is explicitly and honestly partial. The Pi adapter (§C) has
one material blocker and several testing holes.

### Blockers

1. **C1 blocker — `empirica_status` and `session_before_compact` dispatch `GetRun`, which does not
   carry `run.contract`.** `service.py:973` (`_run_snapshot` → `_run_view(key, state)`) omits the
   contract parameter for StartRun/GetRun/ObserveAction. The Pi `empirica_status` tool
   (`index.ts:142`) and the compaction handler (`index.ts:253`) both dispatch `getRunRequest`
   (`translate.ts:67`). In production `result.run.contract` is `undefined`, so `textResult`
   (`index.ts:128-130`) falls back to `JSON.stringify(result)` and the compaction handler returns
   early (`index.ts:255: if (!run?.contract) return`). The decision C1 says "`empirica_status`
   (returns handle + `run.contract` view to the model)" — **not implemented**. The parity test
   masks this: its fake dispatch (`parity.test.ts:7-8`) returns `allow` with a `contract` for
   *every* command, so the test never exercises the real GetRun-omits-contract path.

2. **B5 blocker — per-set-change contract persistence is not implemented.** `revise()` is never
   called anywhere in the Empirica application (`grep -rn "revise" plugins/empirica/application/
   plugins/empirica/core/` → zero hits outside the vendor export). The contract is re-projected from
   the graph on every view and persisted as an Artifact only at terminal Allow
   (`service.py:1018-1024`). New-claim → `revise(add=…)`, refutation → `retire(reason=…)`, and
   freeze → deferred-hold transitions do not call `revise()`. ADR-0039:38 claims "A contract
   revision is persisted … when the live obligation set changes" — **over-claims**.

3. **SKILL.md stale claim** — `SKILL.md:372-373` still says "`SessionStart:compact` re-injects the
   graph — including the missing folds — so the loop is durable-resumable (ADR-8/9)." The new
   "Resume contract" section (`SKILL.md:118-121`) correctly says `run.contract` is the sole
   resume contract and graph counts are telemetry. The two statements **contradict**; the old one
   was not updated.

### Not blockers but material

4. **8 of 16 mutations survived** (see Part 2) — the regression suite has real holes in the Codex
   adapter, Pi notice rendering, the kind-separation guard, the revise-reason guard, the cap-at-10
   regression, the trusted-predicate model guard, and the semantic `must` text.

---

## Part 1 — Decision conformance

Evidence is from the code, not the reports. Where a report's `file:line` does not match the file,
it is flagged.

### §A — Generic module (`lib/obligations`)

| # | Verdict | Evidence |
|---|---------|----------|
| A1 | **PASS** | `Obligation` has no `state` field; `hold: Hold\|None` + `hold_reason: str\|None` are caller-declared, validated atomic (`model.py:57-62: if (self.hold is None) != (self.hold_reason is None): raise`). Status is derived by `verify()` (`verify.py:7-33`). |
| A2 | **PASS** | `Mode = Literal["require","forbid"]` (`model.py:8`). No `must_not`. `verify.py:22`: forbid + any hit → violated, else holds. `verify.py:23-29`: require all-hits → satisfied, any-contradiction → violated, else residual. |
| A3 | **PASS** | `Verdict` has all 6 partitions (`model.py:199-209`). `unwitnessed`/`held` built only inside the residual branch (`verify.py:28-30`). Sorted by id (`verify.py:20: sorted(...key=lambda item: item.id)`). |
| A4 | **PASS** | `Witness{kind, ref, expect, description}` (`model.py:29-43`). `description` validated non-empty (`model.py:25: _nonempty("description", self.description)`). |
| A5 | **PASS** | `REF_PATTERN = r"^[a-z][a-z0-9_-]*(/[A-Za-z0-9._:@-]+)+$"` (`model.py:11`). Enforced in `Witness.__post_init__` (`model.py:34`) and `Observation.__post_init__` (`model.py:107`). Mirrored in `contract.schema.json`. |
| A6 | **PASS** | `Observation.__post_init__`: `_nonempty("source", self.source)` (`model.py:116`); judgment rejects `anonymous`/`unknown`/`model` case-insensitively (`model.py:118-119`). |
| A7 | **PASS** | `revise()` requires non-empty reason+authority (`revise.py:12-13`). Retired kept as `Retirement{obligation, reason, authority, at_revision}` (`revise.py:21`). Ids unique across live+retired (`model.py:167-170`). revision increments, parent_revision+supersedes set (`revise.py:19-20`). |
| A8 | **PASS** | `canonical()` sorts obligations/witnesses/provenance/retired, collapses `must` whitespace (`project.py:29-50`). `parse()` is the inverse for the contract part (`project.py:53-66`). `preserved()` checks present-or-retired-with-reason, mode equal, must-equal-after-normalisation, witnesses(before)⊆witnesses(after), because(before)⊆because(after) (`project.py:96-122`). Docstring disclaims semantic specificity (`project.py:3-5`). |
| A9 | **PASS** | `project()` emits `observed: pass\|fail\|null` per witness via `_observed()` (`project.py:69-93`). |
| A10 | **PASS** | `render_text()` is deterministic: every obligation id, mode, collapsed must, hold, every witness `kind:ref (expect) — description [observed]`, retirements, verdict partitions (`project.py:129-162`). |
| A11 | **PASS** | 21 fixtures: F01–F15, P01–P05, V01. One directory iterator executes verify/project/text/preservation/revision/cold-start without fixture-specific methods (`test_obligations.py:89-134`). |
| A12 | **PASS** | `validate_obligations.py` always runs stdlib structural validation (`validate_obligations.py:39-115`). When `jsonschema` is absent it does not skip — it validates refs/enums/required keys (`validate_obligations.py:125: jsonschema = None` try/except). |
| A13 | **PASS** | `__init__.py:8-27` exports exactly the frozen surface. `check_vendor.py` compares 5 files byte-for-byte and rejects extra files (`check_vendor.py:10-17`). Every value has `to_json`/`from_json`. |

### §B — Empirica consumption (Lane B)

| # | Verdict | Evidence |
|---|---------|----------|
| B1 | **PASS** | `wire.block()` adds `run["contract"]` (`wire.py:196-199`). RestoreRun: `contract = self._contract_view(...)` passed to `_run_view(..., contract=contract)` (`service.py:242-244`). Terminal Allow: `contract=contract, contract_artifact_id=artifact_id` (`service.py:870-873`). Budget terminal: `service.py:913-914`. Stall terminal: `service.py:943-944`. Counts in `snapshot["graph"]` (`service.py:267`). **Report cite mismatch**: terra-impl cites `service.py:265-268` for "RestoreRun derives and supplies run.contract" — that range is the graph-count telemetry inside `_restore_graph_view`, not the `run.contract` supply (which is at `service.py:242-244`). |
| B2 | **PASS** | `contract_for_graph` uses `claims.gating_goals(graph, theta, ev_ok)` (`core/obligations.py:85`), not `Block.open_claims`. Module docstring states this explicitly (`core/obligations.py:4-5`). **But see m5 — untested.** |
| B3 | **PARTIAL** | Research+spike+audit/budget/stall run-level witnesses are correct; observation mapping uses `evidence_fold` with no regex over reasons (`core/obligations.py:29-55`). **Missing**: `judgment decision/<id>` for `needs-decision` claims and `event budget/<id>` for `needs-budget` claims (B3 decision table). `_witnesses()` (`core/obligations.py:14-23`) only emits `research/{id}` for all claims and `spike/{id}` for `needs-experiment`; `needs-decision`/`needs-budget` claims get `hold=blocked` but no `decision/` or `budget/` discharge witness — the agent sees only a research witness for a claim that needs a decision or budget. `VALID_BLOCKED_TAGS` confirms these kinds exist (`claims.py:27`). |
| B4 | **PASS** | `trusted()` (`core/obligations.py:56-66`): exit_code→spike harness, artifact→knowledge store, judgment audit/*→not anonymous/unknown/model, judgment decision/*→human, event budget/*→operator. Never the executing model. **But see m14 — the model-rejection guard is untested.** |
| B5 | **PARTIAL** | Terminal Allow persists via `_persist_contract` → `ArtifactRepository.append` (`service.py:1018-1024`). Artifact is `content_address(body)` (`service.py:1023`). **Not implemented**: per-set-change `revise(add/retire)` — `revise()` is never called in the application; no `contract_artifact_id` pointer in `OperationalState`. ADR-0039:38 over-claims ("persisted … when the live obligation set changes"). |
| B6 | **PASS** (doc) | ADR-0039:38-40 states the authority rule: "only the application may revise … in response to a knowledge artifact … The executing actor alone is never an authority." **Not enforced in code** — `revise()` is never called, so the rule is policy-only. |
| B7 | **PARTIAL** | RS2 extended with mutation test (`test_application.py:1229-1245`). E2e `preserved()` at every hop, Claude stderr `render_text` byte-for-byte, compaction `parse()` round-trip, terminal artifact round-trip (`test_application.py:1296-1335`). Codex compact `parse()` round-trip (`test_codex_adapter.py:323`). **Gap**: no test checks the Codex block *reason* contains `render_text` (m7 survived). The codex test only checks `decision == "block"` (`test_codex_adapter.py:256`). |
| B8 | **PASS** | `$defs.contract_view` → `$ref` to `../../obligations/v1/contract.schema.json#/$defs/view` (`response.schema.json:166-167`). `run.contract` → `$ref: #/$defs/contract_view` (`response.schema.json:64-65`). `validate_contracts.py` does Draft-2020-12 instance validation with locally indexed `$id` schemas (`validate_contracts.py:60-69`). |
| B9 | **PASS** | Pi runtime boundary paragraph present (`SKILL.md:113`). Resume-contract section present (`SKILL.md:118-121`). **But old stale claim remains at `SKILL.md:372-373`** (see blocker 3). |

### §C — Pi parity (Lane C)

| # | Verdict | Evidence |
|---|---------|----------|
| C1 | **PARTIAL** | `parseModeFlags` (`translate.ts:22-39`); `registerTool` for 3 tools (`index.ts:115-151`); `subagent` interception (`index.ts:221-231`); `session_start` reconstruction (`index.ts:103-114`); `session_before_compact` (`index.ts:253-262`); `agent_settled` nudge (`translate.ts:176-187`). **BLOCKER**: `empirica_status` and `session_before_compact` dispatch `GetRun` (`index.ts:142`, `index.ts:255`) which does not include `run.contract` (`service.py:973`). The `/empirica` StartRun path also gets no contract (`service.py:142,157 → _run_snapshot`). The parity test's fake dispatch always returns a contract (`parity.test.ts:7-8`), masking this. Knowledge kinds: `KNOWLEDGE_ACTION_KINDS` includes `investigate` (an operational kind in `wire.py:30`) but omits `freeze`; minor inconsistency with the Claude builders. |
| C2 | **PARTIAL** | Audit ticket issuance is inside `tool_call` interception only (`index.ts:222-229`). **But** `empirica_knowledge` is a registered tool that accepts `audit_verdict` as a kind (`index.ts:33: KNOWLEDGE_ACTION_KINDS`, `index.ts:137-139`). The decision says "audit verdict submission is not a registered tool" — it IS reachable through `empirica_knowledge`. The application's nonce guard (`service.py:304-307`) partially mitigates, but the adapter exposes it. |
| C3 | **PASS** | `obligations.ts:1-57` mirrors the frozen schema. `obligations.test.ts` iterates all 21 fixtures asserting `renderText` byte-equality and `preserved` results. |
| C4 | **PASS** | ADR-0040:18-22: "Pi has no completion-veto lifecycle. Enforcement exists only when `report_convergence` is invoked; an agent that never invokes it can complete." Two UNVERIFIED runtime claims named. README mirrors (`README.md:1-8`). |
| C5 | **PASS** | Process decision; code is in `plugins/empirica/adapters/pi/**` and `doc/adr/0040-*.md` only. |

---

## Part 2 — The invariant, by falsification

Baseline: `make check` green in `/private/tmp/obligations-audit` for all sub-targets except
`activation-check` (which needs `git show HEAD:` — the audit copy has no `.git`; pre-existing, not
implementation). Sub-targets verified green: `make test` (94 tests), `make lint`, `make
contract-check` (7 schemas, 9 fixtures), `make obligations-check` (3 schemas, 21 fixtures), `make
vendor-check` (5 byte-identical), `make empirica-pi-check` (63 tests), `make adr-check` (0 errors),
`make empirica-codex-check` (6 tests). Application suite baseline: 131/131.

Each mutation applied one at a time; file re-copied from `/private/tmp/obligations-contract` between
runs.

| Mutation | Target | Result | Finding |
|----------|--------|--------|---------|
| m1: `block()` drops `run.contract` | `wire.py:195-200` | **FAILED** | OB1 `KeyError: 'contract'` — caught. ✓ |
| m2: RestoreRun omits `run.contract` | `service.py:242-244` | **FAILED** | OB3 `KeyError: 'contract'` — caught. ✓ |
| m3: budget terminal omits contract/artifact_id | `service.py:913-914` | **FAILED** | OB5 `StopIteration` (artifact_id missing) — caught. ✓ |
| m4: break spike `predicateType` (no exit_code obs) | `core/obligations.py:48` | **FAILED** | OC1 status=residual, spike unobserved — caught. ✓ |
| m5: project from `pending()[:10]` instead of `gating_goals` | `core/obligations.py:85` | **SURVIVED** | **FINDING (HIGH)**: 131/131 passed. No test has >10 claims (cap-at-10 regression untested). No test checks blocked/deferred claims are projected. This is the exact regression B2/B7 warn against — "a test that FAILS if `open_claims` is dropped again" only tests dropping ALL obligations (`test_application.py:1244`), not capping or filtering. |
| m6: Claude stderr drops `render_text` | `completion.py:91-96` | **FAILED** | OB2 stderr missing `render_text` — caught. ✓ |
| m7: Codex block reason drops `render_text` | `lifecycle.py:242-247` | **SURVIVED** | **FINDING (MEDIUM)**: app 131/131 + codex 6/6. The codex test checks `decision=="block"` only (`test_codex_adapter.py:256`), never the reason content. B7 says "Codex deny … likewise" should render the contract. |
| m8a: `gateFromDecision` drops contract | `translate.ts:99-101` | **FAILED** | `translate.test.ts:116` deepEqual — caught. ✓ |
| m8b: `convergenceNotice` drops contract | `translate.ts:156-159` | **SURVIVED** | **FINDING (MEDIUM)**: 63/63. `translate.test.ts:152` tests a Block *without* contract; no test passes a Block-with-contract to `convergenceNotice` and checks the text. |
| m8c: `settledFollowUp` drops contract | `translate.ts:183-187` | **SURVIVED** | **FINDING (MEDIUM)**: 63/63. Same: `translate.test.ts:177` uses a Block without contract. |
| m9: `renderText` one-char format change (space→hyphen in header) | `obligations.ts:35` | **FAILED** | 15 fixture byte-equality tests — caught. ✓ |
| m10: judgment observation discharges exit_code witness | `verify.py:9-11` | **SURVIVED** | **FINDING (MEDIUM)**: lib 7/7. No fixture has a judgment observation with the same ref as a machine-kind witness. F08 tests kind-separation but with *different* refs (`audit/run` vs `test/case`). The kind guard (A2/D3) is untested for same-ref cross-kind. In practice the Empirica projection uses distinct ref prefixes, so not currently exploitable. |
| m11: `revise()` allows retire without reason | `revise.py:12-13` | **SURVIVED** | **FINDING (MEDIUM)**: lib 7/7 + obligations-check. `test_revision_requires_authority_and_records_retirement` (`test_obligations.py:75-86`) tests empty *authority* but never empty *reason*. A7 requires both. |
| m12: `preserved()` ignores witness loss | `project.py:112-114` | **FAILED** | P02-witness-loss fixture — caught. ✓ |
| m13: vendor one-byte drift | `vendor/obligations/model.py:1` | **FAILED** | `vendor-check` — caught. ✓ |
| m14: `trusted()` accepts `source='model'` for audit | `core/obligations.py:60` | **SURVIVED** | **FINDING (LOW)**: 131/131. The lib constructor (`model.py:118-119`) already rejects `source='model'` for judgment, making the `trusted()` guard dead code — but the guard itself is untested. Defense-in-depth unverified. |
| m15 (mine): obligation `must=claim_id` (drops claim text) | `core/obligations.py:92` | **SURVIVED** | **FINDING (MEDIUM-HIGH)**: 131/131. No Empirica test checks the `must` field carries the claim's semantic text. `preserved()` only checks consistency (before==after), not content. The agent would see `must: "G0"` instead of `must: "true succeeds"` — the issue's test B ("the agent-visible response identifies … its meaning") is not regression-tested for text content. |

### Mutation summary

- **Caught (9)**: m1, m2, m3, m4, m6, m8a, m9, m12, m13.
- **Survived (8)**: m5 (HIGH), m7 (MED), m8b (MED), m8c (MED), m10 (MED), m11 (MED), m14 (LOW), m15 (MED-HIGH).

---

## Part 3 — Cold-start conformance (issue test F)

Using ONLY the JSON fixture and `lib/obligations` (no Empirica imports):

```
fixture: contracts/fixtures/empirica-terminal-handoff-budget.json
result.type=Allow  run.status=stopped_budget  contract_artifact_id=<present>
```

`parse(view)` (`project.py:53`) reconstructs the `Contract` from the `run.contract` view:

| Field | Recovered? |
|-------|-----------|
| contract_id, revision | ✓ `empirica/fixture`, 1 |
| obligation id, mode, must | ✓ `empirica/G0`, `require`, `"record research"` |
| hold, hold_reason | ✓ `None`, `None` |
| because (provenance) | ✓ `("G0",)` |
| witness kind, ref, expect, description | ✓ `artifact:research/G0`, `pass`, `"recorded Fold-1 research attestation supporting this claim"` |
| witness observed (from view) | ✓ `null` (unobserved) |
| verdict partitions | ✓ `residual=["empirica/G0"]`, `unwitnessed=["empirica/G0"]` |
| retired | ✓ 0 |
| contract_artifact_id | ✓ present on the wire |
| round-trip `canonical(parse(view))` | ✓ equals `canonical(parse(view))` |

`render_text(view)` produces:

```
Obligation contract empirica/fixture@1
empirica/G0 [require] record research
  hold: none
  - artifact:research/G0 (pass) — recorded Fold-1 research attestation supporting this claim [observed: null]
Verdict: satisfied=-; holds=-; violated=-; residual=empirica/G0; unwitnessed=empirica/G0; held=-
```

**Verdict: PASS.** A fresh agent with only the JSON and `lib/obligations` can recover every
obligation, its mode/must, its witnesses with expected outcomes and observed status, its hold,
its provenance, and the artifact id — without any Empirica internal. The frozen fixture
(`empirica-terminal-handoff-frozen.json`) yields the identical structure.

**What is sufficient**: the `must` text + witness `description` together name the discharge
condition; `observed: null` says it is outstanding; the verdict says it is residual/unwitnessed;
`contract_artifact_id` gives a durable reference.

**What is NOT sufficient for action beyond this fixture**: the agent does not know the
*ObserveAction payload* to record a research attestation (that is host-specific and lives in the
adapter, not the contract). The contract says *what* to discharge, not *how* to submit it. This is
by design (the issue says "The next agent should not have to understand GSN").

---

## Part 4 — Phase 1 critique D1–D6

| # | Critique | Status | Evidence |
|---|----------|--------|----------|
| D1 | Obligation lost / less specific across boundary (transport flattening, Stop→compaction gap) | **ADDRESSED** | A10 (`render_text`, `project.py:129-162`) + B1 (one wire location `run.contract`) solve flattening: the structured view is on the wire, `render_text` is the text-channel fallback. Stop→compaction is solved by `run.contract` on every Block/RestoreRun/terminal (`wire.py:196`, `service.py:242,870,913,943`) and embedded in compaction (`restore.py:58-62`, `lifecycle.py:395-397`). Residual: B5 per-set-change persistence (see blocker 2). |
| D2 | Exact `(kind,ref)` matching breaks (ref drift, TS divergence) | **ADDRESSED** | A5 canonical ref grammar enforced at construction (`model.py:34,107`) and in schema. m9 proved the fixture byte-equality test catches TS divergence (15 failures on one-char change). Ref drift across revisions is moot: the contract is re-projected from the graph, not persisted with old refs. |
| D3 | Is `judgment` a backdoor? | **ADDRESSED (with testing gap)** | Observation-side: A6 rejects anonymous/unknown/model (`model.py:118-119`). Witness-declaration: Empirica uses machine kinds for machine facts (`artifact:research/`, `exit_code:spike/`), `judgment:audit/` only for audit (`core/obligations.py:14-23`). `trusted()` rejects model for audit (`core/obligations.py:60`). Source provenance required. **Gap**: m10 survived — the kind-separation guard is not tested with same-ref cross-kind. m14 survived — the trusted-predicate model guard is untested. |
| D4 | Can `revise()` move goalposts? | **PARTIALLY ADDRESSED** | Authority rule documented in ADR-0039:38-40. A7 requires reason+authority. A8 `preserved()` checks non-weakening across views. **But** `revise()` is never called in Empirica (B5 partial) — the per-set-change revision authority and `preserved()`-across-revisions are policy-only, not enforced. |
| D5 | Is `preserved()` decidable? | **ADDRESSED** | A8 ships `canonical()` (normal form) + `parse()` (decoder) + `preserved()` defined as `parse(before) ⊇ parse(after)` over normalised obligations (`project.py:96-122`). The docstring disclaims semantic specificity (`project.py:3-5`). This was the #1 Phase-1 residual; it is resolved. |
| D6 | Minimal fixture set for TS divergence | **ADDRESSED** | 21 fixtures (F01–F15, P01–P05, V01). Both Python (`test_obligations.py:89-134`) and TS (`obligations.test.ts:9-14`) iterate the directory. F4/F5 (kind/ref), F8 (judgment separation), F9 (order), F11 (revision), F15 (cold-start) all present. m9 confirmed the byte-equality pin works. |

---

## Part 5 — Anything else

### Layering

- **`lib/obligations` imports nothing Empirica** — stdlib only (`re`, `dataclasses`, `typing`,
  `collections.abc`). CLEAN. ✓
- **`core/obligations.py:31` deferred-imports `from application.knowledge import evidence_fold`**
  inside `observations_from_knowledge()`. This is a **core→application layering inversion**: the
  pure core projection depends on the application layer for the fold classifier. The import is
  deferred (not at module level) with a comment acknowledging it, but the dependency direction is
  wrong — `evidence_fold` is a pure function that belongs in `core/` (or should be injected). Not a
  blocker; the fold derivation exists exactly once (`knowledge.py:378-391`), satisfying the
  "fold derivation must exist once" requirement.
- **`core/` does not import adapters.** ✓

### Duplicated definitions

- The fold derivation (`evidence_fold`) exists once (`knowledge.py:378-391`) and is reused by
  `core/obligations.py:31` and `knowledge.py:418`. No duplication. ✓
- `render_text` exists once in `lib/obligations/project.py` and is mirrored (not duplicated) in
  `obligations.ts:35-52`, pinned by fixtures. ✓

### Docs that over-claim

| Doc | Claim | Verdict |
|-----|-------|---------|
| ADR-0039:38 | "A contract revision is persisted … when the live obligation set changes" | **OVER-CLAIMS** — `revise()` never called; only terminal Allow persists. |
| ADR-0039:38-40 | "Revision authority: only the application may revise … in response to a knowledge artifact" | **Policy-only** — not enforced in code; no `revise()` calls, no authority checks. |
| SKILL.md:372-373 | "`SessionStart:compact` re-injects the graph — including the missing folds" | **STALE** — contradicts the new resume-contract section at `SKILL.md:118-121` which says graph counts are telemetry and `run.contract` is the sole resume contract. |
| `index.ts:140` (tool description) | "Show the current run handle and obligation contract" (`empirica_status`) | **OVER-CLAIMS** — GetRun does not include `run.contract`; the tool shows `JSON.stringify(result)`. |
| ADR-0040 | Pi completion-veto gap | **PLAIN** — ADR-0040:18-22 states it directly: "Pi has no completion-veto lifecycle. Enforcement exists only when `report_convergence` is invoked; an agent that never invokes it can complete." Two UNVERIFIED claims named. ✓ |
| Pi README | | **HONEST** — names the gap and UNVERIFIED items. ✓ |

### ADR-0040 Pi completion-veto gap

ADR-0040 **states the gap plainly** (`doc/adr/0040-pi-adapter-parity-and-named-gaps.md:18-22`):
"Pi has no completion-veto lifecycle. Enforcement exists only when `report_convergence` is invoked;
an agent that never invokes it can complete." It also names the two UNVERIFIED runtime claims
(blocked-reason model visibility, follow-up turn reliability) and the ungated-external-spawn gap.
**PASS.**

---

## Residual risks (ranked)

1. **C1 contract visibility gap (HIGH)**: `empirica_status` and `session_before_compact` dispatch
   GetRun which omits `run.contract`. The Pi model cannot see the obligation contract through these
   two surfaces — only through Block responses. Fix: either project `run.contract` in GetRun
   (`service.py:973`), or dispatch RestoreRun from the Pi adapter for status/compaction.

2. **m5 — cap-at-10 regression untested (HIGH)**: no test has >10 open claims; the B2/B7 mutation
   test only checks dropping ALL obligations. A regression to `open_claims[:10]` survives.

3. **m15 — semantic `must` text untested (MEDIUM-HIGH)**: no Empirica test verifies the `must` field
   carries the claim text. `must=claim_id` survives. The issue's test B ("identifies … its
   meaning") is not regression-tested.

4. **B5 — per-set-change persistence incomplete (MEDIUM-HIGH)**: `revise()` never called; ADR-0039
   over-claims. Goalpost-moving (D4) is policy-only.

5. **B3 — missing `decision/` and `budget/` discharge witnesses (MEDIUM)**: `needs-decision` and
   `needs-budget` claims get only a research witness; the agent cannot distinguish "needs a
   decision" from "needs research."

6. **m7/m8b/m8c — Codex and Pi notice rendering untested (MEDIUM)**: dropping `render_text` from the
   Codex block reason, `convergenceNotice`, and `settledFollowUp` all survive.

7. **C2 — `audit_verdict` reachable via `empirica_knowledge` (MEDIUM)**: the model-callable
   knowledge tool accepts `audit_verdict`; C2 says it should not be a registered tool.

8. **m10/m11 — lib guards untested (MEDIUM)**: judgment→exit_code cross-kind and empty-reason both
   survive. Not exploitable today (constructor guards + distinct ref prefixes) but defense-in-depth
   is unverified.

9. **SKILL.md:372 stale claim (MEDIUM)**: contradicts the new resume-contract section.

10. **core→application layering inversion (LOW)**: `core/obligations.py:31` deferred-imports
    `evidence_fold` from `application.knowledge`. Pure function in the wrong layer.

11. **`activation-check` needs git (LOW)**: cannot run in the audit copy (no `.git`); pre-existing,
    not introduced by this change.

12. **Parity test fakes mask production behavior (LOW)**: `parity.test.ts:7-8` always returns
    `run.contract` for GetRun, hiding the C1 gap.

---

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "Independent read-only audit completed: all 27 decisions (A1-A13, B1-B9, C1-C5) verdicted with file:line evidence from the code; 16 mutation experiments run (9 caught, 8 survived with findings); cold-start conformance (issue test F) verified; D1-D6 Phase-1 critiques resolved-status cited; layering/docs/ADR-0040 assessed. No files in /private/tmp/obligations-contract were created or modified. All scratch mutations reverted; audit copy verified byte-identical to contract."
    }
  ],
  "changedFiles": [],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {"command": "make check", "result": "failed", "summary": "Failed at activation-check (no .git in audit copy — pre-existing, not implementation). All other sub-targets green."},
    {"command": "make test", "result": "passed", "summary": "94 tests across 6 suites (lib, app, claude, codex, methodologist-core, methodologist-codex)."},
    {"command": "make lint", "result": "passed", "summary": "ruff: All checks passed."},
    {"command": "make contract-check", "result": "passed", "summary": "7 schemas, 9 fixtures."},
    {"command": "make obligations-check", "result": "passed", "summary": "3 schemas, 21 fixtures (jsonschema + stdlib)."},
    {"command": "make vendor-check", "result": "passed", "summary": "5 byte-identical obligation vendor files."},
    {"command": "make empirica-pi-check", "result": "passed", "summary": "63 tests, typecheck, package validation, bridge smoke."},
    {"command": "make adr-check", "result": "passed", "summary": "0 errors, 82 warnings, 1 info."},
    {"command": "python3 plugins/empirica/tests/test_application.py (baseline)", "result": "passed", "summary": "131/131 checks passed."},
    {"command": "m1 wire.block drops contract", "result": "failed", "summary": "Test caught: KeyError 'contract' (OB1)."},
    {"command": "m2 RestoreRun omits run.contract", "result": "failed", "summary": "Test caught: KeyError 'contract' (OB3)."},
    {"command": "m3 budget terminal omits contract/artifact_id", "result": "failed", "summary": "Test caught: StopIteration (OB5)."},
    {"command": "m4 break spike predicateType", "result": "failed", "summary": "Test caught: OC1 status=residual, spike unobserved."},
    {"command": "m5 project from open_claims[:10]", "result": "not-run", "summary": "SURVIVED: 131/131 — no test has >10 claims or checks blocked/deferred projection. FINDING HIGH."},
    {"command": "m6 Claude stderr drops render_text", "result": "failed", "summary": "Test caught: OB2 stderr missing render_text."},
    {"command": "m7 Codex block reason drops render_text", "result": "not-run", "summary": "SURVIVED: app 131/131 + codex 6/6 — codex test checks decision only. FINDING MEDIUM."},
    {"command": "m8a gateFromDecision drops contract", "result": "failed", "summary": "Test caught: translate.test.ts deepEqual."},
    {"command": "m8b convergenceNotice drops contract", "result": "not-run", "summary": "SURVIVED: 63/63 — test uses Block without contract. FINDING MEDIUM."},
    {"command": "m8c settledFollowUp drops contract", "result": "not-run", "summary": "SURVIVED: 63/63 — test uses Block without contract. FINDING MEDIUM."},
    {"command": "m9 renderText one-char format change", "result": "failed", "summary": "Test caught: 15 fixture byte-equality failures."},
    {"command": "m10 judgment discharges exit_code witness", "result": "not-run", "summary": "SURVIVED: lib 7/7 — no same-ref cross-kind fixture. FINDING MEDIUM."},
    {"command": "m11 retire without reason", "result": "not-run", "summary": "SURVIVED: lib 7/7 + obligations-check — empty reason never tested. FINDING MEDIUM."},
    {"command": "m12 preserved ignores witness loss", "result": "failed", "summary": "Test caught: P02-witness-loss fixture."},
    {"command": "m13 vendor one-byte drift", "result": "failed", "summary": "Test caught: vendor-check mismatch."},
    {"command": "m14 trusted accepts source=model", "result": "not-run", "summary": "SURVIVED: 131/131 — guard is dead code (constructor rejects model); untested. FINDING LOW."},
    {"command": "m15 must=claim_id drops claim text", "result": "not-run", "summary": "SURVIVED: 131/131 — no test checks must text content. FINDING MEDIUM-HIGH."}
  ],
  "validationOutput": [
    "Baseline make check: green for test, lint, contract-check, obligations-check, vendor-check, empirica-pi-check, adr-check, empirica-codex-check. activation-check fails (no .git in audit copy).",
    "9 of 16 mutations caught by the regression suite; 8 survived (m5 HIGH, m7 MED, m8b MED, m8c MED, m10 MED, m11 MED, m14 LOW, m15 MED-HIGH).",
    "Cold-start conformance (issue test F): PASS — parse(view) + render_text recover every obligation, witness, hold, provenance, and artifact_id from the JSON alone.",
    "All 11 mutated files reverted and verified byte-identical to /private/tmp/obligations-contract."
  ],
  "residualRisks": [
    "C1 blocker: empirica_status and session_before_compact dispatch GetRun which omits run.contract — Pi model cannot see the contract through these surfaces; parity test fakes mask this.",
    "B5 incomplete: revise() never called in Empirica; per-set-change persistence not implemented; ADR-0039 over-claims.",
    "m5: cap-at-10 regression untested (no >10-claim test).",
    "m15: semantic must text content untested (must=claim_id survives).",
    "B3 partial: needs-decision/needs-budget claims lack decision/budget discharge witnesses.",
    "C2: audit_verdict reachable via model-callable empirica_knowledge tool.",
    "SKILL.md:372-373 stale claim contradicts the new resume-contract section.",
    "core/obligations.py:31 deferred-imports from application.knowledge (layering inversion)."
  ],
  "noStagedFiles": true,
  "diffSummary": "No files changed. READ-ONLY audit on /private/tmp/obligations-contract. 16 mutation experiments in /private/tmp/obligations-audit (scratch), all reverted. Report written to the authoritative output path.",
  "reviewFindings": [
    "blocker: C1 — empirica_status/session_before_compact dispatch GetRun (service.py:973) which omits run.contract; Pi model cannot see the contract through these surfaces.",
    "blocker: B5 — revise() never called; ADR-0039:38 over-claims per-set-change persistence.",
    "blocker: SKILL.md:372-373 stale 're-injects the graph' claim contradicts SKILL.md:118-121.",
    "finding: m5 SURVIVED (HIGH) — cap-at-10 regression untested.",
    "finding: m15 SURVIVED (MED-HIGH) — semantic must text untested.",
    "finding: m7 SURVIVED (MED) — Codex block reason rendering untested.",
    "finding: m8b/m8c SURVIVED (MED) — Pi convergenceNotice/settledFollowUp contract rendering untested.",
    "finding: m10 SURVIVED (MED) — judgment→exit_code kind guard untested for same-ref.",
    "finding: m11 SURVIVED (MED) — revise empty-reason guard untested.",
    "finding: m14 SURVIVED (LOW) — trusted predicate model-rejection untested.",
    "finding: B3 PARTIAL — needs-decision/needs-budget claims lack decision/budget discharge witnesses.",
    "finding: C2 PARTIAL — audit_verdict reachable via empirica_knowledge tool.",
    "finding: core/obligations.py:31 layering inversion (core→application deferred import).",
    "report cite mismatch: terra-impl cites service.py:265-268 for RestoreRun run.contract supply; actual code is at service.py:242-244 (265-268 is graph-count telemetry)."
  ],
  "manualNotes": "This is a read-only audit. The verdict is FAIL due to 3 blockers (C1 contract-visibility gap, B5 per-set-change persistence incomplete, SKILL.md stale claim) and 8 surviving mutations. The generic lib/obligations (§A) is fully conformant and is the strongest part of the implementation. No git commands were run. The activation-check target requires git and cannot run in the audit copy (pre-existing constraint)."
}
```
