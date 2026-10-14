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

SUMMARY = json.loads((v.FIXTURES / "summary.json").read_text())
ROWS = json.loads((v.FIXTURES / "rows.json").read_text())
RUNS = json.loads((v.FIXTURES / "runs.json").read_text())
SCENARIOS = json.loads((v.FIXTURES / "scenarios.json").read_text())


def _run(rows, scenario):
    return next(r for r in rows if r["key"] == SCENARIOS[scenario])


def _detail(details, scenario):
    return details[SCENARIOS[scenario]]


class ValidateUiViewTests(unittest.TestCase):
    def assertRejected(self, rows, details, needle):
        problems = v.check(SUMMARY, rows, details)
        self.assertTrue(any(needle in p for p in problems), problems)

    def test_the_committed_fixtures_pass(self):
        self.assertEqual(v.check(SUMMARY, ROWS, RUNS), [])

    def test_an_enum_that_drifts_from_its_owner_is_rejected(self):
        view_schema, run_schema = v.schemas()
        view_schema["$defs"]["claim"]["properties"]["state"]["enum"].append("contested")
        reg = v.registry()
        state = json.loads((v.V2 / "state.schema.json").read_text())
        problems = v.mirror_problems(view_schema, run_schema, reg, state)
        self.assertTrue(any("view claim.state" in p for p in problems), problems)

    def test_assured_under_an_audit_that_is_not_independent_is_rejected(self):
        view = copy.deepcopy(ROWS)
        run = _run(view, "converged")
        self.assertTrue(all(c["gating"] for c in run["claims"]) and run["audit"]["independence"] not in ("same_model", "unverified", "mixed"))
        run["audit"]["independence"] = "same_model"
        self.assertRejected(view, RUNS, "not independent")

    def test_a_run_that_cannot_be_read_back_to_its_stop_but_has_residuals_is_rejected(self):
        runs = copy.deepcopy(RUNS)
        key = next(k for k, d in runs.items() if d["stop"] is not None)
        runs[key]["stop"]["basis"] = "unavailable"
        self.assertRejected(ROWS, runs, "residuals must be present exactly when")

    def test_today_s_evaluation_beside_an_active_run_is_rejected(self):
        runs = copy.deepcopy(RUNS)
        key = next(k for k, d in runs.items() if d["stop"] is None and d["residuals"] is not None)
        runs[key]["now"] = []
        self.assertRejected(ROWS, runs, "today's evaluation is published only beside a recorded stop")

    def test_a_row_that_needs_a_human_but_names_the_agent_s_work_is_rejected(self):
        view = copy.deepcopy(ROWS)
        run = _run(view, "parallel-human")
        detail = _detail(RUNS, "parallel-human")
        self.assertEqual((run["attention"], detail["outstanding"][0]["who"]), ("needs_you", "agent"))
        run["next"] = {k: detail["outstanding"][0][k] for k in ("who", "where", "message")}
        self.assertRejected(view, RUNS, "next is not the human's question")

    def test_a_human_question_the_host_will_show_but_listed_as_later_is_rejected(self):
        runs = copy.deepcopy(RUNS)
        human = next(i for i in _detail(runs, "parallel-human")["outstanding"] if i["who"] == "human")
        human["where"] = "later"
        self.assertRejected(ROWS, runs, "attention 'needs_you' is not what its outstanding items say")

    def test_a_row_whose_next_is_not_its_first_obligation_is_rejected(self):
        view = copy.deepcopy(ROWS)
        _run(view, "verification")["next"] = None
        self.assertRejected(view, RUNS, "next is not the human's question")
        view = copy.deepcopy(ROWS)
        _run(view, "unreadable")["next"] = {"who": "agent", "where": "next_turn", "message": "x"}
        self.assertRejected(view, RUNS, "carries derived facts")

    def test_proposed_ceilings_exactly_while_a_proposal_waits_are_required(self):
        details = copy.deepcopy(RUNS)
        gov = _detail(details, "converged")["governance"]
        gov["proposed"] = {"passes": 1, "spawns": 1, "audit_spawns": 1}
        self.assertRejected(ROWS, details, "ceilings are proposed exactly while a proposal waits")
        details = copy.deepcopy(RUNS)
        _detail(details, "pending-amendment")["governance"]["proposed"] = None
        self.assertRejected(ROWS, details, "ceilings are proposed exactly while a proposal waits")

    def test_a_blocker_that_is_not_a_reason_is_rejected(self):
        view = copy.deepcopy(ROWS)
        _run(view, "refuted")["claims"][1]["blocker"] = "claim.contested"
        self.assertRejected(view, RUNS, "is not a registry reason")

    def test_an_approved_claim_with_a_blocker_is_rejected(self):
        view = copy.deepcopy(ROWS)
        _run(view, "converged")["claims"][0]["blocker"] = "claim.research_missing"
        _run(view, "converged")["claims"][0]["blocker_claim"] = "C0"
        self.assertRejected(view, RUNS, "exactly the scoped claims that are not approved")

    def test_an_unresolved_scoped_claim_without_a_blocker_is_rejected(self):
        view = copy.deepcopy(ROWS)
        claim = _run(view, "refuted")["claims"][1]
        claim["blocker"] = claim["blocker_claim"] = None
        self.assertRejected(view, RUNS, "exactly the scoped claims that are not approved")

    def test_a_blocker_claim_outside_the_run_is_rejected(self):
        view = copy.deepcopy(ROWS)
        _run(view, "decision")["claims"][0]["blocker_claim"] = "C9"
        self.assertRejected(view, RUNS, "blocker_claim must be one of the run's claims")

    def test_an_unreadable_row_with_a_state_is_rejected(self):
        view = copy.deepcopy(ROWS)
        _run(view, "unreadable")["state"] = "active"
        self.assertRejected(view, RUNS, "an unreadable row reports facts of its state")

    def test_a_detail_with_an_audit_its_row_has_none_of_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["unreadable"]]["audit"] = details[SCENARIOS["converged"]]["audit"]
        self.assertRejected(ROWS, details, "audit detail must be present exactly")

    def test_a_detail_that_repeats_a_row_fact_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["converged"]]["diagnostic"] = None
        self.assertRejected(ROWS, details, "Additional properties are not allowed")

    def test_a_message_that_keeps_its_placeholder_is_rejected(self):
        details = copy.deepcopy(RUNS)
        _detail(details, "budget")["residuals"][0]["message"] = "{resource} budget was exhausted when the run stopped."
        self.assertRejected(ROWS, details, "still holds a placeholder")

    def test_a_message_that_is_not_the_registry_s_filled_in_is_rejected(self):
        details = copy.deepcopy(RUNS)
        _detail(details, "budget")["residuals"][0]["message"] = "The budget is gone."
        self.assertRejected(ROWS, details, "is not a registry reason with its wording")
        self.assertTrue(v.fits("{resource} budget exhausted.", "pass budget exhausted."))
        self.assertFalse(v.fits("{resource} budget exhausted.", "budget exhausted."))

    def test_an_outstanding_claim_the_run_does_not_have_is_rejected(self):
        details = copy.deepcopy(RUNS)
        _detail(details, "verification")["outstanding"][0]["claims"] = ["C99"]
        self.assertRejected(ROWS, details, "which the run does not have")

    def test_a_decision_that_says_where_it_is_asked_when_it_is_not_waiting_is_rejected(self):
        details = copy.deepcopy(RUNS)
        decision = next(c for c in _detail(details, "verification")["claims"] if c["decision"])["decision"]
        decision["where"] = None
        self.assertRejected(ROWS, details, "a decision says where it is asked exactly while it waits")

    def test_a_record_enum_that_drifts_from_the_core_is_rejected(self):
        view_schema, run_schema = v.schemas()
        run_schema["$defs"]["record"]["properties"]["status"]["enum"].append("waived")
        state = json.loads((v.V2 / "state.schema.json").read_text())
        problems = v.mirror_problems(view_schema, run_schema, v.registry(), state)
        self.assertTrue(any("record status" in p for p in problems), problems)

    def test_budgets_on_an_undecoded_run_are_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["unreadable"]]["governance"] = details[SCENARIOS["converged"]]["governance"]
        self.assertRejected(ROWS, details, "governance must be null exactly")

    def test_a_previous_that_is_not_the_prior_generation_is_rejected(self):
        view = copy.deepcopy(ROWS)
        _run(view, "audit-failed")["previous"] = SCENARIOS["converged"]
        self.assertRejected(view, RUNS, "is not this run's prior generation")

    def test_a_partial_row_carrying_derived_facts_is_rejected(self):
        view = copy.deepcopy(ROWS)
        _run(view, "unreadable")["audit"] = {"verdict": "passed", "current": True, "pending": False, "independence": "distinct"}
        self.assertRejected(view, RUNS, "row carries derived facts")

    def test_an_edge_outside_the_run_is_rejected(self):
        view = copy.deepcopy(ROWS)
        _run(view, "converged")["edges"].append({"from": "C0", "to": "C9"})
        self.assertRejected(view, RUNS, "leaves the run's claims")

    def test_a_residual_message_that_is_not_the_registry_s_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["refuted"]]["residuals"][0]["message"] = "Something else."
        self.assertRejected(ROWS, details, "is not a registry reason with its wording")

    def test_a_blocker_message_that_is_not_its_blocker_s_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["refuted"]]["claims"][0]["blocker_message"] = "Something else."
        self.assertRejected(ROWS, details, "blocker_message is not the registry wording")

    def test_a_detail_whose_version_differs_from_its_row_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["audit-failed"]]["version"] = "sha256:" + "1" * 64
        self.assertRejected(ROWS, details, "key or version differs")

    def test_a_sheet_whose_row_is_not_the_index_s_is_rejected(self):
        details = copy.deepcopy(RUNS)
        _detail(details, "audit-failed")["row"]["standing"] = "proven"
        self.assertRejected(ROWS, details, "the row it carries is not the index's row")

    def test_a_sheet_that_lacks_an_attempt_is_rejected(self):
        details = copy.deepcopy(RUNS)
        key = next(k for k, d in details.items() if len(d["attempts"]) > 1)
        details[key]["attempts"].pop()
        self.assertRejected(ROWS, details, "attempts are not every attempt at the run, by generation")

    def test_a_schema_violation_is_rejected(self):
        view = copy.deepcopy(ROWS)
        _run(view, "converged")["state"] = "settled"
        self.assertRejected(view, RUNS, "'settled' is not one of")


class ValidateUiViewSummaryTests(unittest.TestCase):
    def assertRejected(self, summary, rows, details, needle):
        problems = v.view_problems(summary, rows, details)
        self.assertTrue(any(needle in p for p in problems), problems)

    def test_the_committed_fixtures_hold(self):
        self.assertEqual(v.view_problems(SUMMARY, ROWS, RUNS), [])

    def test_a_standing_that_disagrees_with_attention_is_rejected(self):
        rows = copy.deepcopy(ROWS)
        _run(rows, "audit-failed")["standing"] = "needs_human"
        self.assertRejected(SUMMARY, rows, RUNS, "needs_human exactly when")

    def test_a_place_out_of_start_order_is_rejected(self):
        summary = copy.deepcopy(SUMMARY)
        n = next(i for i, p in enumerate(summary["marks"]["place"]) if p == 0)
        summary["marks"]["place"][n] = 1
        self.assertRejected(summary, ROWS, RUNS, "its place is not its ordinal in its project's start order")

    def test_a_project_standing_count_that_is_not_its_runs_is_rejected(self):
        summary = copy.deepcopy(SUMMARY)
        p = summary["projects"][0]
        k = next(iter(p["standings"]))
        p["standings"][k] += 1
        self.assertRejected(summary, ROWS, RUNS, "standings count exactly its runs by standing")

    def test_a_needs_list_grouped_by_project_is_rejected(self):
        summary = copy.deepcopy(SUMMARY)
        summary["needs"] = sorted(summary["needs"], key=lambda n: n["key"])
        self.assertNotEqual(summary["needs"], SUMMARY["needs"], "the fixture's needs are not in key order")
        self.assertRejected(summary, ROWS, RUNS, "needs is in asked order")

    def test_a_needs_bucket_in_key_order_is_rejected(self):
        rows = copy.deepcopy(ROWS)
        needs = [i for i, r in enumerate(rows) if r["standing"] == "needs_human"]
        bucket = sorted((rows[i] for i in needs), key=lambda r: r["key"])
        self.assertNotEqual([r["key"] for r in bucket], [rows[i]["key"] for i in needs], "the fixture's needs rows are not in key order")
        for i, r in zip(needs, bucket):
            rows[i] = r
        self.assertRejected(SUMMARY, rows, RUNS, "not in page order")

    def test_an_ask_on_an_ended_run_is_rejected(self):
        rows = copy.deepcopy(ROWS)
        _run(rows, "converged")["ask"] = "decide_claim"
        self.assertRejected(SUMMARY, rows, RUNS, "only an active run asks")

    def test_a_total_that_is_not_the_runs_counted_is_rejected(self):
        summary = copy.deepcopy(SUMMARY)
        summary["aggregates"]["total"] += 1
        self.assertRejected(summary, ROWS, RUNS, "the aggregates' total is the runs they count")

    def test_a_project_s_needs_that_is_not_its_runs_that_need_a_human_is_rejected(self):
        summary = copy.deepcopy(SUMMARY)
        busy = next(p for p in summary["projects"] if p["needs"])
        busy["needs"] -= 1
        self.assertRejected(summary, ROWS, RUNS, "needs counts exactly its runs that need a human")

    def test_a_decision_on_a_claim_the_run_does_not_have_is_rejected(self):
        details = copy.deepcopy(RUNS)
        decided = next(d for d in details.values() if any(x["claim"] for x in d["decisions"] or ()))
        next(x for x in decided["decisions"] if x["claim"])["claim"] = "C999"
        self.assertRejected(SUMMARY, ROWS, details, "a decision is attached to a claim of its run")

    def test_a_latest_audit_that_is_not_the_rows_verdict_is_rejected(self):
        details = copy.deepcopy(RUNS)
        details[SCENARIOS["audit-failed"]]["audits"][0]["verdict"] = "passed"
        self.assertRejected(SUMMARY, ROWS, details, "the first of audits (newest first)")

    def test_audits_oldest_first_are_rejected(self):
        details = copy.deepcopy(RUNS)
        one = details[SCENARIOS["audit-failed"]]["audits"][0]
        details[SCENARIOS["audit-failed"]]["audits"] = [one, {**one, "at": one["at"] + 60}]  # the older first
        self.assertRejected(SUMMARY, ROWS, details, "audits are newest first")

    def test_coverage_that_does_not_sum_is_rejected(self):
        summary = copy.deepcopy(SUMMARY)
        summary["coverage"]["read"] += 1
        self.assertRejected(summary, ROWS, RUNS, "coverage does not sum")

    def test_a_vocabulary_that_drifts_from_its_owner_is_rejected(self):
        schemas = v.view_schemas()
        schemas["row"]["$defs"]["row"]["properties"]["standing"]["enum"].reverse()
        self.assertTrue(any("vocabulary row standing" in p for p in v.view_problems(SUMMARY, ROWS, RUNS, schemas)))


class ContractIdTests(unittest.TestCase):
    """The contract's folder owns its id: what the schemas declare and what the server, the demo, the fixtures and the
    client serve or expect is ``empirica-ui-view/<folder>``, and the folder is ``v1`` (the contract was never released)."""

    def test_the_contract_folder_is_its_first_version(self):
        self.assertEqual(v.UI_VIEW.name, "v1")

    def test_the_served_and_declared_ids_are_the_folders(self):
        self.assertEqual(v.contract_id_problems(), [])

    def test_a_server_that_serves_another_id_is_rejected(self):
        from unittest import mock
        from application import observer
        with mock.patch.object(observer, "CONTRACT", "empirica-ui-view/v2"):
            problems = v.contract_id_problems()
        self.assertTrue(any("served" in p and "empirica-ui-view/v1" in p for p in problems), problems)

    def test_a_schema_that_declares_another_id_is_rejected(self):
        schemas = v.view_schemas()
        schemas["page"]["properties"]["contract"]["const"] = "empirica-ui-view/v2"
        problems = v.contract_id_problems(schemas=schemas)
        self.assertTrue(any("page" in p and "empirica-ui-view/v1" in p for p in problems), problems)


class GoldenFixtureTests(unittest.TestCase):
    """The fixtures are golden examples (T1 B): one run per standing and per ask a human can be shown, for the docs and
    the built-in demo. Which values the contract can hold is the schema-derived generators' (``test_ui_view_generators``)."""

    def test_every_standing_and_every_reachable_ask_has_a_golden_run(self):
        row = v.view_schemas()["row"]["$defs"]["row"]["properties"]
        self.assertEqual({r["standing"] for r in ROWS}, set(row["standing"]["enum"]))
        self.assertEqual({r["ask"] for r in ROWS} - {None},
                         {"approve_configuration", "approve_revision", "decide_claim", "confirm_source", "freeze_scope"})
        # P3c: every ask a fixture holds is a human's pending action, so its run is filed under Needs a human
        self.assertEqual({r["standing"] for r in ROWS if r["ask"]}, {"needs_human"})

    def test_the_fixtures_are_the_minimal_selection(self):
        self.assertLessEqual(len(RUNS), 16)


class FixtureCopyTests(unittest.TestCase):
    """The vendored fixtures (the plugin's built-in demo) are a byte copy of the contract's."""

    def test_the_vendored_copy_is_a_byte_copy(self):
        import regen_ui_view_fixtures as regen
        self.assertEqual(regen.copy_problems(), [])

    def test_one_changed_byte_is_rejected(self):
        import shutil
        import tempfile
        import regen_ui_view_fixtures as regen
        with tempfile.TemporaryDirectory() as scratch:
            copy_dir = Path(scratch) / "fixtures"
            shutil.copytree(regen.ROOT / regen.FIXTURES, copy_dir)
            text = (copy_dir / "rows.json").read_bytes()
            (copy_dir / "rows.json").write_bytes(text[:100] + bytes([text[100] ^ 1]) + text[101:])
            problems = regen.copy_problems(regen.ROOT / regen.FIXTURES, copy_dir)
        self.assertTrue(any("rows.json" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
