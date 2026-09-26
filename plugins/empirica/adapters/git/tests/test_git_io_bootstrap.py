#!/usr/bin/env python3
"""Real-bridge regression for bounded Git process amplification during bootstrap."""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[5]
PI = ROOT / "plugins/empirica/adapters/pi"
PROFILE = "pi@0.84.1+pi-subagents@0.50.0"


class GitIoBootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.real_git = shutil.which("git")
        assert self.real_git is not None
        subprocess.run([self.real_git, "init", "-q", str(self.repo)], check=True)
        self.git_log = self.root / "git.jsonl"
        wrapper_dir = self.root / "bin"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"with open({str(self.git_log)!r}, 'a') as stream:\n"
            "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            f"os.execv({self.real_git!r}, [{self.real_git!r}, *sys.argv[1:]])\n"
        )
        wrapper.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(wrapper_dir) + os.pathsep + os.environ.get("PATH", ""),
            "EMPIRICA_HOME": str(self.root / "state"),
            "EMPIRICA_REPO_DIR": str(self.repo),
            "EMPIRICA_HOST_PROFILE_ID": PROFILE,
        }

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def call(self, command: dict, *, private: bool = False) -> dict:
        request = command if private else {
            "protocol": "empirica/v2", "request_id": "test", "command": command,
        }
        script = PI / ("private_bridge.py" if private else "bridge.py")
        proc = subprocess.run(
            [sys.executable, str(script)], input=json.dumps(request), cwd=self.repo,
            env=self.env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        response = json.loads(proc.stdout)
        return response["result"]

    def test_graph_bootstrap_uses_four_batched_tree_reads_and_keeps_governance_pending(self):
        started = self.call({
            "type": "StartRun", "selector": {"project": "io", "session": "bootstrap"},
            "goal": "Verify deterministic output.",
        })
        run_id = started["run"]["id"]
        context = {
            "operation": "governance_context", "run_id": run_id,
            "payload": {
                "author": {"provider_id": "amazon-bedrock-eu", "model_id": "eu.anthropic.claude-opus-4-8"},
                "ingress": "pi_ui",
            },
        }
        self.assertEqual(self.call(context, private=True)["type"], "Allow")
        self.assertEqual(self.call({
            "type": "ObserveAction", "run_id": run_id,
            "action": {"kind": "route", "reason": "Test a deterministic claim."},
        })["type"], "Allow")
        self.assertIn(self.call(context, private=True)["type"], {"Allow", "Inert"})

        self.git_log.write_text("")
        graph = self.call({
            "type": "ObserveAction", "run_id": run_id,
            "action": {"kind": "graph", "payload": {
                "root": "C0", "claims": [{
                    "id": "C0", "text": "The deterministic command succeeds.",
                    "kind": "needs-experiment", "gating": True,
                }], "edges": [],
            }},
        })
        self.assertEqual(graph["type"], "Allow")

        commands = [json.loads(line) for line in self.git_log.read_text().splitlines()]
        names = [command[0] for command in commands]
        self.assertEqual(len(commands), 29)
        self.assertEqual(Counter(names), Counter({
            "rev-parse": 8, "show-ref": 4, "ls-tree": 4, "cat-file": 4,
            "hash-object": 2, "mktree": 2, "commit-tree": 2, "update-ref": 2, "-C": 1,
        }))
        self.assertTrue(all(command[:2] == ["cat-file", "--batch"]
                            for command in commands if command[0] == "cat-file"))

        run = self.call({"type": "GetRun", "run_id": run_id})["run"]
        self.assertIsNone(run["governance"]["approved_digest"])
        state_files = list((self.root / "state").rglob("run.json"))
        self.assertEqual(len(state_files), 1)
        state = json.loads(state_files[0].read_text())
        self.assertIsNone(state["investigation_stamp"])
        self.assertEqual(state["children"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
