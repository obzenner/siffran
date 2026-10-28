#!/usr/bin/env python3
"""Committed regression suite for the write-once HITL document sidecar (ADR-0072, PR 05).

Run: python3 plugins/empirica/tests/test_hitl_sidecar.py   (stdlib only, no pytest dependency)
Exit 0 = all pass; 1 = at least one failed.

ADR-0072's write-first flow step 2: each human decision's document is written exactly once beside
the run's state, at ``gen-<n>/hitl/<sha256-prefix-hex>.json``, mode 0444, so a later read can prove
the bytes the human decided on are the bytes the core rendered. These tests prove the write-once
and atomic mechanics: one file per digest, same-bytes idempotence, different-bytes refusal,
symlink refusal at every writable path, crash-temp tolerance, thread concurrency, and the read's
verify-or-refuse.

Every test isolates the home namespace by pointing ``EMPIRICA_HOME`` at a fresh temp dir, so
nothing here touches a developer's real ``~/.empirica-plugin`` or anything outside it.
"""
import errno
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
PLUGIN = HERE.parent  # plugins/empirica — makes `core` and `adapters` importable as packages
sys.path.insert(0, str(PLUGIN))

from adapters.state import hitl as hitl_sidecar  # noqa: E402
from adapters.state import SymlinkRefused  # noqa: E402
from core import hitl  # noqa: E402
from core.records import RunKey  # noqa: E402

DATA = hitl.render({"schema": "empirica.hitl/1", "kind": "configuration", "decided": "once"})


class _Isolated(unittest.TestCase):
    """Base fixture: a private home dir wired through ``EMPIRICA_HOME``, restored on teardown."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        os.environ["EMPIRICA_HOME"] = str(self.home)
        self.store = hitl_sidecar.HitlDocuments()
        self.key = RunKey("proj", "runA", 1)

    def tearDown(self) -> None:
        os.environ.pop("EMPIRICA_HOME", None)
        self._tmp.cleanup()

    def documents(self) -> list[Path]:
        return sorted(self.store.dir(self.key).glob("*.json"))


class TestWriteOnce(_Isolated):
    def test_path_shape_digest_mode_and_bytes(self):
        path, digest = self.store.write(self.key, DATA)
        self.assertEqual(path, self.home / "projects" / "proj" / "runs" / "runA" / "gen-1"
                         / "hitl" / f"{digest[len('sha256:'): ]}.json")
        self.assertEqual(digest, hitl.digest(DATA))
        self.assertEqual(self.store.path(self.key, digest), path)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o444)
        self.assertEqual(path.read_bytes(), DATA)

    def test_same_bytes_twice_is_idempotent(self):
        path, digest = self.store.write(self.key, DATA)
        before = path.stat()
        again_path, again_digest = self.store.write(self.key, DATA)
        self.assertEqual((again_path, again_digest), (path, digest))
        after = path.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertEqual(len(self.documents()), 1)

    def test_different_bytes_at_a_planted_digest_name_is_refused(self):
        digest = hitl.digest(DATA)
        hitl_dir = self.store.dir(self.key)
        hitl_dir.mkdir(parents=True)
        final = hitl_dir / f"{digest[7:]}.json"
        planted = b"{ planted by an attacker }"
        final.write_bytes(planted)  # a file at the digest name holding different bytes
        with self.assertRaises(hitl_sidecar.DocumentMismatch):
            self.store.write(self.key, DATA)
        self.assertEqual(final.read_bytes(), planted)  # the planted file is left untouched

    def test_two_documents_side_by_side(self):
        other = hitl.render({"schema": "empirica.hitl/1", "kind": "evidence", "n": 2})
        p1, d1 = self.store.write(self.key, DATA)
        p2, d2 = self.store.write(self.key, other)
        self.assertNotEqual(p1, p2)
        self.assertEqual(self.store.read(self.key, d1), DATA)
        self.assertEqual(self.store.read(self.key, d2), other)
        self.assertEqual(len(self.documents()), 2)

    def test_unsafe_key_segment_is_refused(self):
        with self.assertRaises(ValueError):
            self.store.write(RunKey("../escape", "runA", 1), DATA)


class TestSymlinkRefusal(_Isolated):
    def test_symlinked_final_path_is_refused_and_outside_is_untouched(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        target = outside / "stolen.json"
        target.write_bytes(b"do not touch")
        hitl_dir = self.store.dir(self.key)
        hitl_dir.mkdir(parents=True)
        final = hitl_dir / f"{hitl.digest(DATA)[7:]}.json"
        final.symlink_to(target)
        with self.assertRaises(SymlinkRefused):
            self.store.write(self.key, DATA)
        self.assertEqual(target.read_bytes(), b"do not touch")
        self.assertEqual(list(outside.iterdir()), [target])  # nothing new written outside the home

    def test_symlinked_hitl_directory_is_refused(self):
        outside = Path(self._tmp.name) / "outside-dir"
        outside.mkdir()
        (outside / "stolen.json").write_bytes(b"stolen")
        gen = self.home / "projects" / "proj" / "runs" / "runA" / "gen-1"
        gen.mkdir(parents=True)
        (gen / "hitl").symlink_to(outside)
        with self.assertRaises(SymlinkRefused):
            self.store.write(self.key, DATA)
        self.assertEqual(os.listdir(outside), ["stolen.json"])

    def test_symlinked_generation_directory_is_refused(self):
        outside = Path(self._tmp.name) / "outside-gen"
        outside.mkdir()
        runs = self.home / "projects" / "proj" / "runs"
        runs.mkdir(parents=True)
        (runs / "runA").mkdir()
        (runs / "runA" / "gen-1").symlink_to(outside)
        with self.assertRaises(SymlinkRefused):
            self.store.write(self.key, DATA)
        self.assertEqual(os.listdir(outside), [])

    def test_symlinked_final_path_is_refused_on_read(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        target = outside / "stolen.json"
        target.write_bytes(b"stolen")
        digest = hitl.digest(DATA)
        hitl_dir = self.store.dir(self.key)
        hitl_dir.mkdir(parents=True)
        (hitl_dir / f"{digest[7:]}.json").symlink_to(target)
        with self.assertRaises(SymlinkRefused):
            self.store.read(self.key, digest)


class TestCrashTempTolerance(_Isolated):
    def test_a_leftover_temp_file_neither_blocks_nor_masquerades(self):
        hitl_dir = self.store.dir(self.key)
        hitl_dir.mkdir(parents=True)
        crashed = hitl_dir / ".tmp.crashed.9j2x.json"
        crashed.write_bytes(b"a torn write from a simulated crash")
        path, digest = self.store.write(self.key, DATA)
        self.assertEqual(path.read_bytes(), DATA)
        self.assertNotEqual(path, crashed)
        self.assertEqual(self.store.read(self.key, digest), DATA)
        # the leftover is not mistaken for a document: only the digest-named file is one
        self.assertEqual([p for p in hitl_dir.iterdir() if p.is_file() and not p.name.startswith(".")],
                         [path])


class TestConcurrency(_Isolated):
    def test_many_threads_same_bytes_one_file(self):
        results: list = []
        failures: list = []
        barrier = threading.Barrier(8)

        def writer():
            try:
                barrier.wait()
                results.append(self.store.write(self.key, DATA))
            except BaseException as exc:  # noqa: BLE001  (record, don't lose)
                failures.append(exc)

        threads = [threading.Thread(target=writer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 8)
        self.assertEqual({(r[0], r[1]) for r in results},
                         {(self.store.path(self.key, hitl.digest(DATA)), hitl.digest(DATA))})
        self.assertEqual(len(self.documents()), 1)

    def test_concurrent_writers_over_a_planted_mismatch_all_refuse_and_clean_up(self):
        """All racing writers of a name that already holds different bytes fail closed; no writer
        leaves a temp file behind, and the planted bytes are never mixed into anything."""
        digest = hitl.digest(DATA)
        hitl_dir = self.store.dir(self.key)
        hitl_dir.mkdir(parents=True)
        final = hitl_dir / f"{digest[7:]}.json"
        planted = b"{ content that is not the document }"
        final.write_bytes(planted)
        outcomes = []

        def writer():
            try:
                self.store.write(self.key, DATA)
                outcomes.append("written")
            except hitl_sidecar.DocumentMismatch:
                outcomes.append("refused")

        threads = [threading.Thread(target=writer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(outcomes, ["refused"] * 8)
        self.assertEqual(final.read_bytes(), planted)
        self.assertEqual([p.name for p in hitl_dir.iterdir() if p.name.startswith(".tmp.")], [])


class TestRead(_Isolated):
    def test_read_returns_the_bytes_and_verifies_the_digest(self):
        _, digest = self.store.write(self.key, DATA)
        self.assertEqual(self.store.read(self.key, digest), DATA)

    def test_read_refuses_a_tampered_file(self):
        digest = hitl.digest(DATA)
        hitl_dir = self.store.dir(self.key)
        hitl_dir.mkdir(parents=True)
        final = hitl_dir / f"{digest[7:]}.json"
        final.write_bytes(b"{ tampered }")  # fresh bytes at the digest name
        with self.assertRaises(hitl_sidecar.DocumentMismatch):
            self.store.read(self.key, digest)

    def test_read_of_a_missing_document_is_none(self):
        self.assertIsNone(self.store.read(self.key, hitl.digest(DATA)))

    def test_read_refuses_a_non_regular_file(self):
        digest = hitl.digest(DATA)
        hitl_dir = self.store.dir(self.key)
        hitl_dir.mkdir(parents=True)
        (hitl_dir / f"{digest[7:]}.json").write_bytes(b"x")
        socket = hitl_dir / f"{digest[7:]}.json"
        socket.unlink()
        os.mkfifo(socket)
        try:
            with self.assertRaises(hitl_sidecar.DocumentMismatch):
                self.store.read(self.key, digest)
        finally:
            socket.unlink()

    def test_read_rejects_a_malformed_digest(self):
        with self.assertRaises(ValueError):
            self.store.read(self.key, "not-a-digest")
        with self.assertRaises(ValueError):
            self.store.path(self.key, "sha256:short")

class TestDigestNames(_Isolated):
    """Review-1 finding 14: a document's filename is the body of a ``sha256:`` digest — the prefix
    is part of the identity, not decoration the name of which is sliced off blindly."""

    def test_only_a_sha256_digest_names_a_document(self):
        self.assertEqual(self.store.path(self.key, "sha256:" + "0" * 64).name, "0" * 64 + ".json")

    def test_everything_else_is_refused_on_both_paths(self):
        for bad in ("md5:" + "0" * 64,
                    "0123456" + "0" * 64,
                    "sha256:" + "g" * 64,
                    "SHA256:" + "0" * 64,
                    "sha256:" + "0" * 64 + "0",
                    "sha256:" + "0" * 32,
                    "sha256:",
                    "",
                    None,
                    42,
                    b"sha256:" + b"0" * 64):
            with self.subTest(digest=repr(bad)):
                with self.assertRaises(ValueError):
                    self.store.path(self.key, bad)
                with self.assertRaises(ValueError):
                    self.store.read(self.key, bad)


class TestWriteDurability(_Isolated):
    """Review-1 finding 17: the published mode is durable with the bytes, and a first write's new
    directory entries survive a crash too."""

    def test_the_mode_is_set_before_the_bytes_are_synced(self):
        order: list[str] = []
        real_fchmod, real_fsync = os.fchmod, os.fsync

        def fchmod(fd, mode):
            order.append("fchmod")
            real_fchmod(fd, mode)

        def fsync(fd):
            order.append("fsync")
            real_fsync(fd)

        with mock.patch("os.fchmod", side_effect=fchmod), mock.patch("os.fsync", side_effect=fsync):
            path, digest = self.store.write(self.key, DATA)
        self.assertEqual(order[:2], ["fchmod", "fsync"])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o444)
        self.assertEqual(self.store.read(self.key, digest), DATA)

    def test_a_first_write_also_flushes_the_generation_directory(self):
        """The first write creates the ``hitl`` directory; its directory entry too must reach the
        disk, so the flushed set is the ``hitl`` directory and its parent generation directory."""
        flushed: list[Path] = []
        with mock.patch("adapters.state.fsio._fsync_dir",
                        side_effect=lambda directory: flushed.append(Path(directory))):
            path, _ = self.store.write(self.key, DATA)
        hitl_dir = path.parent
        generation = hitl_dir.parent
        self.assertIn(hitl_dir.absolute(), {p.absolute() for p in flushed})
        self.assertIn(generation.absolute(), {p.absolute() for p in flushed})
        # a second document in the established directory flushes the entry, not the parent again
        flushed.clear()
        other = hitl.render({"schema": "empirica.hitl/1", "kind": "evidence", "n": 3})
        with mock.patch("adapters.state.fsio._fsync_dir",
                        side_effect=lambda directory: flushed.append(Path(directory))):
            self.store.write(self.key, other)
        self.assertEqual([p.absolute() for p in flushed], [hitl_dir.absolute()])


class TestHardLinkSupport(_Isolated):
    """Review-1 finding 18: a filesystem without hard links fails with a named condition, so a host
    can tell "this storage cannot hold the sidecar" from "this document is corrupt"."""

    def test_link_unsupported_errors_become_a_named_error(self):
        for number, name in ((errno.EPERM, "EPERM"), (errno.EXDEV, "EXDEV"), (errno.EMLINK, "EMLINK")):
            with self.subTest(errno=name):
                def refused(src, dst, *, dir_fd=None, _number=number):
                    raise OSError(_number, f"link refused ({name})")

                with mock.patch("os.link", side_effect=refused):
                    with self.assertRaises(hitl_sidecar.HardLinkUnsupported):
                        self.store.write(self.key, DATA)
                self.assertEqual(self.documents(), [])  # nothing was published


class TestLinkRace(_Isolated):
    """Review item 6, sidecar: the ``os.link`` -> ``FileExistsError`` branch, made deterministic —
    the raced name's bytes alone decide between idempotent success and refusal."""

    def _race_plant(self, planted: bytes):
        def plant(src, dst, *, dir_fd=None):
            Path(dst).write_bytes(planted)  # exactly what a racing writer or attacker would leave
            raise FileExistsError(17, "the name was taken")

        with mock.patch("os.link", side_effect=plant):
            return self.store.write(self.key, DATA)

    def test_the_racer_publishing_the_same_bytes_is_idempotent(self):
        path, digest = self._race_plant(DATA)
        self.assertEqual(self.store.read(self.key, digest), DATA)  # idempotent success
        self.assertEqual([p.name for p in self.store.dir(self.key).iterdir()],
                         [f"{digest[len('sha256:'): ]}.json"])  # our temp was cleaned up

    def test_the_racer_publishing_other_bytes_fails_closed_and_cleans_up(self):
        planted = b"{ a hostile plant at the digest name }"
        with self.assertRaises(hitl_sidecar.DocumentMismatch):
            self._race_plant(planted)
        digest_name = f"{hitl.digest(DATA)[7:]}.json"
        self.assertEqual((self.store.dir(self.key) / digest_name).read_bytes(), planted)
        self.assertEqual([p.name for p in self.store.dir(self.key).iterdir()
                           if p.name.startswith(".tmp.")], [])


class TestFifoRefusal(_Isolated):
    """Review-1 finding 16: a FIFO swapped at the document path must be refused promptly — an
    unhardened ``open`` for read would block until a writer appears, hanging the run's dialog."""

    BOUND = 5.0  # seconds; the refusal must be orders of magnitude faster

    def _outcomes(self, call):
        outcome: dict = {}

        def guarded():
            try:
                outcome["result"] = call()
            except BaseException as exc:  # noqa: BLE001  (record, don't lose)
                outcome["error"] = exc

        worker = threading.Thread(target=guarded, daemon=True)
        worker.start()
        worker.join(self.BOUND)
        self.assertFalse(worker.is_alive(), "the FIFO refused the read by hanging on it")
        self.assertEqual(list(outcome), ["error"])
        return outcome["error"]

    def _plant_fifo_and_lie_about_lstat(self):
        """Plant a FIFO at the digest name and make the pre-open checks believe it is a regular
        file, so only the open itself separates a hardened read from a hang."""
        digest = hitl.digest(DATA)
        hitl_dir = self.store.dir(self.key)
        hitl_dir.mkdir(parents=True)
        fifo = hitl_dir / f"{digest[7:]}.json"
        os.mkfifo(fifo)
        return (fifo, digest, mock.patch("stat.S_ISREG", side_effect=lambda mode: True),
                mock.patch("stat.S_ISLNK", side_effect=lambda mode: False))

    def test_read_refuses_a_swapped_fifo_promptly(self):
        fifo, digest, regular_lies, link_lies = self._plant_fifo_and_lie_about_lstat()
        with regular_lies, link_lies:
            error = self._outcomes(lambda: self.store.read(self.key, digest))
        self.assertIsInstance(error, hitl_sidecar.DocumentMismatch)
        fifo.unlink()

    def test_the_existing_document_check_refuses_a_swapped_fifo_promptly(self):
        fifo, digest, regular_lies, link_lies = self._plant_fifo_and_lie_about_lstat()
        with regular_lies, link_lies:
            error = self._outcomes(lambda: self.store.write(self.key, DATA))
        self.assertIsInstance(error, hitl_sidecar.DocumentMismatch)
        fifo.unlink()


class TestHomeIsAbsolute(_Isolated):
    """Review-1 finding 19: Q4 decided the form shows an absolute path — so a relative
    ``EMPIRICA_HOME`` must become absolute the moment the store is built, without resolving
    symlinks (an absolute path is not a canonical path)."""

    def test_a_relative_home_publishes_absolute_paths(self):
        previous = os.getcwd()
        os.chdir(self._tmp.name)  # so "relhome" resolves against the test's own sandbox
        try:
            store = hitl_sidecar.HitlDocuments(Path("relhome"))
            path, digest = store.write(self.key, DATA)
            self.assertTrue(path.is_absolute())
            self.assertEqual(path, Path.cwd() / "relhome" / "projects" / "proj"
                             / "runs" / "runA" / "gen-1" / "hitl"
                             / f"{digest[len('sha256:'): ]}.json")
            self.assertEqual(store.read(self.key, digest), DATA)
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    unittest.main(verbosity=1)
