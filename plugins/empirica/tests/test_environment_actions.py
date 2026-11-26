#!/usr/bin/env python3
"""Empirica 7.2 E6a: the ``environment`` and ``qualify`` author actions and their interim refusal.

The two coming actions are declared on the wire (request schema, public contract registry, model
tool projection) with the honest note that they are not available yet: until the run-scoped
environment qualification behaviour lands (E7/E9), the core refuses every ``environment`` and
``qualify`` action with one closed Block reason ``environment.not_available`` in every active run
state — decided before any bootstrap, graph or governance admission fact, with an invalid frozen
scope still blocking first — records nothing, changes no binding, and leaves a terminal run
Inert
<plugins/../doc/design/specs/empirica-7.2-run-scoped-environment-qualification.md> (§4.1, §5).

This module owns only that behaviour; no contract value is restated as a literal. The samples and
the refused reason come from their owners (the public contract's bootstrap guidance and the core
constant), and the emitted reason row is compared with the registry row that owns it. Shape and
bound coverage of the shared proposal body belongs to the contract conformance harness (E2c):
the environment/qualify actions, their payloads and the bootstrap examples are registered there
once the harness lands.
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
_GRAPH = {"root": "C0", "claims": [{"id": "C0", "text": "supplied environment uncertainty",
                                   "gating": True, "kind": "ordinary"}], "edges": []}

# The action samples are the public contract's own bootstrap examples (validated against the
# request schema by scripts/validate_contracts.py), never a second hand-written shape.
ENVIRONMENT_ACTION = copy.deepcopy(_PUBLIC_CONTRACT["bootstrap"]["actions"]["environment"]["example"])
QUALIFY_ACTION = copy.deepcopy(_PUBLIC_CONTRACT["bootstrap"]["actions"]["qualify"]["example"])


def _request(command: dict) -> dict:
    return {"protocol": "empirica/v2", "request_id": "environment-actions-test", "command": command}


class ServiceEnv:
    """One real composed service per test, as the governance tests build it."""

    def __init__(self):
        self.service = compose(Workspace(), Harness(), Runs(), Artifacts(), None, PROFILE, {}, None)
        self.run_id = None

    def start(self):
        result = self.request({"type": "StartRun", "control_mode": "deliberative",
                              "goal": "Qualify the run's environment from supplied context.",
                              "invocation": dict(TEST_INVOCATION),
                              "selector": {"project": "p", "session": "s"}})
        assert result["type"] == "Allow", result
        self.run_id = result["run"]["id"]
        return self

    def request(self, command: dict):
        return self.service.dispatch(_request(command))["result"]

    def observe(self, action: dict):
        return self.request({"type": "ObserveAction", "run_id": self.run_id, "action": action})

    def view(self) -> dict:
        return copy.deepcopy(self.request({"type": "GetRun", "run_id": self.run_id})["run"])

    def fully_approved(self):
        """Graph selected, configuration approved, route and investigation recorded."""
        self.observe({"kind": "graph", "payload": _GRAPH})
        approve_current(self.service._coordinator, self.run_id)
        self.observe({"kind": "route", "reason": "primary environment qualification route"})
        self.observe({"kind": "investigate"})
        return self


class EnvironmentRefusalTests(unittest.TestCase):
    """The core must refuse both actions with one closed reason, not crash or accept them."""

    def _refused_with(self, result: dict) -> dict:
        self.assertTrue(protocol.validate_public_result(result), result)
        self.assertEqual(result["type"], "Block", result)
        codes = [row["code"] for row in result["reasons"]]
        self.assertEqual(codes, [ENVIRONMENT_NOT_AVAILABLE], result)
        reason = result["reasons"][0]
        self.assertEqual(reason["parameters"], {})
        # The emitted row must be exactly the registry row that owns the reason.
        row = _PUBLIC_CONTRACT["reasons"][ENVIRONMENT_NOT_AVAILABLE]
        self.assertEqual(reason["sections"], row["sections"], result)
        self.assertEqual(reason["next_actions"], row["next_actions"], result)
        self.assertIn(row["message"], reason["message"])
        return reason

    def test_refused_before_any_approval_in_a_fresh_run(self):
        env = ServiceEnv().start()
        before = env.view()
        self._refused_with(env.observe(ENVIRONMENT_ACTION))
        self._refused_with(env.observe(QUALIFY_ACTION))
        self.assertEqual(before, env.view())

    def test_refused_the_same_way_once_the_run_is_approved_and_investigating(self):
        env = ServiceEnv().start().fully_approved()
        before = env.view()
        self._refused_with(env.observe(ENVIRONMENT_ACTION))
        self._refused_with(env.observe(QUALIFY_ACTION))
        self.assertEqual(before, env.view())

    def test_a_terminal_run_stays_inert_not_refused(self):
        """A terminal run keeps its terminal refusal closure: every observation is Inert, never the
        environment reason (the closed refusal is an active-run admission fact)."""
        env = ServiceEnv().start()
        stopped = env.request({"type": "EvaluateRun", "run_id": env.run_id, "intent": "stop"})
        self.assertEqual(stopped["type"], "Allow", stopped)
        self.assertEqual(stopped["run"]["status"], "stopped_residual", stopped)
        result = env.observe(ENVIRONMENT_ACTION)
        self.assertTrue(protocol.validate_public_result(result), result)
        self.assertEqual(result["type"], "Inert", result)
        self.assertNotEqual(result["reason"], ENVIRONMENT_NOT_AVAILABLE, result)

    def test_a_malformed_payload_still_never_reaches_the_core(self):
        """A payload outside the closed proposal body is rejected at ingress: no environment
        Block, no recorded revision, no view mutation — the core never sees the action."""
        env = ServiceEnv().start()
        before = env.view()
        payload = copy.deepcopy(ENVIRONMENT_ACTION["payload"])
        # Not one of the closed profile ids: take a valid one and leave the closed set.
        payload["profile_id"] = f"{payload['profile_id']}-unlisted"
        rejected = {**copy.deepcopy(ENVIRONMENT_ACTION), "payload": payload}
        result = env.observe(rejected)
        self.assertTrue(protocol.validate_public_result(result), result)
        self.assertEqual(result["type"], "Fault", result)
        self.assertNotIn("reason", result, result)
        self.assertEqual(before, env.view())


if __name__ == "__main__":
    unittest.main()
