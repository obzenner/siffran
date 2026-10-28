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
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main(verbosity=1)
