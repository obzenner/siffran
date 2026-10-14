"""The observer on the real filesystem store (contract v2): a cheap per-run version, one ref listing per repository per
poll, batched history reads equal to the single read, and a background indexer that requests never wait on.

The store is ``fs_store.build_fs_store``: runs driven through the bridge (spikes over distinct, overlapping files; some
stopped), cloned across 20 repositories with valid refs, a share of them drifted. What a pass costs here is counted
(git processes, state reads, hashes, projections); how it grows from n to 2n is ``test_observer_complexity.py`` and the
budgets at 1,000 runs ``test_observer_smoke.py`` (``make observer-smoke``)."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parents[1])]

from adapters.git.artifact_repo import GitArtifactRepository  # noqa: E402
from adapters.observer import indexer as indexer_module  # noqa: E402
from adapters.observer.source import StoreSource  # noqa: E402
from application.location import storage_id  # noqa: E402
from application.observer import Observer  # noqa: E402
from application.observer_index import utc_day  # noqa: E402
from core.records import RunKey  # noqa: E402
from fs_store import _git, build_fs_store, replace_run  # noqa: E402
from observer_store import BOUND, body  # noqa: E402

RUNS, REPOS = 60, 20  # three runs a repository: two active templates and a stopped one


_SHARED: dict = {}


def setUpModule():
    """One store for every case that only reads it (a case that changes a run builds its own)."""
    _SHARED["tmp"] = tempfile.TemporaryDirectory()
    _SHARED["store"] = build_fs_store(Path(_SHARED["tmp"].name), RUNS, workspaces=REPOS)


def tearDownModule():
    _SHARED["tmp"].cleanup()


class FsStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = _SHARED["store"]

    def test_the_fixture_is_valid_every_run_reads_whole(self):
        observer = Observer(StoreSource(self.store.home))
        summary = observer.summary()
        self.assertEqual(summary["coverage"], {"runs": RUNS, "read": RUNS, "unreadable": 0, "partial": 0, "pending": 0, "reindexing": 0})
        details = observer.details()
        self.assertEqual(len({d["now"] is None for d in details.values()}) >= 1, True)
        self.assertEqual({r["state"] for r in observer.rows()}, {"active", "stopped_residual"}, "active and ended")
        drifted = sum(bool(d["stale"]) for d in details.values())
        self.assertTrue(0 < drifted < RUNS, drifted)
        self.assertGreaterEqual(observer._drift.size(), REPOS * len(BOUND) - 20, "distinct files in every repository")

    def test_a_batched_history_read_is_the_single_read(self):
        repo = GitArtifactRepository(self.store.repo)
        heads = repo.ref_heads()
        keys = [k for k in self.store.keys if self.store.repo_of[k] == self.store.repo]
        commits = {heads[repo.ref_for(k)] for k in keys}
        batched = repo.read_listed(commits)
        self.assertEqual(set(batched), commits)
        for key in keys:
            self.assertEqual(batched[heads[repo.ref_for(key)]], repo.read(key))

    def test_what_a_batch_cannot_read_exactly_is_left_to_the_single_read(self):
        repo = GitArtifactRepository(self.store.repo)
        commit = repo.ref_heads()[repo.ref_for(self.store.keys[0])]
        tree = _git(self.store.repo, "rev-parse", f"{commit}^{{tree}}").strip()
        blob = _git(self.store.repo, "hash-object", "-w", "--stdin", stdin="not an artifact").strip()
        nested = _git(self.store.repo, "mktree", stdin=f"040000 tree {tree}\tnested\n").strip()
        odd = _git(self.store.repo, "mktree", stdin=f"100644 blob {blob}\tnot-a-path\n").strip()
        commits = {name: _git(self.store.repo, "commit-tree", t, "-m", name).strip()
                   for name, t in (("nested", nested), ("odd", odd))}
        self.assertEqual(repo.read_listed({tree, blob, "0" * len(commit), *commits.values()}), {},
                         "a tree, a blob, a missing object, a nested tree and an undecodable blob")

    def test_a_poll_lists_refs_once_per_repository_never_per_run_or_project(self):
        source = StoreSource(self.store.home)
        observer = Observer(source)
        observer.poll()
        source._signed.clear()  # as if a ref moved: the listing is taken again
        with mock.patch.object(GitArtifactRepository, "ref_heads", autospec=True,
                               side_effect=GitArtifactRepository.ref_heads) as listing, \
                mock.patch.object(GitArtifactRepository, "read", autospec=True,
                                  side_effect=GitArtifactRepository.read) as read:
            observer.poll()
        self.assertEqual(listing.call_count, REPOS, "once per repository")
        self.assertEqual(read.call_count, 0, "a warm poll reads no history")

    def test_a_warm_poll_runs_no_git_process_while_no_ref_moved(self):
        from adapters.git import artifact_repo
        observer = Observer(StoreSource(self.store.home))
        observer.poll()
        with mock.patch.object(artifact_repo.subprocess, "run", side_effect=AssertionError("git ran")), \
                mock.patch.object(artifact_repo.subprocess, "Popen", side_effect=AssertionError("git ran")):
            observer.poll()

    def test_a_moved_ref_is_seen_whether_refs_are_loose_or_packed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = build_fs_store(Path(tmp), 4, workspaces=1)
            source = StoreSource(store.home)
            repo = GitArtifactRepository(store.repo)
            first, second = store.keys[0], store.keys[1]
            for packed in (False, True, False):
                if packed:
                    _git(store.repo, "pack-refs", "--all")
                source.refresh()
                before = source.history_revision(first)
                target = source.history_revision(second) if before != source.history_revision(second) else \
                    source.history_revision(store.keys[2])
                _git(store.repo, "update-ref", repo.ref_for(first), target)
                source.refresh()
                self.assertEqual(source.history_revision(first), target, f"packed={packed}")
                _git(store.repo, "update-ref", repo.ref_for(first), before)
                source.refresh()
                self.assertEqual(source.history_revision(first), before, f"packed={packed}")
                self.assertEqual(next(iter(source._listings.values())), repo.ref_heads(), "as a full listing")
            # A ref in a project directory that did not exist, and one deleted: each seen, only its directory listed.
            new = RunKey(storage_id("a new project"), storage_id("a new run"), 1)
            for change in (("update-ref", repo.ref_for(new), source.history_revision(first)),
                           ("update-ref", "-d", repo.ref_for(new))):
                _git(store.repo, *change)
                with mock.patch.object(GitArtifactRepository, "ref_heads", side_effect=AssertionError("listed all")):
                    source.refresh()
                self.assertEqual(next(iter(source._listings.values())), repo.ref_heads(), change[:2])

    def test_unchanged_damage_is_read_again_every_poll_and_publishes_the_same_version(self):
        """A failed ref listing says nothing about whether a history moved, so the run is read again every poll; but
        the published versions and token are a function of what was read, never of when (no nonce reaches them)."""
        from adapters.git.artifact_repo import GitError
        with tempfile.TemporaryDirectory() as tmp:
            store = build_fs_store(Path(tmp), 4, workspaces=1)  # its own, small: every poll reads every run again
            source = StoreSource(store.home)
            observer = Observer(source)
            observer.rows()
            published = []
            with mock.patch.object(GitArtifactRepository, "ref_heads", side_effect=GitError("listing failed")), \
                    mock.patch.object(GitArtifactRepository, "refs_signature", return_value=None), \
                    mock.patch.object(source, "read_state", wraps=source.read_state) as read:
                for _ in range(2):
                    read.reset_mock()
                    versions = {r["key"]: r["version"] for r in observer.rows()}
                    self.assertEqual(read.call_count, 4, "every run is read again while its listing fails")
                    published.append((versions, observer._published.summary(None, "UTC", utc_day)["token"]))
        self.assertEqual(published[0], published[1])

    def test_a_warm_poll_reads_no_run_state_only_its_version(self):
        source = StoreSource(self.store.home)
        observer = Observer(source)
        observer.poll()
        with mock.patch.object(source, "read_state", side_effect=AssertionError("read")), \
                mock.patch.object(source, "timeline", side_effect=AssertionError("read")):
            observer.poll()

    def test_the_version_moves_when_a_run_is_rewritten_and_only_that_run_is_read_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = build_fs_store(Path(tmp), 6, workspaces=2)  # its own: this case rewrites a run
            source = StoreSource(store.home)
            observer = Observer(source)
            before = {r["key"]: r for r in observer.rows()}
            changed = store.keys[0]
            replace_run(store, changed, store.keys[2])  # another template's run
            with mock.patch.object(source, "read_state", wraps=source.read_state) as read:
                after = {r["key"]: r for r in observer.rows()}
            self.assertEqual(read.call_count, 1)
            moved = [k for k in after if after[k]["version"] != before[k]["version"]]
            self.assertEqual(len(moved), 1)
            self.assertEqual({k: v["state"] for k, v in after.items() if k != moved[0]},
                             {k: v["state"] for k, v in before.items() if k != moved[0]})


#: How long a background case waits on a state before it fails: a bound on liveness (a hung indexer), never a measure.
LIVENESS = 120.0


def _until(reached) -> None:
    """Wait until ``reached()`` holds, on the state (polled), failing only if it never does within ``LIVENESS``."""
    end = time.monotonic() + LIVENESS
    while not reached():
        if time.monotonic() > end:
            raise AssertionError("the state was never reached")
        time.sleep(0.02)


class BackgroundIndexerTests(unittest.TestCase):
    """Requests read what the indexer last published; they never index, and coverage says what is not read yet."""

    @classmethod
    def setUpClass(cls):
        cls.store = _SHARED["store"]

    def test_a_request_never_indexes_and_coverage_grows_honestly(self):
        """Counted and ordered, never timed (T1): every projection is held at a gate, so the first publication must come
        before any run is projected; a request projects nothing, on its own thread or the indexer's."""
        observer = Observer(StoreSource(self.store.home), clock=time.monotonic)
        gate = threading.Event()
        real = observer._run
        entered, ran = [], []

        def held(*args, **kwargs):
            entered.append(threading.current_thread().name)
            if threading.current_thread() is not index._thread:
                raise AssertionError("a request projected a run")  # at once: a request never waits at the gate
            # a liveness bound only: a summary that waited for a run returns after it, with a run projected
            gate.wait(LIVENESS)
            ran.append(threading.current_thread().name)
            return real(*args, **kwargs)
        observer._run = held
        index = indexer_module.Indexer(observer, slice=0.01, idle=0.05)
        index.start()
        try:
            first = observer.summary()
            self.assertEqual(ran, [], "the first publication came before any run was projected")
            self.assertEqual(first["coverage"]["runs"], RUNS)
            self.assertEqual(first["coverage"]["pending"], RUNS)
            self.assertEqual(observer.runs({})["rows"], [])
            self.assertNotIn(threading.current_thread().name, entered, "a request started projecting a run")
            gate.set()
            _until(lambda: observer.summary()["coverage"]["pending"] == 0)
            self.assertEqual(observer.summary()["coverage"]["read"], RUNS)
            self.assertNotIn(threading.current_thread().name, ran, "a request projected a run")
            self.assertEqual(len(ran), RUNS, "each run projected once, by the indexer")
        finally:
            gate.set()
            index.stop()

    def test_a_change_is_published_by_the_indexer(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = build_fs_store(Path(tmp), 4, workspaces=1)
            observer = Observer(StoreSource(store.home), clock=time.monotonic)
            index = indexer_module.Indexer(observer, slice=0.05, idle=0.02)
            index.start()
            try:
                _until(lambda: observer.summary()["coverage"]["pending"] == 0)
                token = observer.summary()["token"]
                replace_run(store, store.keys[0], store.keys[1])
                _until(lambda: observer.summary()["token"] != token)
            finally:
                index.stop()


if __name__ == "__main__":
    unittest.main()


class _Count:
    """Counts the processes started (every one is git: ``subprocess.run`` starts its process through ``Popen``), the
    hashes the workspace adapter takes (and their bytes) and the state reads the source is asked for."""

    def __init__(self, source):
        import subprocess
        from adapters import execution
        self.git = self.hashes = self.hashed = self.reads = 0
        self.started: list = []
        real_popen, real_read = subprocess.Popen, execution.FilesystemWorkspace._read_at
        real_state = source.read_state
        counter = self

        class Popen(real_popen):
            def __init__(self, args, *a, **k):
                counter.git += 1
                counter.started.append(args)
                super().__init__(args, *a, **k)

        def read_at(fd, relative):
            captured = real_read(fd, relative)
            self.hashes += 1
            self.hashed += len(captured.content or b"")
            return captured

        def state(key):
            self.reads += 1
            return real_state(key)
        self._patches = [mock.patch.object(subprocess, "Popen", Popen),
                         mock.patch.object(execution.FilesystemWorkspace, "_read_at", staticmethod(read_at)),
                         mock.patch.object(source, "read_state", state)]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


class FsPassCostTests(unittest.TestCase):
    """What one pass costs on the filesystem, counted: N + F stats and nothing else when nothing changed; a user's
    edit costs one hash of that file and the projections of the runs bound to it, and no git process."""

    @classmethod
    def setUpClass(cls):
        # The shared store: each case edits files or rewrites runs in a repository of its own, which no other case
        # reads by name, and the store stays valid (FsStoreTests).
        cls.store = _SHARED["store"]
        cls.source = StoreSource(cls.store.home)
        cls.observer = Observer(cls.source)  # one warm observer: each case changes only what it then counts
        cls.observer.rows()

    def setUp(self):
        time.sleep(1.05)  # every file is older than the second it was last hashed in: none is racily clean
        self.observer.rows()  # the last case's racy re-check, so this case counts only its own change

    def _pass(self) -> tuple[_Count, int]:
        self.observer.counts.clear()
        with _Count(self.source) as counted:
            self.observer.rows()
        return counted, self.observer.counts["projections"]

    def _bound_to(self, repo: Path, path: str) -> set:
        return {k for k in self.store.keys if (b := self.observer._drift.bound(k)) is not None and b[0] == str(repo)
                and path in b[1]}

    def test_a_pass_with_nothing_changed_runs_no_git_reads_nothing_hashes_nothing(self):
        counted, projections = self._pass()
        self.assertEqual((counted.git, counted.reads, counted.hashes, projections), (0, 0, 0, 0))

    def test_an_edit_hashes_that_file_once_and_projects_only_the_runs_bound_to_it(self):
        repo = self.store.repos[3]
        bound = self._bound_to(repo, "src/core/a.py")
        self.assertGreater(len(bound), 1)
        (repo / "src/core/a.py").write_bytes(body("src/core/a.py", BOUND["src/core/a.py"], version=7))
        counted, projections = self._pass()
        self.assertEqual((counted.git, counted.hashes, counted.hashed), (0, 1, BOUND["src/core/a.py"]))
        self.assertEqual((projections, counted.reads), (len(bound), 0), "a run projected again for a file reads nothing")

    def test_an_edit_that_restores_the_modification_time_is_seen(self):
        """Same inode, same size, the modification time put back: only the change time tells."""
        repo = self.store.repos[5]
        path = repo / "src/util/x.py"
        before = os.stat(path)
        bound = self._bound_to(repo, "src/util/x.py")
        versions = {k: r["version"] for k, r in ((r["key"], r) for r in self.observer.rows())}
        with open(path, "r+b") as handle:  # in place: the inode stays
            handle.write(body("src/util/x.py", BOUND["src/util/x.py"], version=8))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.stat(path)
        self.assertEqual((after.st_ino, after.st_size, after.st_mtime_ns), (before.st_ino, before.st_size, before.st_mtime_ns))
        counted, projections = self._pass()
        self.assertEqual((counted.hashes, projections), (1, len(bound)))
        moved = {r["key"] for r in self.observer.rows() if r["version"] != versions[r["key"]]}
        self.assertEqual(moved, {f"{k.project_id}/{k.run_id}/{k.generation}" for k in bound})

    def test_k_writes_in_one_repository_run_git_only_for_that_repository(self):
        repo = self.store.repos[2]
        mine = [k for k in self.store.keys if self.store.repo_of[k] == repo]
        replace_run(self.store, mine[0], mine[1])
        counted, projections = self._pass()
        self.assertEqual((projections, counted.reads, counted.hashes), (1, 1, 0))
        self.assertEqual(counted.git, 2, counted.started)  # that repository's moved refs listed, its history read


class FilesystemSignatureTests(unittest.TestCase):
    """The workspace adapter's change signature, by ``lstat`` alone: what ``observe`` would read differently moves it."""

    def setUp(self):
        from adapters.execution import FilesystemWorkspace
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_bytes(b"one\n")
        self.ws = FilesystemWorkspace(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _sig(self, path="src/a.py"):
        return self.ws.signatures([path])[path]

    def test_a_file_renamed_over_with_its_size_and_times_kept_moves_the_signature(self):
        before, st = self._sig(), os.stat(self.root / "src" / "a.py")
        (self.root / "src" / "b.py").write_bytes(b"two\n")
        os.utime(self.root / "src" / "b.py", ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(self.root / "src" / "b.py", self.root / "src" / "a.py")
        self.assertNotEqual(self._sig(), before, "a new inode")

    def test_a_directory_swapped_for_a_symlink_moves_the_signature_and_observe_refuses_it(self):
        before = self._sig()
        (self.root / "elsewhere").mkdir()
        (self.root / "elsewhere" / "a.py").write_bytes(b"one\n")
        os.rename(self.root / "src", self.root / "old")
        os.symlink(self.root / "elsewhere", self.root / "src")
        self.assertNotEqual(self._sig(), before)
        self.assertNotEqual(self.ws.observe(("src/a.py",)).files[0].observation.state.value, "present")

    def test_a_missing_file_and_a_missing_directory_have_signatures_of_their_own(self):
        missing, gone = self._sig("src/none.py"), self._sig("nope/a.py")
        self.assertEqual(missing[-1][0], "absent")
        self.assertEqual(gone[-1][0], "absent")
        self.assertIsNone(self.ws.changed_at(missing))

    def test_a_file_changed_within_the_clock_second_of_now_is_racily_clean(self):
        now = self.ws.now_ns()
        self.assertEqual(now % 1_000_000_000, 0, "floored to the second: coarse filesystem times are covered")
        self.assertGreaterEqual(self.ws.changed_at(self._sig()), now - 1_000_000_000)


class TimelineStampTests(unittest.TestCase):
    """A run's stamp watches its ``timeline.jsonl`` too (AST-7): a rewrite in place (a corruption, a hand edit) is seen by
    the warm observer at once, and the warm view equals a cold one over the same files."""

    def setUp(self):
        from adapters.state.timeline import encode
        from core.records import Present
        from observer_store import DriverSource, build_store
        self.tmp = tempfile.TemporaryDirectory()
        mem = DriverSource(build_store())
        key = next(k for k in mem.keys() if isinstance(mem.read_state(k), Present) and mem.timeline(k))

        class Source(StoreSource):  # the real filesystem state and timeline; the history from the service's run
            def history(self, k): return mem.history(k)
            def history_revision(self, k): return mem.history_revision(k)
            def workspace(self, k): return mem.workspace(k)
            def project(self, p): return mem.project(p)

        self.source = Source(Path(self.tmp.name))
        folder = self.source._runs.generation_dir(key)
        folder.mkdir(parents=True, exist_ok=True)
        import json
        (folder / "run.json").write_text(json.dumps(mem.read_state(key).value))
        self.entries, self.encode = mem.timeline(key), encode
        self.path = folder / "timeline.jsonl"
        self.path.write_bytes(encode(self.entries))
        self.key = key

    def tearDown(self):
        self.tmp.cleanup()

    def _agree(self, warm):
        cold = Observer(self.source).rows()
        self.assertEqual(warm.rows(), cold, "the warm view is the cold one")
        self.assertEqual(warm.summary()["token"], Observer(self.source).summary()["token"])
        return cold[0]

    def _rewrite_in_place(self, data: bytes):
        with open(self.path, "r+b") as f:  # the same inode: no rename, the directory unchanged
            f.truncate(0)
            f.write(data)

    def test_a_timeline_corrupted_in_place_moves_the_stamp_and_the_warm_view(self):
        warm = Observer(self.source)
        warm.rows()
        before, stamp = warm.rows()[0], self.source.stamp(self.key)
        self.assertIsNotNone(before["updated"])
        self._rewrite_in_place(b"{broken}\n")
        self.assertNotEqual(self.source.stamp(self.key), stamp)
        self.assertEqual(self.source.stamps()[self.key][:3], self.source.stamp(self.key)[:3], "the bulk stamp reads the same files")
        self.assertIsNone(self._agree(warm)["updated"], "the time is unknown, not the old one")

    def test_a_valid_timeline_replaced_in_place_republishes_its_times(self):
        warm = Observer(self.source)
        before = warm.rows()[0]
        self._rewrite_in_place(self.encode([{**e, "at": e["at"] + 3600.0} for e in self.entries]))
        after = self._agree(warm)
        self.assertEqual(after["updated"], before["updated"] + 3600.0)
        self.assertNotEqual(after["version"], before["version"])


class HermeticFixtureTests(unittest.TestCase):
    """The fixture's git calls need nothing from the machine's git configuration: copied, ref-updated and committed to
    under an empty HOME and no global or system config, as on a CI runner with no git identity. (The whole 1,000-run
    build, templates included, runs under ``make verify``'s scratch HOME: ``make observer-smoke``.)"""

    def test_the_fixture_builds_and_commits_with_no_git_identity(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as tmp:
            clean = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "EMAIL"))}
            clean.update(HOME=home, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", GIT_CONFIG_NOSYSTEM="1")
            with mock.patch.dict(os.environ, clean, clear=True):
                store = build_fs_store(Path(tmp), 2, workspaces=1)
                tree = _git(store.repo, "mktree", stdin="").strip()
                commit = _git(store.repo, "commit-tree", tree, "-m", "hermetic").strip()
        self.assertRegex(commit, r"^[0-9a-f]{40,64}$")
