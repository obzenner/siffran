"""The observer's cost, calculated (T1 A): what a pass costs as a function of N runs, by doubling at small n.

A cost model per operation, counted (never timed), so it holds at any store size. N runs over W workspaces (fixed), F
indexed (workspace, path) pairs (bounded by W and the files the runs bind, never by N), k runs written since the last
pass:

    a pass with nothing changed      N version checks (stamps) and F signatures; no read, hash, projection or count update
    k writes                         k reads of each record, k projections, 2k count updates; no hash
    a summary, warm                  no projection, no count update; a window visits its hours, never the runs
    on disk, nothing changed         no git process, no read, no hash, no projection; stat calls linear in N
    on disk, one run rewritten       one read, one projection, git only for that run's repository

Each is measured at n and 2n: an O(1) cost is the same at both sizes, an O(N) one at most doubles (plus a constant). The
in-memory store is ``observer_store.HeteroSource`` (n = 100 and 200), the disk one ``fs_store.build_fs_store`` (n = 50 and
100) read through the plugin's ``StoreSource``. The wall-time budgets at the realistic size (1,000 runs) are one smoke
run, ``test_observer_smoke.py`` (``make observer-smoke``, in ``make verify`` only).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parents[1])]

from adapters.observer.source import StoreSource  # noqa: E402
from application import observer_index as _index  # noqa: E402
from application.observer import Observer  # noqa: E402
from fs_store import build_fs_store, replace_run  # noqa: E402
from observer_store import BOUND, HeteroSource, build_hetero_templates  # noqa: E402
from test_observer_fs import _Count  # noqa: E402

READS = ("read_state", "history", "timeline", "history_revision")
SIZES = (100, 200)
DISK_SIZES = (50, 100)
W = 10
#: An O(N) cost may at most double from n to 2n, plus this constant (a per-pass overhead that does not grow with N).
SLACK = 8


class _Counted:
    """The source, counting each read and each version check it is asked for."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls: Counter = Counter()

    def __getattr__(self, name):
        found = getattr(self.inner, name)
        if name not in (*READS, "stamp"):
            return found

        def counted(*args, **kwargs):
            self.calls[name] += 1
            return found(*args, **kwargs)
        return counted


def _memory(n: int, templates) -> tuple[HeteroSource, _Counted, Observer]:
    hetero = HeteroSource(templates, n, workspaces=W)
    source = _Counted(hetero)
    observer = Observer(source)
    observer.rows()
    for space in hetero.spaces:
        space.tick()
    observer.rows()  # the racy re-check of the cold start's files, if any
    return hetero, source, observer


def _cost(source: _Counted, observer: Observer, act) -> dict:
    source.calls.clear()
    observer.counts.clear()
    act()
    return {**dict.fromkeys((*READS, "stamp", "projections", "hashed_files", "aggregate_updates", "signatures",
                             "window_buckets"), 0), **source.calls, **observer.counts}


def _linear(test: unittest.TestCase, small: int, big: int, what: str) -> None:
    test.assertLessEqual(big, 2 * small + SLACK, f"{what}: {small} at n, {big} at 2n is more than linear")


class MemoryComplexityTests(unittest.TestCase):
    """Doubling in memory: every cost is counted at n = 100 and n = 200."""

    @classmethod
    def setUpClass(cls):
        templates = build_hetero_templates()
        cls.stores = {n: _memory(n, templates) for n in SIZES}

    def setUp(self):
        # A recount of the store's aggregates visits every row: counted as one update per row it visits.
        real = _index.aggregates

        def recount(rows, *args, **kwargs):
            for _, _, observer in self.stores.values():
                observer.counts["aggregate_updates"] += len(rows)
            return real(rows, *args, **kwargs)
        patch = mock.patch.object(_index, "aggregates", recount)
        patch.start()
        self.addCleanup(patch.stop)

    def _each(self, act):
        return {n: _cost(source, observer, lambda: act(hetero, observer)) for n, (hetero, source, observer) in
                self.stores.items()}

    def test_a_pass_with_nothing_changed_checks_n_versions_and_reads_hashes_and_projects_nothing(self):
        cost = self._each(lambda hetero, observer: observer.rows())
        for n, c in cost.items():
            self.assertEqual({k: c[k] for k in (*READS, "projections", "hashed_files", "aggregate_updates")},
                             dict.fromkeys((*READS, "projections", "hashed_files", "aggregate_updates"), 0), n)
            self.assertEqual(c["stamp"], n, "one version check a run")
        small, big = (cost[n] for n in SIZES)
        self.assertEqual(small["signatures"], big["signatures"], "F signatures: bounded by what the runs bind, not by N")
        self.assertGreater(small["signatures"], 0)

    def test_one_write_costs_one_read_one_projection_and_two_updates_at_any_n(self):
        def write(hetero, observer):
            key = hetero.keys()[7]
            hetero.replace(key, hetero._map[key][0] + 1)
            observer.rows()
        cost = self._each(write)
        for n, c in cost.items():
            self.assertEqual((c["read_state"], c["history"], c["projections"], c["aggregate_updates"]), (1, 1, 1, 2), n)
        small, big = (cost[n]["hashed_files"] for n in SIZES)
        self.assertEqual(small, big, "the files the new records bind, never more with N")
        self.assertLessEqual(big, len(BOUND))

    def test_k_writes_cost_k_projections_whatever_n(self):
        def writes(hetero, observer):
            for n in range(1, 6):
                key = hetero.keys()[n * 11]
                hetero.replace(key, hetero._map[key][0] + 1)
            observer.rows()
        cost = self._each(writes)
        self.assertEqual({n: (c["projections"], c["aggregate_updates"]) for n, c in cost.items()},
                         dict.fromkeys(SIZES, (5, 10)))

    def test_one_edit_projects_only_the_runs_bound_to_it_which_grow_with_n_and_nothing_else(self):
        def edit(hetero, observer):
            hetero.edit(3, "src/core/a.py", version=9)
            observer.rows()
        cost = self._each(edit)
        for n, c in cost.items():
            self.assertEqual((c["hashed_files"], sum(c[r] for r in READS)), (1, 0), n)
        small, big = (cost[n]["projections"] for n in SIZES)
        self.assertGreater(small, 0)
        _linear(self, small, big, "projections of the runs bound to an edited file")

    def test_a_warm_summary_and_a_window_cost_the_same_at_any_n(self):
        def summary(hetero, observer):
            observer.summary()
            latest = max(r["updated"] for r in observer.rows() if r["updated"] is not None)
            observer.summary(latest - 7 * 86_400)
        cost = self._each(summary)
        for n, c in cost.items():
            self.assertEqual((c["projections"], c["aggregate_updates"], sum(c[r] for r in READS)), (0, 0, 0), n)
        small, big = (cost[n]["window_buckets"] for n in SIZES)
        self.assertLessEqual(big, small, "a window is summed from its hours, never from the runs")
        self.assertLessEqual(small, 7 * 24 + 2)

    def test_the_cold_start_projects_each_run_once(self):
        templates = build_hetero_templates()
        for n in SIZES:
            with self.subTest(n=n):
                source = _Counted(HeteroSource(templates, n, workspaces=W))
                observer = Observer(source)
                observer.rows()
                self.assertEqual((observer.counts["projections"], source.calls["read_state"]), (n, n))


class _Stats:
    """Counts the filesystem metadata calls a pass makes (``lstat``, ``stat``, ``scandir``, ``listdir``)."""

    NAMES = ("lstat", "stat", "scandir", "listdir")

    def __init__(self) -> None:
        self.calls = 0
        self._patches = []
        for name in self.NAMES:
            real = getattr(os, name)

            def counted(*args, _real=real, **kwargs):
                self.calls += 1
                return _real(*args, **kwargs)
            self._patches.append(mock.patch.object(os, name, counted))

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


class DiskComplexityTests(unittest.TestCase):
    """Doubling on the filesystem: the plugin's ``StoreSource`` over real stores of 50 and 100 runs."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.stores = {}
        for n in DISK_SIZES:
            store = build_fs_store(Path(cls.tmp.name) / str(n), n, workspaces=W)
            source = StoreSource(store.home)
            observer = Observer(source)
            observer.rows()
            cls.stores[n] = (store, source, observer)
        time.sleep(1.05)  # every file is older than the second it was hashed in: none is racily clean
        for _, _, observer in cls.stores.values():
            observer.rows()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _pass(self, n):
        _, source, observer = self.stores[n]
        observer.counts.clear()
        with _Count(source) as counted, _Stats() as stats:
            observer.rows()
        return counted, stats.calls, observer.counts.get("projections", 0)

    def test_a_pass_with_nothing_changed_runs_no_git_reads_nothing_and_its_stats_are_linear_in_n(self):
        stats = {}
        for n in DISK_SIZES:
            counted, stats[n], projections = self._pass(n)
            self.assertEqual((counted.git, counted.reads, counted.hashes, projections), (0, 0, 0, 0), n)
            self.assertGreaterEqual(stats[n], n, "at least one version check a run")
        small, big = (stats[n] for n in DISK_SIZES)
        _linear(self, small, big, "stat calls of a pass with nothing changed")
        # 9: the bound was 8; the stamp now adds exactly one lstat a run, of its timeline.jsonl (AST-7). The fraction
        # over a whole number is the pass's fixed calls (the store's and the projects' folders) spread over n
        self.assertLessEqual(big / DISK_SIZES[1], 9, "a bounded number of stat calls a run")

    def test_one_run_rewritten_costs_one_read_one_projection_and_git_for_its_repository_only(self):
        for n in DISK_SIZES:
            with self.subTest(n=n):
                store, _, _ = self.stores[n]
                repo = store.repos[2]
                mine = [k for k in store.keys if store.repo_of[k] == repo]
                replace_run(store, mine[0], mine[1])
                counted, _, projections = self._pass(n)
                self.assertEqual((projections, counted.reads, counted.hashes, counted.git), (1, 1, 0, 2), counted.started)


if __name__ == "__main__":
    unittest.main()
