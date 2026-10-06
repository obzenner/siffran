"""ADR-0067 scenario matrix (R2 fix round): what the human is asked, when, and what the run says after.

One test per collapsed cell of the matrix in the ADR's "Scenarios" table. A cell marked [D] was wrong
before the R2 fix; its test is in the mutation table of the build progress log, which restores the old rule
and shows the test fail. The host is the shared Claude mediator with a scripted human; the Claude Stop and
Pi gate rules over the same facts are tested in their own suites."""
from __future__ import annotations

from adapters import confirmation
from application import protocol
from assertions import action_freeze, evaluate, observe_action
from test_evidence_confirmation import URL, WORKSPACE_REF, graph_with_decision
from test_evidence_hosts import Human, MediatorFixture, accept

CONFIRMATION = "evidence.confirmation_required"


class ScenarioFixture(MediatorFixture):
    def two_claims(self):
        """C0 supported by C1, both ordinary; the caller gives research to whichever it wants."""
        return self.start(graph=self.chain())

    @staticmethod
    def chain() -> dict:
        from assertions import canonical_graph
        return canonical_graph(n_claims=2, kind="ordinary")

    def stop(self, drv, run_id) -> dict:
        return self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))["result"]


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
        batch = self.batch(drv, run_id)
        self.assertEqual({k: batch[k] for k in ("record_ids", "claim_ids", "prompt_error")},
                         {"record_ids": [record], "claim_ids": [], "prompt_error": None})
        reason = self.report(drv, run_id)["reasons"][0]
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

    def test_D_later_cancel_and_timeout_are_spent_and_a_changed_derivation_asks_again(self):
        for label, reply in (("later", accept(d1="later")), ("cancel", {"action": "cancel"}), ("timeout", None)):
            with self.subTest(label):
                drv, run_id = self.start()
                self.research(drv, run_id, "web", URL)
                self.mediate(drv, run_id, Human(reply))
                self.assertEqual(self.batch(drv, run_id)["prompt_error"], "already_presented")
                self.assertEqual(self.codes(self.report(drv, run_id)), [CONFIRMATION])  # still the reason
                again = Human(accept(d1="confirm"))
                self.mediate(drv, run_id, again)
                self.assertEqual(again.shown, [])  # not shown again
                self.research(drv, run_id, "web", URL + "/changed")  # a changed derivation
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
        batch = self.batch(drv, run_id)
        self.assertEqual((batch["record_ids"], batch["claim_ids"], batch["prompt_error"]), ([], ["C0"], None))
        reason = self.report(drv, run_id)["reasons"][0]
        self.assertEqual((reason["code"], reason["parameters"]["claim_ids"], reason["next_actions"]),
                         (CONFIRMATION, ["C0"], ["human.confirm_evidence", "residual.accept"]))
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

    def test_D_a_client_without_form_elicitation_has_nobody_to_ask(self):
        drv, run_id = self.start(human_surface="unavailable")
        self.research(drv, run_id, "web", URL)
        mediator = self.mediator(drv, None)  # no form: the host records exactly that
        mediator(self.report(drv, run_id), lambda: self.fail("re-evaluated"))
        self.assertEqual(self.report(drv, run_id)["reasons"][0]["parameters"]["route"], "unavailable")
        self.assertEqual(self.run_view(drv, run_id)["invocation"]["human_surface"], "unavailable")

    def test_D_a_non_interactive_or_delegated_run_is_unavailable_even_with_a_surface(self):
        self.unavailable(interactive=False)
        self.unavailable(delegation=True)

    def test_the_host_observation_follows_the_session_at_each_report(self):
        drv, run_id = self.start(human_surface="unavailable")
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.batch(drv, run_id)["prompt_error"], "unavailable")
        self.mediator(drv, Human(accept(d1="later"))).observe(run_id)
        self.assertEqual(self.run_view(drv, run_id)["invocation"]["human_surface"], "human")
        self.assertIsNone(self.batch(drv, run_id)["prompt_error"])
        self.assertEqual(self.surface(drv, run_id, "human")["type"], "Inert")  # an unchanged observation

    def test_codex_projects_the_same_batch_with_nobody_to_ask(self):
        drv, run_id = self.start("codex-cli@0.146.0", mode="auto", audit_retrieval=[], delegation=True,
                                 interactive=False)
        self.research(drv, run_id, "web", URL)
        batch = self.batch(drv, run_id)
        self.assertEqual((sorted(batch), len(batch["record_ids"]), batch["prompt_error"]),
                         (["claim_ids", "key", "prompt_error", "record_ids", "records"], 1, "unavailable"))


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
        items = [{"subject": "claim", "id": "C1", "digest": "d", "text": "T"}]
        long_answer = [{"subject": "claim", "id": "C1", "choice": "approve", "note": "x" * 2001}]
        self.assertEqual(confirmation.decisions(items, long_answer), [])
        self.assertEqual(len(confirmation.decisions(items, [{**long_answer[0], "note": "x" * 2000}])), 1)
        _, schema = confirmation.form(items, 60)
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
