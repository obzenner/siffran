#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from adapters.execution import FilesystemWorkspace  # noqa: E402
from core.freshness import ObservationState  # noqa: E402


class WorkspaceCaptureAdapterTests(unittest.TestCase):
    def test_workspace_preserves_requested_path_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").write_text("a", encoding="utf-8")
            (root / "b").write_text("b", encoding="utf-8")
            capture = FilesystemWorkspace(root).observe(("b", "a"))
            self.assertEqual(
                [row.observation.path for row in capture.files], ["b", "a"])
            self.assertEqual([row.content for row in capture.files], [b"b", b"a"])

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

    def test_capture_inputs_expands_directories_in_canonical_order_and_empty_is_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tree" / "z").mkdir(parents=True)
            (root / "tree" / "b.txt").write_bytes(b"b")
            (root / "tree" / "z" / "a.txt").write_bytes(b"a")
            workspace = FilesystemWorkspace(root)
            capture = workspace.capture_inputs(("tree/",))
            self.assertEqual(
                [item.observation.path for item in capture.files],
                ["tree/b.txt", "tree/z/a.txt"])
            self.assertEqual([item.content for item in capture.files], [b"b", b"a"])
            empty = workspace.capture_inputs(())
            self.assertEqual(empty.files, ())
            self.assertEqual(empty.basis_id, workspace.basis_of(()))

    def test_capture_inputs_skips_nested_symlink_git_and_special_file(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            tree = root / "tree"
            tree.mkdir()
            (tree / "kept.txt").write_text("kept", encoding="utf-8")
            secret = Path(outside) / "secret"
            secret.write_text("secret", encoding="utf-8")
            (tree / "link").symlink_to(secret)
            (tree / "loop").symlink_to(tree, target_is_directory=True)
            (tree / ".git").mkdir()
            (tree / ".git" / "config").write_text("private", encoding="utf-8")
            os.mkfifo(tree / "pipe")
            capture = FilesystemWorkspace(root).capture_inputs(("tree/",))
            self.assertEqual([item.observation.path for item in capture.files],
                             ["tree/kept.txt"])
            self.assertEqual(capture.files[0].content, b"kept")

    def test_capture_inputs_applies_count_and_size_ceilings_before_any_unbounded_read(self):
        from core import spike_plan
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = root / "node_modules"
            tree.mkdir()
            huge = tree / "huge.bin"
            with huge.open("wb") as stream:
                stream.truncate(spike_plan.MAX_FILE_SIZE_BYTES + 1)
            with self.assertRaisesRegex(OSError, "per-file byte ceiling"):
                FilesystemWorkspace(root).capture_inputs(("node_modules/",))
            huge.unlink()
            for name in ("aggregate-a", "aggregate-b"):
                with (tree / name).open("wb") as stream:
                    stream.truncate(40 * 1024 * 1024)
            with self.assertRaisesRegex(OSError, "aggregate ceiling"):
                FilesystemWorkspace(root).capture_inputs(("node_modules/",))
            for name in ("aggregate-a", "aggregate-b"):
                (tree / name).unlink()
            for index in range(spike_plan.MAX_STAGING_FILES + 1):
                (tree / f"f-{index:03d}").write_bytes(b"")
            with self.assertRaisesRegex(OSError, "file count"):
                FilesystemWorkspace(root).capture_inputs(("node_modules/",))

    def test_capture_inputs_rejects_explicit_symlink_special_and_git_paths(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            target = Path(outside) / "secret"
            target.write_text("secret", encoding="utf-8")
            (root / "link").symlink_to(target)
            os.mkfifo(root / "pipe")
            (root / ".git").mkdir()
            for declaration in ("link", "pipe", ".git/"):
                with self.subTest(declaration=declaration), self.assertRaises(OSError):
                    FilesystemWorkspace(root).capture_inputs((declaration,))

    def test_capture_inputs_rejects_file_growth_during_bounded_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "changing.txt"
            target.write_bytes(b"initial")
            original_fstat = os.fstat
            calls = 0

            def racing_fstat(fd):
                nonlocal calls
                calls += 1
                if calls == 3:
                    with target.open("ab") as stream:
                        stream.write(b"changed")
                return original_fstat(fd)

            with patch("adapters.execution.os.fstat", side_effect=racing_fstat):
                capture = FilesystemWorkspace(root).capture_inputs(("changing.txt",))
            self.assertIs(capture.files[0].observation.state, ObservationState.UNREADABLE)
            self.assertIsNone(capture.files[0].content)

    def test_capture_inputs_rejects_directory_replaced_by_symlink_during_walk(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            tree = root / "tree"
            tree.mkdir()
            (tree / "a").write_text("a", encoding="utf-8")
            secret = Path(outside) / "secret"
            secret.mkdir()
            (secret / "x").write_text("x", encoding="utf-8")
            real_open = os.open
            replaced = False

            def racing_open(path, flags, *args, **kwargs):
                nonlocal replaced
                if path == "tree" and kwargs.get("dir_fd") is not None and not replaced:
                    replaced = True
                    (tree / "a").unlink()
                    tree.rmdir()
                    tree.symlink_to(secret, target_is_directory=True)
                return real_open(path, flags, *args, **kwargs)

            with patch("adapters.execution.os.open", side_effect=racing_open), \
                    self.assertRaises(OSError):
                FilesystemWorkspace(root).capture_inputs(("tree/",))

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
