"""Production-facing Codex positive path through MCP tools and managed audit."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from adapters.codex.lifecycle import _start, _stop  # noqa: E402
from adapters.public_tools import PublicTools  # noqa: E402
from adapters.governance import HostGovernance  # noqa: E402


class CodexAdapterConformanceTests(unittest.TestCase):
    def test_real_service_blank_goal_is_operator_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            payload = {"cwd": str(repo), "session_id": "blank",
                       "model": "gpt-5.1-codex", "prompt": "$empirica   ",
                       "hook_event_name": "UserPromptSubmit"}
            with patch.dict(os.environ, {"EMPIRICA_HOME": str(repo / "state"),
                                         "EMPIRICA_REPO_DIR": str(repo)}, clear=False):
                result = _start(payload)
            self.assertEqual(result["decision"], "block")
            self.assertRegex(result["reason"], r"non-empty goal is required")

    def test_real_pending_run_exact_tool_names_and_real_slug_identity_limit(self) -> None:
        from adapters.codex.lifecycle import _pre_tool_use
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            payload = {"cwd": str(repo), "session_id": "real-slug", "model": "gpt-5.1-codex",
                "prompt": "$empirica --auto known limits", "hook_event_name": "UserPromptSubmit"}
            with patch.dict(os.environ, {"EMPIRICA_HOME": str(repo / "state"),
                                              "EMPIRICA_REPO_DIR": str(repo),
                                              "EMPIRICA_AUTO_DELEGATION": "1"}):
                started = _start(payload)
                handle = re.search(r"er2:[^ ]+", started["hookSpecificOutput"]["additionalContext"]).group(0).rstrip(".)")
                for name in ("mcp__evil__empirica_read", "evil_report_convergence"):
                    denied = _pre_tool_use({**payload, "tool_name": name, "hook_event_name": "PreToolUse"})
                    self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
                for prefix in ("", "mcp__empirica__"):
                    for tool in ("empirica_read", "empirica_observe", "report_convergence"):
                        self.assertIsNone(_pre_tool_use({**payload, "tool_name": prefix + tool}))
                tools = PublicTools("codex-cli@0.146.0", govern=HostGovernance("codex-cli@0.146.0"))
                tools.call("empirica_observe", {"run_id": handle, "action": {"kind": "route", "reason": "route first"}})
                tools.call("empirica_observe", {"run_id": handle, "action": {"kind": "graph", "payload": {
                    "root": "G0", "claims": [{"id": "G0", "text": "identity", "gating": True, "kind": "ordinary"}], "edges": []}}})
                result = tools.call("empirica_observe", {"run_id": handle, "action": {"kind": "configure_run"}})["structuredContent"]
                self.assertEqual(result["run"]["governance"]["approval_kind"], "auto")

    def test_public_mcp_surface_runs_managed_auditor_but_blocks_unobserved_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = root / "repo", root / "state"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "probe.py").write_text("print('reachable')\n", encoding="utf-8")
            payload = {
                "cwd": str(repo), "session_id": "codex-reachability-session",
                "model": "gpt-4.1-2025-04-14", "prompt": "$empirica --auto prove Codex reachability",
                "hook_event_name": "UserPromptSubmit", "turn_id": "turn-1",
            }
            previous_cwd = Path.cwd()
            self.addCleanup(os.chdir, previous_cwd)
            os.chdir(repo)
            with patch.dict(os.environ, {
                "EMPIRICA_HOME": str(home), "EMPIRICA_REPO_DIR": str(repo),
                "EMPIRICA_AUTO_DELEGATION": "1",
            }, clear=False):
                started = _start(payload)
                context = started["hookSpecificOutput"]["additionalContext"]
                handle = re.search(r"er2:[^ ]+", context).group(0).rstrip(".)")
                tools = PublicTools("codex-cli@0.146.0", govern=HostGovernance("codex-cli@0.146.0"))

                def observe(action: dict) -> dict:
                    out = tools.call("empirica_observe", {"run_id": handle, "action": action})
                    self.assertFalse(out["isError"], out)
                    return out["structuredContent"]

                observe({"kind": "route", "reason": "route first"})
                observe({"kind": "graph", "payload": {
                    "root": "G0",
                    "claims": [{"id": "G0", "text": "Codex drives Empirica v2.",
                                "gating": True, "kind": "needs-experiment"}],
                    "edges": [],
                }})
                approved = observe({"kind": "configure_run"})
                self.assertEqual(approved["run"]["governance"]["approval_kind"], "auto")
                observe({"kind": "investigate"})
                observe({"kind": "research", "claim_id": "G0", "source_kind": "code",
                         "result": "supports", "payload": {"source_ref": "probe.py",
                                                               "citation": "The probe executes successfully."}})
                observe({"kind": "spike_request", "claim_id": "G0",
                         "command": "python3 probe.py", "dependent_files": ["probe.py"]})
                observe({"kind": "freeze"})

                stop_payload = {**payload, "hook_event_name": "Stop",
                                "stop_hook_active": False,
                                "last_assistant_message": "ready"}
                stop = _stop(stop_payload)
                self.assertEqual(stop.get("decision"), "block")

                final = tools.call("report_convergence", {"run_id": handle})
                result = final["structuredContent"]
                self.assertEqual(result["type"], "Block")
                self.assertEqual([reason["code"] for reason in result["reasons"]],
                                 ["audit.required"])
                self.assertEqual(result["run"]["status"], "active")


if __name__ == "__main__":
    unittest.main()
