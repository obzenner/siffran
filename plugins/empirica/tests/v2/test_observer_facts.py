"""Contract v2's per-run and store-wide facts that are not the gate's own answers: one owner each, held here.

* ``latest_audit_verdict`` is the last of ``audit_verdicts`` (one selector, no second scan);
* the gate's claim state and ``dropped``'s why are one rule (``evaluation.claim_state_cause``);
* ``asked_at`` is the time of the write that recorded the ask: not a dialog's time, not reset by a re-presentation or a
  restart;
* ``id`` is a function of a run's key alone, and a run's island ``place`` its ordinal in its project's start order,
  published once the project is read in full, so no place given ever moves;
* aggregates count within ``since`` and say what was not read and what has no time;
* a poll with a budget is progressive and honest about coverage;
* the v2 endpoints keep the server's safety (Host allowlist, ETag/304) and refuse what they do not know.
"""
from __future__ import annotations

import gzip
import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import core.evaluation as evaluation
import core.observer as core_observer
from adapters.observer import civil
from adapters.observer.server import Sources, serve
from application import observer_index as index
from application.observer import Observer
from assertions import observe_action
from governance_setup import sized_configure_run
from observer_store import STEP, T0, walk_pages, DemoSource, DriverSource, ScaleSource, build_demo_store, build_store, demo_goals

import test_observer_agreement as agreement

ROOT = Path(__file__).resolve().parents[4]


class OneOwnerTests(unittest.TestCase):
    def test_the_latest_audit_verdict_is_the_last_of_every_verdict(self):
        first, last = {"kind": "audit_verdict", "verdict": "fail"}, {"kind": "audit_verdict", "verdict": "pass"}
        run = SimpleNamespace(history=())  # a scan of its own would find nothing here
        with mock.patch.object(evaluation, "audit_verdicts", lambda snapshot: (first, last)):
            self.assertIs(evaluation.latest_audit_verdict(run), last, "no second scan of the history")
        with mock.patch.object(evaluation, "audit_verdicts", lambda snapshot: ()):
            self.assertIsNone(evaluation.latest_audit_verdict(SimpleNamespace(history=(last,))))

    def test_the_gate_and_dropped_read_one_claim_state_rule(self):
        b, run = agreement._refuted()
        observer = Observer(DriverSource(b.store))
        self.assertEqual([d["why"] for d in observer.details()[observer.rows()[0]["key"]]["dropped"]], ["refuted"])
        cause = {"kind": "spike", "artifact_id": "x", "command": "the rule's own"}
        with mock.patch.object(core_observer, "claim_state_cause", lambda *a: ("discarded", cause)):
            fresh = Observer(DriverSource(b.store))
            dropped = fresh.details()[fresh.rows()[0]["key"]]["dropped"]
        self.assertTrue(dropped and all(d["why"] == "spike_failed" and d["command"] == "the rule's own" for d in dropped),
                        "dropped's why is whatever the gate's rule says")
        with mock.patch.object(evaluation, "claim_state_cause", lambda *a: ("approved", None)):
            self.assertEqual(evaluation.local_claim_state(None, {}, set()), "approved", "and the gate's state is its first part")


class AskedAtTests(unittest.TestCase):
    def test_asked_at_is_when_the_ask_was_recorded_not_when_a_dialog_showed_it(self):
        b, run = agreement._evidence_waiting()()
        first = Observer(DriverSource(b.store)).rows()[0]
        steps = Observer(DriverSource(b.store)).details()[first["key"]]["steps"]
        cite = next(s for s in steps if s["kind"] == "research")
        self.assertEqual((first["ask"], first["asked_at"]), ("confirm_source", cite["at"]))
        agreement._mediate(b, run, can_present=True)  # a host shows the question, and its human leaves it for later
        again = Observer(DriverSource(b.store)).rows()[0]  # a restart: a new observer over the same store
        self.assertEqual(again["ask"], "confirm_source")
        self.assertEqual(again["asked_at"], first["asked_at"], "neither the dialog nor a restart resets it")
        self.assertNotEqual(again["updated"], first["updated"], "though the run was written since")

    def test_a_configuration_ask_and_answer_are_timed_by_the_writes_that_recorded_them(self):
        """The timeline names each write's configuration (ADR-0070), so the write that proposed the waiting revision,
        and the write that recorded each dialog's answer, are known exactly."""
        appended = []
        timelines = []
        entries = lambda: timelines[0].read(next(iter(timelines[0].raw)))  # noqa: E731 - the one run's entries
        driver = type(agreement._Builder().drv)
        approve = driver.approve_governance

        def watched(self, run_id):
            timelines[:] = [self._runs._timeline]
            before = len(entries())
            approve(self, run_id)
            state = self._runs.read(next(iter(self._runs._store))).value
            answer = next(r["id"] for r in reversed(state["governance"]["receipts"]) if r["outcome"] == "approve")
            # The write that recorded the answer: the first appended entry (in append order) whose state lists it.
            appended.append(next(e["at"] for e in entries()[before:] if answer in e["receipts"]))
        with mock.patch.object(driver, "approve_governance", watched):
            b, run = agreement._begin("amendment")
        before = len(entries())
        b.a.dispatch(b.drv, observe_action(run_id=run, action={
            "kind": "configure_run", "budgets": {"max_passes": 3, "max_spawns": 0, "max_audit_spawns": 1},
            "rationale": "Small amended scope."}))
        proposed = entries()[before]["at"]
        observer = Observer(DriverSource(b.store))
        row = observer.rows()[0]
        self.assertEqual((row["ask"], row["asked_at"]), ("approve_revision", proposed))
        answers = [d for d in observer.details()[row["key"]]["decisions"] if d["asked"] == "approve_configuration"]
        self.assertEqual([d["at"] for d in answers], appended)
        self.assertTrue(appended and all(at is not None for at in appended))


class PositionTests(unittest.TestCase):
    def test_an_id_is_a_function_of_the_key_alone(self):
        store = build_store()
        whole = {r["key"]: r["id"] for r in Observer(ScaleSource(store, 60)).rows()}
        part = {r["key"]: r["id"] for r in Observer(ScaleSource(store, 30)).rows()}
        self.assertTrue(part and all(whole[k] == v for k, v in part.items()), "unchanged by adding or removing runs")
        self.assertEqual({r["key"]: r["id"] for r in Observer(ScaleSource(store, 60)).rows()}, whole, "and across restarts")

    def test_a_place_is_the_ordinal_in_its_projects_start_order(self):
        summary = Observer(ScaleSource(build_store(), 60, projects=4)).summary()
        rows = {r["id"]: r for r in Observer(ScaleSource(build_store(), 60, projects=4)).rows()}
        m = summary["marks"]
        for k, project in enumerate(summary["projects"]):
            held = [i for i in range(len(m["id"])) if m["project"][i] == k]
            timed = sum(rows[m["id"][i]]["started"] is not None for i in held)
            band = max(index.UNTIMED_FROM, timed)
            self.assertEqual(sorted(m["place"][i] for i in held),
                             [*range(timed), *range(band, band + len(held) - timed)],
                             "0, 1, 2, ... none skipped; the untimed in the outer band")
            by_place = sorted(held, key=lambda i: m["place"][i])
            self.assertEqual([rows[m["id"][i]]["key"] for i in by_place],
                             [r["key"] for r in sorted((rows[m["id"][i]] for i in held), key=index.start_order)])
            self.assertEqual(len(held), project["runs"])

    def test_runs_started_later_take_the_next_places_and_move_none(self):
        def row(project, n, started):
            return {"key": f"{project}/r{n:03d}/1", "project": project, "started": started}
        before = [row(p, n, 100.0 + 7 * ((n * 13) % 11)) for p in ("a", "b") for n in range(9)] + [row("a", 99, None)]
        later = before + [row("a", 50, 1_000.0), row("b", 30, 2_000.0), row("c", 1, 5.0)]
        old, new = index.places(before, ["a", "b"]), index.places(later, ["a", "b", "c"])
        self.assertTrue(all(new[k] == v for k, v in old.items()), "every place kept, the untimed one's too")
        self.assertEqual((new["a/r050/1"], new["b/r030/1"], new["c/r001/1"]), (9, 9, 0), "each new run takes the next place")
        self.assertEqual(new["a/r099/1"], index.UNTIMED_FROM, "an untimed run sits in the outer band")
        self.assertEqual(index.places(list(reversed(later)), ["c", "b", "a"]), new, "the rows' order changes nothing")
        self.assertEqual(set(index.places(later, ["a"]).values()) - set(range(11)) - {index.UNTIMED_FROM}, {None},
                         "unread projects: no place yet")

    def test_a_new_timed_run_never_moves_an_untimed_one_until_the_timed_runs_fill_the_band(self):
        def row(n, started):
            return {"key": f"p/r{n:04d}/1", "project": "p", "started": started}
        untimed = [row(9000, None), row(9001, None)]
        timed = [row(n, float(n)) for n in range(index.UNTIMED_FROM)]
        for count in range(index.UNTIMED_FROM + 1):
            at = index.places(timed[:count] + untimed, ["p"])
            self.assertEqual((at["p/r9000/1"], at["p/r9001/1"]), (index.UNTIMED_FROM, index.UNTIMED_FROM + 1), count)
        full = index.places(timed + [row(5000, 5000.0)] + untimed, ["p"])
        self.assertEqual(full["p/r5000/1"], index.UNTIMED_FROM, "the band is full: the next timed run takes its first place")
        self.assertEqual((full["p/r9000/1"], full["p/r9001/1"]), (index.UNTIMED_FROM + 1, index.UNTIMED_FROM + 2),
                         "and the untimed ones follow every timed one")
        self.assertEqual(len(set(full.values())), len(full), "no two runs share a place")

    def test_a_project_whose_first_run_starts_later_takes_the_next_slot_whatever_its_id(self):
        old = [{"id": "m", "since": 100.0}, {"id": "z", "since": 200.0}, {"id": "q", "since": None}]
        new = old + [{"id": "a", "since": 300.0}]
        self.assertEqual([p["id"] for p in index.island_order(old)], ["m", "z", "q"])
        self.assertEqual([p["id"] for p in index.island_order(new)], ["m", "z", "a", "q"], "an earlier id appends")
        self.assertEqual([p["id"] for p in index.island_order([{"id": "b", "since": 1.0}, {"id": "a", "since": 1.0}])],
                         ["a", "b"], "ties by id")
        source = ScaleSource(build_store(), 12, projects=3)
        summary = Observer(source).summary()
        since = [p["since"] for p in summary["projects"]]
        self.assertEqual(since, sorted(since), "the summary lists the islands in that order")

    def test_a_cold_start_never_moves_a_place_it_gave(self):
        source = ScaleSource(build_store(), 50, projects=5)
        observer = Observer(source, clock=iter(range(10_000)).__next__)
        given: dict[str, int] = {}
        steps = 0
        while True:
            summary = observer.summary(budget=3)  # the fake clock: about three runs per poll
            m = summary["marks"]
            for i, place in enumerate(m["place"]):
                if place is not None:
                    self.assertEqual(given.setdefault(m["id"][i], place), place, "a place given is never moved")
            steps += 1
            if summary["coverage"]["pending"] == 0:
                break
        self.assertGreater(steps, 3)
        final = Observer(source).summary()["marks"]
        self.assertEqual(dict(zip(final["id"], final["place"])), given, "and a restart gives the same places")


class AggregateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = build_store()
        cls.observer = Observer(DriverSource(cls.store))
        cls.rows = cls.observer.rows()

    def test_every_count_says_what_was_not_read(self):
        summary = self.observer.summary()
        unread = sum(r["diagnostic"] is not None for r in self.rows)
        self.assertGreater(unread, 0)
        self.assertEqual(summary["aggregates"]["not_read"], unread)
        self.assertEqual(sum(summary["facets"]["standing"].values()), len(self.rows) - unread)

    def test_a_window_counts_facts_by_their_time_and_says_which_have_none(self):
        everything = self.observer.summary()["aggregates"]
        middle = T0 + STEP * 300
        window = self.observer.summary(middle)["aggregates"]
        self.assertEqual(window["since"], middle)
        self.assertLess(sum(window["by"]["standing"].values()), sum(everything["by"]["standing"].values()))
        updated = [r for r in self.rows if r["diagnostic"] is None and r["updated"] is not None and r["updated"] >= middle]
        self.assertEqual(sum(window["by"]["standing"].values()), len(updated))
        untimed = [r for r in self.rows if r["diagnostic"] is None and r["updated"] is None]
        self.assertEqual(window["untimed_runs"], len(untimed))
        for name in index.FACTS:
            self.assertLessEqual(window["facts"][name], everything["facts"][name])
        for row in self.observer.runs({"since": str(middle)}, limit=200)["rows"]:
            self.assertGreaterEqual(row["updated"], middle)

    def test_the_aggregates_serve_the_total_of_the_runs_they_count(self):
        """The value strip's "runs" is the observer's count (P3): never a sum the UI makes of the standings."""
        for since in (None, T0 + STEP * 300):
            counted = self.observer.summary(since)["aggregates"]
            self.assertEqual(counted["total"], sum(counted["by"]["standing"].values()), since)
        scratch = index.aggregates(self.rows, {}, since=None)
        self.assertEqual(scratch["total"], sum(r["diagnostic"] is None for r in self.rows))
        self.assertEqual(index.aggregates(self.rows, {}, since=T0 + STEP * 300)["total"],
                         self.observer.summary(T0 + STEP * 300)["aggregates"]["total"])

    def test_a_fact_without_a_time_is_untimed_inside_a_window_never_zero_or_inside(self):
        rows = [{"key": "k", "diagnostic": None, "updated": None, "standing": "not_ended", "ask": None, "project": "p",
                 "session": None, "mode": "auto", "audit": {"verdict": "failed"}}]
        counted = index.aggregates(rows, {"k": {"failed_audits": [None], "refutations": [], "decisions": []}}, since=1.0)
        self.assertEqual((counted["untimed_runs"], counted["untimed"]["failed_audits"], counted["facts"]["failed_audits"]),
                         (1, 1, 0))


class DecisionCountTests(unittest.TestCase):
    """The value strip counts every human answer but ``later``, from the one owner (``core.observer.decisions``), by
    what was asked; the ``decided`` flag follows the same owner."""

    ANSWERS = [{"asked": "approve_configuration", "answer": "approve", "at": 1.0},
               {"asked": "consent_stop", "answer": "approve", "at": 2.0},
               {"asked": "decide_claim", "answer": "reject", "at": 3.0},
               {"asked": "confirm_source", "answer": "later", "at": 4.0},
               {"asked": "freeze_scope", "answer": "approve", "at": None}]

    def test_every_answer_but_later_is_a_decision_by_its_ask(self):
        held = index.facts({"decisions": self.ANSWERS})
        self.assertEqual(held["decisions"], [1.0, 2.0, 3.0, None])
        self.assertEqual({a: held[f"decisions:{a}"] for a in core_observer.ASKS},
                         {"approve_configuration": [1.0], "approve_revision": [], "decide_claim": [3.0],
                          "confirm_source": [], "consent_stop": [2.0], "freeze_scope": [None]})

    def test_the_aggregates_break_decisions_down_by_ask(self):
        rows = [{"key": "k", "diagnostic": None, "updated": 3.0, "standing": "not_ended", "ask": None, "project": "p",
                 "session": None, "mode": "deliberative", "audit": {"verdict": "none"}}]
        counted = index.aggregates(rows, {"k": index.facts({"decisions": self.ANSWERS})}, since=2.0)
        self.assertEqual(counted["facts"]["decisions"], 2)
        self.assertEqual(counted["decisions_by"]["consent_stop"], {"runs": 1, "facts": 1, "untimed": 0})
        self.assertEqual(counted["decisions_by"]["approve_configuration"], {"runs": 0, "facts": 0, "untimed": 0})
        self.assertEqual(counted["decisions_by"]["freeze_scope"], {"runs": 0, "facts": 0, "untimed": 1})

    def test_INVARIANT_the_decided_flag_is_any_answer_but_later(self):
        observer = Observer(DriverSource(build_store()))
        details, seen = observer.details(), set()
        for row in observer.rows():
            if row["flags"] is None:
                continue
            answers = details[row["key"]]["decisions"]
            self.assertEqual(row["flags"]["decided"], any(d["answer"] != "later" for d in answers), row["key"])
            seen |= {d["asked"] for d in answers if d["answer"] != "later"}
        self.assertIn("approve_configuration", seen, "a configuration answer alone makes a run decided")


class DecisionShapeTests(unittest.TestCase):
    """A decision is attached to what it decides (P3): the claim it is about (a decided claim, or the claim a confirmed
    source supports), the source, or the configuration's plan revision; a decision about the run's end names none. Never
    one overloaded ``subject`` string the reader has to guess the meaning of."""

    def test_a_decision_names_the_claim_the_source_or_the_plan_it_decides(self):
        observer = Observer(DriverSource(build_store()))
        rows, seen = {r["key"]: r for r in observer.rows()}, set()
        for key, detail in observer.details().items():
            claims = {c["id"] for c in rows[key]["claims"] or ()} | {d["claim"] for d in detail["dropped"] or ()}
            for d in detail["decisions"] or ():
                self.assertEqual(set(d), {"asked", "claim", "source", "plan", "answer", "effect", "at"}, key)
                seen.add(d["asked"])
                if d["asked"] == "decide_claim":
                    self.assertIn(d["claim"], claims, key)
                    self.assertEqual((d["source"], d["plan"]), (None, None))
                elif d["asked"] == "confirm_source":
                    self.assertIn(d["claim"], claims, key)
                    self.assertIsInstance(d["source"], str)
                    self.assertIsNone(d["plan"])
                elif d["asked"] in {"approve_configuration", "approve_revision"}:
                    self.assertIsInstance(d["plan"], int)
                    self.assertEqual((d["claim"], d["source"]), (None, None))
                else:
                    self.assertEqual((d["claim"], d["source"], d["plan"]), (None, None, None), key)
        self.assertLessEqual({"decide_claim", "confirm_source", "approve_configuration", "consent_stop"}, seen)


class DemoStoreTests(unittest.TestCase):
    """``make ui-demo``'s store (``observer_store.DemoSource``): what the real service wrote, shifted onto a calendar that
    ends now, never after it (P3: "asked in 2 hr." was a time the generator put in the future), with goals, claims and
    projects varied from a seed so that a search finds a few runs, not a third of them."""

    NOW = T0 + 90 * 86400

    @classmethod
    def setUpClass(cls):
        cls.source = DemoSource(build_demo_store(24), 160, now=cls.NOW)
        cls.observer = Observer(cls.source)
        cls.rows = cls.observer.rows()

    def test_no_time_in_the_demo_is_after_now(self):
        details = self.observer.details()
        for row in self.rows:
            for name in ("started", "updated", "ended", "asked_at"):
                if row[name] is not None:
                    self.assertLessEqual(row[name], self.NOW, (row["key"], name))
            for step in (details[row["key"]] or {}).get("steps") or ():
                if step["at"] is not None:
                    self.assertLessEqual(step["at"], self.NOW, row["key"])
        self.assertTrue(any(r["updated"] is not None and r["updated"] > self.NOW - 86400 for r in self.rows),
                        "the calendar still reaches today")

    def test_the_demo_varies_its_goals_and_projects(self):
        goals = [r["goal"] for r in self.rows if r["goal"]]
        worded = {"".join(ch for ch in g if not ch.isdigit()) for g in goals}
        self.assertGreaterEqual(len(worded), 40, "goals that differ in words, not by a number")
        self.assertGreaterEqual(len({g.split()[0] for g in goals}), 8, "more than a handful of verbs")
        top = max(goals.count(g) for g in set(goals))
        self.assertLessEqual(top, len(goals) // 10, "no goal is a tenth of the store")
        self.assertGreaterEqual(len(self.observer.summary()["projects"]), 14)

    def test_the_runs_that_need_a_human_are_spread_across_the_projects(self):
        needs = self.observer.summary()["needs"]
        projects = [n["key"].split("/")[0] for n in needs]
        self.assertGreaterEqual(len(needs), 4, "the demo has runs that need a human")
        self.assertLessEqual(max(projects.count(p) for p in set(projects)), len(needs) // 2,
                             "no project holds more than half of them")

    def test_the_demo_s_hosts_are_claude_code_and_pi(self):
        """What a host records at StartRun (``adapters.invocation.provenance``: ``claude``, ``pi``), never a test label."""
        hosts = {r["session"]["host"] for r in self.rows if r["session"]}
        self.assertEqual(hosts, {"claude", "pi"})
        self.assertEqual({n["host"] for n in self.observer.summary()["needs"]} - {"claude", "pi"}, set())

    def test_a_session_s_host_is_the_host_whose_folder_holds_it(self):
        """A recorded session locator names its host's own folder (Pi under ``.pi``, Claude Code under ``.claude``): the
        generator never records a Pi run whose session sits under ``~/.claude`` (P4)."""
        folders = {"pi": "/.pi/", "claude": "/.claude/"}
        located = [r["session"] for r in self.rows if r["session"] and r["session"]["locator"]]
        self.assertTrue(located, "the demo records some session locators")
        for s in located:
            self.assertIn(folders[s["host"]], s["locator"], s)

    def test_the_goals_are_deterministic_from_the_seed_and_distinct(self):
        self.assertEqual(demo_goals(40, seed=3), demo_goals(40, seed=3))
        self.assertNotEqual(demo_goals(40, seed=3), demo_goals(40, seed=4))
        self.assertEqual(len(set(g for g, _ in demo_goals(120, seed=3))), 120)


class RevisionTests(unittest.TestCase):
    """A configuration the human approved twice is two asks of different kinds: the first approval of the configuration
    and one revision of it. 5.0.0 does not record whether a revision raised a ceiling, so a revision is any governance ask
    after the first approval, counted exactly."""

    def _twice(self):
        b, run = agreement._begin("revised")
        b.a.dispatch(b.drv, observe_action(run_id=run, action=sized_configure_run(
            budgets={"max_passes": 3, "max_spawns": 0, "max_audit_spawns": 1}, rationale="One claim, no spikes.")))
        return b, run

    def test_a_revision_waiting_asks_for_a_revision_whatever_it_changes(self):
        b, run = self._twice()
        row = Observer(DriverSource(b.store)).rows()[0]
        self.assertEqual(row["ask"], "approve_revision")

    def test_two_approvals_publish_one_configuration_and_one_revision(self):
        b, run = self._twice()
        b.drv.approve_governance(run)
        observer = Observer(DriverSource(b.store))
        key = observer.rows()[0]["key"]
        asked = [d["asked"] for d in observer.details()[key]["decisions"]]
        self.assertEqual(asked, ["approve_configuration", "approve_revision"])
        by = observer.summary()["aggregates"]["decisions_by"]
        self.assertEqual({a: by[a]["facts"] for a in ("approve_configuration", "approve_revision")},
                         {"approve_configuration": 1, "approve_revision": 1})
        self.assertNotIn("approve_raise", core_observer.ASKS)


class CursorTests(unittest.TestCase):
    """A cursor belongs to the published view it was given in: after any change it is stale (an explicit answer with
    the current token), so a walk restarts and never skips or repeats a row."""

    def setUp(self):
        self.source = ScaleSource(build_store(), 60, projects=4)
        self.observer = Observer(self.source)

    def _walk(self, filters, limit=7):
        rows, page = walk_pages(lambda c, n: self.observer.runs(filters, c, n), limit)
        return [r["key"] for r in rows], page

    def _change(self, key, standing_from):
        """Give ``key`` another template whose standing differs (a run changing between pages)."""
        for n in range(1, 40):
            self.source.replace(key, self.source._map[key][0] + n)
            if {r["key"]: r for r in self.observer.rows()}[_key(key)]["standing"] != standing_from:
                return
        raise AssertionError("no template changes the standing")

    def _check(self, filters, pick):
        first = self.observer.runs(filters, None, 7)
        self.assertEqual(first["token"], self.observer.summary()["token"])
        seen = {r["key"] for r in first["rows"]}
        rows = {r["key"]: r for r in self.observer.rows()}
        target = next(k for k in self.source.keys() if _key(k) not in seen and pick(rows[_key(k)]))
        self._change(target, rows[_key(target)]["standing"])
        with self.assertRaises(index.StaleCursor) as stale:
            self.observer.runs(filters, first["next"], 7)
        self.assertEqual(stale.exception.token, self.observer.summary()["token"])
        walked, last = self._walk(filters)
        self.assertEqual(len(walked), len(set(walked)), "exactly once")
        self.assertEqual(len(walked), last["total"])
        self.assertEqual(set(walked), {r["key"] for r in self.observer.rows() if index.matches(r, filters)})

    def test_a_standing_change_between_pages_makes_the_cursor_stale(self):
        self._check({}, lambda r: r["diagnostic"] is None)

    def test_a_run_entering_a_filter_between_pages_makes_the_cursor_stale(self):
        self._check({"standing": "not_ended"}, lambda r: r["standing"] != "not_ended" and r["diagnostic"] is None)

    def test_a_change_in_another_project_between_pages_makes_the_cursor_stale(self):
        project = self.source.keys()[0].project_id
        self._check({"project": project}, lambda r: r["project"] != project and r["diagnostic"] is None)

    def test_an_unchanged_view_keeps_its_cursor(self):
        walked, last = self._walk({})
        self.assertEqual(len(walked), len(set(walked)))
        self.assertEqual(len(walked), last["total"])


def _key(k):
    return f"{k.project_id}/{k.run_id}/{k.generation}"


class FlagFilterTests(unittest.TestCase):
    """The value strip's numbers each link to the runs that carry the fact: a runs page filters by a flag the row holds
    (``failed_audit``, ``refuted``, ``decided``, ``changed_since_audit``), the very flags the marks carry."""

    def test_a_page_filtered_by_a_flag_is_exactly_the_rows_that_hold_it(self):
        observer = Observer(DriverSource(build_store()))
        rows = observer.rows()
        for flag in index.FLAGS:
            with self.subTest(flag=flag):
                want = sorted(r["key"] for r in rows if r["flags"] and r["flags"][flag])
                self.assertTrue(want, "the store has such a run")
                page = observer.runs({"flag": flag}, None, 200)
                self.assertEqual((sorted(r["key"] for r in page["rows"]), page["total"]), (want, len(want)))

    def test_a_flag_that_is_none_is_refused_and_so_is_a_state_of_it(self):
        observer = Observer(DriverSource(build_store()))
        with self.assertRaises(ValueError):
            observer.runs({"flag": "shiny"}, None, 10)


class WindowFlagTests(unittest.TestCase):
    """A flag within a window is the event's own time, the time the aggregates count it by (AST-6): the strip's linked
    runs and the Universe's lit marks are the runs the strip counted."""

    def setUp(self):
        rows = json.loads((ROOT / "contracts/empirica-ui-view/v1/fixtures/rows.json").read_text())
        self.row = next(r for r in rows if r["flags"] and r["flags"]["failed_audit"])

    def _three(self, row, at, since):
        facts = {row["key"]: {"failed_audits": [at], "refutations": [], "decisions": []}}
        counted = index.aggregates([row], facts, since)["runs"]["failed_audits"]
        linked = index.page([row], {row["project"]: "P"},
                            lambda r: index.matches(r, {"flag": "failed_audit", "since": str(since)}, facts[r["key"]]),
                            None, 100, "sha256:" + "0" * 64)["total"]
        lit = index.marks([row], [row["project"]], [row["project"]], facts, since)["window_flags"][0] & 1
        return counted, linked, lit

    def test_an_old_event_with_recent_unrelated_writes_is_not_in_the_window(self):
        self.assertEqual(self._three({**self.row, "updated": 7200.0}, 100.0, 3600.0), (0, 0, 0))

    def test_a_recent_event_is_in_the_window_whatever_the_runs_latest_write_says(self):
        self.assertEqual(self._three({**self.row, "updated": 100.0}, 7200.0, 3600.0), (1, 1, 1))
        self.assertEqual(self._three({**self.row, "updated": None}, 7200.0, 3600.0)[1:], (1, 1))

    def test_without_a_window_the_window_flags_are_the_flags(self):
        observer = Observer(DriverSource(build_store()))
        marks = observer.summary()["marks"]
        self.assertEqual(marks["window_flags"], marks["flags"])
        windowed = observer.summary(T0 + STEP * 300)
        for flag, fact in index.EVENT_OF.items():
            page = observer.runs({"flag": flag, "since": str(T0 + STEP * 300)}, None, 200)
            bit = 1 << index.FLAGS.index(flag)
            self.assertEqual(page["total"], sum(bool(b & bit) for b in windowed["marks"]["window_flags"]), flag)
            self.assertEqual(page["total"], windowed["aggregates"]["runs"][fact], flag)


class StaleCoverageTests(unittest.TestCase):
    def test_a_known_stale_run_is_reindexing_and_moves_the_token(self):
        """A warm budgeted pass that found a run changed but did not reach it says so (AST-5): never the old view as
        current under its old token (no 304 for it)."""
        source = ScaleSource(build_store(), 6, projects=1)
        observer = Observer(source, clock=iter(range(10_000)).__next__)
        first = observer.summary()
        key = source.keys()[0]
        source.replace(key, source._map[key][0] + 1)
        observer.serve_from_index()
        left = observer.index(0.5)
        stale = observer.summary()
        self.assertEqual(left, 1, "the indexer keeps going")
        self.assertEqual(stale["coverage"], {"runs": 6, "read": 6, "unreadable": 0, "partial": 0, "pending": 0,
                                             "reindexing": 1})
        self.assertNotEqual(stale["token"], first["token"])
        observer.index()
        self.assertEqual(observer.summary()["coverage"]["reindexing"], 0)
        self.assertEqual(observer.summary()["token"], Observer(source).summary()["token"], "then current")


class ResolveTests(unittest.TestCase):
    def test_a_full_key_wins_a_short_id_is_one_run_and_a_shared_one_is_a_collision(self):
        a, b = "p/a/1", "p/b/1"
        pairs = [(a, "A"), (b, "B")]
        self.assertEqual((index.resolve(pairs, a), index.resolve(pairs, index.short_id(b))), ("A", "B"))
        self.assertIsNone(index.resolve(pairs, "nope"))
        with mock.patch.object(index, "short_id", lambda key: "same"):
            with self.assertRaises(index.AmbiguousId) as clash:
                index.resolve(pairs, "same")
            self.assertEqual(clash.exception.keys, (a, b))
            self.assertEqual(index.resolve(pairs, b), "B", "a full key is never ambiguous")


class CalendarTests(unittest.TestCase):
    """The calendar's cells are the observer's (SOL-01): per project and civil day in the viewer's IANA zone (AST-3), so a
    day that is 23 or 25 hours long still holds exactly its own runs."""

    def _cells(self, times, tz):
        rows = [{"key": f"p/r{n}/1", "project": "p", "updated": at, "standing": s}
                for n, (at, s) in enumerate(times)]
        c = index.calendar(rows, ["p"], tz, civil.zone(tz))
        return dict(zip(c["day"], zip(c["runs"], (index.STANDINGS[s] for s in c["standing"]), c["start"])))

    def test_spring_forward_keeps_each_run_on_its_civil_day(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
        at = lambda *t: datetime(*t, tzinfo=ny).timestamp()  # noqa: E731
        cells = self._cells([(at(2026, 3, 9, 0, 30), "proven"), (at(2026, 3, 8, 23, 30), "needs_human"),
                             (at(2026, 3, 8, 0, 10), "proven")], "America/New_York")
        self.assertEqual({d: v[:2] for d, v in cells.items()},
                         {"2026-03-08": (2, "needs_human"), "2026-03-09": (1, "proven")})
        self.assertEqual(cells["2026-03-09"][2] - cells["2026-03-08"][2], 23 * 3600, "a 23-hour day")

    def test_fall_back_keeps_each_run_on_its_civil_day(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
        at = lambda *t: datetime(*t, tzinfo=ny).timestamp()  # noqa: E731
        cells = self._cells([(at(2026, 11, 1, 23, 30), "proven"), (at(2026, 11, 2, 0, 30), "audit_pending")],
                            "America/New_York")
        self.assertEqual({d: v[:2] for d, v in cells.items()},
                         {"2026-11-01": (1, "proven"), "2026-11-02": (1, "audit_pending")})
        self.assertEqual(cells["2026-11-02"][2] - cells["2026-11-01"][2], 25 * 3600, "a 25-hour day")

    def test_the_zone_is_checked_and_the_summary_is_versioned_by_it(self):
        for bad in ("Mars/Olympus", "../etc/passwd", "/etc/localtime", "a" * 80, "Europe/../UTC"):
            with self.assertRaises(ValueError, msg=bad):
                civil.tz_of(bad)
        self.assertEqual((civil.tz_of(None), civil.tz_of("Asia/Kathmandu")), ("UTC", "Asia/Kathmandu"))
        for at in (0.0, 951_782_400.0, 1_790_586_000.0, -86_400.5):  # the pure UTC days agree with the zone database's
            self.assertEqual(index.utc_day(at), civil.zone("Etc/UTC")(at), at)
        with self.assertRaises(ValueError):
            Observer(DriverSource(build_store())).summary(None, "Asia/Tokyo")  # no zones given: UTC alone
        observer = Observer(DriverSource(build_store()), zones=civil.zone)
        utc, far = observer.summary(), observer.summary(None, "Pacific/Kiritimati")
        self.assertEqual((utc["calendar"]["tz"], far["calendar"]["tz"]), ("UTC", "Pacific/Kiritimati"))
        self.assertNotEqual(utc["token"], far["token"])
        self.assertEqual(sum(utc["calendar"]["runs"]), sum(far["calendar"]["runs"]))


class ProgressiveTests(unittest.TestCase):
    def test_a_budgeted_poll_never_shows_a_false_total(self):
        observer = Observer(ScaleSource(build_store(), 50), clock=iter(range(10_000)).__next__)
        seen = []
        while True:
            coverage = observer.summary(budget=3)["coverage"]  # the fake clock: about three runs per poll
            seen.append(coverage)
            self.assertEqual(coverage["runs"], 50)
            self.assertEqual(sum(coverage[k] for k in ("read", "unreadable", "partial", "pending")), 50)
            if coverage["pending"] == 0:
                break
        self.assertGreater(len(seen), 3)
        self.assertEqual([c["pending"] for c in seen], sorted((c["pending"] for c in seen), reverse=True))


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = build_store()
        cls.server = serve(Sources(Observer(DriverSource(cls.store), zones=civil.zone), identity="facts"))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.host = f"127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path, **headers):
        request = urllib.request.Request(f"http://{self.host}{path}", headers={"Host": self.host, **headers})
        try:
            with urllib.request.urlopen(request) as answer:
                return answer.status, answer.read(), dict(answer.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)

    def test_the_summary_has_an_etag_a_304_and_gzip(self):
        status, body, headers = self._get("/api/summary")
        self.assertEqual(status, 200)
        summary = json.loads(body)
        self.assertEqual((summary["contract"], f'"{summary["token"]}"'), ("empirica-ui-view/v1", headers["ETag"]))
        self.assertEqual(self._get("/api/summary", **{"If-None-Match": headers["ETag"]})[0], 304)
        status, zipped, headers = self._get("/api/summary", **{"Accept-Encoding": "gzip"})
        self.assertEqual(json.loads(gzip.decompress(zipped)), summary)
        windowed = json.loads(self._get(f"/api/summary?since={T0 + STEP * 300}")[1])
        self.assertNotEqual(windowed["token"], summary["token"], "a window is another answer")
        zoned = json.loads(self._get("/api/summary?tz=Asia%2FTokyo")[1])
        self.assertEqual(zoned["calendar"]["tz"], "Asia/Tokyo")
        self.assertNotEqual(zoned["token"], summary["token"], "a zone is another answer")
        self.assertEqual(self._get("/api/summary?tz=Asia%2FTokyo", **{"If-None-Match": headers["ETag"]})[0], 200)

    def test_runs_search_and_run_answer_and_refuse_what_they_do_not_know(self):
        page = json.loads(self._get("/api/runs?standing=needs_human")[1])
        self.assertTrue(page["rows"] and all(r["standing"] == "needs_human" for r in page["rows"]))
        for bad in ("/api/runs?standing=nope", "/api/runs?limit=201", "/api/runs?colour=red", "/api/runs?since=nan",
                    "/api/summary?since=soon", "/api/summary?tz=Mars%2FOlympus", "/api/runs?cursor=nope", "/api/run"):
            self.assertEqual(self._get(bad)[0], 400, bad)
        found = json.loads(self._get("/api/search?q=checksum")[1])
        self.assertEqual({r["goal"] for r in found["rows"]}, {"Check the tile checksum."})
        key = page["rows"][0]["key"]
        detail = json.loads(self._get(f"/api/run?key={urllib.request.quote(key)}")[1])
        self.assertEqual((detail["contract"], detail["key"], detail["row"]["key"]), ("empirica-ui-view/v1", key, key))
        self.assertIn(key, [a["key"] for a in detail["attempts"]])
        self.assertEqual(json.loads(self._get(f"/api/run?key={page['rows'][0]['id']}")[1]), detail)
        self.assertEqual(self._get("/api/run?key=nope")[0], 404)

    def test_the_safety_is_unchanged(self):
        request = urllib.request.Request(f"http://{self.host}/api/summary", headers={"Host": "evil.example:80"})
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request)
        self.assertEqual(refused.exception.code, 403)
        _, _, headers = self._get("/api/runs")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

    def test_a_stale_cursor_is_a_409_with_the_current_token(self):
        page = json.loads(self._get("/api/runs?limit=3")[1])
        stale = urllib.request.quote("0" * 16 + page["next"][16:])
        status, body, _ = self._get(f"/api/runs?limit=3&cursor={stale}")
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body), {"error": "stale_cursor", "token": page["token"]})
        self.assertEqual(self._get(f"/api/runs?limit=3&cursor={urllib.request.quote(page['next'])}")[0], 200)

    def test_about_says_what_each_source_serves(self):
        """The data menu names what it serves (P3: it said "Your store" over a generated demo)."""
        about = json.loads(self._get("/api/about")[1])
        self.assertEqual(about["sources"], {"store": "store", "demo": "fixtures"})
        self.assertEqual(Sources(None, identity="x", store_kind="generated").about()["sources"],
                         {"demo": "fixtures"})
        self.assertEqual(Sources(Observer(DriverSource(self.store)), identity="x", store_kind="generated").about(),
                         {"observer": "empirica-observer", "identity": "x", "sources": {"store": "generated", "demo": "fixtures"}})

    def test_the_demo_serves_the_contract_from_its_fixtures(self):
        page = json.loads(self._get("/api/runs?source=demo&limit=5")[1])
        self.assertEqual(len(page["rows"]), 5)
        self.assertIsNotNone(page["next"])
        summary = json.loads(self._get("/api/summary?source=demo")[1])
        self.assertEqual(summary["contract"], "empirica-ui-view/v1")


if __name__ == "__main__":
    unittest.main()
