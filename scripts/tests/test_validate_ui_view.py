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
        self.assertRejected(view, None, "an unreadable row reports a state")

    def test_a_detail_with_an_audit_its_row_has_none_of_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["older-format"]]["audit"] = details[SCENARIOS["converged"]]["audit"]
        self.assertRejected(VIEW, details, "audit detail must be present exactly")

    def test_a_detail_that_repeats_a_row_fact_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["converged"]]["diagnostic"] = None
        self.assertRejected(VIEW, details, "Additional properties are not allowed")

    def test_budgets_on_an_undecoded_run_are_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["older-format"]]["budgets"] = details[SCENARIOS["converged"]]["budgets"]
        self.assertRejected(VIEW, details, "budgets and control must be null exactly")

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
        _run(view, "older-format")["audit"] = "passed"
        self.assertRejected(view, None, "row carries derived facts")

    def test_an_edge_outside_the_run_is_rejected(self):
        view = copy.deepcopy(VIEW)
        _run(view, "converged")["edges"].append({"from": "C0", "to": "C9"})
        self.assertRejected(view, None, "leaves the run's claims")

    def test_a_residual_message_that_is_not_the_registry_s_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["refuted"]]["residuals"][0]["message"] = "Something else."
        self.assertRejected(VIEW, details, "is not a registry reason with its message")

    def test_a_blocker_message_that_is_not_its_blocker_s_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["refuted"]]["claims"][0]["blocker_message"] = "Something else."
        self.assertRejected(VIEW, details, "blocker_message is not the registry message")

    def test_a_detail_whose_version_differs_from_its_row_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["stale"]]["version"] = "sha256:" + "1" * 64
        self.assertRejected(VIEW, details, "key or version differs")

    def test_a_schema_violation_is_rejected(self):
        view = copy.deepcopy(VIEW)
        _run(view, "converged")["claims"][0]["state"] = "supported"
        self.assertRejected(view, None, "'supported' is not one of")


if __name__ == "__main__":
    unittest.main()
