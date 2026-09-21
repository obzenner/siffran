# Empirica 2.0 D6 — strict v2 shell and compatibility subtraction

**Status:** Parent-frozen decomposition proposal; requires Terra/Sol acceptance before red tests.

## 1. Whole and stage outcome

D6 replaces—not wraps—the reachable v1 protocol/service/state surface with one strict v2 shell. At
D6 completion:

- every Claude/Codex/Pi entrypoint emits `empirica/v2` and reaches `application.v2.compose`;
- raw request identity/shape is validated before command dispatch;
- persisted identity is classified before semantic decoding;
- exact current v2 state is strict and has no migration/defaulting/legacy branch;
- D4 cases 45–47 pass against the real seam after case 47's incidental StartRun setup is removed;
- other D4 cases remain honest behavioral failures for D7–D10, not absent-seam errors;
- all ordinary repository/host suites are updated and green for the behavior implemented at D6;
- v1 service/state/wire, migration, contracts, literals, and compatibility tests are deleted;
- total executable runtime is at most 9,457 LOC, repaying D5's 445 LOC and reducing D0 by at least one.

D6 is intentionally a **minimal strict shell**, not a temporary v1 translation layer and not a full
D7 evaluator. Unsupported-yet v2 commands return exact schema-valid Fault/closed; they never call v1.
D7–D10 implement their owner cases behind this seam.

## 2. Change axes

1. private persisted-state schema and strict codec;
2. closed request/response schema validation and deterministic Faults;
3. failure-safe old/corrupt state service/composition needed by strict tests; normal bootstrap/read is
   explicitly unsupported until D7/D9;
4. supported-host protocol cutover;
5. compatibility/runtime subtraction and measured LOC gate.

No axis may retain an internal v1 envelope/state/service as a fallback.

## 3. Serial modules and dependency order

### D6-A — internal state schema + red strict tests

Add `contracts/empirica/v2/state.schema.json` and fixtures. It is an internal host-neutral schema, not
PublicContract behavior. Extend contract validation mechanically, including purpose-shape parity,
complete child-branch coverage against the registry, and procedural rejection of non-finite child
deadlines. Add complete red tests for protocol,
state classification, codec, composition, and D4 45–47 expected outcomes. In the same red-test slice:

- remove case 47's incidental StartRun and use an opaque probe run ID, because every malformed request
  must fail before lookup;
- amend the D4 report owner map so route/investigate cases 2–3 are D7 and strict decoder case 47 is D6.

Production remains unchanged; new tests are observed red at missing v2 modules.

### D6-B — strict codecs and minimal service

Implement final modules:

- `plugins/empirica/application/protocol.py`
- `plugins/empirica/application/run_state.py`
- `plugins/empirica/application/v2.py`

Unit tests become green. D4 45–47 bind and become green after case 47 uses an opaque probe run ID
instead of StartRun. Other D4 cases must collect and fail on their unimplemented owner behavior,
never crash or route to v1.

### D6-C — host cutover + deletion + measured subtraction

Cut bridge/Claude/Codex/Pi to v2; remove old runtime and compatibility surfaces; update host tests and
fixtures. Run every suite and effective LOC gate. D6 is not accepted before this slice.

One GLM writer works serially. Parent freezes each slice and verifies before the next. No parallel
writers.

## 4. Canonical persisted v2 state

Exact identity:

```json
{"protocol":"empirica/v2","state_schema":"empirica.run/2", ...}
```

Closed required fields—no decoder defaults:

```text
goal                         nonempty string
status                       active | converged | stopped_residual | stopped_frozen | stopped_budget
modes                        {multi_provider:boolean, cli_exec:boolean}
budgets                      {max_passes:int>=1, passes_used:int>=0,
                              max_spawns:int>=0, spawns_used:int>=0,
                              max_audit_spawns:int>=0, audit_spawns_used:int>=0}
selected_graph_artifact_id   digest256 | null
frozen_claim_ids             null | unique ordered nonempty-string array
frozen_semantic_digest       digest256 | null (null exactly with frozen_claim_ids)
route_stamp                  int>=1 | null
investigation_stamp          int>=1 | null
stamp_seq                    int>=0
last_derivation_digest       digest256 | null
children                     ordered array of strict child records
```

Strict child record:

```text
child_id                     nonempty unique opaque ID
purpose                      nonempty opaque string
resource_class               investigation | audit (host-owned, immutable)
state                        canonical PublicContract child state
spent                        boolean
refunded                     boolean
deadline                     finite number | null
native_id                    nonempty string | null
first_terminal_fingerprint   digest256 | null
capability_ref               nonempty private string
```

Schema branches are mechanically checked against canonical PublicContract status and child-state
vocabularies; purpose remains the same nonempty opaque string accepted by D2 request/response schemas.
The internal schema cannot own a drifting enum. D6 implements no child transition table or mutation.
Branch shape rules:

- `launch_rejected` requires `refunded=true` and `spent=false`; every other state requires
  `refunded=false`;
- observed-start/terminal states except launch_rejected require `spent=true`;
- reserved requires `spent=false`, native/fingerprint null;
- launching/pending require non-null native ID, null terminal fingerprint;
- every terminal state requires terminal fingerprint; completed/adverse post-start require native ID;
- child IDs are unique; each used counter equals the non-refunded durable children in its exact
  resource class and is bounded by that class's maximum;
- audit-class children require canonical purpose and complete audit bindings; investigation-class
  children forbid audit bindings; at most one audit child may be active;
- route/investigation stamps, when non-null, are positive and `<=stamp_seq`;
- investigation implies route and `route_stamp < investigation_stamp`; children and converged state
  require investigation;
- terminal status is just a stored fact; no old evidence/convergence inference occurs in D6.

Repository CAS revision remains repository metadata and is not duplicated in the document. Graph,
evidence, attribution, and audit remain append-only artifact-plane facts and are not duplicated in
operational state. No phase, obligation contract pointer/revision, reservation entity/sequence,
audit ticket/nonce, composite verdict, `is_legacy`, migration marker, or host profile is persisted.

## 5. Identity classification before decode

`classify_and_decode(raw)` accepts only an object with exact `empirica/v2` and current state-schema
identity that passes the closed schema and procedural invariants. Every other value is one
`current_corrupt` classification; only `valid` carries an immutable state whose `encode()`
reproduces the exact document. Classification never selects artifacts, defaults fields, migrates,
infers terminality, evaluates evidence, or reuses rejected fields.

Every rejected aggregate maps to sole `run.corrupt` with the fixed safe goal
`Unsupported run state.`, canonical contract/profile facts, and empty projected operational facts.
No rejected state is rewritten or repaired.

## 6. Strict request protocol

Runtime loads accepted v2 request/response schemas once through one schema utility. The frozen
internal protocol seam is `dispatch_request(raw, handler)`: it validates the raw value, calls
`handler` only with a valid discriminated envelope, validates the handler response, and applies the
response fallback. This is the service's only public-request gateway and permits deterministic
fallback testing without a test-only production hook. Request behavior:

- exact top-level protocol required;
- every closed object rejects unknown/missing/wrong fields before dispatch;
- invalid wire—including null/empty/v1/future protocol, unknown command/action, extra top-level,
  command, or action field—returns exact Fault `invalid_request`, `fail_direction=closed`;
- Fault envelope always speaks v2 and uses the supplied valid nonempty request ID when safe,
  otherwise deterministic `invalid-request`;
- response is validated before return; an internally malformed handler response becomes exact
  schema-valid `unavailable` Fault/closed (the accepted D2 code), without recursive validation loops;
- trusted payload shape never grants capability admission.

No handwritten duplicate command/action/status/reason vocabulary: schemas and PublicContract registry
are loaded as SSOT. Python code dispatches handler names only after validated discrimination.

## 7. Minimal v2 service at D6

`application.v2.compose(workspace,harness,runs,artifacts,host,profile_id,limits,clock)` returns one
service exposing the methods required by `LiveDriver`:

```text
dispatch, reload, compact, operational_state,
trusted_child_event, trusted_evidence_leaf, trusted_audit_verdict, trusted_attribution
```

D6 implements no normal public RunView projector. Valid normal operations whose owner behavior is not
yet present return exact schema-valid `unsupported` Fault/closed. Implemented behavior is limited to:

- strict request validation and dispatch discrimination;
- strict state classification/codec;
- old/current-corrupt state-bearing reads (`GetRun`, `RestoreRun`, and `EvaluateRun`) returning the
  failure-safe Block projection above;
- optional exact GetContract target materialization only by calling the one accepted D2 schema-only
  registry materializer; otherwise GetContract is unsupported.

`StartRun`, valid-current GetRun/RestoreRun, ResolveRun, ObserveAction, EvaluateRun, GetArgument, and
private ingress return exact `unsupported`/capability-closed until their owner stages. D6 does not
choose empty obligations/freshness/audit semantics for normal runs.

`application.v2.compose(...)` still returns the service surface required by LiveDriver. `reload`
returns a distinct new shell over the identical port objects. `compact` returns exactly
`{"status":"unsupported"}` and defines no public subset. `operational_state()` returns exactly `{}`.
Trusted methods validate shape/capability and fail closed; they do not implement D7–D9 policy.

## 8. Host cutover

- `adapters/bridge.py` composes only `application.v2`; no `EmpiricaService` v1 import or fallback.
- All Claude/Codex request builders use v2 command/action names and closed fields.
- Pi `contract.ts` becomes the minimal exact v2 host mirror needed by its translators; no v1 type
  alias. Translator requests validate against contract fixtures in tests.
- Host adapters translate/render decisions only. Unsupported D7+ operations fail closed honestly;
  tests assert that rather than retaining v1 behavior.
- No cross-host imports and no adapter adjudication.

## 9. Mandatory deletion ledger

Delete, not deprecate:

- `plugins/empirica/application/service.py`
- `plugins/empirica/application/state.py`
- `plugins/empirica/application/wire.py`
- `plugins/empirica/adapters/claude/migrate_legacy.py`
- v1 schemas under `contracts/empirica/v1/`
- migration Make target/PHONY/help text and activation exception
- migration/default-prefix/legacy-adjudication tests
- `is_legacy` and legacy convergence branch
- every reachable `empirica/v1` literal and compatibility writer/import path
- old action literals/entities: reserve_spawn, void_spawn, audit_ticket, consume_audit_ticket,
  phase, direct evidence approval, frozen composite verdict acceptance
- model-visible nonce/ticket/reservation persistence and default/drop decode helpers.

Do not count tests/docs/schemas/Make lines as runtime repayment. Hardened repository CAS/FSIO,
generation identity, artifact append safety, and fail-closed old-state recognition are retained.

Final measured runtime requirement uses the validator's current pre-D6 inventory, not a reconstructed
D0+D5 estimate:

```text
D0 baseline                         9458
current pre-D6 effective runtime   10015
required D6 final                    <=9457
required net deletion from current >=558 + any D6 runtime growth after measurement point
```

The historical D5 debit is 445, but the extra current 112 lines are also real and must be repaid.
Use the existing effective-runtime validator inventory and list every added/deleted runtime file and
physical LOC. Moving/renaming/generated/vendor code still counts.

## 10. Red-first acceptance

D6-A tests first and observed red. Required tests:

- raw request null/empty/v1/future/partial protocol;
- unknown/extra top-level, command, action fields;
- every claimed-valid command/action sample validates against request.schema before future production
  is imported; trusted samples come from accepted observe fixtures;
- response self-validation fallback is forced through `dispatch_request` with an injected malformed
  handler and is exact v2 `unavailable`/closed, schema-valid, correlated, and nonrecursive;
- state-schema status and child-state enums mechanically equal PublicContract registry; child purpose
  remains the same nonempty opaque D2 string;
- old states: missing/null/empty/v1/future/unknown/hostile false-converged;
- current corrupt: missing field, extra field, wrong type, unknown status, duplicate child ID,
  invalid counters/stamps/child branch, truncated JSON/non-object;
- exact committed valid-state fixture roundtrip through classification/state.encode with no defaults;
- complete child branch relations, purpose parity, duplicate IDs, counter/stamp bounds, and
  NaN/+Infinity/-Infinity deadlines reject;
- old/current-corrupt GetRun/RestoreRun/EvaluateRun are driven through the composed service and a
  recording repository, compared to exact canonical failure-safe Blocks, contain no injected canary,
  and perform zero writes; valid-current StartRun/GetRun/RestoreRun return exact unsupported/closed;
- compact is exactly `{"status":"unsupported"}`, operational_state is exactly `{}`, and reload is a
  distinct shell with equivalent behavior over the identical recording ports;
- D4 cases 45–47 green individually against real compose seam;
- no migration/write on old state;
- every supported entrypoint contains only v2 literals and reaches same service;
- forbidden v1 symbols/files/actions absent;
- effective runtime <=9457.

No baseline exception list, v1 fixture acceptance, translation shim, migration test, skipped/xfail, or
schema-permissive fallback.

## 11. Verification by slice

D6-A: contract-check/static green; new D6 behavioral tests red for missing production.

D6-B: focused protocol/state tests green; D4 preflight green; D4 cases 45–47 green after case 47's
StartRun setup is removed; other D4 cases fail behaviorally, not at absent seam; ordinary current
suites may remain on old entrypoints until C.

D6-C: `make check`, all host suites green, strict tests green, D4 preflight green, D4 45–47 green,
normal D4 owner failures honestly regrouped (route/investigate cases 2–3 are D7; strict case 47 is
D6), architecture violations reduced including Codex→Claude imports, effective runtime <=9457,
`git diff --check`, empty index. Report exact deletion/debit evidence and residual D7 owners.

## 12. Stop conditions

Stop if:

- preserving a current test requires v1 acceptance/migration/defaulting;
- a v2 handler would need evidence/child/audit/convergence policy owned by D7–D9;
- host cutover cannot fail closed without fabricating semantic behavior;
- the deletion ledger or runtime count cannot meet <=9457;
- public D2 contract fields must change rather than implementation conforming to them.
