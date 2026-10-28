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

from sandbox_isolation import force_absent_sandbox_home  # noqa: E402

from adapters import bridge  # noqa: E402
from application import host_runtime, protocol  # noqa: E402
from core.projection import project_invocation  # noqa: E402
from governance_setup import TEST_INVOCATION, host_runtime_for, invocation_for  # noqa: E402
force_absent_sandbox_home()

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
        other["subagents_compatibility"]["minimum"] = "0.76.0"
        good = next(case["host_runtime"] for case in CASES if case["id"] == "a-reviewed-version")
        self.assertEqual(host_runtime.rejection(other, good), "version")
        self.assertEqual(host_runtime.rejection(PI, good), None)

    def test_the_audit_admission_comparison_is_exact_equality_of_the_recorded_object(self):
        good = next(case["host_runtime"] for case in CASES if case["id"] == "a-reviewed-version")
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

    def test_a_pi_start_with_a_version_outside_the_range_is_refused_closed(self):
        for version in ("9.9.9", "1.0.0", "0.49.9"):
            with self.subTest(version=version):
                runtime = host_runtime_for(PI["profile_id"])
                runtime["subagents"]["version"] = version
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

    def test_the_session_locator_is_stored_once_with_the_run_and_only_in_the_shape_the_contract_names(self):
        # U1-11: the host records where the session lives; it is persisted opaquely, never shown in a run view, and a
        # malformed one is refused by the request schema (the author has no StartRun to put one in).
        session = {"label": "019e-abc", "locator": "/home/u/.pi/sessions/019e-abc.jsonl"}
        response = bridge.handle(start(CLAUDE, {**TEST_INVOCATION, "session": session}), CLAUDE["profile_id"])
        self.assertEqual(response["result"]["type"], "Allow", response)
        self.assertNotIn("019e-abc", json.dumps(response["result"]))
        runs = list(Path(os.environ["EMPIRICA_HOME"]).glob("projects/*/runs/*/gen-*/run.json"))
        self.assertEqual(json.loads(runs[0].read_text(encoding="utf-8"))["invocation"]["session"], session)
        for label, bad in (("empty label", {**session, "label": ""}), ("no locator", {"label": "x"}),
                           ("empty locator", {**session, "locator": ""}), ("an extra member", {**session, "id": 1}),
                           ("a path longer than the bound", {**session, "locator": "x" * 4097}), ("a string", "x")):
            with self.subTest(label):
                refused = bridge.handle(start(CLAUDE, {**TEST_INVOCATION, "session": bad}), CLAUDE["profile_id"])
                self.assertEqual(refused["result"]["code"], "invalid_request")

    def test_a_version_in_range_without_a_compat_record_starts_and_the_run_view_says_so(self):
        runtime = host_runtime_for(PI["profile_id"])
        runtime["subagents"].update(version="0.99.0", reviewed=False)
        invocation = {**TEST_INVOCATION, "host_runtime": runtime}
        response = bridge.handle(start(PI, invocation), PI["profile_id"])
        self.assertEqual(response["result"]["type"], "Allow", response)
        self.assertEqual(response["result"]["run"]["host"]["unreviewed_runtime"],
                         {"package": "pi-subagents", "version": "0.99.0"})
        self.assertNotIn("owner_path", json.dumps(response["result"]), "only the package and version are shown")

    def test_a_start_without_a_reviewed_flag_is_shown_as_not_reviewed(self):
        runtime = host_runtime_for(PI["profile_id"])
        del runtime["subagents"]["reviewed"]
        response = bridge.handle(start(PI, {**TEST_INVOCATION, "host_runtime": runtime}), PI["profile_id"])
        self.assertEqual(response["result"]["type"], "Allow", response)
        self.assertEqual(response["result"]["run"]["host"]["unreviewed_runtime"]["package"], "pi-subagents")

    def test_the_reviewed_flag_is_not_runtime_identity_but_a_version_move_is(self):
        from application.host_runtime import same_runtime
        runtime = host_runtime_for(PI["profile_id"])
        flipped = copy.deepcopy(runtime)
        flipped["subagents"]["reviewed"] = not runtime["subagents"]["reviewed"]
        self.assertTrue(same_runtime(runtime, flipped))
        moved = copy.deepcopy(runtime)
        moved["subagents"]["version"] = "0.99.0"
        self.assertFalse(same_runtime(runtime, moved))

    def test_a_reviewed_runtime_adds_nothing_to_the_run_view(self):
        response = bridge.handle(start(PI, invocation_for(PI["profile_id"])), PI["profile_id"])
        self.assertNotIn("unreviewed_runtime", response["result"]["run"]["host"])

    def test_a_claude_start_is_unaffected(self):
        response = bridge.handle(start(CLAUDE, dict(TEST_INVOCATION)), CLAUDE["profile_id"])
        self.assertEqual(response["result"]["type"], "Allow", response)


if __name__ == "__main__":
    unittest.main()
