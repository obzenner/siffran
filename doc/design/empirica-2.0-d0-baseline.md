# Empirica 2.0 D0 baseline freeze

**Status:** Parent-frozen factual baseline. No implementation authority.

**Runtime baseline:** Empirica 1.3.0, commit `4257c8d`.

**Historical behavior reference:** Empirica 0.7.0, commit `3150f4d`.

**Experimental branch:** `experiment/empirica-2.0` in `/private/tmp/empirica-twofold-fix`.

**Normative plan:** `doc/design/empirica-2.0-final-dag.md`.

## 1. Provenance and reconciliation

D0 used two independent discovery-only lanes:

- Luna mapped semantic facts, reads/writes, data flows, historical behavior, async-host source facts,
  and the uncommitted two-fold blast radius.
- Haiku enumerated repository state, executable code, compatibility surfaces, schemas/mirrors, Make
  targets, tests, model-visible surfaces, host events, and configuration.

Neither lane supplied design judgment. The parent reconciled their inventories against source and
recomputed metrics. In particular:

- baseline reachable runtime is **9,458 physical lines across 68 Python/TypeScript files**, not the
  partial 8,425-line count that omitted much of the Pi TypeScript runtime;
- baseline contains **222 Python test definitions and 66 TypeScript `test(...)` calls**; this is a
  static inventory, not an assertion count or passing-gate result;
- `release-check` currently depends on `check`; commit validation is proposed, not implemented;
- the direct approval shortcut is the live legacy `kind="evidence"` application path, not an
  obligation-vendor field;
- frozen composite verdicts belong to evidence-leaf records/decoding, not the generic obligation
  model;
- several Haiku host/event labels were approximate; the source-cited host facts in §7 are canonical
  for D0.

Discovery artifacts are retained by the subagent runtime under runs `a73372e1` and `065c1f79`.

## 2. Repository freeze

At D0 freeze:

```text
branch: experiment/empirica-2.0
base:   4257c8d
modified:
  plugins/empirica/adapters/claude/evidence.py
  plugins/empirica/application/knowledge.py
  plugins/empirica/application/service.py
  plugins/empirica/core/evidence.py
  plugins/empirica/tests/test_application.py
untracked:
  doc/design/empirica-2.0-final-dag.md
  doc/design/empirica-2.0-d0-baseline.md (this artifact)
staged: none
```

The five pre-existing modified files are an experimental two-fold implementation, not accepted code.
The design DAG and this baseline are planning artifacts.

## 3. Effective runtime inventory

Count definition: every committed `.py`/`.ts` file under `plugins/empirica` reachable as plugin
runtime/package code, excluding test paths. Vendored executable Python and Pi TypeScript are counted.
Contracts, skill prose, fixtures, tests, and repository validators are tracked separately.

| Category | Baseline LOC |
|---|---:|
| application | 2,670 |
| core | 1,069 |
| adapters/claude | 2,323 |
| adapters/codex | 603 |
| adapters/git | 413 |
| adapters/state | 476 |
| adapters/pi Python | 22 |
| adapters/pi TypeScript | 1,146 |
| adapter bridge/init | 141 |
| hooks | 83 |
| vendored obligations runtime | 512 |
| **Total** | **9,458** |

Largest modules:

| LOC | File |
|---:|---|
| 1,484 | `application/service.py` |
| 545 | `adapters/pi/src/index.ts` |
| 458 | `application/knowledge.py` |
| 445 | `adapters/codex/lifecycle.py` |
| 402 | `adapters/git/artifact_repo.py` |
| 379 | `application/state.py` |
| 365 | `adapters/claude/lifecycle.py` |
| 305 | `adapters/claude/migrate_legacy.py` |
| 230 | `vendor/obligations/model.py` |
| 228 | `adapters/claude/knowledge.py` |

Largest functions include `migrate` (127 lines), `_observe` (100), `coverage_check` (90),
`_decide_terminal` (82), `_finalize_block` (81), state `decode` (78), `_revise_contract` (73),
`canonicalize_graph` (72), Claude `spawn_main` (70), and `_update_graph` (67).

D16 compares the final effective reachable inventory against this same definition. Moving code to a
new runtime directory, generated output, vendor copy, or helper module does not reduce this number.

## 4. Semantic authority freeze

| Semantic fact | Current authority | Derived projections / duplicate surfaces | 2.0 ownership question |
|---|---|---|---|
| Run identity/current artifact | Operational state and pointer CAS (`application/state.py`, `service.py:928-990`) | Opaque wire handle; state repository path | Preserve operational authority and opaque boundary |
| Graph | Append-only canonical graph artifact selected by operational pointer | Contract, dossier, claim state, restore | Graph remains argument-content authority |
| Claim state | `core/claims.state_of` derives on read | Contract and convergence views | Never persisted/editable |
| Evidence facts | Append-only evidence artifacts; active set removes superseded artifact IDs | Stored composite verdicts; adapter telemetry; oracle | Raw validated facts plus active-set derivation |
| Evidence verdict | Baseline adapter verdict plus application truthy merge; experimental complete-set core recomputation | Frozen `verdicts` | One pure core rule over complete active set |
| Spike freshness | Claude adapter records and regate rereads file digests | Experimental `_recorded_intact` checks only stored shape | Current workspace observation must be explicit snapshot input |
| Audit ticket | Operational state nonce/reservation/consumption fields | Host-side correlation maps | Operational authority; host maps transport only |
| Audit verdict | Append-only knowledge artifact covered by pure audit oracle | Host output parsing/rendering | Artifact fact; pure coverage decision |
| Audit independence | Actor/model attribution plus coverage logic | Agent definition/config and host evidence | Public result reports measured tier, never guarantee |
| Obligations | Pure projection from graph/evidence/audit/budget/freeze | Persisted contract revision; Pi TS renderer | Projection/history only, not adjudicator |
| Freeze | First committed claim tuple/sequence in operational state | Deferred list and contract | First-write-wins operational authority |
| Budget/passes/spawns | Operational counters/caps; pure budget derivation | Adapter reservations and rendered status | Operational state; idle progress rule derived |
| Async child | No general durable entity; only reservations/tickets plus host-local correlation | Claude SubagentStop, Pi in-memory map, Codex pre-tool path | D1-H/D8 must define authority |
| Host capability | No canonical registry | Adapter code, README/skill prose | D1-H must define measured declaration |
| Protocol actions/status | Python wire constants/schema plus TS mirrors | Service branches, fixtures, adapter tables | v2 schema/registry with mechanical parity |
| Reason/next action | Free-form core/service prose plus adapter renderers | Skill guidance and fixtures | Stable public-contract registry |
| Compaction/restore | Re-derived service/run view from state/artifacts | Host-specific rendering/session entries | Same facts, bounded progressive projection |

### Transaction facts to preserve

- graph/artifact append precedes operational pointer CAS;
- stale CAS is retried/re-read rather than silently overwritten;
- missing/corrupt selected artifact fails closed;
- terminal status is operational authority and is not reopened by later observations;
- knowledge writes are append-only and content-addressed;
- host correlation state is not evidence authority.

## 5. Feature-preservation baseline

| Capability | Current/historical factual anchor | D0 test status |
|---|---|---|
| Activation | Current StartRun/lifecycle adapters; 0.7 `run_start.py` | Current tests present |
| Route-before-investigation | Current route/investigate actions; historical route witness/audit | Current order tests present |
| Derived graph state | `core/claims.py`; corrupt/missing graph fail-closed tests | Present |
| Fold-1 research | Current in-toto research leaves; 0.7 `evidence.py` | Present |
| Fold-2 deterministic spike | Harness exit-derived gate; 0.7 `spike_harness.py` | Present for recorded evidence |
| Separate submissions combine | Experimental complete active-set test | Baseline defect; experimental test present |
| Live freshness | 0.7 `_files_intact`; current regate helper rereads files | Baseline evaluation gap; D5-F tests absent |
| Re-gate | 0.7 and current Claude helper rerun stale active heads | No application/host conformance test found |
| Refutation/history | Append-only leaves and refuted terminal distinction | Present |
| Budget/idle progress | Operational counters and pure budget logic | Present |
| Durable async lifecycle | Reservations/tickets only | Not implemented; conformance absent |
| Independent audit | Ticket/dossier/verdict/coverage/model attribution | Present for current partial host paths |
| Freeze first-write-wins | Operational freeze fields and current tests; historical behavior | Present |
| Terminal honesty | Terminal statuses and lifecycle fail-closed behavior | Present |
| Compaction | Restore views and host renderers | Partial; pending child and context subset absent |
| Progressive contract | Full contract currently rides ordinary views/Blocks | Not implemented |
| Host capability honesty | Prose and adapter behavior only | No registry/conformance matrix |

The D4 test inventory must add or strengthen live freshness/re-gate, durable async lifecycle,
progressive selection, context bounds, host capability declarations, exact-once completion, and pure
core no-I/O proof.

## 6. Compatibility and subtraction freeze

Confirmed removal candidates for D6/D16:

- `adapters/claude/migrate_legacy.py` (305 lines), `make migrate-legacy`, its docs/tests, and
  activation-validator exception;
- runtime acceptance/defaulting of old state/protocol shapes, while retaining explicit fail-closed
  recognition and a fresh-run instruction;
- v1 protocol runtime/package acceptance after v2 cutover;
- frozen evidence-leaf composite `verdicts` and every decoder/test/fixture that treats them as
  adjudication input;
- live direct `kind="evidence"` boolean approval shortcut;
- Claude/Codex adapter-owned composite verdict policy;
- compatibility re-exports introduced only to preserve old imports;
- free-form reason/agent-marker control paths replaced by available structured identifiers;
- duplicated enum/table mirrors after generated/mechanical parity exists;
- nudge machinery only after D9 proves equivalent or better action-local/next-turn guidance.

Not classified as compatibility:

- `supersedes` append-only history;
- fail-closed legacy/corrupt recognition;
- clockless operation where the host supplies no timestamp;
- conservative transport failure direction;
- first-write-wins freeze;
- opaque run handles;
- CAS retries and artifact-pointer validation.

## 7. Current host async capability facts

| Capability | Claude | Codex | Pi |
|---|---|---|---|
| Spawn reservation | Application PreToolUse path | Pre-tool-use path | `tool_call` path |
| Durable reservation/ticket | Yes, operational state | Yes, operational state | Yes, operational state |
| Durable native child ID | None found | None found | None found |
| Pending child entity | None | None | In-memory ticket correlation only |
| Completion observation | Auditor `SubagentStop` | No child completion callback found | `tool_result`; auditor forced foreground |
| Failure/cancel/timeout/orphan states | No general handling | None found | No durable general handling |
| Reload recovery of pending child | Not proven | Not proven | Not proven; map is process memory |
| Final output/transcript | `last_assistant_message` or transcript fallback | Not proven | Tool result for foreground call |
| Audit dossier injection | Yes | Ticket path exists; final round trip not proven | Yes for forced foreground auditor |
| Audit privacy from author | Child prompt private; child output later host-visible | Not proven | Not private after tool result |
| Completion veto | Parent Stop can block convergence | Stop can block | No general completion veto; report tool can block claim |

These are source facts, not final support tiers. D1-H requires official API research/live probes and a
maintainer decision for every row.

## 8. Model-visible/context baseline

Canonical fixture payload sizes (compact JSON; token count is only `ceil(chars/4)`, not an actual
host-rendered tokenizer measurement):

| Fixture | Chars | Approx. tokens |
|---|---:|---:|
| block audit | 862 | 216 |
| block open claim | 846 | 212 |
| get argument | 3,266 | 817 |
| issue audit ticket | 435 | 109 |
| phase transition | 352 | 88 |
| reserve spawn denied | 980 | 245 |
| restore run | 1,315 | 329 |
| terminal budget | 901 | 226 |
| terminal frozen | 901 | 226 |
| void spawn | 539 | 135 |

Current ordinary run views carry the full obligation contract. Actual model-visible host messages may
add adapter prose and differ from fixtures; no tokenizer-backed StartRun/Block/stale/pending-audit/
compaction sample set exists. D0 therefore freezes these as a payload proxy and D9/D16 must add
actual rendered context fixtures with deterministic bounds.

## 9. Make/test baseline

Existing suite composition:

- `check-static`: lint, manifests, docs, ADR, contract, obligations, vendor, activation;
- `check-core`: generic obligations, core, application, state, git artifact repository;
- `check-claude`: activation lifecycle and Claude adapter;
- `check-codex`: methodologist/Empirica Codex validators and tests;
- `check-pi`: Pi bundle, methodologist Pi, Empirica Pi;
- `check`: all five subject suites;
- `check-ci`: static/core/Claude/Codex, plus Pi only with `PI_CHECKS=1`;
- `release-check`: currently `check` followed by human release instructions; no commit validator yet.

Static baseline test inventory: 222 Python test functions/methods and 66 TypeScript `test(...)`
calls. Passing-gate evidence remains the previously recorded merged 1.3 verification; D12 reruns the
full current suite after implementation.

## 10. Experimental two-fold delta quarantine

The uncommitted diff:

- adds `core.evidence.project_leaf` and `two_fold_verdict`;
- routes graph-aware complete active-leaf recomputation through application oracle call sites;
- delegates Claude verdict projection to core;
- adds a separate research/spike submission regression;
- retains frozen request-time verdict telemetry;
- defines recorded metadata as sufficient freshness;
- asserts in `2F5` that deleting a bound file after recording does not revoke approval.

D5 may retain neutral projection and complete active-set evaluation. D5-F must reverse `2F5`, remove
frozen composite verdicts, observe current bound files for every state-bearing read, and prove
stale→open→deterministic re-gate→approved.

## 11. D1/D1-H decision inputs

The next freeze must resolve, from official/live evidence and the normative invariants:

1. Exact v2 public contract sections, reason codes, next-action IDs, and context-selection inputs.
2. Workspace observation shape, authority/error cases, path normalization, and operations requiring
   a fresh observation.
3. Spike attestation/re-gate command and supersession semantics.
4. Per-host async lifecycle API evidence and advertised tier.
5. Durable child identity/transitions, result admission, timeout/orphan/reload behavior.
6. Strict old-run refusal view and fresh-run guidance.
7. Current direct boolean-evidence removal shape.
8. Operational-state versus artifact-history fields for child lifecycle and contract history.
9. Exact architecture removal manifest and schema/mirror generation boundary.
10. Actual model-visible context budgets and deterministic fallback section.

No implementation agent is launched until D1/D1-H are parent-frozen.

## 12. D0 acceptance

D0 is accepted when this artifact and the final DAG agree on:

- feature-preservation rows;
- semantic authority/projection distinctions;
- effective runtime metric definition and baseline;
- compatibility removal versus current safety;
- experimental diff quarantine;
- current source-level host capability facts;
- missing test/context evidence feeding D1–D4.

**Parent disposition:** accepted as the factual baseline for D1/D1-H discovery. It does not accept the
uncommitted code delta or authorize implementation.
