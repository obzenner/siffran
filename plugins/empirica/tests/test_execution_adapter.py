#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from adapters.execution import (  # noqa: E402
    OUTPUT_LIMIT_EXIT_CODE,
    FilesystemWorkspace,
    SubprocessSpikeHarness,
)
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
            write = SubprocessSpikeHarness().run("echo changed >> src/value.txt", snapshot)
            self.assertNotEqual(write.exit_code, 0)

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

    def test_workspace_root_replacement_fails_closed_without_fd_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "workspace"
            root.mkdir()
            (root / "input").write_text("original", encoding="utf-8")
            workspace = FilesystemWorkspace(root)
            root.rename(base / "old-workspace")
            root.mkdir()
            (root / "input").write_text("replacement", encoding="utf-8")
            fd_dir = Path("/dev/fd") if Path("/dev/fd").is_dir() else Path("/proc/self/fd")
            before = len(list(fd_dir.iterdir()))
            for _ in range(20):
                capture = workspace.observe(("input",))
                self.assertIs(capture.files[0].observation.state, ObservationState.UNREADABLE)
                self.assertIsNone(capture.files[0].content)
            self.assertLessEqual(len(list(fd_dir.iterdir())), before + 1)

    def test_workspace_rejects_path_replaced_by_symlink_before_open(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            victim = root / "victim"
            victim.write_text("captured", encoding="utf-8")
            secret = Path(outside) / "secret"
            secret.write_text("outside", encoding="utf-8")
            real_open = os.open
            replaced = False

            def racing_open(path, flags, *args, **kwargs):
                nonlocal replaced
                if path == "victim" and kwargs.get("dir_fd") is not None and not replaced:
                    replaced = True
                    victim.unlink()
                    victim.symlink_to(secret)
                return real_open(path, flags, *args, **kwargs)

            with patch("adapters.execution.os.open", side_effect=racing_open):
                capture = FilesystemWorkspace(root).observe(("victim",))
            self.assertIsNone(capture.files[0].content)
            self.assertIsNot(capture.files[0].observation.state, ObservationState.PRESENT)

    def test_harness_bounds_output_and_terminates_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input").write_text("ok", encoding="utf-8")
            snapshot = build_execution_snapshot(("input",), FilesystemWorkspace(root))
            noisy = SubprocessSpikeHarness(output_limit_bytes=1024).run(
                f"{shlex.quote(sys.executable)} -c 'import sys;sys.stdout.write(\"x\"*100000)'",
                snapshot)
            self.assertEqual(noisy.exit_code, OUTPUT_LIMIT_EXIT_CODE)
            pidfile = root / "child.pid"
            result = SubprocessSpikeHarness().run(
                f"sleep 60 & echo $! > {shlex.quote(str(pidfile))}", snapshot)
            self.assertEqual(result.exit_code, 0)
            pid = int(pidfile.read_text(encoding="utf-8"))
            for _ in range(100):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.01)
            else:
                self.fail("spike descendant survived process-group cleanup")

    def test_harness_timeout_and_mcp_margin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input").write_text("ok", encoding="utf-8")
            snapshot = build_execution_snapshot(("input",), FilesystemWorkspace(root))
            harness = SubprocessSpikeHarness(timeout_seconds=0.02)
            self.assertEqual(harness.run("sleep 60", snapshot).exit_code, 124)
        manifest = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
        mcp_timeout = manifest["mcpServers"]["empirica"]["tool_timeout_sec"]
        self.assertGreaterEqual(mcp_timeout, SubprocessSpikeHarness().timeout_seconds + 60)

    def test_workspace_rejects_symlink_escape_as_non_regular(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (Path(outside) / "secret").write_text("secret", encoding="utf-8")
            (root / "link").symlink_to(Path(outside) / "secret")
            capture = FilesystemWorkspace(root).observe(("link",))
            self.assertIs(capture.files[0].observation.state, ObservationState.NON_REGULAR)
            self.assertIsNone(capture.files[0].content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
