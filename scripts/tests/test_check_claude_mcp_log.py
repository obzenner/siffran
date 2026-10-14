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

    OWN = 'MCP server "plugin:empirica:empirica": '  # Claude names the server in every line it logs about it

    @classmethod
    def _connection(cls, version: str = "3.1.5") -> list[tuple[str, str]]:
        capabilities = json.dumps(
            {
                "hasTools": True,
                "serverVersion": {"name": "empirica", "version": version},
            },
            separators=(",", ":"),
        )
        return [
            ("debug", cls.OWN + "Successfully connected (transport: stdio) in 100ms"),
            ("debug", cls.OWN + f"Connection established with capabilities: {capabilities}"),
        ]

    def test_accepts_clean_connection_and_required_calls(self):
        messages = self._connection()
        for tool in ("empirica_read", "empirica_observe", "report_convergence"):
            messages.extend(
                [
                    ("debug", self.OWN + f"Calling MCP tool: {tool}"),
                    ("debug", self.OWN + f"Tool '{tool}' completed successfully in 1ms"),
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


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ClaudeTextDebugLogTests(unittest.TestCase):
    """Claude Code 2.1.291 writes ``--debug-file`` as text lines, not JSON. The fixture is a redacted excerpt of a
    real 2.1.291 log (the unrelated GitHub server fails in it, as it did in the qualified session)."""

    FIXTURE = FIXTURES / "claude-2.1.291-debug-excerpt.log"
    ALL = ("empirica_read", "empirica_observe", "report_convergence")

    def _copy(self, text: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "claude.debug.log"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_real_2_1_291_log_is_accepted_with_its_calls(self):
        self.assertEqual(check_log(self.FIXTURE, required_calls=self.ALL, expected_version="5.0.0"), [])

    def test_another_servers_connection_failure_is_not_empiricas(self):
        text = self.FIXTURE.read_text()
        self.assertIn("plugin:github:github", text)
        self.assertIn("Connection failed", text)
        self.assertEqual(check_log(self.FIXTURE), [])

    def test_the_text_log_still_rejects_what_it_should(self):
        text = self.FIXTURE.read_text()
        cases = {
            "no connection": "\n".join(line for line in text.splitlines() if "Successfully connected" not in line),
            "no capabilities": "\n".join(line for line in text.splitlines() if "capabilities" not in line),
            "empirica failed": text + '2026-10-07T11:30:00.000Z [ERROR] MCP server "plugin:empirica:empirica" Connection failed (1)\n',
            "tool skipped": text + '2026-10-07T11:30:00.000Z [WARN] Skipping tool "empirica_read": input schema rejected\n',
        }
        for name, broken in cases.items():
            with self.subTest(name):
                self.assertNotEqual(check_log(self._copy(broken)), [], name)

    def test_an_error_entry_about_empirica_is_rejected_as_in_the_json_format(self):
        """The level the log gives an entry is kept: an ERROR about Empirica's server fails the check even when its
        text is none of the known rejection markers, exactly like the equivalent JSON record."""
        message = 'MCP server "plugin:empirica:empirica": Unexpected EOF during tools/list'
        source = self.FIXTURE.read_text()
        text = self._copy(source + f"2026-10-07T11:30:00.000Z [ERROR] {message}\n")
        self.assertEqual(check_log(text), [f"{text}: Claude MCP error: {message}"])
        json_log = self._copy("".join(json.dumps({"debug": line.split("] ", 1)[1]}) + "\n"
                                      for line in source.splitlines() if "plugin:empirica:empirica" in line)
                              + json.dumps({"error": message}) + "\n")
        self.assertEqual(check_log(json_log), [f"{json_log}: Claude MCP error: {message}"])

    def test_the_same_message_below_error_level_or_about_another_server_is_not_an_error(self):
        source = self.FIXTURE.read_text()
        for entry in ('[DEBUG] MCP server "plugin:empirica:empirica": Unexpected EOF during tools/list',
                      '[ERROR] MCP server "plugin:github:github": Unexpected EOF during tools/list'):
            with self.subTest(entry):
                self.assertEqual(check_log(self._copy(source + f"2026-10-07T11:30:00.000Z {entry}\n")), [])

    def test_a_missing_call_and_a_wrong_version_are_named(self):
        errors = check_log(self.FIXTURE, required_calls=("empirica_read",), expected_version="9.9.9")
        self.assertEqual(errors, [f"{self.FIXTURE}: server version is not 9.9.9"])
        text = "\n".join(line for line in self.FIXTURE.read_text().splitlines() if "report_convergence" not in line)
        errors = check_log(self._copy(text), required_calls=("report_convergence",))
        self.assertEqual(len(errors), 2)

    def test_the_json_lines_form_applies_the_same_scope_rule(self):
        """Another server's failure is not Empirica's, in either log format."""
        source = self.FIXTURE.read_text()
        good = "".join(json.dumps({"debug": line.split("] ", 1)[1]}) + "\n"
                       for line in source.splitlines() if "plugin:empirica:empirica" in line)
        other = json.dumps({"debug": 'MCP server "plugin:github:github": Connection failed (1)'}) + "\n" \
            + json.dumps({"error": 'MCP server "plugin:github:github": Unexpected EOF'}) + "\n"
        self.assertEqual(check_log(self._copy(good + other)), [])
        own = json.dumps({"debug": 'MCP server "plugin:empirica:empirica": Connection failed (1)'}) + "\n"
        self.assertNotEqual(check_log(self._copy(good + own)), [])

    def test_only_empiricas_own_server_can_satisfy_the_positive_checks(self):
        """Another server that connected, advertised tools and completed the required calls admits nothing for Empirica,
        in the JSON-lines form as in the text form."""
        source = self.FIXTURE.read_text().replace("plugin:empirica:empirica", "plugin:github:github")
        entries = [line.split("] ", 1)[1] for line in source.splitlines() if "plugin:github:github" in line]
        json_log = "".join(json.dumps({"debug": entry}) + "\n" for entry in entries)
        for name, log in (("json", json_log), ("text", source)):
            with self.subTest(name):
                errors = check_log(self._copy(log), required_calls=self.ALL, expected_version="5.0.0")
                joined = " ".join(errors)
                for expected in ("no successful stdio connection", "no established MCP capability record",
                                 "no native call observed for empirica_read",
                                 "no successful native completion observed for empirica_observe"):
                    self.assertIn(expected, joined, name)

    def test_a_continuation_line_joins_its_entry(self):
        text = self.FIXTURE.read_text() + '2026-10-07T11:30:00.000Z [DEBUG] MCP server "plugin:empirica:empirica": note\nSkipping tool "empirica_read"\n'
        self.assertTrue(any("Skipping tool" in e for e in check_log(self._copy(text))))

    def test_text_that_is_not_a_debug_log_is_refused(self):
        errors = check_log(self._copy("hello\nworld\n"))
        self.assertTrue(any("invalid JSON" in e for e in errors))
        errors = check_log(self._copy("2026-10-07T11:16:58.031Z [DEBUG] hello\n"))
        self.assertIn("no successful stdio connection", " ".join(errors))


if __name__ == "__main__":
    unittest.main()
