#!/usr/bin/env python3
"""Deterministic Claude adapter conformance with injected native hook observations."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[3]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from adapters.claude import lifecycle  # noqa: E402
from adapters.claude.run_start import dispatch_start_run  # noqa: E402
from adapters.confirmation import HostConfirmation  # noqa: E402
from adapters.public_tools import PublicTools  # noqa: E402
from adapters.governance import HostGovernance  # noqa: E402
from application.protocol import host_profile_id  # noqa: E402
CLAUDE_PROFILE = host_profile_id("claude-code")


class ClaudeReachabilityTests(unittest.TestCase):
    def test_real_service_blank_goal_is_operator_visible(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            payload = {"session_id": "blank", "cwd": str(root),
                       "command_name": "empirica:empirica", "command_args": ""}
            output = io.StringIO()
            with patch.dict(os.environ, {"EMPIRICA_HOME": str(root / "state"),
                                         "EMPIRICA_REPO_DIR": str(root)}, clear=False), \
                 patch.object(lifecycle, "_payload", return_value=payload), redirect_stdout(output):
                self.assertEqual(lifecycle.run_start_main(), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["decision"], "block")
            self.assertRegex(result["reason"], r"non-empty goal is required")

    def test_real_public_tools_and_native_audit_hooks_converge(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "probe.py").write_text("print('reachable')\n", encoding="utf-8")
            parent_transcript = root / "parent.jsonl"
            parent_transcript.write_text(json.dumps({"message": {"role": "assistant",
                "model": "claude-sonnet-5", "content": "author"}}) + "\n")
            env = {
                "EMPIRICA_HOME": str(root / "state"),
                "EMPIRICA_REPO_DIR": str(root),
            }
            payload = {
                "session_id": "claude-reachability",
                "cwd": str(root),
                "transcript_path": str(parent_transcript),
                "command_name": "empirica:empirica",
                "command_args": "prove the Claude host path",
                "model": "claude-sonnet-5",
            }
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, env, clear=False):
                    started = dispatch_start_run(payload, environ={})
                    run_id = started["result"]["run"]["id"]
                    lifecycle._governance_context(payload, run_id)
                    mediator = HostGovernance(CLAUDE_PROFILE, elicit=lambda _m, _s: {
                        "action": "accept", "content": {}})
                    tools = PublicTools(CLAUDE_PROFILE, govern=mediator)

                    def observe(action: dict) -> dict:
                        result = tools.call_internal("empirica_observe", {
                            "run_id": run_id, "action": action,
                        })
                        self.assertFalse(result["isError"], result)
                        return result["structuredContent"]

                    observe({"kind": "route", "reason": "route first"})
                    observe({"kind": "graph", "payload": {
                        "root": "G0",
                        "claims": [{"id": "G0", "text": "Claude can drive v2.",
                                    "gating": True, "kind": "needs-experiment"}],
                        "edges": [],
                    }})
                    approved = observe({"kind": "configure_run",
                        "budgets": {"max_passes": 8, "max_spawns": 1,
                                    "max_audit_spawns": 2},
                        "rationale": "sized for one experimental claim and an audit retry"})
                    self.assertEqual(approved["run"]["governance"]["state"], "approved")
                    observe({"kind": "investigate"})
                    observe({"kind": "research", "claim_id": "G0",
                             "source_kind": "code", "result": "supports",
                             "payload": {"source_ref": "probe.py",
                                         "citation": "print('reachable')"}})
                    observe({"kind": "spike_request", "claim_id": "G0",
                             "command": "python3 probe.py",
                             "dependent_files": ["probe.py"]})
                    observe({"kind": "freeze"})

                    agent_payload = {
                        **payload,
                        "tool_use_id": "claude-audit-native-1",
                        "tool_name": "Agent",
                        "tool_input": {
                            "subagent_type": "empirica:empirica-auditor",
                            "prompt": "author text must be replaced",
                        },
                    }
                    output = io.StringIO()
                    with patch.object(lifecycle, "_payload", return_value=agent_payload), \
                         redirect_stdout(output):
                        self.assertEqual(lifecycle.spawn_main(), 0)
                    updated = json.loads(output.getvalue())
                    self.assertIn("AUDIT DOSSIER (UNTRUSTED EVIDENCE CONTENT)",
                                  updated["hookSpecificOutput"]["updatedInput"]["prompt"])
                    self.assertNotIn("author text must be replaced",
                                     updated["hookSpecificOutput"]["updatedInput"]["prompt"])

                    native_id = "claude-native-auditor-1"
                    start_payload = {
                        **payload, "agent_type": "empirica:empirica-auditor",
                        "agent_id": native_id,
                    }
                    with patch.object(lifecycle, "_payload", return_value=start_payload):
                        self.assertEqual(lifecycle.subagent_start_main(), 0)

                    pending_output = io.StringIO()
                    with patch.object(lifecycle, "_payload", return_value=payload), \
                         redirect_stdout(pending_output):
                        self.assertEqual(lifecycle.completion_main(), 0)
                    pending = pending_output.getvalue()
                    self.assertIn("Block active", pending)
                    self.assertIn("audit.pending", pending)

                    restore_output = io.StringIO()
                    with patch.object(lifecycle, "_payload", return_value=payload), \
                         redirect_stdout(restore_output):
                        self.assertEqual(lifecycle.restore_main(), 0)
                    restored = tools.call_internal("empirica_read", {
                        "run_id": run_id, "operation": "GetRun",
                    })["structuredContent"]
                    audit_children = [child for child in restored["run"]["children"]
                                      if child["resource_class"] == "audit"]
                    self.assertEqual([child["state"] for child in audit_children], ["pending"])

                    argument = tools.call_internal("empirica_read", {
                        "run_id": run_id, "operation": "GetArgument",
                    })["structuredContent"]["argument"]
                    verdict = {
                        "verdict": "pass",
                        "findings": ["Claude positive host trace"],
                        "argument_digest": argument["argument_digest"],
                        "goal_digest": argument["goal_digest"],
                        "frozen_scope_digest": argument["frozen_scope_digest"],
                        "deferred_scope_digest": argument["deferred_scope_digest"],
                        "reviewed_claims": [
                            {"claim_id": claim["claim_id"],
                             "evidence_digest": claim["evidence_digest"]}
                            for claim in argument["claims"]
                            if claim["gating"] and claim["state"] == "approved"
                        ],
                        "scope_review": "pass",
                    }
                    final_text = "```empirica-verdict\n" + json.dumps(verdict) + "\n```"
                    parent_transcript = root / "parent.jsonl"
                    child_transcript = root / "child.jsonl"
                    parent_transcript.write_text(json.dumps({"message": {
                        "role": "assistant", "model": "claude-sonnet-5", "content": "author"}}) + "\n")
                    child_transcript.write_text(json.dumps({"message": {
                        "role": "assistant", "model": "claude-opus-4-8",
                        "content": [{"type": "text", "text": final_text}]}}) + "\n")
                    stop_payload = {
                        **payload,
                        "agent_type": "empirica:empirica-auditor",
                        "agent_id": native_id,
                        "transcript_path": str(parent_transcript),
                        "agent_transcript_path": str(child_transcript),
                        "last_assistant_message": final_text,
                    }
                    with patch.object(lifecycle, "_payload", return_value=stop_payload):
                        self.assertEqual(lifecycle.subagent_stop_main(), 0)

                    final = tools.call_internal("report_convergence", {"run_id": run_id})
                    self.assertFalse(final["isError"], final)
                    self.assertEqual(final["structuredContent"]["type"], "Allow")
                    self.assertTrue(final["structuredContent"]["converged"])
                    self.assertEqual(final["structuredContent"]["run"]["status"], "converged")
            finally:
                os.chdir(old_cwd)


class ClaudeAuditLaunchOrderTests(unittest.TestCase):
    """ADR-0067 (F-Q4-1): ``PreToolUse:Agent`` reserves the audit only after the confirmation the view puts first, unless
    nobody can be asked. The real service, the real hook and the real confirmation mediator."""

    def run_to_the_q4_shape(self, root: Path, *, human: bool, author: str = "claude-sonnet-5"):
        """An ordinary claim approved by a verbatim workspace record, a web record that needs a human, nothing audited."""
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "probe.py").write_text("print('reachable')\n", encoding="utf-8")
        transcript = root / "parent.jsonl"
        transcript.write_text(json.dumps({"message": {"role": "assistant", "model": author,
                                                      "content": "author"}}) + "\n")
        payload = {"session_id": "claude-q4", "cwd": str(root), "transcript_path": str(transcript),
                   "command_name": "empirica:empirica", "command_args": "audit order", "model": author}
        started = dispatch_start_run(payload, environ={})
        run_id = started["result"]["run"]["id"]
        lifecycle._governance_context(payload, run_id)
        self.answers: list[dict] = []

        def human_says(_message, schema):
            self.answers.append(schema)
            return {"action": "accept", "content": {"d1": "confirm"}}

        confirmer = HostConfirmation(CLAUDE_PROFILE, elicit=human_says if human else None, timeout=lambda: 900)
        tools = PublicTools(CLAUDE_PROFILE, confirm=confirmer,
                            govern=HostGovernance(CLAUDE_PROFILE, elicit=lambda _m, _s: {"action": "accept", "content": {}}))

        def observe(action: dict) -> dict:
            result = tools.call_internal("empirica_observe", {"run_id": run_id, "action": action})
            self.assertFalse(result["isError"], result)
            return result["structuredContent"]

        observe({"kind": "route", "reason": "route first"})
        observe({"kind": "graph", "payload": {"root": "G0", "claims": [
            {"id": "G0", "text": "The probe prints.", "gating": True, "kind": "ordinary"}], "edges": []}})
        observe({"kind": "configure_run", "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2},
                 "rationale": "one claim and an audit"})
        observe({"kind": "investigate"})
        for kind, ref in (("code", "probe.py"), ("web", "https://example.test/spec")):
            observe({"kind": "research", "claim_id": "G0", "source_kind": kind, "result": "supports",
                     "payload": {"source_ref": ref, "citation": "print('reachable')"}})
        observe({"kind": "freeze"})
        agent = {**payload, "tool_use_id": "claude-audit-q4", "tool_name": "Agent",
                 "tool_input": {"subagent_type": "empirica:empirica-auditor", "prompt": "audit"}}
        return tools, run_id, agent

    def launch(self, agent: dict) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with patch.object(lifecycle, "_payload", return_value=agent), redirect_stdout(out), \
                patch("sys.stderr", new=err):
            code = lifecycle.spawn_main()
        return code, out.getvalue() + err.getvalue()

    def test_the_audit_launch_is_denied_while_a_human_is_about_to_be_asked(self):
        with tempfile.TemporaryDirectory() as raw, patch.dict(
                os.environ, {"EMPIRICA_HOME": str(Path(raw) / "state"), "EMPIRICA_REPO_DIR": raw}, clear=False):
            old = Path.cwd()
            os.chdir(raw)
            try:
                tools, run_id, agent = self.run_to_the_q4_shape(Path(raw).resolve(), human=True)
                code, said = self.launch(agent)
                self.assertEqual(code, 2, said)
                self.assertIn("confirm", said.lower())
                view = tools.call_internal("empirica_read", {"run_id": run_id, "operation": "GetRun"})["structuredContent"]
                self.assertEqual([c for c in view["run"]["children"] if c["resource_class"] == "audit"], [])
                # The report asks the human (the dialog), the answer is recorded, and the audit is then admitted.
                tools.call_internal("report_convergence", {"run_id": run_id})
                self.assertEqual(len(self.answers), 1)
                code, said = self.launch(agent)
                self.assertEqual(code, 0, said)
            finally:
                os.chdir(old)

    def test_the_audit_launch_is_admitted_when_nobody_can_be_asked(self):
        with tempfile.TemporaryDirectory() as raw, patch.dict(
                os.environ, {"EMPIRICA_HOME": str(Path(raw) / "state"), "EMPIRICA_REPO_DIR": raw}, clear=False):
            old = Path.cwd()
            os.chdir(raw)
            try:
                tools, run_id, agent = self.run_to_the_q4_shape(Path(raw).resolve(), human=False)
                tools.call_internal("report_convergence", {"run_id": run_id})  # looks for a surface, finds none
                code, said = self.launch(agent)
                self.assertEqual(code, 0, said)
            finally:
                os.chdir(old)

    def test_the_auditor_differs_from_the_earlier_producer_and_the_switched_author_through_the_real_bridge(self):
        """G15 F-P2-1, Claude, no stubs: evidence by fable, then the author switches to opus (the pi-2 shape). The real
        `exclusions()` read through the bridge gives the core's set {fable, opus}; the default alias is outside both (an author-only
        check would pick fable, the first family), and an explicit CLAUDE_CODE_SUBAGENT_MODEL in the set is refused before any spawn."""
        with tempfile.TemporaryDirectory() as raw:
            env = {"EMPIRICA_HOME": str(Path(raw) / "state"), "EMPIRICA_REPO_DIR": raw}
            with patch.dict(os.environ, env, clear=False):
                for key in ("CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                            "CLAUDE_CODE_USE_FOUNDRY"):
                    os.environ.pop(key, None)
                old = Path.cwd()
                os.chdir(raw)
                try:
                    tools, run_id, agent = self.run_to_the_q4_shape(Path(raw).resolve(), human=False, author="claude-fable-1")
                    tools.call_internal("report_convergence", {"run_id": run_id})  # looks for a surface, finds none
                    Path(agent["transcript_path"]).write_text(json.dumps({"message": {
                        "role": "assistant", "model": "claude-opus-4-8", "content": "author"}}) + "\n")  # /model
                    lifecycle._governance_context(agent, run_id)
                    audits = lambda: [c for c in tools.call_internal("empirica_read", {  # noqa: E731
                        "run_id": run_id, "operation": "GetRun"})["structuredContent"]["run"]["children"]
                        if c["resource_class"] == "audit"]
                    for configured in ("claude-fable-1", "fable", "claude-opus-4-8"):  # the producer, its alias, the author
                        with self.subTest(configured=configured), patch.dict(os.environ, {"CLAUDE_CODE_SUBAGENT_MODEL": configured}):
                            code, said = self.launch(agent)
                            self.assertEqual(code, 2, said)
                            self.assertIn("anthropic/claude-fable-1", said)
                            self.assertIn("anthropic/claude-opus-4-8", said)
                            self.assertEqual(audits(), [], "refused before any spawn")
                    budgets = tools.call_internal("empirica_read", {"run_id": run_id, "operation": "GetRun"}
                                                  )["structuredContent"]["run"]["governance"]["budgets"]
                    self.assertEqual(budgets["audit_spawns_used"], 0)
                    code, said = self.launch(agent)
                    self.assertEqual(code, 0, said)
                    self.assertEqual(json.loads(said)["hookSpecificOutput"]["updatedInput"]["model"], "sonnet")
                    self.assertEqual(len(audits()), 1)
                finally:
                    os.chdir(old)


if __name__ == "__main__":
    unittest.main()
