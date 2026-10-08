"""ADR-0067 Slice 1: verification classes, where a record waits, the presentation that bounds the human
batch, and the human's receipt.

The class is derived from ``(source_kind, source_ref)``. The route of a record that is not verbatim
is derived from the auditor retrieval recorded at ``StartRun`` and from whether the run has a human
ingress. Neither is stored; both are checked here through the public views. Each rule below has a test
that fails when the rule is removed (the mutation table is in the build progress log)."""
from __future__ import annotations

from unittest import mock

from assertions import (
    ConformanceCase, action_freeze, action_graph, action_investigate, action_research, action_route,
    canonical_graph, evaluate, get_argument, get_run, observe_action,
)
from core import governance as core_governance
from core.evaluation import dossier_artifact_ids, evidence_digest
from driver import CITED_SOURCE
from governance_setup import TEST_INVOCATION
from application.protocol import host_profile_id  # noqa: E402

URL = "https://example.test/spec"
ABSOLUTE = "/opt/vendor/lib/notes.md"
NO_REF = "the deploy pipeline output"
WORKSPACE_REF = CITED_SOURCE[0] + ":3"


def receipt_of(run_id: str, receipt: str, *decisions: dict) -> dict:
    return {"run_id": run_id, "receipt_id": receipt, "decisions": list(decisions)}


def confirm(record: str) -> dict:
    return {"subject": "research", "artifact_id": record, "outcome": "confirm"}


def reject(record: str, **extra) -> dict:
    return {"subject": "research", "artifact_id": record, "outcome": "reject", **extra}


def graph_with_decision() -> dict:
    """An ordinary root C0 supported by a needs-decision claim C1."""
    graph = canonical_graph(n_claims=2, kind="ordinary")
    graph["claims"][1]["kind"] = "needs-decision"
    return graph


class EvidenceFixture(ConformanceCase):
    """Starting a run, recording research and reading the dossier; shared by the host-mediator tests.

    ``SURFACE_AT_START`` is what the host has observed when the run starts, and the order of events each host really
    follows. Pi observes ``ctx.hasUI`` at start, so a test that calls the core's trusted ingresses directly (a host
    that already has its surface) starts ``human``. Claude has not looked at start (``unobserved``) and records the
    surface only inside ``report_convergence``: ``MediatorFixture`` and ``test_host_faithful`` start that way and
    observe exactly where Claude does."""

    SURFACE_AT_START = "human"

    def start(self, profile: str | None = None, *, graph: dict | None = None, kind: str = "ordinary",
              mode: str = "deliberative", **invocation):
        drv = self.bind_driver("ADR-0067", "slice-1", "verification classes", profile_id=profile)
        run_id = self.start_run(drv, goal="Confirm what the service cannot verify.",
                                control_mode=mode, invocation={**TEST_INVOCATION, "human_surface": self.SURFACE_AT_START, **invocation})
        self.dispatch(drv, observe_action(run_id=run_id, action=action_route(reason="route")))
        self.dispatch(drv, observe_action(
            run_id=run_id, action=action_graph(graph or canonical_graph(kind=kind))))
        drv.approve_governance(run_id)
        self.dispatch(drv, observe_action(run_id=run_id, action=action_investigate()))
        return drv, run_id

    def research(self, drv, run_id, source_kind, source_ref, claim="C0", result="supports", **extra):
        payload = {"source_ref": source_ref, "citation": CITED_SOURCE[1], **extra}
        return self.dispatch(drv, observe_action(run_id=run_id, action=action_research(
            claim_id=claim, source_kind=source_kind, result=result, payload=payload)))["result"]

    def argument(self, drv, run_id) -> dict:
        return self.dispatch(drv, get_argument(run_id=run_id))["result"]["argument"]

    def rows(self, drv, run_id) -> list[dict]:
        return [a for a in self.argument(drv, run_id)["artifacts"] if a["kind"] == "research"]

    def last_id(self, drv, run_id) -> str:
        return self.rows(drv, run_id)[-1]["artifact_id"]

    def verification(self, drv, run_id, index: int = -1) -> dict:
        return self.rows(drv, run_id)[index]["verification"]

    def run_view(self, drv, run_id) -> dict:
        return self.dispatch(drv, get_run(run_id=run_id))["result"]["run"]

    def routes(self, drv, run_id) -> list[str]:
        """Where each waiting record goes, as the run view states it."""
        return [row["confirm"] for o in self.run_view(drv, run_id)["obligations"]["active"]
                for row in o["observed"] if row.get("confirm")]

    def report(self, drv, run_id, host_failure: str | None = None) -> dict:
        return self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence", host_failure=host_failure))["result"]

    def codes(self, result: dict) -> list[str]:
        return [r["code"] for r in result.get("reasons", [])]

    def claim(self, drv, run_id, claim_id="C0") -> dict:
        return next(c for c in self.argument(drv, run_id)["claims"] if c["claim_id"] == claim_id)

    def present_now(self, drv, run_id, receipt="p1", key=None) -> dict:
        """The core's presentation ingress, with no host step before it: the batch the run shows now, under the key it
        was shown under. A host fixture overrides ``present`` to add the host's own step first."""
        shown = key or (self.run_view(drv, run_id)["confirmation_batch"] or {}).get("key")
        return drv.trusted_evidence_presentation(
            run_id, {"run_id": run_id, "receipt_id": receipt, "key": shown})["result"]

    present = present_now

    def answer(self, drv, run_id, receipt, *decisions) -> dict:
        return drv.trusted_evidence_confirmation(run_id, receipt_of(run_id, receipt, *decisions))["result"]

    def interactions(self, drv, run_id) -> dict:
        return self.run_view(drv, run_id)["governance"]["interactions_remaining"]

    def snapshot(self, drv, run_id):
        self.run_view(drv, run_id)
        return drv._service._coordinator.last_snapshot


class EvidenceConfirmationTests(EvidenceFixture):
    # ---- the class ------------------------------------------------------------------------

    def test_class_follows_the_ref_and_not_the_kind(self):
        drv, run_id = self.start()
        self.assertEqual(self.research(drv, run_id, "code", WORKSPACE_REF)["type"], "Allow")
        self.assertEqual(self.verification(drv, run_id)["class"], "verbatim")
        self.assertEqual(self.research(drv, run_id, "code", URL)["type"], "Allow")  # a URL is a URL
        self.assertEqual(self.verification(drv, run_id)["class"], "retrievable")

    def test_a_url_scheme_is_matched_without_regard_to_case(self):
        drv, run_id = self.start()
        self.assertEqual(self.research(drv, run_id, "web", "HTTPS://example.test/Spec")["type"], "Allow")
        self.assertEqual(self.verification(drv, run_id)["class"], "retrievable")

    def test_a_ref_of_no_accepted_form_is_refused_unless_runtime(self):
        drv, run_id = self.start()
        for kind in ("code", "docs", "web"):
            with self.subTest(kind=kind):
                refusal = self.research(drv, run_id, kind, NO_REF)
                self.assertEqual(refusal["type"], "Block")
                self.assertEqual(refusal["reasons"][0]["code"], "research.citation_unverified")
                self.assertEqual(refusal["reasons"][0]["parameters"]["problem"], "source_ref_form")

    def test_an_absolute_path_inside_the_workspace_must_be_cited_relative(self):
        drv, run_id = self.start()
        refusal = self.research(drv, run_id, "code", "/srv/work/" + CITED_SOURCE[0])
        self.assertEqual(refusal["reasons"][0]["parameters"]["problem"], "source_ref_form")
        self.assertIn("repository-relative path", refusal["reasons"][0]["message"])
        self.assertEqual(self.research(drv, run_id, "code", "/srv/elsewhere/notes.md")["type"], "Allow")

    def test_observed_record_must_say_why_no_spike_can_establish_it(self):
        drv, run_id = self.start()
        refusal = self.research(drv, run_id, "runtime", NO_REF)
        self.assertEqual(refusal["reasons"][0]["parameters"]["problem"], "spike_reason_missing")
        self.assertEqual(self.research(drv, run_id, "runtime", NO_REF, spike_unavailable="  ")["type"],
                         "Block")
        admitted = self.research(drv, run_id, "runtime", NO_REF,
                                 spike_unavailable="Needs production credentials.")
        self.assertEqual(admitted["type"], "Allow")
        row = self.rows(drv, run_id)[-1]
        self.assertEqual(row["spike_unavailable"], "Needs production credentials.")
        self.assertEqual(row["verification"]["class"], "observed")

    # ---- the route ------------------------------------------------------------------------

    def test_route_is_derived_from_recorded_retrieval_and_human_capability(self):
        drv, run_id = self.start()  # auditor reads local + workspace; the human ingress exists
        self.research(drv, run_id, "docs", ABSOLUTE)
        local = self.verification(drv, run_id)
        self.assertEqual((local["class"], local["status"], local["retrieve"]),
                         ("retrievable", "pending", True))
        self.assertEqual(self.routes(drv, run_id), ["audit"])
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.verification(drv, run_id)["retrieve"], False)  # the auditor cannot read it
        self.research(drv, run_id, "runtime", NO_REF, spike_unavailable="No harness.")
        self.assertEqual(self.verification(drv, run_id),
                         {"class": "observed", "status": "pending", "retrieve": False})
        self.assertEqual(self.routes(drv, run_id), ["audit", "human", "human"])

    def test_a_web_record_goes_to_the_audit_when_the_auditor_can_read_the_web(self):
        drv, run_id = self.start(audit_retrieval=["local", "web", "workspace"])
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.routes(drv, run_id), ["audit"])
        self.assertNotIn("evidence.confirmation_required", self.codes(self.report(drv, run_id)))

    def test_without_a_human_ingress_a_record_is_unavailable_from_the_moment_it_is_recorded(self):
        drv, run_id = self.start(host_profile_id("codex-cli"), mode="auto", audit_retrieval=[],
                                 delegation=True, interactive=False)
        for kind, ref, extra in (("web", URL, {}), ("runtime", NO_REF, {"spike_unavailable": "No harness."})):
            with self.subTest(kind=kind):
                recorded = self.research(drv, run_id, kind, ref, **extra)
                self.assertEqual(recorded["type"], "Allow")
                observed = [row for o in recorded["run"]["obligations"]["active"] for row in o["observed"]]
                self.assertEqual(observed[-1]["confirm"], "unavailable")
        blocked = self.report(drv, run_id)
        reason = next(r for r in blocked["reasons"] if r["code"] == "evidence.confirmation_required")
        self.assertEqual(reason["parameters"]["route"], "unavailable")
        self.assertEqual(reason["next_actions"], ["residual.accept"])  # nobody can be asked
        claim = next(o for o in self.run_view(drv, run_id)["obligations"]["active"] if o["id"] == "claim:C0")
        self.assertEqual(claim["next"], ["residual.accept"])
        self.assertEqual(self.present(drv, run_id)["type"], "Block")
        self.assertEqual(self.answer(drv, run_id, "r1", confirm(self.last_id(drv, run_id)))["type"], "Block")
        stopped = self.stop(drv, run_id)["result"]
        residual = next(r for r in stopped["run"]["residuals"] if r["code"] == "evidence.confirmation_required")
        self.assertNotIn("human.confirm_evidence", residual["next_actions"])

    # ---- the gate -------------------------------------------------------------------------

    def test_the_research_response_itself_names_the_waiting_record(self):
        drv, run_id = self.start()
        recorded = self.research(drv, run_id, "web", URL)
        claim = next(o for o in recorded["run"]["obligations"]["active"] if o["id"] == "claim:C0")
        self.assertEqual(claim["observed"][-1]["confirm"], "human")
        self.assertEqual(claim["missing"]["code"], "evidence.confirmation_required")
        self.assertEqual(claim["missing"]["parameters"],
                         {"route": "human", "record_ids": [self.last_id(drv, run_id)]})
        self.assertIn("human.confirm_evidence", claim["next"])

    def test_a_claim_resting_on_a_waiting_record_is_not_approved(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.claim(drv, run_id)["state"], "open")
        self.assertEqual(self.report(drv, run_id)["reasons"][0]["code"], "evidence.confirmation_required")

    def test_convergence_names_the_records_and_offers_the_human(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        blocked = self.report(drv, run_id)
        reason = blocked["reasons"][0]
        self.assertEqual(reason["parameters"], {"route": "human", "record_ids": [self.last_id(drv, run_id)]})
        # The surface is observed (this host has it from the start), so the report is how the human is asked.
        self.assertEqual(reason["next_actions"], ["convergence.report", "human.confirm_evidence", "residual.accept"])

    def test_the_gate_holds_even_when_another_record_already_approves_the_claim(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "code", WORKSPACE_REF)
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.claim(drv, run_id)["state"], "approved")
        blocked = self.report(drv, run_id)
        self.assertEqual(blocked["reasons"][0]["code"], "evidence.confirmation_required")

    def test_the_block_does_not_charge_a_pass(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        used = self.run_view(drv, run_id)["governance"]["budgets"]["passes_used"]
        for _ in range(3):
            self.assertEqual(self.report(drv, run_id)["reasons"][0]["code"], "evidence.confirmation_required")
        self.assertEqual(self.run_view(drv, run_id)["governance"]["budgets"]["passes_used"], used)

    def test_the_reason_carries_the_unanswered_decision_claims_too(self):
        drv, run_id = self.start(graph=graph_with_decision())
        self.research(drv, run_id, "web", URL)
        reason = self.report(drv, run_id)["reasons"][0]
        self.assertEqual(reason["code"], "evidence.confirmation_required")
        self.assertEqual(reason["parameters"]["claim_ids"], ["C1"])

    # ---- the presentation -----------------------------------------------------------------

    def test_a_receipt_needs_a_presentation(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        record = self.last_id(drv, run_id)
        self.assertEqual(self.answer(drv, run_id, "p1", confirm(record))["type"], "Fault")
        self.assertEqual(self.verification(drv, run_id)["status"], "pending")

    def test_a_presentation_is_recorded_once_per_derivation(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        left = self.interactions(drv, run_id)["total"]  # the configuration approval spent one
        self.assertEqual(self.present(drv, run_id, "p1")["type"], "Allow")
        self.assertEqual(self.present(drv, run_id, "p1")["type"], "Inert")     # the same presentation
        self.assertEqual(self.present(drv, run_id, "p2")["type"], "Fault")     # the same batch, again
        self.assertEqual(self.interactions(drv, run_id)["total"], left - 1)    # one unit, once

    def test_nothing_waiting_is_nothing_to_present(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "code", WORKSPACE_REF)
        left = self.interactions(drv, run_id)["total"]
        self.assertEqual(self.present(drv, run_id)["type"], "Fault")
        self.assertEqual(self.interactions(drv, run_id)["total"], left)

    def test_a_changed_derivation_is_presented_again_and_a_stale_presentation_answers_nothing(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        first = self.last_id(drv, run_id)
        self.present(drv, run_id, "p1")
        self.research(drv, run_id, "web", URL + "/other")  # the pending set grew
        self.assertEqual(self.answer(drv, run_id, "p1", confirm(first))["type"], "Fault")
        self.assertEqual(self.present(drv, run_id, "p2")["type"], "Allow")
        self.assertEqual(self.answer(drv, run_id, "p2", confirm(first))["type"], "Allow")

    def test_an_item_left_out_stays_pending_and_is_shown_again_only_once(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.research(drv, run_id, "web", URL + "/later")
        first, later = (row["artifact_id"] for row in self.rows(drv, run_id)[-2:])
        left = self.interactions(drv, run_id)["total"]
        self.present(drv, run_id, "p1")
        self.assertEqual(self.answer(drv, run_id, "p1", confirm(first))["type"], "Allow")
        self.assertEqual(self.verification(drv, run_id)["status"], "pending")  # "Later"
        self.assertEqual(self.report(drv, run_id)["reasons"][0]["parameters"]["record_ids"], [later])
        self.assertEqual(self.present(drv, run_id, "p2")["type"], "Allow")      # a smaller batch
        self.assertEqual(self.present(drv, run_id, "p3")["type"], "Fault")      # but not again
        self.assertEqual(self.interactions(drv, run_id)["total"], left - 2)

    def test_a_decision_about_something_the_presentation_did_not_show_is_refused(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "code", WORKSPACE_REF)
        verbatim = self.last_id(drv, run_id)
        self.research(drv, run_id, "web", URL)
        self.present(drv, run_id, "p1")
        self.assertEqual(self.answer(drv, run_id, "p1", confirm(verbatim))["type"], "Fault")

    def test_the_real_interaction_limit_is_128(self):
        self.assertEqual(core_governance.MAX_INTERACTIONS, 128)  # the other budget tests patch it down for speed

    def test_the_human_interaction_budget_is_one_count_shared_with_governance(self):
        with mock.patch.object(core_governance, "MAX_INTERACTIONS", 3):  # its single owner; the real value is pinned above
            self.exhaust_the_interaction_budget()

    def exhaust_the_interaction_budget(self):
        drv, run_id = self.start()
        for n in range(self.interactions(drv, run_id)["total"]):
            self.research(drv, run_id, "web", f"{URL}/{n}")
            self.assertEqual(self.present(drv, run_id, f"p{n}")["type"], "Allow", n)
        view = self.run_view(drv, run_id)["governance"]
        self.assertEqual(view["interactions_remaining"]["total"], 0)
        self.assertEqual(view["prompt_error"], "governance.interaction_limit")
        self.research(drv, run_id, "web", URL + "/one-more")
        refused = self.present(drv, run_id, "p-last")
        self.assertEqual((refused["type"], refused["reasons"][0]["code"]),
                         ("Block", "governance.interaction_limit"))

    def test_the_governance_policy_counts_evidence_presentations_in_every_path(self):
        state = {"receipts": [], "plan_revision": 1}
        self.assertEqual(core_governance.interactions_remaining(state, 5)["total"], 123)
        self.assertIsNone(core_governance.interaction_error(state, 127))
        self.assertEqual(core_governance.interaction_error(state, 128), "governance.interaction_limit")

    # ---- the receipt ----------------------------------------------------------------------

    def test_confirm_reject_replay_and_conflict(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.research(drv, run_id, "runtime", NO_REF, spike_unavailable="No harness.")
        first, second = (row["artifact_id"] for row in self.rows(drv, run_id)[-2:])
        self.present(drv, run_id, "r1")
        self.assertEqual(self.answer(drv, run_id, "r1", confirm(first))["type"], "Allow")
        self.assertEqual(self.verification(drv, run_id, -2)["status"], "confirmed_by_human")
        self.assertEqual(self.answer(drv, run_id, "r1", confirm(first))["type"], "Inert")  # exact replay
        self.assertEqual(self.answer(drv, run_id, "r1", reject(first))["type"], "Fault")   # same id, other content
        self.present(drv, run_id, "r2")
        self.assertEqual(self.answer(drv, run_id, "r2", reject(first))["type"], "Fault")   # decided already
        self.assertEqual(self.answer(drv, run_id, "r2", confirm("sha256:" + "0" * 64))["type"], "Fault")
        self.assertEqual(self.answer(drv, run_id, "r2", reject(second, note="Wrong system."))["type"], "Allow")

    def test_a_receipt_with_one_subject_twice_is_refused(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        record = self.last_id(drv, run_id)
        self.present(drv, run_id, "p1")
        self.assertEqual(self.answer(drv, run_id, "p1", confirm(record), reject(record))["type"], "Fault")
        self.assertEqual(self.verification(drv, run_id)["status"], "pending")

    def test_a_confirmed_record_no_longer_blocks_convergence(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.present(drv, run_id, "p1")
        self.assertEqual(self.answer(drv, run_id, "p1", confirm(self.last_id(drv, run_id)))["type"], "Allow")
        self.assertEqual(self.claim(drv, run_id)["state"], "approved")
        self.assertNotIn("evidence.confirmation_required", self.codes(self.report(drv, run_id)))

    def test_a_rejected_record_leaves_the_claim_without_evidence(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        record = self.last_id(drv, run_id)
        self.present(drv, run_id, "p1")
        self.assertEqual(self.answer(drv, run_id, "p1", reject(record, note="Wrong system."))["type"], "Allow")
        claim = self.claim(drv, run_id)
        self.assertEqual(claim["active_evidence_ids"], [])
        self.assertNotEqual(claim["state"], "approved")
        self.assertEqual(self.verification(drv, run_id)["status"], "rejected_by_human")
        self.assertEqual(self.report(drv, run_id)["reasons"][0]["code"], "claim.research_missing")

    def test_the_humans_note_reaches_the_author(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.research(drv, run_id, "web", URL + "/second")
        first, second = (row["artifact_id"] for row in self.rows(drv, run_id)[-2:])
        self.present(drv, run_id, "p1")
        self.answer(drv, run_id, "p1", {**confirm(first), "note": "Fine, but the spec is v2."},
                    reject(second, note="Wrong system."))
        rows = self.rows(drv, run_id)
        self.assertEqual(rows[-2]["verification"]["note"], "Fine, but the spec is v2.")
        self.assertEqual(rows[-1]["verification"]["note"], "Wrong system.")
        observed = [row for o in self.run_view(drv, run_id)["obligations"]["active"] for row in o["observed"]]
        self.assertEqual([(row["note"], row.get("rejected")) for row in observed if "note" in row],
                         [("Fine, but the spec is v2.", None), ("Wrong system.", True)])

    def test_a_workspace_record_needs_no_confirmation(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "code", WORKSPACE_REF)
        self.assertEqual(self.verification(drv, run_id)["status"], "verified_by_service")
        self.assertEqual(self.routes(drv, run_id), [])

    # ---- needs-decision -------------------------------------------------------------------

    def test_a_human_decides_a_needs_decision_claim(self):
        for outcome, state in (("approve", "approved"), ("reject", "discarded")):
            with self.subTest(outcome=outcome):
                drv, run_id = self.start(kind="needs-decision")
                claim = self.claim(drv, run_id)
                self.assertEqual(claim["state"], "blocked")  # waiting on the human
                reason = self.report(drv, run_id)["reasons"][0]
                self.assertEqual((reason["code"], reason["parameters"]["claim_ids"]),
                                 ("evidence.confirmation_required", ["C0"]))
                self.assertEqual(self.present(drv, run_id)["type"], "Allow")
                decision = {"subject": "claim", "claim_id": "C0", "claim_digest": claim["wording_digest"],
                            "outcome": outcome, "answer": "Ship it."}
                self.assertEqual(self.answer(drv, run_id, "p1", decision)["type"], "Allow")
                claim = self.claim(drv, run_id)
                self.assertEqual(claim["state"], state)
                self.assertEqual(claim["human_decision"]["outcome"], outcome)

    def test_a_decision_on_another_wording_of_the_claim_is_refused(self):
        drv, run_id = self.start(kind="needs-decision")
        claim = self.claim(drv, run_id)
        self.present(drv, run_id, "p1")
        wrong = {"subject": "claim", "claim_id": "C0", "claim_digest": "sha256:" + "0" * 64,
                 "outcome": "approve"}
        self.assertEqual(self.answer(drv, run_id, "p1", wrong)["type"], "Fault")
        reworded = canonical_graph(kind="needs-decision")
        reworded["claims"][0]["text"] = "A different question for the human."
        self.dispatch(drv, observe_action(run_id=run_id, action=action_graph(reworded)))
        old = {**wrong, "claim_digest": claim["wording_digest"]}
        self.assertEqual(self.answer(drv, run_id, "p1", old)["type"], "Fault")
        self.assertEqual(self.claim(drv, run_id)["state"], "blocked")

    # ---- the audit ------------------------------------------------------------------------

    def audit(self, drv, run_id, variant="distinct") -> dict:
        self.dispatch(drv, observe_action(run_id=run_id, action=action_freeze()))
        child = self.require_pending_audit_child(drv, run_id)
        self.require_trusted_audit_attribution(drv, run_id, child, variant=variant)
        verdict = self.build_audit_verdict_payload(drv, run_id, verdict="pass", scope_review="pass")
        self.assertEqual(drv.trusted_audit_verdict(run_id, child, verdict)["result"]["type"], "Allow")
        return self.report(drv, run_id)

    def test_a_confirmation_after_an_audit_makes_that_audit_stale(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "code", WORKSPACE_REF)
        self.research(drv, run_id, "web", URL)
        record = self.last_id(drv, run_id)
        self.audit(drv, run_id)
        self.assertEqual(self.run_view(drv, run_id)["audit"]["state"], "passed")
        self.present(drv, run_id, "p1")
        self.assertEqual(self.answer(drv, run_id, "p1", confirm(record))["type"], "Allow")
        self.assertEqual(self.run_view(drv, run_id)["audit"]["state"], "failed")
        self.assertEqual(self.report(drv, run_id)["reasons"][0]["code"], "audit.failed")

    def test_a_receipt_changes_what_an_audit_reviewed_and_what_the_dossier_covers(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        record = self.last_id(drv, run_id)
        before = self.snapshot(drv, run_id)
        claim = next(c for c in before.graph["claims"] if c["id"] == "C0")
        self.present(drv, run_id, "p1")
        self.answer(drv, run_id, "p1", confirm(record))
        after = self.snapshot(drv, run_id)
        self.assertNotEqual(evidence_digest(before, claim), evidence_digest(after, claim))
        self.assertEqual(len(dossier_artifact_ids(after.history)) - len(dossier_artifact_ids(before.history)), 1)

    # ---- human-owned claims and independence -----------------------------------------------

    def decide(self, drv, run_id, claim_id="C0", receipt="p1") -> None:
        claim = self.claim(drv, run_id, claim_id)
        self.assertEqual(self.present(drv, run_id, receipt)["type"], "Allow")
        self.assertEqual(self.answer(drv, run_id, receipt, {
            "subject": "claim", "claim_id": claim_id, "claim_digest": claim["wording_digest"],
            "outcome": "approve"})["type"], "Allow")

    def test_a_run_of_human_decisions_only_still_needs_an_audit_but_no_independence(self):
        drv, run_id = self.start(kind="needs-decision")
        self.decide(drv, run_id)
        self.dispatch(drv, observe_action(run_id=run_id, action=action_freeze()))
        self.assertEqual(self.report(drv, run_id)["reasons"][0]["code"], "audit.required")
        converged = self.audit(drv, run_id)
        self.assertEqual(converged["type"], "Allow")
        self.assertTrue(converged["converged"])
        self.assertEqual(self.run_view(drv, run_id)["audit"]["independence"], "human_owned")

    def test_a_run_of_human_decisions_only_still_needs_a_host_observed_auditor(self):
        drv, run_id = self.start(kind="needs-decision")
        self.decide(drv, run_id)
        blocked = self.audit(drv, run_id, variant="unverified")
        self.assertEqual(blocked["reasons"][0]["code"], "audit.independence_unverified")
        self.assertEqual(self.run_view(drv, run_id)["audit"]["independence"], "unverified")

    def test_a_human_approved_claim_with_research_keeps_its_producers(self):
        drv, run_id = self.start(graph=graph_with_decision())
        self.research(drv, run_id, "code", WORKSPACE_REF)
        self.decide(drv, run_id, claim_id="C1")
        blocked = self.audit(drv, run_id, variant="same_model")
        self.assertEqual(blocked["reasons"][0]["code"], "audit.same_model")
        self.assertEqual(self.run_view(drv, run_id)["audit"]["independence"], "same_model")


class ViewIsOrderedLikeTheGateTests(EvidenceFixture):
    """ADR-0067 (2026-10-08, finding F-Q3-2): the run view orders what is owed exactly as the gate does."""

    def test_a_pending_confirmation_on_an_approved_claim_is_not_followed_by_an_offer_to_audit(self):
        """The Q3 shape: the claim is approved by a verbatim record, a second record waits on a human, nothing was audited.
        ``EvaluateRun`` answers with the confirmation (the batch comes before the audit gate), so the view must lead
        with it and must not offer ``child.spawn_auditor``."""
        drv, run_id = self.start(kind="ordinary")
        self.research(drv, run_id, "code", CITED_SOURCE[0])
        self.research(drv, run_id, "web", URL)
        gate = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))["result"]
        self.assertEqual([r["code"] for r in gate["reasons"]], ["evidence.confirmation_required"])
        self.assertEqual([c["state"] for c in self.argument(drv, run_id)["claims"]], ["approved"])
        view = self.dispatch(drv, get_run(run_id=run_id))["result"]["run"]
        audit = next(row for row in view["obligations"]["active"] if row["id"] == "obligation.audit")
        self.assertEqual(audit["status"], "residual")
        self.assertEqual(audit["missing"]["code"], "evidence.confirmation_required")
        self.assertNotIn("child.spawn_auditor", audit["next"])
        self.assertIn("human.confirm_evidence", audit["next"])
        self.assertEqual(audit["missing"]["parameters"], gate["reasons"][0]["parameters"])

    def test_once_the_human_has_decided_the_view_offers_the_audit(self):
        """The ordering is the gate's, not a ban: with nothing waiting on a human the audit is what is owed."""
        drv, run_id = self.start(kind="ordinary")
        self.research(drv, run_id, "code", CITED_SOURCE[0])
        view = self.dispatch(drv, get_run(run_id=run_id))["result"]["run"]
        audit = next(row for row in view["obligations"]["active"] if row["id"] == "obligation.audit")
        self.assertEqual(audit["missing"]["code"], "audit.required")
        self.assertIn("child.spawn_auditor", audit["next"])
