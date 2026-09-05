#!/usr/bin/env python3
"""Regression tests for the Codex MCP translation boundary (stdlib only)."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

CODEX_ADAPTER = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = CODEX_ADAPTER.parents[1]
sys.path.insert(0, str(CODEX_ADAPTER))

from mcp_server import TOOL_NAME, handle_message  # noqa: E402


class TestMessages(unittest.TestCase):
    def test_initialize_and_list_expose_one_read_only_tool(self):
        initialized = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25"},
            }
        )
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-11-25")
        listed = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = listed["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], [TOOL_NAME])
        self.assertTrue(tools[0]["annotations"]["readOnlyHint"])

    def test_named_call_returns_core_validated_plan(self):
        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": TOOL_NAME,
                    "arguments": {
                        "methodology": "formal-reasoning",
                        "reason": "test the Codex bridge",
                    },
                },
            }
        )
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["methodology"], "formal-reasoning")
        self.assertEqual(len(result["structuredContent"]["phases"]), 6)

    def test_two_candidates_return_decision_requirement_without_state(self):
        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": TOOL_NAME,
                    "arguments": {
                        "candidates": [
                            {"name": "decomposition", "rationale": "find boundaries"},
                            {"name": "contradiction", "rationale": "choose an option"},
                        ]
                    },
                },
            }
        )
        content = response["result"]["structuredContent"]
        self.assertEqual(content["type"], "HumanDecisionRequired")
        self.assertEqual(len(content["candidates"]), 2)

    def test_unknown_method_is_json_rpc_error(self):
        response = handle_message({"jsonrpc": "2.0", "id": 5, "method": "unknown"})
        self.assertEqual(response["error"]["code"], -32601)


class TestStdio(unittest.TestCase):
    def test_server_round_trips_multiple_json_lines(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        completed = subprocess.run(
            [sys.executable, str(CODEX_ADAPTER / "mcp_server.py")],
            input="".join(json.dumps(item) + "\n" for item in messages),
            text=True,
            capture_output=True,
            cwd=PLUGIN_ROOT,
            timeout=10,
            check=True,
        )
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([item["id"] for item in responses], [1, 2])
        self.assertEqual(responses[1]["result"]["tools"][0]["name"], TOOL_NAME)


if __name__ == "__main__":
    unittest.main()
