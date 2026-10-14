#!/usr/bin/env python3
"""Observer read model (``contracts/empirica-ui-view/v1``): agreement, scope, read-only, caching,
and the on-disk store and git adapters.

The store under test is ``tests/v2/observer_store.build_store``: real runs driven through the v2
service. The observer must agree with the plugin's own public projections of the same runs (the
observer reads the plugin and does not judge). It must be independent of the host profile, write
nothing, and re-project only what changed.

Run: python3 plugins/empirica/tests/test_observer.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLUGIN = _HERE.parent
_ROOT = _PLUGIN.parents[1]
for _path in (_HERE / "v2", _HERE, _PLUGIN, _ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import validate_ui_view  # noqa: E402
from application import observer as observer_module  # noqa: E402
from application import protocol as _proto  # noqa: E402
from application.observer import Observer  # noqa: E402
from core.records import RunKey  # noqa: E402
from assertions import (action_graph, action_investigate, action_research, action_route,  # noqa: E402
                        get_argument, get_run, observe_action, start_run)
from governance_setup import sized_configure_run  # noqa: E402
from observer_store import (DriverSource, _Builder, _graph, build_store, observed, contract_problems,  # noqa: E402
                            view_of)
from application.protocol import host_profile_id  # noqa: E402
CLAUDE_PROFILE = host_profile_id("claude-code")

_STORE = build_store()


def _observe(store=_STORE, **kw):
    return observed(Observer(DriverSource(store), **kw))


def _rows(view):
    return {run["key"]: run for project in view["projects"] for run in project["runs"]}


class ContractTests(unittest.TestCase):
    def test_the_store_satisfies_the_contract_and_its_mirrors(self):
        view, details = _observe()
        self.assertEqual(contract_problems(view, details), [])

    def test_every_scenario_is_present_and_every_diagnostic_is_exercised(self):
        view, _ = _observe()
        rows = _rows(view)
        self.assertLessEqual(set(_STORE.expect.values()), set(rows))
        schema = validate_ui_view.schemas()[0]
        diagnostics = validate_ui_view._enum(schema["$defs"]["row"]["properties"], "diagnostic")
        seen = {row["diagnostic"] for row in rows.values()} - {None}
        # These need a refusing workspace, damaged history or a repository without the refs:
        # RecoveryTests, ClassificationTests and FilesystemStoreTests cover them.
        self.assertEqual(seen | {"workspace-unavailable", "history-corrupt", "history-missing"},
                         diagnostics)


    def test_history_kinds_are_exactly_the_kinds_the_plugin_records(self):
        """``HISTORY_KINDS`` is what the contract's step kinds are held to; here it is held to what
        the plugin's own service actually writes, across every scenario, in both directions."""
        from application.history_records import HISTORY_KINDS
        _, details = _observe()
        recorded = {step["kind"] for detail in details.values() for step in detail["steps"] or []}
        self.assertEqual(recorded, set(HISTORY_KINDS))


class ReasonMessageTests(unittest.TestCase):
    """A reason's wording is chosen and filled once, by ``confirmation.reason_text``; the observer adds only the refusal of
    a half-filled message and the plainness rules for what reaches a human."""

    def test_the_placeholders_are_the_reason_s_parameters_and_a_stopped_run_reads_in_the_past(self):
        from core.observer import reason_message
        spec = {"message": "{resource} budget exhausted.", "ended_message": "{resource} budget was spent when the run stopped."}
        self.assertEqual(reason_message(spec, {"resource": "pass"}), "pass budget exhausted.")
        self.assertEqual(reason_message(spec, {"resource": "pass"}, ended=True), "pass budget was spent when the run stopped.")
        # a reason with no ended wording reads the same on a stopped run
        self.assertEqual(reason_message({"message": "Nothing to fill."}, {}, ended=True), "Nothing to fill.")

    def test_the_observer_words_a_reason_exactly_as_the_author_view_does(self):
        # one renderer (core.confirmation.reason_text): the observer's text is the core's, for every reason and both tenses
        from core import confirmation
        from core.observer import reason_message
        contract = json.loads((_ROOT / "contracts" / "empirica" / "v2" / "public-contract.json").read_text())
        for code, spec in contract["reasons"].items():
            params = {name: "X" for name in spec["params"].get("properties", {})}
            for ended in (False, True):
                with self.subTest(code=code, ended=ended):
                    if "{" in confirmation.reason_text(spec, params, ended=ended):
                        continue
                    self.assertEqual(reason_message(spec, params, ended=ended),
                                     confirmation.reason_text(spec, params, ended=ended))

    def test_an_unfillable_or_unsuitable_parameter_is_a_contract_bug_not_half_a_message(self):
        from core.observer import reason_message
        for parameters in ({}, {"resource": None}, {"resource": {"a": 1}}, {"resource": True}, {"resource": ["a", "b"]}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                reason_message({"message": "{resource} spent."}, parameters)

    def test_no_message_the_observer_emits_keeps_a_placeholder(self):
        _, details = _observe()
        texts = [r["message"] for d in details.values() for r in d["residuals"] or []]
        texts += [i["message"] for d in details.values() for i in d["outstanding"] or []]
        texts += [c["blocker_message"] for d in details.values() for c in d["claims"] or [] if c["blocker_message"]]
        self.assertIn("pass budget was exhausted when the run stopped.", texts)
        self.assertEqual([t for t in texts if "{" in t or "}" in t], [])

    def test_every_reason_the_core_can_emit_has_its_placeholders_among_its_required_params(self):
        import re
        contract = json.loads((_ROOT / "contracts" / "empirica" / "v2" / "public-contract.json").read_text())
        for code, reason in contract["reasons"].items():
            for text in (reason["message"], reason.get("ended_message", "")):
                placeholders = set(re.findall(r"\{(\w+)\}", text))
                self.assertLessEqual(placeholders, set(reason["params"].get("required", ())), code)

    def test_every_registry_message_that_reaches_the_ui_is_one_plain_sentence(self):
        from core.observer import message_problems
        contract = json.loads((_ROOT / "contracts" / "empirica" / "v2" / "public-contract.json").read_text())
        problems = [p for code, reason in contract["reasons"].items() for p in message_problems(code, reason)]
        self.assertEqual(problems, [])
        # every reason a run can show as unresolved on a stopped run has its own ended wording
        from core.observer import UI_REASON_FAMILIES
        self.assertEqual([c for c in contract["reasons"] if c.startswith(UI_REASON_FAMILIES)
                          and "ended_message" not in contract["reasons"][c]], [])

    def test_negative_control_the_old_paragraph_and_a_jargon_word_are_caught(self):
        from core.observer import message_problems
        old = ("Research or a decision waits on a human, and nothing else blocks the run. The host shows the pending set "
               "once per derivation, at most one batch of the items below, and the human answers in one receipt.")
        self.assertEqual(len(message_problems("evidence.confirmation_required", {"message": old})), 3)  # too long, derivation, receipt
        self.assertEqual(len(message_problems("claim.refuted", {"message": "Fold-1 research refutes it."})), 1)
        self.assertEqual(len(message_problems("claim.refuted", {"message": "Fine.", "ended_message": "A receipt."})), 1)
        self.assertEqual(message_problems("governance.auto_ceiling", {"message": "x" * 300}), [])  # not shown to a human

    def test_one_reason_that_cannot_be_worded_makes_one_run_unreadable_not_the_store(self):
        from unittest.mock import patch
        import core.observer as core_observer
        real = core_observer.project
        calls = []

        def project(snapshot, reasons, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("unfillable placeholder")
            return real(snapshot, reasons, **kwargs)
        def unreadable(view):
            return sum(1 for p in view["projects"] for run in p["runs"] if run["diagnostic"] == "unreadable")
        baseline = unreadable(view_of(Observer(DriverSource(build_store()))))
        with patch.object(core_observer, "project", project):
            view = view_of(Observer(DriverSource(build_store())))
        self.assertEqual(unreadable(view), baseline + 1)
        self.assertGreater(sum(1 for p in view["projects"] for run in p["runs"] if run["diagnostic"] is None), 0)


class ProjectLabelTests(unittest.TestCase):
    """U1-9: the observer names each project, unique across the store, never by its place in the view."""

    def test_a_project_is_its_folder_name_or_the_word_for_an_unnamed_one(self):
        view, _ = _observe()
        labels = {p["id"]: p["label"] for p in view["projects"]}
        names = {p["name"]: p["label"] for p in view["projects"] if p["name"] not in {"tiles"}}
        self.assertEqual(names, {"atlas": "atlas", "harbor": "harbor", "lumen": "lumen", "prism": "prism", "quarry": "quarry",
                                 None: "Unnamed project"})
        self.assertEqual(len(set(labels.values())), len(labels))

    def test_two_folders_of_one_name_are_told_apart_by_the_shortest_differing_path_suffix(self):
        view, _ = _observe()
        tiles = sorted(p["label"] for p in view["projects"] if p["name"] == "tiles")
        self.assertEqual(tiles, ["north/tiles", "south/tiles"])

    def test_the_label_is_the_shortest_that_is_unique_and_a_tie_gets_a_short_stable_key(self):
        from application.observer import _tell_apart
        def rows(*repos):
            return [{"id": f"s256-{i:02x}" + "ab" * 31, "name": r.rsplit("/", 1)[-1] if r else None, "repo": r, "label": ""}
                    for i, r in enumerate(repos)]
        deep = rows("/a/x/tiles", "/b/x/tiles", "/c/y/tiles", "/work/atlas")
        _tell_apart(deep)
        self.assertEqual([r["label"] for r in deep], ["a/x/tiles", "b/x/tiles", "y/tiles", "atlas"])
        # the same path under two storage ids, and projects no repository matched: the key of the id is the last resort
        tie = rows("/w/atlas", "/w/atlas", None, None)
        _tell_apart(tie)
        labels = [r["label"] for r in tie]
        self.assertEqual(len(set(labels)), 4, labels)
        self.assertTrue(all(name.startswith(("atlas · ", "Unnamed project · ")) for name in labels), labels)
        self.assertTrue(all("sha256" not in name and len(name.split(" · ")[1]) == 6 for name in labels), labels)
        # nothing to tell apart: the short forms stay
        alone = rows("/w/atlas", None)
        _tell_apart(alone)
        self.assertEqual([r["label"] for r in alone], ["atlas", "Unnamed project"])

    def test_a_project_is_dated_by_when_its_earliest_run_started(self):
        view, _ = _observe()
        for project in view["projects"]:
            started = [r["started"] for r in project["runs"] if r["started"] is not None]
            self.assertEqual(project["since"], min(started, default=None))
        self.assertTrue(all(p["since"] is not None for p in view["projects"]))

    def test_the_label_does_not_change_when_projects_are_reordered(self):
        store = build_store()
        before = {p["id"]: p["label"] for p in view_of(Observer(DriverSource(store)))["projects"]}
        store.timeline.retime(lambda key, at: -at)  # the opposite recency
        after = view_of(Observer(DriverSource(store)))["projects"]
        self.assertEqual({p["id"]: p["label"] for p in after}, before)


class HistoryStepTests(unittest.TestCase):
    """U1-7: every history step says what it is about, the human's outcome, and whether the claim it names still stands."""

    @classmethod
    def setUpClass(cls):
        cls.view, cls.details = _observe()
        cls.rows = _rows(cls.view)

    def test_a_human_answer_and_the_end_of_a_run_are_steps_with_their_subject_and_outcome(self):
        steps = [s for d in self.details.values() for s in d["steps"] or []]
        answers = [s for s in steps if s["kind"] == "evidence_confirmation" and not s["terminal"]]
        self.assertEqual({s["outcome"] for s in answers} & {"confirm", "reject"}, {"confirm", "reject"})
        self.assertEqual({s["subject"] for s in answers if s["outcome"] in {"confirm", "reject"}}, {"record"})
        self.assertEqual({s["subject"] for s in steps if s["kind"] in {"graph", "audit_verdict", "attribution"}}, {"run"})
        ends = [s for s in steps if s["terminal"]]
        self.assertTrue(ends)
        self.assertEqual({(s["subject"], s["claim"], s["standing"]) for s in ends}, {("run", None, None)})


    def test_the_core_says_which_steps_are_bookkeeping_and_the_human_s_consent_never_is(self):
        steps = [s for d in self.details.values() for s in d["steps"] or []]
        self.assertEqual({s["kind"] for s in steps if s["bookkeeping"]}, {"observation_basis", "attribution"})
        self.assertFalse(any(s["bookkeeping"] for s in steps if s["terminal"] or s["kind"] in {"evidence_confirmation", "audit_verdict"}))


class AttemptFamilyTests(unittest.TestCase):
    """U1-10: attempts at one session are listed chronologically, by generation, whatever a recorded time says."""

    def test_INVARIANT_a_family_lists_its_attempts_by_generation_whatever_the_times_say(self):
        import itertools
        key = _STORE.expect["attempts"]
        project_id, run_id, _ = key.split("/")
        n = sum(1 for p in _observe()[0]["projects"] for r in p["runs"] if r["project"] == project_id and r["run"] == run_id)
        self.assertGreaterEqual(n, 3)
        for order in itertools.permutations(range(n)):
            store = build_store()
            rank = {RunKey(project_id, run_id, g): r for g, r in zip(range(1, n + 1), order)}
            store.timeline.retime(lambda k, at: 1000.0 + 100 * rank[k] if k in rank else at)
            view = view_of(Observer(DriverSource(store)))
            project = next(p for p in view["projects"] if p["id"] == project_id)
            family = next(f for f in project["families"] if f["run"] == run_id)
            self.assertEqual(family["attempts"], [f"{project_id}/{run_id}/{g}" for g in range(1, n + 1)], order)
        # control: with the first attempt written last, the runs (newest first by time) are not in generation order, so
        # ordering attempts by time would have been caught
        store = build_store()
        store.timeline.retime(lambda k, at: at + 10**6 if k == RunKey(project_id, run_id, 1) else at)
        built = next(p for p in view_of(Observer(DriverSource(store)))["projects"] if p["id"] == project_id)
        self.assertNotEqual([r["generation"] for r in built["runs"] if r["run"] == run_id], [3, 2, 1])
        self.assertNotEqual([r["generation"] for r in built["runs"] if r["run"] == run_id], list(range(1, n + 1)))

    def test_every_run_is_in_exactly_one_family_and_a_run_row_carries_its_goal_and_session(self):
        view, _ = _observe()
        for project in view["projects"]:
            listed = sorted(k for f in project["families"] for k in f["attempts"])
            self.assertEqual(listed, sorted(r["key"] for r in project["runs"]))
        goals = {r["goal"] for p in view["projects"] for r in p["runs"] if r["key"].split("/")[1] == _STORE.expect["attempts"].split("/")[1]}
        self.assertGreaterEqual(len(goals), 3)


class CoverageTests(unittest.TestCase):
    """U1-4: what was not read is unknown, never zero, and the view says how much was read."""

    def test_the_view_counts_the_runs_it_read_and_those_it_could_not(self):
        view, _ = _observe()
        rows = list(_rows(view).values())
        self.assertEqual(view["coverage"]["runs"], len(rows))
        self.assertEqual(view["coverage"]["unreadable"], sum(r["diagnostic"] == "unreadable" for r in rows))
        self.assertGreater(view["coverage"]["unreadable"], 0)
        self.assertGreater(view["coverage"]["partial"], 0)
        self.assertEqual(view["coverage"]["read"] + view["coverage"]["unreadable"] + view["coverage"]["partial"], len(rows))

    def test_INVARIANT_a_run_that_was_not_read_has_null_facts_never_empty_or_zero_ones(self):
        view, details = _observe()
        for key, row in _rows(view).items():
            if row["diagnostic"] is None:
                continue
            for field in ("audit", "attention", "progress", "root", "claims", "edges"):
                self.assertIsNone(row[field], (key, field))
            for field in ("residuals", "outstanding", "stale", "steps", "claims"):
                self.assertIsNone(details[key][field], (key, field))


class VerificationFactsTests(unittest.TestCase):
    """ADR-0067: how each research record is verified, what waits on a human, and the human's answers."""

    @classmethod
    def setUpClass(cls):
        _, details = _observe()
        cls.detail = details[_STORE.expect["verification"]]
        cls.claims = {c["id"]: c for c in cls.detail["claims"]}

    def record(self, claim):
        # C4's rejected log is the one that matters here; the agent's replacement for it is a second record.
        record, *replacements = self.claims[claim]["records"]
        self.assertEqual(len(replacements), claim == "C4")
        return record

    def test_each_record_states_its_class_status_and_who_it_waits_on(self):
        rows = {claim: {k: self.record(claim)[k] for k in ("class", "status", "wait", "retrieve")}
                for claim in ("C1", "C2", "C3", "C4", "C5", "C6")}
        human = {"who": "human", "where": "next_report"}
        self.assertEqual(rows, {
            "C1": {"class": "verbatim", "status": "verified_by_service", "wait": None, "retrieve": True},
            "C2": {"class": "retrievable", "status": "pending", "wait": {"who": "auditor", "where": "next_turn"}, "retrieve": True},
            "C3": {"class": "retrievable", "status": "confirmed_by_human", "wait": None, "retrieve": False},
            "C4": {"class": "observed", "status": "rejected_by_human", "wait": None, "retrieve": False},
            "C5": {"class": "retrievable", "status": "pending", "wait": human, "retrieve": False},
            "C6": {"class": "observed", "status": "pending", "wait": human, "retrieve": False}})

    def test_an_observed_record_carries_why_no_spike_could_establish_it_and_the_human_s_note(self):
        self.assertEqual(self.record("C4")["spike_unavailable"], "The job only runs on the nightly cluster.")
        self.assertEqual(self.record("C4")["note"], "That log is from a different job.")
        self.assertEqual(self.record("C3")["note"], "Checked the format page myself.")
        self.assertEqual((self.record("C1")["spike_unavailable"], self.record("C1")["note"]), (None, None))

    def test_what_waits_on_a_human_is_outstanding_per_claim_grouped_and_audit_routed_research_is_not(self):
        human = [i for i in self.detail["outstanding"] if i["who"] == "human"]
        self.assertEqual([(i["need"], i["claims"], i["where"]) for i in human],
                         [("evidence.confirmation_required", ["C5", "C6", "C7"], "next_report")])

    def test_a_needs_decision_claim_says_whether_it_is_answered(self):
        self.assertEqual(self.claims["C7"]["decision"],
                         {"status": "waiting", "outcome": None, "answer": None, "where": "next_report"})
        self.assertIsNone(self.claims["C1"]["decision"])


class WaitingOwnerTests(unittest.TestCase):
    """R3 M2: what waits on a human is the run view's ``confirmation_batch``, nothing else (the owner of ``where``). It is empty
    when the run is not active, and it says when the batch was already shown or the run's interactions are spent."""

    @staticmethod
    def _run(answer: bool = False, present: bool = False, limit: bool = False):
        from core import governance
        b = _Builder()
        run = b.begin("atlas", "s-wait", "Read tiles through one path.", _graph(
            "C0", [("C0", "ordinary", "Every tile read goes through the index."),
                   ("C1", "ordinary", "The nightly log shows no other read."),
                   ("C2", "ordinary", "Last week's log shows no other read.")], [("C0", "C1"), ("C0", "C2")]))
        b.cite(run, "C1", "runtime", "nightly log", "0 reads outside the index", spike_unavailable="Only runs nightly.")
        b.cite(run, "C2", "runtime", "weekly log", "0 reads outside the index", spike_unavailable="Only runs weekly.")
        b.research(run, "C0", ref="README.md")
        b.evaluate(run, "report_convergence")
        ids = b.record_ids(run)
        if present:
            b.drv.trusted_evidence_presentation(run, {"run_id": run, "receipt_id": "r1", "key": b.batch_key(run)})
        if answer:
            patch = mock.patch.object(governance, "MAX_INTERACTIONS", len(
                (lambda src: src.read_state(next(iter(src.keys()))))(DriverSource(b.store)).value["governance"]["receipts"]) + 1) if limit else contextlib.nullcontext()
            with patch:
                b.present_and_answer(run, "r1", [{"subject": "research", "artifact_id": ids["C1"], "outcome": "confirm"}])
                view, details = observed(Observer(DriverSource(b.store)))
            return _rows(view), details
        b.stamp("wait", "atlas", "s-wait")
        view, details = observed(Observer(DriverSource(b.store)))
        return _rows(view), details

    @staticmethod
    def _detail(details):
        return next(iter(details.values()))

    def _human(self, details):
        return [i for i in self._detail(details)["outstanding"] if i["who"] == "human"]

    def test_a_run_a_host_will_ask_needs_you_and_lists_its_batch(self):
        rows, details = self._run()
        self.assertEqual(next(iter(rows.values()))["attention"], "needs_you")
        self.assertEqual([(i["claims"], i["where"]) for i in self._human(details)], [(["C1", "C2"], "next_report")])

    def test_a_batch_already_shown_is_only_asked_again_by_an_explicit_report(self):
        rows, details = self._run(present=True)
        self.assertEqual(next(iter(rows.values()))["attention"], "needs_you")  # still the human's to answer, and reachable
        self.assertEqual({i["where"] for i in self._human(details)}, {"explicit_report"})
        self.assertEqual({r["wait"]["where"] for c in self._detail(details)["claims"] for r in c["records"]
                          if r["status"] == "pending"}, {"explicit_report"})

    def test_when_the_runs_interactions_are_spent_it_says_so_and_nobody_is_promised_a_question(self):
        rows, details = self._run(answer=True, limit=True)
        self.assertEqual({i["where"] for i in self._human(details)}, {"limit"})
        self.assertEqual(next(iter(rows.values()))["attention"], "not_ended")

    def test_INVARIANT_a_row_names_the_obligation_its_detail_lists_first_the_human_s_when_they_are_asked(self):
        view, details = _observe()
        seen = set()
        for key, row in _rows(view).items():
            if row["diagnostic"] is not None:
                self.assertIsNone(row["next"], key)
                continue
            items = details[key]["outstanding"]
            reachable = [i for i in items if i["who"] == "human" and i["where"] in ("next_report", "explicit_report")]
            first = (reachable or items)[:1]
            self.assertEqual(row["next"], {k: first[0][k] for k in ("who", "where", "message")} if first else None, key)
            seen.add((row["attention"], row["next"] and row["next"]["who"]))
        # control: the store holds a human's turn, someone else's, and a run owing nothing, so none of this is vacuous
        self.assertGreaterEqual(seen, {("needs_you", "human"), ("ended", None)})
        self.assertTrue({who for _, who in seen} - {None, "human"})

    def test_a_stopped_run_waits_on_nobody_and_owes_nothing(self):
        rows, details = _observe()
        view = _rows(rows)
        stopped = details[_STORE.expect["decision"]]
        self.assertEqual(stopped["outstanding"], [])
        self.assertEqual(view[_STORE.expect["decision"]]["attention"], "ended")
        pending = [c["decision"] for c in stopped["claims"] if c["decision"] and c["decision"]["outcome"] is None]
        self.assertTrue(pending)
        self.assertEqual({d["status"] for d in pending}, {"unanswered"})
        self.assertEqual({d["where"] for d in pending}, {None})
        # control: the active run in the same store does list what waits, so "always empty" would be caught
        self.assertTrue(details[_STORE.expect["verification"]]["outstanding"])
        self.assertNotIn("unanswered", {d["decision"]["status"] for d in details[_STORE.expect["verification"]]["claims"] if d["decision"]})


class HumanPendingActionTests(unittest.TestCase):
    """P3c (the user's ruling): a run whose human has an action pending (approve its configuration, agree to end it with the
    scope frozen) is filed under Needs a human with that ask; one whose agent has yet to ask, or whose human already said
    no, is Not ended, the agent next. Each case is driven through the real service; the observer's fact is the core's
    (``governance.human_wait`` and the report's requirements) and the hosts' own turn-end wait (Claude Code's
    ``completion._human_approval_wait`` on the gate's answer)."""

    GOAL, GRAPH = "Grade the north pit.", _graph("C0", [("C0", "ordinary", "The grading meets the road spec.")], [])

    def _read(self, b, run):
        from core import governance
        b.stamp("case", "quarry", "s-case")
        view, details = observed(Observer(DriverSource(b.store)))
        row, detail = next(iter(_rows(view).values())), next(iter(details.values()))
        g = b.a.dispatch(b.drv, get_run(run_id=run))["result"]["run"]["governance"]  # the author view's governance
        core_wait = governance.human_wait(g, g["prompt_error"])
        return row, detail, core_wait, b.evaluate(run, "report_convergence")["result"]

    def _case(self, outcome=None, *, sized=True, revise=False):
        b = _Builder()
        run = b.begin("quarry", "s-case", self.GOAL, self.GRAPH, approve=revise)
        if revise:  # approved once, then the agent proposes other ceilings: a revision
            b.research(run, "C0", ref="pit/grading.md")
            b.a.dispatch(b.drv, observe_action(run_id=run, action=sized_configure_run(
                budgets={"max_passes": 3, "max_spawns": 0, "max_audit_spawns": 1}, rationale="One claim, no spikes.")))
        if sized and not revise:
            b.ask_configuration(run, "shown", outcome=outcome)
        return (b, run, *self._read(b, run))

    def assertHuman(self, row, detail, ask):
        self.assertEqual((row["standing"], row["ask"], row["attention"]), ("needs_human", ask, "needs_you"))
        self.assertEqual((row["next"]["who"], row["next"]["where"]), ("human", "next_report"))
        self.assertEqual([i["who"] for i in detail["outstanding"]][:1], ["human"])

    def assertAgent(self, row):
        self.assertEqual((row["standing"], row["ask"], row["attention"]), ("not_ended", None, "not_ended"))
        self.assertEqual(row["next"]["who"], "agent")

    def test_the_agent_has_yet_to_ask_for_approval_so_the_agent_acts_next(self):
        _, _, row, _, core_wait, _ = self._case(sized=False)
        self.assertAgent(row)
        self.assertFalse(core_wait)

    def test_a_configuration_shown_to_the_human_and_not_answered_needs_a_human(self):
        from adapters.claude.completion import _human_approval_wait
        from adapters.claude.transport import Result
        _, _, row, detail, core_wait, gate = self._case("present")
        self.assertHuman(row, detail, "approve_configuration")
        self.assertTrue(core_wait)
        self.assertTrue(_human_approval_wait(Result.from_mapping(gate)), "Claude Code's turn end waits on the human too")

    def test_a_dialog_closed_without_an_answer_still_waits_on_the_human(self):
        from adapters.claude.completion import _human_approval_wait
        from adapters.claude.transport import Result
        _, _, row, detail, core_wait, gate = self._case("dismiss")
        self.assertHuman(row, detail, "approve_configuration")
        self.assertTrue(core_wait)
        self.assertTrue(_human_approval_wait(Result.from_mapping(gate)))

    def test_a_rejected_configuration_waits_on_the_human(self):
        """The behavioural contract (the hosts' turn end, ``host-boundaries.json``): after a rejection the turn settles
        with the human-wait notice, so the observer files the run under Needs a human too, by the same predicate."""
        from adapters.claude.completion import _human_approval_wait
        from adapters.claude.transport import Result
        _, _, row, detail, core_wait, gate = self._case("reject")
        self.assertEqual(gate["run"]["governance"]["state"], "rejected")
        self.assertHuman(row, detail, "approve_configuration")
        self.assertTrue(core_wait)
        self.assertTrue(gate["run"]["governance"]["human_wait"], "the run view carries the core's answer")
        self.assertTrue(_human_approval_wait(Result.from_mapping(gate)))

    def test_a_proposal_no_host_mediated_has_nobody_to_ask_so_the_agent_acts_next(self):
        from adapters.claude.completion import _human_approval_wait
        from adapters.claude.transport import Result
        _, _, row, _, core_wait, gate = self._case(None)
        self.assertAgent(row)
        self.assertFalse(core_wait)
        self.assertFalse(_human_approval_wait(Result.from_mapping(gate)))

    def test_a_revision_after_the_first_approval_needs_a_human_to_approve_it(self):
        _, _, row, detail, core_wait, _ = self._case(revise=True)
        self.assertHuman(row, detail, "approve_revision")
        self.assertTrue(core_wait)

    def _frozen(self, *, shown: bool):
        b = _Builder()
        graph = _graph("C0", [("C0", "ordinary", "Blasts happen only on weekday mornings.")], [])
        run = b.begin("quarry", "s-case", "Write down the blasting schedule.", graph)
        b.research(run, "C0", ref="blast/schedule.md")
        b.freeze(run)
        b.a.dispatch(b.drv, observe_action(run_id=run, action=action_graph(_graph(
            "C0", [("C0", "ordinary", "Blasts happen only on weekday mornings."),
                   ("C1", "ordinary", "Neighbours are told a day ahead.")], [("C0", "C1")]))))
        b.audit(run, "pass", ["The frozen claim is cited."])
        if shown:
            b.ask_to_end(run, "freeze-shown")
        b.stamp("case", "quarry", "s-case")
        view, details = observed(Observer(DriverSource(b.store)))
        gate = b.evaluate(run, "report_convergence")["result"]
        return next(iter(_rows(view).values())), next(iter(details.values())), gate

    def test_ending_with_the_scope_frozen_waits_on_the_human_when_the_gate_asks_them(self):
        row, detail, gate = self._frozen(shown=False)
        self.assertHuman(row, detail, "freeze_scope")
        self.assertEqual([r["parameters"]["case"] for r in gate["reasons"]], ["ask"])

    def test_a_declined_end_is_the_agent_s_to_keep_working_not_the_human_s(self):
        """The parked P1 finding: the core's case is ``declined`` (shown, no consent followed), so nothing waits on a human
        until they tell the agent to stop again."""
        row, detail, gate = self._frozen(shown=True)
        self.assertAgent(row)
        self.assertEqual([r["parameters"]["case"] for r in gate["reasons"]], ["declined"])
        self.assertNotIn("human", {i["who"] for i in detail["outstanding"]})

    def test_a_declined_stop_is_not_ended_and_asks_nobody(self):
        b = _Builder()
        run = b.begin("quarry", "s-case", "Retire the old weighbridge.", _graph(
            "C0", [("C0", "ordinary", "No ticket is printed by the old weighbridge any more.")], []))
        b.research(run, "C0", ref="weigh/tickets.py")
        b.ask_to_end(run, "consent-shown")
        b.stamp("case", "quarry", "s-case")
        view, _ = observed(Observer(DriverSource(b.store)))
        self.assertAgent(next(iter(_rows(view).values())))
        gate = b.evaluate_raw(run, "stop")["result"]
        self.assertEqual([r["parameters"]["case"] for r in gate["reasons"]], ["declined"])


class ProposedCeilingsTests(unittest.TestCase):
    def test_INVARIANT_ceilings_are_proposed_exactly_while_a_proposal_waits_on_the_human(self):
        _, details = _observe()
        pending = [d["governance"] for d in details.values() if d["governance"] and d["governance"]["proposal_pending"]]
        settled = [d["governance"] for d in details.values() if d["governance"] and not d["governance"]["proposal_pending"]]
        self.assertTrue(pending and settled)  # control: both kinds are in the store
        self.assertTrue(all(g["proposed"] is not None for g in pending))
        self.assertTrue(all(g["proposed"] is None for g in settled))


class DecisionApplicabilityTests(unittest.TestCase):
    """A human decision says whether it applies (U1-6): answered, waiting, deferred out of scope, or not asked."""

    @classmethod
    def setUpClass(cls):
        cls.view, cls.details = _observe()
        cls.rows = _rows(cls.view)


class AuditSelectorTests(unittest.TestCase):
    """U5 V2: the current pending audit child and the latest audit verdict are each selected once, at the evaluation owner;
    acceptance, the audit summary and the observer consume the same two functions."""

    def test_acceptance_the_summary_and_the_observer_select_through_the_same_two_functions(self):
        from core import evaluation, observer as core_observer, projection
        self.assertTrue(callable(evaluation.audit_pending) and callable(evaluation.latest_audit_verdict))
        self.assertIs(projection.audit_pending, evaluation.audit_pending)
        self.assertIs(projection.latest_audit_verdict, evaluation.latest_audit_verdict)
        self.assertIs(core_observer.latest_audit_verdict, evaluation.latest_audit_verdict)


class PresentabilityTests(unittest.TestCase):
    """U4 H2: whether a human is shown the batch has one owner (``core.confirmation``): the observer's reachable places, the
    host mediator's predicate and the places of the prompt errors cannot drift apart."""

    def test_the_places_a_human_is_reached_are_exactly_those_of_the_errors_a_report_presents_or_looks_under(self):
        from adapters.confirmation import presentable
        from core import confirmation
        from core.observer import HUMAN_REACHABLE
        errors = (None, *confirmation.PROMPT_ERRORS)
        self.assertEqual({e for e in errors if confirmation.ask_where(e) in HUMAN_REACHABLE}, set(confirmation.REPRESENTABLE))
        # An unknown availability is its own place, not a promise: ASKABLE (the gate's "may look") stays distinct.
        self.assertEqual(confirmation.ask_where(confirmation.UNOBSERVED), "if_available")
        self.assertNotIn("if_available", HUMAN_REACHABLE)
        self.assertTrue(set(confirmation.ASKABLE) - set(confirmation.REPRESENTABLE) == {confirmation.UNOBSERVED})
        for error in errors:
            result = {"run": {"confirmation_batch": {"prompt_error": error}}}
            self.assertEqual(presentable(result) is not None, error in confirmation.REPRESENTABLE, error)
            self.assertEqual(confirmation.presentable({"prompt_error": error}), error in confirmation.REPRESENTABLE, error)


class HistoricalFactsTests(unittest.TestCase):
    """U3 F3: what a stopped run says it left unresolved is read from what was recorded when it stopped (its observation
    basis), never from today's files; today's evaluation is its own section, published only when it differs."""

    @staticmethod
    def _read(b):
        view, details = observed(Observer(DriverSource(b.store)))
        row = view["projects"][0]["runs"][0]
        return row, details[row["key"]]

    @staticmethod
    def _stopped_with_spike():
        b = _Builder()
        run = b.begin("probe", "drift", "Stop with a passing experiment", _graph(
            "C0", [("C0", "ordinary", "Goal"), ("C1", "needs-experiment", "Experiment")], [("C0", "C1")]))
        b.research(run, "C0")
        b.research(run, "C1")
        b.spike(run, "C1", "python check.py", {"check.py": b"old\n"})
        b.evaluate(run, "stop")
        return b

    def test_the_workspace_changing_after_a_stop_does_not_rewrite_what_the_stop_left(self):
        b = self._stopped_with_spike()
        before_row, before = self._read(b)
        self.assertIsNone(before["now"], "nothing changed since it stopped")
        b.write("check.py", b"new\n")
        row, after = self._read(b)
        self.assertEqual(after["residuals"], before["residuals"], "what the stop left is read from the stop")
        self.assertEqual([c["state"] for c in before_row["claims"]], ["approved", "approved"])
        self.assertEqual([(c["state"], c["blocker"]) for c in row["claims"]],
                         [("open", "claim.spike_stale")] * 2, "what is true now is read from now (the root rests on it)")
        self.assertEqual(row["progress"]["approved"], 0)
        self.assertNotEqual(row["version"], before_row["version"])
        self.assertEqual([r["code"] for r in after["now"]], ["claim.spike_stale", "audit.required"],
                         "today's requirements: the stale proof, then the audit the report would still ask")
        self.assertTrue(after["stale"], "the files that changed are still listed")

    def test_a_stop_publishes_its_basis_scope_and_status(self):
        b = self._stopped_with_spike()
        _, detail = self._read(b)
        self.assertEqual(detail["stop"], {"basis": "recorded", "status": "stopped_residual", "frozen": False,
                                          "required": ["C0", "C1"], "deferred": []})

    def test_a_basis_that_does_not_hold_the_stop_is_unavailable_not_inferred(self):
        b = self._stopped_with_spike()
        with mock.patch.object(observer_module, "recorded_observations", return_value=None):
            row, detail = self._read(b)
        self.assertEqual(detail["stop"]["basis"], "unavailable")
        self.assertIsNone(detail["residuals"])
        self.assertEqual(contract_problems(*observed(Observer(DriverSource(b.store)))), [])

    def test_a_record_that_lacks_a_path_the_spikes_bind_holds_nothing(self):
        from application import snapshot as snapshot_module
        b = self._stopped_with_spike()
        real = snapshot_module.required_paths
        seen = []
        original = snapshot_module.recorded_observations

        def recording(history, state, heads):
            seen.append(original(history, state, heads))
            with mock.patch.object(snapshot_module, "required_paths", lambda h: (*real(h), "never/recorded.py")):
                seen.append(original(history, state, heads))
            return seen[0]
        with mock.patch.object(observer_module, "recorded_observations", recording):
            self._read(b)
        self.assertEqual([x is None for x in seen], [False, True])

    def test_a_frozen_stop_lists_the_required_claims_it_left_unresolved_with_what_it_deferred(self):
        b = _Builder()
        run = b.begin("probe", "frozen", "Frozen stop", _graph(
            "C0", [("C0", "needs-experiment", "Spike-backed")], []))
        b.research(run, "C0")
        b.spike(run, "C0", "python check.py", {"check.py": b"old\n"})
        b.freeze(run)
        b.write("check.py", b"new\n")
        b.a.dispatch(b.drv, observe_action(run_id=run, action=action_graph(_graph(
            "C0", [("C0", "needs-experiment", "Spike-backed"), ("C1", "ordinary", "Added after")], [("C0", "C1")]))))
        b.evaluate(run, "stop")
        row, detail = self._read(b)
        self.assertEqual(row["state"], "stopped_frozen")
        self.assertEqual({r["code"] for r in detail["residuals"]}, {"freeze.deferred", "claim.spike_stale"})
        self.assertEqual(detail["stop"]["required"], ["C0"])
        self.assertEqual(detail["stop"]["deferred"], ["C1"])


class SpikeProofTests(unittest.TestCase):
    """U3 F4: a claim's proof is each retained spike (what ran, how it ended, which bytes it bound, whether it still holds),
    read from the same dossier the plugin publishes, never a count."""

    @staticmethod
    def _built():
        b = _Builder()
        run = b.begin("probe", "spiked", "Spiked claim", _graph("C0", [("C0", "needs-experiment", "Experiment")], []))
        b.research(run, "C0")
        b.spike(run, "C0", "python check.py", {"check.py": b"old\n"})
        b.spike(run, "C0", "python check.py --strict", {"check.py": b"old\n"}, exit_code=1)
        return b, run

    @staticmethod
    def _proof(b):
        _, details = observed(Observer(DriverSource(b.store)))
        (detail,) = details.values()
        return detail["claims"][0]["proof"]

    def test_each_retained_spike_carries_its_command_result_files_and_standing(self):
        b, run = self._built()
        dossier = [a for a in b.a.dispatch(b.drv, get_argument(run_id=run))["result"]["argument"]["artifacts"]
                   if a["kind"] == "spike"]
        proof = self._proof(b)
        self.assertEqual([(p["command"], p["outcome"], p["exit_code"], p["gate"]) for p in proof],
                         [(a["command"], a["outcome"], a["exit_code"], a["spike_gate"]) for a in dossier])
        self.assertEqual([p["files"] for p in proof], [a["file_bindings"] for a in dossier])
        self.assertEqual([p["state"] for p in proof], ["superseded", "current"])
        self.assertEqual({p["output"] for p in proof}, {"not_retained"})

    def test_a_spike_whose_files_changed_is_stale_and_keeps_its_command(self):
        b, _ = self._built()
        b.write("check.py", b"new\n")
        self.assertEqual([p["state"] for p in self._proof(b)], ["superseded", "stale"])

    def test_a_claim_without_a_spike_has_an_empty_proof(self):
        view, details = observed(Observer(DriverSource(_STORE)))
        proofs = [c["proof"] for d in details.values() for c in d["claims"] or []]
        self.assertTrue(all(isinstance(p, list) for p in proofs) and any(p == [] for p in proofs) and any(proofs))


class AgreementTests(unittest.TestCase):
    """Every derived value equals the plugin's own public projection of the same run."""

    @classmethod
    def setUpClass(cls):
        cls.view, cls.details = _observe()
        cls.rows = _rows(cls.view)
        cls.case = __import__("observer_store")._Author()

    def _public(self, key, envelope):
        return _STORE.driver.request(envelope(run_id=_handle(key)))["result"]

    def test_claims_audit_and_residuals_equal_the_public_projections(self):
        latest = {}
        for key, row in self.rows.items():
            project, run, generation = key.split("/")
            if latest.get((project, run), (0,))[0] < int(generation):
                latest[(project, run)] = (int(generation), key)
        checked = 0
        for _, key in latest.values():
            row = self.rows[key]
            if row["diagnostic"] is not None or not row["claims"]:
                continue
            argument = self._public(key, get_argument)
            runview = self._public(key, get_run)["run"]
            states = {c["claim_id"]: (c["state"], c["kind"], c["gating"]) for c in argument["argument"]["claims"]}
            detail = self.details[key]
            # Claims are the argument's as of now, always; an ended run whose files changed has today's requirements beside
            # its stop's residuals (every unmet obligation of the author view but the consent to end it).
            self.assertEqual({c["id"]: (c["state"], c["kind"], c["gating"]) for c in row["claims"]}, states, key)
            if detail["now"] is not None:
                unmet = {o["missing"]["code"] for o in runview["obligations"]["active"]
                         if o["status"] != "satisfied" and o["missing"] and o.get("hold") != "deferred"}
                self.assertEqual(sorted({r["code"] for r in detail["now"]}),
                                 sorted(unmet - {"governance.terminal_stop_pending"}), key)
            else:
                self.assertEqual(sorted({r["code"] for r in detail["residuals"]}),
                                 sorted({r["code"] for r in runview["residuals"]}), key)
            self.assertEqual(row["audit"], {k: runview["audit"][k] for k in ("verdict", "current", "pending", "independence")}, key)
            self.assertEqual(row["state"], runview["status"], key)
            self.assertEqual(detail["stale"], runview["freshness"]["changes"], key)
            self.assertEqual(detail["audit"]["findings"], runview["audit"]["findings"], key)
            checked += 1
        self.assertGreaterEqual(checked, 6)

    def test_governance_is_the_plugin_s_own_projection_shaped(self):
        """U3 F5: the ceilings, their use, the configuration and the interactions left are ``project_governance``'s."""
        checked = 0
        for key, row in self.rows.items():
            gov = self.details[key]["governance"]
            if row["diagnostic"] is not None or gov is None:
                continue
            public = self._public(key, get_run)["run"]["governance"]
            self.assertEqual((gov["mode"], gov["approval"]), (public["control_mode"], public["state"]), key)
            self.assertEqual({n: (c["used"], c["max"]) for n, c in gov["ceilings"].items()},
                             {"passes": (public["budgets"]["passes_used"], public["budgets"]["max_passes"]),
                              "spawns": (public["budgets"]["spawns_used"], public["budgets"]["max_spawns"]),
                              "audit_spawns": (public["budgets"]["audit_spawns_used"], public["budgets"]["max_audit_spawns"])}, key)
            self.assertEqual(gov["interactions"]["left"], public["interactions_remaining"]["total"], key)
            checked += 1
        self.assertGreaterEqual(checked, 10)

    def test_governance_of_a_run_whose_history_was_not_read_leaves_the_interactions_unknown(self):
        view, details = _observe()
        partial = [k for k, r in _rows(view).items() if r["diagnostic"] == "history-unavailable"]
        self.assertTrue(partial)
        for key in partial:
            gov = details[key]["governance"]
            self.assertIsNone(gov["interactions"])
            self.assertIsNotNone(gov["mode"])

    def test_scenarios_read_as_the_plugin_left_them(self):
        r = {name: self.rows[key] for name, key in _STORE.expect.items()}
        d = {name: self.details[key] for name, key in _STORE.expect.items()}
        claims = {name: {c["id"]: c for c in row["claims"] or []} for name, row in r.items()}
        self.assertEqual((r["converged"]["state"], r["converged"]["audit"]["verdict"], r["converged"]["audit"]["current"]),
                         ("converged", "passed", True))
        self.assertEqual({c["review"] for c in claims["converged"].values()}, {"passed"})
        self.assertEqual(d["converged"]["auditor"] is not None, True)
        self.assertEqual(claims["refuted"]["C1"]["state"], "discarded")
        self.assertEqual(claims["refuted"]["C1"]["blocker"], "claim.refuted")
        self.assertEqual(r["budget"]["state"], "stopped_budget")
        self.assertFalse(claims["frozen"]["C1"]["gating"])
        self.assertEqual(claims["decision"]["C1"]["state"], "blocked")
        self.assertEqual(r["audit-failed"]["previous"], _STORE.expect["decision"])
        self.assertEqual({c["review"] for c in claims["audit-failed"].values()}, {"failed"})
        self.assertTrue(d["audit-failed"]["audit"]["findings"])
        self.assertEqual(r["history-unavailable"]["diagnostic"], "history-unavailable")
        self.assertEqual(r["history-unavailable"]["state"], "active")
        self.assertIsNotNone(d["history-unavailable"]["governance"])
        self.assertIsNone(d["history-unavailable"]["steps"])
        self.assertEqual((r["unreadable"]["diagnostic"], r["unreadable"]["state"]), ("unreadable", None))
        self.assertEqual({cid: c["review"] for cid, c in claims["reaudit"].items()},
                         {"C0": "unreviewed", "C1": "stale", "C2": "unreviewed", "C3": "changed"})
        # a pass that no longer covers the argument is a pass that is not current, never a failure (U1-2)
        self.assertEqual((r["reaudit"]["audit"]["verdict"], r["reaudit"]["audit"]["current"], r["reaudit"]["audit"]["pending"]),
                         ("passed", False, False))
        self.assertEqual({c["assurance"] for c in claims["reaudit"].values() if c["state"] == "approved"}, {"awaiting_audit"})
        self.assertEqual(d["reaudit"]["audit"]["findings"], [])
        self.assertEqual((r["audit-failed"]["audit"]["verdict"], r["audit-failed"]["audit"]["current"]), ("failed", True))
        self.assertEqual({c["assurance"] for c in claims["converged"].values()}, {"assured"})
        # a second audit already running: the earlier pass stays the verdict, nothing is settled, nothing is yet assured
        self.assertEqual((r["pending-reaudit"]["audit"]["verdict"], r["pending-reaudit"]["audit"]["pending"],
                          r["pending-reaudit"]["audit"]["independence"]), ("passed", True, None))
        self.assertEqual({c["assurance"] for c in claims["pending-reaudit"].values()}, {"awaiting_audit"})
        pending_audit = [i for i in d["pending-reaudit"]["outstanding"] if i["need"] == "audit.pending"]
        self.assertEqual([(i["who"], i["where"]) for i in pending_audit], [("auditor", "in_progress")])
        # a proposal the human has not answered is not the ceilings in force
        amended = d["pending-amendment"]["governance"]
        self.assertTrue(amended["proposal_pending"])
        self.assertEqual(amended["proposed"]["passes"], 3)
        self.assertNotEqual(amended["proposed"], {k: v["max"] for k, v in amended["ceilings"].items()})
        self.assertEqual(r["verification"]["attention"], "needs_you")
        # the end of a run the human approved is a run event, not a claim named after a status
        ending = [s for s in d["refuted"]["steps"] if s["terminal"]]
        self.assertEqual([(s["subject"], s["claim"], s["outcome"]) for s in ending], [("run", None, "approve")])
        self.assertEqual((claims["decision"]["C0"]["blocker"], claims["decision"]["C0"]["blocker_claim"]),
                         ("evidence.confirmation_required", "C1"))

    def test_the_gate_s_other_stages_read_as_the_gate_leaves_them(self):
        r = {name: self.rows[key] for name, key in _STORE.expect.items()}
        d = {name: self.details[key] for name, key in _STORE.expect.items()}
        # a human batch beside work the agent owes: the host shows it at the next report, so a human is needed; the gate's first
        # reason is still the agent's work (the first outstanding item), which is a separate fact
        self.assertEqual(r["parallel-human"]["attention"], "needs_you")
        self.assertEqual([(i["who"], i["where"]) for i in d["parallel-human"]["outstanding"] if i["who"] == "human"],
                         [("human", "next_report")])
        self.assertEqual(d["parallel-human"]["outstanding"][0]["who"], "agent")
        self.assertEqual(r["parallel-human"]["next"]["who"], "human")
        # an attempt that cannot be read beside one that can: the family has both
        family = [x for x in self.rows.values() if x["run"] == r["attempt-unread-sibling"]["run"]]
        self.assertEqual(sorted((x["generation"], x["diagnostic"]) for x in family), [(1, None), (2, "unreadable")])

    def test_the_output_does_not_depend_on_the_host_profile(self):
        reference = _observe()
        for profile_id in _proto.profile_ids():
            with self.subTest(profile=profile_id):
                self.assertEqual(_observe(profile_id=profile_id), reference)


class _Refuse:
    """Wraps a port; any method outside ``allowed`` raises."""

    def __init__(self, inner, allowed):
        self._inner, self._allowed = inner, allowed

    def __getattr__(self, name):
        if name not in self._allowed and not name.startswith("_"):
            raise AssertionError(f"observer called {name}")
        return getattr(self._inner, name)


class ReadOnlyTests(unittest.TestCase):
    def test_the_observer_only_reads(self):
        drv = _STORE.driver
        source = DriverSource(_STORE)
        source._runs = _Refuse(drv._runs, {"read", "generations"})
        source._runs._store = drv._runs._store
        source._artifacts = _Refuse(drv._artifacts, {"read"})
        source._workspace = _Refuse(drv._workspace, {"observe", "drift_id", "signatures", "now_ns", "changed_at", "basis_of"})
        before = json.dumps(drv.operational_state(), sort_keys=True, default=str)
        observed(Observer(source))
        self.assertEqual(json.dumps(drv.operational_state(), sort_keys=True, default=str), before)


class CacheTests(unittest.TestCase):
    def test_an_unchanged_store_is_not_re_projected_and_a_change_touches_one_run(self):
        store = build_store()
        obs = Observer(DriverSource(store))
        first, _ = observed(obs)
        with _counting_assemble() as calls:
            again, _ = observed(obs)
            self.assertEqual((again["token"], calls), (first["token"], []))
            key = store.expect["reaudit"]
            store.driver.request(observe_action(run_id=_handle(key), action=action_research(
                claim_id="C0", source_kind="code", result="supports")))
            changed, _ = observed(obs)
        self.assertNotEqual(changed["token"], first["token"])
        self.assertEqual(len(calls), 1)
        before, after = _rows(first), _rows(changed)
        self.assertEqual([k for k in before if before[k]["version"] != after[k]["version"]], [key])

    def test_a_workspace_edit_changes_exactly_the_runs_whose_spikes_depend_on_it(self):
        store = build_store()
        obs = Observer(DriverSource(store))
        first, _ = observed(obs)
        store.driver.workspace_write("src/cache.py", b"cache v2\n")
        with _counting_assemble() as calls:
            changed, details = observed(obs)
        self.assertEqual(len(calls), 1)
        before, after = _rows(first), _rows(changed)
        key = store.expect["converged"]
        self.assertEqual([k for k in before if before[k]["version"] != after[k]["version"]], [key])
        # The run ended, and its outcome stays; but what it says of its claims and audit is true now, as the author view says it:
        # the file its proof is bound to changed, so the claim is open again and the audit no longer covers it.
        claim = {c["id"]: c for c in after[key]["claims"]}["C1"]
        self.assertEqual((claim["state"], claim["blocker"], claim["assurance"]), ("open", "claim.spike_stale", "not_approved"))
        self.assertEqual((after[key]["state"], before[key]["claims"] != after[key]["claims"]), ("converged", True))
        self.assertEqual((before[key]["audit"]["current"], after[key]["audit"]["current"]), (True, False))
        self.assertEqual([c["path"] for c in details[key]["stale"]], ["src/cache.py"])
        self.assertEqual(observed(Observer(DriverSource(store)))[0], changed)


class UnobservableWorkspaceTests(unittest.TestCase):
    def test_a_workspace_that_refuses_observation_is_a_diagnostic(self):
        from application.observation import ObservationUnavailable

        class Refusing:
            def observe(self, paths):
                raise ObservationUnavailable("unmounted")

        source = DriverSource(_STORE)
        source.workspace = lambda key: Refusing()
        view, details = observed(Observer(source))
        bound = _rows(view)[_STORE.expect["converged"]]
        self.assertEqual((bound["diagnostic"], bound["claims"]), ("workspace-unavailable", None))
        coverage = view["coverage"]
        self.assertEqual((coverage["read"] + coverage["unreadable"] + coverage["partial"] + coverage["pending"],
                          coverage["runs"]), (len(view["rows"]), len(view["rows"])), "every run is counted, none dropped")
        self.assertEqual(contract_problems(view, details), [])


class RecoveryTests(unittest.TestCase):
    def test_a_workspace_that_comes_back_is_read_again_by_the_same_observer(self):
        from application.observation import ObservationUnavailable

        class Refusing:
            def observe(self, paths):
                raise ObservationUnavailable("unmounted")

        store = build_store()
        source = DriverSource(store)
        obs = Observer(source)
        key = store.expect["converged"]
        healthy = _rows(view_of(obs))[key]
        source.workspace = lambda k: Refusing()
        self.assertEqual(_rows(view_of(obs))[key]["diagnostic"], "workspace-unavailable")
        del source.workspace
        self.assertEqual(_rows(view_of(obs))[key], healthy)


class ClassificationTests(unittest.TestCase):
    def _inject(self, value):
        from application.location import storage_id
        from core.records import RunKey
        store = build_store()
        key = RunKey(storage_id("atlas"), storage_id("s-injected"), 1)
        store.driver.inject_run_state(key, value)
        view, details = _observe(store)
        self.assertEqual(contract_problems(view, details), [])
        return _rows(view)[f"{key.project_id}/{key.run_id}/1"], store

    def _current_state(self):
        store = build_store()
        project, run, generation = store.expect["converged"].split("/")
        from core.records import RunKey
        return json.loads(json.dumps(store.driver._runs.read(RunKey(project, run, int(generation))).value))

    def test_INVARIANT_a_document_that_is_not_the_current_state_is_unreadable_and_nothing_is_decoded_from_it(self):
        """Whatever wrote it (another protocol, another schema, an old marker, the current identifiers on a body the
        schema rejects), such a document is one category: ``unreadable``, with no state, goal,
        budgets or control read from it. The core reads the same documents as ``run.corrupt`` (``test_d6_strict_v2``)."""
        canary = "CANARY goal that must not be read"
        base = self._current_state()
        over_budget = json.loads(json.dumps(base))
        over_budget["budgets"]["passes_used"] = over_budget["budgets"]["max_passes"] + 5
        documents = {
            "another protocol": {"protocol": "empirica/v1", "goal": canary, "status": "active"},
            "another schema": {**base, "state_schema": "empirica.run/1", "goal": canary},
            "an old marker": {"schema": "empirica.run/1", "goal": canary, "status": "active"},
            "an extra field": {**base, "modes": {}, "goal": canary},
            "an invariant broken": {**over_budget, "goal": canary},
        }
        for label, document in documents.items():
            with self.subTest(label):
                row, store = self._inject(document)
                key = f"{row['key']}"
                view, details = _observe(store)
                self.assertEqual((row["diagnostic"], row["state"]), ("unreadable", None))
                detail = details[key]
                self.assertEqual((detail["goal"], detail["governance"], row["goal"], row["session"]), (None, None, None, None))
                self.assertNotIn(canary, json.dumps([row, detail]))

    def test_a_run_whose_history_cannot_be_assembled_marks_only_that_run(self):
        """A recorded artifact keeps its content address, so a malformed one is a producer bug
        that ``traverse_history`` admits; assembling it raises a plain error. Simulated here."""
        store = build_store()
        real = observer_module.assemble

        def assemble(state, stored, workspace, **kw):
            if kw["run_id"] == _handle(store.expect["reaudit"]):
                raise KeyError("harness_request_id")
            return real(state, stored, workspace, **kw)

        with mock.patch.object(observer_module, "assemble", assemble):
            rows = _rows(_observe(store)[0])
        self.assertEqual(rows[store.expect["reaudit"]]["diagnostic"], "history-corrupt")
        self.assertEqual([k for k, r in rows.items() if r["diagnostic"] == "history-corrupt"],
                         [store.expect["reaudit"]])


def _handle(key):
    """The plugin's opaque run handle for a run key of the view."""
    from application.location import encode_handle
    from core.records import RunKey
    project, run, generation = key.split("/")
    return encode_handle(RunKey(project, run, int(generation)))


@contextlib.contextmanager
def _counting_assemble():
    """Count the observer's snapshot assemblies (each is one run re-projected)."""
    calls: list[int] = []
    real = observer_module.assemble
    with mock.patch.object(observer_module, "assemble", lambda *a, **k: calls.append(1) or real(*a, **k)):
        yield calls


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout


def _drive(profile, repo, clock=None):
    """One run through the bridge, in ``repo`` (the working directory and ``EMPIRICA_HOME`` are the caller's), its writes
    timed by ``clock`` (the bridge's wall clock when none). Receipt ids are drawn from a fixed sequence."""
    import uuid
    import governance_setup
    from adapters import bridge
    from adapters.state import identity
    from governance_setup import approve_current
    sequence = (uuid.UUID(int=n) for n in range(1, 1 << 16))
    with mock.patch.object(governance_setup, "uuid4", lambda: next(sequence)):
        service = bridge.build_service(profile) if clock is None else bridge.build_service(profile, clock=clock)
        started = service.dispatch(start_run(goal="Cache the tile index.", request_id="start",
                                             project=identity.project_id(repo), session="s1"))
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [
            {"id": "C0", "text": "A cold start reads one file.", "kind": "ordinary", "gating": True}],
            "edges": []}
        for action in (action_route(reason="observer"), action_graph(graph)):
            service.dispatch(observe_action(run_id=run_id, action=action))
        approve_current(service._coordinator, run_id)
        for action in (action_investigate(),
                       action_research(claim_id="C0", source_kind="code", result="supports",
                                       payload={"source_ref": "README.md:1", "citation": "atlas"})):
            admitted = service.dispatch(observe_action(run_id=run_id, action=action))
            assert admitted["result"]["type"] == "Allow", admitted
    return run_id


class FilesystemStoreTests(unittest.TestCase):
    """The real adapters: a run started through the bridge in a temporary repository."""

    PROFILE = CLAUDE_PROFILE

    @classmethod
    def setUpClass(cls):
        """One run for every case (git appends are slow); each case only reads."""
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name).resolve()
        cls.home, cls.repo = base / "home", base / "work" / "atlas"
        cls.repo.mkdir(parents=True)
        _git(cls.repo, "init", "-q")
        (cls.repo / "README.md").write_text("atlas\n")
        env, cwd = os.environ.get("EMPIRICA_HOME"), os.getcwd()
        os.environ["EMPIRICA_HOME"] = str(cls.home)
        os.chdir(cls.repo)
        try:
            cls._start()
        finally:
            os.chdir(cwd)
            if env is None:
                os.environ.pop("EMPIRICA_HOME", None)
            else:
                os.environ["EMPIRICA_HOME"] = env

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def _start(cls):
        _drive(cls.PROFILE, cls.repo)

    def _snapshot(self):
        files = {p: p.read_bytes() for p in sorted(self.home.rglob("*")) if p.is_file()}
        git = sorted((str(p.relative_to(self.repo)), p.stat().st_size, p.stat().st_mtime_ns)
                     for p in (self.repo / ".git").rglob("*"))
        return files, git, _git(self.repo, "for-each-ref"), _git(self.repo, "status", "--porcelain")

    def test_a_run_on_disk_reads_fully_and_nothing_is_written(self):
        from adapters.observer.source import StoreSource
        before = self._snapshot()
        view, details = observed(Observer(StoreSource(self.home, [self.repo])))
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(contract_problems(view, details), [])
        (project,) = view["projects"]
        self.assertEqual((project["name"], project["repo"]), ("atlas", str(self.repo)))
        (run,) = project["runs"]
        self.assertEqual((run["diagnostic"], run["state"]), (None, "active"))
        self.assertEqual([(c["id"], c["state"], c["evidence"]) for c in run["claims"]],
                         [("C0", "approved", "research")])
        self.assertIsNotNone(run["updated"])

    def _times(self, home):
        from adapters.observer.source import StoreSource
        view, details = observed(Observer(StoreSource(home, [self.repo])))
        (run,) = view["projects"][0]["runs"]
        return (run["started"], run["updated"], run["ended"], view["projects"][0]["since"],
                [s["at"] for s in details[run["key"]]["steps"]])

    def test_the_bridge_records_when_each_write_happened_beside_the_run(self):
        from adapters.state.timeline import TIMELINE, Timeline
        (path,) = self.home.glob(f"projects/*/runs/*/gen-1/{TIMELINE}")
        self.assertEqual(path.parent / "run.json", next(self.home.glob("projects/*/runs/*/gen-1/run.json")))
        started, updated, ended, since, steps = self._times(self.home)
        gen, run_dir, project_dir = path.parent, path.parents[1], path.parents[3]
        entries = Timeline(self.home).read(RunKey(project_dir.name, run_dir.name, int(gen.name.split("-")[1])))
        self.assertEqual((started, updated, ended, since), (entries[0]["at"], entries[-1]["at"], None, entries[0]["at"]))
        self.assertLessEqual(started, updated)
        self.assertTrue(steps and all(at is not None for at in steps), steps)
        self.assertEqual(_git(self.repo, "for-each-ref", "--format=%(committerdate:raw)").split(),
                         ["0", "+0000"] * len(_git(self.repo, "for-each-ref").splitlines()),
                         "the artifact commits stay dated @0: no time enters refs/empirica")

    def test_a_copied_store_keeps_its_times_whatever_its_files_modification_times(self):
        home = Path(self.enterContext(tempfile.TemporaryDirectory())) / "home"
        shutil.copytree(self.home, home, symlinks=True, copy_function=shutil.copy)  # copy() does not keep mtimes
        for path in home.rglob("*"):
            os.utime(path, (0, 0), follow_symlinks=False)
        self.assertEqual(self._times(home), self._times(self.home))

    def test_an_unchanged_poll_reads_no_history(self):
        from adapters.git.artifact_repo import GitArtifactRepository
        from adapters.observer.source import StoreSource
        obs = Observer(StoreSource(self.home, [self.repo]))
        first = view_of(obs)
        reads = []
        real = GitArtifactRepository.read
        GitArtifactRepository.read = lambda repo, key: reads.append(key) or real(repo, key)
        try:
            again = view_of(obs)
        finally:
            GitArtifactRepository.read = real
        self.assertEqual((again, reads), (first, []))

    def _home_where(self, repo):
        """A copy of the store whose project records ``repo`` (``None``: no record)."""
        from adapters.state.locations import PROJECT
        home = Path(self.enterContext(tempfile.TemporaryDirectory())) / "home"
        shutil.copytree(self.home, home, symlinks=True)
        (record,) = home.glob(f"projects/*/{PROJECT}")
        if repo is None:
            record.unlink()
        else:
            record.write_text(json.dumps({"repo": str(repo)}))
        return home

    def test_the_bridge_records_where_the_run_works_and_the_observer_finds_it_unasked(self):
        from adapters.observer.source import StoreSource
        from adapters.state.locations import PROJECT, WORKSPACE
        (record,) = self.home.glob(f"projects/*/{PROJECT}")
        (workspace,) = self.home.glob(f"projects/*/runs/*/gen-1/{WORKSPACE}")
        self.assertEqual((json.loads(record.read_text()), json.loads(workspace.read_text())),
                         ({"repo": str(self.repo)}, {"workspace": str(self.repo)}))
        view, details = observed(Observer(StoreSource(self.home)))
        (project,) = view["projects"]
        self.assertEqual((project["name"], project["runs"][0]["diagnostic"]), ("atlas", None))
        self.assertEqual(contract_problems(view, details), [])

    def test_without_its_repository_a_run_reads_its_state_only(self):
        from adapters.observer.source import StoreSource
        view, details = observed(Observer(StoreSource(self._home_where(None), [])))
        (run,) = view["projects"][0]["runs"]
        self.assertEqual((run["diagnostic"], run["state"], run["claims"]),
                         ("history-unavailable", "active", None))
        self.assertIsNone(view["projects"][0]["name"])
        self.assertEqual(contract_problems(view, details), [])

    def test_a_repository_without_the_run_s_history_says_so(self):
        from adapters.observer.source import StoreSource
        with tempfile.TemporaryDirectory() as other:
            clone = Path(other).resolve() / "atlas"  # a clone does not copy the artifact refs
            subprocess.run(["git", "clone", "-q", str(self.repo), str(clone)], check=True,
                           capture_output=True)
            (run,) = view_of(Observer(StoreSource(self._home_where(clone))))["projects"][0]["runs"]
        self.assertEqual((run["diagnostic"], run["state"]), ("history-missing", "active"))

    def test_a_recorded_repository_that_has_gone_is_no_repository(self):
        from adapters.observer.source import StoreSource
        home = self._home_where(Path(self.tmp.name) / "gone")
        (run,) = view_of(Observer(StoreSource(home)))["projects"][0]["runs"]
        self.assertEqual(run["diagnostic"], "history-unavailable")

    def test_the_command_line_prints_the_view_and_one_run(self):
        from adapters.observer import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--home", str(self.home), "--repo", str(self.repo)]), 0)
        summary = json.loads(out.getvalue())
        self.assertEqual(summary["contract"], "empirica-ui-view/v1")
        key = summary["marks"]["id"][0]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--home", str(self.home), "--run", key]), 0)
        sheet = json.loads(out.getvalue())
        self.assertEqual((sheet["row"]["id"], sheet["key"] == sheet["row"]["key"]), (key, True))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--home", str(self.home), "--repo", str(self.home)]), 2)


class TwoClockFilesystemTests(unittest.TestCase):
    """ADR-0070 on the real adapters: the same run driven through the bridge with two clocks, in the same place, leaves
    byte-identical run state and artifact refs. Only the timelines differ."""

    def _run(self, base: Path, clock):
        home, repo = base / "home", base / "work" / "atlas"
        if base.exists():
            shutil.rmtree(base)
        repo.mkdir(parents=True)
        _git(repo, "init", "-q")
        (repo / "README.md").write_text("atlas\n")
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            with mock.patch.dict(os.environ, {"EMPIRICA_HOME": str(home)}):
                _drive(FilesystemStoreTests.PROFILE, repo, clock)
        finally:
            os.chdir(cwd)
        from adapters.state.timeline import TIMELINE
        files = {str(p.relative_to(home)): p.read_bytes() for p in sorted(home.rglob("*"))
                 if p.is_file() and p.name != TIMELINE and not p.name.endswith(".lock")}
        timelines = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob(TIMELINE)}
        return files, _git(repo, "for-each-ref"), timelines

    def test_INVARIANT_the_clock_changes_only_the_timeline(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve() / "same"
        ticks = iter(range(10**6))
        files_a, refs_a, times_a = self._run(base, lambda: 1790586000.0 + 60 * next(ticks))
        files_b, refs_b, times_b = self._run(base, lambda: 4.1e9 - 7 * next(ticks))
        self.assertEqual(files_a, files_b)
        self.assertTrue(any(name.endswith("run.json") for name in files_a))
        self.assertEqual(refs_a, refs_b)
        self.assertTrue(refs_a.strip())
        self.assertEqual(set(times_a), set(times_b))
        self.assertNotEqual(times_a, times_b)


class LocationsTests(unittest.TestCase):
    """``adapters.state.locations`` and its writer, the bridge: a run on any host is found by its
    recorded location, whatever selector its project id came from."""

    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.home, self.repo = base / "home", base / "harbor"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        self.enterContext(mock.patch.dict(os.environ, {"EMPIRICA_HOME": str(self.home)}))
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)

    def _start(self, project):
        from adapters import bridge
        return bridge.build_service(FilesystemStoreTests.PROFILE).dispatch(
            start_run(goal="Index the harbour.", project=project, session="s1"))

    def test_a_project_id_no_identity_maps_is_found_by_its_record(self):
        """Pi derives its project from a hash of the working directory, which no repository maps
        to by the plugin's identity; the recorded location still finds its history."""
        from adapters.observer.source import StoreSource
        self.assertEqual(self._start("0123456789abcdef")["result"]["type"], "Allow")
        (project,) = view_of(Observer(StoreSource(self.home)))["projects"]
        self.assertEqual((project["name"], project["runs"][0]["diagnostic"]), ("harbor", None))

    def test_a_record_that_cannot_be_written_does_not_fail_the_run_or_cost_the_other_record(self):
        from adapters.state.locations import PROJECT, WORKSPACE
        from application.location import storage_id
        project = self.home / "projects" / storage_id("p")
        (project / "runs").mkdir(parents=True)
        project.chmod(0o555)  # project.json cannot be written; the run's own directory can
        self.addCleanup(project.chmod, 0o755)
        self.assertEqual(self._start("p")["result"]["type"], "Allow")
        self.assertFalse((project / PROJECT).exists())
        self.assertEqual(len(list(project.glob(f"runs/*/gen-1/{WORKSPACE}"))), 1)

    def test_git_resolves_each_recorded_path_once_not_every_poll(self):
        from adapters.observer import source as source_module
        self._start("p")
        calls = []
        real = source_module.toplevel
        with mock.patch.object(source_module, "toplevel", side_effect=lambda p: calls.append(p) or real(p)):
            source = source_module.StoreSource(self.home)
            for _ in range(3):
                source.refresh()
        self.assertEqual(calls, [self.repo])

    def test_every_generation_directory_has_a_row_whatever_its_state_document_is(self):
        """Q3a-2: a missing, dangling, directory-shaped or unreadable run.json is a diagnostic, not a run
        that silently disappears."""
        from adapters.observer.source import StoreSource
        self._start("p")
        runs = self.home / "projects" / "stray" / "runs"
        for name in ("absent", "directory", "dangling", "unreadable"):
            (runs / name / "gen-1").mkdir(parents=True)
        (runs / "directory" / "gen-1" / "run.json").mkdir()
        os.symlink(runs / "nowhere.json", runs / "dangling" / "gen-1" / "run.json")
        (runs / "unreadable" / "gen-1" / "run.json").write_text("{")
        view = view_of(Observer(StoreSource(self.home)))
        stray = next(p for p in view["projects"] if p["name"] is None)
        self.assertEqual(sorted(r["key"].split("/")[1] for r in stray["runs"]),
                         ["absent", "dangling", "directory", "unreadable"])
        self.assertEqual({r["diagnostic"] for r in stray["runs"]}, {"unreadable"})
        self.assertEqual(validate_ui_view.check(view['summary'], view['rows'], Observer(StoreSource(self.home)).details()), [])

    def test_a_stray_directory_in_the_store_hides_nothing(self):
        from adapters.observer.source import StoreSource
        self._start("p")
        (self.home / "projects" / "stray copy" / "runs" / "r" / "gen-1").mkdir(parents=True)
        (self.home / "projects" / "stray copy" / "runs" / "r" / "gen-1" / "run.json").write_text("{}")
        (self.home / "projects" / "stray two").mkdir()
        view = view_of(Observer(StoreSource(self.home)))
        rows = {p["name"]: [r["diagnostic"] for r in p["runs"]] for p in view["projects"]}
        self.assertEqual(rows, {"harbor": [None], None: ["unreadable"]})

    def test_the_view_token_changes_when_a_project_s_recorded_repository_does(self):
        from adapters.observer.source import StoreSource
        from adapters.state.locations import PROJECT
        self._start("p")
        observer = Observer(StoreSource(self.home))
        before = view_of(observer)["token"]
        moved = self.repo.parent / "harbour"
        shutil.copytree(self.repo, moved, symlinks=True)  # the same history, under another name
        (record,) = self.home.glob(f"projects/*/{PROJECT}")
        record.write_text(json.dumps({"repo": str(moved)}))
        after = view_of(observer)
        self.assertEqual(after["projects"][0]["name"], "harbour")
        self.assertNotEqual(after["token"], before)

    def test_recording_again_does_not_rewrite_and_a_bad_record_reads_as_none(self):
        from adapters.state.locations import PROJECT, Locations
        from core.records import RunKey
        locations, key = Locations(self.home), RunKey("p", "r", 1)
        locations.record(key, self.repo, self.repo)
        record = self.home / "projects" / "p" / PROJECT
        before = record.stat().st_mtime_ns
        locations.record(key, self.repo, self.repo)
        self.assertEqual((record.stat().st_mtime_ns, locations.repo("p")), (before, self.repo))
        for bad in ("not json", '{"repo": "relative/path"}', '{"repo": 7}', "[]"):
            with self.subTest(bad=bad):
                record.write_text(bad)
                self.assertIsNone(locations.repo("p"))
        record.unlink()
        os.symlink(self.repo / "elsewhere.json", record)
        self.assertIsNone(locations.repo("p"), "a symlinked record is refused")


if __name__ == "__main__":
    unittest.main()
