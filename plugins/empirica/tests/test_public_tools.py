#!/usr/bin/env python3
"""Red-first contract for the shared model-callable Empirica v2 public tools."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import jsonschema

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))


class PublicToolContractTests(unittest.TestCase):
    def _tools(self):
        from adapters.public_tools import PublicTools

        self.requests: list[tuple[dict, str]] = []

        def dispatch(request: dict, profile_id: str) -> dict:
            self.requests.append((request, profile_id))
            return {
                "protocol": "empirica/v2",
                "request_id": request["request_id"],
                "result": {"type": "Inert", "reason": "no_run"},
            }

        return PublicTools("claude-code@2.1.278", dispatch=dispatch)

    def test_definitions_expose_only_public_read_observe_and_report(self):
        tools = self._tools()
        definitions = tools.definitions()
        self.assertEqual(
            [item["name"] for item in definitions],
            ["empirica_read", "empirica_observe", "report_convergence"],
        )
        text = repr(definitions)
        for private in (
            "evidence_leaf",
            "attribution",
            "child_event",
            "audit_verdict",
            "capability_ref",
        ):
            self.assertNotIn(private, text)
        self.assertTrue(definitions[0]["annotations"]["readOnlyHint"])
        self.assertFalse(definitions[1]["annotations"]["readOnlyHint"])

    def test_generated_guidance_and_examples_are_canonical(self):
        from adapters import public_tools
        from application import protocol

        artifact = json.loads(public_tools._PUBLIC_TOOL_ARTIFACT.read_text())
        self.assertEqual(artifact["definitions"], protocol._PUBLIC_CONTRACT["bootstrap"]["tools"])
        self.assertEqual(artifact["bootstrap_actions"],
                         protocol._PUBLIC_CONTRACT["bootstrap"]["actions"])
        observe = public_tools._PUBLIC_SCHEMAS["model"]["empirica_observe"]
        variants = {row["properties"]["kind"]["const"]: row
                    for row in observe["properties"]["action"]["oneOf"]}
        for kind, row in artifact["bootstrap_actions"].items():
            self.assertEqual(row["operation"], kind)
            self.assertEqual(row["example"]["kind"], kind)
            self.assertEqual(variants[kind]["description"], row["description"])
            self.assertEqual(variants[kind]["examples"], [row["example"]])
            jsonschema.validate({"run_id": "r", "action": row["example"]}, observe)
        self.assertIn("requires a selected graph",
                      artifact["definitions"]["empirica_observe"]["description"])

    def test_definitions_satisfy_claude_code_schema_admission(self):
        definitions = self._tools().definitions()
        property_name = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
        for definition in definitions:
            schema = definition["inputSchema"]
            jsonschema.Draft202012Validator.check_schema(schema)
            self.assertTrue(
                {"allOf", "anyOf", "oneOf"}.isdisjoint(schema),
                definition["name"],
            )
            for name, field in schema["properties"].items():
                self.assertRegex(name, property_name)
                self.assertTrue(field.get("description"), (definition["name"], name))
            self.assertNotEqual(definition["title"], definition["description"])
            self.assertGreaterEqual(len(definition["description"].split(". ")), 4)
            self.assertLessEqual(len(definition["description"]), 2048)

    def test_host_handle_read_schema_also_avoids_root_combinators(self):
        from adapters.public_tools import _PUBLIC_SCHEMAS

        schema = _PUBLIC_SCHEMAS["host_handle"]["empirica_read"]
        self.assertTrue({"allOf", "anyOf", "oneOf"}.isdisjoint(schema))
        self.assertNotIn("run_id", schema["properties"])
        jsonschema.Draft202012Validator.check_schema(schema)

    def test_observe_wraps_one_canonical_author_action(self):
        tools = self._tools()
        result = tools.call(
            "empirica_observe",
            {"run_id": "er2:opaque:checksum", "action": {"kind": "route", "reason": "unknowns"}},
        )
        self.assertFalse(result["isError"])
        request, profile = self.requests[-1]
        self.assertEqual(profile, "claude-code@2.1.278")
        self.assertEqual(request["protocol"], "empirica/v2")
        self.assertEqual(
            request["command"],
            {
                "type": "ObserveAction",
                "run_id": "er2:opaque:checksum",
                "action": {"kind": "route", "reason": "unknowns"},
            },
        )

    def test_host_and_trusted_actions_are_rejected_before_dispatch(self):
        tools = self._tools()
        for action in (
            {"kind": "child_reserve", "purpose": "audit", "role_profile": "forged",
             "execution": "foreground"},
            {"kind": "evidence_leaf", "trusted": {}},
            {"kind": "attribution", "trusted": {}},
            {"kind": "child_event", "trusted": {}},
            {"kind": "audit_verdict", "trusted": {}},
        ):
            result = tools.call(
                "empirica_observe", {"run_id": "r", "action": action},
            )
            self.assertTrue(result["isError"], action["kind"])
        self.assertEqual(self.requests, [])

    def test_research_requires_locator_and_citation_before_dispatch(self):
        tools = self._tools()
        base = {"kind": "research", "claim_id": "G0", "source_kind": "code",
                "result": "supports"}
        for payload in (None, {"source_ref": "src/a.py"}, {"citation": "Observed line."}):
            action = dict(base)
            if payload is not None:
                action["payload"] = payload
            result = tools.call("empirica_observe", {"run_id": "r", "action": action})
            self.assertTrue(result["isError"], payload)
        self.assertEqual(self.requests, [])

        payload = {"source_ref": "src/a.py:1", "citation": "Observed line.",
                   "observed_content_digest": "sha256:" + "a" * 64}
        result = tools.call("empirica_observe", {
            "run_id": "r", "action": {**base, "payload": payload}})
        self.assertFalse(result["isError"])
        self.assertEqual(self.requests[-1][0]["command"]["action"]["payload"], payload)

    def test_dispatch_failure_is_a_tool_error_not_an_exception(self):
        from adapters.public_tools import PublicTools

        tools = PublicTools(
            "claude-code@2.1.278",
            dispatch=lambda _request, _profile: (_ for _ in ()).throw(OSError("down")),
        )
        result = tools.call("empirica_read", {"run_id": "r", "operation": "GetRun"})
        self.assertTrue(result["isError"])
        self.assertIn("unavailable", result["content"][0]["text"].lower())

    def test_read_and_report_build_exact_commands(self):
        tools = self._tools()
        tools.call("empirica_read", {"run_id": "r", "operation": "GetRun"})
        self.assertEqual(self.requests[-1][0]["command"], {"type": "GetRun", "run_id": "r"})
        tools.call("empirica_read", {"operation": "GetContract", "target": "index"})
        self.assertEqual(
            self.requests[-1][0]["command"],
            {"type": "GetContract", "target": "index"},
        )
        tools.call(
            "empirica_read",
            {"operation": "GetContract", "target": "section", "section_id": "workflow"},
        )
        self.assertEqual(
            self.requests[-1][0]["command"],
            {"type": "GetContract", "target": "section", "section_id": "workflow"},
        )
        tools.call("report_convergence", {"run_id": "r"})
        self.assertEqual(
            self.requests[-1][0]["command"],
            {"type": "EvaluateRun", "run_id": "r", "intent": "report_convergence"},
        )
        tools.call("report_convergence", {"run_id": "r", "intent": "stop"})
        self.assertEqual(
            self.requests[-1][0]["command"],
            {"type": "EvaluateRun", "run_id": "r", "intent": "stop"},
        )

    def test_read_conditional_requirements_return_actionable_errors(self):
        tools = self._tools()
        cases = (
            ({"operation": "GetRun"}, "GetRun requires run_id"),
            ({"operation": "GetArgument"}, "GetArgument requires run_id"),
            ({"operation": "RestoreRun"}, "RestoreRun requires run_id"),
            ({"operation": "GetContract"}, "GetContract requires target"),
            (
                {"operation": "GetContract", "target": "section"},
                "target=section requires section_id",
            ),
        )
        for arguments, message in cases:
            with self.subTest(arguments=arguments):
                result = tools.call("empirica_read", arguments)
                self.assertTrue(result["isError"])
                self.assertIn(message, result["content"][0]["text"])
        self.assertEqual(self.requests, [])

    def test_caller_cannot_supply_protocol_profile_or_request_id(self):
        tools = self._tools()
        for extra in ("protocol", "profile_id", "request_id"):
            result = tools.call(
                "empirica_read",
                {"run_id": "r", "operation": "GetRun", extra: "forged"},
            )
            self.assertTrue(result["isError"], extra)
        self.assertEqual(self.requests, [])


class McpTransportTests(unittest.TestCase):
    @staticmethod
    def _tools():
        from adapters.public_tools import PublicTools

        return PublicTools(
            "claude-code@2.1.278",
            dispatch=lambda request, _profile: {
                "protocol": "empirica/v2",
                "request_id": request["request_id"],
                "result": {"type": "Inert", "reason": "no_run"},
            },
        )

    @staticmethod
    def _initialize(version: str = "2025-11-25"):
        return {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1"},
            },
        }

    def test_profile_is_bound_by_host_environment(self):
        from adapters.mcp_server import profile_from_environment

        self.assertEqual(
            profile_from_environment({"CLAUDE_PLUGIN_ROOT": "/plugin"}),
            "claude-code@2.1.278",
        )
        self.assertEqual(
            profile_from_environment({"PLUGIN_ROOT": "/plugin"}),
            "codex-cli@0.146.0",
        )
        with self.assertRaises(ValueError):
            profile_from_environment({})

    def test_initialize_uses_the_latest_revision_supported_by_claude_code(self):
        from adapters.mcp_server import handle_message

        exact = handle_message(self._initialize(), self._tools())["result"]
        self.assertEqual(exact["protocolVersion"], "2025-11-25")
        self.assertEqual(exact["capabilities"], {"tools": {"listChanged": False}})

        fallback = handle_message(self._initialize("1900-01-01"), self._tools())["result"]
        self.assertEqual(fallback["protocolVersion"], "2025-11-25")

        invalid = handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            self._tools(),
        )
        self.assertEqual(invalid["error"]["code"], -32602)

    def test_mcp_lists_the_same_three_public_tools(self):
        from adapters.mcp_server import handle_message

        response = handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            self._tools(),
        )
        self.assertEqual(
            [item["name"] for item in response["result"]["tools"]],
            ["empirica_read", "empirica_observe", "report_convergence"],
        )

    def test_plugin_copy_starts_without_repository_root_contracts(self):
        with tempfile.TemporaryDirectory() as temp:
            isolated = Path(temp) / "empirica"
            shutil.copytree(PLUGIN, isolated)
            requests = [
                self._initialize(),
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            ]
            env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(isolated)}
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, str(isolated / "adapters/mcp_server.py")],
                input="".join(json.dumps(row) + "\n" for row in requests),
                text=True, capture_output=True, cwd=temp, env=env, check=True,
            )
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(len(responses[1]["result"]["tools"]), 3)


if __name__ == "__main__":
    unittest.main()
