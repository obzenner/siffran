# Empirica 2.0 D5 — red-first correction report (review corrections against revised accepted spec)

**Status:** D5 corrections complete (two localized correction passes). Three production files + two
test files modified; this report created. All red-first regression tests recorded red before
production fixes; all gates green after. Production LOC 445 (≤450 budget). D4 normal still red only at
the absent `application.v2` SUT seam (78 failures, expected on the pre-D6/D7 tree). No staging,
commit, push, or child spawning.

## 1. Files

Modified by D5 corrections (production + tests + this report only):

```text
plugins/empirica/core/freshness.py              # production (257 LOC)
plugins/empirica/application/ports.py            # production (83 LOC)
plugins/empirica/application/observation.py      # production (105 LOC)
plugins/empirica/tests/test_freshness.py         # tests (58 cases)
plugins/empirica/tests/test_observation.py       # tests (50 cases)
doc/design/reports/empirica-2.0-d5-red-first.md  (this file)
```

No service, adapters, v1 wire/state, D4, contracts, manifests, Make, architecture, or other files
were modified by this correction run.

## 2. Original pre-production red evidence (initial writer run 991d8236)

The three D5 production files and two test files did not pre-exist; they were created by the initial
writer run `991d8236`. Before any production code existed, both test suites failed at import time —
the modules themselves were absent. This is the **original pre-production red**, distinct from the
**correction-red** in §3 (which records tests that pass after the first implementation but fail before
a subsequent constructor-strengthening fix).

Attested by initial writer run `991d8236`:

```text
python3 plugins/empirica/tests/test_freshness.py
Traceback (most recent call last):
  ...
ImportError: cannot import name 'freshness' from 'core'
EXIT: 1

python3 plugins/empirica/tests/test_observation.py
Traceback (most recent call last):
  ...
ModuleNotFoundError: No module named 'application.observation'
EXIT: 1
```

These import errors prove the test suites were red before the first production implementation wrote
any code. After the initial implementation, both suites went green. The correction-red below is a
separate, subsequent red phase: new regression tests added against the already-green tree that expose
missing constructor invariants, recorded red before the second fix pass.

## 3. Correction-red evidence (regression tests added, recorded red before fixes)

### 3a. First correction pass — tests in `test_freshness.py` (6 tests) and `test_observation.py` (19 tests)

Recorded red before the first constructor-strengthening fix:

```text
python3 plugins/empirica/tests/test_freshness.py
....FFF.......................FF........F.................
Ran 58 tests in 0.007s
FAILED (failures=6)
EXIT: 1

python3 plugins/empirica/tests/test_observation.py
FFF......FF..............F..FFFFFFFFFF....
Ran 42 tests in 0.003s
FAILED (failures=16)
EXIT: 1
```

These tests cover: nonfinite canonical-JSON rejection, canonical lexical unique observation paths,
gate/exit-code constructor invariants, duplicate binding path rejection, build_execution_snapshot
order preservation, fail-closed shared capture validation, command pre-port validation, and
direct-constructor port-shape invariants for HarnessResult/ObservationSnapshot/ExecutionSnapshot.

### 3b. Second correction pass — 5 new tests in `test_observation.py`

Recorded red before the second constructor-strengthening fix (this run):

| Test | Class | Red cause |
|------|-------|-----------|
| `test_observation_snapshot_rejects_wrong_digest` | PortShape | `ObservationSnapshot` accepted a valid-format digest that was not the canonical digest of observations |
| `test_observation_snapshot_rejects_noncanonical_order` | PortShape | `ObservationSnapshot` accepted observation paths in noncanonical (non-lexical) order |
| `test_observation_snapshot_rejects_duplicate_observation_paths` | PortShape | `ObservationSnapshot` accepted duplicate observation paths |
| `test_execution_snapshot_rejects_wrong_canonical_digest` | PortShape | `ExecutionSnapshot` accepted a valid-format `snapshot_digest` that was not the canonical digest of bindings |
| `test_build_execution_snapshot_rejects_list_dependent_paths` | ExecutionSnapshotFacts | `build_execution_snapshot` accepted a list for `dependent_paths` (tuple required) |

Recorded red:

```text
python3 plugins/empirica/tests/test_observation.py
..........F.......................F....F.FF....
Ran 47 tests in 0.002s
FAILED (failures=5)
EXIT: 1
```

## 4. Production fixes

### `core/freshness.py` (first correction pass)

1. **Nonfinite rejection:** `_canonical_json` checks `math.isfinite(value)` for floats and raises
   `DigestContractError` for NaN/±infinity at every recursion level.
2. **`validate_observations` canonical lexical unique:** validates every `requested_path` via
   `validate_relative_posix_path`, then checks `list(requested_paths) == sorted(set(requested_paths))`
   to reject unsorted or duplicate paths.
3. **`ExecutionFacts` constructor invariants:** `__post_init__` checks unique binding paths and
   `gate == gate_from_exit_code(exit_code)`.

### `application/ports.py` (both correction passes)

4. **`HarnessResult`:** non-boolean int exit_code, digest256 result/snapshot digests.
5. **`ObservationSnapshot` (first pass):** tuple of FileObservation, nonempty string basis_id.
6. **`ObservationSnapshot` (second pass):** validates `paths == sorted(set(paths))` (canonical
   lexical unique) and `digest == canonical_digest([(o.path, o.state.value, o.sha256) for o in
   observations])`. The format-only `validate_digest256(self.digest)` was replaced by the strictly
   stronger canonical-digest equality check.
7. **`ExecutionSnapshot` (first pass):** nonempty tuple, matched captured_bytes length, bytes type,
   content-digest match, unique binding paths.
8. **`ExecutionSnapshot` (second pass):** validates `snapshot_digest == canonical_digest([(b.path,
   b.sha256) for b in file_bindings])`. The format-only `validate_digest256(self.snapshot_digest)`
   was replaced by the strictly stronger canonical-digest equality check.

### `application/observation.py` (both correction passes)

9. **Command validation (first pass):** `execute_spike` validates `command` is a nonempty string
   before any Workspace/Harness call; whitespace preserved byte-for-byte.
10. **Preserve supplied dependent tuple (first pass):** `_validate_dependent_paths` no longer sorts;
    returns the tuple in supplied order.
11. **Reject list (second pass):** `_validate_dependent_paths` now requires `isinstance(tuple)` —
    lists are rejected with `PathContractError("dependent_paths must be a nonempty tuple")`.
12. **Centralize full capture validation (first pass):** `_validate_capture(capture, paths)` is a
    single shared helper used by both `build_observation_snapshot` and `build_execution_snapshot`.
13. **Empty snapshot nonempty basis (first pass):** empty observation snapshot uses
    `_EMPTY_BASIS = canonical_digest(())`.

## 5. Final 3-file LOC debit

```text
plugins/empirica/core/freshness.py              257 LOC
plugins/empirica/application/ports.py             83 LOC
plugins/empirica/application/observation.py      105 LOC
                                              -----
Total                                           445 LOC  (≤450 budget)
```

LOC stayed within budget by reducing blank-line formatting (double-to-single between dataclasses,
import collapsed to single line) only — no behavior removed.

A D5 reduction consolidated content validation into the shared `_validate_capture` helper: it now
performs `_validate_content` for every captured file after the type/path checks. The separate
content-validation loop in `build_observation_snapshot` and the duplicate bytes/digest checks in
`build_execution_snapshot` were removed (execution adds only the state-PRESENT check, then uses the
already-validated bytes). This removed 5 LOC (110 → 105) with no behavior change — three regression
tests (`test_both_paths_reject_content_digest_mismatch`,
`test_both_paths_reject_present_with_null_content`, `test_both_paths_reject_nonpresent_with_content`)
prove the same malformed content is rejected identically by both factories.

## 6. Gates

```text
python3 plugins/empirica/tests/test_freshness.py      Ran 58 tests   OK   exit 0
python3 plugins/empirica/tests/test_observation.py     Ran 50 tests   OK   exit 0
make check-core                                       GREEN          exit 0
make check-static                                     GREEN          exit 0
python3 plugins/empirica/tests/v2/__main__.py --preflight              exit 0
python3 plugins/empirica/tests/v2/__main__.py          Ran 50 tests   FAILED(78) exit 1  (expected RED: v2 SUT seam absent)
ruff check (3 files)                                  All checks passed
git diff --check                                       clean
git diff --cached --name-only                          empty (no staged files)
```

D4 normal remains red only at the absent `application.v2` SUT seam (78 failures), as expected on
the pre-D6/D7 tree. D4 preflight is green (structural only, zero post-binding semantics).

## 7. Forbidden files untouched

No production runtime (service/wire/state), adapters, v1 wire/state, D4 test/v2 files, contracts,
manifests/version, skill/docs prose, Makefile, architecture validator, or quarantined two-fold
files were modified by this correction run. The broader worktree carries modified runtime/contract
files from earlier serial milestones (visible in `git status --short`); this is a D5-correction
attribution, not a whole-worktree statement. No staging, commit, push, or child spawning.
