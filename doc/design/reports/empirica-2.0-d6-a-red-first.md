# Empirica 2.0 D6-A — strict v2 state schema + red strict tests (final test bug fix)

**Status:** D6-A complete (red-first slice, final test bug fix). Production modules remain
absent; new tests are observed red at missing v2 modules. D6-B implements the production modules to
turn these green.

## 1. Scope

D6-A only, per `doc/design/specs/empirica-2.0-d6-strict-v2-subtraction.md` section 3 (D6-A). No
production runtime edits. This is the final test bug fix.

## 2. Changed files

| File | Change |
|------|-------|
| `contracts/empirica/v2/state.schema.json` | NEW (unchanged this round) — internal persisted v2 state schema. |
| `contracts/empirica/v2/state-fixtures/*.json` | NEW (unchanged this round) — 11 valid/invalid state fixtures. |
| `scripts/validate_contracts.py` | MODIFIED (unchanged this round) — state-schema mirror, child-matrix validator, mutation negatives. |
| `plugins/empirica/tests/test_d6_strict_v2.py` | MODIFIED — `_expected_corrupt_block` now validates `{}` against the `run.corrupt` params schema and sets `reason.parameters={}` (the params field is a JSON Schema, not an instance); retains deep-copied canonical code/actions/sections/message. Added `test_expected_old_and_corrupt_blocks_validate_response_schema` prevalidation test validating both representative expected old and corrupt Blocks against the response schema. |
| `plugins/empirica/tests/v2/test_protocol_host.py` | MODIFIED (unchanged this round) — D4 case 47 opaque probe run ID. |
| `doc/design/reports/empirica-2.0-d4-red-first.md` | MODIFIED (unchanged this round) — owner map cases 2–3 → D7, case 47 → D6. |

## 3. Final test bug fix

**`_expected_corrupt_block` params bug**: `_PUBLIC_CONTRACT["reasons"]["run.corrupt"]["params"]` is
a JSON Schema (`{"type": "object", "additionalProperties": false, "properties": {}}`), not an
instance. The prior code set `reason.parameters` to the schema object itself. Fixed: validate `{}`
against the params schema via `jsonschema.validate`, then set `reason.parameters = {}` — a valid
instance, not the schema. The deep-copied canonical code/actions/sections/message are retained.

**New prevalidation test**: `test_expected_old_and_corrupt_blocks_validate_response_schema`
validates both representative expected old-version and corrupt Blocks against the v2 response
schema (production-independent). Also asserts the corrupt reason parameters is `{}` (valid
instance), not the params schema object.

## 4. Verification

### contract-check (GREEN)

```
make contract-check
==> contracts
ok: 12 schemas, 11 fixtures, 33 v2 fixtures
```

### check-static (GREEN)

```
make check-static
==> static suite ok
```

### lint (GREEN)

```
make lint
All checks passed!
```

### D4 preflight (GREEN)

```
python3 plugins/empirica/tests/v2/__main__.py --preflight
preflight: structural only; 50 test_* methods; ...
```

### D4 conformance (expected RED, unchanged)

```
make empirica-v2-conformance
Ran 50 tests; FAILED (failures=78)
```

### Focused D6 tests (expected RED — exact evidence)

**Root command:**
```
python3 plugins/empirica/tests/test_d6_strict_v2.py
```

**Output:**
```
Ran 43 tests in 0.740s
FAILED (failures=37)
```

- tests collected/executed: **43**
- GREEN (prevalidation): **6**
- RED (at absent modules): **37** — all `V2ModuleAbsent`
- errors: **0**
- skips / expected failures: **0**

## 5. Residual risks / next steps

- D6-B must implement `application.protocol.py`, `application.run_state.py`, `application.v2.py`.
- D6-C (host cutover + deletion + measured subtraction) is out of scope for D6-A.
