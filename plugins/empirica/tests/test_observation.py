#!/usr/bin/env python3
"""Red-first regression suite for the D5 imperative shell (``application/observation.py`` +
``application/ports.py``).

Run: python3 plugins/empirica/tests/test_observation.py   (stdlib only, no pytest)
Exit 0 = all pass; 1 = at least one failed.

Covers the D5-F proofs that belong to the application imperative shell: empty observation snapshot
makes zero Workspace calls and nonempty makes exactly one complete lexical call (7); workspace
exception / malformed capture / content-digest mismatch fail closed with no partial snapshot (8);
snapshot immutability (9); execution snapshot carries exact immutable captured bytes/bindings and
the harness is called exactly once with no Workspace argument (10); a workspace mutation after
observe is invisible to the harness which sees the original captured bytes and echoed digest, and a
harness requiring ambient workspace is unavailable (11); non-present execution capture prevents the
harness call, and harness exception / snapshot-echo mismatch fail closed (12). Fakes record calls,
captured bytes, and results only and contain no freshness/adjudication policy.
"""
import dataclasses
import hashlib
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent
PLUGIN = HERE.parent  # plugins/empirica — makes `application`/`core` importable
sys.path.insert(0, str(PLUGIN))

from application.observation import (  # noqa: E402
    HarnessContractError, HarnessUnavailable, ObservationUnavailable,
    build_execution_snapshot, build_observation_snapshot, execute_spike,
)
from application.ports import (  # noqa: E402
    CapturedFile, ExecutionSnapshot, HarnessResult, ObservationSnapshot,
    SpikeHarness, Workspace, WorkspaceCapture,
)
from core.freshness import (  # noqa: E402
    ActiveSpikeHead, ExecutionFacts, FileBinding, FileObservation, Gate,
    ObservationState, PathContractError, canonical_digest,
)

PRESENT = ObservationState.PRESENT
MISSING = ObservationState.MISSING


def _d(label):
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _digest_bytes(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _present_capture(path, content, basis="basis-1"):
    """A well-formed present capture: content's SHA-256 equals the observation digest."""
    sha = _digest_bytes(content)
    return CapturedFile(FileObservation(path, PRESENT, sha), content)


def _state_capture(path, state):
    return CapturedFile(FileObservation(path, state, None), None)


class FakeWorkspace:
    """Records observe calls and returns configured captures. No freshness/adjudication policy."""

    def __init__(self, captures=None, *, basis="basis-1", error=None, custom_capture=None):
        self._captures = dict(captures or {})
        self._basis = basis
        self._error = error
        self._custom = custom_capture
        self.observe_calls = []

    def observe(self, paths):
        self.observe_calls.append(tuple(paths))
        if self._error is not None:
            raise self._error
        if self._custom is not None:
            return self._custom
        files = tuple(
            self._captures.get(p, _state_capture(p, MISSING)) for p in paths
        )
        return WorkspaceCapture(self._basis, files)


class FakeSpikeHarness:
    """Records invocations (command + snapshot) and returns a sealed result. No policy."""

    def __init__(self, *, exit_code=0, result_digest=None, raise_exc=None,
                 echo_digest=None, require_workspace=False):
        self._exit = exit_code
        self._result = result_digest or _d("r")
        self._raise = raise_exc
        self._echo = echo_digest
        self._require_workspace = require_workspace
        self.invocations = []

    def run(self, command, snapshot):
        self.invocations.append({"command": command, "snapshot": snapshot})
        if self._raise is not None:
            raise self._raise
        if self._require_workspace:
            raise RuntimeError("harness requires an ambient workspace; refusing snapshot-only input")
        echo = self._echo if self._echo is not None else snapshot.snapshot_digest
        return HarnessResult(self._exit, self._result, echo)


class MutatingHarness:
    """Mutates the ambient workspace when run is called (post-observe) and asserts the snapshot
    still carries the ORIGINAL captured bytes — proving the harness sees the snapshot, not the
    live workspace."""

    def __init__(self, workspace, original, *, exit_code=0):
        self.workspace = workspace
        self.original = original
        self._exit = exit_code
        self.invocations = []
        self.saw_bytes = None

    def run(self, command, snapshot):
        self.invocations.append(snapshot)
        # Ambient mutation AFTER observe returned, while the harness runs.
        self.workspace._captures = {
            "a": _present_capture("a", b"mutated", basis="basis-2")
        }
        self.saw_bytes = snapshot.captured_bytes
        return HarnessResult(self._exit, _d("r"), snapshot.snapshot_digest)


def _present_ws(path, content):
    return FakeWorkspace({path: _present_capture(path, content)})


# --- proof 7: empty zero calls; nonempty exactly one complete lexical call -----

class ObservationSnapshotCalls(unittest.TestCase):
    def test_empty_snapshot_zero_workspace_calls(self):
        ws = FakeWorkspace()
        snap = build_observation_snapshot((), ws)
        self.assertEqual(ws.observe_calls, [])
        self.assertEqual(snap.observations, ())
        self.assertNotEqual(snap.basis_id, "")
        self.assertEqual(snap.digest, canonical_digest(()))
        self.assertIsInstance(snap, ObservationSnapshot)

    def test_nonempty_snapshot_one_complete_lexical_call(self):
        head = ActiveSpikeHead(
            _d("h"), "c", "r",
            (FileBinding("b", _digest_bytes(b"bb")), FileBinding("a", _digest_bytes(b"aa"))))
        ws = FakeWorkspace({
            "a": _present_capture("a", b"aa"),
            "b": _present_capture("b", b"bb"),
        })
        snap = build_observation_snapshot((head,), ws)
        self.assertEqual(len(ws.observe_calls), 1)
        self.assertEqual(ws.observe_calls[0], ("a", "b"))
        self.assertEqual(len(snap.observations), 2)
        self.assertEqual([o.path for o in snap.observations], ["a", "b"])
        self.assertNotEqual(snap.basis_id, "")
        # private contents discarded: only observations returned
        for o in snap.observations:
            self.assertIsInstance(o, FileObservation)
            self.assertFalse(hasattr(o, "content"))

    def test_overlapping_heads_one_lexical_call(self):
        h1 = ActiveSpikeHead(_d("1"), "c", "r", (FileBinding("a", _d("x")),))
        h2 = ActiveSpikeHead(_d("2"), "c", "r",
                            (FileBinding("a", _d("x")), FileBinding("b", _d("y"))))
        ws = FakeWorkspace({
            "a": _present_capture("a", b"aa"),
            "b": _present_capture("b", b"bb"),
        })
        build_observation_snapshot((h1, h2), ws)
        self.assertEqual(len(ws.observe_calls), 1)
        self.assertEqual(ws.observe_calls[0], ("a", "b"))


# --- proof 8: workspace exception / malformed / content mismatch fail closed ----

class ObservationFailClosed(unittest.TestCase):
    def _head(self, path="a", sha=None):
        return ActiveSpikeHead(_d("h"), "c", "r", (FileBinding(path, sha or _d("x")),))

    def test_workspace_exception_raises_unavailable(self):
        ws = FakeWorkspace(error=RuntimeError("filesystem down"))
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((self._head(),), ws)

    def test_empty_basis_raises_unavailable(self):
        bad = WorkspaceCapture("", (_present_capture("a", b"aa"),))
        ws = FakeWorkspace(custom_capture=bad)
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((self._head(),), ws)

    def test_wrong_coverage_raises_unavailable(self):
        bad = WorkspaceCapture("b1", ())
        ws = FakeWorkspace(custom_capture=bad)
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((self._head(),), ws)

    def test_path_mismatch_raises_unavailable(self):
        bad = WorkspaceCapture("b1", (_present_capture("z", b"zz"),))
        ws = FakeWorkspace(custom_capture=bad)
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((self._head(path="a"),), ws)

    def test_content_digest_mismatch_raises_unavailable(self):
        bad = CapturedFile(FileObservation("a", PRESENT, _d("0")), b"actual")
        ws = FakeWorkspace(custom_capture=WorkspaceCapture("b1", (bad,)))
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((self._head(sha=_d("0")),), ws)

    def test_present_with_null_content_raises_unavailable(self):
        bad = CapturedFile(FileObservation("a", PRESENT, _d("x")), None)
        ws = FakeWorkspace(custom_capture=WorkspaceCapture("b1", (bad,)))
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((self._head(),), ws)

    def test_nonpresent_with_content_raises_unavailable(self):
        bad = CapturedFile(FileObservation("a", MISSING, None), b"leaked")
        ws = FakeWorkspace(custom_capture=WorkspaceCapture("b1", (bad,)))
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((self._head(),), ws)

    def test_both_paths_reject_bad_basis_id(self):
        bad = WorkspaceCapture("", (_present_capture("a", b"aa"),))
        head = ActiveSpikeHead(_d("h"), "c", "r", (FileBinding("a", _d("x")),))
        ws1 = FakeWorkspace(custom_capture=bad)
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((head,), ws1)
        ws2 = FakeWorkspace(custom_capture=bad)
        with self.assertRaises(ObservationUnavailable):
            build_execution_snapshot(("a",), ws2)

    def test_both_paths_reject_wrong_coverage(self):
        bad = WorkspaceCapture("b1", ())
        head = ActiveSpikeHead(_d("h"), "c", "r", (FileBinding("a", _d("x")),))
        ws1 = FakeWorkspace(custom_capture=bad)
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((head,), ws1)
        ws2 = FakeWorkspace(custom_capture=bad)
        with self.assertRaises(ObservationUnavailable):
            build_execution_snapshot(("a",), ws2)

    def test_both_paths_reject_non_workspacecapture(self):
        head = ActiveSpikeHead(_d("h"), "c", "r", (FileBinding("a", _d("x")),))
        ws1 = FakeWorkspace(custom_capture="not-a-capture")
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((head,), ws1)
        ws2 = FakeWorkspace(custom_capture="not-a-capture")
        with self.assertRaises(ObservationUnavailable):
            build_execution_snapshot(("a",), ws2)

    def test_both_paths_reject_content_digest_mismatch(self):
        # present capture whose content digest does not match the observation sha256
        bad = CapturedFile(FileObservation("a", PRESENT, _d("0")), b"actual")
        cap = WorkspaceCapture("b1", (bad,))
        head = ActiveSpikeHead(_d("h"), "c", "r", (FileBinding("a", _d("x")),))
        ws1 = FakeWorkspace(custom_capture=cap)
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((head,), ws1)
        ws2 = FakeWorkspace(custom_capture=cap)
        with self.assertRaises(ObservationUnavailable):
            build_execution_snapshot(("a",), ws2)

    def test_both_paths_reject_present_with_null_content(self):
        # present capture that carries no bytes
        bad = CapturedFile(FileObservation("a", PRESENT, _d("x")), None)
        cap = WorkspaceCapture("b1", (bad,))
        head = ActiveSpikeHead(_d("h"), "c", "r", (FileBinding("a", _d("x")),))
        ws1 = FakeWorkspace(custom_capture=cap)
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((head,), ws1)
        ws2 = FakeWorkspace(custom_capture=cap)
        with self.assertRaises(ObservationUnavailable):
            build_execution_snapshot(("a",), ws2)

    def test_both_paths_reject_nonpresent_with_content(self):
        # non-present capture that leaks bytes
        bad = CapturedFile(FileObservation("a", MISSING, None), b"leaked")
        cap = WorkspaceCapture("b1", (bad,))
        head = ActiveSpikeHead(_d("h"), "c", "r", (FileBinding("a", _d("x")),))
        ws1 = FakeWorkspace(custom_capture=cap)
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((head,), ws1)
        ws2 = FakeWorkspace(custom_capture=cap)
        with self.assertRaises(ObservationUnavailable):
            build_execution_snapshot(("a",), ws2)


# --- proof 9: snapshot immutability (application layer) -----------------------

class SnapshotImmutability(unittest.TestCase):
    def test_observation_snapshot_frozen(self):
        ws = FakeWorkspace({"a": _present_capture("a", b"aa")})
        head = ActiveSpikeHead(_d("h"), "c", "r", (FileBinding("a", _digest_bytes(b"aa")),))
        snap = build_observation_snapshot((head,), ws)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snap.basis_id = "x"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snap.observations = ()

    def test_execution_snapshot_frozen(self):
        ws = FakeWorkspace({"a": _present_capture("a", b"aa")})
        snap = build_execution_snapshot(("a",), ws)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snap.snapshot_digest = "x"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snap.captured_bytes = ()

    def test_port_facts_frozen(self):
        for obj in (CapturedFile(FileObservation("a", MISSING, None), None),
                   WorkspaceCapture("b", (_present_capture("a", b"aa"),)),
                   HarnessResult(0, _d("r"), _d("s"))):
            with self.assertRaises(dataclasses.FrozenInstanceError):
                setattr(obj, next(iter(obj.__dataclass_fields__)), None)


# --- proof 10: execution snapshot exact bytes/bindings; harness once, no workspace

class ExecutionSnapshotFacts(unittest.TestCase):
    def test_build_execution_snapshot_preserves_supplied_order(self):
        ws = FakeWorkspace({
            "a": _present_capture("a", b"AAA"),
            "b": _present_capture("b", b"BBB"),
        })
        snap = build_execution_snapshot(("b", "a"), ws)
        self.assertEqual(len(ws.observe_calls), 1)
        self.assertEqual(ws.observe_calls[0], ("b", "a"))  # supplied order preserved
        self.assertEqual(snap.file_bindings,
                         (FileBinding("b", _digest_bytes(b"BBB")),
                          FileBinding("a", _digest_bytes(b"AAA"))))
        self.assertEqual(snap.captured_bytes, (b"BBB", b"AAA"))
        self.assertEqual(snap.snapshot_digest,
                         canonical_digest([("b", _digest_bytes(b"BBB")),
                                           ("a", _digest_bytes(b"AAA"))]))
        self.assertIsInstance(snap, ExecutionSnapshot)

    def test_build_execution_snapshot_three_paths_preserve_order(self):
        ws = FakeWorkspace({
            "z": _present_capture("z", b"ZZ"),
            "a": _present_capture("a", b"AA"),
            "m": _present_capture("m", b"MM"),
        })
        snap = build_execution_snapshot(("z", "a", "m"), ws)
        self.assertEqual([b.path for b in snap.file_bindings], ["z", "a", "m"])
        self.assertEqual(snap.captured_bytes, (b"ZZ", b"AA", b"MM"))

    def test_execute_spike_harness_called_once_no_workspace_arg(self):
        ws = _present_ws("a", b"AA")
        harness = FakeSpikeHarness(exit_code=0)
        facts = execute_spike("pytest", ("a",), ws, harness)
        self.assertEqual(len(harness.invocations), 1)
        self.assertEqual(len(ws.observe_calls), 1)
        inv = harness.invocations[0]
        self.assertEqual(inv["command"], "pytest")
        self.assertIsInstance(inv["snapshot"], ExecutionSnapshot)
        # harness received the snapshot's bytes, not a live workspace handle
        self.assertEqual(inv["snapshot"].captured_bytes, (b"AA",))
        self.assertIsInstance(facts, ExecutionFacts)
        self.assertEqual(facts.exit_code, 0)
        self.assertEqual(facts.gate, Gate.PASS)

    def test_build_execution_snapshot_rejects_list_dependent_paths(self):
        ws = FakeWorkspace({"a": _present_capture("a", b"aa")})
        with self.assertRaises(PathContractError):
            build_execution_snapshot(["a"], ws)  # list rejected; tuple required

    def test_execute_spike_rejects_invalid_dependent_paths(self):
        ws = FakeWorkspace()
        with self.assertRaises(PathContractError):
            build_execution_snapshot(("a/../b",), ws)
        with self.assertRaises(PathContractError):
            build_execution_snapshot(("a", "a"), ws)  # duplicate
        with self.assertRaises(PathContractError):
            build_execution_snapshot((), ws)  # empty


# --- proof 11: mutate workspace after observe; ambient-requiring harness -------

class MutationAndAmbientHarness(unittest.TestCase):
    def test_harness_sees_original_bytes_after_workspace_mutation(self):
        ws = FakeWorkspace({"a": _present_capture("a", b"original")})
        harness = MutatingHarness(ws, b"original", exit_code=0)
        facts = execute_spike("pytest", ("a",), ws, harness)
        # harness saw the snapshot's ORIGINAL captured bytes, never the mutated ambient ones
        self.assertEqual(harness.saw_bytes, (b"original",))
        # echoed digest matched the snapshot built from the original -> no contract error
        self.assertEqual(facts.gate, Gate.PASS)
        self.assertEqual(len(harness.invocations), 1)

    def test_harness_requiring_ambient_workspace_unavailable(self):
        ws = _present_ws("a", b"AA")
        harness = FakeSpikeHarness(require_workspace=True)
        with self.assertRaises(HarnessUnavailable):
            execute_spike("pytest", ("a",), ws, harness)
        self.assertEqual(len(harness.invocations), 1)


# --- proof 12: non-present prevents harness; harness exception; echo mismatch --

class ExecutionFailClosed(unittest.TestCase):
    def test_nonpresent_capture_prevents_harness_call(self):
        ws = FakeWorkspace()  # path "a" defaults to MISSING
        harness = FakeSpikeHarness()
        with self.assertRaises(ObservationUnavailable):
            execute_spike("pytest", ("a",), ws, harness)
        self.assertEqual(harness.invocations, [])

    def test_harness_exception_raises_unavailable(self):
        ws = _present_ws("a", b"AA")
        harness = FakeSpikeHarness(raise_exc=RuntimeError("harness boom"))
        with self.assertRaises(HarnessUnavailable):
            execute_spike("pytest", ("a",), ws, harness)

    def test_snapshot_echo_mismatch_raises_contract_error(self):
        ws = _present_ws("a", b"AA")
        harness = FakeSpikeHarness(exit_code=0, echo_digest=_d("x"))  # wrong echo
        with self.assertRaises(HarnessContractError):
            execute_spike("pytest", ("a",), ws, harness)

    def test_gate_derived_from_exit_code_only(self):
        for code, gate in ((0, Gate.PASS), (1, Gate.FAIL), (42, Gate.FAIL)):
            ws = _present_ws("a", b"AA")
            harness = FakeSpikeHarness(exit_code=code)
            facts = execute_spike("pytest", ("a",), ws, harness)
            self.assertEqual(facts.gate, gate)
            self.assertEqual(facts.exit_code, code)


# --- port shape sanity (protocols present, facts are plain transport facts) ----

class PortShape(unittest.TestCase):
    def test_protocols_are_protocols(self):
        for proto in (Workspace, SpikeHarness):
            self.assertTrue(hasattr(proto, "_is_protocol"))
        # capture types carry the documented fields
        cf = CapturedFile(FileObservation("a", MISSING, None), None)
        self.assertEqual(cf.observation.path, "a")
        self.assertIsNone(cf.content)
        hr = HarnessResult(0, _d("r"), _d("s"))
        self.assertEqual(hr.exit_code, 0)
        self.assertEqual(hr.snapshot_digest, _d("s"))

    def test_harness_result_rejects_bool_exit_code(self):
        with self.assertRaises((ValueError, Exception)):
            HarnessResult(True, _d("r"), _d("s"))

    def test_harness_result_rejects_bad_digests(self):
        with self.assertRaises((ValueError, Exception)):
            HarnessResult(0, "bad", _d("s"))
        with self.assertRaises((ValueError, Exception)):
            HarnessResult(0, _d("r"), "bad")

    def test_observation_snapshot_rejects_empty_basis_id(self):
        with self.assertRaises((ValueError, Exception)):
            ObservationSnapshot((), "", _d("e"))

    def test_observation_snapshot_rejects_bad_observations(self):
        with self.assertRaises((ValueError, Exception)):
            ObservationSnapshot("not-a-tuple", "b1", _d("e"))
        with self.assertRaises((ValueError, Exception)):
            ObservationSnapshot(("not-an-obs",), "b1", _d("e"))

    def test_observation_snapshot_rejects_bad_digest(self):
        with self.assertRaises((ValueError, Exception)):
            ObservationSnapshot((), "b1", "bad")

    def test_execution_snapshot_rejects_mismatched_bytes_length(self):
        with self.assertRaises((ValueError, Exception)):
            ExecutionSnapshot((FileBinding("a", _d("x")),), (), _d("s"))

    def test_execution_snapshot_rejects_bad_content_digest(self):
        with self.assertRaises((ValueError, Exception)):
            ExecutionSnapshot((FileBinding("a", _d("x")),), (b"wrong",), _d("s"))

    def test_execution_snapshot_rejects_non_bytes(self):
        with self.assertRaises((ValueError, Exception)):
            ExecutionSnapshot((FileBinding("a", _d("x")),), ("not-bytes",), _d("s"))

    def test_execution_snapshot_rejects_duplicate_paths(self):
        with self.assertRaises((ValueError, Exception)):
            ExecutionSnapshot(
                (FileBinding("a", _d("x")), FileBinding("a", _d("y"))),
                (b"a", b"a"), _d("s"))

    def test_execution_snapshot_rejects_empty_bindings(self):
        with self.assertRaises((ValueError, Exception)):
            ExecutionSnapshot((), (), _d("s"))

    def test_observation_snapshot_rejects_wrong_digest(self):
        # valid digest256 format but not the canonical_digest of observations
        sha_a = _d("a")
        obs = (FileObservation("a", PRESENT, sha_a),)
        with self.assertRaises((ValueError, Exception)):
            ObservationSnapshot(obs, "b1", _d("not-the-canonical-digest"))

    def test_observation_snapshot_rejects_noncanonical_order(self):
        # paths ("b","a") are not canonical lexical order — constructor must reject
        sha_b = _digest_bytes(b"bb")
        sha_a = _digest_bytes(b"aa")
        obs = (FileObservation("b", PRESENT, sha_b), FileObservation("a", PRESENT, sha_a))
        digest = canonical_digest([("b", "present", sha_b), ("a", "present", sha_a)])
        with self.assertRaises((ValueError, Exception)):
            ObservationSnapshot(obs, "b1", digest)

    def test_observation_snapshot_rejects_duplicate_observation_paths(self):
        sha = _d("a")
        obs = (FileObservation("a", PRESENT, sha), FileObservation("a", PRESENT, sha))
        digest = canonical_digest([("a", "present", sha), ("a", "present", sha)])
        with self.assertRaises((ValueError, Exception)):
            ObservationSnapshot(obs, "b1", digest)

    def test_execution_snapshot_rejects_wrong_canonical_digest(self):
        # valid format + correct content digest, but snapshot_digest != canonical_digest(bindings)
        sha_a = _digest_bytes(b"aa")
        with self.assertRaises((ValueError, Exception)):
            ExecutionSnapshot((FileBinding("a", sha_a),), (b"aa",), _d("wrong"))


# --- D5 correction: command validation pre-port + whitespace preservation ------

class CommandValidation(unittest.TestCase):
    def test_empty_command_rejected_before_port_call(self):
        ws = _present_ws("a", b"AA")
        harness = FakeSpikeHarness()
        with self.assertRaises((ValueError, Exception)):
            execute_spike("", ("a",), ws, harness)
        self.assertEqual(ws.observe_calls, [])
        self.assertEqual(harness.invocations, [])

    def test_nonstring_command_rejected_before_port_call(self):
        ws = _present_ws("a", b"AA")
        harness = FakeSpikeHarness()
        with self.assertRaises((ValueError, Exception)):
            execute_spike(123, ("a",), ws, harness)
        self.assertEqual(ws.observe_calls, [])
        self.assertEqual(harness.invocations, [])

    def test_none_command_rejected_before_port_call(self):
        ws = _present_ws("a", b"AA")
        harness = FakeSpikeHarness()
        with self.assertRaises((ValueError, Exception)):
            execute_spike(None, ("a",), ws, harness)
        self.assertEqual(ws.observe_calls, [])
        self.assertEqual(harness.invocations, [])

    def test_whitespace_command_preserved(self):
        ws = _present_ws("a", b"AA")
        harness = FakeSpikeHarness(exit_code=0)
        cmd = "  pytest --flag  "
        execute_spike(cmd, ("a",), ws, harness)
        self.assertEqual(harness.invocations[0]["command"], cmd)

    def test_whitespace_only_command_preserved(self):
        ws = _present_ws("a", b"AA")
        harness = FakeSpikeHarness(exit_code=0)
        cmd = "   "
        execute_spike(cmd, ("a",), ws, harness)
        self.assertEqual(harness.invocations[0]["command"], cmd)


if __name__ == "__main__":
    unittest.main()
