#!/usr/bin/env python3
"""Bounded exact-v2 activation lifecycle tests for the Claude hooks (D6-C C2a).

The seven thin hooks remain importable and return honest native unsupported/fail-closed behavior
through the strict v2 shell. At D6 the no-location run port returns unsupported/closed. Executable
Agent and investigative-tool admission must distinguish exact ``Inert/no_run`` from that
unavailability and therefore deny; observational hooks remain non-wedging. Removed/trusted actions
are never dispatched. Compatibility/migration assertions are intentionally absent.
"""
from __future__ import annotations

import json
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[3]
HOOKS = PLUGIN / "hooks"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

_ENTRYPOINTS = {
    "run_start.py": "run_start_main",
    "spawn_gate.py": "spawn_main",
    "route_stamp.py": "route_main",
    "convergence_gate.py": "completion_main",
    "state_restore.py": "restore_main",
    "subagent_stop.py": "subagent_stop_main",
}


def _run(hook: str, payload: dict, cwd: Path) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input=json.dumps(payload), text=True, capture_output=True, cwd=str(cwd),
        env={"PYTHONPATH": str(PLUGIN), "PATH": ""},  # hermetic: in-process bridge, no host tools
    )
    return proc.returncode, proc.stdout, proc.stderr


_FIXTURES = Path(__file__).resolve().parents[5] / "contracts" / "empirica" / "v2" / "fixtures"


def _typed(result: dict):
    """Build a start response through the same validated boundary as production."""
    from adapters.claude.transport import Response
    return Response.from_envelope({"request_id": "r"},
                                  {"protocol": "empirica/v2", "request_id": "r", "result": result})


def _payload(**extra: object) -> dict:
    base = {"session_id": "activation-session", "cwd": "."}
    base.update(extra)
    return base


class ClaudeAuditorAliasTests(unittest.TestCase):
    def _alias(self, model, env):
        from adapters.claude import lifecycle
        with patch.object(lifecycle, "_parse_current_assistant_model", return_value=model):
            return lifecycle._auditor_alias({"transcript_path": "main.jsonl"}, env)

    def test_current_author_is_chronological_and_does_not_inherit_missing_model(self):
        from adapters.claude.lifecycle import _parse_current_assistant_model
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "main.jsonl"
            def write(*models):
                transcript.write_text("\n".join(json.dumps({"message": {
                    "role": "assistant", "model": model, "content": "served"}})
                    if model is not None else json.dumps({"message": {
                        "role": "assistant", "content": [{"type": "tool_use", "name": "tool"}]}})
                    for model in models) + "\n", encoding="utf-8")
            write("claude-opus-4-6", "claude-sonnet-4-6", "claude-opus-4-6")
            self.assertEqual(_parse_current_assistant_model(str(transcript)), "claude-opus-4-6")
            write("claude-opus-4-6", "claude-sonnet-4-6", "claude-opus-4-6", "claude-sonnet-4-6")
            self.assertEqual(_parse_current_assistant_model(str(transcript)), "claude-sonnet-4-6")
            write("claude-opus-4-6", None)
            self.assertIsNone(_parse_current_assistant_model(str(transcript)))

    def test_explicitly_malformed_message_fails_the_whole_transcript_scan(self):
        # Regression (M7 final review): the shared reader must not skip a present but
        # non-mapping ``message``; a valid verdict next to a corrupt row must not be read
        # as a valid transcript (that let an auditor child complete and the run converge).
        from adapters.claude.lifecycle import (
            _parse_current_assistant_model, _parse_transcript_contents, _parse_transcript_handbacks)
        verdict = json.dumps({"message": {"role": "assistant", "model": "claude-opus-4-6",
                                           "content": [{"type": "tool_use", "name": "SubagentHandback",
                                                        "input": {"message": "verdict"}}]}})
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "auditor.jsonl"
            for malformed in (None, [], "broken"):
                with self.subTest(message=malformed):
                    transcript.write_text(
                        verdict + "\n" + json.dumps({"message": malformed}) + "\n", encoding="utf-8")
                    self.assertEqual(_parse_transcript_handbacks(str(transcript)), (False, []))
                    self.assertEqual(_parse_transcript_contents(str(transcript)), ([], None))
                    self.assertIsNone(_parse_current_assistant_model(str(transcript)))
            # Positive control: rows without a message field are not messages and are skipped.
            transcript.write_text(verdict + "\n" + json.dumps({"type": "summary"}) + "\n", encoding="utf-8")
            self.assertEqual(_parse_transcript_handbacks(str(transcript)), (True, ["verdict"]))

    def test_anthropic_api_chooses_first_distinct_family(self):
        self.assertEqual(self._alias("claude-fable-5-1", {}), "opus")
        self.assertEqual(self._alias("claude-opus-4-8", {}), "fable")
        self.assertEqual(self._alias("claude-sonnet-5", {}), "fable")
        self.assertEqual(self._alias("claude-haiku-4-5", {}), "fable")

    def test_explicit_subagent_model_is_left_to_the_host(self):
        self.assertIsNone(self._alias("claude-opus-4-8", {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"}))
        self.assertEqual(self._alias("claude-opus-4-8", {"CLAUDE_CODE_SUBAGENT_MODEL": "inherit"}), "fable")

    def test_bedrock_uses_only_pinned_distinct_alias(self):
        env = {"CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_DEFAULT_OPUS_MODEL": "eu.opus"}
        self.assertEqual(self._alias("claude-sonnet-5", env), "opus")
        with self.assertRaisesRegex(RuntimeError, "ANTHROPIC_DEFAULT_FABLE_MODEL"):
            self._alias("claude-opus-4-8", env)

    def test_unobservable_main_family_fails_closed_with_pin_guidance(self):
        with self.assertRaisesRegex(RuntimeError, "CLAUDE_CODE_SUBAGENT_MODEL"):
            self._alias(None, {})


class ThinHookTests(unittest.TestCase):
    """Every hook is a thin entrypoint that imports its lifecycle function."""

    def test_all_seven_entrypoints_import_their_lifecycle_function(self) -> None:
        for name, function in _ENTRYPOINTS.items():
            text = (HOOKS / name).read_text(encoding="utf-8")
            self.assertIn(f"import {function}", text, name)
            self.assertIn("adapters.claude.lifecycle", text, name)
            # thin: a small number of non-blank lines
            self.assertLessEqual(len([line for line in text.splitlines() if line.strip()]), 12, name)

    def test_lifecycle_functions_remain_importable(self) -> None:
        from adapters.claude.lifecycle import (
            completion_main, restore_main, route_main, run_start_main,
            spawn_main, subagent_stop_main,
        )
        for fn in (run_start_main, spawn_main, route_main, completion_main,
                   restore_main, subagent_stop_main):
            self.assertTrue(callable(fn))


class HookNativeBehaviorTests(unittest.TestCase):
    """Honest native unsupported/fail-closed behavior at D6 (no run identity)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_run_start_remains_fail_open_when_workspace_cannot_start(self) -> None:
        code, _out, err = _run("run_start.py",
                               _payload(command_name="empirica:empirica", command_args="prove X"),
                               self.cwd)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

    def test_run_start_injects_handle_and_public_tool_contract(self) -> None:
        from adapters.claude import lifecycle

        result = json.loads((_FIXTURES / "start-bootstrap-allow.json").read_text())["expected"]["result"]
        result["run"]["id"] = "er2:opaque:checksum"
        response = _typed(result)
        output = io.StringIO()
        with patch.object(lifecycle, "_payload", return_value={}), \
             patch.object(lifecycle, "dispatch_start_run", return_value=response), \
             redirect_stdout(output):
            code = lifecycle.run_start_main()

        self.assertEqual(code, 0)
        value = json.loads(output.getvalue())
        context = value["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(value["hookSpecificOutput"]["hookEventName"], "UserPromptExpansion")
        self.assertIn("Opaque run handle: er2:opaque:checksum", context)
        self.assertIn("empirica_observe", context)
        self.assertIn("empirica_read", context)
        self.assertIn("report_convergence", context)

    def test_unknown_flags_refuse_activation_before_start(self) -> None:
        from adapters.claude import lifecycle
        payload = _payload(command_name="empirica:empirica", command_args="--cli-exec --multi-provider prove X")
        output = io.StringIO()
        with patch.object(lifecycle, "_payload", return_value=payload), \
             patch.object(lifecycle, "dispatch_start_run") as start, redirect_stdout(output):
            self.assertEqual(lifecycle.run_start_main(), 0)
        start.assert_not_called()
        value = json.loads(output.getvalue())
        self.assertEqual(value["decision"], "block")
        self.assertIn("unknown flags: --cli-exec --multi-provider", value["reason"])

    def test_start_transport_exception_and_malformed_result_are_operator_visible(self) -> None:
        from adapters.claude import lifecycle
        for returned, side_effect in (({"result": []}, None), (None, RuntimeError("bridge down"))):
            output = io.StringIO()
            with self.subTest(side_effect=side_effect), \
                 patch.object(lifecycle, "_payload", return_value={}), \
                 patch.object(lifecycle, "dispatch_start_run", return_value=returned,
                              side_effect=side_effect), redirect_stdout(output):
                self.assertEqual(lifecycle.run_start_main(), 0)
            value = json.loads(output.getvalue())
            self.assertEqual(value["decision"], "block")
            self.assertIn("internal error", value["reason"])

        output = io.StringIO()
        with patch.object(lifecycle, "_payload", side_effect=RuntimeError("bad payload")), \
             redirect_stdout(output):
            self.assertEqual(lifecycle.run_start_main(), 0)
        self.assertIn("internal error", json.loads(output.getvalue())["reason"])

    def test_faulted_start_blocks_expansion_with_operator_visible_reason(self) -> None:
        from adapters.claude import lifecycle
        response = _typed({"type": "Fault", "code": "invalid_request", "message": "bad request",
                           "fail_direction": "closed"})
        output = io.StringIO()
        with patch.object(lifecycle, "_payload", return_value={}), \
             patch.object(lifecycle, "dispatch_start_run", return_value=response), \
             redirect_stdout(output):
            self.assertEqual(lifecycle.run_start_main(), 0)
        value = json.loads(output.getvalue())
        self.assertEqual(value["decision"], "block")
        self.assertIn("invalid_request", value["reason"])

    def test_refused_start_blocks_expansion_with_operator_visible_reason(self) -> None:
        from adapters.claude import lifecycle

        for code_value in ("run.goal_required", "governance.auto_invocation_required"):
            response = _typed({"type": "Block", "reasons": [{
                "code": code_value, "parameters": {}, "next_actions": [], "sections": [],
                "message": "remedy text"}]})
            output = io.StringIO()
            with self.subTest(code=code_value), \
                 patch.object(lifecycle, "_payload", return_value={}), \
                 patch.object(lifecycle, "dispatch_start_run", return_value=response), \
                 patch.object(lifecycle, "_governance_context") as governance, \
                 redirect_stdout(output):
                self.assertEqual(lifecycle.run_start_main(), 0)
                value = json.loads(output.getvalue())
                self.assertEqual(value["decision"], "block")
                self.assertIn("remedy text", value["reason"])
                self.assertNotIn("hookSpecificOutput", value)
                governance.assert_not_called()

    def test_spawn_non_launch_is_inert(self) -> None:
        code, out, err = _run("spawn_gate.py",
                              _payload(tool_name="Agent", tool_input={"action": "list"}),
                              self.cwd)
        self.assertEqual((code, out, err), (0, "", ""))

    def test_spawn_real_launch_is_inert_when_no_run(self) -> None:
        code, out, err = _run(
            "spawn_gate.py",
            _payload(tool_name="Agent", tool_input={"subagent_type": "worker", "prompt": "do work"}),
            self.cwd,
        )
        self.assertEqual((code, out, err), (0, "", ""))

    def test_spawn_real_launch_missing_purpose_fails_closed_locally(self) -> None:
        # A real launch shape with no prompt (no real purpose) fails closed locally and never
        # dispatches a fabricated reservation.
        code, out, err = _run(
            "spawn_gate.py",
            _payload(tool_name="Agent", tool_input={"subagent_type": "worker"}),
            self.cwd,
        )
        self.assertEqual(code, 2)
        self.assertIn("spawn denied", err)

    def test_route_investigation_is_inert_when_no_run(self) -> None:
        code, out, err = _run("route_stamp.py",
                              _payload(tool_name="Grep", tool_input={"pattern": "x"}),
                              self.cwd)
        self.assertEqual((code, out, err), (0, "", ""))

    def test_completion_is_inert_when_no_run(self) -> None:
        # D6: no run identity -> no active run to gate -> Stop is inert (does not wedge the host).
        code, out, err = _run("convergence_gate.py", _payload(hook_event_name="Stop"), self.cwd)
        self.assertEqual((code, out, err), (0, "", ""))

    def test_restore_is_silent_and_exit_zero(self) -> None:
        code, out, err = _run("state_restore.py",
                              _payload(source="compact", hook_event_name="SessionStart"), self.cwd)
        self.assertEqual((code, out, err), (0, "", ""))

    def test_subagent_stop_is_observational_and_never_blocks(self) -> None:
        code, out, err = _run(
            "subagent_stop.py",
            _payload(agent_type="empirica:empirica-auditor", last_assistant_message="no verdict"),
            self.cwd,
        )
        self.assertEqual(code, 0)


class IsolatedHookResolutionTests(unittest.TestCase):
    """Real hook subprocess coverage for inactive and damaged isolated sessions."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "state"
        self.nongit = self.root / "nongit"
        self.repo = self.root / "repo"
        self.nongit.mkdir()
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, hook: str, payload: dict, cwd: Path,
             overrides: dict | None = None) -> tuple[int, str, str]:
        env = {key: value for key, value in os.environ.items() if not key.startswith("EMPIRICA_")}
        env.update({"EMPIRICA_HOME": str(self.home), "GIT_CEILING_DIRECTORIES": str(self.root)})
        env.update(overrides or {})
        proc = subprocess.run([sys.executable, str(HOOKS / hook)], input=json.dumps(payload),
                              text=True, capture_output=True, cwd=cwd, env=env, timeout=30)
        return proc.returncode, proc.stdout, proc.stderr

    @staticmethod
    def _event(session: object, cwd: object, **extra: object) -> dict:
        payload = {"session_id": session, "cwd": cwd}
        payload.update(extra)
        return payload

    def _start_active(self, session: str = "session-a") -> None:
        code, _out, err = self._run(
            "run_start.py", self._event(session, str(self.repo), command_name="empirica:empirica",
                                         command_args="test lifecycle"),
            self.repo)
        self.assertEqual((code, err), (0, ""))

    def test_inactive_real_hooks_are_inert_in_non_git_and_git_workspaces(self) -> None:
        for cwd in (self.nongit, self.repo):
            with self.subTest(cwd=cwd.name):
                payload = self._event("never-started", str(cwd), tool_name="Grep",
                                      tool_input={"pattern": "x"})
                self.assertEqual(self._run("route_stamp.py", payload, cwd), (0, "", ""))
                agent = self._event("never-started", str(cwd), tool_name="Agent",
                                    tool_input={"subagent_type": "worker", "prompt": "work"})
                self.assertEqual(self._run("spawn_gate.py", agent, cwd), (0, "", ""))
                stop = self._event("never-started", str(cwd), hook_event_name="Stop")
                self.assertEqual(self._run("convergence_gate.py", stop, cwd), (0, "", ""))

    def test_active_session_does_not_gate_other_session_or_nonrepo_project(self) -> None:
        self._start_active()
        same_repo = self._event("session-b", str(self.repo), tool_name="Grep", tool_input={"pattern": "x"})
        other_project = self._event("session-a", str(self.nongit), tool_name="Grep", tool_input={"pattern": "x"})
        self.assertEqual(self._run("route_stamp.py", same_repo, self.repo), (0, "", ""))
        self.assertEqual(self._run("route_stamp.py", other_project, self.nongit), (0, "", ""))

    def test_active_missing_artifacts_fail_closed_on_agent_and_stop(self) -> None:
        self._start_active()
        refs = subprocess.run(["git", "-C", str(self.repo), "for-each-ref", "--format=%(refname)",
                               "refs/empirica/artifacts"], text=True, capture_output=True, check=True).stdout.split()
        self.assertEqual(len(refs), 1)
        subprocess.run(["git", "-C", str(self.repo), "update-ref", "-d", refs[0]], check=True)
        agent = self._event("session-a", str(self.repo), tool_name="Agent",
                            tool_input={"subagent_type": "worker", "prompt": "work"})
        stop = self._event("session-a", str(self.repo), hook_event_name="Stop")
        self.assertEqual(self._run("spawn_gate.py", agent, self.repo)[0], 2)
        self.assertEqual(self._run("convergence_gate.py", stop, self.repo)[0], 2)

    def test_post_model_switch_updates_read_only_context_without_revoking_approval(self) -> None:
        from adapters import bridge
        from adapters.claude import CLAUDE_PROFILE_ID
        sys.path.insert(0, str(PLUGIN / "tests"))
        from test_governance import GRAPH
        self._start_active()
        env = {"EMPIRICA_HOME": str(self.home), "EMPIRICA_REPO_DIR": str(self.repo)}
        with patch.dict(os.environ, env):
            # Use the persisted public handle, not a fabricated selector.
            from adapters.claude.selector import selector_from_payload
            selector = selector_from_payload(self._event("session-a", str(self.repo)))
            resolved = bridge.handle({"protocol": "empirica/v2", "request_id": "resolve",
                "command": {"type": "ResolveRun", "selector": selector}}, CLAUDE_PROFILE_ID)
            handle = resolved["result"]["run"]["id"]
            bridge.handle({"protocol": "empirica/v2", "request_id": "graph",
                "command": {"type": "ObserveAction", "run_id": handle,
                            "action": {"kind": "graph", "payload": GRAPH}}}, CLAUDE_PROFILE_ID)
            context = {"author": {"provider_id": "anthropic", "model_id": "claude-sonnet-4-6"},
                "ingress": "mcp_elicitation"}
            bridge.handle({"protocol": "empirica/v2", "request_id": "config", "command": {
                "type": "ObserveAction", "run_id": handle, "action": {"kind": "configure_run",
                    "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2},
                    "rationale": "sized for the model-switch lifecycle regression"}}}, CLAUDE_PROFILE_ID)
            g = bridge.trusted_governance_context(CLAUDE_PROFILE_ID, handle, context)["result"]["run"]["governance"]
            decision = {"run_id": handle, "receipt_id": "test-ui", "proposal_digest": g["proposal_digest"],
                "plan_revision": g["plan_revision"], "approval_kind": "host_ui"}
            presented = bridge.trusted_governance_decision(CLAUDE_PROFILE_ID, handle, {**decision, "outcome": "present"})
            self.assertEqual(presented["result"]["type"], "Allow")
            approved = bridge.trusted_governance_decision(CLAUDE_PROFILE_ID, handle, {**decision,
                "submission": {"action": "approve", "configuration": {
                    "budgets": g["proposal"]["budgets"]}}})
            self.assertEqual(approved["result"]["type"], "Allow")
            self.assertEqual(approved["result"]["run"]["governance"]["state"], "approved")
            before_digest = g["proposal_digest"]
            event = self._event("session-a", str(self.repo), hook_event_name="PostModelSwitch", to_model="claude-opus-4-6")
            self.assertEqual(self._run("route_stamp.py", event, self.repo, env)[0], 0)
            result = bridge.handle({"protocol": "empirica/v2", "request_id": "read",
                "command": {"type": "GetRun", "run_id": handle}}, CLAUDE_PROFILE_ID)["result"]["run"]["governance"]
            self.assertEqual(result["state"], "approved")
            self.assertEqual(result["proposal_digest"], before_digest)
            self.assertEqual(result["context"]["author"]["model_id"], "claude-opus-4-6")

    def test_inactive_git_marker_with_git_failure_is_not_proven_absent(self) -> None:
        fakebin = self.root / "bin"
        fakebin.mkdir()
        git = fakebin / "git"
        git.write_text("#!/bin/sh\necho 'dubious ownership' >&2\nexit 128\n")
        git.chmod(0o755)
        event = self._event("never-started", str(self.repo), tool_name="Grep", tool_input={})
        self.assertEqual(self._run("route_stamp.py", event, self.repo, {"PATH": str(fakebin)})[0], 2)

    def test_active_git_unavailable_does_not_change_project_identity(self) -> None:
        self._start_active()
        for hook, extra in (
            ("route_stamp.py", {"tool_name": "Grep", "tool_input": {"pattern": "x"}}),
            ("spawn_gate.py", {"tool_name": "Agent", "tool_input": {"subagent_type": "worker", "prompt": "work"}}),
            ("convergence_gate.py", {"hook_event_name": "Stop"}),
        ):
            with self.subTest(hook=hook):
                event = self._event("session-a", str(self.repo), **extra)
                self.assertEqual(self._run(hook, event, self.repo, {"PATH": ""})[0], 2)
        event = self._event("never-started", str(self.nongit), tool_name="Grep", tool_input={})
        self.assertEqual(self._run("route_stamp.py", event, self.nongit, {"PATH": ""}), (0, "", ""))

    def test_active_resolution_fault_blocks_stop(self) -> None:
        self._start_active()
        event = self._event("session-a", str(self.repo), hook_event_name="Stop")
        self.assertEqual(self._run("convergence_gate.py", event, self.repo,
                                   {"EMPIRICA_REPO_DIR": str(self.nongit)})[0], 2)

    def test_active_corrupt_run_fails_closed(self) -> None:
        self._start_active()
        run_doc = next(self.home.glob("projects/*/runs/*/gen-1/run.json"))
        run_doc.write_text("{not-json", encoding="utf-8")
        route = self._event("session-a", str(self.repo), tool_name="Grep", tool_input={"pattern": "x"})
        self.assertEqual(self._run("route_stamp.py", route, self.repo)[0], 2)

    def test_malformed_session_or_path_denies_admission_and_stop_remains_nonwedging(self) -> None:
        malformed = (
            self._event("", str(self.nongit), tool_name="Grep", tool_input={"pattern": "x"}),
            self._event("session", "", tool_name="Agent", tool_input={"subagent_type": "worker", "prompt": "work"}),
        )
        self.assertEqual(self._run("route_stamp.py", malformed[0], self.nongit)[0], 2)
        self.assertEqual(self._run("spawn_gate.py", malformed[1], self.nongit)[0], 2)
        stop = self._event("", str(self.nongit), hook_event_name="Stop")
        self.assertEqual(self._run("convergence_gate.py", stop, self.nongit), (0, "", ""))


class TransportProfileTests(unittest.TestCase):
    """The in-process hook transport reaches the bridge with the exact Claude profile."""

    def test_run_start_dispatches_start_run_with_claude_profile(self) -> None:
        captured: dict = {}
        import adapters.bridge as bridge

        def fake_handle(request, profile_id=None):
            captured["profile_id"] = profile_id
            captured["command_type"] = request["command"]["type"]
            return {"protocol": "empirica/v2", "request_id": request["request_id"],
                    "result": {"type": "Fault", "code": "unsupported", "fail_direction": "closed"}}

        with patch_target(bridge, "handle", fake_handle):
            from adapters.claude.run_start import dispatch_start_run
            dispatch_start_run(_payload(command_args="prove X"), environ={})
        self.assertEqual(captured["profile_id"], "claude-code@2.1.278")
        self.assertEqual(captured["command_type"], "StartRun")


def patch_target(module, name, fake):
    from unittest.mock import patch
    return patch.object(module, name, side_effect=fake)


if __name__ == "__main__":
    unittest.main()
