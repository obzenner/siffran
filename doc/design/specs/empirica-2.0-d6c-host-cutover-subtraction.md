# Empirica 2.0 D6-C — supported-host cutover and v1 subtraction

**Status:** Parent-frozen proposal; requires Terra/Sol acceptance before implementation.

## 1. Outcome

D6-C makes the accepted D6-B strict shell the only runtime reached by Claude, Codex, and Pi, then
deletes v1 service/state/wire/migration surfaces. It does not implement D7–D10 policy.

Final D6-C invariants:

- every supported host emits only schema-valid `empirica/v2` requests;
- every host reaches `application.v2.compose` through the shared bridge;
- removed v1 operations are reported/denied locally or return exact v2 unsupported/closed; no host
  fabricates trusted admission or translates them into semantically different actions;
- no reachable/runtime `empirica/v1` protocol literal, v1 schema, migration path, compatibility writer,
  old service/state/wire, or cross-host adapter import remains;
- old/current-corrupt v2-shell behavior and D4 45–47 remain green;
- normal D7+ operations remain honestly unsupported;
- all ordinary host suites are green for this bounded behavior;
- architecture effective runtime is <=9,457 LOC.

## 2. Non-goals

D6-C does not implement normal StartRun/RunView, evaluation, evidence admission, child lifecycle,
audit, presentation selection, compaction, or final host UX. D7–D10 own those. It does not preserve
public Python/TypeScript builder compatibility, migrate old run files, or retain old tests as frozen
behavior.

A removed operation may not be mapped to another v2 action merely to make a request validate. If no
v2 operation has the same semantics (void_spawn, audit_ticket, consume ticket, phase), the host
adapter returns its native fail-closed/unsupported outcome without dispatching that operation.

## 3. Serial slices and one-writer rule

One GLM writer works serially. Each slice is verified before the next. D6-C is accepted only after C4.

### C1 — application/bridge subtraction

- Change `adapters/bridge.py` to compose only `application.v2`. The D6 shell deliberately receives a
  no-location run port whose `read(opaque_id)` reports absent/unresolved without touching the legacy
  RunKey repository; D7 owns v2 identity/location and reconnects the hardened repository. No v1
  fallback, handle decoder, mapping/index, allocator, or old `EmpiricaService` import.
- `build_service`/`handle` require an explicit exact registry `profile_id`. Missing/unknown profile is
  exact correlated v2 `unavailable`/closed. Public request data and ambient generic defaults cannot
  select profile facts.
- Change `application/__init__.py` to export only current v2 protocol/composition surfaces actually
  required by callers.
- Delete:
  - `application/service.py`;
  - `application/state.py`;
  - `application/wire.py`;
  - `adapters/claude/migrate_legacy.py`;
  - `contracts/empirica/v1/`.
- Remove migration Make target/help/PHONY and activation-validator exception.
- Remove the old monolithic application tests and Make references that test the deleted service;
  preserve D5/D6 and repository/CAS tests. Remove only state-adapter tests whose asserted behavior is
  missing-field/default compatibility; retain repository safety tests.
- Replace/remove core tests whose sole assertion is `is_legacy` approval; do not weaken current safety
  tests.

C1 barrier: strict 44 green, D4 45–47 green, check-core/static green, bridge focused tests prove an
explicit canonical profile composes, unresolved opaque IDs return exact unsupported/closed without
repository access/write, invalid requests do not invoke the run port, and no v1 parser/import is
reached. Index empty. Host suites may remain red until their owner slice.

### C2 — Claude and Codex v2-only cutover

- Claude and Codex transports each pass their own fixed exact registry profile into bridge
  construction (`claude-code@2.1.270`, `codex-cli@0.146.0`). Missing/unknown profile fails closed; it
  never falls back to another host.
- Every retained public request builder produces an instance accepted by
  `contracts/empirica/v2/request.schema.json`; tests schema-validate builder output.
- StartRun: remove actor wire field; nest explicit max values under `budgets`; omit absent values.
- EvaluateRun: omit numeric timestamps; `observed_at` is only string/null.
- route/investigate/restore/resolve/get-argument use their exact v2 shapes.
- dispatch uses only `{kind:"dispatch", target, claim_id?}`; actor/witnessed telemetry is not public
  admission.
- mode becomes exact `configure_run` with modes; phase is removed.
- spawn reservation may use exact `child_reserve` only when purpose, role_profile, and execution are
  real host inputs; otherwise it fails closed locally. No synthetic capability.
- void_spawn, audit_ticket, consume-ticket, and author-submitted trusted actions are removed from the
  public bridge path. Private trusted ingress is not invented in D6-C.
- Codex owns local translators/helpers; all Codex→Claude imports are removed.
- Delete adapter modules/exports that exist solely for legacy migration or old trusted/evidence/ticket
  construction when no retained caller remains.
- Rewrite host tests to assert exact v2 envelopes, schema validity, response correlation, and honest
  unsupported/native fail-closed behavior. Do not merely replace string literals in stale expected
  structures.

C2 barrier: check-claude/check-codex green, strict/D4 45–47/preflight green, production scans show no
v1 literal or forbidden old action in Python host paths, no cross-host imports, index empty.

### C3 — Pi v2-only cutover

- `contract.ts` is a non-authoritative **Pi boundary projection**, not the canonical v2 mirror. Its
  closed outbound `PiRequest` subset contains only commands/actions the retained adapter can emit and
  is mechanically validated as a subset of request.schema. Its inbound response surface keeps the
  stable complete top-level `Allow|Block|Inert|Fault` union needed for safe rendering without
  hand-copying every deep response schema.
- `translate.ts` emits v2; StartRun nests supplied budgets and never manufactures an absent budget
  value; GetRun/EvaluateRun/RestoreRun remain exact.
- `index.ts` uses imported `PROTOCOL`, not protocol string literals. Delete old reserve/ticket/refund
  pipeline and nonce/reservation state. Do not publicly submit trusted audit/evidence/child payloads.
  Until D8/D10 provide native binding, those events produce an honest local unsupported/fail-closed
  notice/gate.
- Pi's entrypoint resolves and passes one exact Pi registry profile to the stdio bridge. At D6 the
  conservative native profile is `pi@0.84.1`; no pi-subagents capability is claimed until its owner
  stage verifies/binds that profile. The shared bridge has no Claude/default fallback.
- Stdio transport requires exact v2 protocol/request correlation and a runtime fail-closed guard for
  the declared top-level response projection. It accepts only canonical `Allow|Block|Inert|Fault`
  discriminants with the minimum safe branch fields: Allow has boolean `converged` plus a run with
  nonempty id and canonical status; Block has a run plus nonempty canonical reasons; Inert has an
  exact canonical reason; Fault has canonical code and fail_direction. Unknown, partial, or malformed
  envelopes/branches become a local closed transport failure and never reach gate/render functions.
  This remains policy-free and does not duplicate deep response-schema validation.
- Rewrite Pi tests/fixtures to schema-valid v2 envelopes and D6 unsupported behavior. Remove tests for
  deleted nonce/ticket/refund semantics; D4 owns their future child-lifecycle replacement.
- Methodologist `methodologist/v1` is unrelated and untouched.

C3 barrier: check-pi green, strict/D4 45–47/preflight green, no Empirica v1/removed actions in Pi
runtime, TypeScript build/test green, and malformed/partial/unknown Allow, Inert, Block, and Fault
responses are proven unable to permit the hard gate. Index empty.

### C4 — repository-wide absence and subtraction

- Remove remaining runtime `empirica/v1` literals and old public action constants/exports in supported
  entrypoint reachability. Do not alter negative old-state/protocol test data, attestation schema
  versions, Git porcelain version, or Methodologist protocol.
- Remove dead imports/files exposed by C1–C3.
- Run the effective-runtime validator and list every runtime file added/deleted with physical LOC.
- Update D6 report with honest normal-D4 grouping. D4 45–47 must remain green; other owner cases may
  fail or error only because their documented D7–D10 behavior is unsupported, never because the v2
  seam is absent or a v1 parser was reached.

C4 barrier: all `make check-*` suites green, D4 preflight green, D4 45–47 green, contract checks green,
runtime <=9,457, `git diff --check`, empty index. Architecture target may retain only violations owned
by later D7–D10; no ARCH-V1-PROTOCOL, ARCH-FORBIDDEN-PATH migration, ARCH-DEP-PY cross-host, or
ARCH-BUDGET violation remains. Contract validation checks Pi outbound boundary members are canonical
request-schema members; complete generated/mechanical TypeScript discriminator equality remains D11.

## 4. Bridge composition boundary

The bridge remains the only Python transport composition root, but D6-C does not own v2 run identity
or storage location. It must not resolve opaque IDs into legacy `RunKey`s, decode existing handles,
create a mapping/index, or recreate `wire.py`. Its D6 shell run port reports every opaque real-store ID
unresolved; valid state-bearing commands therefore return exact unsupported/closed without repository
I/O. The already-frozen direct v2 run-port seam and D4 fakes continue to prove old/current-corrupt
classification. D7, together with StartRun/ResolveRun and transaction ownership, freezes v2 identity
and reconnects the hardened filesystem repository.

Each host supplies an exact profile at bridge construction, never in a public request: Claude and
Codex pass fixed registry IDs in-process; Pi passes its resolved exact ID in the subprocess environment
or invocation configuration. The generic bridge has no host default. Missing, malformed, or unknown
profile returns correlated v2 unavailable/closed and never projects another host's facts.

Bridge construction/configuration errors return exact v2 `unavailable`/closed. Invalid requests are
handled by `protocol.dispatch_request` and return `invalid_request`/closed. The bridge does not catch
an invalid request and relabel it unavailable.

## 5. Host request conformance

Every retained builder test first validates the produced envelope against the accepted request schema.
A host adapter may emit only:

- StartRun, ResolveRun, GetRun, GetArgument, GetContract, RestoreRun, EvaluateRun;
- author actions graph, research, spike_request, configure_run, route, investigate, dispatch, freeze,
  child_reserve when all real required inputs exist.

Trusted actions in the public schema remain test/transport contract shapes but are not author/adapter
admission. D6-C hosts do not possess or synthesize capability refs and must not send them through
public dispatch.

## 6. Mandatory deletion/absence ledger

At minimum delete the four runtime files totaling 2,395 physical LOC at discovery:

```text
application/service.py                    1493
application/state.py                       379
application/wire.py                        218
adapters/claude/migrate_legacy.py          305
```

Also delete v1 contracts and migration lifecycle surfaces (non-runtime, not counted as repayment).
The current measured runtime after D6-B is 10,378 LOC across 74 files. D6-C final is <=9,457; therefore
net runtime deletion from the current measurement must be at least 921 LOC after all C additions.
Deleting the four mandatory files provides 2,395 gross LOC and a 1,474-LOC replacement/dead-code
margin. Tests/docs/schemas/Make do not count as runtime repayment; moved/generated/vendor runtime does.

## 7. Test disposition rules

- Delete tests only when their production subject is deleted or their sole assertion is forbidden
  compatibility behavior.
- Replace host tests with exact v2 schema-valid translation/unsupported tests before deleting old
  expectations.
- Do not weaken repository atomicity/CAS/symlink/corruption tests, D5 freshness/snapshot tests, D6
  strict codec tests, or D4 conformance.
- No skip/xfail/baseline exception and no v1 fixture accepted as current behavior.
- Negative v1 wire/state examples remain allowed in tests/contracts because strict refusal depends on
  them; absence scans are runtime-scoped.

## 8. Stop conditions

Stop and return to parent if:

- a host operation needs D7 evaluation, D8 child admission, D9 projection, or D10 native capability
  policy to produce a truthful result;
- preserving a host test requires a removed v1 action/field or fabricated trusted capability;
- bridge cutover attempts to locate/decode v1 handles or invent a v2 mapping/index before D7;
- any slice adds a second protocol/state/projector model;
- final runtime cannot reach <=9,457 after exact inventory.
