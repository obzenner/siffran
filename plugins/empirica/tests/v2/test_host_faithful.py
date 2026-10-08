"""Host-faithful order of events (F7): a human surface is recorded where the real host records it, never earlier.

Claude's start hook runs before the MCP client has declared form elicitation, so a Claude run starts with its
surface *unobserved* and the host records it only inside ``report_convergence`` (``HostConfirmation.observe``).
Every other evidence fixture records the surface before the first evaluation, which hid a defect: the author was
told "no human can be asked" before Claude had ever looked for one, and the only next action was ``intent=stop``.

These tests start a run with the provenance Claude's hook really computes, and drive it only through the rendered
author view (``PublicTools``) with a *literal agent*: it reads the ``next:`` actions of the last view, always takes
the first one a tool call can carry out, and does nothing else. The scenarios are indexed in
``contracts/empirica/v2/host-boundaries.json``; [D] marks the cells where the third state "unobserved" makes the
outcome differ from the old two-state model."""
from __future__ import annotations

import json
import re
from unittest.mock import patch

from adapters.author_view import render_author_view
from adapters.claude.completion import stop_result
from adapters.claude.run_start import invocation_provenance
from adapters.claude.transport import Response
from adapters.public_tools import PublicTools
from application.protocol import author_view_labels, host_profile_id
from core import confirmation, governance
from core.confirmation import HOST_FAILURES
from core.projection import project_host_failure
from test_evidence_confirmation import URL, WORKSPACE_REF, graph_with_decision
from test_evidence_hosts import Human, MediatorFixture, accept



CLAUDE = host_profile_id("claude-code")
TOOLS = ("empirica_observe", "empirica_read", "report_convergence")
TERMINAL = re.compile(r"^(?:Allow|Block)[^\n]* (stopped_\w+|converged|failed_\w+)\b")
NO_HUMAN = "no human can be asked"


def claude_invocation(*, entrypoint: str = "cli", delegated: bool = False) -> dict:
    """What the Claude start hook records, computed by the hook's own code."""
    environ = {"CLAUDE_CODE_ENTRYPOINT": entrypoint, **({"EMPIRICA_AUTO_DELEGATION": "1"} if delegated else {})}
    return invocation_provenance({}, environ)


def next_actions(text: str) -> list[str]:
    """Every ``next:`` action of a rendered author view, in reading order."""
    return [action.strip() for line in re.findall(r"^\s*next: (.+)$", text, re.M) for action in line.split("; ")]


def first_actionable(text: str) -> str | None:
    """The first rendered next action a tool call can carry out (host- and human-owned ones are not the agent's)."""
    return next((a for a in next_actions(text) if a.split()[0] in TOOLS), None)


def call_of(action: str, run_id: str) -> tuple[str, dict]:
    tool, *pairs = action.split()
    arguments = dict(pair.split("=", 1) for pair in pairs)
    if tool == "empirica_read":
        arguments.setdefault("operation", "GetRun")
    return tool, {"run_id": run_id, **arguments}


def research_call(run_id: str, claim: str = "C0", ref: str = URL) -> tuple[str, dict]:
    return "empirica_observe", {"run_id": run_id, "action": {
        "kind": "research", "claim_id": claim, "source_kind": "web", "result": "supports",
        "payload": {"source_ref": ref, "citation": "The service contract."}}}


class HostFaithful(MediatorFixture):
    """A Claude session: the real provenance at start, the surface recorded only inside ``report_convergence``."""

    def claude(self, *, form: bool = True, reply=None, graph=None, kind="ordinary", mode="deliberative", **session):
        drv, run_id = self.start(CLAUDE, graph=graph, kind=kind, mode=mode, **claude_invocation(**session))
        self.human = Human(reply or accept(d1="confirm"))
        confirm = self.mediator(drv, self.human if form else None)
        self.tools = PublicTools(CLAUDE, dispatch=lambda request, _p: drv.request(request), confirm=confirm)
        return drv, run_id

    def say(self, call: tuple[str, dict]) -> str:
        return self.tools.call(*call)["content"][0]["text"]

    def literal_agent(self, run_id: str, text: str, steps: int = 6) -> tuple[str, list[str]]:
        """Follow the first actionable ``next:`` of each view. Returns ``(outcome, calls)``: ``presented`` once a human
        was shown a dialog, ``stopped`` at a terminal run, ``no_action`` / ``looping`` when the rendered view gives
        the agent nothing (or the same thing) to do."""
        calls: list[str] = []
        self.views = [text]
        for _ in range(steps):
            if self.human.shown:
                return "presented", calls
            if TERMINAL.match(text):
                return "stopped", calls
            action = first_actionable(text)
            if action is None:
                return "no_action", calls
            if calls and calls[-1] == action and action.startswith("report_convergence intent=report"):
                return "looping", calls
            calls.append(action)
            text = self.say(call_of(action, run_id))
            self.views.append(text)
        return ("presented" if self.human.shown else "looping"), calls

    def after_research(self, run_id: str, **kw) -> str:
        return self.say(research_call(run_id, **kw))


class FaithfulFixture(HostFaithful):
    """The fixture itself must be faithful: Claude's own hook decides what the run starts with."""

    def test_a_claude_run_starts_with_the_surface_its_hook_computes_and_not_a_borrowed_one(self):
        drv, run_id = self.claude()
        recorded = self.run_view(drv, run_id)["invocation"]
        self.assertEqual(recorded, claude_invocation())
        self.assertNotEqual(recorded["human_surface"], "human", "no host has looked for a human at start")

    def test_the_surface_is_recorded_only_inside_report_convergence(self):
        drv, run_id = self.claude()
        before = self.run_view(drv, run_id)["invocation"]["human_surface"]
        self.after_research(run_id)
        self.say(("empirica_read", {"operation": "GetRun", "run_id": run_id}))
        self.assertEqual(self.run_view(drv, run_id)["invocation"]["human_surface"], before)
        self.say(("report_convergence", {"run_id": run_id}))
        self.assertEqual(self.run_view(drv, run_id)["invocation"]["human_surface"], "human")


class ThirdState(HostFaithful):
    """The route of an unobserved surface (Part B rulings)."""

    def route(self, drv, run_id) -> str:
        return self.evaluated(drv, run_id)["reasons"][0]["parameters"]["route"]

    def test_an_interactive_undelegated_run_with_an_ingress_is_unobserved_until_the_host_looks(self):  # [D]
        drv, run_id = self.claude()
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.route(drv, run_id), confirmation.UNOBSERVED)
        self.assertEqual(self.routes(drv, run_id), [confirmation.UNOBSERVED])

    def test_a_delegated_run_is_unavailable_at_once(self):
        drv, run_id = self.claude(delegated=True)
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.route(drv, run_id), "unavailable")

    def test_a_non_interactive_run_is_unavailable_at_once(self):
        drv, run_id = self.claude(entrypoint="sdk-cli")
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.route(drv, run_id), "unavailable")

    def test_a_run_without_a_human_ingress_is_unavailable_at_once(self):
        drv, run_id = self.start(host_profile_id("codex-cli"), mode="auto", audit_retrieval=[], delegation=True,
                                 interactive=False, human_surface="unobserved")
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.route(drv, run_id), "unavailable")

    def test_the_route_is_human_after_the_host_observes_a_surface_and_unavailable_after_it_observes_none(self):
        drv, run_id = self.claude()
        self.research(drv, run_id, "web", URL)
        self.surface(drv, run_id, "human")
        self.assertEqual(self.route(drv, run_id), "human")
        self.surface(drv, run_id, "unavailable")
        self.assertEqual(self.route(drv, run_id), "unavailable")

    def test_an_unobserved_surface_is_not_a_surface_the_host_can_report(self):
        drv, run_id = self.claude()
        self.assertEqual(self.surface(drv, run_id, "unobserved")["type"], "Fault")

    def test_nothing_can_be_presented_before_the_host_has_looked(self):  # [D]
        drv, run_id = self.claude()
        self.research(drv, run_id, "web", URL)
        self.assertEqual(self.batch(drv, run_id)["prompt_error"], "unobserved")
        self.assertEqual(self.present_now(drv, run_id)["type"], "Block")
        self.assertEqual(self.interactions(drv, run_id)["total"], self.left(drv, run_id))

    def test_an_unobserved_run_keeps_the_record_pending_so_nothing_is_approved_early(self):
        drv, run_id = self.claude()
        self.research(drv, run_id, "web", URL)
        self.assertNotEqual(self.claim(drv, run_id)["state"], "approved")


class LiteralAgentOnClaude(HostFaithful):
    """The agent that reads only ``next:``. Where a real human could be asked it must reach the dialog; where nobody
    can be asked it must end in an honest stop."""

    def test_url_evidence_in_an_interactive_session_reaches_the_dialog(self):  # [D] the live defect
        drv, run_id = self.claude()
        view = self.after_research(run_id)
        self.assertNotIn(NO_HUMAN, view, "told nobody can be asked before the host ever looked")
        outcome, calls = self.literal_agent(run_id, view)
        self.assertEqual(outcome, "presented", calls)
        self.assertEqual(calls[0], "report_convergence intent=report_convergence")
        self.assertEqual(len(self.human.shown), 1)

    def test_the_view_before_the_host_looks_says_a_human_will_be_asked_at_the_report(self):  # [D]
        drv, run_id = self.claude()
        view = self.after_research(run_id)
        self.assertIn("a human is asked when you report, if this host can show a dialog", view)
        actions = next_actions(view)
        self.assertLess(actions.index("report_convergence intent=report_convergence"),
                        actions.index("report_convergence intent=stop"))

    def test_a_needs_decision_claim_reaches_the_dialog(self):  # [D]
        drv, run_id = self.claude(graph=graph_with_decision())
        self.research(drv, run_id, "code", WORKSPACE_REF)  # verified by the service: only the decision waits
        view = self.say(("empirica_read", {"operation": "GetRun", "run_id": run_id}))
        outcome, calls = self.literal_agent(run_id, view)
        self.assertEqual(outcome, "presented", calls)

    def test_a_decision_and_a_record_in_one_batch_reach_one_dialog(self):  # [D]
        drv, run_id = self.claude(graph=graph_with_decision(), reply=accept(d1="approve", d2="confirm"))
        view = self.after_research(run_id)  # the URL record on C0 and the decision on C1 wait together
        outcome, calls = self.literal_agent(run_id, view)
        self.assertEqual(outcome, "presented", calls)
        self.assertEqual(len(self.human.shown), 1)
        self.assertIn("approve", self.human.shown[0][0].lower() + str(self.human.shown[0][1]).lower())

    def test_a_client_without_a_form_ends_in_an_honest_stop(self):
        drv, run_id = self.claude(form=False)
        view = self.after_research(run_id)
        self.assertNotIn(NO_HUMAN, view)  # not known yet: the host has not looked
        outcome, calls = self.literal_agent(run_id, view)
        self.assertEqual(outcome, "stopped", calls)
        self.assertEqual(calls[0], "report_convergence intent=report_convergence")  # it looked before it gave up
        self.assertEqual(self.human.shown, [])
        self.assertIn(NO_HUMAN, self.views[1])  # said only once the host had looked and found no form

    def test_a_delegated_run_ends_in_an_honest_stop_without_pretending_to_look(self):
        drv, run_id = self.claude(delegated=True)
        view = self.after_research(run_id)
        self.assertIn(NO_HUMAN, view)
        self.assertNotIn("report_convergence intent=report_convergence", next_actions(view))
        outcome, calls = self.literal_agent(run_id, view)
        self.assertEqual((outcome, calls), ("stopped", ["report_convergence intent=stop"]))
        self.assertEqual(self.human.shown, [])

    def test_a_non_interactive_run_ends_in_an_honest_stop(self):
        drv, run_id = self.claude(entrypoint="sdk-cli")
        view = self.after_research(run_id)
        self.assertIn(NO_HUMAN, view)
        self.assertEqual(self.literal_agent(run_id, view)[0], "stopped")
        self.assertEqual(self.human.shown, [])

    def test_a_later_answer_spends_the_derivation_and_the_agent_stops_instead_of_looping(self):
        drv, run_id = self.claude(reply=accept(d1="later"))
        outcome, calls = self.literal_agent(run_id, self.after_research(run_id))
        self.assertEqual(outcome, "presented", calls)
        view = self.say(("report_convergence", {"run_id": run_id}))
        self.assertIn("already shown", view)
        self.assertNotIn("report_convergence intent=report_convergence", next_actions(view))
        self.assertEqual(len(self.human.shown), 1)

    def test_new_research_after_an_observation_is_asked_again(self):  # a human surface observed earlier
        drv, run_id = self.claude(reply=accept(d1="later"))
        first, _ = self.literal_agent(run_id, self.after_research(run_id))
        self.assertEqual(first, "presented")
        self.human.shown.clear()
        view = self.after_research(run_id, ref="https://example.test/second")
        outcome, calls = self.literal_agent(run_id, view)
        self.assertEqual(outcome, "presented", calls)

    def test_the_surface_going_away_is_followed(self):
        drv, run_id = self.claude(form=False)
        self.surface(drv, run_id, "human")  # an earlier observation
        view = self.after_research(run_id)
        outcome, _ = self.literal_agent(run_id, view)
        self.assertEqual(outcome, "stopped")
        self.assertEqual(self.human.shown, [])


class StopHookLoopOnClaude(HostFaithful):
    """EHB-STOP-16/17 (ADR-0068): the Stop hook loop as Claude runs it, with a literal agent.

    Claude re-runs the Stop hook each time a block is answered by another end of turn, and sets ``stop_hook_active`` on
    those continuations. The agent here does only what the hook's own text tells it to. In Q2 (the operator's checkpoint)
    the hook blocked nine times until Claude overrode it; the loop must now end by itself, by the run's control mode."""

    CAP = 8  # Claude's own override: the hook must end the loop before it matters, and the test fails if it does not

    def hook_loop(self, drv, run_id, agent):
        stops, active = [], False
        for _ in range(self.CAP):
            response = Response.from_envelope({"request_id": "stop"}, {
                "protocol": "empirica/v2", "request_id": "stop", "result": self.evaluated(drv, run_id)})
            stop = stop_result(response, stop_hook_active=active)
            stops.append(stop)
            if stop.exit_code == 0:
                break
            agent(stop.stderr)
            active = True
        return stops

    def test_EHB_STOP_16_a_deliberative_operator_checkpoint_ends_the_loop_in_two_stops(self):
        drv, run_id = self.claude(mode="deliberative")
        stops = self.hook_loop(drv, run_id, lambda text: None)  # the agent stops to wait for the operator
        self.assertLessEqual(len(stops), 2, [s.stderr or s.stdout for s in stops])
        self.assertEqual(stops[0].exit_code, 2)  # the first Stop still blocks, with the real reason
        message = json.loads(stops[-1].stdout)["systemMessage"]
        self.assertEqual(stops[-1].exit_code, 0)
        self.assertRegex(message, r"paused.*not converged|not converged.*paused")
        self.assertIn("nobody was asked", message)
        self.assertIn(stops[0].stderr.strip().rstrip("."), message)  # the real reason, not a stock sentence
        self.assertEqual(self.run_view(drv, run_id)["status"], "active")  # nothing was terminalized or approved by it

    def test_EHB_STOP_16_an_evidence_directive_the_agent_ignores_is_handed_off_not_repeated(self):
        drv, run_id = self.claude(mode="deliberative")
        self.after_research(run_id)
        stops = self.hook_loop(drv, run_id, lambda text: None)
        self.assertEqual(len(stops), 2)
        self.assertIn("report_convergence", stops[0].stderr)  # F7: the first Stop is the directive, never a pause
        self.assertEqual(self.human.shown, [])
        self.assertIn("nobody was asked", json.loads(stops[1].stdout)["systemMessage"])

    def unattended(self, **session):
        drv, run_id = self.claude(mode="auto", **session)
        calls: list[str] = []

        def agent(text):  # the literal agent: take the way out the directive names
            if "intent=stop" in text and calls == []:
                calls.append("report_convergence intent=stop")
                self.say(call_of(calls[-1], run_id))
        stops = self.hook_loop(drv, run_id, agent)
        return drv, run_id, stops, calls

    def test_EHB_STOP_17_unattended_auto_ends_honestly_through_the_directives_intent_stop(self):
        for label, session in (("interactive auto after the first approval", {}), ("delegated auto", {"delegated": True})):
            with self.subTest(label):
                drv, run_id, stops, calls = self.unattended(**session)
                self.assertEqual([s.exit_code for s in stops], [2, 2, 0], [s.stderr or s.stdout for s in stops])
                self.assertEqual(stops[0].stderr.strip(), "Record research for this claim.")  # first Stop: unchanged
                self.assertIn("intent=stop", stops[1].stderr)  # the continuation names the way out
                self.assertIn("Record research for this claim.", stops[1].stderr)
                self.assertEqual(calls, ["report_convergence intent=stop"])
                self.assertTrue(self.run_view(drv, run_id)["status"].startswith("stopped_"), self.run_view(drv, run_id)["status"])
                self.assertEqual(self.human.shown, [])


PI = host_profile_id("pi")


class LiteralAgentOnPi(HostFaithful):
    """Pi's order: ``ctx.hasUI`` is observed when ``/empirica`` starts the run (so its surface is never ``unobserved``),
    and the gate observes it again, evaluates and presents at each ``report_convergence`` (``src/index.ts``). The gate
    itself is exercised by ``adapters/pi/test/gate.test.ts``; here the same core answers a Pi-ordered run."""

    def pi(self, *, has_ui: bool = True, reply=None, **session):
        invocation = {"host": "pi", "interactive": True, "signal": "ctx.mode=tui", "delegation": False,
                      "audit_retrieval": ["local", "workspace"], "human_surface": "human" if has_ui else "unavailable",
                      **session}
        drv, run_id = self.start(PI, **invocation)
        self.human = Human(reply or accept(d1="confirm"))
        confirm = self.mediator(drv, self.human if has_ui else None)
        self.tools = PublicTools(PI, dispatch=lambda request, _p: drv.request(request), confirm=confirm)
        return drv, run_id

    def test_a_pi_run_is_never_unobserved(self):
        for has_ui in (True, False):
            drv, run_id = self.pi(has_ui=has_ui)
            self.research(drv, run_id, "web", URL)
            route = self.evaluated(drv, run_id)["reasons"][0]["parameters"]["route"]
            self.assertEqual(route, "human" if has_ui else "unavailable")

    def test_url_evidence_with_a_ui_reaches_the_dialog_by_following_next(self):  # [D] before the fix: only `stop`
        drv, run_id = self.pi()
        view = self.after_research(run_id)
        self.assertNotIn(NO_HUMAN, view)
        outcome, calls = self.literal_agent(run_id, view)
        self.assertEqual((outcome, calls), ("presented", ["report_convergence intent=report_convergence"]))

    def test_url_evidence_without_a_ui_ends_in_an_honest_stop(self):
        drv, run_id = self.pi(has_ui=False)
        view = self.after_research(run_id)
        self.assertIn(NO_HUMAN, view)
        self.assertEqual(self.literal_agent(run_id, view), ("stopped", ["report_convergence intent=stop"]))
        self.assertEqual(self.human.shown, [])

    def test_a_delegated_pi_run_ends_in_an_honest_stop(self):
        drv, run_id = self.pi(delegation=True)
        view = self.after_research(run_id)
        self.assertEqual(self.literal_agent(run_id, view)[0], "stopped")
        self.assertEqual(self.human.shown, [])

    def test_a_later_answer_does_not_loop(self):
        drv, run_id = self.pi(reply=accept(d1="later"))
        outcome, _ = self.literal_agent(run_id, self.after_research(run_id))
        self.assertEqual(outcome, "presented")
        self.assertNotIn("report_convergence intent=report_convergence",
                         next_actions(self.say(("report_convergence", {"run_id": run_id}))))


FAULT = {"result": {"type": "Fault", "code": "conflict", "fail_direction": "closed"}}
NOTICE = "nobody was asked"
CONFIRMATION = "evidence.confirmation_required"


class HostFailure(HostFaithful):
    """A host that fails to observe its surface or to present the batch says so in the same call.

    Without that, the author's view kept leading with ``report_convergence`` (a human is asked) after a call that
    asked nobody, so a reader that follows ``next:`` repeated the report forever. The failure is injected at the
    private ingresses; the core's state is never touched by it."""

    def inject(self, target, name: str, how: str = "fault", *, when=lambda *_: True) -> None:
        """Make ``target.<name>`` answer a typed Fault (``how="fault"``) or raise (``how="raise"``) for the calls ``when``
        selects (all, by default); every other call goes through unchanged. The one fault injector of this file."""
        original = getattr(target, name)
        calls = []

        def broken(*args):
            calls.append(args)
            if when(len(calls), *args):
                if how == "raise":
                    raise OSError("the core went away")
                return FAULT
            return original(*args)
        setattr(target, name, broken)

    def failing(self, *, surface: bool = False, presentation: bool = False, how: str = "fault"):
        """A Claude run whose private ingress answers a typed Fault (``how="fault"``) or raises (``how="raise"``)."""
        drv, run_id = self.claude()
        for wanted, name in ((surface, "_surface"), (presentation, "_presentation")):
            if wanted:
                self.inject(self.tools._confirm, name, how)
        return drv, run_id

    def spent(self, drv, run_id):
        snapshot = self.snapshot(drv, run_id)
        return (sum(a.get("kind") == "evidence_presentation" for a in snapshot.history),
                self.interactions(drv, run_id)["total"])

    def check(self, **fault):
        for how in ("fault", "raise"):
            with self.subTest(how=how):
                drv, run_id = self.failing(how=how, **fault)
                before = self.spent(drv, run_id)
                outcome, calls = self.literal_agent(run_id, self.after_research(run_id))
                self.assertEqual((outcome, self.human.shown), ("stopped", []), calls)
                self.assertEqual(calls, ["report_convergence intent=report_convergence", "report_convergence intent=stop"])
                self.assertIn(NOTICE, self.views[1])
                self.assertEqual(first_actionable(self.views[1]), "report_convergence intent=stop")
                self.assertNotIn("a human is asked when you report, if this host can show a dialog", self.views[1])
                self.assertEqual(self.spent(drv, run_id), before)  # nothing spent, approved or invented

    def test_a_surface_that_cannot_be_recorded_ends_in_an_honest_stop_within_two_calls(self):
        self.check(surface=True)

    def test_a_presentation_that_fails_ends_in_an_honest_stop_within_two_calls(self):
        self.check(presentation=True)

    def test_both_failing_ends_in_an_honest_stop_within_two_calls(self):
        self.check(surface=True, presentation=True)

    def test_the_core_words_each_failure_and_withholds_the_report_from_every_next_list(self):
        for reason in HOST_FAILURES:
            for looked in (False, True):  # the failure is the same before and after the host has recorded its surface
                with self.subTest(reason=reason, looked=looked):
                    drv, run_id = self.claude()
                    self.research(drv, run_id, "web", URL)
                    if looked:
                        self.look(drv, run_id)
                    before = self.spent(drv, run_id)
                    result = self.evaluated(drv, run_id, reason)
                    self.assertEqual(result["run"]["confirmation_batch"]["prompt_error"], reason)
                    self.assertEqual(self.codes(result), [CONFIRMATION])  # the reason is still the real one
                    view = render_author_view(result)
                    self.assertIn(author_view_labels()[f"waiting_{reason}"], view)
                    self.assertEqual(first_actionable(view), "report_convergence intent=stop")
                    self.assertNotIn("report_convergence intent=report_convergence", next_actions(view))
                    self.assertNotIn("convergence.report", json.dumps(result))
                    self.assertEqual(self.spent(drv, run_id), before)
                    self.assertEqual(self.evaluated(drv, run_id)["run"]["confirmation_batch"]["prompt_error"],
                                     None if looked else "unobserved")  # a fact about that one call, never stored

    def test_what_the_core_already_knows_outranks_what_the_host_reports(self):
        for reason in HOST_FAILURES:
            with self.subTest(reason=reason, case="nobody can be asked"):
                drv, run_id = self.claude(delegated=True)
                self.research(drv, run_id, "web", URL)
                self.assertEqual(self.evaluated(drv, run_id, reason)["run"]["confirmation_batch"]["prompt_error"],
                                 "unavailable")
            with self.subTest(reason=reason, case="already shown"):
                drv, run_id = self.claude()
                self.research(drv, run_id, "web", URL)
                self.look(drv, run_id)
                self.assertEqual(self.present(drv, run_id)["type"], "Allow")
                self.assertEqual(self.evaluated(drv, run_id, reason)["run"]["confirmation_batch"]["prompt_error"],
                                 "already_presented")
            with self.subTest(reason=reason, case="interactions spent"):
                drv, run_id = self.claude()
                with patch.object(governance, "MAX_INTERACTIONS", 1):
                    self.research(drv, run_id, "web", URL)
                    self.assertEqual(self.evaluated(drv, run_id, reason)["run"]["confirmation_batch"]["prompt_error"],
                                     "interaction_limit")

    def test_a_host_failure_is_only_a_value_of_an_evaluation(self):
        drv, run_id = self.claude()
        for command in ({"type": "EvaluateRun", "run_id": run_id, "intent": "report_convergence", "host_failure": "boom"},
                        {"type": "GetRun", "run_id": run_id, "host_failure": "surface_unrecorded"}):
            with self.subTest(command=command["type"]):
                request = {"protocol": "empirica/v2", "request_id": "r1", "command": command}
                self.assertEqual(drv.request(request)["result"]["code"], "invalid_request")

    def test_a_dialog_the_host_could_show_is_not_called_a_failure(self):
        drv, run_id = self.claude(reply=accept(d1="later"))
        self.after_research(run_id)
        view = self.say(("report_convergence", {"run_id": run_id}))
        self.assertNotIn(NOTICE, view)

    def test_the_stop_directive_may_send_one_report_and_that_report_leads_to_stop(self):
        drv, run_id = self.failing(presentation=True)
        self.after_research(run_id)
        first = self.say(("report_convergence", {"run_id": run_id}))   # the one report the Stop directive asks for
        self.assertEqual(first_actionable(first), "report_convergence intent=stop")
        self.assertTrue(TERMINAL.match(self.say(("report_convergence", {"run_id": run_id, "intent": "stop"}))))

    def test_unobserved_and_exhausted_offers_no_report_and_says_the_limit(self):
        drv, run_id = self.claude()
        with patch.object(governance, "MAX_INTERACTIONS", 1):
            view = self.after_research(run_id)
            self.assertEqual(self.interactions(drv, run_id)["total"], 0)
            self.assertEqual(self.batch(drv, run_id)["prompt_error"], "interaction_limit")
            self.assertNotIn("report_convergence intent=report_convergence", next_actions(view))
            self.assertIn("the human interaction limit is reached", view)
            self.assertNotIn("a human is asked when you report, if this host can show a dialog", view)

    # ---- the evaluation that reports the failure fails too ------------------------------

    def break_evaluation_that_reports(self, how: str) -> None:
        """Every evaluation that carries a host failure raises (``raise``) or answers a typed Fault (``fault``); the
        ordinary evaluations and the stop still work."""
        self.inject(self.tools, "_dispatch", how, when=lambda _n, request, _profile: bool(request["command"].get("host_failure")))

    def double_fault(self, how: str):
        drv, run_id = self.failing(presentation=True)
        view = self.after_research(run_id)
        self.break_evaluation_that_reports(how)
        return drv, run_id, view

    def test_a_failed_failure_report_ends_in_an_honest_stop_in_two_calls(self):
        for how in ("raise", "fault"):
            with self.subTest(how=how):
                drv, run_id, view = self.double_fault(how)
                before = self.spent(drv, run_id)
                outcome, calls = self.literal_agent(run_id, view)
                self.assertEqual((outcome, self.human.shown), ("stopped", []), calls)
                self.assertEqual(calls, ["report_convergence intent=report_convergence", "report_convergence intent=stop"])
                self.assertIn(NOTICE, self.views[1])
                self.assertEqual(first_actionable(self.views[1]), "report_convergence intent=stop")
                self.assertEqual(self.spent(drv, run_id), before)

    def test_repeated_default_reports_under_the_double_fault_never_offer_the_report_first(self):
        for how in ("raise", "fault"):
            with self.subTest(how=how):
                drv, run_id, _view = self.double_fault(how)
                for attempt in range(3):
                    view = self.say(("report_convergence", {"run_id": run_id}))
                    self.assertEqual(first_actionable(view), "report_convergence intent=stop", attempt)
                    self.assertIn(NOTICE, view)
                    self.assertNotIn("a human can be asked at the end of your next pass", view)
                self.assertEqual(self.human.shown, [])
                self.assertTrue(TERMINAL.match(self.say(("report_convergence", {"run_id": run_id, "intent": "stop"}))))

    def test_a_failed_failure_report_after_a_surface_failure_is_a_closed_error_not_a_stale_view(self):
        for how in ("raise", "fault"):
            with self.subTest(how=how):
                drv, run_id = self.failing(surface=True)
                self.after_research(run_id)
                self.break_evaluation_that_reports(how)
                reply = self.tools.call("report_convergence", {"run_id": run_id})
                self.assertNotIn("report_convergence intent=report_convergence", reply["content"][0]["text"])
                self.assertEqual(self.human.shown, [])
                self.assertTrue(TERMINAL.match(self.say(("report_convergence", {"run_id": run_id, "intent": "stop"}))))

    def test_the_in_process_projection_is_the_cores_own_response(self):
        """The fallback is one pure function of the core's rule: for each failure, and before and after the host's look, it
        turns the answerable response into exactly what the core answers when told the failure."""
        for reason in HOST_FAILURES:
            for looked in (False, True):
                with self.subTest(reason=reason, looked=looked):
                    drv, run_id = self.claude()
                    self.research(drv, run_id, "web", URL)
                    if looked:
                        self.look(drv, run_id)
                    stale = self.evaluated(drv, run_id)
                    self.assertEqual(project_host_failure(stale, reason), self.evaluated(drv, run_id, reason))

    def test_a_refusal_the_core_knows_is_not_replaced_by_the_hosts_report(self):
        drv, run_id = self.claude()
        self.research(drv, run_id, "web", URL)
        self.look(drv, run_id)
        self.assertEqual(self.present(drv, run_id)["type"], "Allow")
        spent = self.evaluated(drv, run_id)
        self.assertEqual(spent["run"]["confirmation_batch"]["prompt_error"], "already_presented")
        for reason in HOST_FAILURES:
            self.assertEqual(confirmation.apply_host_report("already_presented", reason), "already_presented")
        self.assertEqual(self.evaluated(drv, run_id, "presentation_failed"), spent)
        self.assertEqual(project_host_failure(spent, "presentation_failed"), spent)

    def test_a_shown_batch_whose_reevaluation_fails_is_not_offered_again(self):
        for how in ("raise", "fault"):
            with self.subTest(how=how):
                drv, run_id = self.claude(reply=accept(d1="later"))
                self.after_research(run_id)
                evaluations = []

                def after_the_dialog(_n, request, _profile):
                    if request["command"].get("type") == "EvaluateRun":
                        evaluations.append(request)
                        return len(evaluations) == 2  # the evaluation after the dialog
                    return False
                self.inject(self.tools, "_dispatch", how, when=after_the_dialog)
                view = self.say(("report_convergence", {"run_id": run_id}))
                self.assertEqual(len(self.human.shown), 1)
                self.assertNotEqual(first_actionable(view), "report_convergence intent=report_convergence")
                self.assertIn("already shown", view)

    # ---- the precedence of what the core already knows ------------------------------------

    def test_exhaustion_outranks_unobserved_and_human_but_unavailable_stays_unavailable(self):
        for surface, expected in (("unobserved", "interaction_limit"), ("human", "interaction_limit"),
                                  ("unavailable", "unavailable")):
            with self.subTest(surface=surface):
                drv, run_id = self.claude()
                if surface != "unobserved":
                    self.assertEqual(self.surface(drv, run_id, surface)["type"], "Allow")
                with patch.object(governance, "MAX_INTERACTIONS", 1):
                    view = self.after_research(run_id)
                    self.assertEqual(self.interactions(drv, run_id)["total"], 0)
                    self.assertEqual(self.batch(drv, run_id)["prompt_error"], expected)
                    self.assertNotIn("report_convergence intent=report_convergence", next_actions(view))
                    self.assertEqual(first_actionable(view), "report_convergence intent=stop")

    def test_delegated_and_exhausted_is_unavailable(self):
        drv, run_id = self.claude(delegated=True)
        with patch.object(governance, "MAX_INTERACTIONS", 1):
            self.after_research(run_id)
            self.assertEqual(self.interactions(drv, run_id)["total"], 0)
            self.assertEqual(self.batch(drv, run_id)["prompt_error"], "unavailable")
