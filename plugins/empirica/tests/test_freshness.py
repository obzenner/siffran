#!/usr/bin/env python3
"""Red-first regression suite for the D5 pure freshness core (``core/freshness.py``).

Run: python3 plugins/empirica/tests/test_freshness.py   (stdlib only, no pytest)
Exit 0 = all pass; 1 = at least one failed.

Covers the D5-F proofs that belong to the pure core: strict D2 path validation (1), the five
observation states and digest/content branch invariants (2), sequence-preserving canonical digest
(3), exact observation coverage/order (4), per-head fresh/stale changes including overlaps and
gate-independence (5,6), core-fact immutability (9), exit-code-as-sole-gate-authority (13), the
AST/import no-I/O proof (14), and the historical 2F5 reversal without core I/O (15).
"""
import ast
import dataclasses
import hashlib
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent
PLUGIN = HERE.parent  # plugins/empirica — makes `core` importable as a package
sys.path.insert(0, str(PLUGIN))

from core import freshness as fr  # noqa: E402
from core.freshness import (  # noqa: E402
    ActiveSpikeHead, DigestContractError, ExecutionContractError, ExecutionFacts, FileBinding,
    FileObservation, FreshnessChange, FreshnessEvaluation, FreshnessContractError, Gate,
    ObservationContractError, ObservationState, PathContractError, StaleHead,
    canonical_digest, command_digest, evaluate_freshness, execution_facts, gate_from_exit_code,
    required_paths, validate_digest256, validate_observations,
    validate_relative_posix_path,
)

PRESENT = ObservationState.PRESENT
MISSING = ObservationState.MISSING
UNREADABLE = ObservationState.UNREADABLE
NON_REGULAR = ObservationState.NON_REGULAR
OUTSIDE_WORKSPACE = ObservationState.OUTSIDE_WORKSPACE
ALL_STATES = (PRESENT, MISSING, UNREADABLE, NON_REGULAR, OUTSIDE_WORKSPACE)


def _d(label):
    """A canonical digest256 derived deterministically from ``label`` (valid lowercase hex)."""
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _digest_bytes(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


class _HarnessResult:
    """A duck-typed harness result for the pure ``execution_facts`` (core must not import ports)."""

    def __init__(self, exit_code, result_digest, snapshot_digest):
        self.exit_code = exit_code
        self.result_digest = result_digest
        self.snapshot_digest = snapshot_digest


def _head(artifact_char="h", bindings=()):
    return ActiveSpikeHead(_d(artifact_char), "claim-1", "hreq-1", tuple(bindings))


# --- proof 1: strict already-normalized D2 paths -------------------------------

class D2PathValidation(unittest.TestCase):
    def test_already_normalized_accepted_unchanged(self):
        for p in ("a", "a/b", "src/mod.py", "dir/sub/file.txt", "a.b/c-d_e.txt"):
            self.assertEqual(validate_relative_posix_path(p), p)

    def test_rejects_invalid_forms_unchanged(self):
        bad = [
            "", ".", "..", "./a", "a/./b", "a/../b", "../a", "a//b", "a/", "/a",
            "/", "//", "a\\b", "a\0b", "a:b", "dir/a:b", "a/.", "a/..",
        ]
        for p in bad:
            with self.assertRaises(PathContractError):
                validate_relative_posix_path(p)

    def test_non_string_rejected(self):
        for p in (None, 5, b"a/b", ["a", "b"]):
            with self.assertRaises(PathContractError):
                validate_relative_posix_path(p)

    def test_binding_constructor_validates_path_and_digest(self):
        FileBinding("a/b", _d("x"))
        with self.assertRaises(PathContractError):
            FileBinding("a//b", _d("x"))
        with self.assertRaises(PathContractError):
            FileBinding("/a", _d("x"))
        with self.assertRaises(DigestContractError):
            FileBinding("a", "not-a-digest")
        with self.assertRaises(DigestContractError):
            FileBinding("a", "sha256:" + "A" * 64)  # uppercase rejected
        with self.assertRaises(DigestContractError):
            FileBinding("a", "sha256:" + "0" * 63)  # too short


# --- proof 2: five observation states and digest/content branch invariants -----

class ObservationStateInvariants(unittest.TestCase):
    def test_present_requires_digest(self):
        FileObservation("a", PRESENT, _d("a"))
        with self.assertRaises(DigestContractError):
            FileObservation("a", PRESENT, None)
        with self.assertRaises(DigestContractError):
            FileObservation("a", PRESENT, "bad")

    def test_non_present_require_null_digest(self):
        for state in (MISSING, UNREADABLE, NON_REGULAR, OUTSIDE_WORKSPACE):
            FileObservation("a", state, None)
            with self.assertRaises(DigestContractError):
                FileObservation("a", state, _d("a"))

    def test_invalid_state_rejected(self):
        with self.assertRaises((TypeError, FreshnessContractError)):
            FileObservation("a", "present", _d("a"))
        with self.assertRaises((TypeError, FreshnessContractError)):
            FileObservation("a", None, None)

    def test_all_five_states_constructible_and_distinct(self):
        seen = set()
        for state in ALL_STATES:
            if state is PRESENT:
                obs = FileObservation("a", state, _d("a"))
            else:
                obs = FileObservation("a", state, None)
            seen.add(obs.state)
        self.assertEqual(seen, set(ALL_STATES))

    def test_observation_path_validated(self):
        with self.assertRaises(PathContractError):
            FileObservation("a/../b", PRESENT, _d("a"))


# --- proof 3: sequence-preserving canonical JSON digest ------------------------

class CanonicalDigest(unittest.TestCase):
    def test_mapping_permutation_stable(self):
        self.assertEqual(canonical_digest({"a": 1, "b": 2}),
                         canonical_digest({"b": 2, "a": 1}))

    def test_nested_mapping_keys_sorted(self):
        self.assertEqual(canonical_digest({"x": {"b": 1, "a": 2}}),
                         canonical_digest({"x": {"a": 2, "b": 1}}))

    def test_sequence_permutation_changes_identity(self):
        self.assertNotEqual(canonical_digest([1, 2, 3]), canonical_digest([3, 2, 1]))

    def test_sequence_order_preserved(self):
        self.assertEqual(canonical_digest([1, 2, 3]), canonical_digest([1, 2, 3]))
        self.assertEqual(canonical_digest((1, 2)), canonical_digest([1, 2]))

    def test_unordered_containers_rejected(self):
        with self.assertRaises(DigestContractError):
            canonical_digest({1, 2})
        with self.assertRaises(DigestContractError):
            canonical_digest(frozenset({1, 2}))

    def test_unsupported_values_rejected(self):
        with self.assertRaises(DigestContractError):
            canonical_digest(object())
        with self.assertRaises(DigestContractError):
            canonical_digest({"a": {1, 2}})  # set value

    def test_non_string_keys_rejected(self):
        with self.assertRaises(DigestContractError):
            canonical_digest({1: "a"})

    def test_command_digest_exact_bytes_no_trim(self):
        self.assertEqual(command_digest("pytest"),
                         "sha256:" + hashlib.sha256(b"pytest").hexdigest())
        self.assertNotEqual(command_digest("pytest"), command_digest(" pytest"))
        self.assertNotEqual(command_digest("pytest"), command_digest("pytest\n"))

    def test_validate_digest256_lowercase_only(self):
        self.assertEqual(validate_digest256("sha256:" + "0" * 64), "sha256:" + "0" * 64)
        with self.assertRaises(DigestContractError):
            validate_digest256("sha256:" + "F" * 64)
        with self.assertRaises(DigestContractError):
            validate_digest256("sha256:" + "0" * 63)
        with self.assertRaises(DigestContractError):
            validate_digest256("sha1:" + "0" * 64)

    def test_nonfinite_top_level_rejected(self):
        for v in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(DigestContractError):
                canonical_digest(v)

    def test_nonfinite_nested_in_list_rejected(self):
        with self.assertRaises(DigestContractError):
            canonical_digest([1, float("nan"), 3])
        with self.assertRaises(DigestContractError):
            canonical_digest([float("inf")])

    def test_nonfinite_nested_in_dict_rejected(self):
        with self.assertRaises(DigestContractError):
            canonical_digest({"a": float("nan")})
        with self.assertRaises(DigestContractError):
            canonical_digest({"a": [float("-inf")]})


# --- proof 4: exact observation coverage/order ---------------------------------

class ObservationCoverage(unittest.TestCase):
    def _obs(self, path, state=PRESENT, sha=None):
        if sha is not None:
            return FileObservation(path, state, sha)
        if state is PRESENT:
            return FileObservation(path, state, _d(path))
        return FileObservation(path, state, None)

    def test_exact_coverage_ok(self):
        rps = ("a", "b")
        obs = (self._obs("a"), self._obs("b", MISSING, None))
        self.assertEqual(validate_observations(rps, obs), obs)

    def test_missing_raises(self):
        with self.assertRaises(ObservationContractError):
            validate_observations(("a", "b"), (self._obs("a"),))

    def test_extra_raises(self):
        with self.assertRaises(ObservationContractError):
            validate_observations(("a",), (self._obs("a"), self._obs("b")))

    def test_reordered_raises(self):
        with self.assertRaises(ObservationContractError):
            validate_observations(("a", "b"), (self._obs("b"), self._obs("a")))

    def test_path_mismatch_raises(self):
        with self.assertRaises(ObservationContractError):
            validate_observations(("a", "b"), (self._obs("a"), self._obs("c", MISSING, None)))

    def test_malformed_raises(self):
        with self.assertRaises(ObservationContractError):
            validate_observations(("a",), ("not-an-observation",))

    def test_duplicate_raises(self):
        with self.assertRaises(ObservationContractError):
            validate_observations(("a", "b"), (self._obs("a"), self._obs("a")))

    def test_unsorted_requested_paths_rejected(self):
        obs = (self._obs("a", MISSING, None), self._obs("b", MISSING, None))
        with self.assertRaises(ObservationContractError):
            validate_observations(("b", "a"), obs)

    def test_duplicate_requested_paths_rejected(self):
        obs = (self._obs("a", MISSING, None), self._obs("a", MISSING, None))
        with self.assertRaises(ObservationContractError):
            validate_observations(("a", "a"), obs)


# --- proof 5 & 6: freshness evaluation ----------------------------------------

class FreshnessEvaluationTests(unittest.TestCase):
    def test_fresh_match_no_stale_heads(self):
        head = _head(bindings=(FileBinding("a", _d("x")),))
        obs = (FileObservation("a", PRESENT, _d("x")),)
        ev = evaluate_freshness((head,), obs)
        self.assertEqual(ev.stale_heads, ())

    def test_present_digest_mismatch_yields_present_change(self):
        head = _head(bindings=(FileBinding("a", _d("x")),))
        obs = (FileObservation("a", PRESENT, _d("y")),)
        ev = evaluate_freshness((head,), obs)
        self.assertEqual(ev.stale_heads,
                         (StaleHead(_d("h"), (FreshnessChange("a", PRESENT),)),))

    def test_non_present_states_yield_their_exact_change(self):
        for state in (MISSING, UNREADABLE, NON_REGULAR, OUTSIDE_WORKSPACE):
            head = _head(bindings=(FileBinding("a", _d("x")),))
            obs = (FileObservation("a", state, None),)
            ev = evaluate_freshness((head,), obs)
            self.assertEqual(ev.stale_heads,
                             (StaleHead(_d("h"), (FreshnessChange("a", state),)),),
                             f"state {state} should yield its exact change")

    def test_changes_are_hash_free(self):
        head = _head(bindings=(FileBinding("a", _d("x")),))
        obs = (FileObservation("a", PRESENT, _d("y")),)
        ev = evaluate_freshness((head,), obs)
        change = ev.stale_heads[0].changes[0]
        self.assertEqual(change.path, "a")
        self.assertEqual(change.state, PRESENT)
        self.assertFalse(hasattr(change, "sha256"))

    def test_overlapping_paths_use_one_lexical_union(self):
        h1 = _head("1", (FileBinding("a", _d("x")), FileBinding("b", _d("y"))))
        h2 = _head("2", (FileBinding("b", _d("y")), FileBinding("c", _d("z"))))
        obs = (FileObservation("a", PRESENT, _d("x")),
               FileObservation("b", PRESENT, _d("y")),
               FileObservation("c", PRESENT, _d("z")))
        ev = evaluate_freshness((h1, h2), obs)
        self.assertEqual(ev.stale_heads, ())
        self.assertEqual(required_paths((h1, h2)), ("a", "b", "c"))

    def test_every_supplied_head_evaluated_independent_of_gate(self):
        # ActiveSpikeHead carries no gate; every supplied head is evaluated regardless.
        fresh_head = _head("f", (FileBinding("a", _d("x")),))
        stale_head = _head("s", (FileBinding("a", _d("mismatch")),))
        obs = (FileObservation("a", PRESENT, _d("x")),)
        ev = evaluate_freshness((fresh_head, stale_head), obs)
        self.assertEqual(len(ev.stale_heads), 1)
        self.assertEqual(ev.stale_heads[0].artifact_id, _d("s"))
        self.assertFalse(hasattr(fresh_head, "gate"))

    def test_partial_staleness_per_head_in_binding_order(self):
        head = _head(bindings=(FileBinding("a", _d("x")), FileBinding("b", _d("y"))))
        obs = (FileObservation("a", PRESENT, _d("x")),
               FileObservation("b", MISSING, None))
        ev = evaluate_freshness((head,), obs)
        self.assertEqual(ev.stale_heads,
                         (StaleHead(_d("h"), (FreshnessChange("b", MISSING),)),))

    def test_evaluate_requires_full_coverage(self):
        head = _head(bindings=(FileBinding("a", _d("x")), FileBinding("b", _d("y"))))
        with self.assertRaises(ObservationContractError):
            evaluate_freshness((head,), (FileObservation("a", PRESENT, _d("x")),))


# --- proof 9: core-fact immutability ------------------------------------------

class CoreFactsImmutability(unittest.TestCase):
    def test_all_core_facts_frozen(self):
        samples = [
            FileBinding("a", _d("x")),
            FileObservation("a", PRESENT, _d("x")),
            FreshnessChange("a", MISSING),
            _head(bindings=(FileBinding("a", _d("x")),)),
            StaleHead(_d("h"), (FreshnessChange("a", MISSING),)),
            FreshnessEvaluation(()),
            ExecutionFacts((FileBinding("a", _d("x")),), 0, Gate.PASS, _d("r")),
        ]
        for obj in samples:
            first_field = next(iter(obj.__dataclass_fields__))
            with self.assertRaises(dataclasses.FrozenInstanceError):
                setattr(obj, first_field, getattr(obj, first_field))

    def test_binding_unique_by_path_required(self):
        with self.assertRaises(FreshnessContractError):
            ActiveSpikeHead(_d("h"), "c", "r",
                            (FileBinding("a", _d("x")), FileBinding("a", _d("y"))))

    def test_binding_nonempty_required(self):
        with self.assertRaises(FreshnessContractError):
            ActiveSpikeHead(_d("h"), "c", "r", ())

    def test_head_ids_nonempty_opaque(self):
        with self.assertRaises(FreshnessContractError):
            ActiveSpikeHead(_d("h"), "", "r", (FileBinding("a", _d("x")),))
        with self.assertRaises(FreshnessContractError):
            ActiveSpikeHead(_d("h"), "c", "", (FileBinding("a", _d("x")),))
        with self.assertRaises(DigestContractError):
            ActiveSpikeHead("not-a-digest", "c", "r", (FileBinding("a", _d("x")),))


# --- proof 13: exit code is sole gate authority --------------------------------

class GateAuthority(unittest.TestCase):
    def test_zero_pass_nonzero_fail(self):
        self.assertEqual(gate_from_exit_code(0), Gate.PASS)
        for code in (1, -1, 2, 127, 255):
            self.assertEqual(gate_from_exit_code(code), Gate.FAIL)

    def test_bool_rejected(self):
        with self.assertRaises(ExecutionContractError):
            gate_from_exit_code(True)
        with self.assertRaises(ExecutionContractError):
            gate_from_exit_code(False)

    def test_non_int_rejected(self):
        for code in ("0", 0.0, None):
            with self.assertRaises(ExecutionContractError):
                gate_from_exit_code(code)

    def test_execution_facts_derives_gate_from_exit_code_only(self):
        bindings = (FileBinding("a", _d("x")),)
        result = _HarnessResult(0, _d("r"), _d("s"))
        facts = execution_facts(bindings, result)
        self.assertEqual(facts.exit_code, 0)
        self.assertEqual(facts.gate, Gate.PASS)
        self.assertEqual(facts.result_digest, _d("r"))
        self.assertEqual(facts.file_bindings, bindings)

    def test_execution_facts_nonzero_fails(self):
        bindings = (FileBinding("a", _d("x")),)
        facts = execution_facts(bindings, _HarnessResult(3, _d("r"), _d("s")))
        self.assertEqual(facts.gate, Gate.FAIL)
        self.assertEqual(facts.exit_code, 3)

    def test_execution_facts_contain_no_identity(self):
        bindings = (FileBinding("a", _d("x")),)
        facts = execution_facts(bindings, _HarnessResult(0, _d("r"), _d("s")))
        for attr in ("command", "request_id", "claim_id", "artifact_id",
                     "snapshot_digest", "statement_digest"):
            self.assertFalse(hasattr(facts, attr), f"ExecutionFacts must not carry {attr}")

    def test_execution_facts_rejects_bad_result(self):
        bindings = (FileBinding("a", _d("x")),)
        with self.assertRaises(ExecutionContractError):
            execution_facts(bindings, "not-a-result")
        with self.assertRaises(ExecutionContractError):
            execution_facts(bindings, _HarnessResult(True, _d("r"), _d("s")))
        with self.assertRaises(ExecutionContractError):
            execution_facts(bindings, _HarnessResult(0, "bad", _d("s")))

    def test_execution_facts_rejects_empty_bindings(self):
        with self.assertRaises(ExecutionContractError):
            execution_facts((), _HarnessResult(0, _d("r"), _d("s")))

    def test_constructor_gate_must_match_exit_code(self):
        with self.assertRaises(ExecutionContractError):
            ExecutionFacts((FileBinding("a", _d("x")),), 0, Gate.FAIL, _d("r"))
        with self.assertRaises(ExecutionContractError):
            ExecutionFacts((FileBinding("a", _d("x")),), 1, Gate.PASS, _d("r"))

    def test_constructor_duplicate_binding_paths_rejected(self):
        with self.assertRaises(ExecutionContractError):
            ExecutionFacts((FileBinding("a", _d("x")), FileBinding("a", _d("y"))),
                           0, Gate.PASS, _d("r"))

    def test_constructor_bad_result_digest_rejected(self):
        with self.assertRaises(DigestContractError):
            ExecutionFacts((FileBinding("a", _d("x")),), 0, Gate.PASS, "bad")


# --- proof 14: AST/import proof core freshness has no I/O imports/calls --------

class PureCoreNoIO(unittest.TestCase):
    _FORBIDDEN_IO = {
        "os", "sys", "pathlib", "subprocess", "io", "socket", "tempfile", "shutil",
        "glob", "time", "datetime", "asyncio", "threading", "multiprocessing",
        "signal", "ctypes", "mmap", "select", "ssl",
    }
    _FORBIDDEN_LAYERS = {"application", "adapters", "hooks", "vendor"}

    def test_no_io_or_layer_imports(self):
        src = Path(fr.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], self._FORBIDDEN_IO,
                                     f"core freshness must not import {alias.name}")
                    self.assertNotIn(alias.name.split(".")[0], self._FORBIDDEN_LAYERS,
                                     f"core must not import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[0]
                self.assertNotIn(mod, self._FORBIDDEN_IO,
                                 f"core freshness must not import {node.module}")
                self.assertNotIn(mod, self._FORBIDDEN_LAYERS,
                                 f"core must not import {node.module}")

    def test_no_io_calls(self):
        src = Path(fr.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ("open", "input"),
                                 f"core freshness must not call {node.func.id}()")


# --- proof 15: historical 2F5 reversal without core I/O -----------------------

class TwoF5Reversal(unittest.TestCase):
    def test_fixed_sealed_facts_changed_observations_become_stale(self):
        head = ActiveSpikeHead(_d("h"), "c", "r", (FileBinding("a", _d("sealed")),))
        fresh = (FileObservation("a", PRESENT, _d("sealed")),)
        self.assertEqual(evaluate_freshness((head,), fresh).stale_heads, ())
        changed = (FileObservation("a", PRESENT, _d("changed")),)
        ev = evaluate_freshness((head,), changed)
        self.assertEqual(ev.stale_heads,
                         (StaleHead(_d("h"), (FreshnessChange("a", PRESENT),)),))

    def test_identical_observations_remain_identical(self):
        head = ActiveSpikeHead(_d("h"), "c", "r", (FileBinding("a", _d("x")),
                                                    FileBinding("b", _d("y"))))
        obs = (FileObservation("a", PRESENT, _d("x")),
               FileObservation("b", MISSING, None))
        self.assertEqual(evaluate_freshness((head,), obs),
                         evaluate_freshness((head,), obs))

    def test_reversal_is_pure_function_of_supplied_observations(self):
        # Same sealed head, two different supplied observations -> two different verdicts,
        # with no filesystem read: a later eval with the original observations is fresh again.
        head = ActiveSpikeHead(_d("h"), "c", "r", (FileBinding("a", _d("x")),))
        self.assertEqual(
            evaluate_freshness((head,), (FileObservation("a", PRESENT, _d("x")),)).stale_heads,
            ())
        self.assertEqual(
            evaluate_freshness((head,), (FileObservation("a", MISSING, None),)).stale_heads,
            (StaleHead(_d("h"), (FreshnessChange("a", MISSING),)),))
        self.assertEqual(
            evaluate_freshness((head,), (FileObservation("a", PRESENT, _d("x")),)).stale_heads,
            ())


if __name__ == "__main__":
    unittest.main()
