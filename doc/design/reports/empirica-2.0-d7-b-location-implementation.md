# Empirica 2.0 D7-B — location codec implementation report

**Status:** D7-B complete (corrected). Production `application/location.py` codec module rewritten
at 128 physical LOC. All 17 D7-B location tests green (15 original + 2 new regression tests).
`check-core`, `check-static`, and full `make check` green. No staging, commit, push, subagents,
or outside search.

## 1. Scope

D7 spec §8 identity/location codec — three pure functions only:

- `storage_id(raw)` — opaque `s256-<64hex>` storage ID from a raw selector string.
- `encode_handle(RunKey)` — canonical `er2` self-locating token.
- `decode_handle(token)` — `RunKey | None`; never raises for arbitrary input.

No service mapping, Inert, bridge wiring, located adapter/facade, DTO, or protocol — those are
Slice F. Pure stdlib (`base64`, `hashlib`, `hmac`, `json`, `re`) + `core.records.RunKey`. No I/O,
environment access, repository, or adapter imports.

## 2. Files

Modified (production module + tests + spec + report only):

```text
plugins/empirica/application/location.py   # REWRITTEN — 128 physical LOC (was 211)
plugins/empirica/tests/test_d7_location.py  # UPDATED — 300 physical LOC (was 274), 17 tests (was 15)
doc/design/specs/empirica-2.0-d7-application-transactions.md  # UPDATED — §8.1/§8.2 exact decoder
doc/design/reports/empirica-2.0-d7-b-location-implementation.md  (this file)
```

No other production runtime files created or modified.

## 3. API

### `storage_id(raw: str) -> str`

Derives `storage_id = "s256-" + sha256(raw.encode("utf-8")).hexdigest()`. Uses `type(raw) is str`
(exact built-in `str`; subclasses raise `TypeError`). Empty string is valid.

### `encode_handle(key: RunKey) -> str`

Produces `er2:<base64url(payload)>:<base64url(sha256(payload_bytes))>` where payload is canonical
compact sorted-key JSON: `{"g":<int>,"p":"<sid>","s":"<sid>"}`.

- Uses `type(key) is RunKey` (exact type; subclasses raise `TypeError`).
- `_valid_fields` checks exact types: `type(p) is str`, `type(s) is str`, `type(g) is int`,
  `g > 0`, and `s256-[0-9a-f]{64}` grammar for `p`/`s`.
- Invalid fields raise `ValueError`.

### `decode_handle(token: object) -> RunKey | None`

**Never raises** for arbitrary input. Returns `RunKey` on valid token, `None` otherwise.

Decoder steps (spec §8.2, corrected):

1. `type(token) is str` — exact built-in `str` only; subclasses return `None` before any method call.
2. Split on `:` into exactly `["er2", payload_b64, checksum_b64]`; else `None`.
3. Prevalidate strict URL-safe alphabet `[A-Za-z0-9_-]+` for both segments (no `=`, `+`, `/`).
4. Reject impossible base64url lengths (`len % 4 == 1` → `None`).
5. base64url-decode both segments (padding added; exceptions caught → `None`).
6. **Reject nonzero pad-bit aliases:** require `_b64url_nopad(decoded) == original` for BOTH
   payload and checksum segments. This rejects tokens where the last base64 char has nonzero
   padding bits that decode to the same bytes — a canonical segment roundtrip check.
7. Checksum must be exactly 32 bytes (full SHA-256 raw digest).
8. Constant-time compare (`hmac.compare_digest`): `sha256(payload_bytes) == checksum_bytes`.
9. Parse payload JSON; require exactly keys `g`, `p`, `s` with exact types
   (`type(g) is int`, `type(p) is str`, `type(s) is str`; bool/float rejected), `g > 0`,
   `s256-[0-9a-f]{64}` grammar.
10. Strict canonical re-encode: re-encode payload canonically, verify byte-for-byte equality.
11. Return `RunKey(project_id=p, run_id=s, generation=g)`.

## 4. LOC

```text
plugins/empirica/application/location.py   # 128 physical lines (production module)
```

Architecture validator:

```text
effective runtime: 6614 lines across 63 files (baseline 9458 at 4257c8d, maximum 9457, delta -2844)
architecture: ok — no target-state violations
```

Delta from D7-A baseline (6486/62): +128 lines, +1 file for the production module. The 128-line
module replaces the prior 211-line version (−83 LOC reduction). Total runtime 6614 is well
under the 9457 maximum.

## 5. Tests and gates

### D7 location codec tests (17 green)

```text
$ make empirica-d7-location
==> empirica d7 location (D7-B)
...
Ran 17 tests in 0.006s
OK
EXIT: 0 (GREEN)
```

New tests added:
- `test_decode_handle_str_subclass_returns_none` — `ExplodingStr` subclass with `split` that
  raises `AssertionError`; `decode_handle` returns `None` without calling `split`.
- `test_decode_handle_pad_bit_aliases` — two independently generated literal tokens with
  nonzero pad-bit aliases: payload alias (g=10, last char `Q` → `R`) and checksum alias
  (CANON_TOKEN final `w` → `x`). Both decode to the same bytes (checksum verifies) but are
  rejected by the canonical segment roundtrip check.

### check-core (green)

```text
$ make check-core
...
OK (all suites)
EXIT: 0 (GREEN)
```

### check-static (green)

```text
$ make check-static
==> lint
All checks passed!
...
==> static suite ok
EXIT: 0 (GREEN)
```

### Architecture (green)

```text
$ make empirica-architecture-check
effective runtime: 6614 lines across 63 files (maximum 9457, delta -2844)
architecture: ok
EXIT: 0 (GREEN)
```

### Full make check (green)

```text
$ make check
...
All checks passed.
EXIT: 0 (GREEN)
```

## 6. Corrections from prior report

- **LOC:** Production module reduced from 211 to 128 physical LOC (−83). The prior draft report
  incorrectly stated 147; this report corrects the physical provenance.
- **Architecture total:** Prior report stated 6697/63; corrected to 6614/63 after the rewrite.
- **Architecture delta:** Prior report stated −2761; corrected to −2844.
- **Test count:** Prior report stated 15 tests; now 17 tests (2 new regression tests added).
- **Spec §8.2 decoder:** Updated to specify exact built-in `str` requirement, strict b64
  alphabet/length prevalidation, canonical segment roundtrip (pad-bit alias rejection), and
  exact type checks for `g`/`p`/`s` in the decoder — removing the prior contradiction where
  step 2 allowed raw base64url decode without alphabet/length prevalidation.

## 7. Runtime constraints

- No I/O, environment access, repository, or adapter imports — pure stdlib + `core.records.RunKey`.
- No DTO, facade, service, or bridge — codec functions only.
- `decode_handle` never raises for arbitrary input (wrong type, malformed, tampered, legacy,
  str subclass).
- Constant-time checksum comparison via `hmac.compare_digest`.
- Strict canonical re-encode prevents non-canonical JSON from round-tripping.
- Canonical segment roundtrip prevents nonzero pad-bit base64url aliases from round-tripping.

## 8. Git

```text
$ git diff --cached --stat
(empty — nothing staged)

$ git status --short
M Makefile
M doc/design/specs/empirica-2.0-d7-application-transactions.md
M plugins/empirica/tests/test_d7_location.py
?? doc/design/reports/empirica-2.0-d7-b-location-implementation.md
?? plugins/empirica/application/location.py
```

No files staged or committed.

## 9. Forbidden actions

No staging. No commit. No push. No subagents. No outside search. No other production files.
