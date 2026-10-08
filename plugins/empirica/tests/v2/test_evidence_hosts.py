"""ADR-0067 Slice 2: the host-neutral mediator that presents the end-of-pass batch and submits one receipt.

Driven against the real service through the conformance driver, with a fake MCP form as the human. A
host decides no policy: these tests pin that it asks once, maps answers one item at a time, and grants
nothing on cancel, timeout, malformed input or a stale presentation."""
from __future__ import annotations

from adapters import confirmation
from adapters.confirmation import HostConfirmation
from adapters.public_tools import PublicTools
from test_evidence_confirmation import EvidenceFixture, URL
from application.protocol import host_profile_id  # noqa: E402
CLAUDE_PROFILE = host_profile_id("claude-code")
PI_PROFILE = host_profile_id("pi")
CODEX_PROFILE = host_profile_id("codex-cli")


class Human:
    """A scripted form: records what it was shown and answers with ``reply`` (a dict, or a callable)."""

    def __init__(self, reply):
        self.reply, self.shown = reply, []

    def __call__(self, message: str, schema: dict):
        self.shown.append((message, schema))
        return self.reply(message, schema) if callable(self.reply) else self.reply


def accept(**content) -> dict:
    return {"action": "accept", "content": content}


class MediatorFixture(EvidenceFixture):
    """The real service, a scripted human and the shared Claude mediator, in Claude's order of events: a run starts
    with its surface unobserved and the mediator records the surface before it evaluates a report."""

    SURFACE_AT_START = "unobserved"

    def surface(self, drv, run_id, surface: str) -> dict:
        return drv.trusted_human_surface(run_id, surface)["result"]

    def mediator(self, drv, human) -> HostConfirmation:
        return HostConfirmation(
            CLAUDE_PROFILE, elicit=human, timeout=lambda: 900, dispatch=lambda req, _p: drv.request(req),
            presentation_ingress=lambda _p, run, payload: drv.trusted_evidence_presentation(run, payload),
            confirmation_ingress=lambda _p, run, payload: drv.trusted_evidence_confirmation(run, payload),
            surface_ingress=lambda _p, run, payload: drv.trusted_human_surface(run, payload["surface"]))

    form = True  # whether the client declared form elicitation
    #: A test of the presentation step starts after the host's look: in Claude the look and the presentation happen
    #: inside one ``report_convergence``, so a run that is being presented has been looked at.
    LOOKED_AT_START = False

    def start(self, *args, **kwargs):
        drv, run_id = super().start(*args, **kwargs)
        if self.LOOKED_AT_START:
            self.look(drv, run_id)
        return drv, run_id

    def look(self, drv, run_id) -> None:
        """What Claude does first inside ``report_convergence``: record whether a form can be shown."""
        self.mediator(drv, Human(None) if self.form else None).observe(run_id)

    def report(self, drv, run_id, host_failure: str | None = None) -> dict:
        """``report_convergence`` as Claude runs it: the host records its surface, then the core evaluates."""
        self.look(drv, run_id)
        return super().report(drv, run_id, host_failure)

    def evaluated(self, drv, run_id, host_failure: str | None = None) -> dict:
        """An evaluation with no look before it: what a Stop hook reads, and what a report does after the look that the
        mediator under test already made (or failed to make); ``report`` would look again, with a mediator that works."""
        return super().report(drv, run_id, host_failure)

    def reevaluator(self, drv, run_id, *, look: bool = True):
        """The callback a mediator is given: evaluate again, carrying the host failure it reports (looking first, or not)."""
        evaluate = self.report if look else self.evaluated
        return lambda failure=None: evaluate(drv, run_id, failure)

    def present(self, drv, run_id, receipt="p1", key=None) -> dict:
        """The mediator's presentation step, which follows the host's look within the same report."""
        self.look(drv, run_id)
        return self.present_now(drv, run_id, receipt, key)

    def mediate(self, drv, run_id, human, *, blocked=None) -> dict:
        mediator = self.mediator(drv, human)
        mediator.observe(run_id)  # Claude records the surface before the evaluation reads it
        blocked = blocked or self.report(drv, run_id)
        return mediator(blocked, self.reevaluator(drv, run_id))

    def left(self, drv, run_id) -> int:
        return self.interactions(drv, run_id)["total"]

    def batch(self, drv, run_id):
        return self.run_view(drv, run_id)["confirmation_batch"]


class MediatorTests(MediatorFixture):
    # ---- the answers ----------------------------------------------------------------------

    def test_reject_carries_the_note_and_leaves_the_claim_without_evidence(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        refreshed = self.mediate(drv, run_id, Human(accept(d1="reject", n1="  Wrong system.  ")))
        self.assertEqual(refreshed["reasons"][0]["code"], "claim.research_missing")
        row = self.verification(drv, run_id)
        self.assertEqual((row["status"], row["note"]), ("rejected_by_human", "Wrong system."))

    def test_later_grants_nothing_and_the_presentation_stays_spent(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        before = self.left(drv, run_id)
        blocked = self.report(drv, run_id)
        self.assertEqual(self.codes(self.mediate(drv, run_id, Human(accept(d1="later")), blocked=blocked)), self.codes(blocked))
        self.assertEqual(self.verification(drv, run_id)["status"], "pending")
        self.assertEqual(self.left(drv, run_id), before - 1)
        # The same pending set is not shown again until the derivation changes: a stale response that still offers it is
        # evaluated again and the fresh one says it was already shown (the core's own refusal outranks the host's report).
        again = Human(accept(d1="confirm"))
        fresh = self.mediate(drv, run_id, again, blocked=blocked)
        self.assertEqual(fresh["run"]["confirmation_batch"]["prompt_error"], "already_presented")
        self.assertEqual(again.shown, [])

    # ---- nothing granted ------------------------------------------------------------------

    def test_cancel_timeout_decline_and_malformed_answers_grant_nothing(self):
        for label, reply in (("timeout", None), ("decline", {"action": "decline"}),
                             ("cancel", {"action": "cancel"}), ("not a dict", "accept"),
                             ("no content", {"action": "accept"}),
                             ("unknown choice", accept(d1="maybe")),
                             ("choice of the other subject", accept(d1="approve")),
                             ("unknown field", accept(d1="confirm", x9="confirm")),
                             ("note not text", accept(d1="confirm", n1=7))):
            with self.subTest(label):
                drv, run_id = self.start()
                self.research(drv, run_id, "web", URL)
                before = self.left(drv, run_id)
                blocked = self.report(drv, run_id)
                self.assertEqual(self.codes(self.mediate(drv, run_id, Human(reply), blocked=blocked)), self.codes(blocked))  # fresh, same reason
                self.assertEqual(self.verification(drv, run_id)["status"], "pending")
                self.assertEqual(self.left(drv, run_id), before - 1)  # only the presentation was spent

    def test_a_dialog_that_raises_grants_nothing(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)

        def broken(_message, _schema):
            raise RuntimeError("the client went away")
        blocked = self.report(drv, run_id)
        # The presentation was recorded before the dialog raised, so the batch was shown: the fresh evaluation says so.
        fresh = self.mediator(drv, broken)(blocked, self.reevaluator(drv, run_id))
        self.assertEqual(fresh["run"]["confirmation_batch"]["prompt_error"], "already_presented")
        self.assertEqual(self.verification(drv, run_id)["status"], "pending")

    def test_without_form_elicitation_nothing_is_presented_or_spent(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        before = self.left(drv, run_id)
        blocked = self.report(drv, run_id)
        self.assertEqual(self.mediate(drv, run_id, None, blocked=blocked), blocked)
        self.assertEqual(self.left(drv, run_id), before)

    def test_route_unavailable_shows_no_dialog_and_keeps_the_requirement_visible(self):
        drv, run_id = self.start(host_profile_id("codex-cli"), mode="auto", audit_retrieval=[],
                                 delegation=True, interactive=False)
        self.research(drv, run_id, "web", URL)
        human = Human(accept(d1="confirm"))
        blocked = self.report(drv, run_id)
        self.assertIsNone(confirmation.presentable(blocked))
        self.assertEqual(self.mediate(drv, run_id, human, blocked=blocked), blocked)
        self.assertEqual(human.shown, [])
        self.assertEqual(blocked["reasons"][0]["code"], "evidence.confirmation_required")

    def test_a_presentation_made_stale_during_the_dialog_is_refused_and_grants_nothing(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        blocked = self.report(drv, run_id)

        def derivation_changes(_message, _schema):
            self.research(drv, run_id, "web", URL + "/another")  # the pending set grows under the dialog
            return accept(d1="confirm")
        self.assertEqual(self.codes(self.mediate(drv, run_id, Human(derivation_changes), blocked=blocked)), self.codes(blocked))
        self.assertEqual(self.verification(drv, run_id, 0)["status"], "pending")

    def test_a_second_pass_end_with_a_changed_set_is_presented_again(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.mediate(drv, run_id, Human(accept(d1="later")))
        self.research(drv, run_id, "web", URL + "/another")
        human = Human(accept(d1="confirm", d2="confirm"))
        self.mediate(drv, run_id, human)
        self.assertEqual(len(human.shown), 1)
        self.assertEqual({r["verification"]["status"] for r in self.rows(drv, run_id)}, {"confirmed_by_human"})

    # ---- the pure parts -------------------------------------------------------------------

    def test_decisions_omit_later_and_refuse_anything_that_was_not_shown(self):
        items = [{"subject": "research", "id": "r1"}, {"subject": "claim", "id": "C1", "digest": "sha256:d"}]
        answers = [{"subject": "research", "id": "r1", "choice": "later", "note": None},
                   {"subject": "claim", "id": "C1", "choice": "approve", "note": " ok "}]
        self.assertEqual(confirmation.decisions(items, answers), [
            {"subject": "claim", "claim_id": "C1", "claim_digest": "sha256:d", "outcome": "approve",
             "answer": "ok"}])
        self.assertEqual(confirmation.decisions(items, answers[:1]), [])  # all Later: nothing to submit
        for bad in ([{"subject": "research", "id": "other", "choice": "confirm"}],
                    [{"subject": "research", "id": "r1", "choice": "confirm"}] * 2,
                    [{"subject": "research", "id": "r1", "choice": "approve"}],
                    [{"subject": "research", "id": "r1", "choice": "confirm", "note": "x" * 2049}],
                    "confirm", [None]):
            with self.subTest(bad=str(bad)[:40]):
                self.assertEqual(confirmation.decisions(items, bad), [])
        half = [answers[1], {"subject": "research", "id": "r1", "choice": "nonsense"}]
        self.assertEqual(confirmation.decisions(items, half), [])  # one bad answer withdraws them all

    def test_the_form_is_flat_primitive_and_unlike_the_configuration_form(self):
        items = [{"subject": "research", "id": "r1", "claim": "C", "source_kind": "web", "source_ref": URL,
                  "quote": "q", "class": "retrievable"}, {"subject": "claim", "id": "C1", "digest": "d", "text": "T"}]
        message, schema = confirmation.form(items, 900)
        self.assertEqual(set(schema["properties"]), {"d1", "n1", "d2", "n2"})
        for key, prop in schema["properties"].items():
            self.assertEqual(prop["type"], "string", key)
            self.assertNotIn("properties", prop)
        self.assertEqual(schema["properties"]["d1"]["enum"], ["later", "confirm", "reject"])
        self.assertEqual(schema["properties"]["d2"]["enum"], ["later", "approve", "reject"])
        self.assertEqual({p["default"] for k, p in schema["properties"].items() if k[0] == "d"}, {"later"})
        self.assertNotIn("max_passes", schema["properties"])
        self.assertNotIn("approve all", message.lower())
        # What the human reads, per item: the source, the claim it supports, and the decision (decisions listed in order).
        self.assertIn(f"web source: {URL}", message)
        self.assertIn("For claim: C", message)
        self.assertIn("2. Decision needed", message)
        observed = {"subject": "research", "id": "r3", "claim": "C", "source_kind": "runtime", "source_ref": "none",
                    "quote": "q", "class": "observed", "reason": "No harness."}
        self.assertIn("Why no deterministic check: No harness.", confirmation.form([observed], 900)[0])

    def test_a_batch_is_presentable_only_without_a_prompt_error_and_whatever_the_reason(self):
        def view(batch, code="claim.research_missing"):
            return {"type": "Block", "reasons": [{"code": code}], "run": {"id": "r", "confirmation_batch": batch}}
        ready = {"key": "k", "record_ids": [], "claim_ids": ["C1"], "prompt_error": None}
        self.assertEqual(confirmation.presentable(view(ready)), ready)  # another blocker leads; the human is still asked
        for other in (view(None), {"type": "Allow"}, None, view({**ready, "prompt_error": "already_presented"}),
                      view({**ready, "prompt_error": "interaction_limit"}),
                      view({**ready, "prompt_error": "unavailable"})):
            self.assertIsNone(confirmation.presentable(other))

    # ---- through the public tool ----------------------------------------------------------

    def test_report_convergence_through_the_public_tool_presents_then_returns_the_refreshed_view(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        human = Human(accept(d1="confirm"))
        tools = PublicTools(CLAUDE_PROFILE, dispatch=lambda req, _p: drv.request(req),
                            confirm=self.mediator(drv, human))
        called = tools.call_internal("report_convergence", {"run_id": run_id})
        self.assertEqual(len(human.shown), 1)
        self.assertNotIn("evidence.confirmation_required", self.codes(called["structuredContent"]))
        # A stop is not the end of a pass: the pending evidence is moot, so what is presented is only the consent to end
        # the run (ADR-0063 amendment), and declining it ends nothing.
        drv2, run2 = self.start()
        self.research(drv2, run2, "web", URL)
        declined = Human({"action": "decline"})
        tools2 = PublicTools(CLAUDE_PROFILE, dispatch=lambda req, _p: drv2.request(req), confirm=self.mediator(drv2, declined))
        result = tools2.call_internal("report_convergence", {"run_id": run2, "intent": "stop"})["structuredContent"]
        self.assertEqual(len(declined.shown), 1)
        self.assertIn("End this run as stopped_residual", declined.shown[0][0])
        self.assertNotIn("web source", declined.shown[0][0])  # the pending record is not asked
        self.assertEqual(self.codes(result), ["governance.terminal_stop_pending"])
        self.assertEqual(result["run"]["status"], "active")
        drv3, run3 = self.start()
        approving = Human(accept(d1="approve"))
        tools3 = PublicTools(CLAUDE_PROFILE, dispatch=lambda req, _p: drv3.request(req), confirm=self.mediator(drv3, approving))
        ended = tools3.call_internal("report_convergence", {"run_id": run3, "intent": "stop"})["structuredContent"]
        self.assertEqual(ended["run"]["status"], "stopped_residual")


class ClaudeElicitationTests(EvidenceFixture):
    """The MCP session gives the batch form only to a client that declared form elicitation."""

    def session(self, caps: dict):
        import queue
        from adapters.mcp_server import McpSession
        session = McpSession(CLAUDE_PROFILE, queue.Queue(), lambda _m: None, timeout=0.05)
        session.process({"jsonrpc": "2.0", "id": "i", "method": "initialize",
                         "params": {"protocolVersion": "2025-11-25", "capabilities": caps}})
        return session

    def test_form_elicitation_is_required_for_the_batch(self):
        for caps, form in (({"elicitation": {"form": {}}}, True), ({"elicitation": {}}, True),
                           ({}, False), ({"elicitation": {"url": {}}}, False)):
            with self.subTest(caps=caps):
                session = self.session(caps)
                self.assertEqual(session.confirmer.elicit is not None, form)
                self.assertEqual(session.mediator.elicit is not None, form)  # the same capability as governance

    def test_the_batch_is_a_separate_form_from_the_configuration_review(self):
        from adapters import governance
        self.assertNotEqual(confirmation.form.__module__, governance.review_form.__module__)
        _, schema = confirmation.form([{"subject": "claim", "id": "C1", "digest": "d", "text": "T"}], 60)
        self.assertFalse({"max_passes", "max_spawns", "max_audit_spawns"} & set(schema["properties"]))


class PiPrivateBridgeTests(EvidenceFixture):
    """Pi reuses the shared flow through its private bridge; the bridge only relays it."""

    def call(self, raw: dict, payload: dict):
        import importlib.util
        from pathlib import Path
        path = Path(confirmation.__file__).parent / "pi" / "private_bridge.py"
        spec = importlib.util.spec_from_file_location("empirica_pi_private_bridge", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module._dispatch(PI_PROFILE, raw, payload)

    def test_presentation_relays_items_or_the_core_refusal(self):
        from unittest.mock import patch
        items = [{"subject": "claim", "id": "C1", "digest": "d", "text": "T"}]
        with patch.object(confirmation, "present", return_value=("p-1", items)):
            self.assertEqual(self.call({"operation": "evidence_presentation", "run_id": "r"}, {}),
                             {"type": "evidence_presentation", "receipt_id": "p-1", "items": items})
        with patch.object(confirmation, "present", return_value=confirmation.PRESENTATION_FAILED):
            self.assertEqual(self.call({"operation": "evidence_presentation", "run_id": "r"}, {}),
                             {"type": "evidence_refused", "result": {"failure": "presentation_failed"}})

    def test_a_refusal_from_the_real_present_is_relayed_not_crashed_on(self):
        """G6-01: ``present`` returns an ``Outcome`` when nothing can be shown; the relay must say so, in the shape the
        Pi transport accepts (``evidence_refused`` with an object result), not unpack it as a receipt."""
        from unittest.mock import patch
        with patch.object(confirmation, "_call", return_value={}):  # no batch to show: the real present refuses
            shown = confirmation.present(PI_PROFILE, "r")
            self.assertIs(shown, confirmation.PRESENTATION_FAILED)
            relayed = self.call({"operation": "evidence_presentation", "run_id": "r"}, {})
        self.assertEqual(relayed, {"type": "evidence_refused", "result": {"failure": "presentation_failed"}})

    def test_confirmation_relays_the_receipt_or_that_nothing_was_granted(self):
        from unittest.mock import patch
        payload = {"receipt_id": "p-1", "items": [], "answers": []}
        with patch.object(confirmation, "submit", return_value=None) as submit:
            self.assertEqual(self.call({"operation": "evidence_confirmation", "run_id": "r"}, payload),
                             {"type": "evidence_none"})
            self.assertEqual(submit.call_args.args[:3], (PI_PROFILE, "r", "p-1"))
        with patch.object(confirmation, "submit", return_value={"type": "Allow"}):
            self.assertEqual(self.call({"operation": "evidence_confirmation", "run_id": "r"}, payload),
                             {"type": "evidence_receipt", "result": {"type": "Allow"}})


class SurfaceFailsClosedTests(MediatorFixture):
    """R3 m11: a host that cannot record that a form is available tells the run nobody is there."""

    def _broken(self, drv, fail_for):
        """A mediator whose surface ingress fails (raises, or faults) for the given surfaces."""
        def ingress(_p, run, payload):
            if payload["surface"] in fail_for:
                if "raise" in fail_for:
                    raise OSError("store unavailable")
                return {"result": {"type": "Fault", "code": "conflict", "fail_direction": "closed"}}
            return drv.trusted_human_surface(run, payload["surface"])
        return HostConfirmation(CLAUDE_PROFILE, elicit=Human(accept()), timeout=lambda: 900,
                                dispatch=lambda req, _p: drv.request(req), surface_ingress=ingress)

    def test_a_surface_that_could_not_be_recorded_as_present_is_recorded_as_unavailable(self):
        for how in ({"human"}, {"human", "raise"}):
            with self.subTest(how=sorted(how)):
                drv, run_id = self.start()
                self.research(drv, run_id, "web", URL)
                self.assertEqual(self.surface(drv, run_id, "unavailable")["type"], "Allow")  # a stale earlier observation...
                self.assertEqual(self.surface(drv, run_id, "human")["type"], "Allow")        # ...that said a human was there
                self.assertIsNone(self.batch(drv, run_id)["prompt_error"])
                self.assertEqual(self.mediator(drv, Human(accept())).__class__, HostConfirmation)
                self._broken(drv, how).observe(run_id)
                self.assertEqual(self.batch(drv, run_id)["prompt_error"], "unavailable")

    def test_negative_control_without_the_fallback_the_stale_human_stays(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.surface(drv, run_id, "human")  # a stale earlier observation
        broken = self._broken(drv, {"human"})
        confirmation.observe_surface(broken.profile, run_id, True, ingress=broken._surface)  # the failed attempt alone
        self.assertIsNone(self.batch(drv, run_id)["prompt_error"], "the run still believes a human is there")

    def test_a_host_without_a_form_records_unavailable_and_a_recorded_one_does_not_fall_back(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        HostConfirmation(CLAUDE_PROFILE, elicit=None,
                         surface_ingress=lambda _p, run, payload: drv.trusted_human_surface(run, payload["surface"])).observe(run_id)
        self.assertEqual(self.batch(drv, run_id)["prompt_error"], "unavailable")
        calls = []
        ok = HostConfirmation(CLAUDE_PROFILE, elicit=Human(accept()), surface_ingress=lambda _p, run, payload: (
            calls.append(payload["surface"]), drv.trusted_human_surface(run, payload["surface"]))[1])
        ok.observe(run_id)
        self.assertEqual((calls, self.batch(drv, run_id)["prompt_error"]), (["human"], None))
