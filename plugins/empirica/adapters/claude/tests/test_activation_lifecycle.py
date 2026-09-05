#!/usr/bin/env python3
"""Subprocess parity for the activated Claude lifecycle."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[3]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from adapters.claude.knowledge import (  # noqa: E402
    build_audit_verdict_request,
    build_graph_request,
    build_research_request,
    build_spike_request,
    run_spike,
)
from adapters.claude.route import build_route_announcement_request  # noqa: E402
from adapters.claude.spike import MAX_LINE, run_gate  # noqa: E402
from adapters.claude.transport import BridgeTransport  # noqa: E402
from application import knowledge  # noqa: E402
from application.knowledge import canonicalize_graph  # noqa: E402
from core import claims as C  # noqa: E402


class ActivatedLifecycleTests(unittest.TestCase):
    def git(self, repo: Path, *args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@example.invalid",
               "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                              text=True, env=env).stdout.strip()

    def hook(self, name: str, payload: dict, repo: Path, home: Path, **env: str):
        return subprocess.run([sys.executable, str(PLUGIN / "hooks" / name)],
                              input=json.dumps(payload), text=True, capture_output=True, cwd=repo,
                              env={**os.environ, "EMPIRICA_HOME": str(home), **env})

    def test_complete_lifecycle_generation_cap_restore_and_dirty_worktree_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, home = base / "repo", base / "home"
            repo.mkdir()
            self.git(repo, "init", "-q")
            (repo / "tracked.txt").write_text("base\n")
            self.git(repo, "add", "tracked.txt")
            self.git(repo, "commit", "-q", "-m", "base")
            # Pre-existing dirty state must survive every operational and knowledge write exactly.
            (repo / "tracked.txt").write_text("dirty bytes\n")
            (repo / "untracked.bin").write_bytes(b"\x00dirty\xff")
            head = self.git(repo, "rev-parse", "HEAD")
            index = self.git(repo, "write-tree")
            status = self.git(repo, "status", "--porcelain=v1")
            files = {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()}

            payload = {"session_id": "activation-session", "cwd": str(repo),
                       "command_name": "empirica:empirica", "command_args": "prove true"}
            started = self.hook("run_start.py", payload, repo, home,
                                EMPIRICA_MAX_SPAWNS="3")
            self.assertEqual((started.returncode, started.stdout, started.stderr), (0, "", ""))

            transport = BridgeTransport(repo)
            os.environ["EMPIRICA_HOME"] = str(home)
            selector = transport.dispatch({"protocol": "empirica/v1", "request_id": "resolve",
                "command": {"type": "ResolveRun", "selector": {
                    "project": __import__("adapters.state", fromlist=["project_id"]).project_id(repo),
                    "session": __import__("adapters.state", fromlist=["run_id"]).run_id(
                        "activation-session")}}})
            handle = selector["result"]["run"]["id"]
            first_handle = handle

            route_payload = {**payload, "tool_name": "Bash", "tool_input": {
                "command": "python route_stamp.py --announce-route --session activation-session"}}
            self_stamping = self.hook("route_stamp.py", route_payload, repo, home)
            self.assertEqual((self_stamping.returncode, self_stamping.stdout,
                              self_stamping.stderr), (0, "", ""))
            before_route = transport.dispatch({"protocol": "empirica/v1", "request_id": "pre-route",
                "command": {"type": "RestoreRun", "run_id": handle}})
            self.assertIsNone(before_route["result"]["run"]["snapshot"]["route"][
                "first_investigation_seq"])

            transport.dispatch(build_route_announcement_request(
                route_payload, handle, reason="runtime behavior is unknown", correlation_id="route"))
            investigated = self.hook("route_stamp.py", {**payload, "tool_name": "Grep",
                                                         "tool_input": {"pattern": "x"}}, repo, home)
            self.assertEqual((investigated.returncode, investigated.stdout, investigated.stderr),
                             (0, "", ""))
            ordered = transport.dispatch({"protocol": "empirica/v1", "request_id": "ordered",
                "command": {"type": "RestoreRun", "run_id": handle}})
            route_state = ordered["result"]["run"]["snapshot"]["route"]
            self.assertEqual(route_state["verdict"], "ok")
            self.assertLess(route_state["route_seq"], route_state["first_investigation_seq"])

            graph = {"root": "G0", "nodes": {"G0": {
                "type": "Goal", "text": "true exits zero", "kind": "needs-experiment",
                "confidence": 0.9}}, "edges": []}
            transport.dispatch(build_graph_request(handle, graph, correlation_id="graph"))
            text_digest = hashlib.sha256(b"true exits zero").hexdigest()
            research = {"_type": "https://in-toto.io/Statement/v1",
                "subject": [{"name": "G0", "digest": {"sha256": text_digest}}],
                "predicateType": "https://empirica.dev/attestation/research/v1",
                "predicate": {"fold": "research", "kind": "runtime", "source": "true",
                              "citation": "POSIX true exits zero", "result": "supports",
                              "ts": "2026-09-05T00:00:00Z"}}
            research_req = build_research_request(handle, "research-G0", research, graph, [research],
                                                  correlation_id="research")
            transport.dispatch(research_req)
            spike = run_spike("G0", "true exits zero", ["true"], [repo / "tracked.txt"],
                              "2026-09-05T00:01:00Z")
            spike_req = build_spike_request(handle, "spike-G0", spike, graph, [research],
                                             correlation_id="spike")
            transport.dispatch(spike_req)

            auditor = self.hook("spawn_gate.py", {**payload, "tool_name": "Agent", "tool_input": {
                "subagent_type": "empirica:empirica-auditor"}}, repo, home)
            self.assertEqual(auditor.returncode, 0, auditor.stderr)
            restored_response = transport.dispatch({"protocol": "empirica/v1", "request_id": "r",
                "command": {"type": "RestoreRun", "run_id": handle}})
            nonce = restored_response["result"]["run"]["snapshot"]["audit_tickets"][0]["nonce"]
            records = [research_req["command"]["action"], spike_req["command"]["action"]]
            leaf_records = [{"statement": r["statement"], "verdicts": r["verdicts"]} for r in records]
            verdict = {"verdict": "pass", "nonce": nonce,
                "argument_digest": C.argument_digest(canonicalize_graph(graph)),
                "claims_reviewed": [{"claim_id": "G0", "claim_digest": text_digest,
                    "evidence_digest": knowledge._leaf_digest(leaf_records, "G0", "true exits zero")}],
                "findings": []}
            transport.dispatch(build_audit_verdict_request(handle, verdict, correlation_id="audit"))

            # A graph change invalidates the otherwise-passing audit until a fresh ticket/verdict
            # covers the new argument digest.
            refreshed_graph = {"root": "G0", "nodes": {
                "G0": graph["nodes"]["G0"],
                "C0": {"type": "Context", "text": "activation regression context"},
            }, "edges": [{"from": "G0", "to": "C0", "type": "InContextOf"}]}
            refreshed_response = transport.dispatch(build_graph_request(
                handle, refreshed_graph, correlation_id="graph-refresh"))
            self.assertEqual(refreshed_response["result"]["type"], "Allow",
                             refreshed_response)
            stale = self.hook("convergence_gate.py", {**payload, "hook_event_name": "Stop"},
                              repo, home)
            self.assertEqual(stale.returncode, 2)
            self.assertIn("audit", stale.stderr.lower())

            fresh_auditor = self.hook("spawn_gate.py", {**payload, "tool_name": "Agent",
                "tool_input": {"subagent_type": "empirica:empirica-auditor"}}, repo, home)
            self.assertEqual(fresh_auditor.returncode, 0, fresh_auditor.stderr)
            refreshed = transport.dispatch({"protocol": "empirica/v1", "request_id": "fresh-ticket",
                "command": {"type": "RestoreRun", "run_id": handle}})
            fresh_nonce = refreshed["result"]["run"]["snapshot"]["audit_tickets"][-1]["nonce"]
            fresh_verdict = {**verdict, "nonce": fresh_nonce,
                             "argument_digest": C.argument_digest(
                                 canonicalize_graph(refreshed_graph))}
            transport.dispatch(build_audit_verdict_request(
                handle, fresh_verdict, correlation_id="fresh-audit"))

            stop = self.hook("convergence_gate.py", {**payload, "hook_event_name": "Stop"}, repo, home)
            self.assertEqual(stop.returncode, 0, stop.stderr)
            self.assertTrue(json.loads(stop.stdout)["converged"])

            relaunched = self.hook("run_start.py", payload, repo, home,
                                   EMPIRICA_MAX_SPAWNS="1")
            self.assertEqual(relaunched.returncode, 0)
            latest = transport.dispatch({"protocol": "empirica/v1", "request_id": "resolve2",
                "command": {"type": "ResolveRun", "selector": selector["result"]["run"].get(
                    "selector", {"project": __import__("adapters.state", fromlist=["project_id"]).project_id(repo),
                                 "session": __import__("adapters.state", fromlist=["run_id"]).run_id("activation-session")})}})
            second_handle = latest["result"]["run"]["id"]
            self.assertNotEqual(second_handle, first_handle)
            spawn_payload = {**payload, "tool_name": "Agent", "tool_input": {"subagent_type": "worker"}}
            self.assertEqual(self.hook("spawn_gate.py", spawn_payload, repo, home).returncode, 0)
            denied = self.hook("spawn_gate.py", spawn_payload, repo, home)
            self.assertEqual(denied.returncode, 2)
            compact = self.hook("state_restore.py", {**payload, "source": "compact"}, repo, home)
            self.assertEqual(compact.returncode, 0)
            self.assertIn("BEGIN UNTRUSTED EMPIRICA RUN DATA", compact.stdout)

            self.assertEqual(self.git(repo, "rev-parse", "HEAD"), head)
            self.assertEqual(self.git(repo, "write-tree"), index)
            self.assertEqual(self.git(repo, "status", "--porcelain=v1"), status)
            self.assertEqual({p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()}, files)
            self.assertFalse((repo / ".claude").exists())
            self.assertFalse((repo / ".pi").exists())
            refs = self.git(repo, "for-each-ref", "--format=%(refname)", "refs/empirica/")
            self.assertIn("refs/empirica/", refs)


class SpikeExecutableTests(unittest.TestCase):
    def test_nonzero_timeout_launch_failure_and_bounded_output(self) -> None:
        nonzero = run_gate([sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(7)"])
        self.assertEqual((nonzero["gate"], nonzero["returncode"], nonzero["timed_out"]),
                         ("fail", 7, False))
        self.assertEqual(nonzero["stderr_tail"], ["bad"])

        timed_out = run_gate([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.05)
        self.assertEqual((timed_out["gate"], timed_out["returncode"], timed_out["timed_out"]),
                         ("fail", None, True))
        self.assertIn("timed out", timed_out["stderr_tail"][0])

        missing = run_gate(["/definitely/missing/empirica-spike-command"])
        self.assertEqual((missing["gate"], missing["returncode"]), ("fail", 127))
        self.assertIn("launch failed", missing["stderr_tail"][0])

        bounded = run_gate([sys.executable, "-c",
                            "import sys; sys.stdout.write('x' * 2000000 + '\\nlast\\n')"])
        self.assertEqual(bounded["gate"], "pass")
        self.assertLessEqual(len(bounded["stdout_tail"]), 5)
        self.assertTrue(all(len(line) <= MAX_LINE + 1 for line in bounded["stdout_tail"]))
        self.assertEqual(bounded["stdout_tail"][-1], "last")


if __name__ == "__main__":
    unittest.main()
