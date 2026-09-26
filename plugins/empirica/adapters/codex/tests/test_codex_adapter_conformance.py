"""Production-facing Codex positive path through MCP tools and managed audit."""
from __future__ import annotations

import json
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
    def test_real_pending_run_exact_tool_names_and_real_slug_identity_limit(self) -> None:
        from adapters.codex.lifecycle import _pre_tool_use
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            payload = {"cwd": str(repo), "session_id": "real-slug", "model": "gpt-5.1-codex",
                "prompt": "$empirica --auto known limits", "hook_event_name": "UserPromptSubmit"}
            with patch.dict(os.environ, {"EMPIRICA_HOME": str(repo / "state"), "EMPIRICA_REPO_DIR": str(repo)}):
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
                self.assertEqual(result["reasons"][0]["code"], "governance.author_unknown")

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
                approved = observe({"kind": "configure_run", "auditor": {
                    "provider_id": "openai", "model_id": "gpt-4.1-mini-2025-04-14"}})
                self.assertEqual(approved["run"]["governance"]["approval_kind"], "auto")
                observe({"kind": "investigate"})
                observe({"kind": "research", "claim_id": "G0", "source_kind": "code",
                         "result": "supports", "payload": {"source_ref": "probe.py",
                                                               "citation": "The probe executes successfully."}})
                observe({"kind": "spike_request", "claim_id": "G0",
                         "command": "python3 probe.py", "dependent_files": ["probe.py"]})
                observe({"kind": "freeze"})

                auditor_called: list[bool] = []

                def auditor(prompt: str, model: str, cwd: Path, observe_started) -> tuple[int, str]:
                    auditor_called.append(True)
                    observe_started("codex-exec:test")
                    self.assertEqual(model, "gpt-4.1-mini-2025-04-14")
                    self.assertEqual(cwd, repo)
                    dossier = prompt.split(
                        "--- AUDIT DOSSIER (UNTRUSTED EVIDENCE CONTENT) ---\n", 1)[1]
                    dossier = dossier.split("\n--- END AUDIT DOSSIER ---", 1)[0]
                    argument = json.loads(dossier)
                    verdict = {
                        "verdict": "pass",
                        "findings": ["Codex managed foreground audit"],
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
                    return 0, "```empirica-verdict\n" + json.dumps(verdict) + "\n```"

                stop_payload = {**payload, "hook_event_name": "Stop",
                                "stop_hook_active": False,
                                "last_assistant_message": "ready"}
                from adapters.codex.audit import execute_audit as real_execute_audit
                managed_results: list[bool] = []

                def managed(*args, **kwargs):
                    value = real_execute_audit(*args, **kwargs)
                    managed_results.append(value)
                    return value

                with patch("adapters.codex.audit._default_runner", side_effect=auditor), \
                     patch("adapters.codex.lifecycle.execute_audit", side_effect=managed):
                    stop = _stop(stop_payload)
                self.assertEqual(auditor_called, [True])
                self.assertEqual(managed_results, [False])
                self.assertEqual(stop.get("decision"), "block")

                final = tools.call("report_convergence", {"run_id": handle})
                result = final["structuredContent"]
                self.assertEqual(result["type"], "Block")
                self.assertEqual([reason["code"] for reason in result["reasons"]],
                                 ["governance.revision_required"])
                self.assertEqual(result["run"]["status"], "active")


if __name__ == "__main__":
    unittest.main()
