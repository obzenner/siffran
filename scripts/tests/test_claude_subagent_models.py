#!/usr/bin/env python3
"""Regression tests for scripts/claude_subagent_models.py (requested vs served subagent models)."""
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

import claude_subagent_models as tool  # noqa: E402


def _assistant(model: str, content: list) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "model": model, "content": content}}


def _agent_call(tool_id: str, subagent: str, model: str | None = None) -> dict:
    payload = {"subagent_type": subagent, "prompt": "p"}
    if model:
        payload["model"] = model
    return {"type": "tool_use", "id": tool_id, "name": "Agent", "input": payload}


class SubagentModelTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / "proj"
        self.project.mkdir()

    def _session(self, name: str, rows: list, children: dict[str, tuple[str, list]]) -> Path:
        session = self.project / f"{name}.jsonl"
        session.write_text("".join(json.dumps(r) + "\n" for r in rows) + "{broken\n", encoding="utf-8")
        sub = self.project / name / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        for agent_id, (tool_use, child_rows) in children.items():
            (sub / f"agent-{agent_id}.meta.json").write_text(
                json.dumps({"toolUseId": tool_use}), encoding="utf-8")
            (sub / f"agent-{agent_id}.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in child_rows), encoding="utf-8")
        return session

    def test_joins_requested_parent_and_served_models(self) -> None:
        session = self._session("s1", [
            _assistant("claude-opus-4-8", [_agent_call("t1", "empirica:empirica-auditor", "sonnet")]),
            _assistant("claude-opus-4-8", [_agent_call("t2", "Explore")]),
        ], {"a1": ("t1", [_assistant("claude-sonnet-5", [{"type": "tool_use", "name": "SubagentHandback",
                                                          "id": "h", "input": {}}])])})
        items = tool.launches(session)
        self.assertEqual(len(items), 2)
        audit = items[0]
        self.assertEqual((audit.requested_model, audit.parent_model, audit.served_models, audit.handback),
                         ("sonnet", "claude-opus-4-8", ["claude-sonnet-5"], True))
        self.assertEqual(items[1].child, "missing")

    def test_mixed_child_models_are_all_reported(self) -> None:
        session = self._session("s2", [_assistant("m-main", [_agent_call("t1", "x")])],
                                {"a1": ("t1", [_assistant("m1", []), _assistant("m2", []),
                                               _assistant("m1", [])])})
        self.assertEqual(tool.launches(session)[0].served_models, ["m1", "m2"])

    def test_filters_and_fixed_depth_scan(self) -> None:
        self._session("s3", [_assistant("m", [_agent_call("t1", "empirica:empirica-auditor"),
                                                _agent_call("t2", "other", "haiku")])], {})
        deep = self.project / "nested" / "deeper.jsonl"
        deep.parent.mkdir()
        deep.write_text(json.dumps(_assistant("m", [_agent_call("t9", "other")])) + "\n", encoding="utf-8")
        found = tool.sessions_under(self.root)
        self.assertEqual([p.name for p in found], ["s3.jsonl"])
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(tool.main(["--projects-root", str(self.root), "--requested-only", "--json"]), 0)
        data = json.loads(out.getvalue())
        self.assertEqual([d["requested_model"] for d in data], ["haiku"])

    def test_missing_explicit_session_is_an_error(self) -> None:
        err = io.StringIO()
        sys_stderr, sys.stderr = sys.stderr, err
        try:
            self.assertEqual(tool.main([str(self.root / "absent.jsonl")]), 2)
        finally:
            sys.stderr = sys_stderr
        self.assertIn("not found", err.getvalue())

    def test_read_only(self) -> None:
        session = self._session("s4", [_assistant("m", [_agent_call("t1", "x")])],
                                {"a1": ("t1", [_assistant("m1", [])])})
        before = {str(p): p.stat().st_mtime_ns for p in self.root.rglob("*")}
        with redirect_stdout(io.StringIO()):
            tool.main([str(session)])
            tool.main(["--projects-root", str(self.root), "--json"])
        self.assertEqual({str(p): p.stat().st_mtime_ns for p in self.root.rglob("*")}, before)


if __name__ == "__main__":
    unittest.main()
