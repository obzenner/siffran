# Empirica 2.0 D7/D7-W — application transactions and single-writer proof

**Status:** Parent-frozen implementation specification; requires Terra/Sol acceptance before red tests.

**Normative inputs:** D0, D1, D1-H, accepted D2/D2A/D2B/D2C/D2D/D2E, D3, D4, D5/D5-F, D6, final-DAG §5/§7/§9.

**Baseline:** D6-C4 post-subtraction tree at 6,486 effective runtime LOC (62 files), under the 9,457 maximum. D6 seam modules (`application.protocol`, `application.run_state`, `application.v2`) and D5 modules (`core.freshness`, `application.ports`, `application.observation`) are green. The bridge composes `application.v2` with a no-location run port; D7 reconnects v2 identity and storage.

## 1. Whole and stage outcome

D7 turns the D6 strict shell into a working evaluator for the D7-owned command set. At D7-W completion:

- StartRun, ResolveRun, GetRun, GetArgument, RestoreRun, EvaluateRun execute real read/write behavior through a located application repository boundary.
- ObserveAction admits author actions `route`, `investigate`, `graph`, `research`, `spike_request`, `freeze`, `configure_run`, producing append-only artifacts and CAS state transitions through one transaction coordinator.
- The transaction coordinator is the sole writer of operational state and append-only domain artifacts; adapters and trusted ingress emit commands.
- A complete immutable `EvaluationSnapshot` joins the selected graph, active evidence derived from complete committed history, current workspace observations, audit/child state, and budget/scope facts.
- A pure `evaluate_snapshot` decides `Allow | Block | Inert | Fault` and returns a declarative mutation intent; a pure projection renders the committed revision.
- D4 cases 2–5, 16, 18–20 turn green. Case 17 and whole case 21 remain D8-blocked. Case 1 and D9 cases remain red. D5 cases 6–15 may green through integration but are not D7 owner acceptance.
- Total effective runtime ≤ 9,457 LOC.

## 2. Non-goals

D7 does not implement: D8 audit/child admission, durable child state machine, idempotent completion, reload recovery, timeout/orphan, late-result rule; D9 GetContract, bootstrap card, context selector, compaction; D10 host adapter behavior, native event translation, capability policy; backward compatibility, v1 decoding, legacy handle migration; a second state model or persisted projection/verdict. Zero-budget child denial is the only child behavior D7 owns. No audit policy, child transitions, GetContract, context selector, compaction, or host behavior.

## 3. Methodology rationale (non-normative)

This spec applies functional decomposition (Dijkstra/Parnas/Simon), abstraction refinement (Wirth/Dijkstra/Back), and proof by contradiction to the single-writer transaction boundary. The rationale is audit evidence only and adds no normative rules. Proof by contradiction: if adapters write directly, concurrent interleaving binds a decision to artifacts the state revision does not reflect, contradicting final-DAG §5 sole-writer; if the handle is an authoritative capability, it reintroduces a storage-selection index, contradicting D6-C §4. Therefore single coordinator, sole writer, append-before-CAS, private self-locating handle.

## 4. Corrections to prior discovery overclaims

| Overclaim | Correction |
|---|---|
| "D7 owns orchestration and writes policy decisions in the application layer" | Application owns orchestration and writes (CAS/append); **policy is pure in core**. The coordinator commits a mutation intent from the pure core; it never decides evidence/claim/convergence. |
| "D7 must preserve backward compatibility with 1.3 run handles" | **No backward compatibility.** D6 deleted v1/migration; D7 adds no legacy decode, index, or migration. An old/v1 selector fails closed. |
| "CAS updates bump the generation to avoid conflicts" | **CAS updates the same generation.** Only a terminal/corrupt selector starts the next generation; a live run never changes generation mid-flight. |
| "D7 will make D4 cases 1–44 green" | **D7 owns cases 2–5, 16, 18–20 only.** Case 17 and whole case 21 are D8; case 1 and 36–44 are D9; 6–15 are D5 owner. |
| "Inert carries run.no_active/run.terminal with a RunView" | **Inert carries only schema enum `no_run`/`unsupported_host_event` and no RunView.** `run.no_active`/`run.terminal` are Block reason codes. |
| "Missing graph → graph.missing, malformed → graph.invalid" | **No selected graph when required yields `graph.invalid`; a persisted selected-graph pointer whose artifact is missing, malformed, or structurally invalid makes the aggregate `run.corrupt`.** |
| "Terminal runs report a `failed` status" | **No `failed` status.** Terminal statuses: `converged`, `stopped_residual`, `stopped_frozen`, `stopped_budget`. |
| "Storage IDs use `sha256:<64hex>`" | **Storage IDs use `s256-<64hex>`.** |
| "compose takes an allocator parameter" | **compose keeps its current signature; the located facade wraps the allocator internally.** |

Acting on an overclaim is a stop condition.

## 5. State authority and internal amendment

### 5.1 Committed-history head amendment

The D6 `empirica.run/2` state schema is internally amended with one required nullable field:

```text
committed_artifact_head_id   digest256 | null
```

This is the sole committed-history visibility pointer: the content digest of the most recent `transaction_manifest` artifact reachable from this state. It is not a second state model, policy, or persisted projection. The `state_schema` remains `empirica.run/2`; no compatibility or version bump. State schema, fixtures, and codec are amended to include this field. The `run_state` codec only validates the **head field grammar** — that `committed_artifact_head_id` is either `null` or a well-formed `digest256`. State written before the amendment (without the field) classifies as `current_corrupt` — D7 is the first to write real state. Manifest traversal corruption (§7.2) is snapshot/history corruption, not a codec classification: it produces a `run.corrupt` Block, never `current_corrupt`.

### 5.2 State authority rules

- The graph and active evidence are authoritative argument content (append-only artifacts).
- Operational state holds only the closed D6 fields plus `committed_artifact_head_id`; no persisted claim state, obligation contract, composite verdict, projected snapshot, phase, reservation, audit ticket, or host profile.
- `evaluation.derive_claims` is the only claim-state derivation: it combines local evidence with scoped conjunctive dependencies on read; claim states are never persisted.
- Active/deferred obligations are ephemeral projections, never persisted.
- No new persisted representation without authority declaration and projection invariant test.

### 5.3 Graph failure

`graph.invalid` applies to a missing required graph or an invalid candidate graph. A candidate is
validated before artifact creation and cannot replace the selected graph when invalid. Once state
contains `selected_graph_artifact_id`, that exact artifact must be reachable, decodable, and a valid
strict dependency DAG; otherwise the persisted aggregate is `run.corrupt`. A fresh `StartRun`,
`ResolveRun`, or `GetRun` may `Allow` a complete `RunView` with a null graph pointer and no
`ArgumentView` because no selected graph is yet claimed.

## 6. OperationalState value type

One immutable operational value type lives in `core/run.py`:

```text
OperationalState (frozen dataclass):
  protocol, state_schema, goal, status, modes, budgets,
  selected_graph_artifact_id, frozen_claim_ids, frozen_semantic_digest,
  route_stamp, investigation_stamp, stamp_seq,
  last_derivation_digest, children,
  committed_artifact_head_id
```

Exact fields match the D6 `empirica.run/2` schema plus the committed head. `application/run_state.py` remains the sole schema classifier/codec constructing `OperationalState` from raw JSON and encoding it back. No duplicate dict or dataclass. Core evaluation returns a pure next `OperationalState` (or `StateIntent`) without importing `application`. The core produces the full next `OperationalState` with the **prior head unchanged**; the coordinator then uses `dataclasses.replace(state, committed_artifact_head_id=manifest_id)` to set the committed head to the appended manifest's content digest **before** CAS — not after CAS success. CAS compares and sets the **replaced** state; the state written on success already carries the new head.

## 7. Transaction manifest and committed history

### 7.1 Internal transaction_manifest artifact

The `ArtifactRepository` remains an unordered commutative set (ADR-31). D7 adds one internal closed artifact kind — `transaction_manifest` — never projected in public `ArgumentView`:

```text
transaction_manifest:
  version               1
  parent                digest256 | null      # prior manifest content digest, null for genesis
  artifact_ids          ordered unique domain artifact_ids appended in this transaction
  observation_basis_id  nonempty              # workspace observation basis ID
  observation_digest    digest256             # ObservationSnapshot digest
  next_state_digest     digest256             # canonical digest256 of next OperationalState excluding committed_artifact_head_id
```

Manifest ID is its content digest. The coordinator constructs the manifest; the pure evaluator does not.

`parent` and the ordered `artifact_ids` define the exact post-plan committed history. Active heads derive only from reachable history (§7.2) — there is no duplicate active-head field in the manifest. `next_state_digest` is the canonical digest256 of the next `OperationalState` **excluding** `committed_artifact_head_id`; it is an integrity witness, never authority or cache. The latest manifest's `next_state_digest` must match the canonical digest256 of the current state excluding the head, or the run is `run.corrupt`. The manifest binds `parent` + `artifact_ids` + `observation_basis_id`/`observation_digest` + `next_state_digest` in one content-addressed body.

### 7.2 Committed-history traversal

Starting from `committed_artifact_head_id` in the state, traverse the manifest chain (via `parent` links) over the physical artifact set. This defines the committed ordered history and the D2C artifact sequence. Active heads derive only from this reachable history. Unreachable orphans (appended but not linked) are ignored. Missing, malformed, cyclic, duplicate-ref, wrong-kind, or wrong-head manifests, a missing/malformed/structurally invalid selected graph artifact, or a latest `next_state_digest` mismatch are **snapshot/history corruption**: they produce a fixed-safe `run.corrupt` Block. A null selected pointer remains valid operational state until a command requires a graph.

### 7.3 Transaction state machine

Every mutating transaction — even state-only — follows:

```text
1. append domain artifacts to ArtifactRepository (commutative, idempotent set union)
2. construct transaction_manifest (parent = current committed_artifact_head_id)
3. append manifest to ArtifactRepository
4. compare_and_set state with committed_artifact_head_id = manifest content digest
```

CAS conflict → discard the whole attempt and retry: reread state/artifacts, recompute active heads, re-observe complete paths (one lexical batch), re-evaluate, re-append, re-manifest, re-CAS. Observations from the discarded attempt are never mixed with the new one. CAS loss retries the full attempt.

An orphan append (artifact appended but subsequent CAS fails) is safe and unselected: it remains in append-only history but no manifest links it; re-read traverses from the pre-CAS head and ignores it. History is never rolled back.

### 7.4 Idempotent append

Retrying the same artifact content is idempotent (content-addressed set union). A retried commit re-appends the same artifacts before a fresh CAS without duplication.

### 7.5 Spike two-commit protocol

A `spike_request` ObserveAction executes as two commits:

1. **Request commit:** append `spike_request` domain artifact + manifest, CAS state with committed head. This publishes the request before harness execution.
2. **Execute once:** run the harness against the immutable `ExecutionSnapshot` (captured bytes) after the request commit. Result facts are stable. The harness receives no workspace or ambient authority; exit code is the sole spike-gate authority.
3. **Result commit:** append spike-result domain artifact + manifest, CAS state.
4. **Conflict:** a CAS conflict on the result commit revalidates and retries the result commit without automatic harness rerun (the result facts are immutable).

A crash after the request commit leaves a reachable request without a result (safe) or an unreachable physical orphan (safe). Exit code only is authority for `spike_gate`.

## 8. Identity and location

### 8.1 Selector → storage ID

Request `selector` raw strings are not storage IDs. D7 derives a safe storage ID:

```text
storage_id = "s256-" + sha256(raw_string_utf8).hexdigest()
```

Storage IDs are opaque, collision-resistant, safe as filesystem segments, and carry no host path or `cwd`. The `RunKey` (ADR-31) is `RunKey(project_id, run_id, generation)` where `project_id`/`run_id` are derived storage IDs and `generation` is a positive integer.

**API contract (codec):**
- `storage_id(raw)` — `raw` must be a `str`; non-string raises `TypeError`. Empty string is valid and returns the formula literal (`s256-e3b0c44…`).
- `encode_handle(RunKey)` — argument must be a `RunKey`; non-`RunKey` raises `TypeError`. Invalid `p`/`s`/`g` (non-string `s256-<64hex>`, bool, zero, or non-positive generation) raises `ValueError`.
- `decode_handle(token)` — returns `RunKey | None`; never raises for representative arbitrary inputs (malformed, wrong type, tampered, legacy, unknown prefix). Requires exact built-in `str` (subclasses rejected before any method call).

### 8.2 Public run handle — er2 self-locating token

The public run handle is a private canonical self-locating `er2` token — opaque, non-authoritative, self-locating. It is not a capability and cannot select storage.

**Token format:** `er2:<base64url(payload)>:<base64url(sha256(payload_bytes))>`

**Canonical payload** (JSON, sorted keys, no whitespace): `{"p":"<project_storage_id>","s":"<session_storage_id>","g":<positive generation int>`

**Exact decoder:**
1. Require exact built-in `str` (subclasses rejected before any method call); else malformed.
2. Split on `:` into exactly `["er2", payload_b64, checksum_b64]`; else malformed.
3. Prevalidate strict URL-safe base64 alphabet `[A-Za-z0-9_-]+` for both segments (no `=`, `+`, `/`); reject impossible lengths (`len % 4 == 1`).
4. base64url-decode both; any failure → malformed.
5. Reject nonzero pad-bit aliases: require `_b64url_nopad(decoded) == original` for both payload and checksum segments.
6. Checksum must be exactly 32 bytes (full SHA-256); constant-time compare (`hmac.compare_digest`): `sha256(payload_bytes) == checksum_bytes`; mismatch → tampered.
7. Parse payload JSON; require exactly keys `p`, `s`, `g` with `p`/`s` exact `str` `s256-<64hex>` and `g` exact `int` positive (`bool`/`float` rejected); strict canonical re-encode check; else malformed.

Malformed/tampered/unknown handle → `Inert` `no_run`. No index, no legacy decode, no migration.

### 8.3 Resolve / Start / terminal / corrupt

- **ResolveRun absent** → `Inert` `no_run`. ResolveRun never returns a Block.
- **StartRun on active** → returns existing run unchanged (same handle, current view). No reset, bump, or overwrite.
- **StartRun on terminal/corrupt** → starts next generation (current + 1) with fresh empty state. Prior generation left intact (isolation).
- **StartRun create race** → `RunRepository.create` first-writer-wins (ADR-31). Loser catches `Conflict`, rereads, recomputes active generation. If winner created next gen, loser returns it unchanged.
- **ResolveRun on terminal/corrupt** → `Inert` `no_run`; no auto-advance. Only StartRun advances.

### 8.3.1 StartRun genesis

A `StartRun` that creates a new run (absent or next generation) appends an **empty genesis manifest** as the initial committed-history entry, then calls `RunRepository.create` to write the initial state pointing to it:

```text
genesis transaction_manifest:
  parent                null                  # no prior manifest
  artifact_ids          []                    # empty — no domain artifacts
  observation_basis_id  canonical_digest(())  # exact D5 build_observation_snapshot empty basis
  observation_digest    canonical_digest(())  # exact D5 build_observation_snapshot empty digest
  next_state_digest     <canonical digest256 of next OperationalState excluding committed_artifact_head_id>
```

The initial `OperationalState` is created with `committed_artifact_head_id` set to the genesis manifest's content digest and `selected_graph_artifact_id = null`.

For any **no-active-path** mutating transaction (including genesis), D7 uses the exact D5 `build_observation_snapshot` empty result — `basis_id = canonical_digest(())`, `digest = canonical_digest(())`, `observations = ()` — with zero `Workspace.observe` calls.

**Create race:** if two concurrent `StartRun` calls race, both append a genesis manifest, but `RunRepository.create` first-writer-wins (ADR-31). The loser's orphan genesis manifest is harmless and unselected; the loser catches `Conflict`, rereads, and returns the winner's run unchanged. A duplicate genesis manifest in append-only history is an ignored orphan (§7.3).

### 8.4 Bridge composition

`application/location.py` defines DTO/protocol/codec only. `adapters/state/located.py` is the concrete facade wrapping `FilesystemRunRepository` + `GenerationAllocator`. `compose` keeps its current signature and receives `runs` as the located facade; no concrete allocator import or extra parameter. The repository root is a construction-time fact from the current repo (cwd), never a request wire field. Concrete filesystem workspace/harness ports are added only if D7's real bridge needs them. The D4 test fake is upgraded as a storage fake (using real storage interfaces) with no policy.

## 9. Workspace and observation integration

D7 consumes the D5/D5-F ports exactly as frozen. The snapshot assembly computes `required_paths(active_heads)` (lexical union, D5) and calls `Workspace.observe` with the complete tuple in one lexical batch. No per-file reads, no partial snapshot, no mixing captures. The workspace returns one coherent batch with a truthful as-of returned basis; D7 does **not** claim detection of silent post-capture mutation.

Capture or `ObservationUnavailable` fails closed immediately as a schema-valid `Fault` `unavailable`/`closed` — no retry, no reobservation. Only a state revision change or CAS conflict triggers whole-attempt retry and reobservation (discard → reread, recompute heads, re-observe in one lexical batch, re-evaluate, re-append, re-manifest, re-CAS). There is no unconditional second observation, no `validate_basis` step, and no detection of silent post-capture workspace mutation. The workspace captures a truthful as-of basis. A read does not observe twice by default; a second observation happens only when a state revision change or CAS conflict forces it. Changed observation is tested explicitly after a failed CAS.

For a read-only response: observe, evaluate/project, reread state revision; if the revision changed, retry (reread, recompute, re-observe, re-evaluate). Identical revision → single observation.

### 9.1 Post-success projection

After a successful CAS, the coordinator constructs a **provisional committed snapshot** by deriving active heads from the new reachable manifest chain (parent + ordered artifact_ids) and applying the known domain appends + manifest + next state to the exact attempt snapshot, reusing the same `ObservationSnapshot`. No I/O reassembly or reobservation. The response is projected from this provisional snapshot. A diff check confirms the provisional snapshot is consistent with the committed manifest chain. The next request captures a fresh observation.

## 10. Response exactness

### 10.1 Inert

`Inert` carries only schema enum `no_run` or `unsupported_host_event` and **no RunView**.

| Condition | Response |
|---|---|
| absent / malformed / unknown handle | `Inert` `no_run` |
| ResolveRun on terminal run | `Inert` `no_run` |
| EvaluateRun `continue` on terminal run | `Inert` `no_run` unchanged |
| ObserveAction on terminal run | `Inert` `no_run` unchanged |
| trusted late ingress on terminal run | `Inert` `no_run` unchanged |

Never use `Inert` with `run.no_active` or `run.terminal` — those are Block reason codes.

### 10.2 Allow (terminal read)

| Condition | Response |
|---|---|
| GetRun / RestoreRun on terminal run | `Allow` current `RunView` assembled from exact fresh D5 `ObservationSnapshot` for all active spikes, projecting current freshness/derived claim/audit/obligation facts; `converged` iff `status == "converged"`; no state transition, CAS, or status change; `converged` boolean remains historical from immutable status |
| GetArgument on terminal run | `Allow` current `RunView` **and** `ArgumentView` assembled from exact fresh D5 `ObservationSnapshot` for all active spikes, projecting current freshness/derived claim/audit/obligation facts; `converged` iff `status == "converged"`; no state transition, CAS, or status change; `converged` boolean remains historical from immutable status |
| EvaluateRun `report_convergence` / `stop` on terminal run | `Allow` current `RunView` assembled from exact fresh D5 `ObservationSnapshot` for all active spikes, projecting current freshness/derived claim/audit/obligation facts; `converged` iff `status == "converged"`; no state transition, CAS, or status change; `converged` boolean remains historical from immutable status |

A terminal `GetArgument` returns both the current `RunView` and the current `ArgumentView` only when committed history and the selected graph are valid. Any malformed/missing reachable history, state-witness mismatch, or missing/malformed/structurally invalid selected graph artifact produces fixed-safe `run.corrupt`; a corrupt aggregate cannot yield a truthful projection.

### 10.3 Block

Block is used for active-run failures requiring Block + RunView (e.g., `run.corrupt`, `graph.invalid`, `route.required`, `budget.exhausted`). Block `run.terminal` is used only if a canonical operation explicitly requires Block + RunView. No new schema, reason code, or response field.

### 10.4 Active-evidence derivation (D2C)

Active evidence is derived from complete committed history (§7.2) and the current claim digest on every read/commit. A spike result's `supersedes` names a prior spike result for the same claim; at most one spike result is active per current claim digest. No `last_spike` field, no verdict cache, no latest-request shortcut. Approval follows D1 §5 / D2C §10.13–14: `ordinary` requires active supporting research; `needs-experiment` additionally requires an active passing spike with satisfied prerequisites and no freshness change; `needs-decision` cannot project approved.

## 11. MutationPlan and core interfaces

```text
evaluate_snapshot(snapshot: EvaluationSnapshot) -> Decision + StateIntent
project_runview(snapshot: EvaluationSnapshot) -> RunView
project_argument(snapshot: EvaluationSnapshot) -> ArgumentView
```

`StateIntent` carries the full next `OperationalState` (prior head unchanged) plus the ordered domain artifacts to append. The core returns no capability, host event, or audit verdict. The coordinator owns ordering (§7) and the CAS.

`EvaluationSnapshot` is one immutable value joining: selected graph artifact, active evidence (derived from committed history via D2C), current workspace `ObservationSnapshot` for every active spike head, audit/child state, and budget/scope facts. It is ephemeral per read/commit — never persisted.

## 12. Budgets

- Defaults: both modes `false`; `max_passes=8`, `max_spawns=1` unless request supplies explicit budgets. Request budget overrides injected `limits`; injected limits override defaults when request omits.
- A derivation pass is consumed only when the semantic derivation digest changes and the commit succeeds. `GetRun`, `GetArgument`, and identical `EvaluateRun` consume no pass.
- Exhausted `child_reserve` (`max_spawns` exceeded) → `Block` `budget.exhausted` (`resource: spawn`), appends **no child record**. Non-exhausted child request → `unsupported`/closed until D8.
- Route before investigation: route and investigation are positive, strictly ordered first-write
  witnesses. Research, spikes, executable children, trusted audit facts, and convergence require
  both. Candidate rejection precedes domain artifacts, budget/child mutation, harness execution,
  manifests, and CAS. Reachable investigative artifacts persist the exact witness pair; mismatch is
  `run.corrupt`. Native Claude/Pi pre-tool gates deny investigation before execution. Honest stop and
  preparation-only graph/configuration/freeze remain available.
- Freeze first-write-wins: ordered `frozen_claim_ids` and canonical `frozen_semantic_digest` are set atomically only when both are null; repeated freeze is `Inert` unchanged. The digest binds exact committed claim records and sorted edges with frozen endpoints. Candidate mismatch is zero-write `graph.invalid`; persisted mismatch is `run.corrupt`. Later claims and cross-scope edges remain deferred and audit-invalidating.
- Terminal status first wins: once non-`active`, no later event changes status or produces convergence.

## 13. Ownership and modules table

| Module | Owns (secret) | Must not own |
|---|---|---|
| `core/run.py` | Immutable `OperationalState` value type | I/O, schema validation, codecs |
| `core/evaluation.py` | Pure `evaluate_snapshot` (decision + `StateIntent`) | I/O, repositories, adapters, `application` import |
| `core/projection.py` | Pure `project_runview` / `project_argument` | I/O, repositories, adapters, `application` import |
| `application/location.py` | Selector → storage ID, `er2` codec functions only | Host paths, concrete repository impl, facade protocol/adapter |
| `application/snapshot.py` | I/O assembly: build immutable `EvaluationSnapshot` | Policy decisions, CAS |
| `application/transaction.py` | Append-before-CAS ordering, retry, idempotency, manifest construction, provisional snapshot | Policy decisions |
| `application/run_state.py` | Schema classifier/codec constructing `OperationalState` | Policy, I/O |
| `application/v2.py` | Thin dispatch (validated command → module) | Policy |
| `application/protocol.py` | Schema SSOT, `dispatch_request` gateway | D7-specific dispatch logic |
| `adapters/state/located.py` | Concrete facade: `FilesystemRunRepository` + `GenerationAllocator` | Domain policy |

Dependency direction (D3 enforced): `core/*` is pure (imports only `core` siblings + stdlib); `application/*` may import `core/*` and `application.protocol`; no adapter imports. `application/v2.py` is thin dispatch to `transaction.py`, `snapshot.py`, `location.py`, and D6 `protocol`/`run_state`. No duplicate loader.

## 14. D4 owner ledger

| Case | D7 behavior | Status |
|---|---|---|
| 2 | Route must precede investigate; investigate-first Blocks `route.required` | D7 green |
| 3 | Valid route then investigation proceeds (Allow + witness) | D7 green |
| 4 | Claim state derived; only committed support scope gates | D7 green |
| 5 | Missing required graph or invalid candidate fails `graph.invalid`; corrupt persisted selected graph fails `run.corrupt` | D7 green |
| 16 | Derivation and spawn limits enforce structured budget Blocks | D7 green |
| 18 | Freeze first-write-wins under repeated/conflicting requests | D7 green |
| 19 | Committed frozen scope remains sole gating/audited scope; later claims deferred | D7 green |
| 20 | Stopped/frozen/budget/refuted runs honestly non-converged | D7 green |
| 1 | Bootstrap card, progressive disclosure | D9 red |
| 6–15 | Evidence/freshness/re-gate | D5 owner (may green via integration; not D7 claim) |
| 17 | Idle wait / pending audit/child consume no pass (child-pending part) | D8 red |
| 21 | Late evidence/audit/child result after terminal never reconverges | D8 red (whole case) |
| 36–44 | Progressive context selection, compaction | D9 red |

## 15. Serial red-first slices

No production before red. One GLM writer, serially, verified before the next slice.

| Slice | Content |
|---|---|
| A | Red identity/location tests against absent `application/location.py` |
| B | Implement `application/location.py` (codec functions only: `storage_id`, `encode_handle`, `decode_handle`). A tests green; compose green target into `check-core`. Does NOT wire bridge/located adapter — facade protocol/adapter remains Slice F. |
| C | Red internal schema amendment (committed head), manifest, snapshot/evaluation/projection tests against absent modules |
| D | Implement `core/run.py`, `core/evaluation.py`, `core/projection.py`, `application/snapshot.py`; amend `run_state` codec. C tests green. |
| E | Red transaction race/idempotency tests against absent `application/transaction.py` |
| F | Implement `application/transaction.py`, wire `application/v2.py` thin dispatch, `adapters/state/located.py`, bridge composition to located repo + `GenerationAllocator`. E tests green; D6 strict codec and D4 45–47 green; D4 D7-owned cases green. |
| W | Single-writer transaction/CAS/idempotency proof. D7-W red tests green; runtime ≤ 9,457. |

Per-slice runtime is measured, not suggested. Each slice reports physical LOC delta.

## 16. Red-first tests

### D7 focused tests (A/C/E slices)

- **Locator roundtrip:** selector → `s256-<64hex>` storage IDs → `RunKey` → `er2` encode → decode → equality; base64url canonical; full SHA-256 checksum verifies; strict canonical re-encode.
- **Locator tamper:** flip payload/checksum/generation byte → malformed/tampered → `Inert` `no_run`; no index lookup, no non-`er2` decode.
- **No index:** raw selector or foreign handle never selects storage.
- **Generation:** StartRun absent → gen 1; StartRun terminal → next gen; ResolveRun terminal → `Inert` `no_run`; active CAS stays same gen.
- **Create/CAS race:** concurrent StartRun → first-writer-wins; loser rereads and returns existing unchanged.
- **Append-before-CAS orphan:** append succeeds, CAS fails → orphan unselected; re-read ignores it.
- **CAS retry full recompute:** conflict → whole attempt discarded; reread; recompute heads; re-observe (one batch); re-evaluate; no mixing.
- **Head add/remove:** new spike result with `supersedes` deactivates prior; selecting new graph changes heads; re-observation covers new set.
- **Changed observation after failed CAS:** after a failed CAS, the retry discards the whole attempt and re-observes; changed observation is tested explicitly after failed CAS, not by detecting silent post-capture mutation.
- **Overlap one lexical call:** overlapping active spike paths observed in one deduplicated batch.
- **Full recompute:** any state-bearing read/commit recomputes active evidence from complete committed history.
- **Idempotent append:** retry re-appends same content → no duplicate.
- **Corrupt/absent fail closed:** corrupt manifest chain or selected persisted graph → `run.corrupt` Block; absent selector → `Inert` `no_run`; null graph pointer is valid until an operation requires a graph.
- **Manifest traversal:** cyclic/missing/duplicate/wrong-kind manifest → `run.corrupt` Block; orphan ignored; selected graph reachable.
- **Spike two-commit:** request committed before harness; execute once; result committed; conflict retries result commit without rerun; crash-safe orphan.
- **Terminal response exact:** GetRun/GetArgument/RestoreRun/Evaluate(stop/report_convergence) on terminal → Allow (converged iff status converged); Evaluate(continue)/ObserveAction/ingress on terminal → Inert `no_run`.

### D7-W red tests

- **Projection invariants:** pure projection: identical committed snapshots → identical views; no I/O; no private fields.
- **Concurrent/stale-writer retry:** two writers race CAS; loser retries fresh snapshot; final revision coherent.
- **Idempotency:** retried mutating commit → same state; no duplicate artifacts; no double pass.
- **Append-then-pointer ordering:** orphan append unselected; successful commit has consistent pointers.
- **Fail-closed reads:** read against corrupt/missing/cyclic/duplicate/wrong-kind committed history, a state-witness mismatch, or a missing/malformed/structurally invalid persisted selected graph fails fixed-safe `run.corrupt`; capture/`ObservationUnavailable` fails closed `Fault` `unavailable`/`closed` immediately with no retry; never approves.

## 17. Runtime budget

Total effective runtime ≤ **9,457 LOC** (D6-C4 maximum). Post-D6-C4: 6,486 LOC across 62 files. Per-slice LOC is measured by `make empirica-architecture-check`. Added modules are measured against this cap. The writer reports every added/deleted runtime file and physical LOC.

## 18. Stop conditions

Stop and escalate if:

- a D7 operation requires a **public schema change** (new command/action/reason/status/section/host tier, or new public response field). The internal `committed_artifact_head_id` amendment is allowed.
- a **second state model** would be required (persisted snapshot/projection/verdict).
- **v1** behavior (migration, legacy decode, backward compatibility) is required.
- **D8/D9/D10 behavior** is required to produce a truthful result and cannot be honestly `unsupported`/closed. (Zero-budget child denial is the only admitted D8-related behavior.)
- the runtime **cannot stay ≤ 9,457** after D7 additions.
- acting on a discovery overclaim (§4) would be required.

## 19. Verification

Per slice, run focused Make commands and record exit codes. Before D7-W acceptance:

```text
make check-core
make check-static
make empirica-architecture-check
make empirica-v2-conformance
```

D6 strict 44 and D4 45–47 remain green. D4 D7-owned cases (2–5, 16, 18–20) green. D4 17, 21, 1, 36–44 remain red at owner gap. `git diff --check` clean; index empty. No staging or commit.

## 20. Handoff

Report: files changed/created and every runtime file added/deleted with physical LOC; D4 owner ledger (green/red by owner); D7-W proof results; focused Make commands with exit codes; effective runtime LOC and delta from 6,486 confirming ≤ 9,457; residual risks and unresolved decisions (escalated); confirmation no files staged/committed, no subagents, no outside search.
