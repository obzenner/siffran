"""ADR-0063 amendment (2026-10-08), EHB-TERM-01…04: a deliberative run does not end without converging until its human
approves; the auto modes are unchanged. The consent is the ADR-0067 confirmation batch with one item (the status), asked
by the evaluation that reaches the terminal step, and nothing but an accepted receipt produces a terminal state."""
from __future__ import annotations

from assertions import evaluate
from test_evidence_confirmation import graph_with_decision
from core.evaluation import TERMINAL_STATUSES
from driver import CITED_SOURCE
from test_evidence_hosts import Human, MediatorFixture, accept

from adapters.public_tools import PublicTools
from application.protocol import host_profile_id

CLAUDE = host_profile_id("claude-code")
ASK = "governance.terminal_stop_pending"


class TerminalConsentTests(MediatorFixture):
    def tools(self, drv, human):
        return PublicTools(CLAUDE, dispatch=lambda req, _p: drv.request(req), confirm=self.mediator(drv, human))

    def end(self, drv, run_id, human, intent="stop") -> dict:
        return self.tools(drv, human).call_internal("report_convergence", {"run_id": run_id, "intent": intent})["structuredContent"]

    def status(self, drv, run_id) -> str:
        return self.run_view(drv, run_id)["status"]

    # ---- EHB-TERM-01: accept -------------------------------------------------------------------------

    def test_EHB_TERM_01_a_deliberative_stop_commits_only_after_the_human_approves(self):
        drv, run_id = self.start(kind="ordinary")
        human = Human(accept(d1="approve"))
        result = self.end(drv, run_id, human)
        self.assertEqual(result["run"]["status"], "stopped_residual")
        self.assertEqual(len(human.shown), 1)
        self.assertIn("End this run as stopped_residual", human.shown[0][0])
        self.assertEqual(self.status(drv, run_id), "stopped_residual")

    def test_EHB_TERM_01_the_item_is_the_status_and_binds_the_derivation(self):
        drv, run_id = self.start(kind="ordinary")
        batch = self.looked_batch(drv, run_id)
        self.assertEqual(batch["claim_ids"], [], "the status is not a claim of the argument (HQ-G9-02)")
        self.assertIn(batch["terminal"]["status"], TERMINAL_STATUSES)
        self.assertEqual(batch["record_ids"], [])
        self.research(drv, run_id, "code", CITED_SOURCE[0])  # the argument changed: a new question, a new digest
        again = self.looked_batch(drv, run_id)
        self.assertNotEqual(again["terminal"]["digest"], batch["terminal"]["digest"])
        self.assertNotEqual(again["key"], batch["key"])

    def test_EHB_TERM_01_an_approval_of_an_older_derivation_grants_nothing_now(self):
        drv, run_id = self.start(kind="ordinary")
        batch = self.looked_batch(drv, run_id)
        shown = self.present_now(drv, run_id, "t1", batch["key"])
        self.assertEqual(shown["type"], "Allow")
        self.research(drv, run_id, "code", CITED_SOURCE[0])  # the run moved on before the answer arrived
        answered = self.answer(drv, run_id, "t1", self.approve(batch))
        self.assertEqual(answered["type"], "Fault", "a receipt for a batch that is no longer current is a conflict")
        self.assertEqual(self.status(drv, run_id), "active")

    def test_the_author_view_asks_to_end_the_run_and_names_no_claim(self):
        """HQ-G9-02: the view says what is asked (end the run as <status>), never "decision needed on claim <status>"."""
        from adapters.author_view import render_author_view
        drv, run_id = self.start(kind="ordinary")
        self.look(drv, run_id)
        result = self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))["result"]
        view = render_author_view(result)
        status = result["run"]["confirmation_batch"]["terminal"]["status"]
        self.assertIn(f"decision needed to end the run as {status}", view)
        self.assertNotIn("decision needed on claim", view)

    def test_the_message_is_true_for_every_route(self):
        """HQ-G9-01: the same text is read by a host with a dialog and by one without; it must not promise a question
        where none can be asked, and the route it depends on is rendered with it (the parameters line)."""
        from adapters.author_view import render_author_view
        for route, surface in (("human", "human"), ("unavailable", "unavailable"), ("unobserved", None)):
            with self.subTest(route=route):
                drv, run_id = self.start(kind="ordinary")
                if surface:
                    self.surface(drv, run_id, surface)
                result = self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))["result"]
                self.assertEqual(result["reasons"][0]["parameters"]["route"], route)
                view = render_author_view(result)
                message = result["reasons"][0]["message"]
                self.assertIn(f"route={route}", view)
                self.assertIn(f"route {route}:", message, "each route has its own sentence in the message")
                actions = result["reasons"][0]["next_actions"]
                if route == "unavailable":
                    self.assertNotIn("report_convergence intent=report_convergence", view.split("Open obligations")[0])
                    self.assertIn("only a host that can show a dialog can end it", message)
                    self.assertNotIn("convergence.report", actions)
                else:
                    self.assertIn("convergence.report", actions)

    def test_a_terminal_item_the_caller_supplies_is_shown_only_as_the_core_derived_it(self):
        """HQ-G9-05: ``present`` renders a terminal item only when it is the core's own: status, digest and key agree, and the
        core admits the key. A forged status or digest is never rendered, and nothing is recorded as shown."""
        from adapters import confirmation
        from core.evaluation import terminal_key
        drv, run_id = self.start(kind="ordinary")
        real = self.looked_batch(drv, run_id)
        forged = {"status": "stopped_frozen", "digest": "sha256:" + "0" * 64}
        for label, batch in (
                ("status", {**real, "terminal": {**real["terminal"], "status": "stopped_frozen"}}),
                ("digest", {**real, "terminal": {**real["terminal"], "digest": forged["digest"]}}),
                ("not a status", {**real, "terminal": {**real["terminal"], "status": "converged"}}),
                ("consistent, not the core's", {**real, "terminal": forged, "key": terminal_key(forged["status"], forged["digest"])})):
            with self.subTest(forged=label):
                recorded = []
                shown = confirmation.present(
                    CLAUDE, run_id, dispatch=lambda req, _p: drv.request(req), terminal=batch,
                    ingress=lambda _p, run, payload: recorded.append(payload) or drv.trusted_evidence_presentation(run, payload))
                self.assertIs(shown, confirmation.PRESENTATION_FAILED)
                if label != "consistent, not the core's":
                    self.assertEqual(recorded, [], "an inconsistent item never reaches the core")
                self.assertEqual(len([a for a in self.snapshot(drv, run_id).history
                                      if a.get("kind") == "evidence_presentation"]), 0)
        again = confirmation.present(CLAUDE, run_id, dispatch=lambda req, _p: drv.request(req), terminal=real,
                                     ingress=lambda _p, run, payload: drv.trusted_evidence_presentation(run, payload))
        self.assertNotIsInstance(again, confirmation.Outcome, "the core's own item is shown")
        self.assertIn(real["terminal"]["status"], again[1][0]["text"])

    # ---- EHB-TERM-02: anything but an accepted approval ----------------------------------------------

    def test_EHB_TERM_02_decline_cancel_and_timeout_grant_nothing(self):
        for label, reply in (("decline", {"action": "decline"}), ("cancel", {"action": "cancel"}), ("timeout", None),
                             ("later", accept(d1="later")), ("reject", accept(d1="reject"))):
            with self.subTest(answer=label):
                drv, run_id = self.start(kind="ordinary")
                human = Human(reply)
                result = self.end(drv, run_id, human)
                self.assertEqual(len(human.shown), 1)
                self.assertEqual(self.codes(result), [ASK])
                self.assertEqual(result["run"]["status"], "active")
                self.assertEqual(self.status(drv, run_id), "active")

    def test_EHB_TERM_02_a_spent_question_is_not_asked_again_for_the_same_derivation(self):
        drv, run_id = self.start(kind="ordinary")
        human = Human({"action": "decline"})
        self.end(drv, run_id, human)
        again = self.end(drv, run_id, human)
        self.assertEqual(len(human.shown), 1)
        self.assertEqual(self.codes(again), [ASK])
        self.assertEqual(again["run"]["confirmation_batch"]["prompt_error"], "already_presented")

    # ---- EHB-TERM-03: no dialog ----------------------------------------------------------------------

    def test_EHB_TERM_03_without_a_dialog_the_step_stays_blocked(self):
        drv, run_id = self.start(kind="ordinary")
        tools = PublicTools(CLAUDE, dispatch=lambda req, _p: drv.request(req), confirm=self.mediator_without_form(drv))
        result = tools.call_internal("report_convergence", {"run_id": run_id, "intent": "stop"})["structuredContent"]
        self.assertEqual(self.codes(result), [ASK])
        self.assertEqual(result["reasons"][0]["parameters"]["route"], "unavailable")
        self.assertEqual(self.status(drv, run_id), "active")

    # ---- EHB-TERM-04: auto modes unchanged -------------------------------------------------------------

    def test_EHB_TERM_04_every_auto_mode_ends_without_a_question(self):
        for label, extra in (("interactive auto", {}), ("delegated auto", {"delegation": True})):
            with self.subTest(mode=label):
                drv, run_id = self.start(kind="ordinary", mode="auto", **extra)
                human = Human(accept(d1="approve"))
                result = self.end(drv, run_id, human)
                self.assertEqual(result["type"], "Allow")
                self.assertEqual(result["run"]["status"], "stopped_residual")
                self.assertEqual(human.shown, [], "an auto run is never asked")

    # ---- the consent is not evidence ---------------------------------------------------------------------

    def test_a_claim_named_like_a_status_keeps_its_approval_in_the_dossier(self):
        """HQ-G9-03: a consent is recognised by what it binds (the terminal digest), not by the name of its id. A claim may be
        called ``stopped_budget``; the human's approval of that claim is evidence of the argument and stays in the dossier."""
        from core.evaluation import dossier_artifact_ids
        graph = graph_with_decision()
        graph["claims"][1]["id"] = "stopped_budget"
        graph["edges"] = [{"from": "C0", "to": "stopped_budget", "type": "SupportedBy"}]
        drv, run_id = self.start(graph=graph)
        self.look(drv, run_id)
        batch = self.run_view(drv, run_id)["confirmation_batch"]
        self.assertEqual(batch["claim_ids"], ["stopped_budget"])
        self.assertEqual(self.present_now(drv, run_id, "d1", batch["key"])["type"], "Allow")
        digest = self.argument(drv, run_id)["claims"][1]["wording_digest"]
        answered = self.answer(drv, run_id, "d1", {"subject": "claim", "claim_id": "stopped_budget",
                                                   "claim_digest": digest, "outcome": "approve"})
        self.assertEqual(answered["type"], "Allow")
        kept = [a["artifact_id"] for a in self.snapshot(drv, run_id).history
                if a.get("kind") == "evidence_confirmation" and a.get("claim_id") == "stopped_budget"]
        self.assertEqual(len(kept), 1)
        self.assertIn(kept[0], dossier_artifact_ids(self.snapshot(drv, run_id).history))

    def test_a_consent_leaves_the_dossier_unchanged(self):
        """The approval is recorded after the audit the end depends on; it is not part of the dossier that audit bound."""
        from core.evaluation import dossier_artifact_ids
        drv, run_id = self.start(kind="ordinary")
        before = dossier_artifact_ids(self.snapshot(drv, run_id).history)
        self.end(drv, run_id, Human(accept(d1="approve")))
        self.assertEqual(dossier_artifact_ids(self.snapshot(drv, run_id).history), before)

    # ---- helpers -------------------------------------------------------------------------------------------

    def looked_batch(self, drv, run_id) -> dict:
        """The terminal batch a stop returns, as the host in hand sees it."""
        self.look(drv, run_id)
        result = self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))["result"]
        self.assertEqual(self.codes(result), [ASK])
        return result["run"]["confirmation_batch"]

    @staticmethod
    def approve(batch: dict) -> dict:
        return {"subject": "claim", "claim_id": batch["terminal"]["status"],
                "claim_digest": batch["terminal"]["digest"], "outcome": "approve"}

    def mediator_without_form(self, drv):
        from adapters.confirmation import HostConfirmation
        return HostConfirmation(
            CLAUDE, elicit=None, dispatch=lambda req, _p: drv.request(req),
            presentation_ingress=lambda _p, run, payload: drv.trusted_evidence_presentation(run, payload),
            confirmation_ingress=lambda _p, run, payload: drv.trusted_evidence_confirmation(run, payload),
            surface_ingress=lambda _p, run, payload: drv.trusted_human_surface(run, payload["surface"]))


if __name__ == "__main__":
    import unittest
    unittest.main()
