#!/usr/bin/env python3
"""Negative controls for scripts/validate_ui_view.py: one mutation per rule, each rejected with
its own diagnostic, so a check cannot pass for the wrong reason."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate_ui_view as v  # noqa: E402

VIEW = json.loads((v.FIXTURES / "view.json").read_text())
RUNS = json.loads((v.FIXTURES / "runs.json").read_text())
SCENARIOS = json.loads((v.FIXTURES / "scenarios.json").read_text())


def _run(view, scenario):
    return next(r for p in view["projects"] for r in p["runs"] if r["key"] == SCENARIOS[scenario])


def _detail(details, scenario):
    return details[SCENARIOS[scenario]]


class ValidateUiViewTests(unittest.TestCase):
    def assertRejected(self, view, details, needle):
        problems = v.check(view, details)
        self.assertTrue(any(needle in p for p in problems), problems)

    def test_the_committed_fixtures_pass(self):
        self.assertEqual(v.check(VIEW, RUNS), [])

    def test_an_enum_that_drifts_from_its_owner_is_rejected(self):
        view_schema, run_schema = v.schemas()
        view_schema["$defs"]["claim"]["properties"]["state"]["enum"].append("contested")
        reg = v.registry()
        state = json.loads((v.V2 / "state.schema.json").read_text())
        problems = v.mirror_problems(view_schema, run_schema, reg, state)
        self.assertTrue(any("view claim.state" in p for p in problems), problems)

    def test_assured_under_an_audit_that_is_not_independent_is_rejected(self):
        view = copy.deepcopy(VIEW)
        run = _run(view, "converged")
        self.assertTrue(all(c["gating"] for c in run["claims"]) and run["audit"]["independence"] not in ("same_model", "unverified", "mixed"))
        run["audit"]["independence"] = "same_model"
        self.assertRejected(view, RUNS, "not independent")

    def test_a_run_that_cannot_be_read_back_to_its_stop_but_has_residuals_is_rejected(self):
        runs = copy.deepcopy(RUNS)
        key = next(k for k, d in runs.items() if d["stop"] is not None)
        runs[key]["stop"]["basis"] = "unavailable"
        self.assertRejected(VIEW, runs, "residuals must be present exactly when")

    def test_today_s_evaluation_beside_an_active_run_is_rejected(self):
        runs = copy.deepcopy(RUNS)
        key = next(k for k, d in runs.items() if d["stop"] is None and d["residuals"] is not None)
        runs[key]["now"] = []
        self.assertRejected(VIEW, runs, "today's evaluation is published only beside a recorded stop")

    def test_a_row_that_needs_a_human_but_names_the_agent_s_work_is_rejected(self):
        view = copy.deepcopy(VIEW)
        run = _run(view, "parallel-human")
        detail = _detail(RUNS, "parallel-human")
        self.assertEqual((run["attention"], detail["outstanding"][0]["who"]), ("needs_you", "agent"))
        run["next"] = {k: detail["outstanding"][0][k] for k in ("who", "where", "message")}
        self.assertRejected(view, RUNS, "next is not the human's question")

    def test_a_human_question_the_host_will_show_but_listed_as_later_is_rejected(self):
        runs = copy.deepcopy(RUNS)
        view = copy.deepcopy(VIEW)
        _run(view, "parallel-human")["attention"] = "not_ended"
        self.assertRejected(view, runs, "attention 'not_ended' is not what its outstanding items say")

    def test_a_row_whose_next_is_not_its_first_obligation_is_rejected(self):
        view = copy.deepcopy(VIEW)
        _run(view, "verification")["next"] = None
        self.assertRejected(view, RUNS, "next is not the human's question")
        view = copy.deepcopy(VIEW)
        _run(view, "unreadable")["next"] = {"who": "agent", "where": "next_turn", "message": "x"}
        self.assertRejected(view, RUNS, "carries derived facts")

    def test_proposed_ceilings_exactly_while_a_proposal_waits_are_required(self):
        details = copy.deepcopy(RUNS)
        gov = _detail(details, "converged")["governance"]
        gov["proposed"] = {"passes": 1, "spawns": 1, "audit_spawns": 1}
        self.assertRejected(VIEW, details, "ceilings are proposed exactly while a proposal waits")
        details = copy.deepcopy(RUNS)
        _detail(details, "pending-amendment")["governance"]["proposed"] = None
        self.assertRejected(VIEW, details, "ceilings are proposed exactly while a proposal waits")

    def test_a_blocker_that_is_not_a_reason_is_rejected(self):
        view = copy.deepcopy(VIEW)
        _run(view, "refuted")["claims"][1]["blocker"] = "claim.contested"
        self.assertRejected(view, None, "is not a registry reason")

    def test_an_approved_claim_with_a_blocker_is_rejected(self):
        view = copy.deepcopy(VIEW)
        _run(view, "converged")["claims"][0]["blocker"] = "claim.research_missing"
        _run(view, "converged")["claims"][0]["blocker_claim"] = "C0"
        self.assertRejected(view, None, "exactly the scoped claims that are not approved")

    def test_an_unresolved_scoped_claim_without_a_blocker_is_rejected(self):
        view = copy.deepcopy(VIEW)
        claim = _run(view, "refuted")["claims"][1]
        claim["blocker"] = claim["blocker_claim"] = None
        self.assertRejected(view, None, "exactly the scoped claims that are not approved")

    def test_a_blocker_claim_outside_the_run_is_rejected(self):
        view = copy.deepcopy(VIEW)
        _run(view, "decision")["claims"][0]["blocker_claim"] = "C9"
        self.assertRejected(view, None, "blocker_claim must be one of the run's claims")

    def test_an_unreadable_row_with_a_state_is_rejected(self):
        view = copy.deepcopy(VIEW)
        _run(view, "unreadable")["state"] = "active"
        self.assertRejected(view, None, "an unreadable row reports facts of its state")

    def test_a_detail_with_an_audit_its_row_has_none_of_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["unreadable"]]["audit"] = details[SCENARIOS["converged"]]["audit"]
        self.assertRejected(VIEW, details, "audit detail must be present exactly")

    def test_a_detail_that_repeats_a_row_fact_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["converged"]]["diagnostic"] = None
        self.assertRejected(VIEW, details, "Additional properties are not allowed")

    def test_a_message_that_keeps_its_placeholder_is_rejected(self):
        details = copy.deepcopy(RUNS)
        _detail(details, "budget")["residuals"][0]["message"] = "{resource} budget was exhausted when the run stopped."
        self.assertRejected(VIEW, details, "still holds a placeholder")

    def test_a_message_that_is_not_the_registry_s_filled_in_is_rejected(self):
        details = copy.deepcopy(RUNS)
        _detail(details, "budget")["residuals"][0]["message"] = "The budget is gone."
        self.assertRejected(VIEW, details, "is not a registry reason with its wording")
        self.assertTrue(v.fits("{resource} budget exhausted.", "pass budget exhausted."))
        self.assertFalse(v.fits("{resource} budget exhausted.", "budget exhausted."))

    def test_an_outstanding_claim_the_run_does_not_have_is_rejected(self):
        details = copy.deepcopy(RUNS)
        _detail(details, "verification")["outstanding"][0]["claims"] = ["C99"]
        self.assertRejected(VIEW, details, "which the run does not have")

    def test_a_decision_that_says_where_it_is_asked_when_it_is_not_waiting_is_rejected(self):
        details = copy.deepcopy(RUNS)
        decision = next(c for c in _detail(details, "verification")["claims"] if c["decision"])["decision"]
        decision["where"] = None
        self.assertRejected(VIEW, details, "a decision says where it is asked exactly while it waits")

    def test_a_record_enum_that_drifts_from_the_core_is_rejected(self):
        view_schema, run_schema = v.schemas()
        run_schema["$defs"]["record"]["properties"]["status"]["enum"].append("waived")
        state = json.loads((v.V2 / "state.schema.json").read_text())
        problems = v.mirror_problems(view_schema, run_schema, v.registry(), state)
        self.assertTrue(any("record status" in p for p in problems), problems)

    def test_a_project_without_a_label_is_rejected(self):
        view = copy.deepcopy(VIEW)
        del view["projects"][0]["label"]
        self.assertRejected(view, None, "'label' is a required property")

    def test_budgets_on_an_undecoded_run_are_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["unreadable"]]["governance"] = details[SCENARIOS["converged"]]["governance"]
        self.assertRejected(VIEW, details, "governance must be null exactly")

    def test_a_previous_that_is_not_the_prior_generation_is_rejected(self):
        view = copy.deepcopy(VIEW)
        _run(view, "audit-failed")["previous"] = SCENARIOS["converged"]
        view["token"] = v.token_of(view)
        self.assertRejected(view, None, "is not this run's prior generation")

    def test_a_stale_token_is_rejected(self):
        view = copy.deepcopy(VIEW)
        _run(view, "stale")["version"] = "sha256:" + "0" * 64
        self.assertRejected(view, None, "token is not the digest")

    def test_runs_out_of_order_are_rejected(self):
        view = copy.deepcopy(VIEW)
        project = next(p for p in view["projects"] if len(p["runs"]) > 1)
        project["runs"].reverse()
        view["token"] = v.token_of(view)
        self.assertRejected(view, None, "runs not newest first")

    def test_a_partial_row_carrying_derived_facts_is_rejected(self):
        view = copy.deepcopy(VIEW)
        _run(view, "unreadable")["audit"] = {"verdict": "passed", "current": True, "pending": False, "independence": "distinct"}
        self.assertRejected(view, None, "row carries derived facts")

    def test_an_edge_outside_the_run_is_rejected(self):
        view = copy.deepcopy(VIEW)
        _run(view, "converged")["edges"].append({"from": "C0", "to": "C9"})
        self.assertRejected(view, None, "leaves the run's claims")

    def test_a_residual_message_that_is_not_the_registry_s_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["refuted"]]["residuals"][0]["message"] = "Something else."
        self.assertRejected(VIEW, details, "is not a registry reason with its wording")

    def test_a_blocker_message_that_is_not_its_blocker_s_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["refuted"]]["claims"][0]["blocker_message"] = "Something else."
        self.assertRejected(VIEW, details, "blocker_message is not the registry wording")

    def test_a_detail_whose_version_differs_from_its_row_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["stale"]]["version"] = "sha256:" + "1" * 64
        self.assertRejected(VIEW, details, "key or version differs")

    def test_a_schema_violation_is_rejected(self):
        view = copy.deepcopy(VIEW)
        _run(view, "converged")["state"] = "settled"
        self.assertRejected(view, None, "'settled' is not one of")


if __name__ == "__main__":
    unittest.main()
