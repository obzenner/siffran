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
    "dispatch_gate.py": "dispatch_main",
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


def _payload(**extra: object) -> dict:
    base = {"session_id": "activation-session", "cwd": "."}
    base.update(extra)
    return base


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
            completion_main, dispatch_main, restore_main, route_main, run_start_main,
            spawn_main, subagent_stop_main,
        )
        for fn in (run_start_main, spawn_main, route_main, dispatch_main, completion_main,
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

        response = {"protocol": "empirica/v2", "request_id": "r", "result": {
            "type": "Allow", "converged": False,
            "run": {"id": "er2:opaque:checksum", "status": "active"},
        }}
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

    def test_spawn_non_launch_is_inert(self) -> None:
        code, out, err = _run("spawn_gate.py",
                              _payload(tool_name="Agent", tool_input={"action": "list"}),
                              self.cwd)
        self.assertEqual((code, out, err), (0, "", ""))

    def test_spawn_real_launch_fails_closed_when_resolution_is_unavailable(self) -> None:
        code, out, err = _run(
            "spawn_gate.py",
            _payload(tool_name="Agent", tool_input={"subagent_type": "worker", "prompt": "do work"}),
            self.cwd,
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("run resolution unavailable", err)

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

    def test_route_investigation_fails_closed_when_resolution_is_unavailable(self) -> None:
        code, out, err = _run("route_stamp.py",
                              _payload(tool_name="Grep", tool_input={"pattern": "x"}),
                              self.cwd)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("investigation denied", err)

    def test_dispatch_non_actor_bash_is_inert(self) -> None:
        code, out, err = _run("dispatch_gate.py",
                              _payload(tool_name="Bash", tool_input={"command": "grep -rn x src/"}),
                              self.cwd)
        self.assertEqual((code, out, err), (0, "", ""))

    def test_dispatch_actor_bash_is_inert_when_no_run(self) -> None:
        code, out, err = _run(
            "dispatch_gate.py",
            _payload(tool_name="Bash", tool_input={"command": "codex exec --model m resolve G"}),
            self.cwd,
        )
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
