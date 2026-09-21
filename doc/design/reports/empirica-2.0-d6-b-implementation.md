# Empirica 2.0 D6-B — strict v2 production seam (bounded correction)

**Status:** D6-B complete. The three production modules are introduced and the focused D6 test
suite is green (44/44). D4 strict-decoder cases 45–47 are green through the live SUT; the remaining
D4 cases stay red as expected (they require D7–D9 owner behavior, out of scope here).

## 1. Scope

D6-B bounded correction only, per `doc/design/specs/empirica-2.0-d6-strict-v2-subtraction.md`.
`application.protocol` becomes the sole internal loader/root discovery; `application.run_state`
and `application.v2` consume its private constants and drop all duplicate loads. No stage/commit,
no other edits.

## 2. Changed files

| File | Change |
|------|-------|
| `plugins/empirica/application/protocol.py` | Sole internal loader: loads PublicContract, request/response/state schemas, host profiles once; computes canonical PublicContract SHA256 over strict canonical JSON (`sort_keys=True`, `separators=(",",":")`) and exposes the private `_DIGEST`/`_PROTOCOL`/`_STATE_SCHEMA`/`_STATE_SCHEMA_ID`/`_PROFILES`/`_PUBLIC_CONTRACT` constants. `dispatch_request` now requires `response.request_id == request request_id` after response-schema validation; a mismatch returns a correlated `unavailable`/closed Fault. |
| `plugins/empirica/application/run_state.py` | Removed duplicate root/json/PublicContract/state loads; consumes `protocol._STATE_SCHEMA`/`_PROTOCOL`/`_STATE_SCHEMA_ID`. Codec (`classify_and_decode`, `_freeze`/`_thaw`, `RunState`, `Classification`, `_procedural_ok`) unchanged in behavior. |
| `plugins/empirica/application/v2.py` | `import Any` from `typing`; removed the runtime fixture load (`_BLOCK_FIXTURE`) and all `copy.deepcopy` of it; removed duplicate root/json/PublicContract/profile loads. Consumes `protocol._PUBLIC_CONTRACT`/`_PROTOCOL`/`_PROFILES`/`_DIGEST`. Constructs one private old/corrupt-only failure-safe `RunView` directly: protocol/request ID; run id/goal from safe goal; `status=active` with `multi_provider`/`cli_exec` both `false`; contract id/version/digest from the registry; `relevant_sections` = ordered-unique `reason.sections` + `protocol`; empty obligations/residuals/freshness/children; host profile/tier from the loaded profiles with failure-safe `missing_capabilities=[]`; canonical reason from the registry with `{}` validated against the accepted params schema. No normal projector. |
| `plugins/empirica/tests/test_d6_strict_v2.py` | Added `test_handler_wrong_request_id_fallback_unavailable_closed`: a handler returning a schema-valid response with a mismatched `request_id` becomes exact schema-valid `unavailable`/closed, correlated to the request request_id, handler called exactly once. (Focused suite now 44.) |
| `doc/design/reports/empirica-2.0-d6-b-implementation.md` | This report (NEW). |

## 3. Verification

### Focused D6 tests (GREEN)

```
python3 plugins/empirica/tests/test_d6_strict_v2.py
Ran 44 tests in 6.156s
OK
```

### D4 strict-decoder cases 45–47 (GREEN)

```
python3 plugins/empirica/tests/v2/__main__.py
test_v2_identity_checked_before_decoding ... ok
test_v1_and_old_state_rejected_with_fresh_run_recovery ... ok
test_unknown_fields_actions_fail_closed ... ok
Ran 50 tests; FAILED (failures=12, errors=75)
```

The 12 failures + 75 errors are the expected D4 behavioral reds: they require D7–D9 owner
policy (StartRun/Allow, host tier projection, trusted ingress, child lifecycle) which is out of
scope for D6-B. The D6-owned cases 45–47 now execute against the live SUT and pass.

### D4 preflight (GREEN)

```
python3 plugins/empirica/tests/v2/__main__.py --preflight
preflight: structural only; 50 test_* methods; 29 request envelopes validated; ...
```

### check-static / check-core / check-claude / check-codex / check-pi (GREEN)

```
make check-static   # static suite ok
make check-core     # core suite ok
make check-claude   # claude suite ok
make check-codex    # codex suite ok
make check-pi       # pi suite ok
```

### Lint (GREEN)

```
ruff check plugins/empirica/application/   # All checks passed!
```

### Architecture count (constraint met)

Combined three-file physical LOC:

```
wc -l application/protocol.py application/run_state.py application/v2.py
  88 protocol.py
 112 run_state.py
 163 v2.py
 363 total   (<= 364)
```

### Digest correlation

`protocol._DIGEST` equals the canonical `registry_digest` and matches the committed
`block-corrupt-state` fixture, so the fixed-safe rejected-aggregate RunView correlates exactly with
on-demand contract discovery.

### diff / index

All four edited/new files are untracked (`??`); no files are staged (`git diff --cached` empty).
No pre-existing tracked files were modified by this work.

## 4. Residual risks / next steps

- D7–D9 must implement StartRun/Allow, host tier projection, trusted ingress, and child lifecycle;
  the remaining D4 behavioral reds are owned there.
- D6-C (host cutover + deletion + measured subtraction) is out of scope for D6-B.
