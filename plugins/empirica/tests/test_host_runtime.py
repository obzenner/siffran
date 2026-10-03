"""The host-recorded runtime provenance of a StartRun (Empirica 4.1, D7).

Three layers, each with cases that can fail: the pure rule against the shared accept/reject fixture
(the Pi adapter runs the same file), the bridge that enforces it where the exact profile is bound, and
the persistence/projection contract (stored once with the run, never shown in a run view).
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from adapters import bridge  # noqa: E402
from application import host_runtime, protocol  # noqa: E402
from core.projection import project_invocation  # noqa: E402
from governance_setup import TEST_INVOCATION, host_runtime_for, invocation_for  # noqa: E402

CASES = json.loads((ROOT / "tests/fixtures/host-runtime-cases.json").read_text(encoding="utf-8"))["cases"]
PI = next(p for p in protocol._HOST_PROFILES["profiles"] if p["host_id"] == "pi")
CLAUDE = next(p for p in protocol._HOST_PROFILES["profiles"] if p["host_id"] == "claude-code")
PROFILES = {"external": PI, "none": CLAUDE}


def start(profile: dict, invocation: dict) -> dict:
    return {"protocol": "empirica/v2", "request_id": "r", "command": {
        "type": "StartRun", "control_mode": "deliberative", "goal": "g",
        "selector": {"project": "p", "session": "s"}, "invocation": invocation}}


class HostRuntimeRuleTests(unittest.TestCase):
    def test_every_shared_case_is_decided_as_the_fixture_states(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                self.assertEqual(host_runtime.rejection(PROFILES[case["profile"]], case["host_runtime"]),
                                 case["expect"], case["why"])

    def test_the_cases_cover_every_rejection_code_and_both_profile_kinds(self):
        codes = {case["expect"] for case in CASES}
        self.assertEqual(codes, {None, "missing", "malformed", "policy", "package", "version", "paths", "unexpected"})
        self.assertEqual({case["profile"] for case in CASES}, {"external", "none"})

    def test_the_rule_reads_the_profile_not_a_literal(self):
        other = copy.deepcopy(PI)
        other["subagents_compatibility"]["reviewed_versions"] = ["0.75.1"]
        good = next(case["host_runtime"] for case in CASES if case["id"] == "newest-reviewed")
        self.assertEqual(host_runtime.rejection(other, good), "version")
        self.assertEqual(host_runtime.rejection(PI, good), None)

    def test_the_audit_admission_comparison_is_exact_equality_of_the_recorded_object(self):
        good = next(case["host_runtime"] for case in CASES if case["id"] == "newest-reviewed")
        self.assertTrue(host_runtime.same_runtime(good, copy.deepcopy(good)))
        self.assertTrue(host_runtime.same_runtime(None, None))
        for label, observed in (
                ("absent", None), ("not an object", "x"), ("empty", {}),
                ("another policy", {**good, "policy_id": "other-v1"}),
                ("another version", {**good, "subagents": {**good["subagents"], "version": "0.64.0"}}),
                ("another root", {**good, "subagents": {**good["subagents"], "package_root": "/x"}}),
                ("an extra member", {**good, "subagents": {**good["subagents"], "extra": 1}}),
                ("a dropped member", {**good, "subagents": {k: v for k, v in good["subagents"].items() if k != "source"}})):
            with self.subTest(label):
                self.assertFalse(host_runtime.same_runtime(good, observed))
                self.assertFalse(host_runtime.same_runtime(observed, good))
        self.assertFalse(host_runtime.same_runtime(None, good))


class HostRuntimeBridgeTests(unittest.TestCase):
    """The shared bridge is where a profile is bound to a request: it refuses before any run exists."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = {key: os.environ.get(key) for key in ("EMPIRICA_HOME", "EMPIRICA_REPO_DIR")}
        os.environ["EMPIRICA_HOME"] = str(Path(self._tmp.name) / "state")
        repo = Path(self._tmp.name) / "repo"
        repo.mkdir()
        os.system(f"git init -q {repo}")
        os.environ["EMPIRICA_REPO_DIR"] = str(repo)
        self.addCleanup(self._restore)

    def _restore(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_a_pi_start_without_the_recorded_runtime_is_refused_closed(self):
        response = bridge.handle(start(PI, dict(TEST_INVOCATION)), PI["profile_id"])
        self.assertEqual(response["result"], {"type": "Fault", "code": "invalid_request", "fail_direction": "closed"})

    def test_a_pi_start_with_an_unreviewed_version_is_refused_closed(self):
        runtime = host_runtime_for(PI["profile_id"])
        runtime["subagents"]["version"] = "9.9.9"
        response = bridge.handle(start(PI, {**TEST_INVOCATION, "host_runtime": runtime}), PI["profile_id"])
        self.assertEqual(response["result"]["code"], "invalid_request")

    def test_a_malformed_runtime_is_refused_by_the_request_schema_before_the_profile_is_consulted(self):
        runtime = host_runtime_for(PI["profile_id"])
        runtime["extra"] = True
        response = bridge.handle(start(PI, {**TEST_INVOCATION, "host_runtime": runtime}), PI["profile_id"])
        self.assertEqual(response["result"]["code"], "invalid_request")

    def test_a_host_without_an_external_runtime_cannot_record_one(self):
        runtime = host_runtime_for(PI["profile_id"])
        response = bridge.handle(start(CLAUDE, {**TEST_INVOCATION, "host_runtime": runtime}), CLAUDE["profile_id"])
        self.assertEqual(response["result"]["code"], "invalid_request")

    def test_a_valid_start_persists_the_runtime_and_never_projects_it(self):
        invocation = invocation_for(PI["profile_id"])
        response = bridge.handle(start(PI, invocation), PI["profile_id"])
        self.assertEqual(response["result"]["type"], "Allow", response)
        self.assertEqual(response["result"]["run"]["invocation"], project_invocation(invocation))
        self.assertNotIn("host_runtime", response["result"]["run"]["invocation"])
        self.assertNotIn("host_runtime", json.dumps(response["result"]))
        runs = list(Path(os.environ["EMPIRICA_HOME"]).glob("projects/*/runs/*/gen-*/run.json"))
        self.assertEqual(len(runs), 1)
        stored = json.loads(runs[0].read_text(encoding="utf-8"))
        self.assertEqual(stored["invocation"]["host_runtime"], invocation["host_runtime"])

    def test_a_claude_start_is_unaffected(self):
        response = bridge.handle(start(CLAUDE, dict(TEST_INVOCATION)), CLAUDE["profile_id"])
        self.assertEqual(response["result"]["type"], "Allow", response)


if __name__ == "__main__":
    unittest.main()
