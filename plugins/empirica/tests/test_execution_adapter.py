#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from adapters.execution import FilesystemWorkspace, SubprocessSpikeHarness  # noqa: E402
from application.observation import build_execution_snapshot  # noqa: E402
from core.freshness import ObservationState  # noqa: E402


class ProductionExecutionAdapterTests(unittest.TestCase):
    def test_harness_executes_only_immutable_captured_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "src" / "value.txt"
            target.parent.mkdir()
            target.write_text("captured", encoding="utf-8")
            workspace = FilesystemWorkspace(root)
            snapshot = build_execution_snapshot(("src/value.txt",), workspace)
            target.write_text("ambient-mutation", encoding="utf-8")
            result = SubprocessSpikeHarness().run(
                "test \"$(cat src/value.txt)\" = captured", snapshot)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.snapshot_digest, snapshot.snapshot_digest)

    def test_workspace_preserves_requested_path_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").write_text("a", encoding="utf-8")
            (root / "b").write_text("b", encoding="utf-8")
            capture = FilesystemWorkspace(root).observe(("b", "a"))
            self.assertEqual(
                [row.observation.path for row in capture.files], ["b", "a"])
            snapshot = build_execution_snapshot(("b", "a"), FilesystemWorkspace(root))
            self.assertEqual(
                [row.path for row in snapshot.file_bindings], ["b", "a"])

    def test_workspace_rejects_symlink_escape_as_non_regular(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (Path(outside) / "secret").write_text("secret", encoding="utf-8")
            (root / "link").symlink_to(Path(outside) / "secret")
            capture = FilesystemWorkspace(root).observe(("link",))
            self.assertIs(capture.files[0].observation.state, ObservationState.OUTSIDE_WORKSPACE)
            self.assertIsNone(capture.files[0].content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
