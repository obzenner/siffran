#!/usr/bin/env python3
"""Empirica 7.2 E7: the ``environment`` and ``qualify`` author actions' admission (spec §5).

The ``environment`` action admits one closed proposal as a new revision after configuration
approval and investigation admission (never while a configuration is pending), refuses a profile
above the StartRun-bound host ceiling with ``profile-not-allowed``, supersedes one still-pending
revision, and — where the configuration or delegation provenance says auto — derives the approval
itself, fabricating no receipt. The ``qualify`` action is admissible only for the latest approved
revision with attempts remaining; its host effect is a later node's (E9), so E7 only decides
admission: an inadmissible qualify names its closed reason and an admissible one still refuses
with the one closed reason ``environment.not_available``, changing nothing
<plugins/../doc/design/specs/empirica-7.2-run-scoped-environment-qualification.md> (§4.4, §5).

No contract value is restated as a literal in a test: the action samples are the public contract's
own bootstrap examples, every emitted reason row is compared with the registry row that owns it,
and the receipt shape belongs to the contract conformance harness (registered as
``environmentDecisionPayload``); the deliberative receipt behaviour itself lives in
``test_environment_governance.py``.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application import protocol
from application.protocol import host_profile_id
from application.v2 import compose
from core.evaluation import ENVIRONMENT_NOT_AVAILABLE
from governance_setup import TEST_INVOCATION, approve_current
from test_d7_transactions import Artifacts, Harness, Runs, Workspace

PROFILE = host_profile_id("pi")
_V2 = Path(__file__).resolve().parents[3] / "contracts/empirica/v2"
_PUBLIC_CONTRACT = json.loads((_V2 / "public-contract.json").read_text(encoding="utf-8"))
_REQUEST_SCHEMA = json.loads((_V2 / "request.schema.json").read_text(encoding="utf-8"))
_GRAPH = {"root": "C0", "claims": [{"id": "C0", "text": "supplied environment uncertainty",
                                   "gating": True, "kind": "ordinary"}], "edges": []}

# The action samples are the public contract's own bootstrap examples (validated against the
# request schema by scripts/validate_contracts.py), never a second hand-written shape.
ENVIRONMENT_ACTION = copy.deepcopy(_PUBLIC_CONTRACT["bootstrap"]["actions"]["environment"]["example"])
QUALIFY_ACTION = copy.deepcopy(_PUBLIC_CONTRACT["bootstrap"]["actions"]["qualify"]["example"])
#: The profile ceiling vocabulary is the wire's own enum (StartRun.spike_profile): the other
#: legal profile of the pair is read from it, never restated as a literal.
_SPIKE_PROFILES = _REQUEST_SCHEMA["$defs"]["startRun"]["properties"]["spike_profile"]["enum"]
_NETWORKED = next(profile for profile in _SPIKE_PROFILES
                  if profile != _PUBLIC_CONTRACT["bootstrap"]["actions"]["environment"]["example"][
                      "payload"]["profile_id"])


def _request(command: dict) -> dict:
    return {"protocol": "empirica/v2", "request_id": "environment-actions-test", "command": command}


def _networked_payload() -> dict:
    """The example body under the other legal profile of the wire's own enum (never a literal)."""
    payload = copy.deepcopy(ENVIRONMENT_ACTION["payload"])
    payload["profile_id"] = next(profile for profile in _SPIKE_PROFILES
                                  if profile != payload["profile_id"])
    return payload


class ServiceEnv:
    """One real composed service per test, as the governance tests build it."""

    def __init__(self, control_mode="deliberative", spike_profile=None):
        self.service = compose(Workspace(), Harness(), Runs(), Artifacts(), None, PROFILE, {}, None)
        self.run_id = None
        self.control_mode = control_mode
        self.spike_profile = spike_profile

    def start(self):
        command = {"type": "StartRun", "control_mode": self.control_mode,
                   "goal": "Qualify the run's environment from supplied context.",
                   "invocation": dict(TEST_INVOCATION),
                   "selector": {"project": "p", "session": "s"}}
        if self.spike_profile is not None:
            # StartRun's own member is the one wire word for the ceiling (§4.4).
            command["spike_profile"] = self.spike_profile
        result = self.request(command)
        assert result["type"] == "Allow", result
        self.run_id = result["run"]["id"]
        return self

    def request(self, command: dict):
        return self.service.dispatch(_request(command))["result"]

    def observe(self, action: dict):
        return self.request({"type": "ObserveAction", "run_id": self.run_id, "action": action})

    def view(self) -> dict:
        return copy.deepcopy(self.request({"type": "GetRun", "run_id": self.run_id})["run"])

    def state(self):
        """The core's last committed state: the run view does not project the environment member."""
        return self.service._coordinator.last_state

    def fully_approved(self):
        """Graph selected, configuration approved, route and investigation recorded."""
        self.observe({"kind": "graph", "payload": _GRAPH})
        approve_current(self.service._coordinator, self.run_id)
        self.observe({"kind": "route", "reason": "primary environment qualification route"})
        self.observe({"kind": "investigate"})
        return self


class ReasonRow(unittest.TestCase):
    """One emitted Block row is exactly the registry row that owns it."""

    def _block(self, env: ServiceEnv, action: dict, code: str) -> dict:
        result = env.observe(action)
        self.assertTrue(protocol.validate_public_result(result), result)
        self.assertEqual(result["type"], "Block", result)
        codes = [row["code"] for row in result["reasons"]]
        self.assertEqual(codes, [code], result)
        reason = result["reasons"][0]
        self.assertTrue(protocol.validate_public_result(result), result)
        row = _PUBLIC_CONTRACT["reasons"][code]
        self.assertEqual(reason["sections"], row["sections"], result)
        self.assertEqual(reason["next_actions"], row["next_actions"], result)
        self.assertIn(row["message"], reason["message"])
        return reason

    def _changed_nothing(self, env: ServiceEnv, before: dict):
        self.assertEqual(before, env.view())


class EnvironmentOrderingTests(ReasonRow):
    """The step sits between configuration approval and the first spike, order R1 (spec §5)."""

    def test_a_fresh_run_refuses_the_proposal_on_its_pending_configuration(self):
        env = ServiceEnv().start()
        self._block(env, ENVIRONMENT_ACTION, "governance.approval_required")
        before = env.view()
        self._changed_nothing(env, before)

    def test_a_fresh_run_names_the_qualify_reason_for_no_approved_revision(self):
        env = ServiceEnv().start()
        self._block(env, QUALIFY_ACTION, "environment-required")
        self.assertEqual(env.state().environment, None)

    def test_an_approved_run_without_investigation_cannot_submit_yet(self):
        env = ServiceEnv().start()
        env.observe({"kind": "graph", "payload": _GRAPH})
        approve_current(env.service._coordinator, env.run_id)
        self._block(env, ENVIRONMENT_ACTION, "investigation.required")
        self.assertEqual(env.state().environment, None)

    def test_a_terminal_run_stays_inert_not_refused(self):
        """A terminal run keeps its terminal refusal closure: every observation is Inert, never an
        environment reason (the closed refusals are active-run admission facts)."""
        env = ServiceEnv().start()
        stopped = env.request({"type": "EvaluateRun", "run_id": env.run_id, "intent": "stop"})
        self.assertEqual(stopped["type"], "Allow", stopped)
        for action in (ENVIRONMENT_ACTION, QUALIFY_ACTION):
            result = env.observe(copy.deepcopy(action))
            self.assertTrue(protocol.validate_public_result(result), result)
            self.assertEqual(result["type"], "Inert", result)


class EnvironmentAdmissionTests(ReasonRow):
    """An admissible run records revisions; the ceiling and the modes decide admission."""

    def test_a_deliberative_run_records_its_first_revision_pending(self):
        env = ServiceEnv().start().fully_approved()
        result = env.observe(copy.deepcopy(ENVIRONMENT_ACTION))
        self.assertTrue(protocol.validate_public_result(result), result)
        self.assertEqual(result["type"], "Allow", result)
        member = env.state().environment
        self.assertEqual(member["revision"], 1)
        self.assertEqual(member["state"], "pending")
        self.assertIsNone(member["receipt_id"])
        self.assertEqual(env.state().environment_history, ())

    def test_each_submission_is_a_new_revision_that_supersedes_one_still_pending(self):
        env = ServiceEnv().start().fully_approved()
        first = env.observe(copy.deepcopy(ENVIRONMENT_ACTION))
        self.assertEqual(first["type"], "Allow", first)
        member_one = dict(env.state().environment)
        second_payload = copy.deepcopy(ENVIRONMENT_ACTION["payload"])
        second_payload["rationale"] = "a second, revised preparation rationale"
        second = env.observe({**copy.deepcopy(ENVIRONMENT_ACTION), "payload": second_payload})
        self.assertEqual(second["type"], "Allow", second)
        member_two = dict(env.state().environment)
        self.assertEqual(member_two["revision"], 2)
        self.assertEqual(member_two["state"], "pending")
        history = env.state().environment_history
        self.assertEqual([row["revision"] for row in history], [member_one["revision"]])
        self.assertEqual(dict(history[0]), member_one)

    def test_an_a_b_a_sequence_is_three_revisions(self):
        """Approving A once never approves its replay: the same body comes back as revision 3."""
        env = ServiceEnv().start().fully_approved()
        env.observe(copy.deepcopy(ENVIRONMENT_ACTION))
        digest_a = env.state().environment["digest"]
        other = {**copy.deepcopy(ENVIRONMENT_ACTION), "payload": _networked_payload()}
        self._block(env, other, "profile-not-allowed")
        revised_payload = copy.deepcopy(ENVIRONMENT_ACTION["payload"])
        revised_payload["rationale"] = "a second, revised preparation rationale"
        env.observe({**copy.deepcopy(ENVIRONMENT_ACTION), "payload": revised_payload})
        env.observe(copy.deepcopy(ENVIRONMENT_ACTION))
        member = env.state().environment
        self.assertEqual(member["revision"], 3)
        self.assertEqual(member["digest"], digest_a)
        self.assertEqual(member["state"], "pending")
        self.assertEqual([row["revision"] for row in env.state().environment_history], [1, 2])

    def test_a_networked_profile_over_the_isolated_ceiling_is_refused(self):
        """Absent StartRun.spike_profile is the isolated ceiling (§4.4): a networked body is above it."""
        env = ServiceEnv().start().fully_approved()
        before = env.view()
        action = {**copy.deepcopy(ENVIRONMENT_ACTION), "payload": _networked_payload()}
        self._block(env, action, "profile-not-allowed")
        self._changed_nothing(env, before)
        self.assertEqual(env.state().environment, None)

    def test_a_networked_ceiling_at_start_run_admits_a_networked_body(self):
        env = ServiceEnv(spike_profile=_NETWORKED).start().fully_approved()
        result = env.observe({**copy.deepcopy(ENVIRONMENT_ACTION),
                              "payload": _networked_payload()})
        self.assertTrue(result["type"] == "Allow", result)
        member = env.state().environment
        self.assertEqual(member["revision"], 1)

    def test_an_auto_run_derives_the_approval_and_fabricates_no_receipt(self):
        """Under auto provenance the core approves the revision at submission: no receipt exists."""
        env = ServiceEnv(control_mode="auto").start().fully_approved()
        result = env.observe(copy.deepcopy(ENVIRONMENT_ACTION))
        self.assertEqual(result["type"], "Allow", result)
        member = env.state().environment
        self.assertEqual(member["state"], "approved")
        self.assertIsNone(member["receipt_id"])

    def test_a_malformed_payload_still_never_reaches_the_core(self):
        """A payload outside the closed proposal body is rejected at ingress: no environment Block,
        no recorded revision, no view mutation — the core never sees the action."""
        env = ServiceEnv().start()
        before = env.view()
        payload = copy.deepcopy(ENVIRONMENT_ACTION["payload"])
        payload["profile_id"] = f"{payload['profile_id']}-unlisted"
        rejected = {**copy.deepcopy(ENVIRONMENT_ACTION), "payload": payload}
        result = env.observe(rejected)
        self.assertTrue(protocol.validate_public_result(result), result)
        self.assertEqual(result["type"], "Fault", result)
        self.assertNotIn("reason", result, result)
        self.assertEqual(before, env.view())


class QualifyAdmissionTests(ReasonRow):
    """The qualify action decides admission only; its effect is a later node's (E9)."""

    def test_a_pending_revision_blocks_qualify_on_the_human(self):
        env = ServiceEnv().start().fully_approved()
        env.observe(copy.deepcopy(ENVIRONMENT_ACTION))
        before = env.view()
        self._block(env, QUALIFY_ACTION, "environment-pending")
        self._changed_nothing(env, before)

    def test_with_no_revision_ever_the_reason_names_the_proposal_step(self):
        env = ServiceEnv().start()  # not even approved: the qualify decision needs no bootstrap
        self._block(env, QUALIFY_ACTION, "environment-required")

    def test_an_approved_revision_admits_but_the_effect_still_refuses_honestly(self):
        """E7 owns the decision; E9 owns the attempt: an admissible qualify still refuses with the
        one closed reason, and nothing is attempted, charged or bound."""
        env = ServiceEnv(control_mode="auto").start().fully_approved()
        env.observe(copy.deepcopy(ENVIRONMENT_ACTION))
        self.assertEqual(env.state().environment["state"], "approved")
        reason = self._block(env, QUALIFY_ACTION, ENVIRONMENT_NOT_AVAILABLE)
        self.assertEqual(reason["parameters"], {})


if __name__ == "__main__":
    unittest.main()
