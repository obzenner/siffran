#!/usr/bin/env python3
"""Empirica 7.2 E6a: the ``environment`` and ``qualify`` author actions and their interim refusal.

The two coming actions are declared on the wire (request schema, public contract registry, model
tool projection) with the honest note that they are not available yet: until the run-scoped
environment qualification behaviour lands (E7/E9), the core refuses every ``environment`` and
``qualify`` action with one closed Block reason ``environment.not_available`` in every run
state, records nothing, and changes no binding
<plugins/../doc/design/specs/empirica-7.2-run-scoped-environment-qualification.md> (§4.1, §5).
"""
import copy
import json
import sys
import unittest
from pathlib import Path

import jsonschema


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application import protocol
from application.protocol import host_profile_id
from application.v2 import compose
from core.environment import PROPOSAL_SCHEMA as ENVIRONMENT_PROPOSAL_SCHEMA
from governance_setup import TEST_INVOCATION, approve_current
from test_d7_transactions import Artifacts, Harness, Runs, Workspace

PROFILE = host_profile_id("pi")
_V2 = Path(__file__).resolve().parents[3] / "contracts/empirica/v2"
_REQUEST_SCHEMA = json.loads((_V2 / "request.schema.json").read_text(encoding="utf-8"))
_PUBLIC_CONTRACT = json.loads((_V2 / "public-contract.json").read_text(encoding="utf-8"))
_GRAPH = {"root": "C0", "claims": [{"id": "C0", "text": "supplied environment uncertainty",
                                   "gating": True, "kind": "ordinary"}], "edges": []}

_ENVIRONMENT_PAYLOAD = {
    "profile_id": "docker-isolated-v1",
    "spike_images": ["docker.io/library/alpine:3.22"],
    "preparation": ["Inspect the already-local probe image before qualifying the endpoint."],
    "rationale": "One isolated profile over one local image, sized for a bounded qualification.",
}
ENVIRONMENT_ACTION = {"kind": "environment", "payload": dict(_ENVIRONMENT_PAYLOAD)}
QUALIFY_ACTION = {"kind": "qualify"}


def _request(command: dict) -> dict:
    return {"protocol": "empirica/v2", "request_id": "environment-actions-test", "command": command}


def _observe_request(action: dict) -> dict:
    return _request({"type": "ObserveAction", "run_id": "r1", "action": action})


class EnvironmentWireSchema(unittest.TestCase):
    """The two actions and the closed proposal body: schema-first, before production imports."""

    @staticmethod
    def _valid(action: dict) -> bool:
        return jsonschema.Draft202012Validator(_REQUEST_SCHEMA).is_valid(_observe_request(action))

    def test_the_two_actions_validate_as_author_actions(self):
        for action in (ENVIRONMENT_ACTION, QUALIFY_ACTION):
            with self.subTest(kind=action["kind"]):
                self.assertTrue(self._valid(action))
        jsonschema.validate(_observe_request(QUALIFY_ACTION), _REQUEST_SCHEMA)

    def test_the_payload_bounds_mirror_the_core_environment_model(self):
        for label, payload in (
            ("missing_payload", {k: v for k, v in ENVIRONMENT_ACTION.items() if k != "payload"}),
            ("extra_field", {**ENVIRONMENT_ACTION, "note": "x"}),
            ("body_extra_field", {"kind": "environment", "payload": {
                **_ENVIRONMENT_PAYLOAD, "schema": ENVIRONMENT_PROPOSAL_SCHEMA}}),
            ("unknown_profile", {"kind": "environment", "payload": {
                **_ENVIRONMENT_PAYLOAD, "profile_id": "docker-supervisord-v1"}}),
            ("no_images", {"kind": "environment", "payload": {
                **_ENVIRONMENT_PAYLOAD, "spike_images": []}}),
            ("too_many_images", {"kind": "environment", "payload": {
                **_ENVIRONMENT_PAYLOAD, "spike_images": [f"image-{i}:latest" for i in range(17)]}}),
            ("duplicated_images", {"kind": "environment", "payload": {
                **_ENVIRONMENT_PAYLOAD, "spike_images": ["alpine:3.22", "alpine:3.22"]}}),
            ("whitespace_image", {"kind": "environment", "payload": {
                **_ENVIRONMENT_PAYLOAD, "spike_images": ["alpine: 3.22"]}}),
            ("blank_preparation", {"kind": "environment", "payload": {
                **_ENVIRONMENT_PAYLOAD, "preparation": ["   "]}}),
            ("no_preparation", {"kind": "environment", "payload": {
                **_ENVIRONMENT_PAYLOAD, "preparation": []}}),
            ("too_many_preparation_steps", {"kind": "environment", "payload": {
                **_ENVIRONMENT_PAYLOAD, "preparation": [f"step {i}" for i in range(9)]}}),
            ("too_long_preparation_step", {"kind": "environment", "payload": {
                **_ENVIRONMENT_PAYLOAD, "preparation": ["x" * 201]}}),
            ("blank_rationale", {"kind": "environment", "payload": {
                **_ENVIRONMENT_PAYLOAD, "rationale": "  "}}),
            ("too_long_rationale", {"kind": "environment", "payload": {
                **_ENVIRONMENT_PAYLOAD, "rationale": "x" * 601}}),
            ("qualify_with_payload", {"kind": "qualify", "payload": dict(_ENVIRONMENT_PAYLOAD)}),
        ):
            with self.subTest(case=label):
                self.assertFalse(self._valid(payload), label)


class EnvironmentRegistry(unittest.TestCase):
    """Every registry that claims the two actions and their refusal reason, from the SSOT files."""

    def test_the_public_contract_registers_the_two_author_actions(self):
        for kind in ("environment", "qualify"):
            self.assertIn(kind, _PUBLIC_CONTRACT["actions"]["author"])
            self.assertEqual(_PUBLIC_CONTRACT["actions"]["metadata"][kind]["evidence"], False)

    def test_the_bootstrap_guidance_examples_validate_agains_the_request_schema(self):
        guidance = _PUBLIC_CONTRACT["bootstrap"]["actions"]
        for kind in ("environment", "qualify"):
            self.assertIn(kind, guidance)
            row = guidance[kind]
            self.assertEqual(row["operation"], kind)
            self.assertEqual(row["example"].get("kind"), kind)
            jsonschema.validate(_observe_request(row["example"]), _REQUEST_SCHEMA)

    def test_the_interim_refusal_reason_is_closed_at_its_owner(self):
        registry = _PUBLIC_CONTRACT["reasons"]
        self.assertIn("environment.not_available", registry)
        row = registry["environment.not_available"]
        self.assertTrue(row["next_actions"])
        self.assertIn("environment/actions", row["sections"])
        protocol.schema_validator("response", "reasonPayload").validate(
            {"code": "environment.not_available", "parameters": {}})

    def test_the_reason_is_mirrored_by_the_response_schema(self):
        codes = [branch.get("properties", {}).get("code", {}).get("const")
                 for branch in protocol.response_schema()["$defs"]["reasonPayload"]["oneOf"]]
        self.assertIn("environment.not_available", codes)

    def test_the_getcontract_section_says_the_actions_are_coming(self):
        section = _PUBLIC_CONTRACT["sections"]["environment/actions"]
        text = " ".join(clause["text"] for clause in section["clauses"])
        self.assertIn("environment", text)
        self.assertIn("available yet", text)
        self.assertIn("environment.not_available", text)


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
        self.assertEqual(codes, ["environment.not_available"], result)
        reason = result["reasons"][0]
        self.assertEqual(reason["parameters"], {})
        self.assertIn("environment/actions", reason["sections"])
        self.assertTrue(reason["next_actions"])
        return reason

    def test_refused_before_any_approval_in_a_fresh_run(self):
        env = ServiceEnv().start()
        before = env.view()
        reason = self._refused_with(env.observe(ENVIRONMENT_ACTION))
        self.assertIn("not available yet", reason["message"])
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
        self.assertIn(result["reason"], ("no_run", "unsupported_host_event"))

    def test_a_malformed_payload_still_never_reaches_the_core(self):
        env = ServiceEnv().start()
        rejected = {**copy.deepcopy(ENVIRONMENT_ACTION),
                    "payload": {**_ENVIRONMENT_PAYLOAD, "profile_id": "docker-bogus-v9"}}
        result = env.observe(rejected)
        self.assertEqual(result["type"], "Fault", result)
        self.assertEqual(result["code"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
