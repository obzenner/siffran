#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from check_claude_mcp_log import check_init_result, check_log  # noqa: E402


class ClaudeMcpLogTests(unittest.TestCase):
    def _log(self, messages: list[tuple[str, str]]) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "mcp.jsonl"
        path.write_text(
            "".join(json.dumps({kind: message}) + "\n" for kind, message in messages),
            encoding="utf-8",
        )
        return path

    def _json(self, payload: object) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "claude-result.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    @staticmethod
    def _connection(version: str = "3.1.5") -> list[tuple[str, str]]:
        capabilities = json.dumps(
            {
                "hasTools": True,
                "serverVersion": {"name": "empirica", "version": version},
            },
            separators=(",", ":"),
        )
        return [
            ("debug", "Successfully connected (transport: stdio) in 100ms"),
            ("debug", f"Connection established with capabilities: {capabilities}"),
        ]

    def test_accepts_clean_connection_and_required_calls(self):
        messages = self._connection()
        for tool in ("empirica_read", "empirica_observe", "report_convergence"):
            messages.extend(
                [
                    ("debug", f"Calling MCP tool: {tool}"),
                    ("debug", f"Tool '{tool}' completed successfully in 1ms"),
                ]
            )
        errors = check_log(
            self._log(messages),
            required_calls=("empirica_read", "empirica_observe", "report_convergence"),
            expected_version="3.1.5",
        )
        self.assertEqual(errors, [])

    def test_rejects_selective_schema_skip_at_any_log_level(self):
        for level in ("error", "debug", "warning"):
            with self.subTest(level=level):
                path = self._log(
                    self._connection("3.1.4")
                    + [(level, 'Skipping tool "empirica_read": top-level allOf is not accepted.')]
                )
                errors = check_log(path)
                self.assertTrue(any("Skipping tool" in error for error in errors))

    def test_metadata_paths_do_not_trigger_rejection_markers(self):
        messages = self._connection()
        path = self._log(messages)
        records = [json.loads(line) for line in path.read_text().splitlines()]
        for record in records:
            record["cwd"] = "/tmp/excluded-input-schema-lab"
            record["sessionId"] = "connection-failed-session"
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        self.assertEqual(check_log(path), [])

    def test_accepts_init_result_with_all_required_tools(self):
        tools = [
            f"mcp__plugin_empirica_empirica__{name}"
            for name in ("empirica_read", "empirica_observe", "report_convergence")
        ]
        payload = [
            {"type": "system", "subtype": "init", "tools": tools},
            {"type": "result", "is_error": True, "result": "authentication failed"},
        ]
        self.assertEqual(check_init_result(self._json(payload)), [])

    def test_rejects_init_result_missing_required_tool(self):
        payload = [{
            "type": "system",
            "subtype": "init",
            "tools": [
                "mcp__plugin_empirica_empirica__empirica_observe",
                "mcp__plugin_empirica_empirica__report_convergence",
            ],
        }]
        errors = check_init_result(self._json(payload))
        self.assertTrue(any("empirica_read" in error for error in errors))

    def test_rejects_missing_connection_capability_and_call_evidence(self):
        errors = check_log(
            self._log([("debug", "Starting connection")]),
            required_calls=("empirica_read",),
            expected_version="3.1.5",
        )
        text = "\n".join(errors)
        self.assertIn("no successful stdio connection", text)
        self.assertIn("no established MCP capability record", text)
        self.assertIn("no native call observed", text)
        self.assertIn("no successful native completion", text)

    def test_rejects_malformed_records(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "bad.jsonl"
        path.write_text("not-json\n[]\n", encoding="utf-8")
        errors = check_log(path)
        self.assertTrue(any("invalid JSON" in error for error in errors))
        self.assertTrue(any("record must be an object" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
