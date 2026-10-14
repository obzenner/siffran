"""When run events happened: a timeline beside each run generation, never inside it (ADR-0070).

The bridge adapter records, after each successful run-state write, one entry with the wall-clock time of that write
(``adapters/state/timeline.py``). The observer reads it as display labels: each history step's ``at`` is the time of the
write that first committed it, and a run's ``started``, ``updated`` and ``ended`` are the times of its create, its latest
and its terminal writes. Nothing the plugin decides reads a time.

Each invariant here is a test over runs driven through the real v2 service (``tests/v2`` driver, the observer store's
scenarios, or a scenario built per variant):

* two clocks give byte-identical run state, artifacts, manifests, ``state_digest``, ``GetRun``, ``GetArgument`` and the
  auditor dossier; only the timelines differ;
* every published ``at`` is its own write's recorded time; a write whose entry is missing publishes ``null``, and no
  artifact borrows a later write's time;
* a timeline that cannot be written changes no result, and its times are ``null``;
* a truncated last line is tolerated; anything malformed reads as unknown, never as zero;
* each generation has its own timeline; a copied store keeps its times; only a CAS winner's write is recorded;
* ``ended`` exists exactly when the core reports a terminal status, and ``started <= updated`` under a monotone clock;
* ``core/`` and ``application/`` read no clock.
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))  # the validator is a repository script

from adapters import bridge  # noqa: E402
from adapters.state import fsio, timeline as tl  # noqa: E402
from adapters.state.repository import FilesystemRunRepository  # noqa: E402
from application import run_state  # noqa: E402
from application.history_records import MANIFEST_KIND  # noqa: E402
from application.observer import Observer  # noqa: E402
from application.snapshot import state_digest  # noqa: E402
from assertions import get_argument, get_run  # noqa: E402
from core.records import Conflict, RunKey  # noqa: E402
from driver import FakeClock, MemoryTimeline  # noqa: E402
from observer_store import DriverSource, T0, _Builder, _graph, build_store, observed, contract_problems, view_of  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[2]
TERMINAL = {"converged", "stopped_residual", "stopped_frozen", "stopped_budget"}
KEY = RunKey("p", "r", 1)


def _doc(n: int, status: str = "active") -> dict:
    return {"committed_artifact_head_id": f"sha256:{n:064x}", "status": status, "n": n}


def _k(key: RunKey) -> tuple:
    return (key.project_id, key.run_id, key.generation)


def _rows(view):
    return {run["key"]: run for project in view["projects"] for run in project["runs"]}


def _handle(key: str) -> str:
    from application.location import encode_handle
    project, run, generation = key.split("/")
    return encode_handle(RunKey(project, run, int(generation)))


def _small(clock=None, timeline=None, *, stop=True):
    """One run with several writes: research, a report, more research and (optionally) a stop. Receipt ids are drawn
    from a fixed sequence (as the observer store draws them), so two builds differ only by what they are given."""
    import uuid
    import governance_setup
    sequence = (uuid.UUID(int=n) for n in range(1, 1 << 16))
    with mock.patch.object(governance_setup, "uuid4", lambda: next(sequence)):
        return _small_run(clock, timeline, stop)


def _small_run(clock, timeline, stop):
    b = _Builder(clock=clock or FakeClock(T0, 60.0), timeline=MemoryTimeline() if timeline is None else timeline)
    run = b.begin("probe", "s-small", "Probe the timeline.", _graph(
        "C0", [("C0", "ordinary", "The probe reads one file."), ("C1", "ordinary", "The file exists.")], [("C0", "C1")]))
    b.research(run, "C1", ref="a.md")
    b.evaluate(run, "report_convergence")
    b.research(run, "C0", ref="b.md")
    if stop:
        b.evaluate(run, "stop")
    return b, run


def _results(b, run):
    """Everything a run's decisions produce, for comparison across clocks."""
    drv = b.drv
    states = {k: fsio.dump_json(v[0]) for k, v in sorted(drv._runs._store.items(), key=lambda kv: (kv[0].project_id, kv[0].run_id, kv[0].generation))}
    artifacts = [(a.artifact_id, a.body) for a in drv.artifacts()]
    out = {"states": states, "artifacts": artifacts,
           "get_run": drv.request(get_run(run_id=run, request_id="r-1")),
           "get_argument": drv.request(get_argument(run_id=run, request_id="r-2"))}
    return out


class TimelineCodecTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.timeline = tl.Timeline(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def test_entries_are_appended_beside_run_json_one_line_each(self):
        self.timeline.append(KEY, None, "rev-1", _doc(1), 10.5)
        self.timeline.append(KEY, "rev-1", "rev-2", _doc(2, "converged"), 11.0)
        path = self.timeline.path(KEY)
        self.assertEqual(path, FilesystemRunRepository(self.home).generation_dir(KEY) / tl.TIMELINE)
        self.assertEqual(path.parent / "run.json", FilesystemRunRepository(self.home)._doc_path(KEY))
        entries = self.timeline.read(KEY)
        none = {"plan_revision": None, "proposal_digest": None, "receipts": None}
        self.assertEqual(entries, (
            {"seq": 1, "previous_revision": None, "revision": "rev-1", "artifact_head": _doc(1)["committed_artifact_head_id"],
             "status": "active", "at": 10.5, **none},
            {"seq": 2, "previous_revision": "rev-1", "revision": "rev-2",
             "artifact_head": _doc(2)["committed_artifact_head_id"], "status": "converged", "at": 11.0, **none}))
        self.assertEqual(path.read_bytes().count(b"\n"), 2)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_an_entry_records_the_configuration_the_write_left(self):
        """Display data read from the written document as ``status`` is: its governance ``plan_revision`` and
        ``proposal_digest`` and the ids of its receipts, so a reader can tell which write proposed or answered what."""
        doc = {**_doc(1), "governance": {"plan_revision": 2, "proposal_digest": "sha256:" + "a" * 64,
                                         "receipts": [{"id": "r-1", "outcome": "approve"}, {"id": "r-2"}]}}
        self.timeline.append(KEY, None, "rev-1", doc, 1.0)
        entry = self.timeline.read(KEY)[0]
        self.assertEqual((entry["plan_revision"], entry["proposal_digest"], entry["receipts"]),
                         (2, "sha256:" + "a" * 64, ["r-1", "r-2"]))
        for bad in ({"plan_revision": "2"}, {"proposal_digest": 7}, {"receipts": ["r", 3]}):
            raw = tl.encode([{**entry, **bad}])
            self.assertIsNone(tl.parse(raw), bad)

    def test_each_generation_has_its_own_timeline(self):
        self.timeline.append(KEY, None, "a", _doc(1), 1.0)
        second = RunKey("p", "r", 2)
        self.timeline.append(second, None, "b", _doc(2), 2.0)
        self.assertNotEqual(self.timeline.path(KEY), self.timeline.path(second))
        self.assertEqual([e["at"] for e in self.timeline.read(second)], [2.0])
        self.assertEqual([e["at"] for e in self.timeline.read(KEY)], [1.0])

    def test_appends_are_written_under_the_lock(self):
        held = []
        real = fsio.lock

        def lock(path):
            held.append(path)
            return real(path)
        with mock.patch.object(tl.fsio, "lock", lock):
            self.timeline.append(KEY, None, "a", _doc(1), 1.0)
        self.assertEqual(held, [self.timeline.path(KEY)])

    def test_a_truncated_last_line_is_tolerated_and_dropped_by_the_next_append(self):
        self.timeline.append(KEY, None, "a", _doc(1), 1.0)
        path = self.timeline.path(KEY)
        path.write_bytes(path.read_bytes() + b'{"seq": 2, "previous_revi')
        self.assertEqual([e["revision"] for e in self.timeline.read(KEY)], ["a"])
        self.timeline.append(KEY, "a", "b", _doc(2), 2.0)
        self.assertEqual([(e["seq"], e["revision"]) for e in self.timeline.read(KEY)], [(1, "a"), (2, "b")])

    def test_anything_malformed_reads_as_unknown_never_as_zero(self):
        good = tl.encode([tl.entry(1, None, "a", _doc(1), 1.0)])
        line = json.loads(good)
        variants = {
            "not json": b"nope\n",
            "not an object": b"[1]\n",
            "a missing key": json.dumps({k: v for k, v in line.items() if k != "at"}).encode() + b"\n",
            "an extra key": json.dumps({**line, "x": 1}).encode() + b"\n",
            "a seq gap": json.dumps({**line, "seq": 2}).encode() + b"\n",
            "a boolean time": json.dumps({**line, "at": True}).encode() + b"\n",
            "a string time": json.dumps({**line, "at": "1.0"}).encode() + b"\n",
            "a non-finite time": good.replace(b"1.0", b"NaN"),
            "an empty revision": json.dumps({**line, "revision": ""}).encode() + b"\n",
            "a corrupt middle line": good + b"garbage\n" + good,
            "not utf-8": b"\xff\n",
        }
        for name, raw in variants.items():
            with self.subTest(name):
                self.assertIsNone(tl.parse(raw))
        self.assertIsNone(tl.parse(None), "a missing timeline is unknown")
        self.assertIsNone(self.timeline.read(KEY), "a missing timeline is unknown")
        path = self.timeline.path(KEY)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"garbage\n")
        self.assertIsNone(self.timeline.read(KEY))
        with self.assertRaises(ValueError):  # a corrupt timeline is not extended (it would stay unknown anyway)
            self.timeline.append(KEY, None, "a", _doc(1), 1.0)

    def test_a_symlinked_timeline_is_refused(self):
        target = self.home / "elsewhere"
        target.write_bytes(tl.encode([tl.entry(1, None, "a", _doc(1), 1.0)]))
        path = self.timeline.path(KEY)
        path.parent.mkdir(parents=True)
        path.symlink_to(target)
        self.assertIsNone(self.timeline.read(KEY))
        with self.assertRaises(OSError):
            self.timeline.append(KEY, None, "b", _doc(1), 1.0)


class RecordingTests(unittest.TestCase):
    """The bridge's run repository records each successful write and nothing else."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.ticks = []

    def tearDown(self):
        self.tmp.cleanup()

    def clock(self):
        self.ticks.append(100.0 + len(self.ticks))
        return self.ticks[-1]

    def runs(self, timeline=None):
        return bridge._RecordingRuns(FilesystemRunRepository(self.home),
                                     timeline=timeline or tl.Timeline(self.home), clock=self.clock)

    def test_create_and_compare_and_set_are_recorded_with_the_injected_clock(self):
        runs = self.runs()
        first = runs.create(KEY, _doc(1))
        second = runs.compare_and_set(KEY, _doc(2, "stopped_residual"), first)
        entries = tl.Timeline(self.home).read(KEY)
        self.assertEqual([(e["previous_revision"], e["revision"], e["status"], e["at"]) for e in entries],
                         [(None, first.token, "active", 100.0), (first.token, second.token, "stopped_residual", 101.0)])

    def test_the_default_clock_is_wall_time_read_only_by_the_bridge(self):
        import inspect
        import time
        self.assertIs(inspect.signature(bridge._RecordingRuns).parameters["clock"].default, time.time)

    def test_only_the_compare_and_set_winner_is_recorded(self):
        runs = self.runs()
        base = runs.create(KEY, _doc(1))
        won = runs.compare_and_set(KEY, _doc(2), base)
        with self.assertRaises(Conflict):
            runs.compare_and_set(KEY, _doc(3), base)
        self.assertEqual([e["revision"] for e in tl.Timeline(self.home).read(KEY)], [base.token, won.token])
        self.assertEqual(len(self.ticks), 2, "a losing write reads no clock")

    def test_concurrent_writers_record_exactly_the_winner(self):
        runs = self.runs()
        base = runs.create(KEY, _doc(1))
        barrier, results = threading.Barrier(8), []

        def write(n):
            barrier.wait()
            try:
                results.append(runs.compare_and_set(KEY, _doc(10 + n), base).token)
            except Conflict:
                pass
        threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 1)
        entries = tl.Timeline(self.home).read(KEY)
        self.assertEqual([(e["previous_revision"], e["revision"]) for e in entries],
                         [(None, base.token), (base.token, results[0])])

    def test_a_timeline_that_cannot_be_written_changes_no_write(self):
        recorded = self.runs()
        plain = FilesystemRunRepository(Path(self.tmp.name) / "plain")
        broken_path = tl.Timeline(self.home).path(KEY)
        broken_path.mkdir(parents=True)  # a directory where the timeline file belongs: every append fails
        for runs in (recorded, plain):
            first = runs.create(KEY, _doc(1))
            runs.compare_and_set(KEY, _doc(2), first)
        self.assertEqual(recorded.read(KEY).value, plain.read(KEY).value)
        self.assertEqual(recorded.read(KEY).revision, plain.read(KEY).revision)
        self.assertIsNone(tl.Timeline(self.home).read(KEY))

        def failing():
            raise RuntimeError("no clock")
        other = bridge._RecordingRuns(FilesystemRunRepository(self.home), timeline=tl.Timeline(self.home), clock=failing)
        second = RunKey("p", "r", 2)
        revision = other.create(second, _doc(1))
        self.assertEqual(other.read(second).revision, revision)
        self.assertIsNone(tl.Timeline(self.home).read(second), "a clock that fails records nothing and fails nothing")


class TwoClockInvarianceTests(unittest.TestCase):
    """The same scenarios driven with two different clocks: everything but the timelines is byte-identical."""

    @classmethod
    def setUpClass(cls):
        cls.a = build_store(clock=FakeClock(T0, 60.0), timeline=MemoryTimeline())
        cls.b = build_store(clock=FakeClock(4.1e9, 7.25), timeline=MemoryTimeline())

    def test_INVARIANT_state_artifacts_manifests_and_digests_do_not_depend_on_the_clock(self):
        runs_a, runs_b = self.a.driver._runs._store, self.b.driver._runs._store
        self.assertEqual(sorted(runs_a, key=_k), sorted(runs_b, key=_k))
        for key in runs_a:
            self.assertEqual(fsio.dump_json(runs_a[key][0]), fsio.dump_json(runs_b[key][0]), key)
            classified = run_state.classify_and_decode(runs_a[key][0])
            if classified.kind == "valid":
                other = run_state.classify_and_decode(runs_b[key][0]).state
                self.assertEqual(state_digest(classified.state), state_digest(other), key)
                dossiers = [c.get("audit_argument") for c in runs_a[key][0]["children"]]
                self.assertEqual(dossiers, [c.get("audit_argument") for c in runs_b[key][0]["children"]], key)
        self.assertEqual([(x.artifact_id, x.body) for x in self.a.driver.artifacts()],
                         [(x.artifact_id, x.body) for x in self.b.driver.artifacts()])
        self.assertTrue(any(json.loads(x.body).get("kind") == MANIFEST_KIND for x in self.a.driver.artifacts()))
        self.assertTrue(any(c.get("audit_argument") for v, _ in runs_a.values() if isinstance(v, dict)
                            for c in v.get("children", ())), "the store holds an auditor dossier")

    def test_INVARIANT_the_public_projections_do_not_depend_on_the_clock(self):
        for key in self.a.expect.values():
            handle = _handle(key)
            for name, request in (("GetRun", get_run), ("GetArgument", get_argument)):
                envelope = request(run_id=handle, request_id="same")
                self.assertEqual(self.a.driver.request(envelope), self.b.driver.request(envelope), (key, name))

    def test_only_the_timelines_differ(self):
        keys = set(self.a.timeline.raw)
        self.assertEqual(keys, set(self.b.timeline.raw))
        self.assertTrue(keys)
        for key in keys:
            ea, eb = self.a.timeline.read(key), self.b.timeline.read(key)
            strip = lambda es: [{k: v for k, v in e.items() if k != "at"} for e in es]  # noqa: E731
            self.assertEqual(strip(ea), strip(eb), key)
            self.assertNotEqual([e["at"] for e in ea], [e["at"] for e in eb], key)


class AttributionTests(unittest.TestCase):
    """U-at: every published time is its own write's; a missing entry leaves its write's artifacts unknown."""

    @classmethod
    def setUpClass(cls):
        cls.store = build_store()
        cls.view, cls.details = observed(Observer(DriverSource(cls.store)))

    def test_INVARIANT_every_step_time_is_the_time_of_the_write_that_first_committed_it(self):
        seen = 0
        for key, detail in self.details.items():
            if detail["steps"] is None:
                continue
            project, run, generation = key.split("/")
            entries = self.store.timeline.read(RunKey(project, run, int(generation)))
            at = {e["artifact_head"]: e["at"] for e in entries}
            history = _history(self.store.driver, RunKey(project, run, int(generation)))
            self.assertEqual(len(history), len(detail["steps"]), key)
            for (_, manifest), step in zip(history, detail["steps"]):
                self.assertIsNotNone(step["at"], (key, step))
                self.assertEqual(step["at"], at[manifest], (key, step))
                seen += 1
        self.assertGreater(seen, 100)

    def test_INVARIANT_a_write_without_its_entry_publishes_null_and_borrows_no_later_time(self):
        complete, run = _small()
        writes = complete.store.timeline.read(_key_of(run))
        self.assertGreaterEqual(len(writes), 6)
        times = {e["artifact_head"]: e["at"] for e in writes}
        orphaned = 0
        for k in range(1, len(writes) + 1):
            with self.subTest(missing=k):
                b, run = _small(timeline=MemoryTimeline(fail_on={k}))
                key = _key_of(run)
                detail = observed(Observer(DriverSource(b.store)))[1]["/".join((key.project_id, key.run_id, "1"))]
                lost = writes[k - 1]["artifact_head"]
                history = _history(b.drv, key)
                self.assertEqual(len(history), len(detail["steps"]))
                before = {e["artifact_head"] for e in writes[:k - 1]}  # the chain up to the missing entry
                for (_, head), step in zip(history, detail["steps"]):
                    if head in before:
                        self.assertEqual(step["at"], times[head], step)
                    else:
                        self.assertIsNone(step["at"], (step, "the chain is broken from the missing entry on"))
                orphaned += any(head == lost for _, head in history)
                if k > 1:
                    self.assertTrue(any(s["at"] is not None for s in detail["steps"]), "the prefix keeps its times")
        self.assertGreater(orphaned, 3, "the variants must orphan artifacts, or null would never be asked of a step")

    def test_run_times_are_their_own_writes_or_null(self):
        complete, run = _small()
        writes = complete.store.timeline.read(_key_of(run))
        row = _rows(view_of(Observer(DriverSource(complete.store))))[_row_key(run)]
        self.assertEqual((row["started"], row["updated"], row["ended"]),
                         (writes[0]["at"], writes[-1]["at"], writes[-1]["at"]))
        self.assertEqual(writes[-1]["status"], "stopped_residual")
        last = len(writes)
        b, run = _small(timeline=MemoryTimeline(fail_on={last}))
        row = _rows(view_of(Observer(DriverSource(b.store))))[_row_key(run)]
        self.assertEqual(row["state"], "stopped_residual")
        self.assertEqual((row["started"], row["updated"], row["ended"]), (writes[0]["at"], None, None),
                         "the latest and terminal write is unrecorded: neither borrows an earlier write's time")
        b, run = _small(timeline=MemoryTimeline(fail_on={1}))
        row = _rows(view_of(Observer(DriverSource(b.store))))[_row_key(run)]
        self.assertIsNone(row["started"])


def _e(seq, previous, revision, at, status="active", head=None):
    return {"seq": seq, "previous_revision": previous, "revision": revision, "artifact_head": head or f"h-{revision}",
            "status": status, "plan_revision": 1, "proposal_digest": "p", "receipts": [], "at": at}


class LinearChainTests(unittest.TestCase):
    """A timeline is one linear chain (ADR-0070): entry 1 is the only create and each entry N chains to entry N-1's
    revision. Where the chain breaks, every later entry is unknown; nothing after a break is borrowed."""

    def _trusted(self, entries):
        from application.observer import Times
        times = Times.of(entries)
        return times.started, {e["artifact_head"]: times.at(e["artifact_head"]) for e in entries}

    def test_a_valid_chain_times_every_write(self):
        started, at = self._trusted([_e(1, None, "A", 1.0), _e(2, "A", "B", 2.0), _e(3, "B", "C", 3.0)])
        self.assertEqual((started, at), (1.0, {"h-A": 1.0, "h-B": 2.0, "h-C": 3.0}))

    def test_a_fork_leaves_the_forking_entry_and_everything_after_it_unknown(self):
        started, at = self._trusted([_e(1, None, "A", 1.0), _e(2, "A", "B", 2.0), _e(3, "A", "C", 3.0),
                                     _e(4, "C", "D", 4.0)])
        self.assertEqual((started, at), (1.0, {"h-A": 1.0, "h-B": 2.0, "h-C": None, "h-D": None}))

    def test_a_create_out_of_order_makes_the_whole_timeline_unknown(self):
        started, at = self._trusted([_e(1, "X", "B", 2.0), _e(2, None, "A", 1.0)])
        self.assertEqual((started, at), (None, {"h-B": None, "h-A": None}))
        started, at = self._trusted([_e(1, None, "A", 1.0), _e(2, None, "Z", 5.0), _e(3, "Z", "Y", 6.0)])
        self.assertEqual((started, at), (1.0, {"h-A": 1.0, "h-Z": None, "h-Y": None}), "a second create breaks it")

    def test_a_backlink_to_an_earlier_revision_breaks_the_chain(self):
        started, at = self._trusted([_e(1, None, "A", 1.0), _e(2, "A", "B", 2.0), _e(3, "B", "A", 3.0, head="h-3"),
                                     _e(4, "A", "C", 4.0)])
        self.assertEqual((started, at), (1.0, {"h-A": 1.0, "h-B": 2.0, "h-3": None, "h-C": None}))

    def test_a_missing_entry_leaves_its_suffix_unknown_and_its_prefix_timed(self):
        started, at = self._trusted([_e(1, None, "A", 1.0), _e(2, "A", "B", 2.0), _e(4, "C", "D", 4.0),
                                     _e(5, "D", "E", 5.0)])
        self.assertEqual((started, at), (1.0, {"h-A": 1.0, "h-B": 2.0, "h-D": None, "h-E": None}))


class RecordingFailureTests(unittest.TestCase):
    def test_INVARIANT_an_unwritable_timeline_changes_no_result_and_its_times_are_null(self):
        recorded, run = _small(clock=FakeClock(T0, 60.0))
        broken, run_b = _small(clock=FakeClock(T0, 60.0), timeline=MemoryTimeline(fail_on=range(1, 10_000)))
        self.assertEqual(run, run_b)
        self.assertEqual(_results(recorded, run), _results(broken, run_b))
        view, details = observed(Observer(DriverSource(broken.store)))
        self.assertEqual(contract_problems(view, details), [])
        row = _rows(view)[_row_key(run)]
        self.assertEqual((row["started"], row["updated"], row["ended"]), (None, None, None))
        self.assertEqual({s["at"] for s in details[_row_key(run)]["steps"]}, {None})


class CorruptTimelineTests(unittest.TestCase):
    def test_a_corrupt_timeline_reads_as_unknown_and_a_truncated_one_keeps_what_it_holds(self):
        b, run = _small()
        key = _key_of(run)
        raw = b.store.timeline.raw[key]
        writes = tl.parse(raw)
        b.store.timeline.raw[key] = raw[:-5]  # the last line torn
        row = _rows(view_of(Observer(DriverSource(b.store))))[_row_key(run)]
        self.assertEqual((row["started"], row["updated"], row["ended"]), (writes[0]["at"], None, None))
        b.store.timeline.raw[key] = b"garbage\n" + raw
        view, details = observed(Observer(DriverSource(b.store)))
        row = _rows(view)[_row_key(run)]
        self.assertEqual((row["started"], row["updated"], row["ended"]), (None, None, None))
        self.assertEqual({s["at"] for s in details[_row_key(run)]["steps"]}, {None})
        self.assertEqual(contract_problems(view, details), [])


class GenerationAndAgreementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = build_store()
        cls.view, cls.details = observed(Observer(DriverSource(cls.store)))
        cls.rows = _rows(cls.view)

    def test_a_resumed_generation_has_its_own_timeline_and_its_own_start(self):
        second = self.store.expect["audit-failed"]
        project, run, generation = second.split("/")
        self.assertEqual(generation, "2")
        first = f"{project}/{run}/1"
        gen1 = self.store.timeline.read(RunKey(project, run, 1))
        gen2 = self.store.timeline.read(RunKey(project, run, 2))
        self.assertEqual(self.rows[first]["started"], gen1[0]["at"])
        self.assertEqual(self.rows[second]["started"], gen2[0]["at"])
        self.assertGreater(self.rows[second]["started"], self.rows[first]["ended"])
        self.assertEqual(gen2[0]["previous_revision"], None)

    def test_INVARIANT_ended_exists_exactly_when_the_core_reports_a_terminal_status(self):
        ended = 0
        for key, row in self.rows.items():
            if row["state"] is None:
                self.assertEqual((row["started"], row["updated"], row["ended"]), (None, None, None), key)
                continue
            self.assertEqual(row["ended"] is not None, row["state"] in TERMINAL, key)
            self.assertIsNotNone(row["started"], key)
            self.assertLessEqual(row["started"], row["updated"], key)
            if row["ended"] is not None:
                self.assertLessEqual(row["ended"], row["updated"], key)
                ended += 1
        self.assertGreater(ended, 3)

    def test_a_project_starts_when_its_earliest_run_started(self):
        for project in self.view["projects"]:
            started = [r["started"] for r in project["runs"] if r["started"] is not None]
            self.assertEqual(project["since"], min(started, default=None))

    def test_the_view_satisfies_its_contract(self):
        self.assertEqual(contract_problems(self.view, self.details), [])


CLOCK_MODULES = {"time", "datetime", "calendar", "zoneinfo"}
CLOCK_CALLS = {"time", "monotonic", "perf_counter", "time_ns", "monotonic_ns", "now", "utcnow", "today"}


def clock_reads(source: str, where: str) -> list[str]:
    """Every import of a clock module, call of a clock function and read of the service's ``_clock`` in ``source``."""
    found = []
    for node in ast.walk(ast.parse(source)):
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        found += [f"{where}:{node.lineno}: imports {n}" for n in names if n.split(".")[0] in CLOCK_MODULES]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in CLOCK_CALLS:
            found.append(f"{where}:{node.lineno}: calls .{node.func.attr}()")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr == "_clock":
            found.append(f"{where}:{node.lineno}: reads the service's clock")
    return found


class NoClockTests(unittest.TestCase):
    """The core and the application read no clock: a time is a label the bridge records beside a run."""

    def test_INVARIANT_core_and_application_read_no_clock(self):
        found = [hit for layer in ("core", "application") for path in sorted((PLUGIN / layer).rglob("*.py"))
                 for hit in clock_reads(path.read_text(encoding="utf-8"), str(path.relative_to(PLUGIN)))]
        self.assertEqual(found, [])

    def test_the_check_sees_each_way_of_reading_a_clock(self):
        for source in ("import time", "from datetime import datetime", "x = datetime.now()", "t = time.monotonic()",
                       "t = self._clock.read()", "import zoneinfo"):
            with self.subTest(source):
                self.assertTrue(clock_reads(source, "probe"))
        self.assertEqual(clock_reads("x = stamp_seq + 1", "probe"), [])


def _key_of(run: str) -> RunKey:
    from application.location import decode_handle
    return decode_handle(run)


def _row_key(run: str) -> str:
    key = _key_of(run)
    return f"{key.project_id}/{key.run_id}/{key.generation}"


def _history(drv, key: RunKey) -> list[tuple[str, str]]:
    """Oracle, from the artifacts the service appended (not the observer's reader): each committed artifact of ``key`` in
    commit order, with the manifest of the write that first committed it, walked from the state's head."""
    by_id = {a.artifact_id: json.loads(a.body) for a in drv.artifacts()}
    head = drv._runs.read(key).value["committed_artifact_head_id"]
    chain = []
    while head is not None:
        chain.append((head, by_id[head]))
        head = by_id[head]["parent"]
    return [(aid, mid) for mid, manifest in reversed(chain) for aid in manifest["artifact_ids"]]


if __name__ == "__main__":
    unittest.main()
