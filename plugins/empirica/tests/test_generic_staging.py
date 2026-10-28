#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path

from adapters.sandbox import common, generic_staging as staging
from application.generic_spike import GenericExecutionSnapshot
from core.canonical import canonical_digest
from core.freshness import FileBinding
from test_docker_planner import operation, request


def snapshot(files):
    bindings = tuple(FileBinding(path, "sha256:" + hashlib.sha256(data).hexdigest())
                     for path, data in files)
    contents = tuple(data for _, data in files)
    basis = canonical_digest([(item.path, item.sha256) for item in bindings])
    digest = canonical_digest({
        "basis_id": basis,
        "files": [(item.path, item.sha256, len(content))
                  for item, content in zip(bindings, contents)],
    })
    return GenericExecutionSnapshot(basis, bindings, contents, digest)


class GenericStagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = common.ExecutionStore(Path(self.temporary.name).resolve() / "sandbox")
        self.store.prepare_execution_scaffolding()
        self.op = operation()

    def materialize(self, files=(("a.py", b"x"),)):
        capture = snapshot(files)
        plan = request().plan
        from dataclasses import replace
        from core import spike_plan
        captured = tuple(spike_plan.CapturedFile(
            path, "sha256:" + hashlib.sha256(data).hexdigest(), len(data))
            for path, data in files)
        declarations = tuple(sorted({path.split("/", 1)[0] + ("/" if "/" in path else "")
                                     for path, _ in files}))
        plan = replace(plan, declared_inputs=declarations, captured_files=captured,
                       capture_digest=canonical_digest([item.to_data() for item in captured]))
        manifest = staging.materialize_generic_staging(
            self.store, self.op.operation_id, plan, capture, self.op.staging_token)
        return plan, capture, manifest

    def test_nested_snapshot_is_published_read_only_from_captured_bytes(self):
        plan, capture, manifest = self.materialize(
            (("src/a.py", b"a"), ("src/lib/b.py", b"b")))
        self.assertEqual((manifest.root / "src/a.py").read_bytes(), b"a")
        self.assertEqual(stat.S_IMODE(manifest.root.stat().st_mode), 0o555)
        self.assertEqual(stat.S_IMODE((manifest.root / "src").stat().st_mode), 0o555)
        self.assertEqual(stat.S_IMODE((manifest.root / "src/a.py").stat().st_mode), 0o444)
        self.assertEqual(staging.revalidate_generic_staging(
            self.store, plan, capture, manifest), manifest.root)
        self.assertFalse((self.store.staging / ("." + self.op.operation_id + ".tmp")).exists())

    def test_empty_capture_still_publishes_one_empty_inputs_root(self):
        plan, capture, manifest = self.materialize(())
        self.assertTrue(manifest.root.is_dir())
        self.assertEqual(tuple(manifest.root.iterdir()), ())
        self.assertEqual(manifest.files, ())
        staging.revalidate_generic_staging(self.store, plan, capture, manifest)

    def test_plan_snapshot_mismatch_refuses_before_publication(self):
        with self.assertRaises(staging.GenericStagingError):
            staging.materialize_generic_staging(
                self.store, self.op.operation_id, request().plan,
                snapshot((("a.py", b"changed"),)), self.op.staging_token)
        self.assertFalse((self.store.staging / self.op.operation_id).exists())

    def test_extra_symlink_special_mode_and_content_drift_fail_closed(self):
        for variant in ("extra", "symlink", "fifo", "mode", "content"):
            with self.subTest(variant=variant):
                if (self.store.staging / self.op.operation_id).exists():
                    staging.cleanup_generic_staging(self.store, self.op.operation_id)
                plan, capture, manifest = self.materialize()
                root = manifest.root
                if variant == "extra":
                    os.chmod(root, 0o755)
                    (root / "extra").write_bytes(b"x")
                    os.chmod(root, 0o555)
                elif variant == "symlink":
                    os.chmod(root, 0o755)
                    (root / "link").symlink_to("a.py")
                    os.chmod(root, 0o555)
                elif variant == "fifo":
                    os.chmod(root, 0o755)
                    os.mkfifo(root / "pipe")
                    os.chmod(root, 0o555)
                elif variant == "mode":
                    os.chmod(root / "a.py", 0o644)
                else:
                    os.chmod(root / "a.py", 0o644)
                    (root / "a.py").write_bytes(b"y")
                    os.chmod(root / "a.py", 0o444)
                with self.assertRaises(staging.GenericStagingError):
                    staging.revalidate_generic_staging(self.store, plan, capture, manifest)
                if variant in {"symlink", "fifo"}:
                    with self.assertRaises(staging.GenericStagingError):
                        staging.cleanup_generic_staging(self.store, self.op.operation_id)
                    if variant == "symlink":
                        (root / "link").unlink()
                    else:
                        (root / "pipe").unlink()
                if variant == "extra":
                    os.chmod(root, 0o755)
                    (root / "extra").unlink()
                    os.chmod(root, 0o555)
                elif variant == "mode":
                    os.chmod(root / "a.py", 0o444)
                if root.exists():
                    staging.cleanup_generic_staging(self.store, self.op.operation_id)

    def test_cleanup_removes_only_exact_tree_and_proves_absence(self):
        _, _, manifest = self.materialize()
        unrelated = self.store.staging / "unrelated"
        unrelated.mkdir(mode=0o700)
        staging.cleanup_generic_staging(self.store, self.op.operation_id)
        self.assertFalse(manifest.root.exists())
        self.assertTrue(unrelated.exists())
        staging.cleanup_generic_staging(self.store, self.op.operation_id)

    def test_cleanup_removes_crash_partial_tree_and_proves_both_paths_absent(self):
        partial = self.store.staging / ("." + self.op.operation_id + ".tmp")
        partial.mkdir(mode=0o700)
        (partial / "partial").write_bytes(b"x")
        (partial / "partial").chmod(0o600)
        staging.cleanup_generic_staging(self.store, self.op.operation_id)
        self.assertFalse(partial.exists())
        self.assertFalse((self.store.staging / self.op.operation_id).exists())

    def test_replaced_root_link_is_never_followed_or_removed(self):
        _, _, manifest = self.materialize()
        os.chmod(manifest.root, 0o700)
        (manifest.root / "a.py").unlink()
        manifest.root.rmdir()
        target = self.store.staging / "target"
        target.mkdir(mode=0o700)
        manifest.root.symlink_to(target, target_is_directory=True)
        with self.assertRaises(staging.GenericStagingError):
            staging.cleanup_generic_staging(self.store, self.op.operation_id)
        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
