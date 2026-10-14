"""Change detection by a cost model (P1b): what one pass costs, counted, and that the incremental view is exactly a
from-scratch recompute.

N runs, F indexed (workspace, path) pairs, K runs written since the last pass, M bound files whose content changed and
R(M) the runs bound to them. A pass may read K states and histories, hash M files (plus the racily clean ones), project
K + |R(M)| runs and update the counts 2(K + |R(M)|) times (each run's old contribution out, its new one in); it may
stat N + F things. Counted, not timed, over ``observer_store.HeteroSource``: 20 workspaces whose runs' spikes bind
distinct, overlapping files of realistic size, active and ended runs, and a share of drifted ones.
"""
from __future__ import annotations

import random
import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parents[1])]

from application import observer_index as _index  # noqa: E402
from application.observer import Observer  # noqa: E402
from driver import FakeWorkspace  # noqa: E402
from observer_store import BOUND, DRIFTED, HeteroSource, build_hetero_templates, observed  # noqa: E402

_REAL_AGGREGATES = _index.aggregates  # the recount, as the oracle (the cost tests count every call of it)
READS = ("read_state", "history", "timeline", "history_revision")


class Counted:
    """A source that counts every read it is asked for (and passes everything else through)."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.reads: Counter = Counter()

    def __getattr__(self, name):
        found = getattr(self.inner, name)
        if name not in READS:
            return found

        def counted(*args, **kwargs):
            self.reads[name] += 1
            return found(*args, **kwargs)
        return counted


def _hashed_bytes(source: HeteroSource) -> int:
    return sum(space.hashed_bytes for space in source.spaces)


class PassCostTests(unittest.TestCase):
    N, W = 100, 20

    def setUp(self):
        self.hetero = HeteroSource(build_hetero_templates(), self.N, workspaces=self.W)
        self.source = Counted(self.hetero)
        self.observer = Observer(self.source)
        # A recount of the store's aggregates visits every row: counted as an update per row it visits.
        real, counts = _index.aggregates, self.observer.counts

        def recount(rows, *args, **kwargs):
            counts["aggregate_updates"] += len(rows)
            return real(rows, *args, **kwargs)
        patch = mock.patch.object(_index, "aggregates", recount)
        patch.start()
        self.addCleanup(patch.stop)
        self.observer.rows()
        self._time_passes()
        self.observer.rows()  # the racy re-check of the cold start's files, if any

    def _time_passes(self):
        for space in self.hetero.spaces:
            space.tick()

    def _pass(self) -> dict:
        self.source.reads.clear()
        self.observer.counts.clear()
        before = _hashed_bytes(self.hetero)
        self.observer.rows()
        return {**{name: self.source.reads[name] for name in READS}, **self.observer.counts,
                "hashed_bytes": _hashed_bytes(self.hetero) - before}

    def _bound_to(self, workspace: int, path: str) -> set:
        space = self.hetero.spaces[workspace]
        return {key for key in self.hetero.keys() if self.hetero.workspace(key) is space
                and path in (self.observer._drift.bound(key) or (None, ()))[1]}

    def test_the_fixture_is_heterogeneous(self):
        rows = self.observer.rows()
        details = self.observer.details()
        standings = Counter(r["standing"] for r in rows)
        self.assertGreaterEqual(len({id(self.hetero.workspace(k)) for k in self.hetero.keys()}), 20)
        self.assertGreater(standings["not_ended"], 0)
        self.assertGreater(sum(r["state"] != "active" for r in rows), 0, "ended runs")
        self.assertGreater(sum(bool(d["stale"]) for d in details.values()), 0, "drifted runs")
        self.assertLess(sum(bool(d["stale"]) for d in details.values()), len(rows), "not every run drifted")
        bound = Counter(self.observer._drift.bound(k) for k in self.hetero.keys())
        files = Counter(f for b in bound if b is not None for f in ((b[0], p) for p in b[1]))
        self.assertGreaterEqual(len({p for _, p in files}), 6, "distinct files")
        self.assertGreater(len({b[1] for b in bound if b is not None}), 2, "distinct file sets")
        self.assertGreater(max(files.values()), 2, "files shared by runs in one workspace")

    def test_a_pass_with_nothing_changed_reads_nothing_hashes_nothing_projects_nothing(self):
        cost = self._pass()
        self.assertEqual({k: cost.get(k, 0) for k in (*READS, "hashed_files", "hashed_bytes", "projections",
                                                     "aggregate_updates")},
                         dict.fromkeys((*READS, "hashed_files", "hashed_bytes", "projections", "aggregate_updates"), 0))
        self.assertEqual(cost["signatures"], self.observer._drift.size(), "F stats, no reads")

    def test_k_writes_cost_k_reads_k_projections_2k_updates_and_no_hash(self):
        keys = self.hetero.keys()
        written = [keys[n * 13] for n in range(1, 8)]
        for n, key in enumerate(written):
            # Another template whose files are indexed in its workspace already.
            self.hetero.replace(key, self.hetero._map[key][0] + 1 + (n % 2))
        cost = self._pass()
        K = len(written)
        self.assertEqual((cost["read_state"], cost["history"]), (K, K))
        self.assertEqual(cost["projections"], K)
        self.assertEqual(cost["aggregate_updates"], 2 * K)
        self.assertEqual(cost.get("hashed_files", 0), 0)

    def test_m_edits_hash_m_files_and_project_only_the_runs_bound_to_them(self):
        edits = [(1, "src/core/a.py"), (2, "src/util/x.py"), (2, "docs/guide.md"), (7, "src/core/c.py")]
        bound = set().union(*(self._bound_to(w, p) for w, p in edits))
        for w, path in edits:
            self.hetero.edit(w, path, version=9)
        cost = self._pass()
        self.assertEqual(cost["hashed_files"], len(edits))
        self.assertEqual(cost["hashed_bytes"], sum(BOUND[p] for _, p in edits))
        self.assertEqual(cost["projections"], len(bound))
        self.assertEqual(cost["aggregate_updates"], 2 * len(bound))
        self.assertEqual(sum(cost[name] for name in READS), 0, "a run projected again for a file reads nothing")
        # The last edit in each workspace was in the tick the pass started in, so it is racily clean: hashed once
        # more, it finds nothing new and projects nothing.
        again = self._pass()
        self.assertEqual((again["hashed_files"], again.get("projections", 0)), (len({w for w, _ in edits}), 0))
        # Once the tick is over, the last re-check is the one that can catch a write later in that tick; then none.
        self._time_passes()
        self.assertEqual(self._pass().get("hashed_files", 0), len({w for w, _ in edits}))
        self.assertEqual(self._pass().get("hashed_files", 0), 0)

    def test_an_edit_that_keeps_the_modification_time_is_seen(self):
        before = self.observer.details()
        self.hetero.edit(3, "src/core/a.py", version=5, keep_mtime=True)
        cost = self._pass()
        self.assertEqual(cost["hashed_files"], 1)
        self.assertEqual(cost["projections"], len(self._bound_to(3, "src/core/a.py")))
        changed = [k for k, d in self.observer.details().items() if d["version"] != before[k]["version"]]
        self.assertEqual(len(changed), cost["projections"])

    def test_a_racily_clean_edit_is_seen_on_the_next_pass(self):
        """A write in the same clock tick as the hash, of the same size: its signature does not move."""
        self.hetero.edit(4, "src/util/x.py", version=6)
        first = self._pass()
        self.assertEqual(first["hashed_files"], 1)
        before = self.observer.details()
        self.hetero.edit(4, "src/util/x.py", version=7, same_tick=True)
        second = self._pass()
        self.assertEqual(second["hashed_files"], 1)
        self.assertEqual(second["projections"], len(self._bound_to(4, "src/util/x.py")))
        self.assertTrue(any(d["version"] != before[k]["version"] for k, d in self.observer.details().items()))

    def test_a_regraph_that_unbinds_files_leaves_no_binding(self):
        space = self.hetero.spaces[5]
        templates = {self.hetero._map[k][0]: k for k in self.hetero.keys() if self.hetero.workspace(k) is space}
        binding = next(t for t, k in templates.items() if self.observer._drift.bound(k) is not None)
        research_only = next(t for t, k in templates.items() if self.observer._drift.bound(k) is None)
        key = templates[binding]
        paths = self.observer._drift.bound(key)[1]
        self.hetero.replace(key, research_only)
        cost = self._pass()
        self.assertEqual(cost["projections"], 1)
        self.assertIsNone(self.observer._drift.bound(key), "the old binding is gone")
        cached = self.observer._cache[key][2]
        self.hetero.edit(5, paths[0], version=8)
        self._pass()
        self.assertIs(self.observer._cache[key][2], cached, "an edit to a file it no longer binds projects nothing")

    def test_a_window_is_summed_from_hour_buckets_and_never_recounts_the_store(self):
        latest = max(r["updated"] for r in self.observer.rows() if r["updated"] is not None)
        for days in (7, 30):
            for since in (latest - days * 86_400, latest - 3_600 * 2.5):
                with self.subTest(days=days, since=since):
                    self.observer.counts.clear()
                    window = self.observer.summary(since)["aggregates"]
                    hours = int((latest - since) // 3_600) + 2
                    self.assertEqual(self.observer.counts.get("aggregate_updates", 0), 0, "no recount of N")
                    self.assertLessEqual(self.observer.counts["window_buckets"], hours)
                    snapped = since // 3_600 * 3_600
                    self.assertEqual(window["since"], snapped, "snapped to the whole hour")
                    rows = self.observer.rows()
                    facts = {r["key"]: _index.facts(d) for r, d in ((r, self.observer.details()[r["key"]]) for r in rows)}
                    self.assertEqual(window, _REAL_AGGREGATES(rows, facts, snapped), "the buckets sum to a recount")

    def test_a_deleted_run_costs_one_update_and_is_unbound(self):
        key = next(k for k in self.hetero.keys() if self.observer._drift.bound(k) is not None)
        self.hetero.delete(key)
        cost = self._pass()
        self.assertEqual((cost.get("projections", 0), cost["aggregate_updates"], sum(cost[n] for n in READS)), (0, 1, 0))
        self.assertIsNone(self.observer._drift.bound(key))
        self.assertNotIn(key, self.observer._cache)

    def test_a_run_moved_to_a_new_workspace_hashes_its_files_there_once(self):
        key = next(k for k in self.hetero.keys() if self.observer._drift.bound(k) is not None)
        import copy
        fresh: FakeWorkspace = copy.deepcopy(self.hetero.spaces[0])
        self.hetero.spaces.append(fresh)
        self.hetero._space_of[key] = fresh
        self.hetero.replace(key, self.hetero._map[key][0])  # its location record moved: one of our writes
        cost = self._pass()
        self.assertEqual(cost["projections"], 1)
        self.assertEqual(cost["hashed_files"], len(self.observer._drift.bound(key)[1]))
        self.assertEqual(self._pass().get("hashed_files", 0), 0, "its files are older than the pass that hashed them")


class IncrementalEqualsRecomputeTests(unittest.TestCase):
    """The oracle: after any sequence of our writes and the user's edits, the incremental view and every run's detail
    are identical to a from-scratch observer over the same store."""

    def _check(self, observer: Observer, source) -> None:
        fresh = Observer(source)
        self.assertEqual(observer.rows(), fresh.rows())
        self.assertEqual(observer.details(), fresh.details())
        self.assertEqual(observer.summary(), fresh.summary())
        rows = fresh.rows()
        facts = {r["key"]: _index.facts(d) for r, d in ((r, fresh.details()[r["key"]]) for r in rows)}
        self.assertEqual(observer.summary()["aggregates"], _index.aggregates(rows, facts), "the tally is a recount")
        self.assertEqual(observed(observer), observed(fresh))

    def test_INVARIANT_incremental_equals_a_full_recompute_over_generated_sequences(self):
        templates = build_hetero_templates()
        for seed in range(4):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                source = HeteroSource(templates, 30, workspaces=6, drift_every=3)
                observer = Observer(source)
                self._check(observer, source)
                paths = sorted(BOUND)
                for step in range(12):
                    op = rng.choice(["write", "edit", "edit_keep", "edit_racy", "remove", "error", "delete", "tick",
                                     "regraph_then_edit"])
                    w = rng.randrange(len(source.spaces))
                    path = rng.choice(paths)
                    if op == "write":
                        key = rng.choice(source.keys())
                        source.replace(key, rng.randrange(source.templates()))
                    elif op == "edit":
                        source.edit(w, path, version=rng.randrange(2, 9))
                    elif op == "edit_keep":
                        source.edit(w, path, version=rng.randrange(2, 9), keep_mtime=True)
                    elif op == "edit_racy":
                        # An edit, a pass that hashes it in the same tick, then another edit in that tick.
                        source.edit(w, path, version=rng.randrange(10, 19))
                        observer.rows()
                        source.edit(w, path, version=rng.randrange(20, 29), same_tick=True)
                    elif op == "remove":
                        source.spaces[w].delete(path)
                    elif op == "error":
                        source.spaces[w].set_error(path, "unreadable")
                    elif op == "delete" and len(source.keys()) > 5:
                        source.delete(rng.choice(source.keys()))
                    elif op == "tick":
                        for space in source.spaces:
                            space.tick()
                    elif op == "regraph_then_edit":
                        # One of our writes rebinds a run to other files; then a file only the new binding has changes.
                        key = rng.choice(source.keys())
                        source.replace(key, rng.randrange(source.templates()))
                        observer.rows()
                        source.edit(source.spaces.index(source.workspace(key)), rng.choice(paths),
                                    version=rng.randrange(20, 29))
                    self._check(observer, source)

    def test_the_drifted_file_is_what_the_fixture_says(self):
        self.assertIn(DRIFTED, BOUND)


if __name__ == "__main__":
    unittest.main()
