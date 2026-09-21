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
from adapters.public_tools import PublicTools  # noqa: E402


class ClaudeReachabilityTests(unittest.TestCase):
    def test_real_public_tools_and_native_audit_hooks_converge(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "probe.py").write_text("print('reachable')\n", encoding="utf-8")
            env = {
                "EMPIRICA_HOME": str(root / "state"),
                "EMPIRICA_REPO_DIR": str(root),
            }
            payload = {
                "session_id": "claude-reachability",
                "cwd": str(root),
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
                    tools = PublicTools("claude-code@2.1.270")

                    def observe(action: dict) -> dict:
                        result = tools.call("empirica_observe", {
                            "run_id": run_id, "action": action,
                        })
                        self.assertFalse(result["isError"], result)
                        return result["structuredContent"]

                    observe({"kind": "route", "reason": "route first"})
                    observe({"kind": "investigate"})
                    observe({"kind": "graph", "payload": {
                        "root": "G0",
                        "claims": [{"id": "G0", "text": "Claude can drive v2.",
                                    "gating": True, "kind": "needs-experiment"}],
                        "edges": [],
                    }})
                    observe({"kind": "research", "claim_id": "G0",
                             "source_kind": "code", "result": "supports",
                             "payload": {"source_ref": "probe.py",
                                         "citation": "The probe executes successfully."}})
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

                    argument = tools.call("empirica_read", {
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

                    final = tools.call("report_convergence", {"run_id": run_id})
                    self.assertFalse(final["isError"], final)
                    self.assertEqual(final["structuredContent"]["type"], "Allow")
                    self.assertTrue(final["structuredContent"]["converged"])
                    self.assertEqual(final["structuredContent"]["run"]["status"], "converged")
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
