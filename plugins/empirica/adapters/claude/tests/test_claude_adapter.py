#!/usr/bin/env python3
"""Bounded exact-v2 tests for the Claude adapter slice (D6-C C2a).

These tests assert the retained request builders produce ``contracts/empirica/v2/request.schema.json``-
valid envelopes with the exact v2 shapes, that the transport reaches the shared bridge with the
fixed exact Claude profile (no ``cwd``), that correlation is exact v2, and that response mapping is
honest native unsupported/fail-closed.  Compatibility/migration/audit-ticket/trusted-submission
assertions are intentionally absent: those surfaces are deleted and must not be reintroduced.
"""
from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

import jsonschema  # noqa: E402

from adapters import bridge  # noqa: E402
from adapters.claude import (  # noqa: E402
    CLAUDE_PROFILE_ID,
    PROTOCOL,
    build_child_reserve_request,
    build_configure_run_request,
    build_dispatch_request,
    build_get_argument_request,
    build_investigation_request,
    build_resolve_request,
    build_restore_request,
    build_route_announcement_request,
    build_start_run_request,
    build_stop_request,
)
from adapters.claude import lifecycle  # noqa: E402
from adapters.claude.completion import stop_result  # noqa: E402
from adapters.claude.correlation import CorrelationError, correlate, request_id  # noqa: E402
from adapters.claude.fail_direction import (  # noqa: E402
    FailureDirection,
    blocks_on_failure,
    failure_direction,
)
from adapters.claude.invocation import parse_invocation  # noqa: E402
from adapters.claude.restore import restore_context  # noqa: E402
from adapters.claude.selector import SelectorError  # noqa: E402
from adapters.claude.spawn import spawn_decision  # noqa: E402
from adapters.claude.transport import BridgeTransport  # noqa: E402

_REQUEST_SCHEMA = json.loads(
    (PLUGIN_ROOT.parent.parent / "contracts" / "empirica" / "v2" / "request.schema.json").read_text(
        encoding="utf-8")
)


def _assert_valid(request: dict) -> None:
    jsonschema.validate(instance=request, schema=_REQUEST_SCHEMA)


def _payload(**extra: object) -> dict:
    base = {"session_id": "claude-session", "cwd": "."}
    base.update(extra)
    return base


class ExactV2ProfileTests(unittest.TestCase):
    """The transport reaches the shared bridge with the fixed exact Claude profile and no cwd."""

    def test_profile_id_is_the_exact_claude_registry_profile(self) -> None:
        self.assertEqual(CLAUDE_PROFILE_ID, "claude-code@2.1.278")

    def test_transport_dispatches_via_bridge_handle_with_profile_and_no_cwd(self) -> None:
        captured: dict = {}

        def fake_handle(request, profile_id=None):
            captured["request"] = request
            captured["profile_id"] = profile_id
            return {"protocol": PROTOCOL, "request_id": request["request_id"],
                    "result": {"type": "Inert", "reason": "recorded"}}

        with patch.object(bridge, "handle", side_effect=fake_handle):
            resp = BridgeTransport().dispatch(
                {"protocol": PROTOCOL, "request_id": "t1",
                 "command": {"type": "GetArgument", "run_id": "opaque-run"}}
            )
        self.assertEqual(captured["profile_id"], CLAUDE_PROFILE_ID)
        self.assertIsNone(captured["request"].get("cwd"))
        self.assertEqual(resp["result"]["type"], "Inert")

    def test_transport_through_real_bridge_returns_v2_and_correlated(self) -> None:
        request = {"protocol": PROTOCOL, "request_id": "real-1",
                   "command": {"type": "GetArgument", "run_id": "opaque-run"}}
        resp = BridgeTransport().dispatch(request)
        self.assertEqual(resp["protocol"], PROTOCOL)
        self.assertEqual(resp["request_id"], "real-1")
        # D7 strict location: a malformed opaque handle cannot select storage.
        self.assertEqual(resp["result"], {"type": "Inert", "reason": "no_run"})


class CorrelationTests(unittest.TestCase):
    def test_request_id_mints_a_claude_correlation_hint(self) -> None:
        rid = request_id({"prompt_id": "p1"}, "stop")
        self.assertTrue(rid.startswith("claude:stop:p1:"))
        self.assertTrue(request_id({}, "op").startswith("claude:op:event:"))

    def test_correlate_accepts_exact_v2_echo(self) -> None:
        request = {"request_id": "one"}
        response = {"protocol": PROTOCOL, "request_id": "one", "result": {"type": "Inert"}}
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


class StartRunTests(unittest.TestCase):
    def test_minimal_start_run_is_schema_valid_with_no_actor_or_budgets(self) -> None:
        request = build_start_run_request(
            _payload(command_name="empirica:empirica"), correlation_id="start-1", environ={},
        )
        _assert_valid(request)
        self.assertEqual(request["protocol"], PROTOCOL)
        self.assertEqual(request["request_id"], "start-1")
        self.assertEqual(request["command"]["type"], "StartRun")
        self.assertNotIn("actor", request["command"])
        self.assertNotIn("budgets", request["command"])
        self.assertNotIn("modes", request["command"])
        self.assertTrue(request["command"]["selector"]["project"])
        self.assertTrue(request["command"]["selector"]["session"])

    def test_actor_field_is_never_emitted_even_when_model_present(self) -> None:
        request = build_start_run_request(
            _payload(model="claude-sonnet-4-5", command_args="design X"),
            correlation_id="start-2", environ={},
        )
        _assert_valid(request)
        self.assertNotIn("actor", request["command"])
        self.assertEqual(request["command"]["goal"], "design X")

    def test_budgets_nest_only_supplied_values_and_omit_absent(self) -> None:
        request = build_start_run_request(
            _payload(command_args="prove X"), correlation_id="start-3",
            environ={"EMPIRICA_MAX_PASSES": "5"},
        )
        _assert_valid(request)
        self.assertEqual(request["command"]["budgets"], {"max_passes": 5})

        request_both = build_start_run_request(
            _payload(command_args="prove X"), correlation_id="start-4",
            environ={"EMPIRICA_MAX_PASSES": "5", "EMPIRICA_MAX_SPAWNS": "3",
                     "EMPIRICA_MAX_AUDIT_SPAWNS": "2"},
        )
        _assert_valid(request_both)
        self.assertEqual(request_both["command"]["budgets"],
                         {"max_passes": 5, "max_spawns": 3, "max_audit_spawns": 2})

    def test_modes_emitted_only_when_resolved(self) -> None:
        request = build_start_run_request(
            _payload(command_args="--cli-exec prove X"), correlation_id="start-5", environ={},
        )
        _assert_valid(request)
        self.assertEqual(request["command"]["modes"], {"cli_exec": True})

    def test_missing_session_is_rejected_before_transport(self) -> None:
        with self.assertRaises(SelectorError):
            build_start_run_request({"cwd": "."}, correlation_id="bad", environ={})


class ResolveRunTests(unittest.TestCase):
    def test_resolve_is_exact_v2_selector(self) -> None:
        request = build_resolve_request(_payload(), correlation_id="resolve-1")
        _assert_valid(request)
        self.assertEqual(request["command"], {"type": "ResolveRun",
                                              "selector": {"project": request["command"]["selector"]["project"],
                                                           "session": request["command"]["selector"]["session"]}})


class EvaluateRunTests(unittest.TestCase):
    def test_stop_is_exact_report_convergence_with_no_numeric_observed_at(self) -> None:
        request = build_stop_request(_payload(hook_event_name="Stop"), "opaque-run",
                                      correlation_id="stop-1")
        _assert_valid(request)
        self.assertNotIn("observed_at", request["command"])
        self.assertEqual(request["command"], {"type": "EvaluateRun", "run_id": "opaque-run",
                                              "intent": "report_convergence"})

    def test_observed_at_is_string_only_never_numeric(self) -> None:
        request = build_stop_request(
            _payload(timestamp="2026-09-05T20:00:00Z"), "opaque-run", correlation_id="stop-ts",
        )
        _assert_valid(request)
        self.assertEqual(request["command"]["observed_at"], "2026-09-05T20:00:00Z")
        self.assertIsInstance(request["command"]["observed_at"], str)
        # numeric/int/float/bool/null are omitted, never stringified as seq:
        for bad in (37, 3.14, True, False, None):
            request = build_stop_request(_payload(event_ts=bad), "opaque-run",
                                         correlation_id="stop-ts")
            _assert_valid(request)
            self.assertNotIn("observed_at", request["command"])


class RestoreAndGetArgumentTests(unittest.TestCase):
    def test_restore_is_exact_v2(self) -> None:
        request = build_restore_request(_payload(hook_event_name="SessionStart"), "opaque-run",
                                        correlation_id="restore-1")
        _assert_valid(request)
        self.assertEqual(request["command"], {"type": "RestoreRun", "run_id": "opaque-run"})

    def test_get_argument_is_exact_v2(self) -> None:
        request = build_get_argument_request(_payload(), "opaque-run",
                                              correlation_id="arg-1")
        _assert_valid(request)
        self.assertEqual(request["command"], {"type": "GetArgument", "run_id": "opaque-run"})


class RouteAndInvestigateTests(unittest.TestCase):
    def test_investigation_hook_fails_closed_for_active_run(self) -> None:
        payload = _payload(tool_name="Read", tool_input={"file_path": "package.json"})
        block = {"protocol": PROTOCOL, "request_id": "r", "result": {
            "type": "Block", "run": {"id": "run", "status": "active"},
            "reasons": [{"code": "route.required", "parameters": {},
                         "next_actions": ["route.record"], "sections": ["route"]}]}}
        allow = {"protocol": PROTOCOL, "request_id": "r", "result": {
            "type": "Allow", "converged": False,
            "run": {"id": "run", "status": "active"}}}
        with patch.object(lifecycle, "_payload", return_value=payload), \
             patch.object(lifecycle, "_resolve", return_value=("run", {})), \
             patch.object(lifecycle, "dispatch_investigation", return_value=block):
            self.assertEqual(lifecycle.route_main(), 2)
        with patch.object(lifecycle, "_payload", return_value=payload), \
             patch.object(lifecycle, "_resolve", return_value=("run", {})), \
             patch.object(lifecycle, "dispatch_investigation", return_value=allow):
            self.assertEqual(lifecycle.route_main(), 0)
        with patch.object(lifecycle, "_payload", return_value=payload), \
             patch.object(lifecycle, "_resolve", return_value=("run", {})), \
             patch.object(lifecycle, "dispatch_investigation", side_effect=RuntimeError("down")):
            self.assertEqual(lifecycle.route_main(), 2)

    def test_investigation_hook_without_active_run_is_inert(self) -> None:
        with patch.object(lifecycle, "_payload", return_value=_payload(tool_name="Read")), \
             patch.object(lifecycle, "_resolve", return_value=(None, None)), \
             patch.object(lifecycle, "dispatch_investigation") as dispatch:
            self.assertEqual(lifecycle.route_main(), 0)
            dispatch.assert_not_called()

    def test_investigation_and_spawn_fail_closed_when_resolution_is_unavailable(self) -> None:
        read = _payload(tool_name="Read", tool_input={"file_path": "package.json"})
        agent = _payload(tool_name="Agent", tool_input={
            "subagent_type": "worker", "prompt": "investigate"})
        for failure in (
            RuntimeError("transport down"),
            {"protocol": PROTOCOL, "request_id": "claude-resolve", "result": {
                "type": "Fault", "code": "internal", "message": "unavailable",
                "fail_direction": "closed"}},
            {"protocol": PROTOCOL, "request_id": "claude-resolve", "result": {
                "type": "Allow", "converged": False}},
        ):
            effect = failure if isinstance(failure, Exception) else None
            value = None if effect is not None else failure
            with self.subTest(failure=failure), \
                 patch.object(lifecycle, "_payload", return_value=read), \
                 patch.object(lifecycle, "dispatch_resolve",
                              side_effect=effect, return_value=value):
                self.assertEqual(lifecycle.route_main(), 2)
            with self.subTest(failure=failure, hook="spawn"), \
                 patch.object(lifecycle, "_payload", return_value=agent), \
                 patch.object(lifecycle, "dispatch_resolve",
                              side_effect=effect, return_value=value):
                self.assertEqual(lifecycle.spawn_main(), 2)

    def test_strict_resolution_distinguishes_exact_no_run_from_unavailability(self) -> None:
        response = {"protocol": PROTOCOL, "request_id": "claude-resolve",
                    "result": {"type": "Inert", "reason": "no_run"}}
        with patch.object(lifecycle, "dispatch_resolve", return_value=response):
            self.assertEqual(lifecycle._resolve(_payload(), strict=True),
                             (None, response["result"]))

    def test_investigation_is_exact_v2_and_marker_text_cannot_bypass_it(self) -> None:
        request = build_investigation_request(
            _payload(tool_name="Grep", tool_input={"pattern": "x"}, event_ts=37),
            "run", correlation_id="investigate-1",
        )
        _assert_valid(request)
        self.assertEqual(request["command"]["action"], {"kind": "investigate"})
        self.assertNotIn("observed_at", request["command"])  # numeric omitted, never seq:
        # Bash marker text is untrusted and cannot exempt native investigation.
        own = _payload(tool_name="Bash",
                       tool_input={"command": "route_stamp.py --announce-route --session s"})
        self.assertEqual(build_investigation_request(own, "run")["command"]["action"],
                         {"kind": "investigate"})

    def test_unknown_mcp_research_and_writers_require_admission_but_public_preparation_does_not(self):
        for name in ("mcp__web__search", "Write", "Edit", "custom_tool",
                     "mcp__other__empirica_read"):
            with self.subTest(tool=name):
                request = build_investigation_request(_payload(tool_name=name), "run")
                self.assertEqual(request["command"]["action"], {"kind": "investigate"})
        for name in ("mcp__plugin_empirica_empirica__empirica_read",
                     "mcp__plugin_empirica_empirica__empirica_observe",
                     "mcp__plugin_empirica_empirica__report_convergence", "ToolSearch", "AskUserQuestion"):
            self.assertIsNone(build_investigation_request(_payload(tool_name=name), "run"))

    def test_route_announcement_is_exact_v2(self) -> None:
        request = build_route_announcement_request(
            _payload(tool_name="Bash", tool_input={"command": "announce"}),
            "run", reason="known/unknown split", correlation_id="route-1",
        )
        _assert_valid(request)
        self.assertEqual(request["command"]["action"], {"kind": "route",
                                                         "reason": "known/unknown split"})
        self.assertNotIn("observed_at", request["command"])


class DispatchTests(unittest.TestCase):
    def test_dispatch_emits_only_target_and_optional_claim(self) -> None:
        payload = _payload(tool_name="Bash",
                           tool_input={"command": "codex exec --model openai.gpt-5.6-sol resolve G4"},
                           ts="2026-09-05T20:01:00Z")
        request = build_dispatch_request(payload, "run", claim_id="G4", correlation_id="dispatch-1")
        _assert_valid(request)
        self.assertEqual(request["command"]["action"],
                         {"kind": "dispatch", "target": "codex", "claim_id": "G4"})
        self.assertEqual(request["command"]["observed_at"], "2026-09-05T20:01:00Z")

    def test_dispatch_omits_claim_id_when_absent_and_is_inert_for_plain_bash(self) -> None:
        payload = _payload(tool_name="Bash", tool_input={"command": "grep -rn claude src/"})
        self.assertIsNone(build_dispatch_request(payload, "run"))
        dispatched = _payload(tool_name="Bash",
                              tool_input={"command": "pi -p prove X"})
        request = build_dispatch_request(dispatched, "run", correlation_id="dispatch-2")
        _assert_valid(request)
        self.assertEqual(request["command"]["action"], {"kind": "dispatch", "target": "pi"})


class ConfigureRunTests(unittest.TestCase):
    def test_mode_becomes_exact_configure_run(self) -> None:
        automatic = parse_invocation(
            {"command_args": "--auto --cli-exec --multi-provider prove X"}, environ={}, fallback_goal="g",
        )
        self.assertEqual(automatic.control_mode, "auto")
        self.assertTrue(automatic.modes["cli_exec"])
        self.assertTrue(automatic.modes["multi_provider"])
        invocation = parse_invocation(
            {"command_args": "--cli-exec --multi-provider prove X"}, environ={}, fallback_goal="g",
        )
        request = build_configure_run_request("opaque-run", invocation.modes,
                                               correlation_id="configure-1")
        _assert_valid(request)
        self.assertEqual(request["command"]["action"],
                         {"kind": "configure_run",
                          "modes": {"multi_provider": True, "cli_exec": True}})

    def test_configure_run_rejects_unknown_and_empty_modes(self) -> None:
        with self.assertRaises(ValueError):
            build_configure_run_request("opaque-run", {"cli_exex": True}, correlation_id="bad")
        with self.assertRaises(ValueError):
            build_configure_run_request("opaque-run", {}, correlation_id="empty")


class ChildReserveTests(unittest.TestCase):
    def _payload(self, tool_input: dict) -> dict:
        return _payload(tool_name="Agent", tool_input=tool_input)

    def test_child_reserve_is_schema_valid_with_real_inputs(self) -> None:
        request = build_child_reserve_request(
            self._payload({"subagent_type": "empirica:empirica-auditor", "prompt": "audit G0"}),
            "opaque-run", purpose="audit G0", role_profile="empirica:empirica-auditor",
            execution="foreground", correlation_id="reserve-1",
        )
        _assert_valid(request)
        self.assertEqual(request["command"]["action"], {
            "kind": "child_reserve", "purpose": "audit G0",
            "role_profile": "empirica:empirica-auditor", "execution": "foreground",
            "resource_class": "investigation",
        })

    def test_child_reserve_accepts_optional_string_deadline(self) -> None:
        request = build_child_reserve_request(
            self._payload({"subagent_type": "worker", "prompt": "do work"}), "opaque-run",
            purpose="do work", role_profile="worker", execution="foreground",
            deadline="2026-09-05T20:00:00Z", correlation_id="reserve-2",
        )
        _assert_valid(request)
        self.assertEqual(request["command"]["action"]["deadline"], "2026-09-05T20:00:00Z")

    def test_child_reserve_fails_closed_without_real_inputs(self) -> None:
        payload = self._payload({"subagent_type": "worker", "prompt": "do work"})
        for bad in (
            {"purpose": "", "role_profile": "worker", "execution": "foreground"},
            {"purpose": "p", "role_profile": "", "execution": "foreground"},
            {"purpose": "p", "role_profile": "worker", "execution": "async-mode"},
        ):
            with self.assertRaises(ValueError):
                build_child_reserve_request(payload, "opaque-run", **bad)


class ResponseMappingTests(unittest.TestCase):
    """Honest native fail-closed response mapping for the gate events."""

    def test_human_governance_wait_settles_turn_without_convergence(self) -> None:
        def result(state="pending", code="governance.approval_required"):
            return {"type": "Block", "run": {"status": "active", "governance": {
                "state": state, "control_mode": "deliberative",
                "context": {"ingress": "mcp_elicitation"}}},
                "reasons": [{"code": code, "message": "approval required"}]}
        for state, code in (("pending", "governance.approval_required"),
                            ("revision_pending", "governance.revision_required"),
                            ("rejected", "governance.approval_required")):
            mapped = stop_result({"result": result(state, code)})
            self.assertEqual(mapped.exit_code, 0)
            notice = json.loads(mapped.stdout)
            self.assertIn("not converged", notice["systemMessage"])
            self.assertNotIn("converged", notice)
            self.assertNotIn("decision", notice)
        for changed in ("auto", "approved", "missing_context", "mixed", "fault", "terminal", "mismatched_reason"):
            blocked = result()
            if changed == "auto":
                blocked["run"]["governance"]["control_mode"] = "auto"
            elif changed == "approved":
                blocked["run"]["governance"]["state"] = "approved"
            elif changed == "missing_context":
                blocked["run"]["governance"]["context"] = {}
            elif changed == "mixed":
                blocked["reasons"].append({"code": "run.corrupt"})
            elif changed == "fault":
                blocked["type"] = "Fault"
            elif changed == "terminal":
                blocked["run"]["status"] = "converged"
            elif changed == "mismatched_reason":
                blocked["reasons"][0]["code"] = "governance.revision_required"
            with self.subTest(changed=changed):
                self.assertEqual(stop_result({"result": blocked}).exit_code, 2)

    def test_stop_result_inert_allow_block_and_faults(self) -> None:
        self.assertEqual(stop_result({"result": {"type": "Inert", "reason": "no_run"}}).exit_code, 0)
        allow = stop_result({"result": {"type": "Allow", "converged": True,
                                        "run": {"id": "r", "status": "converged"}}})
        self.assertEqual(allow.exit_code, 0)
        self.assertEqual(json.loads(allow.stdout)["type"], "Allow")
        pending_result = {"type": "Block", "run": {"status": "active", "children": [
            {"child_id": "ch-audit", "resource_class": "audit", "state": "pending"},
        ]}, "reasons": [
            {"code": "audit.pending", "message": "Audit child is pending; wait."},
        ]}
        pending = stop_result({"result": pending_result})
        self.assertEqual(pending.exit_code, 0)
        self.assertEqual(json.loads(pending.stdout)["reasons"][0]["code"], "audit.pending")
        self.assertEqual(pending.stderr, "")
        for malformed in (
            {**pending_result, "run": {"status": "active", "children": []}},
            {**pending_result, "run": {"status": "active", "children": [
                {"child_id": "ordinary", "resource_class": "investigation", "state": "pending"}]}},
            {**pending_result, "reasons": [*pending_result["reasons"],
                                           {"code": "graph.missing", "message": "missing"}]},
        ):
            with self.subTest(malformed=malformed):
                self.assertEqual(stop_result({"result": malformed}).exit_code, 2)
        block = stop_result({"result": {"type": "Block", "reasons": [
            {"code": "graph.missing", "message": "not converged"},
            {"code": "route.required", "message": "route first"},
        ]}})
        self.assertEqual((block.exit_code, block.stdout), (2, ""))
        self.assertEqual(block.stderr, "not converged\nroute first\n")
        closed = stop_result({"result": {"type": "Fault", "code": "unsupported",
                                         "fail_direction": "closed", "message": "no eval"}})
        self.assertEqual((closed.exit_code, closed.stdout), (2, ""))
        open_fault = stop_result({"result": {"type": "Fault", "code": "unavailable",
                                             "fail_direction": "open", "message": "bridge"}})
        self.assertEqual((open_fault.exit_code, open_fault.stderr), (0, "bridge\n"))
        self.assertEqual(stop_result({"not": "a response"}).exit_code, 2)

    def test_restore_context_renders_bounded_v2_runview(self) -> None:
        fixture = json.loads((PLUGIN_ROOT.parent.parent / "contracts" / "empirica" / "v2" /
                              "fixtures" / "restore-active.json").read_text(encoding="utf-8"))
        context = restore_context(fixture["expected"])
        self.assertIn("BEGIN UNTRUSTED EMPIRICA RUN DATA", context)
        body = context.split("-----\n", 1)[1].split("\n----- END", 1)[0]
        self.assertEqual(json.loads(body), {"run": fixture["expected"]["result"]["run"]})
        terminal = json.loads(json.dumps(fixture["expected"]))
        terminal["result"]["run"]["status"] = "converged"
        self.assertEqual(restore_context(terminal), "")

    def test_spawn_decision_block_closed_fault_and_malformed(self) -> None:
        self.assertEqual(spawn_decision(
            {"result": {"type": "Block", "reason": "cap exhausted"}}).exit_code, 2)
        self.assertEqual(spawn_decision(
            {"result": {"type": "Fault", "code": "unsupported",
                        "fail_direction": "closed", "message": "no cap"}}).exit_code, 2)
        self.assertEqual(spawn_decision(
            {"result": {"type": "Allow", "run": {"status": "converged"}}}).exit_code, 0)
        self.assertEqual(spawn_decision(
            {"result": {"type": "Fault", "fail_direction": "open"}}).exit_code, 0)
        self.assertEqual(spawn_decision(
            {"result": {"type": "Inert", "reason": "no_run"}}).exit_code, 2)
        self.assertEqual(spawn_decision(
            {"result": {"type": "Surprise"}}).exit_code, 2)
        # malformed/non-object/mismatched deny the native launch
        self.assertEqual(spawn_decision({"not": "a response"}).exit_code, 2)
        self.assertEqual(spawn_decision({"result": "not-a-dict"}).exit_code, 2)
        self.assertEqual(spawn_decision([]).exit_code, 2)

    def test_fail_direction_explicit_wins_and_malformed_uses_fallback(self) -> None:
        fault = {"result": {"type": "Fault", "fail_direction": "closed"}}
        self.assertEqual(failure_direction(fault, fallback=FailureDirection.OPEN),
                         FailureDirection.CLOSED)
        self.assertTrue(blocks_on_failure(fault, fallback=FailureDirection.OPEN))
        self.assertEqual(failure_direction({}, fallback=FailureDirection.OPEN),
                         FailureDirection.OPEN)


class AuditBoundaryTests(unittest.TestCase):
    def test_extracts_exact_single_host_observed_verdict(self) -> None:
        from adapters.audit import verdict_from_final_output
        payload = {"verdict": "pass", "findings": ["checked"],
                   "argument_digest": "sha256:" + "1" * 64,
                   "goal_digest": "sha256:" + "2" * 64,
                   "frozen_scope_digest": None,
                   "deferred_scope_digest": "sha256:" + "3" * 64,
                   "reviewed_claims": [{"claim_id": "C0",
                                        "evidence_digest": "sha256:" + "4" * 64}],
                   "scope_review": None}
        text = "```empirica-verdict\n" + json.dumps(payload) + "\n```"
        self.assertEqual(verdict_from_final_output(text), payload)
        self.assertIsNone(verdict_from_final_output(text + "\n" + text))
        self.assertIsNone(verdict_from_final_output("not a verdict"))


class RemovedAndTrustedSurfacesTests(unittest.TestCase):
    """Removed operations and author-submitted trusted actions have no public builder and fail
    closed locally (the modules are deleted; importing them fails)."""

    REMOVED_MODULES = ("evidence", "knowledge", "spike")
    REMOVED_BUILDERS = (
        "build_audit_ticket_request", "build_audit_verdict_request", "build_attribution_request",
        "build_graph_request", "build_research_request", "build_spike_request",
        "build_regate_requests", "run_spike", "build_mode_request", "build_reserve_spawn_request",
        "goal_and_modes", "is_agent_launch",
    )

    def test_removed_modules_are_not_importable(self) -> None:
        import importlib
        for name in self.REMOVED_MODULES:
            with self.assertRaises(ImportError):
                importlib.import_module(f"adapters.claude.{name}")

    def test_removed_builders_are_not_exported_from_the_adapter(self) -> None:
        import adapters.claude as claude
        for name in self.REMOVED_BUILDERS:
            self.assertFalse(hasattr(claude, name), f"removed builder still exported: {name}")

    def test_no_v1_protocol_or_removed_action_literal_in_claude_runtime(self) -> None:
        import re
        v1 = re.compile(r'empirica/v1')
        removed_actions = ("reserve_spawn", "void_spawn", "consume_audit_ticket",
                            "audit_ticket", '"phase"', '"nonce"')
        for path in sorted(Path(PLUGIN_ROOT, "adapters", "claude").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertFalse(v1.search(text), f"v1 protocol literal in {path.name}")
            for action in removed_actions:
                self.assertNotIn(f'"kind": "{action.strip(chr(34))}"', text,
                                 f"removed action literal in {path.name}")


class SpawnLifecycleTests(unittest.TestCase):
    """Real executable Agent child_reserve path: malformed/exception/closed-Fault deny native
    launch; no-active-run Inert and non-launch remain inert."""

    def _stdin(self, tool_input: dict | None = None) -> StringIO:
        return StringIO(json.dumps({
            "session_id": "activation-session", "cwd": ".",
            "tool_name": "Agent",
            "tool_input": tool_input or {"subagent_type": "worker", "prompt": "do work"},
        }))

    def _bridge_with_handle(self, child_result: dict | None = None, *, raises: bool = False):
        """Patch bridge.handle to resolve a run, then return child_result or raise."""
        def fake_handle(request, profile_id=None):
            cmd = request["command"]
            if cmd.get("type") == "ResolveRun":
                return {"protocol": PROTOCOL, "request_id": request["request_id"],
                        "result": {"type": "Allow", "run": {"id": "active-run"}}}
            if raises:
                raise RuntimeError("bridge exploded")
            return {"protocol": PROTOCOL, "request_id": request["request_id"],
                    "result": child_result}
        return patch.object(bridge, "handle", side_effect=fake_handle)

    def test_auditor_reservation_replaces_author_prompt_and_defers_lifecycle(self) -> None:
        from adapters.audit_protocol import AuditLaunchPlan
        from adapters.claude.lifecycle import spawn_main
        argument = {"argument_digest": "sha256:" + "1" * 64, "claims": []}
        plan = AuditLaunchPlan("claude-code@2.1.278", "active-run", "ch-audit",
                               "empirica:empirica-auditor", argument,
                               auditor={"provider_id": "anthropic", "model_id": "claude-opus-4-6"})
        payload = self._stdin({"subagent_type": "empirica:empirica-auditor",
                               "prompt": "author-controlled prompt"})
        out = StringIO()
        investigation = {"protocol": PROTOCOL, "request_id": "investigate", "result": {
            "type": "Allow", "converged": False,
            "run": {"id": "active-run", "status": "active"}}}
        with patch("adapters.claude.lifecycle._resolve", return_value=("active-run", {})), \
             patch("adapters.claude.lifecycle.dispatch_investigation",
                   return_value=investigation), \
             patch("adapters.claude.lifecycle.AuditProtocol.prepare", return_value=plan), \
             patch("adapters.claude.lifecycle._governance_context"), \
             patch("sys.stdin", new=payload), patch("sys.stdout", new=out):
            self.assertEqual(spawn_main(), 0)
        updated = json.loads(out.getvalue())["hookSpecificOutput"]["updatedInput"]
        self.assertIn("AUDIT DOSSIER", updated["prompt"])
        self.assertNotIn("author-controlled prompt", updated["prompt"])
        self.assertEqual(updated["subagent_type"], "empirica:empirica-auditor")
        self.assertIs(updated["run_in_background"], True)
        self.assertEqual(updated["max_turns"], 8)
        self.assertEqual(set(updated), {"subagent_type", "description", "prompt",
                                        "run_in_background", "max_turns", "model"})
        self.assertEqual(updated["model"], "claude-opus-4-6")

    def test_durable_plan_rejects_missing_operation_or_wrong_role(self) -> None:
        from adapters.claude.lifecycle import _durable_plan
        for operation in (
            {"role_profile": "empirica:empirica-auditor", "argument": {}},
            {"operation_id": "sha256:" + "2" * 64, "role_profile": "other", "argument": {}},
        ):
            with self.subTest(operation=operation), patch(
                "adapters.claude.lifecycle.application_bridge.trusted_audit_plan",
                return_value=operation,
            ):
                self.assertIsNone(_durable_plan("run", "child"))

    def test_subagent_stop_delivers_handback_verdict_before_trailing_prose(self) -> None:
        from adapters.claude.lifecycle import subagent_stop_main
        verdict = {"verdict": "pass", "findings": ["ok"]}
        text = "```empirica-verdict\n" + json.dumps(verdict) + "\n```"
        resolved = ("active-run", {"run": {"children": [
            {"child_id": "ordinary", "purpose": "audit",
             "resource_class": "investigation", "state": "pending"},
            {"child_id": "ch-audit", "purpose": "audit",
             "resource_class": "audit", "state": "pending"}]}})
        argument = {"argument_digest": "sha256:" + "1" * 64, "claims": []}
        with TemporaryDirectory() as directory:
            transcript = Path(directory) / "child.jsonl"
            transcript.write_text(json.dumps({"message": {"role": "assistant",
                "model": "auditor", "content": [
                    {"type": "tool_use", "name": "SubagentHandback",
                     "input": {"message": text}},
                    {"type": "text", "text": "Audit complete."}]}}) + "\n")
            payload = StringIO(json.dumps({"agent_type": "empirica:empirica-auditor",
                "agent_id": "native-1", "agent_transcript_path": str(transcript),
                "last_assistant_message": "Audit complete."}))
            with patch("adapters.claude.lifecycle._resolve", return_value=resolved), \
                 patch("adapters.claude.lifecycle.application_bridge.trusted_audit_plan",
                       return_value={"operation_id": "sha256:" + "2" * 64,
                                     "role_profile": "empirica:empirica-auditor",
                                     "argument": argument}), \
                 patch("adapters.claude.lifecycle.application_bridge.trusted_resolve_child",
                       return_value="ch-audit"), \
                 patch("adapters.claude.lifecycle.AuditProtocol.observe_identities"), \
                 patch("adapters.claude.lifecycle.AuditProtocol.observe_verdict",
                       return_value=True) as deliver, \
                 patch("sys.stdin", new=payload):
                self.assertEqual(subagent_stop_main(), 0)
        self.assertEqual(deliver.call_args.args[2], verdict)

    def test_handback_extraction_preserves_legacy_and_rejects_ambiguity(self) -> None:
        from adapters.claude.lifecycle import _transcript_handbacks
        with TemporaryDirectory() as directory:
            transcript = Path(directory) / "child.jsonl"
            transcript.write_text(json.dumps({"message": {"role": "assistant", "content": [
                {"type": "text", "text": "legacy final output"}]}}) + "\n")
            self.assertEqual(_transcript_handbacks(str(transcript)), (True, []))
            transcript.write_text("\n".join(json.dumps({"message": {
                "role": "assistant", "content": [{"type": "tool_use",
                    "name": "SubagentHandback", "input": {"message": value}}]}})
                for value in ("first", "second")) + "\n")
            self.assertEqual(_transcript_handbacks(str(transcript)),
                             (True, ["first", "second"]))
            transcript.write_text(transcript.read_text() + "{")
            self.assertEqual(_transcript_handbacks(str(transcript)), (False, []))

    def test_subagent_stop_does_not_fallback_past_an_unreadable_handback_transcript(self) -> None:
        from adapters.claude.lifecycle import subagent_stop_main
        valid = "```empirica-verdict\n" + json.dumps({"verdict": "pass"}) + "\n```"
        resolved = ("active-run", {"run": {"children": [{
            "child_id": "ch-audit", "resource_class": "audit", "state": "pending"}]}})
        with TemporaryDirectory() as directory:
            transcript = Path(directory) / "child.jsonl"
            transcript.write_text(json.dumps({"message": {"role": "assistant",
                "model": "auditor", "content": [{"type": "tool_use",
                    "name": "SubagentHandback", "input": {"message": "not a verdict"}}]}})
                + "\n{")
            payload = StringIO(json.dumps({"agent_type": "empirica:empirica-auditor",
                "agent_id": "native-1", "agent_transcript_path": str(transcript),
                "last_assistant_message": valid}))
            with patch("adapters.claude.lifecycle._resolve", return_value=resolved), \
                 patch("adapters.claude.lifecycle.application_bridge.trusted_audit_plan",
                       return_value={"operation_id": "sha256:" + "2" * 64,
                                     "role_profile": "empirica:empirica-auditor",
                                     "argument": {}}), \
                 patch("adapters.claude.lifecycle.application_bridge.trusted_resolve_child",
                       return_value="ch-audit"), \
                 patch("adapters.claude.lifecycle.AuditProtocol.observe_failure") as failed, \
                 patch("adapters.claude.lifecycle.AuditProtocol.observe_verdict") as deliver, \
                 patch("sys.stdin", new=payload):
                self.assertEqual(subagent_stop_main(), 0)
        failed.assert_called_once()
        deliver.assert_not_called()

    def test_spawn_denies_on_adapter_exception_with_active_run(self) -> None:
        from adapters.claude.lifecycle import spawn_main
        with self._bridge_with_handle(raises=True), patch("sys.stdin", new=self._stdin()):
            self.assertEqual(spawn_main(), 2)

    def test_spawn_denies_when_run_disappears_during_reservation(self) -> None:
        from adapters.claude.lifecycle import spawn_main
        inert = {"type": "Inert", "reason": "no_run"}
        with self._bridge_with_handle(inert), patch("sys.stdin", new=self._stdin()):
            self.assertEqual(spawn_main(), 2)

    def test_spawn_denies_on_closed_fault_with_active_run(self) -> None:
        from adapters.claude.lifecycle import spawn_main
        fault = {"type": "Fault", "code": "unsupported",
                 "fail_direction": "closed", "message": "no cap"}
        with self._bridge_with_handle(fault), patch("sys.stdin", new=self._stdin()):
            self.assertEqual(spawn_main(), 2)

    def test_spawn_denies_on_block_with_active_run(self) -> None:
        from adapters.claude.lifecycle import spawn_main
        block = {"type": "Block", "reason": "cap exhausted"}
        with self._bridge_with_handle(block), patch("sys.stdin", new=self._stdin()):
            self.assertEqual(spawn_main(), 2)

    def test_spawn_allows_on_application_allow_with_active_run(self) -> None:
        from adapters.claude.lifecycle import spawn_main
        allow = {"type": "Allow", "run": {"id": "active-run", "status": "active"}}
        with self._bridge_with_handle(allow), patch("sys.stdin", new=self._stdin()):
            self.assertEqual(spawn_main(), 0)

    def test_spawn_remains_inert_when_no_active_run(self) -> None:
        from adapters.claude.lifecycle import spawn_main
        with patch("sys.stdin", new=self._stdin()):
            self.assertEqual(spawn_main(), 0)

    def test_spawn_non_launch_remains_inert(self) -> None:
        from adapters.claude.lifecycle import spawn_main
        payload = StringIO(json.dumps({
            "session_id": "s", "cwd": ".",
            "tool_name": "Agent", "tool_input": {"action": "list"},
        }))
        with patch("sys.stdin", new=payload):
            self.assertEqual(spawn_main(), 0)


if __name__ == "__main__":
    unittest.main()
