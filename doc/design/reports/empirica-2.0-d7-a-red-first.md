# Empirica 2.0 D7-A — red-first location tests report

**Status:** D7-A complete. Red-first tests for the absent `application.location` codec functions.
No production code written. Spec §8 API contract updated. Architecture remains 6,486 LOC across
62 files. No staging, commit, push, subagents, or outside search.

## 1. Spec fix

The D7 spec §8 lacked the exact codec API contract for `storage_id`, `encode_handle`, and
`decode_handle`. Added an **API contract (codec)** subsection to §8.1:

- `storage_id(raw)` — `raw` must be a `str`; non-string raises `TypeError`. Empty string is valid
  and returns the formula literal (`s256-e3b0c44…`).
- `encode_handle(RunKey)` — argument must be a `RunKey`; non-`RunKey` raises `TypeError`. Invalid
  `p`/`s`/`g` (non-string `s256-<64hex>`, bool, zero, or non-positive generation) raises
  `ValueError`.
- `decode_handle(token)` — returns `RunKey | None`; never raises for representative arbitrary
  inputs (malformed, wrong type, tampered, legacy, unknown prefix).

Also updated §13 module table and §15 slice table: `application/location.py` owns codec
functions only (`storage_id`, `encode_handle`, `decode_handle`); facade protocol/adapter is
Slice F. Slice B composes the green target into `check-core` (no expected-RED wording).

## 2. Files

Created/modified (test + report + spec only):

```text
plugins/empirica/tests/test_d7_location.py              # rewritten red-first test suite (RED on pre-D7-B tree)
doc/design/reports/empirica-2.0-d7-a-red-first.md         (this file)
doc/design/specs/empirica-2.0-d7-application-transactions.md  # §8 API contract + §13 module + §15 slice wording
```

The Makefile `empirica-d7-location` target is unchanged from the prior D7-A pass.

No production runtime files created or modified.

## 3. Make target

```make
empirica-d7-location  ## run Empirica 2.0 D7-A location red-first tests (expected RED)
```

Intentionally NOT composed into `check-core`, `check-static`, `check`, or `check-ci`: the
production module is absent, so the suite fails at import time and the target is nonzero. D7-B
implements the module and composes the green target into `check-core`.

## 4. Red evidence (expected RED)

```text
$ make empirica-d7-location
==> empirica d7 location (D7-A, expected RED)
Traceback (most recent call last):
  File "/private/tmp/empirica-twofold-fix/plugins/empirica/tests/test_d7_location.py", line 43, in <module>  # line unchanged
    import application.location as location  # noqa: E402,F401  -- ModuleNotFoundError on pre-D7-B
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
ModuleNotFoundError: No module named 'application.location'
make: *** [empirica-d7-location] Error 1
```

The suite fails at module import with exactly `ModuleNotFoundError: No module named
'application.location'`. No test method executes — the entire suite is red at the import seam.

## 5. Test suite design

The test file is 274 physical LOC with 15 test methods, table-driven via `subTest`.

### Literal vectors (independently generated, then pasted)

```text
storage_id("alpha")          = s256-8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8
storage_id("beta")           = s256-f44e64e75f3948e9f73f8dfa94721c4ce8cbb4f265c4790c702b2d41cfbf2753
storage_id("ünïcödé-pröject") = s256-f41a55d1f223df4614cd4b121a8e05d538cae96a0705ed42f7bd17bc942a344e
storage_id("")               = s256-e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855

CANON_KEY = RunKey(SID_ALPHA, SID_BETA, 7)
CANON_TOKEN = er2:eyJnIjo3LCJwIjoiczI1Ni04ZWQzZjZhZDY4NWI5NTllYWQ3MDIyNTE4ZTFhZjc2Y2Q4MTZmOGU4ZWM3Y2NkZGExZWQ0MDE4ZThmMjIyM2Y4IiwicyI6InMyNTYtZjQ0ZTY0ZTc1ZjM5NDhlOWY3M2Y4ZGZhOTQ3MjFjNGNlOGNiYjRmMjY1YzQ3OTBjNzAyYjJkNDFjZmJmMjc1MyJ9:T8R-dXdb4f9W3jdnpp1H1OS4yXgXkyLxd7rt97zj0ww
```

Valid-checksum noncanonical/schema-invalid tokens (all in `BAD_TOKENS` table):

| Label            | Issue                                |
|------------------|-------------------------------------|
| noncanonical_json | whitespace + unsorted keys           |
| wrong_keys        | `project`/`session` instead of `p`/`s` |
| extra_keys        | extra `x` key                        |
| bad_p             | `p` not `s256-<64hex>`               |
| bad_s             | `s` not `s256-<64hex>`               |
| g_zero            | generation = 0                       |
| g_negative        | generation = -1                      |
| g_bool            | generation = `true` (JSON bool)     |
| g_float           | generation = 1.0                     |

### Narrow helpers (no token signer/encoder oracle)

- `_b64decode(s)` — base64url-decodes for structural assertions only.
- `_flip_part(token, part, idx, mask)` — mutates one byte of the fixed literal token's payload or checksum.
- `_inject(token, part, char)` — replaces one character of the payload or checksum with a given char.
- `_insert_edge(token, part, char)` — inserts char as prefix and suffix of the payload or checksum (preserves valid field, no replacement).

No `_expected_storage_id`, `_expected_payload`, or `_expected_token` reference encoder remains.

### Test methods (15)

1. `test_storage_id_formula_literals` — storage_id matches formula for alpha/beta/unicode/empty.
2. `test_storage_id_non_string_raises_type_error` — None/int/float/list/dict/bytes/bool → TypeError.
3. `test_storage_id_empty_valid` — empty string valid, equals formula literal.
4. `test_storage_id_distinct_pair` — two distinct selectors → two distinct IDs (no 100-loop).
5. `test_storage_id_path_opacity` — no `/` or `..` in output.
6. `test_encode_handle_exact_token` — exact pasted canonical token equality.
7. `test_encode_handle_canonical_structure` — sorted compact payload, no padding, full checksum.
8. `test_encode_handle_non_runkey_raises_type_error` — non-RunKey → TypeError.
9. `test_encode_handle_invalid_fields_raise_value_error` — bool p/s/g, zero/neg g, numeric p, list s, invalid lowercase grammar p, uppercase s, empty p/s, float g → ValueError.
10. `test_decode_handle_roundtrip` — canon/unicode/large_gen roundtrip + safe segments.
11. `test_decode_handle_never_raises` — representative malformed/wrong-type inputs → None.
12. `test_decode_handle_tamper` — flipped payload/checksum byte → None.
13. `test_decode_handle_schema_negatives` — BAD_TOKENS table → None.
14. `test_decode_handle_strict_base64url` — injected `!`/`+`/`/`/`=`, `!` prefix/suffix on payload and checksum (INSERT not replace), impossible length → None.
15. `test_decode_handle_legacy_unknown_prefix` — unknown prefix, legacy v1, extra part → None.

### Not in scope

No service mapping, Inert, bridge wiring, or located adapter/facade protocol tests — those are
Slice F.

## 6. Baseline ordinary suites green

```text
$ make check-core
...
Ran 20 tests in 100.992s
OK
...
Ran 32 tests in 0.010s
OK
EXIT: 0 (GREEN)

$ make check-static
...
==> static suite ok
EXIT: 0 (GREEN)
```

## 7. Architecture

```text
$ make empirica-architecture-check
==> empirica architecture
effective runtime: 6486 lines across 62 files (baseline 9458 at 4257c8d, maximum 9457, delta -2972)
architecture: ok — no target-state violations
```

Architecture remains 6,486 LOC across 62 files — unchanged. No production files added or removed.

## 8. Lint

```text
$ make lint
==> lint
All checks passed!
EXIT: 0 (GREEN)
```

## 9. Git

```text
$ git diff --cached --stat
(empty — nothing staged)

$ git status --short
M doc/design/specs/empirica-2.0-d7-application-transactions.md
?? doc/design/reports/empirica-2.0-d7-a-red-first.md
?? plugins/empirica/tests/test_d7_location.py
```

No files staged or committed.

## 10. Forbidden actions

No production runtime created or modified. No staging. No commit. No push. No subagents. No
outside search.
