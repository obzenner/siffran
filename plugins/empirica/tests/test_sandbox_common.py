#!/usr/bin/env python3
"""Fake-only generic sandbox process, scaffolding, lock and implementation-manifest tests."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters.sandbox import common, process


class BoundedRunnerTests(unittest.TestCase):
    def test_captures_independent_stream_identities(self):
        result = process.BoundedRunner().run(
            (sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"),
            timeout_seconds=2, output_limit_bytes=1024)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.data, b"out\n")
        self.assertEqual(result.stderr.data, b"err\n")
        self.assertNotEqual(result.stdout.sha256, result.stderr.sha256)

    def test_independent_stream_limits_do_not_collapse_to_one_ceiling(self):
        result = process.BoundedRunner().run(
            (sys.executable, "-c",
             "import os; os.write(1,b'x'*64); os.write(2,b'y'*1024)"),
            timeout_seconds=2, stdout_limit_bytes=128, stderr_limit_bytes=2048)
        self.assertFalse(result.output_limited)
        self.assertEqual((result.stdout.bytes_seen, result.stderr.bytes_seen), (64, 1024))
        limited = process.BoundedRunner().run(
            (sys.executable, "-c", "import os; os.write(2,b'y'*1024)"),
            timeout_seconds=2, stdout_limit_bytes=2048, stderr_limit_bytes=128)
        self.assertTrue(limited.output_limited)
        self.assertGreater(limited.stderr.bytes_seen, 128)

    def test_child_umask_makes_docker_style_created_file_owner_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "container.cid"
            process.BoundedRunner(umask=0o077).run(
                (sys.executable, "-c", "open(__import__('sys').argv[1],'w').write('x')", str(target)),
                timeout_seconds=2, output_limit_bytes=1024)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_timeout_kills_owned_process_group(self):
        result = process.BoundedRunner().run(
            (sys.executable, "-c", "import time; time.sleep(5)"),
            timeout_seconds=0.05, output_limit_bytes=1024)
        self.assertTrue(result.timed_out)

    def test_output_limit_is_counted_beyond_retained_bytes(self):
        result = process.BoundedRunner().run(
            (sys.executable, "-c", "import sys; sys.stdout.write('x' * 10000)"),
            timeout_seconds=2, output_limit_bytes=128)
        self.assertTrue(result.output_limited)
        self.assertGreater(result.stdout.bytes_seen, 128)
        self.assertEqual(len(result.stdout.data), 128)

    def test_invalid_bounds_reject_before_spawn(self):
        for timeout, limit in ((0, 1), (True, 1), (1, 0), (1, True)):
            with self.subTest(timeout=timeout, limit=limit), self.assertRaises(ValueError):
                process.BoundedRunner().run(
                    (sys.executable, "-c", "pass"), timeout_seconds=timeout,
                    output_limit_bytes=limit)


class ScaffoldingTests(unittest.TestCase):
    def root(self, temporary: str) -> Path:
        root = Path(temporary).resolve(strict=True) / "sandbox"
        return root

    def test_setup_is_explicit_and_runtime_validation_is_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.root(temporary)
            store = common.ExecutionStore(root)
            with self.assertRaises(common.GenericScaffoldingError):
                store.check_execution_scaffolding()
            self.assertFalse(root.exists())
            store.prepare_execution_scaffolding()
            store.check_execution_scaffolding()
            self.assertEqual(store.runtime_lock.read_bytes(), b"")

    def test_relative_or_symlinked_home_is_rejected(self):
        with self.assertRaises(common.GenericScaffoldingError):
            common.ExecutionStore(Path("relative"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            target = root / "target"
            target.mkdir(mode=0o700)
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            with self.assertRaises(common.GenericScaffoldingError):
                common.ExecutionStore(alias)

    def test_mount_csv_delimiters_are_rejected_with_actionable_setup_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            for name in ('bad,name', 'bad"name'):
                with self.subTest(name=name), self.assertRaisesRegex(
                        common.GenericScaffoldingError, "Docker mount syntax"):
                    common.ExecutionStore(root / name)

    def test_lock_requires_exact_store_and_fails_at_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = common.ExecutionStore(self.root(temporary))
            store.prepare_execution_scaffolding()
            with self.assertRaises(common.GenericScaffoldingError):
                with common.runtime_lock(object(), time.monotonic() + 1):
                    pass
            with self.assertRaises(common.GenericScaffoldingError):
                with common.runtime_lock(store, time.monotonic() - 1):
                    pass

    def test_lock_serializes_threads(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = common.ExecutionStore(self.root(temporary))
            store.prepare_execution_scaffolding()
            entered = []
            ready = threading.Event()

            def contender():
                ready.set()
                with common.runtime_lock(store, time.monotonic() + 2):
                    entered.append("second")

            with common.runtime_lock(store, time.monotonic() + 2):
                thread = threading.Thread(target=contender)
                thread.start()
                ready.wait(1)
                time.sleep(0.03)
                self.assertEqual(entered, [])
            thread.join(2)
            self.assertEqual(entered, ["second"])

    def test_sandbox_home_override_is_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.root(temporary)
            with patch.dict(os.environ, {common.SANDBOX_HOME_ENV: str(root)}):
                self.assertEqual(common.sandbox_home(), root)
            with patch.dict(os.environ, {common.SANDBOX_HOME_ENV: "relative"}):
                with self.assertRaises(common.GenericScaffoldingError):
                    common.sandbox_home()


class ImplementationManifestTests(unittest.TestCase):
    def test_implementation_manifest_is_closed_sorted_and_binds_every_listed_source(self):
        # Membership is owned by the closure rule (doc/testing.md), checked by `make empirica-manifest-check`.
        paths = common.IMPLEMENTATION_SOURCE_PATHS
        self.assertEqual(paths, tuple(sorted(set(paths))))
        manifest = common.implementation_manifest()
        self.assertEqual([row["path"] for row in manifest["files"]], list(paths))
        from core.canonical import canonical_digest
        original = manifest["implementation_digest"]
        for path in paths:
            changed = [dict(row) for row in manifest["files"]]
            next(row for row in changed if row["path"] == path)["sha256"] = "sha256:" + "f" * 64
            self.assertNotEqual(canonical_digest({"files": changed}), original)

    def test_loaded_release_is_frozen_once_per_process_and_fails_closed(self):
        digest = "sha256:" + "0" * 64
        other = "sha256:" + "1" * 64
        common._loaded_release = None
        common._loaded_release_failed = None
        self.addCleanup(lambda: setattr(common, "_loaded_release", None))
        self.addCleanup(lambda: setattr(common, "_loaded_release_failed", None))
        calls = []

        def disk_manifest():
            calls.append(1)
            return {"implementation_digest": digest}

        self.assertEqual(common.freeze_loaded_release(disk_manifest), digest)
        self.assertEqual(common.freeze_loaded_release(
            lambda: {"implementation_digest": other}), digest)
        self.assertEqual(common.loaded_release_digest(), digest)
        self.assertEqual(len(calls), 1)  # the disk is read exactly once, at the first freeze
        for index, malformed in enumerate((None, [()], 123, {}, {"implementation_digest": 1})):
            with self.subTest(index=index):
                common._loaded_release = None
                common._loaded_release_failed = None
                with self.assertRaises(common.GenericScaffoldingError) as caught:
                    common.freeze_loaded_release(lambda: malformed)
                self.assertEqual(
                    str(caught.exception), "loaded implementation release is malformed")
                self.assertEqual(
                    common._loaded_release_failed, "loaded implementation release is malformed")

    def test_failed_first_snapshot_latches_loaded_release_unavailable_for_the_process(self):
        digest = "sha256:" + "2" * 64
        common._loaded_release = None
        common._loaded_release_failed = None
        self.addCleanup(lambda: setattr(common, "_loaded_release", None))
        self.addCleanup(lambda: setattr(common, "_loaded_release_failed", None))
        reads = []

        def unsafe_disk_manifest(failure):
            reads.append(1)
            raise failure
        unavailable = "loaded implementation release is unavailable"
        failures = (
            # an arbitrary snapshot failure whose detail carries a filesystem path: the
            # boundary latches a fixed path-free message, never the cause's text
            OSError("cannot read /private/tmp/siffran/secret/implementation.py"),
            # an empty diagnostic must not reach the latched message either
            common.GenericScaffoldingError(""),
        )

        for index, failure in enumerate(failures):
            with self.subTest(index=index):
                common._loaded_release = None
                common._loaded_release_failed = None
                with self.assertRaises(common.GenericScaffoldingError) as caught:
                    common.freeze_loaded_release(lambda: unsafe_disk_manifest(failure))
                self.assertEqual(str(caught.exception), unavailable)
                self.assertNotIn("/private", str(caught.exception))
                # the original failure stays chained as the cause, never as the message
                self.assertIs(caught.exception.__cause__, failure)
                # A valid release has now landed on disk; the process that failed its first
                # snapshot must not adopt it: the loaded release stays unavailable for its life
                # and the disk is never re-read at the boundary.
                self.assertEqual(common.loaded_release_digest(), None)
                for attempt in range(2):
                    with self.subTest(attempt=attempt), self.assertRaises(
                            common.GenericScaffoldingError) as retry:
                        common.freeze_loaded_release(
                            lambda: {"implementation_digest": digest})
                    self.assertEqual(
                        str(retry.exception), unavailable)
                    self.assertNotIn("/private", str(retry.exception))
                self.assertEqual(common._loaded_release_failed, unavailable)
                self.assertNotEqual(common._loaded_release, digest)
        self.assertEqual(reads, [1] * len(failures))  # one attempt per fresh process, never a retry


if __name__ == "__main__":
    unittest.main()
