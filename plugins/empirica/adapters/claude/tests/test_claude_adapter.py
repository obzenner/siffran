#!/usr/bin/env python3
"""Parity and isolated persistence tests for the inactive Claude adapter slice."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from adapters.claude.correlation import CorrelationError, correlate  # noqa: E402
from adapters.claude.fail_direction import (  # noqa: E402
    FailureDirection,
    blocks_on_failure,
    failure_direction,
)
from adapters.claude.run_start import (  # noqa: E402
    FALLBACK_GOAL,
    build_start_run_request,
    dispatch_start_run,
)
from adapters.claude.selector import SelectorError  # noqa: E402
from adapters.claude.transport import BridgeTransport  # noqa: E402


class RecordingTransport:
    def __init__(self, response: dict | None = None) -> None:
        self.requests: list[dict] = []
        self.response = response

    def dispatch(self, request: dict) -> dict:
        self.requests.append(request)
        return self.response or {
            "protocol": "empirica/v1",
            "request_id": request["request_id"],
            "result": {"type": "Inert", "reason": "recorded"},
        }


class TranslationParityTests(unittest.TestCase):
    def test_minimal_existing_payload_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            request = build_start_run_request(
                {"session_id": "sess-runstart", "cwd": tmp,
                 "command_name": "empirica:empirica"},
                correlation_id="run-start-1",
                environ={},
            )
        self.assertEqual(request["protocol"], "empirica/v1")
        self.assertEqual(request["request_id"], "run-start-1")
        self.assertEqual(request["command"]["type"], "StartRun")
        self.assertEqual(request["command"]["goal"], FALLBACK_GOAL)
        self.assertNotIn("modes", request["command"])
        self.assertTrue(request["command"]["selector"]["project"])
        self.assertTrue(request["command"]["selector"]["session"])

    def test_real_captured_payload_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = {
                "session_id": "c7477410-ea2d-4960-bfb6-df1e6f39900c",
                "cwd": tmp,
                "transcript_path": "/x.jsonl",
                "prompt_id": "p1",
                "permission_mode": "bypassPermissions",
                "hook_event_name": "UserPromptExpansion",
                "expansion_type": "slash_command",
                "command_name": "empirica:empirica",
                "command_args": "design something",
                "command_source": "plugin",
                "prompt": "/empirica:empirica ignored fallback",
            }
            transport = RecordingTransport()
            dispatch_start_run(payload, transport=transport, correlation_id="captured", environ={})
        self.assertEqual(transport.requests[0]["command"]["goal"], "design something")

    def test_prompt_fallback_flags_and_max_passes_match_current_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            request = build_start_run_request(
                {"session_id": "s", "cwd": tmp,
                 "prompt": "/empirica:empirica --cli-exec --no-multi-provider design X"},
                correlation_id="fallback",
                environ={"EMPIRICA_MAX_PASSES": "5"},
            )
        self.assertEqual(request["command"]["goal"], "design X")
        self.assertEqual(
            request["command"]["modes"], {"cli_exec": True, "multi_provider": False},
        )
        self.assertEqual(request["command"]["max_passes"], 5)

    def test_missing_session_is_rejected_before_transport(self) -> None:
        with self.assertRaises(SelectorError):
            build_start_run_request({"cwd": "."}, correlation_id="bad", environ={})


class UtilityTests(unittest.TestCase):
    def test_correlation_rejects_a_mismatched_response(self) -> None:
        request = {"request_id": "one"}
        response = {"protocol": "empirica/v1", "request_id": "two", "result": {}}
        with self.assertRaises(CorrelationError):
            correlate(request, response)

    def test_explicit_fail_direction_wins_and_malformed_uses_event_fallback(self) -> None:
        fault = {"result": {"type": "Fault", "fail_direction": "closed"}}
        self.assertEqual(
            failure_direction(fault, fallback=FailureDirection.OPEN), FailureDirection.CLOSED,
        )
        self.assertTrue(blocks_on_failure(fault, fallback=FailureDirection.OPEN))
        self.assertEqual(
            failure_direction({}, fallback=FailureDirection.OPEN), FailureDirection.OPEN,
        )


class IsolatedBridgeIntegrationTests(unittest.TestCase):
    def _git(self, repo: Path, *args: str) -> str:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True,
        )
        return proc.stdout.strip()

    def test_real_bridge_uses_global_state_and_shadow_ref_without_workspace_writes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            repo, home = base / "repo", base / "global-state"
            repo.mkdir()
            self._git(repo, "init", "-q")
            (repo / "tracked.txt").write_text("unchanged\n", encoding="utf-8")
            self._git(repo, "add", "tracked.txt")
            self._git(repo, "commit", "-q", "-m", "fixture")
            head_before = self._git(repo, "rev-parse", "HEAD")
            index_before = self._git(repo, "write-tree")

            payload = {
                "session_id": "isolated-session",
                "cwd": str(repo),
                "command_name": "empirica:empirica",
                "command_args": "prove isolation",
            }
            with patch.dict(os.environ, {"EMPIRICA_HOME": str(home)}, clear=False):
                started = dispatch_start_run(payload, correlation_id="integration-start", environ={})
                self.assertEqual(started["request_id"], "integration-start")
                self.assertEqual(started["result"]["type"], "Allow")
                handle = started["result"]["run"]["id"]

                observed = BridgeTransport(repo).dispatch({
                    "protocol": "empirica/v1",
                    "request_id": "integration-graph",
                    "command": {
                        "type": "ObserveAction",
                        "run_id": handle,
                        "action": {
                            "kind": "graph",
                            "graph": {
                                "root": "G0",
                                "nodes": {"G0": {
                                    "type": "Goal", "text": "prove isolation", "confidence": 0.5,
                                }},
                                "edges": [],
                            },
                        },
                    },
                })

            self.assertIn(observed["result"]["type"], {"Allow", "Block"})
            state_files = list(home.glob("projects/*/runs/*/gen-1/run.json"))
            self.assertEqual(len(state_files), 1)
            state = json.loads(state_files[0].read_text(encoding="utf-8"))
            self.assertIsNotNone(state.get("claim_graph_artifact_id"))

            refs = self._git(repo, "for-each-ref", "--format=%(refname)",
                             "refs/empirica/artifacts/").splitlines()
            self.assertEqual(len(refs), 1)
            self.assertTrue(refs[0].startswith("refs/empirica/artifacts/1/"))

            self.assertFalse((repo / ".claude").exists())
            self.assertFalse((repo / ".pi").exists())
            self.assertEqual(
                sorted(p.relative_to(repo).as_posix() for p in repo.iterdir() if p.name != ".git"),
                ["tracked.txt"],
            )
            self.assertEqual(self._git(repo, "status", "--porcelain"), "")
            self.assertEqual(self._git(repo, "rev-parse", "HEAD"), head_before)
            self.assertEqual(self._git(repo, "write-tree"), index_before)


if __name__ == "__main__":
    unittest.main()
