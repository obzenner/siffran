#!/usr/bin/env python3
"""Bounded exact-v2 tests for the Codex adapter slice (D6-C C2b).

These tests assert the retained request builders (``StartRun``, ``ResolveRun``) produce
``contracts/empirica/v2/request.schema.json``-valid envelopes with the exact v2 shapes, that the
transport reaches the shared bridge with the fixed exact Codex profile (no ``cwd``), that
correlation is exact v2, that the four hook entrypoints ``ResolveRun`` through the strict shell
and return inert when unresolved (real D6 no-location behaviour), that the activation renders the
exact Fault code (never ``unknown fault``), and that official Codex 0.146.0 hook payload shapes
hold.  Compatibility/migration/audit-ticket/trusted-submission/spike assertions are intentionally
absent: those surfaces are deleted and must not be reintroduced.  Route, investigation, dispatch,
child_reserve, EvaluateRun, RestoreRun, and response-mapping builders are absent-scanned.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

import jsonschema  # noqa: E402

from adapters import bridge  # noqa: E402
from adapters.codex import (  # noqa: E402
    CODEX_PROFILE_ID,
    PROTOCOL,
    SelectorError,
    build_resolve_request,
    build_start_run_request,
)
from adapters.codex.correlation import CorrelationError, correlate, request_id  # noqa: E402
from adapters.codex.lifecycle import explicit_activation  # noqa: E402
from adapters.codex.transport import BridgeTransport  # noqa: E402

_REQUEST_SCHEMA = json.loads(
    (PLUGIN_ROOT.parent.parent / "contracts" / "empirica" / "v2" / "request.schema.json").read_text(
        encoding="utf-8")
)

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


def _assert_valid(request: dict) -> None:
    jsonschema.validate(instance=request, schema=_REQUEST_SCHEMA)


def _payload(**extra: object) -> dict:
    base = {"session_id": "codex-session", "cwd": "."}
    base.update(extra)
    return base


def _official(event: str, cwd: Path = Path("."), **extra: object) -> dict:
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


class ExactV2ProfileTests(unittest.TestCase):
    """The transport reaches the shared bridge with the fixed exact Codex profile and no cwd."""

    def test_profile_id_is_the_exact_codex_registry_profile(self) -> None:
        self.assertEqual(CODEX_PROFILE_ID, "codex-cli@0.146.0")

    def test_stop_hook_deadline_exceeds_managed_audit_deadline(self) -> None:
        from adapters.codex.audit import MANAGED_AUDIT_TIMEOUT_SECONDS
        hooks = json.loads((PLUGIN_ROOT / "hooks" / "codex.json").read_text(encoding="utf-8"))
        stop = hooks["hooks"]["Stop"][0]["hooks"][0]
        self.assertGreater(stop["timeout"], MANAGED_AUDIT_TIMEOUT_SECONDS)

    def test_managed_runner_without_start_ack_rejects_reservation(self) -> None:
        from adapters.codex.audit import execute_audit
        protocol = MagicMock()
        protocol.prepare.return_value = SimpleNamespace(argument={}, child_id="ch-1")
        with patch("adapters.codex.audit.AuditProtocol", return_value=protocol):
            self.assertFalse(execute_audit(
                {}, "run", runner=lambda _p, _m, _c, _started: (0, "no start")))
        protocol.reject.assert_called_once()
        protocol.observe_started.assert_not_called()

    def test_managed_timeout_after_native_start_closes_timed_out(self) -> None:
        from adapters.codex.audit import execute_audit
        protocol = MagicMock()
        protocol.prepare.return_value = SimpleNamespace(argument={}, child_id="ch-1")

        def timeout(_prompt, _model, _cwd, started):
            started("native-1")
            return None, ""

        with patch("adapters.codex.audit.AuditProtocol", return_value=protocol):
            self.assertFalse(execute_audit({}, "run", runner=timeout))
        protocol.observe_started.assert_called_once()
        protocol.observe_failure.assert_called_once_with(
            protocol.prepare.return_value, "native-1", "timed_out")

    def test_transport_dispatches_via_bridge_handle_with_profile_and_no_cwd(self) -> None:
        captured: dict = {}

        def fake_handle(request, profile_id=None):
            captured["request"] = request
            captured["profile_id"] = profile_id
            return {"protocol": PROTOCOL, "request_id": request["request_id"],
                    "result": {"type": "Inert", "reason": "no_run"}}

        with patch.object(bridge, "handle", side_effect=fake_handle):
            resp = BridgeTransport().dispatch(
                {"protocol": PROTOCOL, "request_id": "t1",
                 "command": {"type": "ResolveRun",
                             "selector": {"project": "p", "session": "s"}}}
            )
        self.assertEqual(captured["profile_id"], CODEX_PROFILE_ID)
        self.assertIsNone(captured["request"].get("cwd"))
        self.assertEqual(resp["result"]["type"], "Inert")

    def test_transport_through_real_bridge_returns_v2_unsupported_closed(self) -> None:
        request = build_resolve_request(
            _payload(session_id="s", cwd="."), correlation_id="real-1")
        resp = BridgeTransport().dispatch(request)
        self.assertEqual(resp["protocol"], PROTOCOL)
        self.assertEqual(resp["request_id"], "real-1")
        # D7 strict location: unresolved selectors are inert and cannot select storage.
        self.assertEqual(resp["result"], {"type": "Inert", "reason": "no_run"})


class CorrelationTests(unittest.TestCase):
    def test_request_id_mints_a_codex_correlation_hint(self) -> None:
        rid = request_id({"tool_use_id": "call-1", "turn_id": "turn-1"}, "resolve")
        self.assertTrue(rid.startswith("codex:resolve:call-1:"))
        self.assertTrue(request_id({}, "op").startswith("codex:op:event:"))

    def test_correlate_accepts_exact_v2_echo(self) -> None:
        request = {"request_id": "one"}
        response = {"protocol": PROTOCOL, "request_id": "one", "result": {"type": "Inert", "reason": "no_run"}}
        self.assertEqual(correlate(request, response), response)

    def test_correlate_rejects_wrong_protocol(self) -> None:
        with self.assertRaises(CorrelationError):
            correlate({"request_id": "one"},
                      {"protocol": "empirica/v1", "request_id": "one", "result": {}})

    def test_correlate_rejects_mismatched_id_and_non_object(self) -> None:
        with self.assertRaises(CorrelationError):
            correlate({"request_id": "one"},
                      {"protocol": PROTOCOL, "request_id": "two", "result": {}})
        with self.assertRaises(CorrelationError):
            correlate({"request_id": "one"}, "not-a-dict")


class OfficialShapeTests(unittest.TestCase):
    def test_all_official_payloads_carry_required_fields(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            for event in OFFICIAL_REQUIRED:
                assert_official_input(self, _official(event, cwd))

    def test_pretool_payload_has_no_updated_input_or_child_output(self) -> None:
        """0.146.0 cannot inject a child task or observe its final output through hooks."""
        value = _official("PreToolUse", tool_name="spawn_agent",
                          tool_input={"message": "empirica-auditor"})
        self.assertNotIn("updatedInput", OFFICIAL_OUTPUT_ALLOWED["PreToolUse"])
        self.assertNotIn("final_output", value)
        self.assertIsNone(value.get("last_assistant_message"))


class StartRunTests(unittest.TestCase):
    def test_minimal_start_run_is_schema_valid_with_no_actor_or_budgets(self) -> None:
        request = build_start_run_request(
            _payload(prompt="$empirica prove X"), correlation_id="start-1", environ={},
        )
        _assert_valid(request)
        self.assertEqual(request["protocol"], PROTOCOL)
        self.assertEqual(request["request_id"], "start-1")
        self.assertEqual(request["command"]["type"], "StartRun")
        self.assertNotIn("actor", request["command"])
        self.assertNotIn("budgets", request["command"])
        self.assertNotIn("modes", request["command"])
        self.assertEqual(request["command"]["goal"], "prove X")
        self.assertTrue(request["command"]["selector"]["project"])
        self.assertTrue(request["command"]["selector"]["session"])

    def test_actor_field_is_never_emitted_even_when_model_present(self) -> None:
        request = build_start_run_request(
            _payload(model="gpt-5.6-codex", prompt="$empirica design X"),
            correlation_id="start-2", environ={},
        )
        _assert_valid(request)
        self.assertNotIn("actor", request["command"])
        self.assertEqual(request["command"]["goal"], "design X")

    def test_budgets_nest_only_supplied_values_and_omit_absent(self) -> None:
        request = build_start_run_request(
            _payload(prompt="$empirica prove X"), correlation_id="start-3",
            environ={"EMPIRICA_MAX_PASSES": "5"},
        )
        _assert_valid(request)
        self.assertEqual(request["command"]["budgets"], {"max_passes": 5})

        request_both = build_start_run_request(
            _payload(prompt="$empirica prove X"), correlation_id="start-4",
            environ={"EMPIRICA_MAX_PASSES": "5", "EMPIRICA_MAX_SPAWNS": "3"},
        )
        _assert_valid(request_both)
        self.assertEqual(request_both["command"]["budgets"],
                         {"max_passes": 5, "max_spawns": 3})

    def test_modes_emitted_only_when_resolved(self) -> None:
        request = build_start_run_request(
            _payload(prompt="$empirica --cli-exec prove X"), correlation_id="start-5", environ={},
        )
        _assert_valid(request)
        self.assertEqual(request["command"]["modes"], {"cli_exec": True})

    def test_non_activation_returns_none(self) -> None:
        self.assertIsNone(build_start_run_request(
            _payload(prompt="please discuss empirica"), environ={},
        ))
        self.assertEqual(explicit_activation(_payload(prompt="$empirica --cli-exec design X")),
                         "--cli-exec design X")

    def test_missing_session_is_rejected_before_transport(self) -> None:
        with self.assertRaises(SelectorError):
            build_start_run_request({"cwd": ".", "prompt": "$empirica X"},
                                    correlation_id="bad", environ={})


class ResolveRunTests(unittest.TestCase):
    def test_resolve_is_exact_v2_selector(self) -> None:
        request = build_resolve_request(_payload(), correlation_id="resolve-1")
        _assert_valid(request)
        self.assertEqual(request["command"]["type"], "ResolveRun")
        self.assertTrue(request["command"]["selector"]["project"])
        self.assertTrue(request["command"]["selector"]["session"])


class RemovedAndTrustedSurfacesTests(unittest.TestCase):
    """Removed builders, mappers, classifiers, and trusted actions have no public builder and
    fail closed locally (the modules are deleted; importing them fails)."""

    REMOVED_BUILDERS = (
        "build_audit_ticket_request", "build_audit_verdict_request", "build_attribution_request",
        "build_graph_request", "build_research_request", "build_spike_request",
        "build_regate_requests", "run_spike", "build_reserve_spawn_request",
        "normalised_statements", "event_stamp",
    )
    DELETED_CODEX_BUILDERS = (
        "build_child_reserve_request",
        "build_route_request",
        "build_investigation_request",
        "build_dispatch_request",
        "build_stop_request",
        "build_restore_request",
    )
    DELETED_MAPPERS = (
        "stop_output",
        "spawn_decision",
        "failure_direction",
        "blocks_on_failure",
        "FailureDirection",
        "SpawnDecision",
    )
    DELETED_CLASSIFIERS = (
        "bash_command",
        "route_reason",
        "dispatched_harness",
    )

    def test_knowledge_module_is_not_importable(self) -> None:
        with self.assertRaises(ImportError):
            importlib.import_module("adapters.codex.knowledge")

    def test_removed_builders_are_not_exported_from_the_adapter(self) -> None:
        import adapters.codex as codex
        for name in self.REMOVED_BUILDERS:
            self.assertFalse(hasattr(codex, name), f"removed builder still exported: {name}")

    def test_deleted_codex_builders_are_not_exported_or_defined(self) -> None:
        import adapters.codex as codex
        for name in self.DELETED_CODEX_BUILDERS:
            self.assertFalse(hasattr(codex, name),
                             f"deleted codex builder still exported: {name}")
        from adapters.codex import lifecycle
        for name in self.DELETED_CODEX_BUILDERS:
            self.assertFalse(hasattr(lifecycle, name),
                             f"deleted codex builder still in lifecycle: {name}")

    def test_deleted_mappers_are_not_exported_or_defined(self) -> None:
        import adapters.codex as codex
        for name in self.DELETED_MAPPERS:
            self.assertFalse(hasattr(codex, name),
                             f"deleted mapper still exported: {name}")
        from adapters.codex import lifecycle
        for name in self.DELETED_MAPPERS:
            self.assertFalse(hasattr(lifecycle, name),
                             f"deleted mapper still in lifecycle: {name}")

    def test_deleted_classifiers_are_not_exported_or_defined(self) -> None:
        import adapters.codex as codex
        for name in self.DELETED_CLASSIFIERS:
            self.assertFalse(hasattr(codex, name),
                             f"deleted classifier still exported: {name}")
        from adapters.codex import lifecycle
        for name in self.DELETED_CLASSIFIERS:
            self.assertFalse(hasattr(lifecycle, name),
                             f"deleted classifier still in lifecycle: {name}")

    def test_no_v1_protocol_or_removed_action_literal_in_codex_runtime(self) -> None:
        v1 = re.compile(r'empirica/v1')
        removed_actions = ("reserve_spawn", "void_spawn", "consume_audit_ticket",
                           "audit_ticket", '"phase"', '"nonce"')
        for path in sorted(Path(PLUGIN_ROOT, "adapters", "codex").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertFalse(v1.search(text), f"v1 protocol literal in {path.name}")
            for action in removed_actions:
                self.assertNotIn(f'"kind": "{action.strip(chr(34))}"', text,
                                 f"removed action literal in {path.name}")
            self.assertNotIn('"unknown fault"', text,
                             f"unknown fault literal in {path.name}")


class UnsupportedLifecycleTests(unittest.TestCase):
    """Located D7 lifecycle behavior over isolated real storage."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        repo, home = root / "repo", root / "home"
        repo.mkdir()
        home.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        self._env = patch.dict(os.environ, {
            "EMPIRICA_HOME": str(home), "EMPIRICA_REPO_DIR": str(repo)}, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def _stdin(self, event: str, **extra: object):
        from io import StringIO
        return StringIO(json.dumps(_official(event, **extra)))

    def _run(self, action: str, event: str, **extra: object) -> tuple[int, str]:
        from io import StringIO
        from adapters.codex.lifecycle import main
        out = StringIO()
        with patch("sys.stdin", new=self._stdin(event, **extra)), patch("sys.stdout", new=out):
            rc = main([action])
        return rc, out.getvalue()

    def test_activate_returns_located_run_context(self) -> None:
        rc, out = self._run("activate", "UserPromptSubmit", prompt="$empirica prove X")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        context = data["hookSpecificOutput"]["additionalContext"]
        self.assertIn("er2:", context)
        self.assertNotIn("unknown fault", context)

    def test_activate_output_is_valid_userpromptsubmit_shape(self) -> None:
        rc, out = self._run("activate", "UserPromptSubmit", prompt="$empirica prove X")
        self.assertEqual(rc, 0)
        assert_official_output(self, "UserPromptSubmit", json.loads(out))

    def test_pre_tool_use_spawn_is_inert_without_active_run(self) -> None:
        rc, out = self._run("pre-tool-use", "PreToolUse", tool_name="spawn_agent",
                            tool_input={"message": "audit G0", "agent_type": "auditor"})
        self.assertEqual((rc, out), (0, ""))

    def test_pre_tool_use_bash_is_inert_without_active_run(self) -> None:
        rc, out = self._run("pre-tool-use", "PreToolUse", tool_input={"command": "rg x ."})
        self.assertEqual((rc, out), (0, ""))

    def test_stop_blocks_when_active_run_has_unmet_convergence(self) -> None:
        rc, out = self._run("activate", "UserPromptSubmit", prompt="$empirica prove X")
        self.assertEqual(rc, 0)
        self.assertIn("er2:", out)
        rc, out = self._run("stop", "Stop")
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertEqual(result["decision"], "block")
        self.assertTrue(result["reason"])
        assert_official_output(self, "Stop", result)

    def test_stop_fails_closed_when_evaluation_transport_breaks_after_resolution(self) -> None:
        from adapters.codex.lifecycle import _stop
        resolved = {"protocol": "empirica/v2", "request_id": "x",
                    "result": {"type": "Allow", "converged": False,
                               "run": {"id": "er2:opaque"}}}
        with patch("adapters.codex.lifecycle._dispatch",
                   side_effect=[resolved, RuntimeError("transport down")]):
            result = _stop(_official("Stop"))
        self.assertEqual(result["decision"], "block")
        self.assertIn("unavailable", result["reason"].lower())

    def test_stop_is_inert_without_active_run(self) -> None:
        rc, out = self._run("stop", "Stop")
        self.assertEqual((rc, out), (0, ""))

    def test_restore_is_inert_without_active_run(self) -> None:
        rc, out = self._run("restore", "SessionStart", source="compact")
        self.assertEqual((rc, out), (0, ""))

    def test_restore_is_inert_for_non_compact_source(self) -> None:
        rc, out = self._run("restore", "SessionStart", source="startup")
        self.assertEqual((rc, out), (0, ""))

    def test_unknown_action_returns_nonzero(self) -> None:
        from io import StringIO
        from adapters.codex.lifecycle import main
        err = StringIO()
        with patch("sys.stdin", new=StringIO("{}")), patch("sys.stderr", new=err):
            rc = main(["bogus"])
        self.assertEqual(rc, 1)


class StoreIsolationTests(unittest.TestCase):
    """The v2 adapter reaches only the no-location bridge; no repository state is touched."""

    def test_no_local_state_directory_is_created_on_activation(self) -> None:
        import os
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            repo, home = Path(tmp) / "repo", Path(tmp) / "home"
            repo.mkdir()
            result = subprocess.run(
                [sys.executable, str(PLUGIN_ROOT / "hooks" / "codex_hook.py"), "activate"],
                input=json.dumps(_official("UserPromptSubmit", repo, prompt="$empirica prove X")),
                text=True, capture_output=True, cwd=repo,
                env={**os.environ, "EMPIRICA_HOME": str(home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((repo / ".codex").exists())
            self.assertFalse((repo / ".claude").exists())


if __name__ == "__main__":
    unittest.main()
