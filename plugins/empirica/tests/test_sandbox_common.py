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

    def test_a_create_race_lost_to_a_planted_symlink_is_refused_target_untouched(self):
        # (re-review, blocking 3) a scaffolding creation that lost the concurrent
        # first-use race is refused without any permission change first: the planted
        # link's target keeps its own mode, and the chmod never follows the link.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            target = root / "target"
            target.mkdir(mode=0o755)
            planted = root / "runtime"
            planted.symlink_to(target, target_is_directory=True)

            class RaceLostName:
                """The missed-lookup/lost-create race, deterministically: the first
                lookup misses, the concurrent creator (attacker) wins the name with a
                symlink, and safe_directory must validate - never chmod - first."""

                def __fspath__(self) -> str:
                    return str(planted)

                def lstat(self):
                    raise FileNotFoundError(planted)

                def mkdir(self, *args, **kwargs):
                    raise FileExistsError(planted)

            with self.assertRaises(common.GenericScaffoldingError):
                common.safe_directory(Path(RaceLostName()), create=True)   # type: ignore[arg-type]
            # the planted symlink's target was never chmod'ed through the link
            import stat as stat_module
            self.assertEqual(stat_module.S_IMODE(target.lstat().st_mode), 0o755,
                             "the refused entry's link target must keep its own mode")

    def test_a_permissive_preexisting_scaffold_directory_is_refused_not_healed(self):
        # (mutation survivor from the parked run: `own_creation` defaulting True) a
        # pre-existing but permissive scaffolding directory is refused by the identity
        # admission, never fchmod'ed back to the closed mode first: the mutant heals
        # 0755 to 0700 and accepts, this witness refuses and leaves the mode untouched.
        import stat as stat_module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            permissive = root / "work"
            permissive.mkdir(mode=0o755)
            with self.assertRaises(common.GenericScaffoldingError):
                common.safe_directory(permissive, create=True)
            self.assertEqual(
                stat_module.S_IMODE(permissive.lstat().st_mode), 0o755,
                "a refused scaffold keeps its own mode; it is never healed")

    def test_a_regular_file_at_a_scaffold_directory_name_is_refused(self):
        # (mutation survivor: the `or` in the identity admission) a descriptor opened at
        # the scaffold name is a plain file, not a directory: the admission refuses it,
        # whatever mode it carries - never treats a closed-mode file as the scaffold.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            impersonator = root / "runtime"
            impersonator.write_text("not a directory")
            impersonator.chmod(0o600)
            for create in (True, False):
                with self.subTest(mode="<closed-mode too>", create=create):
                    impersonator.chmod(0o700)   # the kill witness: closed mode, still a file
                    with self.assertRaises(common.GenericScaffoldingError):
                        common.safe_directory(impersonator, create=create)
                    impersonator.chmod(0o600)
                with self.subTest(mode="read-only", create=create), self.assertRaises(
                        common.GenericScaffoldingError):
                    common.safe_directory(impersonator, create=create)

    def test_an_owner_stripping_umask_refuses_the_created_entry_unfixed(self):
        # (re-review 3, blocking 1) there is no mode change on scaffolding at all:
        # the mask can only remove bits, so a mask that strips owner-write (0o177)
        # leaves the fresh component at 0600 and the walk refuses it — never fchmod'ed
        # back to the closed mode. Masks that only strip group/other bits (0o077)
        # leave the requested 0700 intact and the creation succeeds. The mask itself
        # survives every creation and refusal untouched.
        import stat as stat_module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            ambient = os.umask(0o077)
            self.addCleanup(lambda: os.umask(ambient))
            good = root / "group-strip"
            common.safe_directory(good, create=True)
            self.assertEqual(os.umask(0o177), 0o077,
                             "the process mask is unchanged through the creation")
            refused = root / "owner-strip"
            with self.assertRaises(common.GenericScaffoldingError):
                common.safe_directory(refused, create=True)
            self.assertEqual(os.umask(0o177), 0o177,
                             "no umask window: the mask survives every refusal")
            self.assertEqual(stat_module.S_IMODE(good.lstat().st_mode), common.MODE_DIR)
            self.assertEqual(stat_module.S_IMODE(refused.lstat().st_mode), 0o600,
                             "the wrongly-masked creation is left exactly as the mask made it")

    def test_a_mask_that_hides_the_fresh_directory_refuses_without_changing_the_mask(self):
        # (re-review 3, blocking 1) a mask that strips the owner-read bit (0o777) makes
        # the fresh directory unopenable: the scaffolding is refused with
        # sandbox-scaffolding-unsafe — the mask is never zeroed, no helper process runs,
        # no path-based permission change happens — and the unusable entry is left in
        # place: this code never removes anything by name either.
        import stat as stat_module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            previous = os.umask(0o777)
            self.addCleanup(lambda: os.umask(previous))
            fresh = root / "runtime"
            with self.assertRaises(common.GenericScaffoldingError):
                common.safe_directory(fresh, create=True)
            self.assertEqual(os.umask(previous), 0o777,
                             "no umask window: the mask survives every refusal")
            self.assertTrue(fresh.exists(),
                             "the entry is never removed by name, fixed, or chmod'ed")
            self.assertEqual(stat_module.S_IMODE(fresh.lstat().st_mode), 0o000,
                             "the unopenable entry keeps exactly the mode the mask gave it")

    def test_created_components_never_touch_the_process_mask_or_a_stranger(self):
        # (re-review 2, blocking 1) the controlled-thread result, made deterministic: a
        # file created inside the creation window — by any thread — must inherit the
        # ambient 0o077 mask (0600, not 0666), an intermediate parent must be created at
        # the closed mode through the descriptor (never a 0777 default-mode parent), and
        # the process mask must be exactly what it was. The old zero-umask window and the
        # old parents=True walk both fail here.
        import stat as stat_module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            leaf = root / "missing-parent" / "closed-leaf"
            strangers: list[int] = []
            real_mkdir = os.mkdir

            def mkdir_with_a_stranger_inside(path, *args, **kwargs):
                # a file created during the scaffolding window: under the old
                # os.umask(0) window it lands at 0666, under the ambient mask at 0600
                if not strangers:
                    stranger = root / "stranger-file"
                    stranger.write_text("x")
                    strangers.append(stat_module.S_IMODE(stranger.stat().st_mode))
                return real_mkdir(path, *args, **kwargs)

            previous = None
            try:
                previous = os.umask(0o077)
                with patch("os.mkdir", mkdir_with_a_stranger_inside):
                    common.safe_directory(leaf, create=True)
                observed = os.umask(previous)
                previous = None
            finally:
                if previous is not None:
                    os.umask(previous)
            self.assertEqual(strangers, [0o600],
                             "a mask-changing creation window is visible in a stranger's file")
            self.assertEqual(observed, 0o077, "the process mask was never changed")
            self.assertEqual(
                stat_module.S_IMODE((root / "missing-parent").lstat().st_mode), common.MODE_DIR,
                "intermediates are created relative to opened parents at the closed mode")
            self.assertEqual(stat_module.S_IMODE(leaf.lstat().st_mode), common.MODE_DIR)

    def test_a_racing_competitor_is_never_chmodded_back_into_admission(self):
        # (mutation Survivor: the lost-create branch must validate the winner it never
        # created, never fchmod it) the race is made deterministic: the walker's own
        # first open misses, and its mkdir is diverted to create a permissive 0755
        # competitor and then lose to it. The winner keeps its own mode and is refused;
        # a mutant that adopts the winner as its own creation heals it to 0700 and
        # accepts, and both mutants of the `created` flag fail here.
        import stat as stat_module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            work = root / "work"
            real_mkdir = os.mkdir

            def mkdir_that_loses_the_race(path, mode=0o777, **kwargs):
                # the competitor wins the name with a permissive directory, and this
                # caller is told it lost: a first-initializer race, deterministically
                real_mkdir(path, common.MODE_DIR, **kwargs)
                os.chmod(path, 0o755, dir_fd=kwargs.get("dir_fd"))
                raise FileExistsError(path)

            with patch("os.mkdir", mkdir_that_loses_the_race):
                with self.assertRaises(common.GenericScaffoldingError):
                    common.safe_directory(work, create=True)
            self.assertEqual(stat_module.S_IMODE(work.lstat().st_mode), 0o755,
                             "the competitor's permissive mode was never chmod'ed away")
            with self.assertRaises(common.GenericScaffoldingError):
                common.safe_directory(work, create=False)

    def test_a_component_owned_by_another_user_is_refused(self):
        # (mutation survivor: the `(final or created) and st_uid != os.getuid()` guard)
        # a scaffold component another user owns - the planted root-owned directory
        # here, a directory whose fresh creation is owned by "someone else" from the
        # walker's point of view - is refused on identity, never adopted and never
        # chmod'ed, whether it was found pre-existing or was lost by create. The other
        # user is simulated through the identity comparison, so the block needs no
        # privileged setup.
        real = os.getuid()
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            preexisting = root / "work"
            preexisting.mkdir(mode=common.MODE_DIR)
            with patch.object(os, "getuid", lambda: real + 1):
                with self.assertRaises(common.GenericScaffoldingError):
                    common.safe_directory(preexisting, create=False)   # found pre-existing
                with self.assertRaises(common.GenericScaffoldingError):
                    common.safe_directory(root / "runtime", create=True)   # freshly created

    def test_a_closed_component_of_the_filesystem_root_is_never_a_scaffold(self):
        # (mutation survivor: the empty-component guard) the filesystem root itself is
        # the anchor of every walk, never a scaffold: a path with no components must
        # be refused before the walk starts, or the walker would open the root anchor
        # and return it as verified scaffolding.
        with self.assertRaises(common.GenericScaffoldingError):
            common.safe_directory(Path("/"), create=False)
        with self.assertRaises(common.GenericScaffoldingError):
            common.safe_directory(Path("/"), create=True)

    def test_a_replacement_after_a_successful_mkdir_is_refused_and_left_alone(self):
        # (re-review 3, blocking 1) a successful mkdir by name does not establish that
        # the opened inode is the one created: between them, the fresh component is
        # moved aside and a same-owner 0755 replacement stands at the name. The walk
        # refuses, the replacement's mode and content are left untouched, the moved
        # entry is left alone, and no child is created inside the impostor. The old
        # code chmod'ed the replacement to 0700 and built the scaffold inside it.
        import stat as stat_module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            name = root / "swap" / "child"
            real_mkdir = os.mkdir

            def mkdir_then_replace(path, mode=0o777, **kwargs):
                real_mkdir(path, common.MODE_DIR, **kwargs)
                swapped = Path(root) / Path(path).name
                if swapped == root / "swap":
                    swapped.rename(root / "moved-aside")
                    real_mkdir(swapped)
                    os.chmod(swapped, 0o755)

            with patch("os.mkdir", mkdir_then_replace):
                with self.assertRaises(common.GenericScaffoldingError):
                    common.safe_directory(name, create=True)
            replacement = root / "swap"
            self.assertEqual(stat_module.S_IMODE(replacement.lstat().st_mode), 0o755,
                             "the same-owner replacement is refused, never chmod'ed")
            self.assertFalse(list(replacement.iterdir()),
                             "nothing was ever created inside the impostor")
            self.assertTrue((root / "moved-aside").is_dir(),
                             "the moved-aside creation is left alone: never removed by name")

    def test_sandbox_file_verification_reads_one_descriptor_and_names_it(self):
        # (re-review 3, blocking 2) one descriptor owns the metadata checks and the
        # bounded read; an entry substituted at the name between the two — Sol's hard
        # link carrying the very same bytes — is neither read as the record nor
        # reported: the call refuses, and the substitute keeps exactly its own mode.
        import stat as stat_module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            record = root / "record.json"
            payload = b'{\n  "exact": "bytes"\n}\n'
            record.write_bytes(payload)
            record.chmod(common.MODE_FILE)
            self.assertEqual(common.safe_file(record, cap=len(payload)), payload)

            unrelated = root / "unrelated-0644"
            unrelated.write_bytes(payload)
            unrelated.chmod(0o644)
            substituted: list[int] = []
            real_read = os.read

            def read_then_substitute(descriptor, size):
                # between the metadata checks and the bytes: the name is replaced by a
                # hard link to an unrelated 0644 file carrying the same bytes
                if not substituted:
                    substituted.append(descriptor)
                    record.unlink()
                    os.link(unrelated, record)
                return real_read(descriptor, size)

            with patch("os.read", read_then_substitute):
                with self.assertRaises(common.GenericScaffoldingError):
                    common.safe_file(record, cap=len(payload))
            self.assertEqual(stat_module.S_IMODE(unrelated.lstat().st_mode), 0o644,
                             "the substitute is never chmod'ed through the swapped name")
            self.assertEqual(record.stat().st_ino, unrelated.stat().st_ino,
                             "the substitution is still in place, left untouched")
            # the next checked read refuses the substituted entry on its own
            # identity: mode and link count, not today's bytes
            with self.assertRaises(common.GenericScaffoldingError):
                common.safe_file(record, cap=len(payload))

            # a symbolic link at the control name is refused without following it:
            # the target keeps its mode and is never read through the link
            linked = root / "linked.json"
            target = root / "the-target"
            target.write_bytes(payload)
            target.chmod(common.MODE_FILE)
            linked.symlink_to(target)
            with self.assertRaises(common.GenericScaffoldingError):
                common.safe_file(linked, cap=len(payload))
            self.assertEqual(common.safe_file(target, cap=len(payload)),
                             payload, "the target was never touched")

            # a named pipe at the control name is refused without blocking: the open is
            # non-blocking and the type check refuses anything not a regular file
            pipe = root / "pipe.json"
            os.mkfifo(pipe, common.MODE_FILE)
            refusals: list[BaseException] = []

            def call_pipe(pipe=pipe, refusals=refusals):
                try:
                    common.safe_file(pipe, cap=len(payload))
                except common.GenericScaffoldingError as exc:
                    refusals.append(exc)

            worker = threading.Thread(target=call_pipe)
            worker.start()
            worker.join(timeout=5.0)
            self.assertFalse(worker.is_alive(),
                             "the named pipe parked the verified read: the open must be "
                             "non-blocking and refuse a non-regular-file entry")
            self.assertEqual(len(refusals), 1)

            # a multi-chunk document round-trips exactly (the bounded read loop), and an
            # entry over its cap refuses without revealing how much it holds
            large = root / "large.json"
            big = payload * (128 * 1024 // len(payload) + 1)   # several read chunks
            large.write_bytes(big)
            large.chmod(common.MODE_FILE)
            self.assertEqual(common.safe_file(large, cap=len(big)), big)
            with self.assertRaises(common.GenericScaffoldingError):
                common.safe_file(large, cap=len(big) - 1)

    def test_a_second_name_linked_to_the_validated_inode_during_the_read_is_refused(self):
        # (re-review 4, non-blocking 1) a hard link added to the very inode being
        # validated and read keeps the bytes, the device, the inode and the size —
        # only the link count changes. The stable-identity confirmation after the
        # read must compare it: the bytes at a double-named record are refused
        # before they are reported, nothing on disk is changed, and the next checked
        # read refuses the double-named inode by its own identity.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            record = root / "record.json"
            payload = b'{\n  "exact": "bytes"\n}\n'
            record.write_bytes(payload)
            record.chmod(common.MODE_FILE)
            linked_name = root / "record-linked.json"
            added: list[int] = []
            real_read = os.read

            def read_then_link(descriptor, size):
                # during the read: a second name is linked to the validated inode
                if not added:
                    added.append(descriptor)
                    os.link(record, linked_name)
                return real_read(descriptor, size)

            with patch("os.read", read_then_link):
                with self.assertRaises(common.GenericScaffoldingError):
                    common.safe_file(record, cap=len(payload))
            self.assertEqual(linked_name.stat().st_ino, record.stat().st_ino,
                             "the refusal changed nothing: both names address one inode")
            self.assertEqual(record.read_bytes(), payload)
            # the next checked read refuses the now double-named inode on its own
            with self.assertRaises(common.GenericScaffoldingError):
                common.safe_file(record, cap=len(payload))

    def test_a_named_pipe_at_a_scaffold_name_is_refused_without_blocking(self):
        # (re-review 2, blocking 3) a named pipe parked at the scaffold name must be
        # refused promptly: the directory-only open rejects a non-directory kernel-side
        # instead of waiting for a reader. The call runs on its own thread so a blocked
        # worker is a bounded failure, not a parked suite.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            impostor = root / "runtime"
            os.mkfifo(impostor, 0o700)
            for create in (True, False):
                with self.subTest(create=create):
                    refusals: list[BaseException] = []

                    def call(impostor=impostor, create=create, refusals=refusals):
                        try:
                            common.safe_directory(impostor, create=create)
                        except common.GenericScaffoldingError as exc:
                            refusals.append(exc)

                    worker = threading.Thread(target=call)
                    worker.start()
                    worker.join(timeout=5.0)
                    self.assertFalse(worker.is_alive(),
                                     "the named pipe parked the scaffolding open: a "
                                     "non-directory must be refused without blocking")
                    self.assertEqual(len(refusals), 1)

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
