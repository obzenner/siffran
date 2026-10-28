#!/usr/bin/env python3
"""Application observation snapshots: one basis, closed captures, and no execution port."""
from __future__ import annotations

import dataclasses
import hashlib
import sys
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from application.observation import (  # noqa: E402
    ObservationUnavailable, build_observation_snapshot)
from application.ports import (  # noqa: E402
    CapturedFile, ObservationSnapshot, WorkspaceCapture)
from core.freshness import (  # noqa: E402
    ActiveSpikeHead, FileBinding, FileObservation, ObservationState, canonical_digest,
)


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_text(value: str) -> str:
    return digest_bytes(value.encode())


def present(path: str, content: bytes) -> CapturedFile:
    return CapturedFile(
        FileObservation(path, ObservationState.PRESENT, digest_bytes(content)), content)


class WorkspaceFake:
    def __init__(self, files=(), *, basis="basis-1", error=None, result=None):
        self.files = {item.observation.path: item for item in files}
        self.basis = basis
        self.error = error
        self.result = result
        self.calls = []

    def observe(self, paths):
        self.calls.append(paths)
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return WorkspaceCapture(self.basis, tuple(self.files[path] for path in paths))


class ObservationTests(unittest.TestCase):
    def head(self, *paths):
        return ActiveSpikeHead(
            digest_text("artifact"), "claim", "research",
            tuple(FileBinding(path, digest_text(path)) for path in paths))

    def test_empty_snapshot_performs_no_workspace_read(self):
        workspace = WorkspaceFake()
        snapshot = build_observation_snapshot((), workspace)
        self.assertEqual(workspace.calls, [])
        self.assertEqual(snapshot, ObservationSnapshot((), canonical_digest(()), canonical_digest(())))

    def test_overlapping_heads_use_one_sorted_capture_basis(self):
        first = self.head("b", "a")
        second = self.head("a")
        workspace = WorkspaceFake((present("a", b"a"), present("b", b"b")))
        snapshot = build_observation_snapshot((first, second), workspace)
        self.assertEqual(workspace.calls, [("a", "b")])
        self.assertEqual(tuple(item.path for item in snapshot.observations), ("a", "b"))
        self.assertEqual(snapshot.basis_id, "basis-1")

    def test_workspace_exception_fails_closed(self):
        workspace = WorkspaceFake(error=RuntimeError("boom"))
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((self.head("a"),), workspace)

    def test_non_capture_result_fails_closed(self):
        workspace = WorkspaceFake(result={"basis_id": "basis-1", "files": ()})
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot((self.head("a"),), workspace)

    def test_wrong_count_path_or_file_type_fails_closed(self):
        cases = (
            WorkspaceCapture("basis-1", ()),
            WorkspaceCapture("basis-1", (present("b", b"b"),)),
            WorkspaceCapture("basis-1", (object(),)),
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ObservationUnavailable):
                build_observation_snapshot((self.head("a"),), WorkspaceFake(result=value))

    def test_present_capture_requires_exact_immutable_bytes(self):
        observation = FileObservation("a", ObservationState.PRESENT, digest_bytes(b"a"))
        cases = (CapturedFile(observation, None), CapturedFile(observation, b"changed"))
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ObservationUnavailable):
                build_observation_snapshot(
                    (self.head("a"),), WorkspaceFake(result=WorkspaceCapture("basis-1", (value,))))

    def test_non_present_capture_requires_null_content(self):
        observation = FileObservation("a", ObservationState.MISSING, None)
        with self.assertRaises(ObservationUnavailable):
            build_observation_snapshot(
                (self.head("a"),),
                WorkspaceFake(result=WorkspaceCapture("basis-1", (CapturedFile(observation, b"x"),))))

    def test_snapshot_is_frozen(self):
        snapshot = build_observation_snapshot(
            (self.head("a"),), WorkspaceFake((present("a", b"a"),)))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snapshot.basis_id = "changed"

    def test_closed_ports_do_not_restore_command_execution_contracts(self):
        from application import ports
        for name in (
            "ExecutionSnapshot", "HarnessResult", "SpikeHarness", "SpikeExecutionUnavailable",
            "UnavailableSpikeHarness",
        ):
            self.assertFalse(hasattr(ports, name), name)


if __name__ == "__main__":
    unittest.main()
