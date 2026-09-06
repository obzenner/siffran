#!/usr/bin/env python3
"""Committed regression suite for the machine-local RunRepository adapter (ADR-31).

Run: python3 plugins/empirica/tests/test_state_adapter.py   (stdlib only, no pytest dependency)
Exit 0 = all pass; 1 = at least one failed.

These are ADR-31's confirmation tests for the *operational-state* half: they prove concurrent CAS
across real OS processes, generation isolation, worktree-unified project identity, 0600 permissions,
the absent/corrupt distinction, symlink refusal, and — the property that motivated the whole split —
that an adapter run never writes into the project repository.

Every test isolates the home namespace by pointing ``EMPIRICA_HOME`` at a fresh temp dir, so nothing
here touches a developer's real ``~/.empirica-plugin``.
"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
PLUGIN = HERE.parent  # plugins/empirica — makes `core` and `adapters` importable as packages
sys.path.insert(0, str(PLUGIN))

from adapters.state import (  # noqa: E402
    FilesystemRunRepository,
    GenerationAllocator,
    SymlinkRefused,
    empirica_home,
    project_id,
    run_id,
)
from adapters.state import fsio  # noqa: E402
from application.state import OperationalState  # noqa: E402
from core.records import ABSENT, Conflict, Corrupt, Present, Revision, RunKey  # noqa: E402

ACTIVE = {"status": "active", "n": 0}
TERMINAL = {"status": "converged", "n": 7}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git in an isolated identity so tests do not depend on the developer's git config."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=True, env=env)


class _Isolated(unittest.TestCase):
    """Base fixture: a private home dir wired through ``EMPIRICA_HOME``, restored on teardown."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self._saved = {k: os.environ.get(k) for k in ("EMPIRICA_HOME", "HOME")}
        os.environ["EMPIRICA_HOME"] = str(self.home)
        self.repo = FilesystemRunRepository()
        self.key = RunKey("proj", "runA", 1)

    def tearDown(self) -> None:
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._tmp.cleanup()


class TestHomeResolution(_Isolated):
    def test_env_override_wins(self):
        self.assertEqual(empirica_home(), self.home)

    def test_default_is_dot_empirica_plugin(self):
        os.environ.pop("EMPIRICA_HOME", None)
        os.environ["HOME"] = str(Path(self._tmp.name) / "fakehome")
        self.assertEqual(empirica_home(), Path(os.environ["HOME"]) / ".empirica-plugin")


class TestReadAbsentCorruptPresent(_Isolated):
    def test_absent_is_a_value_not_an_error(self):
        self.assertIs(self.repo.read(self.key), ABSENT)

    def test_present_after_create(self):
        rev = self.repo.create(self.key, ACTIVE)
        read = self.repo.read(self.key)
        self.assertIsInstance(read, Present)
        self.assertEqual(read.value, ACTIVE)
        self.assertEqual(read.revision, rev)

    def test_corrupt_is_distinct_from_absent(self):
        self.repo.create(self.key, ACTIVE)
        path = self.repo._doc_path(self.key)
        path.write_bytes(b"{ this is not json")
        read = self.repo.read(self.key)
        self.assertIsInstance(read, Corrupt)
        self.assertIsNot(read, ABSENT)

    def test_non_object_json_is_corrupt(self):
        self.repo.create(self.key, ACTIVE)
        self.repo._doc_path(self.key).write_bytes(b"[1, 2, 3]")
        self.assertIsInstance(self.repo.read(self.key), Corrupt)

    def test_non_finite_constant_is_corrupt(self):
        self.repo.create(self.key, ACTIVE)
        self.repo._doc_path(self.key).write_bytes(b'{"n": Infinity}')
        self.assertIsInstance(self.repo.read(self.key), Corrupt)


class TestCompareAndSet(_Isolated):
    def test_create_then_cas_roundtrip(self):
        rev0 = self.repo.create(self.key, {"status": "active", "n": 0})
        rev1 = self.repo.compare_and_set(self.key, {"status": "active", "n": 1}, rev0)
        self.assertNotEqual(rev0, rev1)
        self.assertEqual(self.repo.read(self.key).value["n"], 1)

    def test_first_writer_wins_on_create(self):
        self.repo.create(self.key, ACTIVE)
        with self.assertRaises(Conflict):
            self.repo.create(self.key, ACTIVE)

    def test_create_over_corrupt_conflicts(self):
        self.repo.create(self.key, ACTIVE)
        self.repo._doc_path(self.key).write_bytes(b"garbage")
        with self.assertRaises(Conflict):
            self.repo.create(self.key, ACTIVE)

    def test_cas_stale_revision_conflicts_and_leaves_storage(self):
        rev0 = self.repo.create(self.key, {"status": "active", "n": 0})
        self.repo.compare_and_set(self.key, {"status": "active", "n": 1}, rev0)
        with self.assertRaises(Conflict):
            self.repo.compare_and_set(self.key, {"status": "active", "n": 99}, rev0)
        self.assertEqual(self.repo.read(self.key).value["n"], 1)  # untouched

    def test_cas_on_absent_conflicts(self):
        with self.assertRaises(Conflict):
            self.repo.compare_and_set(self.key, ACTIVE, Revision("whatever"))

    def test_cas_on_corrupt_conflicts(self):
        self.repo.create(self.key, ACTIVE)
        self.repo._doc_path(self.key).write_bytes(b"garbage")
        with self.assertRaises(Conflict):
            self.repo.compare_and_set(self.key, ACTIVE, Revision("whatever"))


def _cas_increment_worker(home: str, key: RunKey, iterations: int) -> None:
    """Increment ``n`` ``iterations`` times with read/CAS/retry. Runs in a forked child process so
    the lock and CAS face genuine cross-process contention, not just threads under the GIL."""
    repo = FilesystemRunRepository(Path(home))
    for _ in range(iterations):
        while True:
            read = repo.read(key)
            updated = dict(read.value)
            updated["n"] += 1
            try:
                repo.compare_and_set(key, updated, read.revision)
                break
            except Conflict:
                continue


class TestConcurrentCasAcrossProcesses(_Isolated):
    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    def test_no_lost_updates_under_process_contention(self):
        procs, iters = 6, 40
        self.repo.create(self.key, {"status": "active", "n": 0})
        pids = []
        for _ in range(procs):
            pid = os.fork()
            if pid == 0:  # child
                try:
                    _cas_increment_worker(str(self.home), self.key, iters)
                    os._exit(0)
                except BaseException:  # pragma: no cover - child failure surfaces via exit code
                    os._exit(1)
            pids.append(pid)
        failures = [os.waitpid(pid, 0)[1] for pid in pids]
        self.assertTrue(all(status == 0 for status in failures), "a worker process failed")
        # Every one of procs*iters increments landed: the lock + content-hash CAS lost nothing.
        self.assertEqual(self.repo.read(self.key).value["n"], procs * iters)


class TestGenerationAllocation(_Isolated):
    def setUp(self) -> None:
        super().setUp()
        self.alloc = GenerationAllocator(self.repo)
        self.pid, self.rid = "projG", "runG"

    def test_fresh_run_is_generation_one(self):
        self.assertEqual(self.alloc.allocate(self.pid, self.rid).generation, 1)

    def test_active_run_reopens_same_generation(self):
        key1 = self.alloc.allocate(self.pid, self.rid)
        self.repo.create(key1, {"status": "active", "n": 0})
        again = self.alloc.allocate(self.pid, self.rid)
        self.assertEqual(again.generation, key1.generation)

    def test_terminal_run_starts_next_without_overwrite(self):
        key1 = self.alloc.allocate(self.pid, self.rid)
        self.repo.create(key1, TERMINAL)
        key2 = self.alloc.allocate(self.pid, self.rid)
        self.assertEqual(key2.generation, key1.generation + 1)
        # The terminal generation is untouched — rollback stays possible (ADR-31).
        self.assertEqual(self.repo.read(key1).value, TERMINAL)
        self.assertIs(self.repo.read(key2), ABSENT)

    def test_corrupt_run_starts_next_without_overwrite(self):
        key1 = self.alloc.allocate(self.pid, self.rid)
        self.repo.create(key1, ACTIVE)
        self.repo._doc_path(key1).write_bytes(b"corrupt")
        key2 = self.alloc.allocate(self.pid, self.rid)
        self.assertEqual(key2.generation, key1.generation + 1)
        self.assertIsInstance(self.repo.read(key1), Corrupt)  # left as-is

    def test_generation_is_isolated(self):
        key1 = RunKey(self.pid, self.rid, 1)
        key2 = RunKey(self.pid, self.rid, 2)
        self.repo.create(key1, {"status": "active", "n": 1})
        # A write under gen 1 is invisible to gen 2 — different storage slice.
        self.assertIs(self.repo.read(key2), ABSENT)


class TestProjectIdentity(_Isolated):
    def test_worktrees_share_identity(self):
        root = Path(self._tmp.name)
        main = root / "main"
        main.mkdir()
        _git(main, "init", "-q")
        _git(main, "commit", "-q", "--allow-empty", "-m", "seed")
        wt = root / "wt"
        _git(main, "worktree", "add", "-q", str(wt))
        self.assertEqual(project_id(main), project_id(wt))
        # A subdirectory of the worktree resolves to the same project too.
        sub = wt / "nested"
        sub.mkdir()
        self.assertEqual(project_id(sub), project_id(main))

    def test_independent_repos_differ(self):
        root = Path(self._tmp.name)
        a, b = root / "a", root / "b"
        a.mkdir()
        b.mkdir()
        _git(a, "init", "-q")
        _git(b, "init", "-q")
        self.assertNotEqual(project_id(a), project_id(b))

    def test_non_git_anchor_is_stable_and_distinct(self):
        plain = Path(self._tmp.name) / "plain"
        plain.mkdir()
        self.assertEqual(project_id(plain), project_id(plain))  # deterministic
        other = Path(self._tmp.name) / "other"
        other.mkdir()
        self.assertNotEqual(project_id(plain), project_id(other))

    def test_ids_are_single_safe_segments(self):
        for value in ("../escape", "a/b/c", "", "..", "  spaces  ", "weird:*id"):
            self.assertNotIn("/", run_id(value))
            self.assertNotIn("..", run_id(value).split("-"))
            self.assertTrue(run_id(value))


class TestPermissions(_Isolated):
    def test_doc_and_lock_are_0600(self):
        self.repo.create(self.key, ACTIVE)
        doc = self.repo._doc_path(self.key)
        self.assertEqual(stat.S_IMODE(doc.stat().st_mode), 0o600)
        lock = doc.with_name(doc.name + ".lock")
        self.assertTrue(lock.exists())
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)


class TestSymlinkRefusal(_Isolated):
    def test_read_refuses_symlinked_target(self):
        real = Path(self._tmp.name) / "real.json"
        real.write_text('{"status": "active"}')
        path = self.repo._doc_path(self.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(real, path)
        with self.assertRaises(SymlinkRefused):
            self.repo.read(self.key)

    def test_write_refuses_symlinked_target(self):
        real = Path(self._tmp.name) / "real.json"
        real.write_text("{}")
        path = self.repo._doc_path(self.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(real, path)
        with self.assertRaises(SymlinkRefused):
            fsio.atomic_write(path, b"{}")


class TestNoRepositoryWrites(_Isolated):
    def test_adapter_never_writes_into_the_project_repo(self):
        root = Path(self._tmp.name) / "repo"
        root.mkdir()
        _git(root, "init", "-q")
        (root / "file.txt").write_text("tracked\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "seed")

        before = sorted(p.relative_to(root).as_posix()
                        for p in root.rglob("*") if ".git" not in p.parts)

        pid, rid = project_id(root), run_id("session-xyz")
        alloc = GenerationAllocator(self.repo)
        key = alloc.allocate(pid, rid)
        rev = self.repo.create(key, {"status": "active", "n": 0})
        self.repo.compare_and_set(key, {"status": "converged", "n": 1}, rev)

        # The state lives under the isolated home, not the repo.
        self.assertTrue(str(self.repo._doc_path(key)).startswith(str(self.home)))
        after = sorted(p.relative_to(root).as_posix()
                       for p in root.rglob("*") if ".git" not in p.parts)
        self.assertEqual(before, after, "adapter created files inside the project tree")
        self.assertEqual(_git(root, "status", "--porcelain").stdout, "",
                         "adapter dirtied the project working tree")


class TestPreFixDocumentDecode(unittest.TestCase):
    """The budget fix added three OperationalState fields (working_passes, last_stop_digest,
    last_progress_ts). Refuting obs #7: a document written by an OLDER build — one that lacks those
    fields — must still decode (to the None defaults) and still be a usable, active run, so the
    backward-compatible-decode invariant holds and merely upgrading never wedges a live run."""

    def _prefix_doc(self) -> dict:
        """A valid stored document with the three post-fix fields removed (as a pre-fix build left
        it). Everything else is exactly what OperationalState.new writes."""
        doc = OperationalState.new(goal="g", max_passes=8, max_spawns=None,
                                   theta=0.8, modes=None).encode()
        for absent in ("working_passes", "last_stop_digest", "last_progress_ts"):
            doc.pop(absent)
        return doc

    def test_prefix_document_decodes_with_none_defaults(self):
        state = OperationalState.decode(self._prefix_doc())
        self.assertIsNotNone(state, "a pre-fix document must decode, not read as corrupt")
        self.assertIsNone(state.working_passes)
        self.assertIsNone(state.last_stop_digest)
        self.assertIsNone(state.last_progress_ts)

    def test_prefix_document_still_evaluates_with_ceiling_fallback(self):
        # No working cap yet -> the pass budget falls back to the a-priori max_passes ceiling, so a
        # pre-fix run keeps evaluating under the new service rather than wedging on absent fields.
        state = OperationalState.decode(self._prefix_doc())
        self.assertTrue(state.is_active)
        self.assertEqual(state.working_passes or state.max_passes, state.max_passes)

    def test_new_fields_roundtrip_through_encode_decode(self):
        upgraded = OperationalState.decode(self._prefix_doc()).evolve(
            working_passes=3, last_stop_digest="a" * 64, last_progress_ts=1234.5)
        redecoded = OperationalState.decode(upgraded.encode())
        self.assertEqual(redecoded.working_passes, 3)
        self.assertEqual(redecoded.last_stop_digest, "a" * 64)
        self.assertEqual(redecoded.last_progress_ts, 1234.5)

    def test_corrupt_new_fields_fail_closed_to_none(self):
        # A corrupt (wrong-type) field reads as absent — the conservative bound (mirrors _opt_int).
        doc = self._prefix_doc()
        doc.update({"working_passes": "eight", "last_stop_digest": 5, "last_progress_ts": "soon"})
        state = OperationalState.decode(doc)
        self.assertIsNotNone(state)
        self.assertIsNone(state.working_passes)
        self.assertIsNone(state.last_stop_digest)
        self.assertIsNone(state.last_progress_ts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
