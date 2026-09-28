#!/usr/bin/env python3
"""Regression tests for scripts/empirica_context_economy.py (QUAL-1 D6 counter)."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import empirica_context_economy as ce  # noqa: E402


def _result(text: str, *, structured: dict | None = None) -> dict:
    result: dict = {"content": [{"type": "text", "text": text}]}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def _row(tool: str, text: str, **kw) -> str:
    return json.dumps({"tool": tool, "result": _result(text, **kw)})


class MeasureTests(unittest.TestCase):
    def test_counts_model_visible_chars_and_ignores_non_empirica_tools(self):
        calls = ce.parse_calls([
            _row("empirica_read", "abc"),          # 3 chars
            _row("empirica_observe", "hello!"),    # 6 chars
            _row("some_other_tool", "x" * 999),    # ignored
            _row("report_convergence", "done"),    # 4 chars
        ])
        report = ce.measure(calls)
        self.assertEqual(report["empirica_calls"], 3)
        self.assertEqual(report["max_chars"], 6)
        self.assertEqual(report["total_chars"], 13)

    def test_counts_unicode_code_points_not_bytes(self):
        # A 3 code-point string that is more than 3 UTF-8 bytes.
        report = ce.measure(ce.parse_calls([_row("empirica_read", "\u00e9\u00e9\U0001f600")]))
        self.assertEqual(report["max_chars"], 3)

    def test_normalized_envelope_bytes_tracked_separately_from_model_visible_chars(self):
        row = _row("empirica_read", "hi", structured={"run": {"id": "r", "big": "z" * 50}})
        report = ce.measure(ce.parse_calls([row]))
        self.assertEqual(report["max_chars"], 2)
        # The normalized envelope carries the duplicated structuredContent and is much larger.
        self.assertGreater(report["max_normalized_envelope_bytes"], report["max_chars"])
        self.assertGreaterEqual(report["max_normalized_envelope_bytes"], 50)
        # The separated byte figure is named "normalized envelope bytes" in the rendered output.
        self.assertIn("Normalized envelope bytes", ce.render(report))

    def test_concatenates_multiple_text_content_items(self):
        result = {"content": [{"type": "text", "text": "ab"},
                              {"type": "image", "data": "..."},
                              {"type": "text", "text": "cde"}]}
        calls = ce.parse_calls([json.dumps({"tool": "empirica_read", "result": result})])
        self.assertEqual(ce.measure(calls)["total_chars"], 5)

    def test_empty_slice_reports_zero(self):
        report = ce.measure(ce.parse_calls([]))
        self.assertEqual((report["max_chars"], report["total_chars"], report["empirica_calls"]),
                         (0, 0, 0))

    def test_rejects_malformed_rows(self):
        with self.assertRaises(ce.TranscriptError):
            ce.parse_calls(["not json"])
        with self.assertRaises(ce.TranscriptError):
            ce.parse_calls([json.dumps({"tool": "empirica_read"})])  # no result
        with self.assertRaises(ce.TranscriptError):
            ce.measure(ce.parse_calls([json.dumps({"tool": "empirica_read", "result": {"content": 3}})]))

    def test_empirica_row_with_missing_or_malformed_text_is_an_error_not_zero(self):
        # An Empirica tool result carrying no text content is an error, not a silent zero.
        with self.assertRaises(ce.TranscriptError):
            ce.measure(ce.parse_calls([json.dumps({"tool": "empirica_read",
                                                    "result": {"content": []}})]))
        with self.assertRaises(ce.TranscriptError):
            ce.measure(ce.parse_calls([json.dumps({"tool": "empirica_observe",
                "result": {"content": [{"type": "image", "data": "x"}]}})]))
        # Malformed (non-string) text on a text content item is an error.
        with self.assertRaises(ce.TranscriptError):
            ce.measure(ce.parse_calls([json.dumps({"tool": "report_convergence",
                "result": {"content": [{"type": "text", "text": 123}]}})]))


class CliTests(unittest.TestCase):
    def _write(self, rows: list[str]) -> str:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        tmp.write("\n".join(rows) + "\n")
        tmp.close()
        return tmp.name

    def test_max_chars_ceiling_fails_closed(self):
        path = self._write([_row("empirica_read", "x" * 40)])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ce.main([path, "--max-chars", "10"])
        self.assertEqual(rc, 1)
        self.assertIn("exceeds ceiling", buf.getvalue())

    def test_under_ceiling_passes(self):
        path = self._write([_row("empirica_read", "x" * 5)])
        with redirect_stdout(io.StringIO()):
            rc = ce.main([path, "--max-chars", "10"])
        self.assertEqual(rc, 0)

    def test_invalid_file_returns_two(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(ce.main(["/nonexistent/path.jsonl"]), 2)


if __name__ == "__main__":
    unittest.main()
