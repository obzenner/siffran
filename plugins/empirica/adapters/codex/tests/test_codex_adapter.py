#!/usr/bin/env python3
"""Payload conformance and isolated lifecycle tests for Codex CLI 0.146.0."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[3]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from adapters.codex.knowledge import (  # noqa: E402
    build_audit_verdict_request,
    build_graph_request,
    build_research_request,
    build_spike_request,
    run_spike,
)
from adapters.codex.lifecycle import (  # noqa: E402
    build_investigation_request,
    build_reserve_spawn_request,
    build_route_request,
    build_start_run_request,
    build_stop_request,
    event_stamp,
    explicit_activation,
)
from adapters.codex.transport import BridgeTransport  # noqa: E402
from adapters.state import project_id, run_id  # noqa: E402
from application import knowledge  # noqa: E402
from application.knowledge import canonicalize_graph  # noqa: E402
from core import claims as C  # noqa: E402


OFFICIAL_REQUIRED = {
    "UserPromptSubmit": {
        "cwd", "hook_event_name", "model", "permission_mode", "prompt", "session_id",
        "transcript_path", "turn_id",
    },
    "PreToolUse": {
        "cwd", "hook_event_name", "model", "permission_mode", "session_id", "tool_input",
        "tool_name", "tool_use_id", "transcript_path", "turn_id",
    },
    "Stop": {
        "cwd", "hook_event_name", "last_assistant_message", "model", "permission_mode",
        "session_id", "stop_hook_active", "transcript_path", "turn_id",
    },
    "SessionStart": {
        "cwd", "hook_event_name", "model", "permission_mode", "session_id", "source",
        "transcript_path",
    },
}
OFFICIAL_OPTIONAL = {"agent_id", "agent_type"}
OFFICIAL_OUTPUT_ALLOWED = {
    "UserPromptSubmit": {"continue", "decision", "hookSpecificOutput", "reason", "stopReason",
                         "suppressOutput", "systemMessage"},
    "PreToolUse": {"continue", "decision", "hookSpecificOutput", "reason", "stopReason",
                   "suppressOutput", "systemMessage"},
    "Stop": {"continue", "decision", "reason", "stopReason", "suppressOutput", "systemMessage"},
    "SessionStart": {"continue", "hookSpecificOutput", "stopReason", "suppressOutput",
                     "systemMessage"},
}


def payload(event: str, cwd: Path, **extra: object) -> dict:
    base = {
        "session_id": "01991b3b-8180-7553-9151-30cb08c67f64",
        "cwd": str(cwd),
        "hook_event_name": event,
        "model": "gpt-5.6-codex",
        "permission_mode": "default",
        "transcript_path": None,
    }
    defaults = {
        "UserPromptSubmit": {"prompt": "$empirica prove true", "turn_id": "turn-1"},
        "PreToolUse": {
            "turn_id": "turn-1", "tool_name": "Bash", "tool_input": {"command": "true"},
            "tool_use_id": "call-1",
        },
        "Stop": {
            "turn_id": "turn-1", "stop_hook_active": False, "last_assistant_message": None,
        },
        "SessionStart": {"source": "compact"},
    }
    return {**base, **defaults[event], **extra}


def assert_official_input(test: unittest.TestCase, value: dict) -> None:
    event = value["hook_event_name"]
    test.assertFalse(OFFICIAL_REQUIRED[event] - value.keys())
    test.assertFalse(value.keys() - OFFICIAL_REQUIRED[event] - OFFICIAL_OPTIONAL)


def assert_official_output(test: unittest.TestCase, event: str, value: dict) -> None:
    test.assertFalse(value.keys() - OFFICIAL_OUTPUT_ALLOWED[event])
    specific = value.get("hookSpecificOutput")
    if specific is not None:
        test.assertIsInstance(specific, dict)
        test.assertEqual(specific.get("hookEventName"), event)
    if value.get("decision") == "block":
        test.assertIn(event, {"UserPromptSubmit", "Stop"})
        test.assertIsInstance(value.get("reason"), str)
        test.assertTrue(value["reason"])
    if event == "PreToolUse" and isinstance(specific, dict):
        decision = specific.get("permissionDecision")
        test.assertIn(decision, {None, "allow", "deny", "ask"})
        if decision == "deny":
            test.assertTrue(specific.get("permissionDecisionReason"))


class TranslationTests(unittest.TestCase):
    def test_official_payloads_and_explicit_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            for event in OFFICIAL_REQUIRED:
                assert_official_input(self, payload(event, cwd))

            start = payload(
                "UserPromptSubmit", cwd,
                prompt="$empirica --cli-exec --no-multi-provider design X",
            )
            request = build_start_run_request(start, correlation_id="start", environ={})
            self.assertEqual(request["command"]["goal"], "design X")
            self.assertEqual(request["command"]["modes"], {
                "cli_exec": True, "multi_provider": False,
            })
            self.assertIsNone(build_start_run_request(
                {**start, "prompt": "please discuss empirica"}, environ={},
            ))
            self.assertEqual(explicit_activation(start),
                             "--cli-exec --no-multi-provider design X")

    def test_agent_alias_payload_reserves_and_action_stamps_are_host_derived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            value = payload(
                "PreToolUse", Path(tmp), tool_name="spawn_agent",
                tool_input={"message": "run empirica-auditor"},
            )
            request = build_reserve_spawn_request(value, "opaque", correlation_id="spawn")
            self.assertEqual(request["command"]["action"], {"kind": "reserve_spawn"})
            self.assertEqual(request["command"]["observed_at"],
                             "codex:turn:turn-1:tool:call-1")
            self.assertEqual(event_stamp(value), "codex:turn:turn-1:tool:call-1")

    def test_route_marker_is_not_misclassified_as_investigation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            value = payload("PreToolUse", Path(tmp), tool_input={
                "command": "python3 -c 'pass' -- --empirica-route 'runtime unknown'",
            })
            route = build_route_request(value, "opaque", correlation_id="route")
            self.assertEqual(route["command"]["action"], {
                "kind": "route", "reason": "runtime unknown",
            })
            self.assertIsNone(build_investigation_request(value, "opaque"))

            mixed = payload("PreToolUse", Path(tmp), tool_input={
                "command": "rg secret . --empirica-route 'pretend routed'",
            })
            self.assertIsNone(build_route_request(mixed, "opaque"))
            self.assertEqual(
                build_investigation_request(mixed, "opaque")["command"]["action"],
                {"kind": "investigate"},
            )

    def test_stop_translation_has_one_authoritative_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            request = build_stop_request(
                payload("Stop", Path(tmp)), "opaque", correlation_id="stop",
            )
            # The hook stamps its own wall clock (Codex supplies no event timestamp), so the stop
            # request now carries a numeric epoch-seconds `observed_at`. Assert its shape, then the
            # rest of the command is exactly the one authoritative report_convergence translation.
            observed_at = request["command"].pop("observed_at")
            self.assertIsInstance(observed_at, (int, float))
            self.assertNotIsInstance(observed_at, bool)
            self.assertEqual(request["command"], {
                "type": "EvaluateRun", "run_id": "opaque",
                "intent": "report_convergence",
            })

    def test_codex_spike_reuses_harness_and_records_codex_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracked = Path(tmp) / "tracked"
            tracked.write_text("x", encoding="utf-8")
            execution = run_spike("G0", "true exits zero", ["true"], [tracked],
                                  "2026-09-06T00:00:01Z")
            self.assertEqual(execution.statement["predicate"]["gate"], "pass")
            self.assertEqual(execution.statement["predicate"]["actor"]["harness"], "codex")


class IsolatedLifecycleTests(unittest.TestCase):
    def git(self, repo: Path, *args: str) -> str:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True,
            text=True, env=env,
        ).stdout.strip()

    def hook(self, action: str, value: dict, repo: Path, home: Path, **env: str):
        assert_official_input(self, value)
        return subprocess.run(
            [sys.executable, str(PLUGIN / "hooks" / "codex_hook.py"), action],
            input=json.dumps(value), text=True, capture_output=True, cwd=repo,
            env={**os.environ, "EMPIRICA_HOME": str(home), **env},
        )

    def test_full_codex_hook_lifecycle_and_store_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, home = base / "repo", base / "home"
            repo.mkdir()
            self.git(repo, "init", "-q")
            tracked = repo / "tracked.txt"
            tracked.write_text("base\n", encoding="utf-8")
            self.git(repo, "add", "tracked.txt")
            self.git(repo, "commit", "-q", "-m", "base")
            tracked.write_text("dirty\n", encoding="utf-8")
            head, tree = self.git(repo, "rev-parse", "HEAD"), self.git(repo, "write-tree")
            status = self.git(repo, "status", "--porcelain=v1")

            activation = payload("UserPromptSubmit", repo)
            started = self.hook("activate", activation, repo, home, EMPIRICA_MAX_SPAWNS="1")
            self.assertEqual(started.returncode, 0, started.stderr)
            start_output = json.loads(started.stdout)
            assert_official_output(self, "UserPromptSubmit", start_output)
            self.assertEqual(start_output["hookSpecificOutput"]["hookEventName"],
                             "UserPromptSubmit")

            with unittest.mock.patch.dict(os.environ, {"EMPIRICA_HOME": str(home)}):
                transport = BridgeTransport(repo)
                resolved = transport.dispatch({
                    "protocol": "empirica/v1", "request_id": "resolve",
                    "command": {"type": "ResolveRun", "selector": {
                        "project": project_id(repo), "session": run_id(activation["session_id"]),
                    }},
                })
                handle = resolved["result"]["run"]["id"]

                blocked = self.hook("stop", payload("Stop", repo), repo, home)
                blocked_output = json.loads(blocked.stdout)
                assert_official_output(self, "Stop", blocked_output)
                self.assertEqual(blocked_output["decision"], "block")

                route = self.hook("pre-tool-use", payload(
                    "PreToolUse", repo, tool_input={
                        "command": "python3 -c 'pass' -- --empirica-route 'runtime unknown'",
                    },
                ), repo, home)
                self.assertEqual((route.returncode, route.stdout, route.stderr), (0, "", ""))
                investigated = self.hook("pre-tool-use", payload(
                    "PreToolUse", repo, tool_input={"command": "rg x ."}, tool_use_id="call-2",
                ), repo, home)
                self.assertEqual(investigated.returncode, 0)

                restored = transport.dispatch({
                    "protocol": "empirica/v1", "request_id": "ordered",
                    "command": {"type": "RestoreRun", "run_id": handle},
                })
                route_view = restored["result"]["run"]["snapshot"]["route"]
                self.assertEqual(route_view["verdict"], "ok")
                self.assertLess(route_view["route_seq"], route_view["first_investigation_seq"])

                compact_active = self.hook("restore", payload("SessionStart", repo), repo, home)
                compact_output = json.loads(compact_active.stdout)
                assert_official_output(self, "SessionStart", compact_output)
                compact_context = compact_output["hookSpecificOutput"]["additionalContext"]
                self.assertIn("BEGIN UNTRUSTED EMPIRICA RUN DATA", compact_context)

                graph = {"root": "G0", "nodes": {"G0": {
                    "type": "Goal", "text": "true exits zero", "kind": "needs-experiment",
                    "confidence": 0.9,
                }}, "edges": []}
                transport.dispatch(build_graph_request(handle, graph, correlation_id="graph"))
                digest = hashlib.sha256(b"true exits zero").hexdigest()
                research = {
                    "_type": "https://in-toto.io/Statement/v1",
                    "subject": [{"name": "G0", "digest": {"sha256": digest}}],
                    "predicateType": "https://empirica.dev/attestation/research/v1",
                    "predicate": {
                        "fold": "research", "kind": "runtime", "source": "true",
                        "citation": "POSIX true exits zero", "result": "supports",
                        "ts": "2026-09-06T00:00:00Z",
                    },
                }
                research_request = build_research_request(
                    handle, "research-G0", research, graph, [research], correlation_id="research",
                )
                transport.dispatch(research_request)
                spike = run_spike(
                    "G0", "true exits zero", ["true"], [tracked],
                    "2026-09-06T00:00:01Z",
                )
                spike_request = build_spike_request(
                    handle, "spike-G0", spike, graph, [research], correlation_id="spike",
                )
                transport.dispatch(spike_request)

                auditor = self.hook("pre-tool-use", payload(
                    "PreToolUse", repo, tool_name="spawn_agent", tool_input={
                        "message": "Act as empirica-auditor and audit this run",
                    }, tool_use_id="call-3",
                ), repo, home)
                self.assertEqual(auditor.returncode, 0, auditor.stderr)

                denied = self.hook("pre-tool-use", payload(
                    "PreToolUse", repo, tool_name="spawn_agent",
                    tool_input={"message": "ordinary worker"}, tool_use_id="call-4",
                ), repo, home)
                denied_output = json.loads(denied.stdout)
                assert_official_output(self, "PreToolUse", denied_output)
                self.assertEqual(
                    denied_output["hookSpecificOutput"]["permissionDecision"], "deny",
                )
                ticketed = transport.dispatch({
                    "protocol": "empirica/v1", "request_id": "ticketed",
                    "command": {"type": "RestoreRun", "run_id": handle},
                })
                nonce = ticketed["result"]["run"]["snapshot"]["audit_tickets"][0]["nonce"]
                leaves = [
                    {"statement": request["command"]["action"]["statement"],
                     "verdicts": request["command"]["action"]["verdicts"]}
                    for request in (research_request, spike_request)
                ]
                verdict = {
                    "verdict": "pass", "nonce": nonce,
                    "argument_digest": C.argument_digest(canonicalize_graph(graph)),
                    "claims_reviewed": [{
                        "claim_id": "G0", "claim_digest": digest,
                        "evidence_digest": knowledge._leaf_digest(
                            leaves, "G0", "true exits zero",
                        ),
                    }],
                    "findings": [],
                }
                transport.dispatch(build_audit_verdict_request(
                    handle, verdict, correlation_id="audit",
                ))

                completed = self.hook("stop", payload("Stop", repo), repo, home)
                completion_output = json.loads(completed.stdout)
                assert_official_output(self, "Stop", completion_output)
                self.assertNotIn("decision", completion_output)
                self.assertTrue(json.loads(completion_output["systemMessage"])["converged"])

                compact = self.hook("restore", payload("SessionStart", repo), repo, home)
                # A terminal run is intentionally not re-injected.
                self.assertEqual((compact.returncode, compact.stdout, compact.stderr), (0, "", ""))

            self.assertEqual(self.git(repo, "rev-parse", "HEAD"), head)
            self.assertEqual(self.git(repo, "write-tree"), tree)
            self.assertEqual(self.git(repo, "status", "--porcelain=v1"), status)
            self.assertFalse((repo / ".codex").exists())
            self.assertFalse((repo / ".claude").exists())
            self.assertFalse((repo / ".pi").exists())
            refs = self.git(repo, "for-each-ref", "--format=%(refname)", "refs/empirica/")
            self.assertIn("refs/empirica/", refs)


if __name__ == "__main__":
    unittest.main()
