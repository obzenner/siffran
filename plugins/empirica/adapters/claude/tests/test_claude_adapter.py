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
from adapters.invocation import split_leading_flags  # noqa: E402
from adapters.claude.restore import restore_context  # noqa: E402
from adapters.claude.selector import SelectorError  # noqa: E402
from adapters.claude.spawn import spawn_decision  # noqa: E402
from adapters.claude.transport import BridgeTransport, Response  # noqa: E402
from application.protocol import author_view_labels, host_profile_id, render_notice  # noqa: E402
CLAUDE_PROFILE = host_profile_id("claude-code")

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


_FIXTURES = PLUGIN_ROOT.parent.parent / "contracts" / "empirica" / "v2" / "fixtures"


def _valid_active_allow() -> dict:
    return json.loads((_FIXTURES / "start-bootstrap-allow.json").read_text())["expected"]["result"]


def _typed(result: dict, request_id: str = "typed-test") -> Response:
    request = {"request_id": request_id}
    return Response.from_envelope(request, {
        "protocol": PROTOCOL, "request_id": request_id, "result": result})


def _active_result(*, children: list[dict] | None = None) -> dict:
    result = json.loads(json.dumps(_valid_active_allow()))
    if children is not None:
        result["run"]["children"] = children
    return result


class ExactV2ProfileTests(unittest.TestCase):
    """The transport reaches the shared bridge with the fixed exact Claude profile and no cwd."""

    def test_profile_id_is_the_exact_claude_registry_profile(self) -> None:
        profiles = json.loads((Path(__file__).resolve().parents[3] / "vendor/contracts/empirica/v2/host-profiles.json").read_text())
        claude = [row["profile_id"] for row in profiles["profiles"] if row["host_id"] == "claude-code"]
        self.assertEqual([CLAUDE_PROFILE_ID], claude)  # the adapter's id is the registry's, read independently

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
            _payload(command_name="empirica:empirica"),
            correlation_id="start-1", environ={},
        )
        _assert_valid(request)
        self.assertEqual(request["protocol"], PROTOCOL)
        self.assertEqual(request["request_id"], "start-1")
        self.assertEqual(request["command"]["type"], "StartRun")
        self.assertEqual(request["command"]["goal"], "")
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

    def test_goal_is_verbatim_and_provenance_uses_env_then_transcript(self) -> None:
        with TemporaryDirectory() as td:
            transcript = Path(td) / "session.jsonl"
            payload = _payload(command_args="  exact goal  ", transcript_path=str(transcript))
            for entrypoint, interactive in (("cli", True), ("sdk-cli", False)):
                transcript.write_text(json.dumps({"entrypoint": entrypoint}) + "\n", encoding="utf-8")
                command = build_start_run_request(payload, environ={})["command"]
                self.assertEqual(command["goal"], "  exact goal  ")
                self.assertEqual(command["invocation"], {
                    "host": "claude", "interactive": interactive,
                    "signal": "transcript.entrypoint", "delegation": False, "audit_retrieval": ["local", "workspace"], "human_surface": "unobserved"})
            unknown = build_start_run_request(_payload(command_args="goal"), environ={})["command"]
            self.assertIsNone(unknown["invocation"]["interactive"])
            self.assertEqual(unknown["invocation"]["signal"], "transcript.entrypoint unavailable")
            for entrypoint, interactive in (("cli", True), ("sdk-cli", False), ("other", None)):
                env = {"CLAUDE_CODE_ENTRYPOINT": entrypoint, "EMPIRICA_AUTO_DELEGATION": "1"}
                provenance = build_start_run_request(payload, environ=env)["command"]["invocation"]
                self.assertEqual(provenance["interactive"], interactive)
                self.assertEqual(provenance["signal"], "CLAUDE_CODE_ENTRYPOINT")
                self.assertTrue(provenance["delegation"])

    def test_shared_invocation_split_cases(self) -> None:
        cases = json.loads((PLUGIN_ROOT.parents[1] / "contracts/empirica/v2/invocation-split-cases.json").read_text())
        for case in cases:
            with self.subTest(args=case["args"]):
                flags, goal = split_leading_flags(case["args"])
                self.assertEqual({"flags": flags, "goal": goal},
                                 {"flags": case["flags"], "goal": case["goal"]})

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
        block = _typed(json.loads(
            (_FIXTURES / "block-open-claim.json").read_text())["expected"]["result"])
        allow = _typed(_valid_active_allow())
        resolved = allow.result
        with patch.object(lifecycle, "_payload", return_value=payload), \
             patch.object(lifecycle, "_resolve", return_value=("run", resolved)), \
             patch.object(lifecycle, "dispatch_investigation", return_value=block):
            self.assertEqual(lifecycle.route_main(), 2)
        with patch.object(lifecycle, "_payload", return_value=payload), \
             patch.object(lifecycle, "_resolve", return_value=("run", resolved)), \
             patch.object(lifecycle, "dispatch_investigation", return_value=allow):
            self.assertEqual(lifecycle.route_main(), 0)
        with patch.object(lifecycle, "_payload", return_value=payload), \
             patch.object(lifecycle, "_resolve", return_value=("run", resolved)), \
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
        failures = [RuntimeError("transport down"), _typed({
            "type": "Fault", "code": "unavailable", "message": "unavailable",
            "fail_direction": "closed"})]
        with self.assertRaises(ValueError):
            _typed({"type": "Allow", "converged": False})
        for failure in failures:
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
        response = _typed({"type": "Inert", "reason": "no_run"}, "claude-resolve")
        with patch.object(lifecycle, "dispatch_resolve", return_value=response):
            self.assertEqual(lifecycle._resolve(_payload(), strict=True),
                             (None, response.result))

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


class RemovedInvocationFlagTests(unittest.TestCase):
    def test_removed_flags_surface_as_unknown(self) -> None:
        invocation = parse_invocation(
            {"command_args": "--auto --cli-exec --multi-provider prove X"}, environ={},
        )
        self.assertEqual(invocation.control_mode, "auto")
        self.assertEqual(invocation.unknown_flags, ("--cli-exec", "--multi-provider"))
        self.assertEqual(invocation.goal, "prove X")

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


def _stop(result: dict, **kwargs):
    """The Stop hook's result; a first Stop (`stop_hook_active` false) unless the caller says otherwise."""
    return stop_result(_typed(result), **{"stop_hook_active": False, **kwargs})


class ResponseMappingTests(unittest.TestCase):
    """Honest native fail-closed response mapping for the gate events."""

    def test_human_governance_wait_settles_turn_without_convergence(self) -> None:
        def result(state="pending", code="governance.approval_required"):
            run = json.loads(json.dumps(_valid_active_allow()["run"]))
            run["governance"].update({
                "state": state, "control_mode": "deliberative"})
            run["governance"]["context"]["ingress"] = "mcp_elicitation"
            return {"type": "Block", "run": run, "reasons": [{
                "code": code, "parameters": {}, "next_actions": [],
                "sections": ["governance"], "message": "approval required"}]}
        for state, code in (("pending", "governance.approval_required"),
                            ("revision_pending", "governance.revision_required"),
                            ("rejected", "governance.approval_required")):
            mapped = _stop(result(state, code))
            self.assertEqual(mapped.exit_code, 0)
            notice = json.loads(mapped.stdout)
            self.assertIn("not converged", notice["systemMessage"])
            self.assertNotIn("converged", notice)
            self.assertEqual(sorted(notice), ["systemMessage"])  # a notice only: no decision, no converged claim
        with patch("adapters.author_view.render_author_view",
                   side_effect=AssertionError("systemMessage must bypass author renderer")):
            notice = json.loads(_stop(result()).stdout)
            self.assertIn("systemMessage", notice)
        initial_auto = result()
        initial_auto["run"]["governance"].update({"control_mode": "auto", "first_approval": False})
        initial_auto["run"]["governance"]["context"]["interactive"] = True
        self.assertEqual(_stop(initial_auto).exit_code, 0)
        for changed in ("proposal_exhausted", "total_exhausted", "prompt_error"):
            blocked = json.loads(json.dumps(initial_auto))
            governance = blocked["run"]["governance"]
            if changed == "prompt_error":
                governance["prompt_error"] = "governance.interaction_limit"
            else:
                governance["interactions_remaining"][changed.split("_")[0]] = 0
            with self.subTest(initial_auto=changed):
                self.assertEqual(_stop(blocked).exit_code, 2)
        for changed in ("auto", "delegated", "proposal_exhausted", "total_exhausted",
                        "prompt_error", "approved", "missing_context", "mixed", "terminal",
                        "mismatched_reason"):
            blocked = result()
            if changed == "auto":
                blocked["run"]["governance"].update({"control_mode": "auto", "first_approval": True})
            elif changed == "delegated":
                blocked["run"]["governance"].update({"control_mode": "auto", "first_approval": False})
                blocked["run"]["governance"]["context"].update({"interactive": False,
                                                                "delegation": True})
            elif changed in ("proposal_exhausted", "total_exhausted"):
                key = changed.split("_")[0]
                blocked["run"]["governance"]["interactions_remaining"][key] = 0
            elif changed == "prompt_error":
                blocked["run"]["governance"]["prompt_error"] = "governance.interaction_limit"
            elif changed == "approved":
                blocked["run"]["governance"]["state"] = "approved"
            elif changed == "missing_context":
                blocked["run"]["governance"]["context"]["ingress"] = "unavailable"
            elif changed == "mixed":
                blocked["reasons"].append({"code": "run.corrupt", "parameters": {},
                                           "next_actions": [], "sections": [],
                                           "message": "corrupt"})
            elif changed == "terminal":
                blocked["run"]["status"] = "converged"
            elif changed == "mismatched_reason":
                blocked["reasons"][0]["code"] = "governance.revision_required"
            with self.subTest(changed=changed):
                self.assertEqual(_stop(blocked).exit_code, 2)

    def test_sole_budget_exhausted_settles_turn_with_the_contract_notice(self) -> None:
        from application.protocol import BUDGET_EXHAUSTED_NOTICE

        def result(resource):
            run = json.loads(json.dumps(_valid_active_allow()["run"]))
            return {"type": "Block", "run": run, "reasons": [{
                "code": "budget.exhausted", "parameters": {"resource": resource},
                "next_actions": ["run.start_fresh", "residual.accept"],
                "sections": ["budget"], "message": f"{resource} budget exhausted."}]}
        for resource in ("audit_spawn", "pass", "spawn"):
            with self.subTest(resource=resource):
                settled = _stop(result(resource))
                self.assertEqual(settled.exit_code, 0)
                self.assertEqual(settled.stderr, "")
                notice = json.loads(settled.stdout)
                self.assertEqual(notice, {"systemMessage":
                                          render_notice(BUDGET_EXHAUSTED_NOTICE, resource=resource)})
                self.assertIn(resource, notice["systemMessage"])
                self.assertNotIn("{resource}", notice["systemMessage"])
        mixed = result("audit_spawn")
        mixed["reasons"].append({"code": "run.corrupt", "parameters": {}, "next_actions": [],
                                 "sections": [], "message": "corrupt"})
        self.assertEqual(_stop(mixed).exit_code, 2)
        terminal = result("audit_spawn")
        terminal["run"]["status"] = "stopped_residual"
        self.assertEqual(_stop(terminal).exit_code, 2)
        unrelated = result("audit_spawn")
        unrelated["reasons"][0].update({"code": "audit.failed", "parameters": {},
                                        "sections": ["audit"], "message": "audit failed"})
        self.assertEqual(_stop(unrelated).exit_code, 2)

    def test_the_stop_never_settles_for_evidence_and_directs_the_author_to_report(self) -> None:
        from application import protocol
        notice = getattr(protocol, "EVIDENCE_REPORT_NOTICE", "<not defined>")
        record = "sha256:" + "a" * 64

        def result(route="human", prompt_error=None, code="evidence.confirmation_required"):
            run = json.loads(json.dumps(_valid_active_allow()["run"]))
            run["confirmation_batch"] = {"key": "sha256:" + "b" * 64, "record_ids": [record],
                                         "claim_ids": [], "prompt_error": prompt_error, "records": [
                                             {"claim_id": "C0", "source_kind": "web", "source_ref": "https://example.test/x"}]}
            parameters = {"route": route, "record_ids": [record]} if code.startswith("evidence") else {}
            return {"type": "Block", "run": run, "reasons": [{
                "code": code, "parameters": parameters,
                "next_actions": ["human.confirm_evidence", "residual.accept"] if route == "human"
                else ["residual.accept"],
                "sections": ["evidence"], "message": "Research waits on a human."}]}
        settled = _stop(result())
        # In Claude the human is asked inside report_convergence, so a pause would end the turn unasked: block, plainly.
        self.assertEqual((settled.exit_code, settled.stderr, settled.stdout), (2, notice + "\n", ""))
        self.assertFalse(hasattr(protocol, "EVIDENCE_WAIT_NOTICE"))
        self.assertEqual(_stop(result("unavailable", "unavailable")).exit_code, 2)  # nobody can answer
        # The host has not looked for a surface yet (it does inside the report): the same directive, never a stop that
        # leaves a human unasked.
        unobserved = _stop(result("unobserved", "unobserved"))
        self.assertEqual((unobserved.exit_code, unobserved.stderr, unobserved.stdout), (2, notice + "\n", ""))
        # The batch was shown already (Later, cancel, timeout), the interactions are spent, or the host said it could
        # not ask: nothing will ask again, so the Stop blocks with the real reason instead of pausing or directing a
        # report that would ask nobody.
        for error in ("already_presented", "interaction_limit", "surface_unrecorded", "presentation_failed"):
            blocked = _stop(result(prompt_error=error))
            self.assertEqual((blocked.exit_code, blocked.stderr), (2, "Research waits on a human.\n"), error)
        # Another claim blocker leads: the open claim's own reason is what the Stop reports.
        other = result(code="claim.research_missing")
        other["reasons"][0]["message"] = "Record research for this claim."
        self.assertEqual(_stop(other).stderr, "Record research for this claim.\n")
        mixed = result()
        mixed["reasons"].append({"code": "run.corrupt", "parameters": {}, "next_actions": [],
                                 "sections": [], "message": "corrupt"})
        self.assertEqual(_stop(mixed).exit_code, 2)
        terminal = result()
        terminal["run"]["status"] = "stopped_residual"
        self.assertEqual(_stop(terminal).exit_code, 2)

    def test_stop_result_inert_allow_block_and_faults(self) -> None:
        self.assertEqual(_stop({"type": "Inert", "reason": "no_run"}).exit_code, 0)
        fixture_root = PLUGIN_ROOT.parents[1] / "contracts" / "empirica" / "v2" / "fixtures"
        allow_result = json.loads(
            (fixture_root / "allow-converged.json").read_text())["expected"]["result"]
        allow = _stop(allow_result)
        self.assertEqual(allow.exit_code, 0)
        self.assertIn("Allow (converged=true)", allow.stdout)
        self.assertIn("run_id: run-fx", allow.stdout)
        pending_result = json.loads(
            (fixture_root / "block-pending-audit.json").read_text())["expected"]["result"]
        pending = _stop(pending_result)
        self.assertEqual(pending.exit_code, 0)
        self.assertIn("audit.pending", pending.stdout)
        self.assertEqual(pending.stderr, "")
        for malformed in (
            {**pending_result, "run": {"status": "active", "children": []}},
            {**pending_result, "run": {"status": "active", "children": [
                {"child_id": "ordinary", "resource_class": "investigation", "state": "pending"}]}},
            {**pending_result, "reasons": [*pending_result["reasons"],
                                           {"code": "graph.missing", "message": "missing"}]},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                _typed(malformed)
        block_result = json.loads(
            (fixture_root / "block-open-claim.json").read_text())["expected"]["result"]
        block_result["reasons"].append({
            "code": "route.required", "parameters": {}, "next_actions": [],
            "sections": [], "message": "route first"})
        block_result["reasons"][0]["message"] = "not converged"
        block = _stop(block_result)
        self.assertEqual((block.exit_code, block.stdout), (2, ""))
        self.assertEqual(block.stderr, "not converged route first.\n")  # one renderer for every Block path (CL-05)
        closed = stop_result(_typed({"type": "Fault", "code": "unsupported",
                                     "fail_direction": "closed", "message": "no eval"}), stop_hook_active=False)
        self.assertEqual((closed.exit_code, closed.stdout), (2, ""))
        open_fault = stop_result(_typed({"type": "Fault", "code": "unavailable",
                                         "fail_direction": "open", "message": "bridge"}), stop_hook_active=False)
        self.assertEqual((open_fault.exit_code, open_fault.stderr), (0, "bridge\n"))
        self.assertEqual(stop_result({"not": "a response"}, stop_hook_active=False).exit_code, 2)

    def test_restore_context_renders_bounded_v2_runview(self) -> None:
        fixture = json.loads((PLUGIN_ROOT.parent.parent / "contracts" / "empirica" / "v2" /
                              "fixtures" / "restore-active.json").read_text(encoding="utf-8"))
        context = restore_context(_typed(fixture["expected"]["result"]))
        self.assertIn("BEGIN UNTRUSTED EMPIRICA RUN DATA", context)
        body = context.split("-----\n", 1)[1].split("\n----- END", 1)[0]
        run = fixture["expected"]["result"]["run"]
        self.assertIn(f"run_id: {run['id']}", body)
        self.assertIn(run["status"], body)
        terminal = json.loads((_FIXTURES / "allow-converged.json").read_text())["expected"]
        self.assertEqual(restore_context(_typed(terminal["result"])), "")

    def test_spawn_decision_block_closed_fault_and_malformed(self) -> None:
        block = json.loads((_FIXTURES / "block-open-claim.json").read_text())["expected"]["result"]
        self.assertEqual(spawn_decision(_typed(block)).exit_code, 2)
        self.assertEqual(spawn_decision(_typed(
            {"type": "Fault", "code": "unsupported",
             "fail_direction": "closed", "message": "no cap"})).exit_code, 2)
        self.assertEqual(spawn_decision(_typed(_valid_active_allow())).exit_code, 0)
        self.assertEqual(spawn_decision(_typed(
            {"type": "Fault", "code": "unavailable", "fail_direction": "open"})).exit_code, 0)
        self.assertEqual(spawn_decision(_typed(
            {"type": "Inert", "reason": "no_run"})).exit_code, 2)
        with self.assertRaises(ValueError):
            _typed({"type": "Surprise"})
        # malformed/non-object/mismatched deny the native launch
        self.assertEqual(spawn_decision({"not": "a response"}).exit_code, 2)
        self.assertEqual(spawn_decision({"result": "not-a-dict"}).exit_code, 2)
        self.assertEqual(spawn_decision([]).exit_code, 2)

    def test_fail_direction_explicit_wins_and_malformed_uses_fallback(self) -> None:
        fault = _typed({"type": "Fault", "code": "unavailable",
                        "fail_direction": "closed"})
        self.assertEqual(failure_direction(fault, fallback=FailureDirection.OPEN),
                         FailureDirection.CLOSED)
        self.assertTrue(blocks_on_failure(fault, fallback=FailureDirection.OPEN))
        self.assertEqual(failure_direction({}, fallback=FailureDirection.OPEN),
                         FailureDirection.OPEN)


class StopModeTests(unittest.TestCase):
    """EHB-STOP-06…08, 11…15, 18…20 (ADR-0068): the Claude Stop depends on the control mode, and only on a continuation.

    ``stop_hook_active`` is Claude's own signal that the previous Stop already blocked this turn. With it absent or
    false nothing changes in any mode (F5, the F7 directive, F8, ADR-0051, ADR-0063 settlements). With it true, a
    non-converged Block that no existing settlement owns ends the hook loop: deliberative hands off with
    ``paused_for_human``; every ``auto`` run (interactive before or after its first approval, delegated) takes the auto
    outcome, ``unattended_continue``, except where ``human_wait`` already settles an approvable first approval, or the
    evidence directive applies. The mode is read only from the run view the core returned."""

    MODES = ("deliberative", "auto-interactive-pre", "auto-interactive-post", "auto-delegated")
    REASON = "Record research for this claim."
    RECORD = "sha256:" + "a" * 64

    @classmethod
    def mode(cls, mode: str, run: dict) -> dict:
        governance = run["governance"]
        governance["context"]["ingress"] = "mcp_elicitation"
        if mode == "deliberative":
            governance.update({"control_mode": "deliberative", "first_approval": False})
            governance["context"].update({"interactive": True, "delegation": False})
        elif mode == "auto-interactive-pre":
            governance.update({"control_mode": "auto", "first_approval": False})
            governance["context"].update({"interactive": True, "delegation": False})
        elif mode == "auto-interactive-post":
            governance.update({"control_mode": "auto", "first_approval": True})
            governance["context"].update({"interactive": True, "delegation": False})
        else:
            governance.update({"control_mode": "auto", "first_approval": True})
            governance["context"].update({"interactive": False, "delegation": True})
        return run

    @classmethod
    def block(cls, mode: str, kind: str) -> dict:
        run = cls.mode(mode, json.loads(json.dumps(_valid_active_allow()["run"])))
        reason = {"code": "claim.research_missing", "parameters": {}, "affected": {"obligation_id": "claim:G0"},
                  "next_actions": ["research.record"], "sections": ["evidence/research"], "message": cls.REASON}
        if kind == "evidence":
            run["confirmation_batch"] = {"key": "sha256:" + "b" * 64, "record_ids": [cls.RECORD], "claim_ids": [],
                                         "prompt_error": None, "records": [
                                             {"claim_id": "C0", "source_kind": "web", "source_ref": "https://example.test/x"}]}
            reason = {"code": "evidence.confirmation_required", "parameters": {"route": "human", "record_ids": [cls.RECORD]},
                      "next_actions": ["human.confirm_evidence"], "sections": ["evidence"],
                      "message": "Research waits on a human."}
        elif kind == "budget":
            reason = {"code": "budget.exhausted", "parameters": {"resource": "pass"}, "next_actions": [],
                      "sections": ["budget"], "message": "pass budget exhausted."}
        elif kind == "audit_pending":
            run["children"] = [{"child_id": "ch-1", "purpose": "audit", "resource_class": "audit", "state": "pending",
                                "deadline": "2026-09-14T00:00:00Z"}]
            reason = {"code": "audit.pending", "parameters": {}, "next_actions": ["child.wait"], "sections": ["audit"],
                      "message": "An audit is pending."}
        elif kind in ("human_wait", "approval_unanswerable"):
            run["governance"].update({"state": "pending"})
            if kind == "approval_unanswerable":  # the approval cannot be asked here: spent, errored or no MCP ingress
                run["governance"]["context"]["ingress"] = "unavailable"
            reason = {"code": "governance.approval_required", "parameters": {}, "next_actions": [], "sections": ["governance"],
                      "message": "approval required"}
        return {"type": "Block", "run": run, "reasons": [reason]}

    stop = staticmethod(_stop)  # a first Stop unless the test says ``stop_hook_active=True``

    @staticmethod
    def notices() -> dict:
        from application.protocol import public_contract
        return public_contract()["settlement_notices"]

    def paused(self, message):
        return {"systemMessage": render_notice(self.notices()["paused_for_human"], reason=message)}

    def test_EHB_STOP_06_the_first_stop_is_unchanged_in_every_mode(self):
        """A first Stop (``stop_hook_active`` false; an absent key is the hook's concern, STOP_18) keeps its exit codes
        and settlements in every mode."""
        notices = self.notices()
        budget, evidence, human_wait = notices["budget_exhausted"], notices["evidence_report"], notices["human_wait"]
        for mode in self.MODES:
            with self.subTest(mode=mode):
                real = self.stop(self.block(mode, "real"))
                self.assertEqual((real.exit_code, real.stdout, real.stderr), (2, "", self.REASON + "\n"))
                if mode != "auto-delegated":  # a delegated run's route is `unavailable`: no human to ask, no directive
                    directive = self.stop(self.block(mode, "evidence"))
                    self.assertEqual((directive.exit_code, directive.stdout, directive.stderr), (2, "", evidence + "\n"))
                spent = self.stop(self.block(mode, "budget"))
                self.assertEqual((spent.exit_code, json.loads(spent.stdout), spent.stderr),
                                 (0, {"systemMessage": render_notice(budget, resource="pass")}, ""))
                wait = self.stop(self.block(mode, "audit_pending"))
                self.assertEqual((wait.exit_code, wait.stderr), (0, ""))
                self.assertNotIn("systemMessage", wait.stdout)  # the view itself, not a settlement notice
                self.assertIn(author_view_labels()["audit"], wait.stdout)
        for mode in ("deliberative", "auto-interactive-pre"):  # the modes whose approval is a human's
            settled = self.stop(self.block(mode, "human_wait"))
            self.assertEqual((settled.exit_code, json.loads(settled.stdout)), (0, {"systemMessage": human_wait}), mode)

    def test_EHB_STOP_18_a_non_boolean_flag_is_not_a_continuation(self):
        """Only the literal ``true`` Claude sends counts; a string or a number is not a signal."""
        from adapters.claude import lifecycle
        for value in ("true", 1, None, "yes"):
            payload = {"session_id": "s", "cwd": ".", "stop_hook_active": value}
            with self.subTest(value=value), \
                 patch.object(lifecycle, "_payload", return_value=payload), \
                 patch.object(lifecycle, "_resolve", return_value=("run", None)), \
                 patch.object(lifecycle, "dispatch_stop", return_value=_typed(self.block("deliberative", "real"))), \
                 patch("sys.stdout", new=StringIO()), patch("sys.stderr", new=StringIO()):
                self.assertEqual(lifecycle.completion_main(), 2)

    def test_EHB_STOP_18_the_hook_reads_stop_hook_active_from_the_payload_and_only_the_literal_true(self):
        from adapters.claude import lifecycle
        payload = {"session_id": "s", "cwd": ".", "stop_hook_active": True}
        out = StringIO()
        with patch.object(lifecycle, "_payload", return_value=payload), \
             patch.object(lifecycle, "_resolve", return_value=("run", None)), \
             patch.object(lifecycle, "dispatch_stop", return_value=_typed(self.block("deliberative", "real"))), \
             patch("sys.stdout", new=out), patch("sys.stderr", new=StringIO()):
            self.assertEqual(lifecycle.completion_main(), 0)
        self.assertIn("paused for you", json.loads(out.getvalue())["systemMessage"])
        payload = {"session_id": "s", "cwd": "."}  # absent: the first Stop
        with patch.object(lifecycle, "_payload", return_value=payload), \
             patch.object(lifecycle, "_resolve", return_value=("run", None)), \
             patch.object(lifecycle, "dispatch_stop", return_value=_typed(self.block("deliberative", "real"))), \
             patch("sys.stdout", new=StringIO()), patch("sys.stderr", new=StringIO()):
            self.assertEqual(lifecycle.completion_main(), 2)

    def test_EHB_STOP_07_deliberative_continuation_hands_off_with_the_real_reason(self):
        for kind, message in (("real", self.REASON), ("evidence", "Research waits on a human.")):
            with self.subTest(kind=kind):
                settled = self.stop(self.block("deliberative", kind), stop_hook_active=True)
                self.assertEqual((settled.exit_code, settled.stderr), (0, ""))
                self.assertEqual(json.loads(settled.stdout), self.paused(message))
                text = json.loads(settled.stdout)["systemMessage"]
                self.assertIn(message, text)
                self.assertIn("not converged", text)
                self.assertIn("Nothing was approved or confirmed", text)
                self.assertIn("nobody was asked", text)
                self.assertNotIn("will be asked", text)

    def test_EHB_STOP_08_auto_runs_block_with_the_way_out_on_a_continuation(self):
        """A real Block on a continuation takes the auto outcome in every auto mode, never a human pause."""
        for mode in ("auto-interactive-pre", "auto-interactive-post", "auto-delegated"):
            with self.subTest(mode=mode):
                blocked = self.stop(self.block(mode, "real"), stop_hook_active=True)
                self.assertEqual((blocked.exit_code, blocked.stdout), (2, ""))
                self.assertEqual(blocked.stderr, render_notice(self.notices()["unattended_continue"], reason=self.REASON) + "\n")
                self.assertIn(self.REASON, blocked.stderr)
                self.assertIn("intent=stop", blocked.stderr)
                self.assertIn("does not hand the turn back", blocked.stderr)
                self.assertNotIn("unattended", blocked.stderr)  # interactive auto has a human present

    def test_EHB_STOP_20_an_auto_continuation_keeps_the_evidence_directive_and_deliberative_hands_off(self):
        """An interactive auto continuation keeps the evidence directive; deliberative hands off with `paused_for_human`."""
        directive = self.notices()["evidence_report"]
        for mode in ("auto-interactive-pre", "auto-interactive-post"):  # a delegated run has no human route to ask
            with self.subTest(mode=mode):
                blocked = self.stop(self.block(mode, "evidence"), stop_hook_active=True)
                self.assertEqual((blocked.exit_code, blocked.stdout, blocked.stderr), (2, "", directive + "\n"))
        paused = self.stop(self.block("deliberative", "evidence"), stop_hook_active=True)
        self.assertEqual((paused.exit_code, paused.stderr), (0, ""))
        self.assertIn("paused for you", json.loads(paused.stdout)["systemMessage"])

    def test_EHB_STOP_11_interactive_auto_before_first_approval_keeps_human_wait_and_the_evidence_directive(self):
        notices = self.notices()
        mode = "auto-interactive-pre"
        for flag in ({}, {"stop_hook_active": True}):  # an approvable first approval settles on either Stop
            settled = self.stop(self.block(mode, "human_wait"), **flag)
            self.assertEqual(json.loads(settled.stdout), {"systemMessage": notices["human_wait"]}, flag)
        first = self.stop(self.block(mode, "real"))  # a first Stop is unchanged
        self.assertEqual((first.exit_code, first.stderr), (2, self.REASON + "\n"))
        directive = self.stop(self.block(mode, "evidence"), stop_hook_active=True)
        self.assertEqual((directive.exit_code, directive.stderr), (2, notices["evidence_report"] + "\n"))

    def test_EHB_STOP_19_an_approval_that_cannot_be_asked_ends_the_loop_by_mode(self):
        """Interactive auto before its first approval, when ``human_wait`` does not apply (no MCP ingress, a prompt error
        or spent interactions), no longer re-blocks with the real reason until Claude's cap (ADR-0068)."""
        for mode in ("auto-interactive-pre", "deliberative"):
            first = self.stop(self.block(mode, "approval_unanswerable"))
            self.assertEqual((first.exit_code, first.stderr), (2, "approval required.\n"), mode)  # the first Stop is unchanged
        blocked = self.stop(self.block("auto-interactive-pre", "approval_unanswerable"), stop_hook_active=True)
        self.assertEqual((blocked.exit_code, blocked.stdout), (2, ""))
        self.assertEqual(blocked.stderr, render_notice(self.notices()["unattended_continue"], reason="approval required.") + "\n")
        paused = self.stop(self.block("deliberative", "approval_unanswerable"), stop_hook_active=True)
        self.assertEqual(json.loads(paused.stdout), self.paused("approval required."))
        for spent in ({"prompt_error": "governance.interaction_limit"},  # interactions spent: human_wait does not apply
                      {"interactions_remaining": {"proposal": 0, "total": 5}}):
            with self.subTest(spent=spent):
                run = self.block("auto-interactive-pre", "human_wait")
                run["run"]["governance"].update(spent)
                blocked = self.stop(run, stop_hook_active=True)
                self.assertEqual(blocked.exit_code, 2)
                self.assertIn("intent=stop", blocked.stderr)

    def test_EHB_STOP_12_existing_settlements_Allow_Inert_and_Fault_are_unchanged_on_a_continuation(self):
        notices = self.notices()
        budget, human_wait = notices["budget_exhausted"], notices["human_wait"]
        for mode in self.MODES:
            with self.subTest(mode=mode):
                spent = self.stop(self.block(mode, "budget"), stop_hook_active=True)
                self.assertEqual(json.loads(spent.stdout), {"systemMessage": render_notice(budget, resource="pass")})
                wait = self.stop(self.block(mode, "audit_pending"), stop_hook_active=True)
                self.assertEqual((wait.exit_code, wait.stderr), (0, ""))
                self.assertNotIn("paused for you", wait.stdout)
                fault = self.stop({"type": "Fault", "code": "unavailable", "message": "core down", "fail_direction": "closed"},
                                  stop_hook_active=True)
                self.assertEqual((fault.exit_code, fault.stderr), (2, "core down\n"))
                allow = _valid_active_allow()
                self.mode(mode, allow["run"])
                self.assertEqual(self.stop(allow, stop_hook_active=True), self.stop(allow))
                self.assertEqual(self.stop({"type": "Inert", "reason": "no_run"}, stop_hook_active=True).exit_code, 0)
        for mode in ("deliberative", "auto-interactive-pre"):
            settled = self.stop(self.block(mode, "human_wait"), stop_hook_active=True)
            self.assertEqual(json.loads(settled.stdout), {"systemMessage": human_wait}, mode)

    def test_EHB_STOP_13_a_missing_view_or_a_terminal_run_blocks(self):
        gone = self.block("deliberative", "real")
        gone["run"]["governance"] = None
        self.assertEqual(self.stop(gone, stop_hook_active=True).exit_code, 2)
        terminal = self.block("deliberative", "real")
        terminal["run"]["status"] = "stopped_residual"
        blocked = self.stop(terminal, stop_hook_active=True)
        self.assertEqual((blocked.exit_code, blocked.stderr), (2, self.REASON + "\n"))  # only an active run can be handed off

    def test_EHB_STOP_13_several_reasons_are_handed_off_in_one_sentence(self):
        two = self.block("deliberative", "real")
        two["reasons"].append({"code": "run.corrupt", "parameters": {}, "next_actions": [], "sections": [], "message": "corrupt"})
        pair = self.stop(two, stop_hook_active=True)
        # more than one reason is still a real Block and is handed off, every reason named in one sentence
        self.assertEqual((pair.exit_code, json.loads(pair.stdout)), (0, self.paused(self.REASON + " corrupt.")))

    def test_EHB_STOP_14_the_rule_is_stateless(self):
        for mode in ("deliberative", "auto-delegated"):
            first = self.stop(self.block(mode, "real"), stop_hook_active=True)
            for _ in range(3):
                self.assertEqual(self.stop(self.block(mode, "real"), stop_hook_active=True), first)

    def test_a_notice_is_rendered_only_with_exactly_its_placeholders(self):
        template = self.notices()["unattended_continue"]
        self.assertIn("{reason}", template)
        for wrong in ({}, {"resource": "x"}, {"reason": "x", "resource": "y"}):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                render_notice(template, **wrong)
        self.assertIn("{braces}", render_notice(template, reason="{braces}"))  # a value is never re-scanned

    def test_EHB_STOP_15_the_notices_come_from_the_contract(self):
        from application.protocol import public_contract
        notices = public_contract()["settlement_notices"]
        from application.protocol import PAUSED_FOR_HUMAN_NOTICE, UNATTENDED_CONTINUE_NOTICE
        self.assertEqual(PAUSED_FOR_HUMAN_NOTICE, notices["paused_for_human"])
        self.assertEqual(UNATTENDED_CONTINUE_NOTICE, notices["unattended_continue"])
        self.assertNotIn("unattended", notices["unattended_continue"])  # an interactive auto run has a human present
        for text in (notices["paused_for_human"], notices["unattended_continue"]):
            self.assertEqual(text.count("{reason}"), 1)
        self.assertNotIn("will be asked", notices["paused_for_human"])


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
        unclosed = "```empirica-verdict\n" + json.dumps({"verdict": "fail"})
        self.assertIsNone(verdict_from_final_output(unclosed + "\n" + text))
        no_newline = "```empirica-verdict " + json.dumps({"verdict": "fail"}) + "```"
        self.assertIsNone(verdict_from_final_output(text + "\n" + no_newline))
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
                result = _valid_active_allow()
            elif child_result and child_result.get("type") == "Allow":
                result = _valid_active_allow()
            else:
                result = child_result
            if raises and cmd.get("type") != "ResolveRun":
                raise RuntimeError("bridge exploded")
            return {"protocol": PROTOCOL, "request_id": request["request_id"],
                    "result": result}
        return patch.object(bridge, "handle", side_effect=fake_handle)

    def test_auditor_reservation_replaces_author_prompt_and_defers_lifecycle(self) -> None:
        from adapters.audit_protocol import AuditLaunchPlan
        from adapters.claude.lifecycle import spawn_main
        argument = {"argument_digest": "sha256:" + "1" * 64, "claims": []}
        plan = AuditLaunchPlan(CLAUDE_PROFILE, "active-run", "ch-audit",
                               "empirica:empirica-auditor", argument)
        payload = self._stdin({"subagent_type": "empirica:empirica-auditor",
                               "prompt": "author-controlled prompt"})
        out = StringIO()
        investigation = _typed(_active_result(), "investigate")
        with patch("adapters.claude.lifecycle._resolve", return_value=("active-run", {})), \
             patch("adapters.claude.lifecycle.dispatch_investigation",
                   return_value=investigation), \
             patch("adapters.claude.lifecycle.AuditProtocol.prepare", return_value=plan), \
             patch("adapters.claude.lifecycle._auditor_alias", return_value="fable"), \
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
        self.assertEqual(updated["model"], "fable")

    def test_auditor_preflight_failure_does_not_reserve(self) -> None:
        from adapters.audit_protocol import AuditProtocolError
        from adapters.claude.lifecycle import spawn_main
        payload = self._stdin({"subagent_type": "empirica:empirica-auditor", "prompt": "audit"})
        investigation = _typed(_active_result(), "investigate")
        with patch("adapters.claude.lifecycle._resolve", return_value=("active-run", {})), \
             patch("adapters.claude.lifecycle.dispatch_investigation", return_value=investigation), \
             patch("adapters.claude.lifecycle._governance_context"), \
             patch("adapters.claude.lifecycle._auditor_alias",
                   side_effect=AuditProtocolError("main identity unknown")), \
             patch("adapters.claude.lifecycle.AuditProtocol.prepare") as prepare, \
             patch("sys.stdin", new=payload):
            self.assertEqual(spawn_main(), 2)
        prepare.assert_not_called()

    def test_auditor_post_reservation_failure_rejects_exact_plan(self) -> None:
        from adapters.audit_protocol import AuditLaunchPlan
        from adapters.claude.lifecycle import spawn_main
        plan = AuditLaunchPlan(CLAUDE_PROFILE, "active-run", "ch-audit",
                               "empirica:empirica-auditor", {"claims": []})
        payload = self._stdin({"subagent_type": "empirica:empirica-auditor", "prompt": "audit"})
        investigation = _typed(_active_result(), "investigate")
        with patch("adapters.claude.lifecycle._resolve", return_value=("active-run", {})), \
             patch("adapters.claude.lifecycle.dispatch_investigation", return_value=investigation), \
             patch("adapters.claude.lifecycle._governance_context"), \
             patch("adapters.claude.lifecycle._auditor_alias", return_value="fable"), \
             patch("adapters.claude.lifecycle.AuditProtocol.prepare", return_value=plan), \
             patch("adapters.claude.lifecycle.AuditProtocol.reject") as reject, \
             patch("adapters.claude.lifecycle.child_prompt", side_effect=RuntimeError("late failure")), \
             patch("sys.stdin", new=payload):
            self.assertEqual(spawn_main(), 2)
        reject.assert_called_once_with(plan)

    def test_auditor_reject_failure_of_any_kind_still_denies(self) -> None:
        """A rollback that raises a non-AuditProtocolError must still deny (quality #4)."""
        from adapters.audit_protocol import AuditLaunchPlan
        from adapters.claude.lifecycle import spawn_main
        plan = AuditLaunchPlan(CLAUDE_PROFILE, "active-run", "ch-audit",
                               "empirica:empirica-auditor", {"claims": []})
        payload = self._stdin({"subagent_type": "empirica:empirica-auditor", "prompt": "audit"})
        investigation = _typed(_active_result(), "investigate")
        with patch("adapters.claude.lifecycle._resolve", return_value=("active-run", {})), \
             patch("adapters.claude.lifecycle.dispatch_investigation", return_value=investigation), \
             patch("adapters.claude.lifecycle._governance_context"), \
             patch("adapters.claude.lifecycle._auditor_alias", return_value="fable"), \
             patch("adapters.claude.lifecycle.AuditProtocol.prepare", return_value=plan), \
             patch("adapters.claude.lifecycle.AuditProtocol.reject",
                   side_effect=RuntimeError("bridge I/O down")) as reject, \
             patch("adapters.claude.lifecycle.child_prompt", side_effect=RuntimeError("late failure")), \
             patch("sys.stdin", new=payload):
            self.assertEqual(spawn_main(), 2)
        reject.assert_called_once_with(plan)

    def test_durable_plan_rejects_missing_operation_or_wrong_role(self) -> None:
        from adapters.claude.lifecycle import _parse_durable_plan
        for operation in (
            {"role_profile": "empirica:empirica-auditor", "argument": {}},
            {"operation_id": "sha256:" + "2" * 64, "role_profile": "other", "argument": {}},
        ):
            with self.subTest(operation=operation), patch(
                "adapters.claude.lifecycle.application_bridge.trusted_audit_plan",
                return_value=operation,
            ):
                self.assertIsNone(_parse_durable_plan("run", "child"))

    def test_subagent_stop_delivers_handback_verdict_before_trailing_prose(self) -> None:
        from adapters.claude.lifecycle import subagent_stop_main
        verdict = {"verdict": "pass", "findings": ["ok"]}
        text = "```empirica-verdict\n" + json.dumps(verdict) + "\n```"
        resolved = ("active-run", _typed(_active_result(children=[
            {"child_id": "ordinary", "purpose": "audit",
             "resource_class": "investigation", "state": "pending"},
            {"child_id": "ch-audit", "purpose": "audit",
             "resource_class": "audit", "state": "pending"}])).result)
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
                 patch("adapters.claude.lifecycle.AuditProtocol.observe_reviewer"), \
                 patch("adapters.claude.lifecycle.AuditProtocol.observe_verdict",
                       return_value=True) as deliver, \
                 patch("sys.stdin", new=payload):
                self.assertEqual(subagent_stop_main(), 0)
        self.assertEqual(deliver.call_args.args[2], verdict)

    def test_handback_extraction_preserves_legacy_and_rejects_ambiguity(self) -> None:
        from adapters.claude.lifecycle import _parse_transcript_handbacks
        with TemporaryDirectory() as directory:
            transcript = Path(directory) / "child.jsonl"
            transcript.write_text(json.dumps({"message": {"role": "assistant", "content": [
                {"type": "text", "text": "legacy final output"}]}}) + "\n")
            self.assertEqual(_parse_transcript_handbacks(str(transcript)), (True, []))
            transcript.write_text("\n".join(json.dumps({"message": {
                "role": "assistant", "content": [{"type": "tool_use",
                    "name": "SubagentHandback", "input": {"message": value}}]}})
                for value in ("first", "second")) + "\n")
            self.assertEqual(_parse_transcript_handbacks(str(transcript)),
                             (True, ["first", "second"]))
            transcript.write_text(transcript.read_text() + "{")
            self.assertEqual(_parse_transcript_handbacks(str(transcript)), (False, []))

    def test_subagent_stop_does_not_fallback_past_an_unreadable_handback_transcript(self) -> None:
        from adapters.claude.lifecycle import subagent_stop_main
        valid = "```empirica-verdict\n" + json.dumps({"verdict": "pass"}) + "\n```"
        resolved = ("active-run", _typed(_active_result(children=[{
            "child_id": "ch-audit", "purpose": "audit", "resource_class": "audit", "state": "pending"}])).result)
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

    def test_subagent_stop_accepts_transcript_verdict_when_handback_has_no_fence(self) -> None:
        """S1 shape: auditor wrote the verdict fence in assistant text, then
        called SubagentHandback with prose only. The transcript verdict wins."""
        from adapters.claude.lifecycle import subagent_stop_main
        verdict = {"verdict": "fail", "findings": ["dossier gap"]}
        fence = "```empirica-verdict\n" + json.dumps(verdict) + "\n```"
        resolved = ("active-run", _typed(_active_result(children=[{
            "child_id": "ch-audit", "purpose": "audit", "resource_class": "audit", "state": "pending"}])).result)
        argument = {"argument_digest": "sha256:" + "1" * 64, "claims": []}
        with TemporaryDirectory() as directory:
            transcript = Path(directory) / "child.jsonl"
            transcript.write_text(json.dumps({"message": {"role": "assistant",
                "model": "auditor", "content": [
                    {"type": "text", "text": fence},
                    {"type": "tool_use", "name": "SubagentHandback",
                     "input": {"message": "verdict delivered above"}}]}}) + "\n")
            payload = StringIO(json.dumps({"agent_type": "empirica:empirica-auditor",
                "agent_id": "native-1", "agent_transcript_path": str(transcript),
                "last_assistant_message": "verdict delivered above"}))
            with patch("adapters.claude.lifecycle._resolve", return_value=resolved), \
                 patch("adapters.claude.lifecycle.application_bridge.trusted_audit_plan",
                       return_value={"operation_id": "sha256:" + "2" * 64,
                                     "role_profile": "empirica:empirica-auditor",
                                     "argument": argument}), \
                 patch("adapters.claude.lifecycle.application_bridge.trusted_resolve_child",
                       return_value="ch-audit"), \
                 patch("adapters.claude.lifecycle.AuditProtocol.observe_reviewer"), \
                 patch("adapters.claude.lifecycle.AuditProtocol.observe_verdict",
                       return_value=True) as deliver, \
                 patch("sys.stdin", new=payload):
                self.assertEqual(subagent_stop_main(), 0)
        self.assertEqual(deliver.call_args.args[2], verdict)

    def test_subagent_stop_fails_closed_on_two_conflicting_verdict_fences(self) -> None:
        """Negative control: two different valid fences in assistant text, handback
        prose only → two different verdicts in the fallback scan → fail closed."""
        from adapters.claude.lifecycle import subagent_stop_main
        verdict_a = {"verdict": "pass", "findings": ["ok"]}
        verdict_b = {"verdict": "fail", "findings": ["nope"]}
        fence_a = "```empirica-verdict\n" + json.dumps(verdict_a) + "\n```"
        fence_b = "```empirica-verdict\n" + json.dumps(verdict_b) + "\n```"
        resolved = ("active-run", _typed(_active_result(children=[{
            "child_id": "ch-audit", "purpose": "audit", "resource_class": "audit", "state": "pending"}])).result)
        with TemporaryDirectory() as directory:
            transcript = Path(directory) / "child.jsonl"
            transcript.write_text(json.dumps({"message": {"role": "assistant",
                "model": "auditor", "content": [
                    {"type": "text", "text": fence_a + "\n" + fence_b},
                    {"type": "tool_use", "name": "SubagentHandback",
                     "input": {"message": "verdict delivered above"}}]}}) + "\n")
            payload = StringIO(json.dumps({"agent_type": "empirica:empirica-auditor",
                "agent_id": "native-1", "agent_transcript_path": str(transcript)}))
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

    def test_subagent_stop_fails_closed_when_handback_prose_and_no_fence_anywhere(self) -> None:
        """Negative control: handback has prose, no fence in transcript or handback."""
        from adapters.claude.lifecycle import subagent_stop_main
        resolved = ("active-run", _typed(_active_result(children=[{
            "child_id": "ch-audit", "purpose": "audit", "resource_class": "audit", "state": "pending"}])).result)
        with TemporaryDirectory() as directory:
            transcript = Path(directory) / "child.jsonl"
            transcript.write_text(json.dumps({"message": {"role": "assistant",
                "model": "auditor", "content": [
                    {"type": "text", "text": "Some analysis."},
                    {"type": "tool_use", "name": "SubagentHandback",
                     "input": {"message": "verdict delivered above"}}]}}) + "\n")
            payload = StringIO(json.dumps({"agent_type": "empirica:empirica-auditor",
                "agent_id": "native-1", "agent_transcript_path": str(transcript)}))
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


class ObservationalHookDiagnosticTests(unittest.TestCase):
    """Host-observational hooks exit 0 on failure and say so on stderr (Stop still gates)."""

    AUDITOR = "empirica:empirica-auditor"

    CASES = (
        ("restore_main", {}, "SessionStart:compact restore"),
        ("agent_failure_main",
         {"tool_input": {"subagent_type": AUDITOR}}, "PostToolUseFailure"),
        ("subagent_start_main",
         {"agent_type": AUDITOR, "agent_id": "native-1"}, "SubagentStart"),
        ("subagent_stop_main",
         {"agent_type": AUDITOR, "agent_id": "native-1"}, "SubagentStop"),
    )

    def _run_hook(self, hook: str, payload: dict, **patches) -> tuple[int, str, str]:
        err, out = StringIO(), StringIO()
        with patch("sys.stdin", new=StringIO(json.dumps(payload))), \
                patch("sys.stderr", new=err), patch("sys.stdout", new=out):
            if "resolve" in patches:
                with patch.object(lifecycle, "dispatch_resolve", **patches["resolve"]):
                    code = getattr(lifecycle, hook)()
            else:
                with patch("adapters.claude.lifecycle._resolve", **patches["_resolve"]):
                    code = getattr(lifecycle, hook)()
        return code, err.getvalue(), out.getvalue()

    def test_real_resolution_failure_reports_one_diagnostic_line_and_exits_zero(self) -> None:
        """Patch dispatch_resolve (not _resolve) so the resolution layer cannot swallow the failure."""
        for hook, payload, name in self.CASES:
            with self.subTest(hook=hook):
                code, stderr, stdout = self._run_hook(
                    hook, payload,
                    resolve={"side_effect": RuntimeError("transport broke\nwith   spaces")})
                self.assertEqual(code, 0)
                self.assertEqual(
                    stderr,
                    f"empirica {name} hook failed: RuntimeError: transport broke with spaces\n")
                self.assertEqual(stdout, "")

    def test_genuine_no_run_stays_silent(self) -> None:
        response = _typed({"type": "Inert", "reason": "no_run"}, "claude-resolve")
        for hook, payload, _ in self.CASES:
            with self.subTest(hook=hook):
                code, stderr, stdout = self._run_hook(
                    hook, payload, resolve={"return_value": response})
                self.assertEqual((code, stderr, stdout), (0, "", ""))

    def test_each_observational_hook_reports_one_diagnostic_line_and_exits_zero(self) -> None:
        """Downstream (post-resolution) failures reach the same single handler."""
        for hook, payload, name in self.CASES:
            with self.subTest(hook=hook):
                code, stderr, _ = self._run_hook(
                    hook, payload, _resolve={"side_effect": RuntimeError("boom\nwith   spaces")})
                self.assertEqual(code, 0)
                self.assertEqual(
                    stderr,
                    f"empirica {name} hook failed: RuntimeError: boom with spaces\n")

    def test_diagnostic_is_one_line_without_traceback(self) -> None:
        line = lifecycle.observational_diagnostic("SubagentStart", ValueError("a\n b"))
        self.assertEqual(line, "empirica SubagentStart hook failed: ValueError: a b")
        self.assertNotIn("\n", line)


class TranscriptIdentityTests(unittest.TestCase):
    def _observe(self, rows):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "child.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            models, final = lifecycle._parse_transcript_contents(str(path))
            return (models[0] if len(models) == 1 else None), final

    def test_single_served_model_ignores_synthetic_rows(self):
        rows = [
            {"message": {"role": "assistant", "model": "<synthetic>", "content": "retry"}},
            {"message": {"role": "assistant", "model": "eu.anthropic.claude-opus-4-8",
                         "content": "verdict"}},
        ]
        self.assertEqual(self._observe(rows), ("eu.anthropic.claude-opus-4-8", "verdict"))

    def test_empty_string_model_rows_are_not_served_models(self):
        rows = [
            {"message": {"role": "assistant", "model": "", "content": "retry"}},
            {"message": {"role": "assistant", "model": "eu.anthropic.claude-opus-4-8",
                         "content": "verdict"}},
        ]
        self.assertEqual(self._observe(rows), ("eu.anthropic.claude-opus-4-8", "verdict"))
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "child.jsonl"
            path.write_text(json.dumps(rows[0]) + "\n")
            self.assertEqual(lifecycle._parse_transcript_contents(str(path)), ([], "retry"))

    def test_synthetic_only_and_mid_transcript_switch_are_unobservable(self):
        synthetic = [{"message": {"role": "assistant", "model": "<synthetic>",
                                    "content": "error"}}]
        self.assertEqual(self._observe(synthetic), (None, "error"))
        switched = [
            {"message": {"role": "assistant", "model": "model-a", "content": "a"}},
            {"message": {"role": "assistant", "model": "model-b", "content": "b"}},
        ]
        self.assertEqual(self._observe(switched), (None, "b"))

    def test_malformed_transcript_discards_partial_observation(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "child.jsonl"
            path.write_text(json.dumps({"message": {"role": "assistant", "model": "model-a"}})
                            + "\n{broken\n")
            self.assertEqual(lifecycle._parse_transcript_contents(str(path)), ([], None))

    def test_subagent_cannot_admit_main_author_evidence(self):
        payload = StringIO(json.dumps({
            "agent_id": "child-1", "tool_name": "mcp__empirica__empirica_observe",
            "tool_input": {"action": {"kind": "research"}},
        }))
        stderr = StringIO()
        with patch("sys.stdin", new=payload), patch("sys.stderr", new=stderr):
            self.assertEqual(lifecycle.route_main(), 2)
        self.assertIn("subagent producer is not the main author", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
