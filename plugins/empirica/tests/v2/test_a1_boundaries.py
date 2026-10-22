"""Final review A1 (ADR-0067): the boundaries between what was shown, what was recorded and what the author reads.

One test per demonstrated divergence cell of the A1 boundary trace; the failing-first record is in the build
progress log ("A1 fix round"). The host is the shared Claude mediator with a scripted human; the Pi side of
the same rules is tested in adapters/pi/test."""
from __future__ import annotations

import json
from pathlib import Path

from adapters import confirmation
from adapters.author_view import render_author_view
from adapters.claude.completion import stop_result
from adapters.claude.transport import Response
from adapters.public_tools import PublicTools
from application import protocol
from assertions import action_freeze, observe_action
from test_evidence_confirmation import URL, WORKSPACE_REF
from test_evidence_hosts import Human, MediatorFixture, accept
from application.protocol import host_profile_id  # noqa: E402
CLAUDE_PROFILE = host_profile_id("claude-code")

CONFIRMATION = "evidence.confirmation_required"
EVIDENCE_REPORT_NOTICE = getattr(protocol, "EVIDENCE_REPORT_NOTICE", "<not defined>")
PAUSED_FOR_EVIDENCE_NOTICE = protocol.PAUSED_FOR_EVIDENCE_NOTICE


def typed(result: dict) -> Response:
    return Response.from_envelope({"request_id": "a1"}, {"protocol": "empirica/v2", "request_id": "a1", "result": result})


class PresentationBindsWhatWasShown(MediatorFixture):
    """A1-1: the presentation names the batch key the host showed; the core admits only the current one."""

    LOOKED_AT_START = True  # the presentation step follows the host's look inside one report

    def racing(self, drv, run_id, extra=1):
        """An ingress that adds ``extra`` unseen research records just before the first presentation lands."""
        state = {"left": extra}

        def ingress(_profile, rid, payload):
            if state["left"]:
                state["left"] -= 1
                self.research(drv, run_id, "web", f"{URL}/unseen-{extra}-{state['left']}")
            return drv.trusted_evidence_presentation(rid, payload)
        return ingress

    def mediator_with(self, drv, human, ingress):
        mediator = self.mediator(drv, human)
        mediator._presentation = ingress
        return mediator

    def presentations(self, drv, run_id):
        return [a for a in self.snapshot(drv, run_id).history if a.get("kind") == "evidence_presentation"]

    def test_the_core_refuses_a_presentation_of_another_batch_and_spends_nothing(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        before = self.interactions(drv, run_id)["total"]
        refused = drv.trusted_evidence_presentation(
            run_id, {"run_id": run_id, "receipt_id": "p1", "key": "sha256:" + "0" * 64})["result"]
        self.assertEqual((refused["type"], refused["code"]), ("Fault", "conflict"))
        self.assertEqual(self.interactions(drv, run_id)["total"], before)
        self.assertEqual(self.presentations(drv, run_id), [])
        self.assertIsNone(self.batch(drv, run_id)["prompt_error"])

    def test_the_core_admits_the_current_key(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.present(drv, run_id)["type"], "Allow")

    def test_a_key_covers_the_contents_it_names(self):
        drv, run_id = self.start(graph=self.decision_graph())
        first = self.batch(drv, run_id)["key"]
        self.research(drv, run_id, "web", URL)
        second = self.batch(drv, run_id)["key"]
        self.assertNotEqual(first, second)  # a record joined
        reworded = self.decision_graph()
        reworded["claims"][1]["text"] = "A different question?"
        self.dispatch(drv, observe_action(run_id=run_id, action=self.graph_action(reworded)))
        self.assertNotEqual(self.batch(drv, run_id)["key"], second)  # a decision claim's wording changed

    @staticmethod
    def decision_graph():
        from test_evidence_confirmation import graph_with_decision
        return graph_with_decision()

    @staticmethod
    def graph_action(graph):
        from assertions import action_graph
        return action_graph(graph)

    def test_D_an_unseen_record_is_shown_after_one_reread_and_the_presentation_names_what_was_shown(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        shown = confirmation.present(CLAUDE_PROFILE, run_id, dispatch=lambda req, _p: drv.request(req),
                                     ingress=self.racing(drv, run_id))
        receipt_id, items = shown
        self.assertEqual(len(items), 2)
        stored = self.presentations(drv, run_id)[-1]
        self.assertEqual(sorted(stored["record_ids"]), sorted(i["id"] for i in items))

    def test_D_full_answers_after_the_interleaving_decide_exactly_what_was_shown(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        human = Human(accept(d1="confirm", d2="confirm"))
        self.mediator_with(drv, human, self.racing(drv, run_id))(self.report(drv, run_id), self.reevaluator(drv, run_id))
        self.assertEqual({r["verification"]["status"] for r in self.rows(drv, run_id)}, {"confirmed_by_human"})

    def test_D_partial_answers_after_the_interleaving_leave_the_rest_pending(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        human = Human(accept(d1="confirm", d2="later"))
        self.mediator_with(drv, human, self.racing(drv, run_id))(self.report(drv, run_id), self.reevaluator(drv, run_id))
        self.assertEqual([r["verification"]["status"] for r in self.rows(drv, run_id)], ["confirmed_by_human", "pending"])

    def test_D_later_after_the_interleaving_marks_only_what_was_shown_as_presented(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        human = Human(accept(d1="later", d2="later"))
        self.mediator_with(drv, human, self.racing(drv, run_id))(self.report(drv, run_id), self.reevaluator(drv, run_id))
        self.assertEqual(len(human.shown[0][1]["properties"]), 4)  # both records were in the dialog
        self.assertEqual(self.batch(drv, run_id)["prompt_error"], "already_presented")
        # And a record nobody saw is never spent: it arrives after the dialog and is asked on its own.
        self.research(drv, run_id, "web", URL + "/after")
        self.assertIsNone(self.batch(drv, run_id)["prompt_error"])

    def test_D_a_second_change_during_the_reread_shows_nothing_and_spends_nothing(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        before = self.interactions(drv, run_id)["total"]
        human = Human(accept(d1="confirm"))
        mediator = self.mediator_with(drv, human, self.racing(drv, run_id, extra=2))
        blocked = self.report(drv, run_id)
        said = mediator(blocked, self.reevaluator(drv, run_id))
        self.assertEqual(said["run"]["confirmation_batch"]["prompt_error"], "presentation_failed")  # and it says so
        self.assertEqual(human.shown, [])
        self.assertEqual(self.interactions(drv, run_id)["total"], before)
        self.assertEqual(self.presentations(drv, run_id), [])
        self.assertIsNone(self.batch(drv, run_id)["prompt_error"])


class ClaudeStopEvidenceByControlMode(MediatorFixture):
    """A2-1/A2-2 (ruling), for an **auto** run: in Claude the human can only be asked *inside* ``report_convergence`` (MCP
    elicitation), so an auto Stop that settled on a batch nobody has shown would end the turn and nobody would ask. With
    the confirmation as the sole blocker and nobody having been told otherwise it blocks (exit 2) with one plain
    directive to report convergence; in every other case it blocks with the real reason. There is no surface note. A
    deliberative turn end pauses instead, because the human is present (``StopModeTests``, EHB-STOP-21/22/23)."""

    MODE = "auto"

    def start(self, *args, **kwargs):
        return super().start(*args, **{"mode": self.MODE, **kwargs})

    FAULT = lambda *_: {"result": {"type": "Fault", "code": "unavailable"}}  # noqa: E731

    @staticmethod
    def broken(reply):
        def raising(*_args):
            raise RuntimeError("the core went away")
        return raising if reply is None else reply

    def cells(self):
        """Every way the surface can be (not) recorded, with whether the mediator may show the batch afterwards."""
        return {
            "recorded human": (True, None, True),
            "recorded unavailable (no form)": (False, None, False),
            "no UI, typed fault": (False, self.FAULT, False),
            "no UI, exception": (False, "raise", False),
            "UI, both writes fault": (True, self.FAULT, False),
            "UI, both writes raise": (True, "raise", False),
        }

    def test_D_the_stop_blocks_with_the_directive_in_every_cell_and_the_mediator_shows_only_when_recorded(self):
        for label, (has_form, reply, may_show) in self.cells().items():
            with self.subTest(label):
                drv, run_id = self.start()  # the stored surface is `human`
                self.research(drv, run_id, "web", URL)
                human = Human(accept(d1="later"))
                mediator = self.mediator(drv, human if has_form else None)
                if reply is not None:
                    mediator._surface = self.broken(None if reply == "raise" else reply)
                mediator.observe(run_id)
                response = self.evaluated(drv, run_id)
                stop = stop_result(typed(response), stop_hook_active=False)
                if has_form or label.startswith("no UI"):
                    self.assertEqual(self.codes(response), [CONFIRMATION], label)
                self.assertEqual(stop.exit_code, 2, label)
                if label != "recorded unavailable (no form)":
                    self.assertEqual(stop.stderr, EVIDENCE_REPORT_NOTICE + "\n", label)
                self.assertEqual(stop.stdout, "", label)  # never a settlement
                mediator(response, self.reevaluator(drv, run_id, look=False))
                self.assertEqual(len(human.shown), 1 if may_show else 0, label)

    def test_D_a_deliberative_stop_pauses_for_evidence_where_the_human_is_present(self):
        """The same cells in deliberative mode: the turn ends with ``paused_for_evidence`` (or, once nobody can be asked,
        the real reason), the run stays active, and nobody is shown a dialog by the Stop."""
        for label, (has_form, reply, may_show) in self.cells().items():
            with self.subTest(label):
                drv, run_id = self.start(mode="deliberative")
                self.research(drv, run_id, "web", URL)
                human = Human(accept(d1="later"))
                mediator = self.mediator(drv, human if has_form else None)
                if reply is not None:
                    mediator._surface = self.broken(None if reply == "raise" else reply)
                mediator.observe(run_id)
                stop = stop_result(typed(self.evaluated(drv, run_id)), stop_hook_active=False)
                self.assertEqual((stop.exit_code, stop.stderr), (0, ""), label)
                message = json.loads(stop.stdout)["systemMessage"]
                if label != "recorded unavailable (no form)":
                    self.assertEqual(message, PAUSED_FOR_EVIDENCE_NOTICE, label)
                else:
                    self.assertIn("paused for you", message)
                self.assertEqual(human.shown, [], label)

    def test_D_overlapping_observations_cannot_make_the_stop_pause(self):
        """Two mediators on one run, their ingress calls interleaved (A2-1): whatever order they finish in, the Stop blocks."""
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        outer, inner = self.mediator(drv, None), self.mediator(drv, Human(accept(d1="later")))
        good = outer._surface

        def outer_surface(profile, run, payload):
            inner.observe(run_id)  # a newer, successful observation lands in the middle of the outer one
            return self.FAULT()
        outer._surface = outer_surface
        outer.observe(run_id)
        self.assertEqual(stop_result(typed(self.evaluated(drv, run_id)), stop_hook_active=False).exit_code, 2)
        outer._surface = good

    def test_D_a_failure_before_an_older_success_cannot_make_the_stop_pause(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        older, newer = self.mediator(drv, Human(accept(d1="later"))), self.mediator(drv, Human(accept(d1="later")))
        good = older._surface

        def older_surface(profile, run, payload):
            newer._surface = self.broken(None)
            newer.observe(run_id)  # the newer observation fails before the older call returns
            return good(profile, run, payload)
        older._surface = older_surface
        older.observe(run_id)
        self.assertEqual(stop_result(typed(self.evaluated(drv, run_id)), stop_hook_active=False).exit_code, 2)

    def test_D_the_surface_note_is_gone(self):
        import importlib.util
        self.assertIsNone(importlib.util.find_spec("adapters.surface_mark"))
        for name in ("SURFACE_UNRECORDED_NOTICE", "EVIDENCE_WAIT_NOTICE"):
            self.assertFalse(hasattr(protocol, name), f"the Claude Stop has no evidence wait to settle ({name})")

    def test_the_directive_names_the_call_that_asks_the_human(self):
        self.assertIn("report_convergence", EVIDENCE_REPORT_NOTICE)

    def test_nothing_is_written_under_the_home_when_observing(self):
        drv, run_id = self.start()
        mediator = self.mediator(drv, Human(accept(d1="later")))
        mediator._surface = self.broken(None)
        mediator.observe(run_id)
        self.assertEqual(list(Path(self.empirica_home).rglob("*")), [])


class TheHumanSeesTheWholeQuote(MediatorFixture):
    """A1-3."""

    def test_D_the_tail_of_a_long_quote_is_in_the_dialog(self):
        drv, run_id = self.start()
        quote = "A" * 610 + " THE ESSENTIAL QUALIFICATION AT THE END"
        self.research(drv, run_id, "web", URL, citation=quote)
        human = Human(accept(d1="confirm"))
        self.mediate(drv, run_id, human)
        message = human.shown[0][0]
        self.assertIn("THE ESSENTIAL QUALIFICATION AT THE END", message)
        self.assertNotIn("…", message)

    def test_no_quote_limit_is_left_in_the_host(self):
        self.assertFalse(hasattr(confirmation, "_QUOTE_LIMIT"))


class TheAuthorReadsWhatTheHumanSees(MediatorFixture):
    """A1-4: the public tool text carries the waiting batch, the verification of each citation and the
    human's note and answer, fenced as untrusted text."""

    def tools(self, drv):
        return PublicTools(CLAUDE_PROFILE, dispatch=lambda req, _p: drv.request(req))

    def text(self, tools, name, args):
        return tools.call(name, args)["content"][0]["text"]

    def test_D_a_url_on_an_already_supported_claim_shows_what_waits_and_why(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "code", WORKSPACE_REF)
        recorded = self.research(drv, run_id, "web", URL)
        text = render_author_view(recorded)
        self.assertIn("Waiting on a human", text)
        self.assertIn(URL, text)
        self.assertIn("C0", text)
        self.assertNotIn("sha256:", text.split("Waiting on a human")[1].split("\n\n")[0])
        self.assertIn("a human is asked when you report, if this host can show a dialog", text)  # Claude has not looked yet

    def test_D_a_fresh_get_run_after_later_says_an_explicit_report_asks_again(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.mediate(drv, run_id, Human(accept(d1="later")))
        text = self.text(self.tools(drv), "empirica_read", {"operation": "GetRun", "run_id": run_id})
        self.assertIn("Waiting on a human", text)
        self.assertIn("already shown", text)
        self.assertIn("an explicit report_convergence asks again", text)

    def test_D_a_rejection_and_its_note_are_in_the_argument_text_fenced(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.mediate(drv, run_id, Human(accept(d1="reject", n1="Use the 2026 specification instead.")))
        text = self.text(self.tools(drv), "empirica_read", {"operation": "GetArgument", "run_id": run_id})
        self.assertIn("rejected by a human", text)
        self.assertRegex(text, r"<<<EMPIRICA_UNTRUSTED_DATA[^>]*>>>Use the 2026 specification instead\.<<<END_EMPIRICA_UNTRUSTED_DATA")

    def test_D_a_decision_outcome_and_answer_are_in_the_argument_text_fenced(self):
        drv, run_id = self.start(kind="needs-decision")
        self.mediate(drv, run_id, Human(accept(d1="approve", n1="Use blue, never red.")))
        text = self.text(self.tools(drv), "empirica_read", {"operation": "GetArgument", "run_id": run_id})
        self.assertIn("approved by a human", text)
        self.assertRegex(text, r"<<<EMPIRICA_UNTRUSTED_DATA[^>]*>>>Use blue, never red\.<<<END_EMPIRICA_UNTRUSTED_DATA")

    def test_each_verification_status_has_plain_words(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "code", WORKSPACE_REF)  # checked by the service
        self.research(drv, run_id, "web", URL + "/a")
        self.research(drv, run_id, "web", URL + "/b")
        self.mediate(drv, run_id, Human(accept(d1="confirm", d2="later")))
        text = self.text(self.tools(drv), "empirica_read", {"operation": "GetArgument", "run_id": run_id})
        for words in ("checked by the service", "confirmed by a human"):
            self.assertIn(words, text)


class OneClaimsBlockerOrder(MediatorFixture):
    """A1-5: terminal states, then what the author can do now, then waiting on a human; the batch is still there."""

    def experiment(self, mode="deliberative"):
        drv, run_id = self.start(kind="needs-experiment", mode=mode, qualified=True)
        self.research(drv, run_id, "web", URL)
        return drv, run_id

    def test_D_a_missing_spike_leads_while_the_url_waits(self):
        drv, run_id = self.experiment()
        blocked = self.report(drv, run_id)
        self.assertEqual(self.codes(blocked), ["claim.spike_missing"])
        batch = self.batch(drv, run_id)
        self.assertIsNotNone(batch)
        self.assertIsNone(batch["prompt_error"])  # and the human is still asked at the end of the pass

    def test_D_a_stale_spike_leads_while_the_url_waits(self):
        drv, run_id = self.experiment()
        self.record_passing_spike(drv, run_id, WORKSPACE_REF)
        self.change_file(drv, WORKSPACE_REF)
        self.dispatch(drv, observe_action(run_id=run_id, action=action_freeze()))
        self.assertEqual(self.codes(self.report(drv, run_id)), ["claim.spike_stale"])
        self.assertIsNotNone(self.batch(drv, run_id))

    def test_the_confirmation_leads_once_the_spike_is_met(self):
        drv, run_id = self.experiment()
        self.record_passing_spike(drv, run_id, WORKSPACE_REF)
        self.assertEqual(self.codes(self.report(drv, run_id)), [CONFIRMATION])

    def test_D_a_deliberative_stop_pauses_with_the_spike_reason(self):
        drv, run_id = self.experiment()
        stop = stop_result(typed(self.report(drv, run_id)), stop_hook_active=False)
        self.assertEqual(stop.exit_code, 0)
        self.assertIn("spike", json.loads(stop.stdout)["systemMessage"].lower())
        self.assertIn("paused for you", json.loads(stop.stdout)["systemMessage"])

    def test_D_claude_stop_blocks_with_the_spike_reason_and_does_not_pause(self):
        drv, run_id = self.experiment("auto")
        stop = stop_result(typed(self.report(drv, run_id)), stop_hook_active=False)
        self.assertEqual(stop.exit_code, 2)
        self.assertIn("spike", stop.stderr.lower())
        self.record_passing_spike(drv, run_id, WORKSPACE_REF)
        self.change_file(drv, WORKSPACE_REF)
        stale = stop_result(typed(self.report(drv, run_id)), stop_hook_active=False)
        self.assertEqual(stale.exit_code, 2)
        self.assertIn("re-gate", stale.stderr.lower())

    def record_passing_spike(self, drv, run_id, ref):
        """Persist sealed generic historical evidence without public ingress or a workload."""
        from application import generic_spike
        from application.location import decode_handle
        from application.ports import ObservationSnapshot
        from core import spike_plan
        from core.evaluation import plan_spike_request, plan_spike_result
        from core.freshness import FileObservation, ObservationState, observations_digest
        from core.spike_plan_draft import canonicalize_spike_plan_draft
        from observer_store import _generic_spike_action, _GenericFixtureHarness

        path = ref.split(":")[0]
        drv.workspace_write(path, b"v1")
        action = _generic_spike_action("C0", [path])
        coordinator = drv._service._coordinator
        key = decode_handle(run_id)
        self.assertIsNotNone(key)
        command = {"type": "ObserveAction", "run_id": run_id, "action": action}
        loaded = coordinator._load(key)
        snapshot = coordinator._assemble(key, loaded.state, command, require_graph=True)
        backend = generic_spike.GenericQualifiedBackend(
            "docker", "docker-isolated-v1", "sha256:" + "a" * 64,
            "sha256:" + "b" * 64)
        sealed = generic_spike.prepare_generic_spike(
            canonicalize_spike_plan_draft(action["plan"]), drv._workspace,
            coordinator.image_resolver, backend)
        request = plan_spike_request(
            snapshot, action, generic_spike.sealed_request_facts(sealed, backend))
        self.assertEqual(request.result_type, "Allow")
        state, _, _, _ = coordinator._commit(
            key, loaded.read.revision, snapshot, loaded.state, request.intent.artifacts)

        request_body = request.intent.artifacts[0]["body"]
        harness_run_id = "r-" + request.intent.artifacts[0]["artifact_id"].split(":", 1)[1][:32]
        harness_request = generic_spike.GenericHarnessRequest(
            harness_run_id, backend, sealed.plan, sealed.plan.digest,
            spike_plan.FRAMEWORK_SPIKE_POLICY.digest, sealed.snapshot)
        result = _GenericFixtureHarness(0).run(harness_request)
        observations = tuple(FileObservation(
            binding.path, ObservationState.PRESENT, binding.sha256)
            for binding in sealed.snapshot.file_bindings)
        observed = ObservationSnapshot(
            observations, sealed.snapshot.basis_id, observations_digest(observations))
        loaded = coordinator._load(key)
        snapshot = coordinator._assemble(key, state, command, require_graph=True)
        planned = plan_spike_result(
            snapshot, request_body,
            generic_spike.sealed_result_facts(result))
        self.assertEqual(planned.result_type, "Allow")
        coordinator._commit(
            key, loaded.read.revision, snapshot, state, planned.intent.artifacts,
            additional_observations=(("spike_execution", observed),))
        checked = coordinator._load(key)
        coordinator._assemble(
            key, checked.state, {"type": "GetRun", "run_id": run_id},
            require_graph=False)

    def change_file(self, drv, ref):
        drv.workspace_write(ref.split(":")[0], b"v2")


class SpellingIsRefusedNotInterpreted(MediatorFixture):
    """m1: a path whose spelling could hide where it points is refused by the core, with no filesystem."""

    def test_D_dot_segments_double_slashes_and_trailing_slashes_are_source_ref_form(self):
        drv, run_id = self.start()
        for ref in ("/srv/other/../work/notes.md", "//srv/work/notes.md", "/srv/work/./notes.md",
                    "/srv/elsewhere/notes.md/", "~/../notes.md", "/srv/a//b.md"):
            with self.subTest(ref):
                refused = self.research(drv, run_id, "code", ref, citation="not in the file")
                self.assertEqual(refused["type"], "Block")
                self.assertEqual(refused["reasons"][0]["parameters"]["problem"], "source_ref_form")

    def test_D_a_colon_inside_a_path_is_part_of_the_path_and_the_whole_path_is_checked(self):
        drv, run_id = self.start()
        for ref in ("/srv/other:folder/../work/notes.md", "/srv/other:folder//notes.md", "/srv/elsewhere/file:word/",
                    "/srv/a:b/./c.md:12"):
            with self.subTest(ref):
                refused = self.research(drv, run_id, "code", ref, citation="not in the file")
                self.assertEqual(refused["type"], "Block")
                self.assertEqual(refused["reasons"][0]["parameters"]["problem"], "source_ref_form")

    def test_D_a_malformed_or_ambiguous_selector_suffix_is_refused(self):
        drv, run_id = self.start()
        for ref in ("/srv/elsewhere/notes.md:", "/srv/elsewhere/notes.md:12-", "/srv/elsewhere/notes.md:1,,2",
                    "/srv/elsewhere/notes.md:,3", "/srv/elsewhere/notes.md:-4"):
            with self.subTest(ref):
                refused = self.research(drv, run_id, "code", ref)
                self.assertEqual(refused["reasons"][0]["parameters"]["problem"], "source_ref_form")

    def test_a_colon_containing_canonical_path_and_every_documented_selector_form_are_accepted(self):
        drv, run_id = self.start()
        for ref in ("/srv/other:folder/notes.md", "/srv/other:folder/notes.md:12", "/srv/elsewhere/notes.md",
                    "/srv/elsewhere/notes.md:12", "/srv/elsewhere/notes.md:12-40",
                    "/srv/elsewhere/notes.md:12-40,55-60", "/srv/elsewhere/notes.md:3,7", "~/notes/x.md:3"):
            with self.subTest(ref):
                self.assertEqual(self.research(drv, run_id, "code", ref)["type"], "Allow", ref)

    def test_one_parser_serves_the_core_and_the_runtime_path_check(self):
        from core import citations
        from application.host_runtime import _inside
        self.assertEqual(citations.split_selector("/a:b/c.md:12-40,55"), ("/a:b/c.md", "12-40,55"))
        self.assertEqual(citations.split_selector("/a:b/c.md"), ("/a:b/c.md", None))
        self.assertIsNone(citations.split_selector("/a/b.md:"))
        self.assertFalse(_inside("/p/r/a:meta/../../elsewhere", "/p/r"))
        self.assertTrue(_inside("/p/r/a:meta/b", "/p/r"))

    def test_a_canonical_absolute_path_outside_the_workspace_is_still_accepted(self):
        drv, run_id = self.start()
        self.assertEqual(self.research(drv, run_id, "code", "/srv/elsewhere/notes.md")["type"], "Allow")
        self.assertEqual(self.research(drv, run_id, "code", "~/notes/x.md:3")["type"], "Allow")

    def test_the_runtime_policy_check_shares_the_rule(self):
        from application.host_runtime import _inside
        self.assertTrue(_inside("/p/r/a/b", "/p/r"))
        for spelled in ("/p/r/./a", "//p/r/a", "/p/r/a/", "/p/r/a/../b"):
            self.assertFalse(_inside(spelled, "/p/r"), spelled)


class TheResponseAfterAPresentationIsFresh(MediatorFixture):
    """m2: any admitted presentation is followed by a re-evaluation, whatever the human did."""

    def returned(self, drv, run_id, human):
        tools = PublicTools(CLAUDE_PROFILE, dispatch=lambda req, _p: drv.request(req),
                            confirm=self.mediator(drv, human))
        return tools.call_internal("report_convergence", {"run_id": run_id})["structuredContent"]

    def test_D_later_cancel_timeout_and_a_failing_dialog_return_the_spent_state(self):
        def failing(_message, _schema):
            raise RuntimeError("the client went away")
        for label, reply in (("later", Human(accept(d1="later"))), ("cancel", Human({"action": "cancel"})),
                             ("timeout", Human(None)), ("dialog error", failing)):
            with self.subTest(label):
                drv, run_id = self.start()
                self.research(drv, run_id, "web", URL)
                got = self.returned(drv, run_id, reply)
                self.assertEqual(got["run"]["confirmation_batch"]["prompt_error"], "already_presented")
                self.assertEqual(self.codes(got), [CONFIRMATION])
