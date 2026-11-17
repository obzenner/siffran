"""ADR-0067 scenario matrix (R2 fix round): what the human is asked, when, and what the run says after.

One test per collapsed cell of the matrix in the ADR's "Scenarios" table. A cell marked [D] was wrong
before the R2 fix; its test is in the mutation table of the build progress log, which restores the old rule
and shows the test fail. The host is the shared Claude mediator with a scripted human; the Claude Stop and
Pi gate rules over the same facts are tested in their own suites."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
import regen_contract_fixtures  # noqa: E402

from adapters import confirmation
from adapters.author_view import render_author_view
from core import confirmation as core_confirmation, evaluation
from application import protocol
from assertions import ConformanceCase, action_freeze, action_graph, get_run, observe_action
from governance_setup import TEST_INVOCATION, invocation_for
from observer_store import PROFILE, _Builder, _graph
from test_evidence_confirmation import URL, WORKSPACE_REF, confirm, graph_with_decision
from test_evidence_hosts import Human, MediatorFixture, accept
from application.protocol import host_profile_id  # noqa: E402

CONFIRMATION = "evidence.confirmation_required"


def lock_only():
    """A run whose records are asked about whatever the mode (the inert rule removed), so that the route lock is the one
    guard standing: what the lock refuses when nothing upstream keeps the record out of a batch."""
    return mock.patch.object(core_confirmation, "asks_human", lambda snapshot, record: (
        core_confirmation.pending_route(snapshot, record) in core_confirmation.HUMAN_ROUTES))


def inert_only():
    """A run whose human route is open whatever the mode (the lock removed), so that the inert rule is the one guard
    standing: what keeps a record that needs a human out of a batch."""
    return mock.patch.object(core_confirmation, "human_route", return_value="human")


def asked_as_before():
    """A run as an earlier version asked it: a human route, and a record that needs a human is in the batch whatever the
    mode. The stored presentations and receipts it leaves are what a persisted run holds."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(inert_only())
    stack.enter_context(lock_only())
    return stack


class ScenarioFixture(MediatorFixture):
    def two_claims(self):
        """C0 supported by C1, both ordinary; the caller gives research to whichever it wants."""
        return self.start(graph=self.chain())

    @staticmethod
    def chain() -> dict:
        from assertions import canonical_graph
        return canonical_graph(n_claims=2, kind="ordinary")

    def stop(self, drv, run_id) -> dict:
        return self.end_run(drv, run_id, "stop")["result"]


def _requirement(stage: str, obligation: str, admissible: bool = True) -> evaluation.Requirement:
    return evaluation.Requirement(stage, obligation, obligation, None, {}, (), None, "agent", "next_turn", admissible)


class FirstAdmissibleCells(ConformanceCase):
    """The report's first admissible requirement is the first row ``report_requirements`` marks ``admissible``: it is the
    one owner of admissibility, and the gate, the view and the turn end's actor read the selector, never a row's position
    or its stage."""

    def test_no_requirement_has_no_first(self):
        self.assertIsNone(evaluation.first_admissible(()))

    def test_no_admissible_row_has_no_first(self):
        self.assertIsNone(evaluation.first_admissible([_requirement("audit", "a", False)]))

    def test_the_first_admissible_row_of_a_stage_ordered_report(self):
        rows = [_requirement("claim", "c"), _requirement("passes", "p", False), _requirement("audit", "a", False)]
        self.assertIs(evaluation.first_admissible(rows), rows[0])

    def test_a_held_row_at_an_earlier_stage_is_not_chosen(self):
        rows = [_requirement("confirmation", "held", False), _requirement("passes", "p")]
        self.assertIs(evaluation.first_admissible(rows), rows[1])

    def test_an_admissible_row_out_of_stage_order_is_the_first_in_row_order(self):
        rows = [_requirement("terminal", "t", False), _requirement("audit", "a"), _requirement("passes", "p")]
        self.assertIs(evaluation.first_admissible(rows), rows[1])

    def test_a_tie_goes_to_the_first_in_row_order(self):
        rows = [_requirement("audit", "a", False), _requirement("claim", "first"), _requirement("claim", "second")]
        self.assertIs(evaluation.first_admissible(rows), rows[1])

    def test_the_stage_rank_is_the_one_order(self):
        self.assertEqual([evaluation.stage_rank(s) for s in evaluation.REPORT_STAGES], list(range(6)))
        self.assertEqual(evaluation.earliest_stage(["audit", "claim", "terminal"]), evaluation.stage_rank("claim"))
        self.assertIsNone(evaluation.earliest_stage([]))


class HumanSurfaceCells(ScenarioFixture):
    """Hosts with a human to ask (H) and hosts without (U)."""

    # ---- H: verbatim only, URL only ---------------------------------------------------------

    def test_verbatim_research_has_no_batch_and_no_dialog(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "code", WORKSPACE_REF)
        self.assertIsNone(self.batch(drv, run_id))
        human = Human(accept(d1="confirm"))
        self.mediate(drv, run_id, human)
        self.assertEqual((human.shown, self.codes(self.report(drv, run_id))), ([], ["audit.required"]))

    def test_a_url_is_one_batch_with_one_reason_and_a_mediator_that_asks_once(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        record = self.last_id(drv, run_id)
        self.assertEqual(self.batch(drv, run_id)["prompt_error"], "unobserved")  # Claude has not looked yet
        reason = self.report(drv, run_id)["reasons"][0]  # the report looks first
        batch = self.batch(drv, run_id)
        self.assertEqual({k: batch[k] for k in ("record_ids", "claim_ids", "prompt_error")},
                         {"record_ids": [record], "claim_ids": [], "prompt_error": None})
        self.assertEqual((reason["code"], reason["parameters"]),
                         (CONFIRMATION, {"route": "human", "record_ids": [record]}))
        human = Human(accept(d1="confirm"))
        self.mediate(drv, run_id, human)
        self.assertEqual(len(human.shown), 1)
        self.assertIsNone(self.batch(drv, run_id))

    # ---- H: URL while another claim is open -------------------------------------------------

    def test_D_an_open_claim_leads_and_the_human_is_still_asked(self):
        drv, run_id = self.two_claims()
        self.research(drv, run_id, "web", URL, claim="C0")  # C1 has no research at all
        blocked = self.report(drv, run_id)
        self.assertEqual([r["code"] for r in blocked["reasons"]], ["claim.research_missing"])
        self.assertEqual(blocked["reasons"][0]["affected"], {"obligation_id": "claim:C1"})
        self.assertEqual(self.batch(drv, run_id)["prompt_error"], None)  # the batch is a fact of the run
        human = Human(accept(d1="confirm"))
        self.assertEqual(self.mediate(drv, run_id, human, blocked=blocked)["reasons"][0]["code"],
                         "claim.research_missing")  # confirmed, and the open claim still leads
        self.assertEqual(len(human.shown), 1)
        self.assertEqual(self.verification(drv, run_id)["status"], "confirmed_by_human")

    def test_D_when_the_open_claim_is_done_the_batch_becomes_the_reason(self):
        drv, run_id = self.two_claims()
        self.research(drv, run_id, "web", URL, claim="C0")
        self.research(drv, run_id, "code", WORKSPACE_REF, claim="C1")
        self.assertEqual(self.codes(self.report(drv, run_id)), [CONFIRMATION])

    # ---- H: Later, cancel, timeout ----------------------------------------------------------

    def test_D_later_cancel_and_timeout_grant_nothing_and_an_explicit_report_asks_again(self):
        for label, reply in (("later", accept(d1="later")), ("cancel", {"action": "cancel"}), ("timeout", None)):
            with self.subTest(label):
                drv, run_id = self.start()
                self.research(drv, run_id, "web", URL)
                self.mediate(drv, run_id, Human(reply))
                self.assertEqual(self.batch(drv, run_id)["prompt_error"], "already_presented")
                self.assertEqual(self.codes(self.report(drv, run_id)), [CONFIRMATION])  # still the reason
                # Nothing pushes the author to ask again (the view says shown); an explicit report does (G14).
                again = Human(accept(d1="confirm"))
                self.mediate(drv, run_id, again)
                self.assertEqual(len(again.shown), 1)
                self.research(drv, run_id, "web", URL + "/changed")  # a changed derivation is new, too
                self.assertIsNone(self.batch(drv, run_id)["prompt_error"])

    def test_the_route_changing_never_makes_a_presented_key_new(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.mediate(drv, run_id, Human(accept(d1="later")))
        self.surface(drv, run_id, "unavailable")
        self.assertEqual(self.batch(drv, run_id)["prompt_error"], "unavailable")
        self.surface(drv, run_id, "human")
        self.assertEqual(self.batch(drv, run_id)["prompt_error"], "already_presented")

    # ---- H: reject with a note --------------------------------------------------------------

    def test_rejecting_the_last_supporting_record_shows_the_note_on_its_obligation(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.mediate(drv, run_id, Human(accept(d1="reject", n1="That is the wrong spec.")))
        blocked = self.report(drv, run_id)
        self.assertEqual(self.codes(blocked), ["claim.research_missing"])
        row = next(o for o in self.run_view(drv, run_id)["obligations"]["active"] if o["id"] == "claim:C0")
        self.assertEqual([(r.get("rejected"), r.get("note")) for r in row["observed"]],
                         [(True, "That is the wrong spec.")])

    # ---- H: interactions exhausted ----------------------------------------------------------

    def test_D_exhausted_interactions_are_a_prompt_error_not_an_answerable_wait(self):
        drv, run_id = self.start()
        self.look(drv, run_id)
        for n in range(400):
            self.research(drv, run_id, "web", f"{URL}/{n}")
            batch = self.batch(drv, run_id)
            if batch["prompt_error"] == "interaction_limit":
                break
            self.assertEqual(self.present(drv, run_id, f"p{n}")["type"], "Allow", n)
        self.assertEqual(self.batch(drv, run_id)["prompt_error"], "interaction_limit")
        self.assertEqual(self.interactions(drv, run_id)["total"], 0)
        refused = self.present(drv, run_id, "last")
        self.assertEqual((refused["type"], refused["reasons"][0]["code"]),
                         ("Block", "governance.interaction_limit"))
        self.assertEqual(self.codes(self.report(drv, run_id)), [CONFIRMATION])

    # ---- H: needs-decision ------------------------------------------------------------------

    def test_D_a_root_needs_decision_is_asked_on_its_own_and_converges_after_an_audit(self):
        drv, run_id = self.start(kind="needs-decision")
        self.assertEqual(self.batch(drv, run_id)["prompt_error"], "unobserved")
        reason = self.report(drv, run_id)["reasons"][0]  # the report looks first
        batch = self.batch(drv, run_id)
        self.assertEqual((batch["record_ids"], batch["claim_ids"], batch["prompt_error"]), ([], ["C0"], None))
        self.assertEqual((reason["code"], reason["parameters"]["claim_ids"], reason["next_actions"]),
                         (CONFIRMATION, ["C0"], ["convergence.report", "human.confirm_evidence", "residual.accept"]))
        human = Human(accept(d1="approve", n1="Ship it."))
        self.mediate(drv, run_id, human)
        self.assertIn("Decision needed", human.shown[0][0])
        self.assertEqual(self.claim(drv, run_id)["human_decision"], {"outcome": "approve", "answer": "Ship it."})
        self.assertEqual(self.codes(self.report(drv, run_id)), ["audit.required"])
        self.dispatch(drv, observe_action(run_id=run_id, action=action_freeze()))
        converged = self.audit(drv, run_id)
        self.assertTrue(converged["converged"])
        self.assertEqual(self.run_view(drv, run_id)["audit"]["independence"], "human_owned")

    def test_D_a_decision_under_an_ordinary_root_is_asked_while_the_root_has_its_research(self):
        drv, run_id = self.start(graph=graph_with_decision())
        self.research(drv, run_id, "code", WORKSPACE_REF)
        human = Human(accept(d1="approve"))
        self.mediate(drv, run_id, human)
        self.assertEqual(len(human.shown), 1)
        self.assertEqual(self.claim(drv, run_id, "C1")["state"], "approved")

    def test_a_rejected_decision_keeps_its_answer_and_discards_the_claim(self):
        drv, run_id = self.start(kind="needs-decision")
        self.mediate(drv, run_id, Human(accept(d1="reject", n1="Not this quarter.")))
        claim = self.claim(drv, run_id)
        self.assertEqual((claim["state"], claim["human_decision"]),
                         ("discarded", {"outcome": "reject", "answer": "Not this quarter."}))
        self.assertEqual(self.codes(self.report(drv, run_id)), ["claim.refuted"])

    def test_decisions_and_records_are_one_batch_decisions_first(self):
        drv, run_id = self.start(graph=graph_with_decision())
        self.research(drv, run_id, "web", URL)
        batch = self.batch(drv, run_id)
        self.assertEqual((batch["claim_ids"], len(batch["record_ids"])), (["C1"], 1))
        parameters = self.report(drv, run_id)["reasons"][0]["parameters"]
        self.assertEqual((parameters["claim_ids"], parameters["record_ids"]), (["C1"], batch["record_ids"]))
        shown = Human(accept())
        self.mediate(drv, run_id, shown)
        self.assertLess(shown.shown[0][0].index("Decision needed"), shown.shown[0][0].index("source:"))

    def test_an_audit_failing_after_a_human_approval_reaudits_without_asking_again(self):
        drv, run_id = self.start(kind="needs-decision")
        self.mediate(drv, run_id, Human(accept(d1="approve")))
        self.dispatch(drv, observe_action(run_id=run_id, action=action_freeze()))
        child = self.require_pending_audit_child(drv, run_id)
        self.require_trusted_audit_attribution(drv, run_id, child, variant="distinct")
        verdict = self.build_audit_verdict_payload(drv, run_id, verdict="fail", scope_review="pass",
                                                   findings=["The question was not answerable."])
        drv.trusted_audit_verdict(run_id, child, verdict)
        self.assertEqual(self.codes(self.report(drv, run_id)), ["audit.failed"])
        self.assertIsNone(self.batch(drv, run_id))  # the answer stands: it is bound to the wording

    def test_a_changed_wording_of_an_answered_decision_asks_again(self):
        drv, run_id = self.start(kind="needs-decision")
        self.mediate(drv, run_id, Human(accept(d1="approve")))
        self.assertIsNone(self.batch(drv, run_id))
        reworded = self.chain_root_decision("A different question.")
        self.dispatch(drv, observe_action(run_id=run_id, action=self.graph_action(reworded)))
        self.assertEqual(self.batch(drv, run_id)["claim_ids"], ["C0"])

    @staticmethod
    def graph_action(graph):
        from assertions import action_graph
        return action_graph(graph)

    @staticmethod
    def chain_root_decision(text: str) -> dict:
        from assertions import canonical_graph
        graph = canonical_graph(kind="needs-decision")
        graph["claims"][0]["text"] = text
        return graph

    def audit(self, drv, run_id, variant="distinct") -> dict:
        child = self.require_pending_audit_child(drv, run_id)
        self.require_trusted_audit_attribution(drv, run_id, child, variant=variant)
        payload = self.build_audit_verdict_payload(drv, run_id, verdict="pass", scope_review="pass")
        self.assertEqual(drv.trusted_audit_verdict(run_id, child, payload)["result"]["type"], "Allow")
        return self.report(drv, run_id)

    # ---- the parity of the one reason, the obligation and the residual -----------------------

    def test_the_batch_reason_the_obligation_and_the_residual_name_the_same_ids(self):
        drv, run_id = self.start(kind="needs-decision")
        gate = self.report(drv, run_id)["reasons"][0]["parameters"]
        row = next(o for o in self.run_view(drv, run_id)["obligations"]["active"] if o["id"] == "claim:C0")
        self.assertEqual(row["missing"]["parameters"], gate)
        stopped = self.stop(drv, run_id)["run"]
        residual = next(r for r in stopped["residuals"] if r["code"] == CONFIRMATION)
        self.assertEqual(residual["parameters"], gate)
        self.assertIsNone(stopped["confirmation_batch"])  # a stopped run asks nobody

    # ---- U: no human to ask -----------------------------------------------------------------

    def unavailable(self, **start):
        with lock_only():
            return self._unavailable(**start)

    def _unavailable(self, **start):
        drv, run_id = self.start(**start)
        self.research(drv, run_id, "web", URL)
        human = Human(accept(d1="confirm"))
        blocked = self.report(drv, run_id)
        self.assertEqual(blocked["reasons"][0]["parameters"]["route"], "unavailable")
        self.assertEqual(blocked["reasons"][0]["next_actions"], ["residual.accept"])
        self.assertEqual(self.batch(drv, run_id)["prompt_error"], "unavailable")
        self.assertEqual(self.mediate(drv, run_id, human, blocked=blocked), blocked)
        self.assertEqual(human.shown, [])
        self.assertEqual(self.present(drv, run_id)["type"], "Block")
        return drv, run_id

    def test_D_a_delegated_auto_run_has_nobody_to_ask(self):
        self.unavailable(mode="auto", delegation=True, interactive=False)

    def test_L1_an_interactive_auto_run_has_nobody_to_ask_before_and_after_the_host_looks(self):
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "web", URL)
        with lock_only():
            self.assertEqual(self.batch(drv, run_id)["prompt_error"], "unavailable")  # before the host has looked
        self.unavailable(mode="auto")  # after it recorded a human surface: the reason, the actions, no dialog, the Block

    def test_L2_an_interactive_auto_presentation_or_receipt_is_refused_whatever_the_surface(self):
        drv, run_id = self.unavailable(mode="auto")
        self.assertEqual(self.run_view(drv, run_id)["invocation"]["human_surface"], "human")
        with lock_only():
            refused = self.present(drv, run_id)
            self.assertEqual((refused["type"], refused["reasons"][0]["code"]), ("Block", "governance.approval_unavailable"))
            self.assertEqual(self.answer(drv, run_id, "p1", confirm(self.last_id(drv, run_id)))["type"], "Block")
        self.assertEqual(self.verification(drv, run_id)["status"], "pending")

    def test_L5_the_first_configuration_of_an_interactive_auto_run_still_waits_on_a_human(self):
        b = _Builder()
        run = b.begin("probe", "s-l5", "Configure first.", _graph("C0", [("C0", "ordinary", "Root")], []),
                      approve=False, control_mode="auto")
        b.ask_configuration(run, "rc-l5", outcome="present")
        view = b.a.dispatch(b.drv, get_run(run_id=run))["result"]["run"]
        self.assertEqual((view["governance"]["human_wait"], view["turn_end_actor"]), (True, "human"))

    def test_L6_a_deliberative_run_keeps_asking_with_the_same_surface(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.report(drv, run_id)["reasons"][0]["parameters"]["route"], "human")
        self.assertIsNone(self.batch(drv, run_id)["prompt_error"])

    def test_L8_a_dialog_opened_before_the_upgrade_cannot_land_its_answer(self):
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "web", URL)
        self.look(drv, run_id)
        with asked_as_before():  # what the run was shown under
            self.assertEqual(self.present_now(drv, run_id)["type"], "Allow")
        with lock_only():
            refused = self.answer(drv, run_id, "p1", confirm(self.last_id(drv, run_id)))
        self.assertEqual((refused["type"], refused["reasons"][0]["code"]), ("Block", "governance.approval_unavailable"))
        self.assertEqual(self.verification(drv, run_id)["status"], "pending")

    def test_D_a_client_without_form_elicitation_has_nobody_to_ask(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.form = False  # the client declared no form: the host records exactly that
        mediator = self.mediator(drv, None)
        mediator(self.report(drv, run_id), lambda failure=None: self.fail("re-evaluated"))
        self.assertEqual(self.report(drv, run_id)["reasons"][0]["parameters"]["route"], "unavailable")
        self.assertEqual(self.run_view(drv, run_id)["invocation"]["human_surface"], "unavailable")

    def test_D_a_non_interactive_or_delegated_run_is_unavailable_even_with_a_surface(self):
        self.unavailable(interactive=False)
        self.unavailable(delegation=True)

    def test_the_host_observation_follows_the_session_at_each_report(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.batch(drv, run_id)["prompt_error"], "unobserved")
        self.mediator(drv, Human(accept(d1="later"))).observe(run_id)
        self.assertEqual(self.run_view(drv, run_id)["invocation"]["human_surface"], "human")
        self.assertIsNone(self.batch(drv, run_id)["prompt_error"])
        self.assertEqual(self.surface(drv, run_id, "human")["type"], "Inert")  # an unchanged observation

    def test_codex_projects_the_same_batch_with_nobody_to_ask(self):
        drv, run_id = self.start(host_profile_id("codex-cli"), mode="auto", audit_retrieval=[], delegation=True,
                                 interactive=False)
        self.research(drv, run_id, "web", URL)
        self.assertIsNone(self.batch(drv, run_id))  # the record is inert: nobody is asked
        with lock_only():  # and were it asked about, the route would be unavailable
            batch = self.batch(drv, run_id)
            self.assertEqual((sorted(batch), len(batch["record_ids"]), batch["prompt_error"]),
                             (["claim_ids", "key", "prompt_error", "record_ids", "records"], 1, "unavailable"))


class AutoDecisionCells(ScenarioFixture):
    """In auto an unanswered ``needs-decision`` claim is the author's, derived by fold 1 after the usual order; a human's
    answer and deliberative runs are as before (F1-F3, a-d, h of the auto-completion plan)."""

    audit = HumanSurfaceCells.audit

    def pending(self, drv, run_id) -> list[str]:
        snapshot = self.snapshot(drv, run_id)
        return evaluation.pending_decisions(snapshot, evaluation.derive_claims(snapshot))

    def state(self, drv, run_id, claim="C0") -> str:
        return self.claim(drv, run_id, claim)["state"]

    def test_F1_an_auto_decision_without_research_is_open_and_asks_nobody(self):
        drv, run_id = self.start(kind="needs-decision", mode="auto")
        self.assertEqual((self.state(drv, run_id), self.codes(self.report(drv, run_id))),
                         ("open", ["claim.research_missing"]))
        self.assertEqual((self.pending(drv, run_id), self.batch(drv, run_id)), ([], None))

    def test_F2_auto_research_approves_or_discards_a_decision(self):
        drv, run_id = self.start(kind="needs-decision", mode="auto")
        self.research(drv, run_id, "code", WORKSPACE_REF)
        self.assertEqual((self.state(drv, run_id), self.pending(drv, run_id)), ("approved", []))
        drv, run_id = self.start(kind="needs-decision", mode="auto")
        self.research(drv, run_id, "code", WORKSPACE_REF, result="refutes")
        self.assertEqual((self.state(drv, run_id), self.pending(drv, run_id)), ("discarded", []))

    def test_a_deliberative_needs_decision_with_only_research_stays_blocked(self):
        drv, run_id = self.start(kind="needs-decision")
        self.research(drv, run_id, "code", WORKSPACE_REF)
        self.assertEqual((self.state(drv, run_id), self.pending(drv, run_id), self.batch(drv, run_id)["claim_ids"]),
                         ("blocked", ["C0"], ["C0"]))

    def test_F3_a_human_answer_keeps_the_order_in_every_mode(self):
        drv, run_id = self.start(kind="needs-decision")
        self.mediate(drv, run_id, Human(accept(d1="approve")))
        self.assertEqual(self.state(drv, run_id), "approved")
        self.research(drv, run_id, "code", WORKSPACE_REF, result="refutes")
        self.assertEqual(self.state(drv, run_id), "discarded")  # a refutation beats the human's approval
        for mode, outcome, want in (("auto", "approve", "approved"), ("auto", "reject", "discarded"),
                                    ("deliberative", "reject", "discarded")):
            drv, run_id = self.start(kind="needs-decision", mode=mode)
            snapshot = self.snapshot(drv, run_id)
            with mock.patch.object(evaluation.confirmation, "decision", return_value={"outcome": outcome}):
                self.assertEqual(evaluation.claim_state_cause(snapshot, snapshot.graph["claims"][0], set())[0], want,
                                 (mode, outcome))

    def test_a_independence_never_calls_an_agent_decided_claim_human_owned(self):
        auditor, producer = {"model": "a"}, {"model": "p"}
        with mock.patch.object(evaluation, "identity_pair", side_effect=lambda x: None if x is None else x["model"]):
            self.assertEqual(evaluation._independence(auditor, [producer], True), "distinct")
            self.assertEqual(evaluation._independence(auditor, [None], True), "unverified")
            self.assertEqual(evaluation._independence(auditor, [], True), "human_owned")  # only decisions a human answered
            self.assertEqual(evaluation._independence(auditor, [producer, None], True), "unverified")  # mixed run

    def test_b_a_decision_resting_on_a_record_that_needs_a_human_is_open_and_asks_nobody(self):
        drv, run_id = self.start(kind="needs-decision", mode="auto")
        self.research(drv, run_id, "web", URL)
        self.assertEqual((self.state(drv, run_id), self.codes(self.report(drv, run_id)), self.batch(drv, run_id),
                          self.pending(drv, run_id)), ("open", ["claim.research_missing"], None, []))

    def test_c_non_gating_and_conflicted_decisions_follow_the_ordinary_rules(self):
        graph = graph_with_decision()
        graph["claims"][1]["gating"] = False
        drv, run_id = self.start(graph=graph, mode="auto")
        self.research(drv, run_id, "code", WORKSPACE_REF)
        self.assertEqual((self.codes(self.report(drv, run_id)), self.state(drv, run_id, "C1")),
                         (["audit.required"], "open"))
        drv, run_id = self.start(kind="needs-decision", mode="auto")
        self.research(drv, run_id, "code", WORKSPACE_REF)
        self.research(drv, run_id, "docs", WORKSPACE_REF, result="refutes")
        self.assertEqual((self.state(drv, run_id), self.pending(drv, run_id), self.codes(self.report(drv, run_id))),
                         ("open", [], ["evidence.conflict"]))

    def test_d_a_researched_decision_converges_in_delegated_auto_with_nobody_asked(self):
        drv, run_id = self.start(kind="needs-decision", mode="auto", delegation=True, interactive=False)
        self.research(drv, run_id, "code", WORKSPACE_REF)
        self.assertEqual(self.codes(self.report(drv, run_id)), ["audit.required"])
        self.dispatch(drv, observe_action(run_id=run_id, action=action_freeze()))
        self.assertTrue(self.audit(drv, run_id)["converged"])
        self.assertEqual(self.run_view(drv, run_id)["audit"]["independence"], "distinct")  # never human_owned
        kinds = {a["kind"] for a in drv._service._coordinator.last_snapshot.history}
        self.assertFalse(kinds & {"evidence_presentation", "evidence_confirmation"})


class InertEvidenceCells(ScenarioFixture):
    """In auto, research that needs a human neither approves a claim nor blocks it, and nobody is asked: the claim reads
    ``claim.research_missing`` (support ``unconfirmed``), and the record stays in the dossier as recorded (I1-I9)."""

    AUTO = (("interactive auto", {"mode": "auto"}),
            ("delegated auto", {"mode": "auto", "delegation": True, "interactive": False}))
    audit = HumanSurfaceCells.audit

    def state(self, drv, run_id, claim="C0") -> str:
        return self.claim(drv, run_id, claim)["state"]

    def test_I1_a_claim_resting_only_on_a_source_that_needs_a_human_is_open_and_says_what_to_do(self):
        for label, start in self.AUTO:
            with self.subTest(label):
                drv, run_id = self.start(**start)
                self.research(drv, run_id, "web", URL)
                record = self.last_id(drv, run_id)
                report = self.report(drv, run_id)
                self.assertEqual((self.state(drv, run_id), self.codes(report), self.batch(drv, run_id)),
                                 ("open", ["claim.research_missing"], None))
                self.assertEqual(report["reasons"][0]["parameters"],
                                 {"support": "unconfirmed", "record_ids": [record], "claim_id": "C0"})
                self.assertEqual(self.run_view(drv, run_id)["turn_end_actor"], "author")
                text = render_author_view(report)
                for named in ("workspace", "spike", "report_convergence intent=stop", "deliberative"):
                    self.assertIn(named, text)
                self.assertNotIn("Waiting on a human", text)

    def test_I2_another_record_approves_the_claim_and_the_inert_one_blocks_nothing(self):
        for label, start in self.AUTO:
            with self.subTest(label):
                drv, run_id = self.start(**start)
                self.research(drv, run_id, "web", URL)
                self.research(drv, run_id, "code", WORKSPACE_REF)
                self.assertEqual((self.state(drv, run_id), self.codes(self.report(drv, run_id)), self.batch(drv, run_id)),
                                 ("approved", ["audit.required"], None))
                self.dispatch(drv, observe_action(run_id=run_id, action=action_freeze()))
                self.assertEqual(self.audit(drv, run_id)["type"], "Allow")
                self.assertEqual(self.run_view(drv, run_id)["status"], "converged")

    def test_I3_an_auto_decision_resting_on_a_source_that_needs_a_human_is_open(self):
        drv, run_id = self.start(kind="needs-decision", mode="auto")
        self.research(drv, run_id, "web", URL)
        self.assertEqual((self.state(drv, run_id), self.codes(self.report(drv, run_id)), self.batch(drv, run_id)),
                         ("open", ["claim.research_missing"], None))

    def test_I4_a_refuting_source_that_needs_a_human_still_discards_the_claim(self):
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "web", URL, result="refutes")
        self.assertEqual((self.state(drv, run_id), self.codes(self.report(drv, run_id)), self.batch(drv, run_id)),
                         ("discarded", ["claim.refuted"], None))

    def test_I6_each_guard_alone_keeps_the_batch_empty_or_the_route_closed(self):
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "web", URL)
        with inert_only():  # the lock removed: nothing is in a batch, so nobody is asked
            self.assertEqual((self.batch(drv, run_id), self.codes(self.report(drv, run_id))), (None, ["claim.research_missing"]))
        with lock_only():  # the inert rule removed: the route is closed
            self.assertEqual((self.batch(drv, run_id)["prompt_error"], self.present(drv, run_id)["type"]), ("unavailable", "Block"))

    def test_I5_a_deliberative_run_still_asks_about_the_same_source(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.assertEqual((self.codes(self.report(drv, run_id)), len(self.batch(drv, run_id)["record_ids"])), ([CONFIRMATION], 1))

    def test_I7_a_stored_human_answer_still_counts_in_an_auto_run(self):
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "web", URL)
        record = self.last_id(drv, run_id)
        self.look(drv, run_id)
        with asked_as_before():  # how a 7.0.x run was asked
            self.assertEqual(self.present_now(drv, run_id)["type"], "Allow")
            self.assertEqual(self.answer(drv, run_id, "p1", confirm(record))["type"], "Allow")
        self.assertEqual((self.state(drv, run_id), self.verification(drv, run_id)["status"]),
                         ("approved", "confirmed_by_human"))
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "web", URL)
        record = self.last_id(drv, run_id)
        self.look(drv, run_id)
        with asked_as_before():
            self.present_now(drv, run_id)
            self.answer(drv, run_id, "p1", {"subject": "research", "artifact_id": record, "outcome": "reject"})
        self.assertEqual((self.state(drv, run_id), self.verification(drv, run_id)["status"]),
                         ("open", "rejected_by_human"))

    def test_I8_a_stopped_auto_run_leaves_the_claim_as_a_residual_that_says_so(self):
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "web", URL)
        stopped = self.stop(drv, run_id)["run"]
        codes = [(r["code"], r["parameters"].get("support")) for r in stopped["residuals"]]
        self.assertEqual(codes, [("claim.research_missing", "unconfirmed")])
        self.assertIsNone(stopped["confirmation_batch"])

    def test_I9_the_observer_waits_on_nobody_for_an_inert_record(self):
        from core import observer as core_observer
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "web", URL)
        snapshot = self.snapshot(drv, run_id)
        record = next(a for a in snapshot.history if a.get("kind") == "research")
        row = core_observer._record(snapshot, record, None, False)
        self.assertEqual((row["status"], row["wait"]), ("pending", None))
        self.assertIsNone(core_confirmation.wait_route(snapshot, record))

    def test_F1_the_observer_says_the_same_thing_in_one_plain_sentence_and_the_author_view_keeps_the_instruction(self):
        from core import observer as core_observer
        from application.protocol import public_contract
        reasons = public_contract()["reasons"]
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "web", URL)
        snapshot = self.snapshot(drv, run_id)
        _row, detail = core_observer.project(snapshot, reasons)
        shown = detail["claims"][0]["blocker_message"]
        self.assertEqual(shown, "Claim C0 rests only on sources a person would have to confirm; this run asks no one.")
        self.assertNotIn("report_convergence", shown)
        blocked = self.report(drv, run_id)["reasons"][0]
        self.assertIn("report_convergence intent=stop", blocked["message"])  # the author is told what to do about it
        self.assertGreater(len(blocked["message"]), core_observer.UI_MESSAGE_LIMIT)

    def test_F6_a_stopped_run_words_the_claim_as_its_mode_did(self):
        from core import observer as core_observer
        from application.protocol import public_contract
        reasons = public_contract()["reasons"]
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "web", URL)
        message = core_observer.reason_message(reasons["claim.research_missing"],
                                               {"claim_id": "C0", "support": "unconfirmed"}, ended=True)
        self.assertEqual(message, "No research that counts was recorded for claim C0 before the run stopped.")
        # a deliberative run, and any research that was never recorded, read as they always did
        self.assertEqual(core_observer.reason_message(reasons["claim.research_missing"], {"claim_id": "C0"}, ended=True),
                         "No research was recorded for claim C0 before the run stopped.")

    def test_F5_research_that_no_longer_binds_is_named_before_research_nobody_confirms(self):
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "code", WORKSPACE_REF)
        self.dispatch(drv, observe_action(run_id=run_id, action=action_graph(self.reworded_graph())))
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.codes(self.report(drv, run_id)), ["claim.research_unbound"])

    def test_F3_an_inert_record_is_in_the_dossier_but_not_among_the_producers_the_audit_classifies(self):
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "web", URL)
        inert = self.last_id(drv, run_id)
        self.research(drv, run_id, "code", WORKSPACE_REF)
        counted = self.last_id(drv, run_id)
        snapshot = self.snapshot(drv, run_id)
        claim = snapshot.graph["claims"][0]
        self.assertEqual([a["artifact_id"] for a in evaluation.active_evidence(snapshot, claim)], [inert, counted])
        self.assertEqual(evaluation.covered_artifact_ids(snapshot), [counted])
        _auditor, producers = evaluation.audit_attributions(snapshot, {"child_id": None})
        self.assertEqual(len(producers), 1)
        argument = self.argument(drv, run_id)
        self.assertEqual(argument["claims"][0]["active_evidence_ids"], [inert, counted])  # the auditor sees it as recorded
        # an inert record whose producer nobody knows leaves the classification as it was
        history = [{**a, "producer": None} if a["artifact_id"] == inert else a for a in snapshot.history]
        unknown = dataclasses.replace(snapshot, history=history)
        self.assertEqual(evaluation.audit_attributions(unknown, {"child_id": None})[1],
                         evaluation.audit_attributions(snapshot, {"child_id": None})[1])
        self.assertEqual(self.audit(drv, run_id)["type"], "Allow")  # a distinct auditor passes over the counted record
        # the observer's historical view classifies the same producers: the inert record whose producer nobody knows is not among them
        recorded = self.snapshot(drv, run_id)
        verdict = evaluation.latest_audit_verdict(recorded)
        self.assertEqual(evaluation.independence_then(recorded, verdict), "distinct")
        history = [{**a, "producer": None} if a["artifact_id"] == inert else a for a in recorded.history]
        self.assertEqual(evaluation.independence_then(dataclasses.replace(recorded, history=history), verdict), "distinct")

    @staticmethod
    def reworded_graph() -> dict:
        from assertions import canonical_graph
        graph = canonical_graph(kind="ordinary")
        graph["claims"][0]["text"] += " (reworded)"
        return graph


class BatchSizeCells(ScenarioFixture):
    """More than one receipt can carry: the core caps what is shown, in a fixed order."""

    LIMIT = 4  # the cap the cells below run under; the real one is pinned by the first test

    def setUp(self):
        super().setUp()
        real = protocol.CONTRACT_VIEW.batch_limit
        object.__setattr__(protocol.CONTRACT_VIEW, "batch_limit", self.LIMIT)  # the contract view is the cap's one owner
        self.addCleanup(lambda: object.__setattr__(protocol.CONTRACT_VIEW, "batch_limit", real))

    def test_the_limit_has_one_owner_the_request_schema(self):
        decisions = protocol.request_schema()["$defs"]["evidenceConfirmationPayload"]["properties"]["decisions"]
        self.assertEqual(protocol.EVIDENCE_BATCH_LIMIT, decisions["maxItems"])
        self.assertEqual(protocol.EVIDENCE_BATCH_LIMIT, 64)  # the real value; the cells below patch the view down
        object.__setattr__(protocol.CONTRACT_VIEW, "batch_limit", protocol.EVIDENCE_BATCH_LIMIT)
        self.assertFalse(hasattr(confirmation, "MAX_ITEMS"))

    def test_D_a_batch_over_the_limit_shows_exactly_the_cap_and_names_exactly_the_shown(self):
        drv, run_id = self.start()
        for n in range(self.LIMIT + 2):
            self.research(drv, run_id, "web", f"{URL}/{n}")
        ordered = [r["artifact_id"] for r in self.rows(drv, run_id)]
        batch = self.batch(drv, run_id)
        self.assertEqual(batch["record_ids"], ordered[:self.LIMIT])  # submission order
        every = {f"d{n}": "confirm" for n in range(1, self.LIMIT + 1)}
        shown = Human(accept(**every))
        self.mediate(drv, run_id, shown)
        self.assertEqual(len(shown.shown[0][1]["properties"]), 2 * self.LIMIT)
        stored = next(a for a in drv._service._coordinator.last_snapshot.history
                      if a.get("kind") == "evidence_presentation")
        self.assertEqual(list(stored["record_ids"]), ordered[:self.LIMIT])  # exactly what was shown
        # Confirming them changed the derivation: the remaining two are shown next.
        self.assertEqual(self.batch(drv, run_id)["record_ids"], ordered[self.LIMIT:])
        self.assertIsNone(self.batch(drv, run_id)["prompt_error"])

    def test_leaving_a_full_batch_on_later_is_spent_like_any_other(self):
        drv, run_id = self.start()
        for n in range(self.LIMIT + 2):
            self.research(drv, run_id, "web", f"{URL}/{n}")
        self.mediate(drv, run_id, Human(accept()))
        self.assertEqual(self.batch(drv, run_id)["prompt_error"], "already_presented")  # only a change asks again

    def test_decisions_come_first_when_the_cap_binds(self):
        drv, run_id = self.start(graph=graph_with_decision())
        for n in range(self.LIMIT):
            self.research(drv, run_id, "web", f"{URL}/{n}")
        batch = self.batch(drv, run_id)
        self.assertEqual((batch["claim_ids"], len(batch["record_ids"])), (["C1"], self.LIMIT - 1))

    def test_a_decline_or_cancel_that_carries_content_grants_nothing(self):
        items = [{"subject": "research", "id": "r1", "claim": "C", "source_kind": "web", "source_ref": URL,
                  "quote": "q", "class": "retrievable"}]
        for action in ("decline", "cancel"):
            self.assertIsNone(confirmation.answers_from_form({"action": action, "content": {"d1": "confirm"}}, items))
        self.assertIsNotNone(confirmation.answers_from_form({"action": "accept", "content": {"d1": "confirm"}}, items))

    def test_a_decision_answer_is_at_most_2000_characters(self):
        items = [{"subject": "claim", "id": "C1", "digest": "d", "text": "T", "title": "t", "description": "d", "note_title": "n"}]
        long_answer = [{"subject": "claim", "id": "C1", "choice": "approve", "note": "x" * 2001}]
        self.assertEqual(confirmation.decisions(items, long_answer), [])
        self.assertEqual(len(confirmation.decisions(items, [{**long_answer[0], "note": "x" * 2000}])), 1)
        _, schema = confirmation.form(items, 60,
                                      {"digest": "sha256:" + "a" * 64, "path": "/machine/x.json"})
        self.assertEqual(schema["properties"]["n1"]["maxLength"], 2000)


class RestoreCells(ScenarioFixture):
    def test_everything_the_human_gate_depends_on_survives_a_reload_and_a_restore(self):
        drv, run_id = self.start(graph=graph_with_decision())
        self.research(drv, run_id, "web", URL)
        self.research(drv, run_id, "code", WORKSPACE_REF)
        self.mediate(drv, run_id, Human(accept(d1="approve", d2="later")))
        before = self.run_view(drv, run_id)
        history_before = [(a["kind"], a["artifact_id"]) for a in drv._service._coordinator.last_snapshot.history]
        restored = drv._service.reload()
        for view in (restored.dispatch({"protocol": "empirica/v2", "request_id": "a",
                                        "command": {"type": "GetRun", "run_id": run_id}})["result"]["run"],
                     restored.dispatch({"protocol": "empirica/v2", "request_id": "b",
                                        "command": {"type": "RestoreRun", "run_id": run_id}})["result"]["run"]):
            for fact in ("confirmation_batch", "invocation", "audit"):
                self.assertEqual(view[fact], before[fact], fact)
            self.assertEqual(view["governance"]["interactions_remaining"],
                             before["governance"]["interactions_remaining"])
        argument = restored.dispatch({"protocol": "empirica/v2", "request_id": "c",
                                      "command": {"type": "GetArgument", "run_id": run_id}})["result"]["argument"]
        self.assertEqual(next(c for c in argument["claims"] if c["claim_id"] == "C1")["human_decision"]["outcome"],
                         "approve")
        self.assertIn(("evidence_presentation", next(i for k, i in history_before if k == "evidence_presentation")),
                      history_before)
        self.assertEqual(len([k for k, _ in history_before if k == "evidence_confirmation"]), 1)
        # A batch already shown is not presented again after a reload.
        drv2, run2 = self.start()
        self.research(drv2, run2, "web", URL)
        self.mediate(drv2, run2, Human(accept(d1="later")))
        shown = drv2._service.reload().dispatch({"protocol": "empirica/v2", "request_id": "d",
                                                 "command": {"type": "GetRun", "run_id": run2}})["result"]["run"]
        self.assertEqual(shown["confirmation_batch"]["prompt_error"], "already_presented")
        self.assertEqual(shown["confirmation_batch"], self.batch(drv2, run2))


class TurnEndActorCells(ScenarioFixture):
    """The run view's ``turn_end_actor`` (D2.3): who a host's turn end waits on, owned by the core. It is the actor of the
    report's first admissible requirement (``author`` for the agent's work, ``auditor`` while an audit child runs, ``human``),
    and ``none`` when nothing is owed, when the run has ended, and when the root claim is refuted or conflicted (no further
    work can approve it) unless a human is being asked."""

    MODES = ("deliberative", "auto")

    def actor(self, drv, run_id, ruled_out=False) -> str:
        """The core's value, which the fixtures' own derivation (from a view's reasons, obligations and governance, as the
        fixture generators set it) must agree with wherever the view states what is owed, from the run view and from a real
        ``Evaluate`` result (its reasons are the derivation's first branch); a view cannot show a ruled-out root."""
        view = self.run_view(drv, run_id)
        report = self.report(drv, run_id)
        if not ruled_out and any(row["status"] != "satisfied" for row in view["obligations"]["active"]):
            self.assertEqual(regen_contract_fixtures.turn_end_actor_of({"run": view}), view["turn_end_actor"])
        if not ruled_out:
            self.assertEqual(regen_contract_fixtures.turn_end_actor_of(report), report["run"]["turn_end_actor"])
        return view["turn_end_actor"]

    def test_author_while_the_agent_owes_research(self):
        for mode in self.MODES:
            drv, run_id = self.start(mode=mode)
            self.assertEqual(self.actor(drv, run_id), "author", mode)
            self.assertEqual(self.codes(self.report(drv, run_id)), ["claim.research_missing"])

    def test_author_before_its_configuration_is_approved(self):
        drv = self.bind_driver("ADR-0073", "turn-end", "setup")
        run_id = self.start_run(drv, goal="Nothing approved yet.", control_mode="deliberative",
                                invocation={**TEST_INVOCATION, "human_surface": "human"})
        self.assertEqual(self.actor(drv, run_id), "author")

    def test_human_while_a_confirmation_waits_on_a_human(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.actor(drv, run_id), "human")

    def test_author_in_auto_while_a_source_needs_a_human_who_is_not_asked(self):
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.actor(drv, run_id), "author")

    def test_the_auditor_while_an_audit_child_runs_and_the_author_before_it_is_launched(self):
        for mode in self.MODES:
            drv, run_id = self.start(mode=mode)
            self.research(drv, run_id, "code", WORKSPACE_REF)
            self.assertEqual((self.codes(self.report(drv, run_id)), self.actor(drv, run_id)),
                             (["audit.required"], "author"), mode)  # the author launches it
            self.require_pending_audit_child(drv, run_id)
            self.assertEqual(self.actor(drv, run_id), "auditor", mode)  # the turn settles; the child's result continues

    def test_human_while_its_configuration_waits_on_a_human(self):
        for mode in self.MODES:  # deliberative, and interactive auto (nobody delegated its approval)
            b = _Builder()
            run = b.begin("probe", f"s-{mode}", "Configure first.", _graph("C0", [("C0", "ordinary", "Root")], []),
                          approve=False, control_mode=mode)

            def view() -> dict:
                return b.a.dispatch(b.drv, get_run(run_id=run))["result"]["run"]

            self.assertEqual((view()["governance"]["human_wait"], view()["turn_end_actor"]), (False, "author"), mode)
            self.assertEqual(self.actor(b.drv, run), "author", mode)  # the view and an evaluation agree with the core
            b.ask_configuration(run, f"rc-{mode}", outcome="present")
            self.assertEqual((view()["governance"]["human_wait"], view()["turn_end_actor"]), (True, "human"), mode)
            self.assertEqual(self.actor(b.drv, run), "human", mode)

    def test_a_delegated_auto_run_never_waits_on_a_human(self):
        """Nobody can be asked in delegated auto, so no state it reaches has ``turn_end_actor`` ``human``: its
        configuration is approved by the host's policy, a source that needs a human is inert, and a stop needs no consent."""
        seen = set()
        delegated = dict(mode="auto", delegation=True, interactive=False)
        drv, run_id = self.start(**delegated)
        seen.add(self.actor(drv, run_id))  # researching
        self.research(drv, run_id, "web", URL)  # a source that would need a human in a deliberative run
        self.assertNotIn("evidence.confirmation_required", self.codes(self.report(drv, run_id)))
        seen.add(self.actor(drv, run_id))
        self.research(drv, run_id, "code", WORKSPACE_REF)
        seen.add(self.actor(drv, run_id))  # the audit is the author's to launch
        child = self.require_pending_audit_child(drv, run_id)
        seen.add(self.actor(drv, run_id))  # a child is running
        self.require_trusted_audit_attribution(drv, run_id, child, variant="distinct")
        drv.trusted_audit_verdict(run_id, child, self.build_audit_verdict_payload(drv, run_id, verdict="pass", scope_review="pass"))
        seen.add(self.actor(drv, run_id))  # nothing is owed
        drv, run_id = self.start(**delegated)
        self.research(drv, run_id, "code", WORKSPACE_REF, result="refutes")
        seen.add(self.actor(drv, run_id, ruled_out=True))  # a refuted root
        self.assertEqual(self.codes(self.stop(drv, run_id)), [])
        seen.add(self.actor(drv, run_id))  # ended without a consent to ask
        pending = _Builder()  # before its configuration is approved: the host's policy does that, no human is asked
        run = pending.begin("probe", "s-delegated", "Configure first.", _graph("C0", [("C0", "ordinary", "Root")], []),
                            approve=False, control_mode="auto",
                            invocation=invocation_for(PROFILE, delegation=True, interactive=False, human_surface="unavailable",
                                                      signal="v2 harness"))
        view = pending.a.dispatch(pending.drv, get_run(run_id=run))["result"]["run"]
        self.assertEqual((view["governance"]["state"], view["governance"]["human_wait"], view["turn_end_actor"]),
                         ("pending", False, "author"))
        seen.add(view["turn_end_actor"])
        self.assertEqual(seen, {"author", "auditor", "none"})  # the states were reached, and none of them is the human's

    def test_an_auto_run_never_reports_the_evidence_confirmation_the_derivation_maps_to_a_human(self):
        """``turn_end_actor_of`` maps ``evidence.confirmation_required`` and the terminal consent to ``human``, as the core
        does in deliberative. The core emits neither in auto, so the derivation refuses an auto view that carries one: a
        fixture that did would be a state no run reaches."""
        drv, run_id = self.start(mode="auto")
        self.research(drv, run_id, "web", URL)
        report = self.report(drv, run_id)
        self.assertEqual((self.codes(report), self.actor(drv, run_id)), (["claim.research_missing"], "author"))
        self.assertEqual(self.codes(self.stop(drv, run_id)), [])  # the stop asks no consent either
        for code in ("evidence.confirmation_required", "governance.terminal_stop_pending"):
            view = {**report, "reasons": [{**report["reasons"][0], "code": code, "parameters": {}}]}
            with self.assertRaises(ValueError, msg=code):
                regen_contract_fixtures.turn_end_actor_of(view)
            deliberative = {**view, "run": {**view["run"], "governance": {**view["run"]["governance"], "control_mode": "deliberative"}}}
            self.assertEqual(regen_contract_fixtures.turn_end_actor_of(deliberative), "human", code)

    def test_none_when_nothing_is_owed(self):
        for mode in self.MODES:
            drv, run_id = self.start(mode=mode)
            self.research(drv, run_id, "code", WORKSPACE_REF)
            child = self.require_pending_audit_child(drv, run_id)
            self.require_trusted_audit_attribution(drv, run_id, child, variant="distinct")
            payload = self.build_audit_verdict_payload(drv, run_id, verdict="pass", scope_review="pass")
            drv.trusted_audit_verdict(run_id, child, payload)
            view = self.run_view(drv, run_id)
            self.assertEqual((view["status"], view["turn_end_actor"]), ("active", "none"), mode)

    def test_none_for_a_refuted_root(self):
        for mode in self.MODES:
            drv, run_id = self.start(mode=mode)
            self.research(drv, run_id, "code", WORKSPACE_REF, result="refutes")
            self.assertEqual(self.codes(self.report(drv, run_id)), ["claim.refuted"])
            self.assertEqual(self.actor(drv, run_id, ruled_out=True), "none", mode)

    def test_none_for_a_conflicted_root(self):
        for mode in self.MODES:
            drv, run_id = self.start(mode=mode)
            self.research(drv, run_id, "code", WORKSPACE_REF)
            self.research(drv, run_id, "code", WORKSPACE_REF, result="refutes")
            self.assertEqual(self.codes(self.report(drv, run_id)), ["evidence.conflict"])
            self.assertEqual(self.actor(drv, run_id, ruled_out=True), "none", mode)

    def test_a_refuted_leaf_is_not_a_refuted_root(self):
        drv, run_id = self.start(graph=self.chain())
        self.research(drv, run_id, "code", WORKSPACE_REF, claim="C1", result="refutes")
        self.assertEqual(self.actor(drv, run_id), "author")

    def test_a_ruled_out_root_does_not_hide_a_human_who_is_being_asked(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "code", WORKSPACE_REF, result="refutes")
        snapshot = self.snapshot(drv, run_id)
        derivation = evaluation.derive_claims(snapshot)
        asked = evaluation.Requirement("terminal", "t", "t", None, {}, (), None, "human", "next_report", True)
        held = evaluation.Requirement("claim", "c", "c", None, {}, (), None, "agent", "next_turn", True)
        self.assertEqual(evaluation.turn_end_actor(snapshot, derivation, (asked,)), "human")
        self.assertEqual(evaluation.turn_end_actor(snapshot, derivation, (held,)), "none")

    def test_none_for_a_terminal_run(self):
        for mode in self.MODES:
            drv, run_id = self.start(mode=mode)
            self.assertEqual(self.stop(drv, run_id)["run"]["turn_end_actor"], "none", mode)
            self.assertEqual(self.actor(drv, run_id), "none", mode)
