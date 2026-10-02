#!/usr/bin/env python3
"""Fake host/MCP dialogs exercise the real public tool and private service lanes."""
import copy
import json
import queue
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters.governance import (Approve, Dismiss, HostGovernance, Reject, confirm_form,
                                 decision, expected_approval_kind, review_form)
from adapters.mcp_server import McpSession
from adapters.public_tools import PublicTools
from application import protocol as _proto
from application.v2 import compose
from test_governance import CONTEXT, GRAPH, AUTHOR
from governance_setup import TEST_INVOCATION, SIZED_RATIONALE
from test_d7_transactions import Runs, Artifacts, Workspace, Harness


class GovernanceHostTests(unittest.TestCase):
    def setUp(self):
        self.profile = "claude-code@2.1.278"
        self.service = compose(Workspace(), Harness(), Runs(), Artifacts(), None, self.profile, {}, None)
        self.sequence = 0
        response = self.dispatch({"type": "StartRun", "control_mode": "deliberative", "goal": "supplied task",
                                  "invocation": dict(TEST_INVOCATION),
                                  "selector": {"project": "p", "session": "ui"}})
        self.run = response["result"]["run"]["id"]
        self.dispatch({"type": "ObserveAction", "run_id": self.run,
                       "action": {"kind": "graph", "payload": GRAPH}})
        context = copy.deepcopy(CONTEXT)
        context["ingress"] = "mcp_elicitation"
        self.service.trusted_governance_context(run_id=self.run, payload=context)
        self.incoming = queue.Queue()
        self.sent = []
        self.session = McpSession(self.profile, self.incoming, self.send, timeout=0.05)
        self.mediator = HostGovernance(self.profile,
            context_ingress=lambda _p, r, v: self.service.trusted_governance_context(run_id=r, payload=v),
            decision_ingress=lambda _p, r, v: self.service.trusted_governance_decision(run_id=r, payload=v))
        self.session.mediator = self.mediator
        self.session.tools = PublicTools(self.profile, dispatch=lambda r, _p: self.service.dispatch(r), govern=self.mediator)
        self.reply_action = "accept"
        self.inject_request = False
        self.wrong_id = False

    def dispatch(self, command):
        self.sequence += 1
        return self.service.dispatch({"protocol": "empirica/v2", "request_id": str(self.sequence), "command": command})

    def send(self, message):
        self.sent.append(message)
        if message.get("method") != "elicitation/create":
            return
        if self.inject_request:
            self.incoming.put({"jsonrpc": "2.0", "id": message["id"], "method": "tools/call",
                               "result": {"action": "accept", "content": {}}})
            return
        if self.reply_action == "timeout":
            return
        content = {}
        self.incoming.put({"jsonrpc": "2.0", "id": "wrong" if self.wrong_id else message["id"],
                           "result": {"action": self.reply_action, "content": content}})

    def initialize(self, caps):
        return self.session.process({"jsonrpc": "2.0", "id": "init", "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": caps}})

    def propose(self):
        with patch.dict("os.environ", {}):
            return self.session.process_internal({"jsonrpc": "2.0", "id": "call", "method": "tools/call",
                "params": {"name": "empirica_observe", "arguments": {"run_id": self.run,
                            "action": {"kind": "configure_run", "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}, "rationale": SIZED_RATIONALE}}}})

    def test_host_owned_decision_table_is_fail_closed(self):
        self.dispatch({"type": "ObserveAction", "run_id": self.run,
                       "action": {"kind": "configure_run", "budgets": {
                           "max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2},
                           "rationale": SIZED_RATIONALE}})
        dialog = self.service.trusted_governance_context(
            run_id=self.run, payload=copy.deepcopy(CONTEXT))["result"]["presentation"]["dialog"]
        schema = review_form(dialog, 900)[1]
        cases = [
            ({"action": "accept", "content": {}}, Approve),
            ({"action": "accept", "content": {"max_passes": "6"}}, Approve),
            ({"action": "decline"}, Reject),
            ({"action": "cancel"}, Dismiss),
            (None, Dismiss),
            ({"action": "accept", "content": {"extra": 1}}, Dismiss),
            ({"action": "accept", "content": {"max_passes": 0}}, Dismiss),
        ]
        for answer, expected in cases:
            with self.subTest(answer=answer):
                self.assertIsInstance(decision(answer, dialog, schema, confirmation=False), expected)
        changed = decision({"action": "accept", "content": {"max_passes": "6"}},
                           dialog, schema, confirmation=False)
        self.assertIsInstance(changed, Approve)
        self.assertEqual(changed.configuration["budgets"]["max_passes"], 6)
        for answer in ({"action": "accept", "content": {"max_passes": 6.0}},
                       {"action": "accept", "content": {"max_passes": True}},
                       {"action": "accept", "content": {"max_passes": "٣"}}):
            self.assertIsInstance(decision(answer, dialog, schema, confirmation=False), Dismiss)
        confirm_schema = confirm_form(dialog, dialog, 900)[1]
        for answer in ({"action": "accept"}, {"action": "accept", "content": {}}):
            self.assertIsInstance(decision(answer, dialog, confirm_schema,
                                           confirmation=True), Approve)
        self.assertIsInstance(decision({"action": "accept", "content": {"max_passes": 6}},
                                       dialog, confirm_schema, confirmation=True), Dismiss)
        for answer in ({"action": "decline"}, {"action": "cancel"}, None):
            self.assertIsInstance(decision(answer, dialog, confirm_schema,
                                           confirmation=True), Dismiss)

    def test_host_owned_approval_views_do_not_offer_feedback_input(self):
        """The native incident must be impossible through the offered form fields."""
        self.initialize({"elicitation": {"form": {}}})
        seen = []
        def answer(message, schema):
            seen.append(message)
            self.assertNotIn("change_request", schema["properties"],
                             "An approval view must not also collect scope-change text")
            content = {}
            if len(seen) == 1:
                content.update(max_passes=8, max_spawns=2, max_audit_spawns=2)
            else:
                self.assertEqual(len(seen), 2)
                self.assertIn("confirm edited configuration", message)
            return {"action": "accept", "content": content}
        self.mediator.elicit = answer
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(len(seen), 2)
        self.assertEqual(result["run"]["governance"]["state"], "approved")
        self.assertEqual(result["run"]["governance"]["budgets"]["max_spawns"], 2)
        self.assertEqual(result["run"]["governance"]["budgets"]["max_audit_spawns"], 2)

    def test_historical_approve_plus_text_payloads_are_rejected_by_new_form(self):
        """Exact native feedback strings cannot coexist with the approval surface."""
        for text in ("nothing", "approved"):
            with self.subTest(text=text):
                self.setUp()
                self.initialize({"elicitation": {"form": {}}})
                self.mediator.elicit = lambda *_: {"action": "accept", "content": {
                    "decision": "approve", "change_request": text,
                    "max_passes": 8, "max_spawns": 2, "max_audit_spawns": 2,
                    "multi_provider": True, "cli_exec": True,
                    "auditor_provider": "anthropic", "auditor_model": "claude-opus-4-6"}}
                result = self.propose()["result"]["structuredContent"]
                self.assertEqual(result["type"], "Block")
                self.assertEqual(result["reasons"][0]["code"], "governance.approval_unavailable")
                self.assertIsNone(result["run"]["governance"]["approved_digest"])
                self.assertEqual(result["run"]["governance"]["budgets"]["max_spawns"], 1)

    def test_host_owned_confirmation_preserves_submitted_values(self):
        self.initialize({"elicitation": {"form": {}}})
        seen = []
        def answer(message, schema):
            seen.append((message, schema))
            if len(seen) == 1:
                return {"action": "accept", "content": {
                    "max_passes": 8, "max_spawns": 2,
                    "max_audit_spawns": 3}}
            self.assertEqual(len(seen), 2)
            self.assertIn("confirm edited configuration", message)
            self.assertIn("spawns 2 (was 1)", message)
            self.assertIn("audits 3 (was 2)", message)
            self.assertNotIn("max_spawns", schema["properties"])
            self.assertNotIn("cli_exec", schema["properties"])
            current = self.dispatch({"type": "GetRun", "run_id": self.run})["result"]["run"]
            self.assertEqual(current["governance"]["state"], "pending")
            self.assertEqual(current["governance"]["budgets"]["max_audit_spawns"], 2)
            return {"action": "accept", "content": {}}
        self.mediator.elicit = answer
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(len(seen), 2)
        g = result["run"]["governance"]
        self.assertEqual(g["state"], "approved")
        self.assertEqual(g["approved_digest"], g["proposal_digest"])
        self.assertEqual(g["budgets"]["max_spawns"], 2)
        self.assertEqual(g["budgets"]["max_audit_spawns"], 3)

    def test_host_owned_confirmation_cancel_preserves_pending_edit(self):
        self.initialize({"elicitation": {}})
        replies = iter([{"action": "accept", "content": {
            "max_passes": 6}}, {"action": "cancel"}])
        self.mediator.elicit = lambda *_: next(replies)
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(result["type"], "Block")
        g = result["run"]["governance"]
        self.assertEqual(g["state"], "pending")
        self.assertEqual(g["proposal"]["budgets"]["max_passes"], 6)
        self.assertEqual(g["budgets"]["max_passes"], 8)
        self.assertIsNone(g["approved_digest"])

    def test_host_owned_confirmation_cannot_accept_stale_or_edited_reply(self):
        for scenario in ("edited_reply", "timeout", "reject"):
            with self.subTest(scenario=scenario):
                self.setUp()
                self.initialize({"elicitation": {}})
                count = 0
                def answer(_message, _schema):
                    nonlocal count
                    count += 1
                    if count == 1:
                        return {"action": "accept", "content": {
                            "max_passes": 6}}
                    self.assertEqual(count, 2)
                    content = {}
                    if scenario == "edited_reply":
                        content["max_passes"] = 7
                    elif scenario == "timeout":
                        return None
                    elif scenario == "reject":
                        return {"action": "decline"}
                    return {"action": "accept", "content": content}
                self.mediator.elicit = answer
                result = self.propose()["result"]["structuredContent"]
                self.assertEqual(count, 2)
                g = result["run"]["governance"]
                self.assertNotEqual(g["state"], "approved")
                self.assertEqual(g["proposal"]["budgets"]["max_passes"], 6)
                self.assertEqual(g["budgets"]["max_passes"], 8)
                self.assertIsNone(g["approved_digest"])


    def test_host_owned_confirmation_blocks_concurrent_configuration_epoch(self):
        self.initialize({"elicitation": {}})
        calls = 0

        def answer(_message, _schema):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"action": "accept", "content": {"max_passes": 6}}
            self.dispatch({"type": "ObserveAction", "run_id": self.run,
                           "action": {"kind": "configure_run", "budgets": {"max_passes": 7, "max_spawns": 1, "max_audit_spawns": 2}, "rationale": SIZED_RATIONALE}})
            return {"action": "accept", "content": {}}

        self.mediator.elicit = answer
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(calls, 2)
        self.assertEqual(result["type"], "Block")
        self.assertEqual(result["reasons"][0]["code"], "governance.stale_proposal")
        self.assertNotEqual(result["run"]["governance"]["state"], "approved")

    def test_host_owned_human_wait_does_not_admit_work_or_convergence(self):
        from adapters.claude.completion import stop_result
        self.initialize({"elicitation": {}})
        self.reply_action = "cancel"
        self.propose()
        self.dispatch({"type": "ObserveAction", "run_id": self.run,
            "action": {"kind": "route", "reason": "supplied context"}})
        stopped = self.dispatch({"type": "EvaluateRun", "run_id": self.run, "intent": "report_convergence"})
        self.assertEqual(stopped["result"]["type"], "Block")
        from adapters.claude.transport import Response
        typed = Response.from_envelope({"request_id": stopped["request_id"]}, stopped)
        self.assertEqual(stop_result(typed).exit_code, 0)
        for command in ({"type": "ObserveAction", "action": {"kind": "investigate"}},
                        {"type": "ObserveAction", "action": {"kind": "child_reserve", "purpose": "test",
                         "role_profile": "worker", "execution": "foreground", "resource_class": "investigation"}},
                        {"type": "EvaluateRun", "intent": "report_convergence"}):
            blocked = self.dispatch({**command, "run_id": self.run})["result"]
            self.assertEqual(blocked["type"], "Block")
            self.assertEqual(blocked["run"]["status"], "active")
            self.assertIsNone(blocked["run"]["governance"]["approved_digest"])

    def test_locked_confirmation_decline_keeps_edits_pending_without_feedback_form(self):
        self.initialize({"elicitation": {}})
        replies = iter([
            {"action": "accept", "content": {
             "max_passes": 6}},
            {"action": "decline"},
        ])
        seen = []
        self.mediator.elicit = lambda message, schema: (seen.append((message, schema)), next(replies))[1]
        with patch.dict("os.environ", {}):
            result = self.mediator(self.dispatch({"type": "ObserveAction", "run_id": self.run,
                "action": {"kind": "configure_run", "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}, "rationale": SIZED_RATIONALE}})["result"])
        g = result["run"]["governance"]
        self.assertEqual(len(seen), 2)
        self.assertIn("confirm edited configuration", seen[1][0])
        self.assertNotIn("change_request", seen[1][1]["properties"])
        self.assertEqual(g["proposal"]["budgets"]["max_passes"], 6)
        self.assertEqual(g["state"], "pending")
        self.assertIsNone(g["approved_digest"])
        self.assertNotIn("change_request", g)


    def test_reject_needs_no_model_affirmation_and_safe_rendering_is_reversible(self):
        self.initialize({"elicitation": {}})
        self.mediator.elicit = lambda *_: {"action": "decline"}
        with patch.dict("os.environ", {}):
            rejected = self.mediator(self.dispatch({"type": "ObserveAction", "run_id": self.run,
                "action": {"kind": "configure_run", "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}, "rationale": SIZED_RATIONALE}})["result"])
        self.assertEqual(rejected["run"]["governance"]["state"], "rejected")
        from core.projection import safe_text as safe
        self.assertEqual(safe("actual\x1b literal \\x1b \u202e emoji 😀 中"),
                         "actual\\x1b literal \\\\x1b \\u202e emoji 😀 中")

    def test_interactive_auto_dialog_once_then_lower_auto_without_dialog(self):
        auto = self.dispatch({"type": "StartRun", "control_mode": "auto", "goal": "auto task",
                              "invocation": {**TEST_INVOCATION, "delegation": True},
                              "selector": {"project": "p", "session": "auto-ui"}})["result"]["run"]["id"]
        self.dispatch({"type": "ObserveAction", "run_id": auto,
                       "action": {"kind": "graph", "payload": GRAPH}})
        calls = []
        mediator = HostGovernance(self.profile, elicit=lambda message, schema: (
            calls.append((message, schema)) or {"action": "accept", "content": {}}),
            context_ingress=lambda _p, r, v: self.service.trusted_governance_context(run_id=r, payload=v),
            decision_ingress=lambda _p, r, v: self.service.trusted_governance_decision(run_id=r, payload=v))
        initial = self.dispatch({"type": "ObserveAction", "run_id": auto,
            "action": {"kind": "configure_run", "budgets": {
                "max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2},
                "rationale": SIZED_RATIONALE}})["result"]
        approved = mediator(initial)
        self.assertEqual(approved["run"]["governance"]["approval_kind"], "host_ui")
        self.assertEqual(len(calls), 1)
        lower = self.dispatch({"type": "ObserveAction", "run_id": auto,
            "action": {"kind": "configure_run", "budgets": {
                "max_passes": 7, "max_spawns": 1, "max_audit_spawns": 2},
                "rationale": "same graph, lower pass allowance"}})["result"]
        lowered = mediator(lower)
        self.assertEqual(lowered["run"]["governance"]["approval_kind"], "auto")
        self.assertEqual(len(calls), 1, "post-approval auto must not open another dialog")
        raised = self.dispatch({"type": "ObserveAction", "run_id": auto,
            "action": {"kind": "configure_run", "budgets": {
                "max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2},
                "rationale": "attempt to raise again"}})["result"]
        self.assertEqual(raised["reasons"][0]["code"], "governance.auto_ceiling")
        self.assertEqual(len(calls), 1)

    def test_interactive_auto_without_ui_fails_closed_despite_delegation(self):
        auto = self.dispatch({"type": "StartRun", "control_mode": "auto", "goal": "auto no ui",
                              "invocation": {**TEST_INVOCATION, "delegation": True},
                              "selector": {"project": "p", "session": "auto-no-ui"}})["result"]["run"]["id"]
        self.dispatch({"type": "ObserveAction", "run_id": auto,
                       "action": {"kind": "graph", "payload": GRAPH}})
        proposed = self.dispatch({"type": "ObserveAction", "run_id": auto,
            "action": {"kind": "configure_run", "budgets": {
                "max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2},
                "rationale": SIZED_RATIONALE}})["result"]
        mediator = HostGovernance(self.profile, elicit=None,
            context_ingress=lambda _p, r, v: self.service.trusted_governance_context(run_id=r, payload=v),
            decision_ingress=lambda _p, r, v: self.service.trusted_governance_decision(run_id=r, payload=v))
        blocked = mediator(proposed)
        self.assertEqual(blocked["reasons"][0]["code"], "governance.approval_unavailable")
        self.assertFalse(blocked["run"]["governance"]["first_approval"])

    def test_python_and_pi_phase_rules_match_identical_neutral_facts(self):
        cases = [
            {"control_mode": "deliberative", "first_approval": False,
             "context": {"interactive": True}},
            {"control_mode": "auto", "first_approval": False,
             "context": {"interactive": True}},
            {"control_mode": "auto", "first_approval": True,
             "context": {"interactive": True}},
            {"control_mode": "auto", "first_approval": False,
             "context": {"interactive": False}},
            {"control_mode": "auto", "first_approval": False,
             "context": {"interactive": None}},
            {"control_mode": "deliberative", "first_approval": False,
             "context": {"interactive": False}},
        ]
        expected = [expected_approval_kind(case) for case in cases]
        self.assertEqual(expected[-2:], ["auto", "host_ui"])
        module = Path(__file__).resolve().parents[1] / "adapters" / "pi" / "src" / "governance-ui.ts"
        script = ("import {expectedApprovalKind as f} from " + json.dumps(module.as_uri()) + ";"
                  "const rows=JSON.parse(process.argv[1]);"
                  "console.log(JSON.stringify(rows.map(f)));" )
        completed = __import__("subprocess").run(
            ["node", "--experimental-strip-types", "--input-type=module", "-e", script,
             json.dumps(cases)], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(completed.stdout), expected)

    def test_documented_timeout_default_and_validated_host_override(self):
        """Shared case table (also run by the Pi suite): exact seconds or exact error message."""
        from adapters.governance import governance_timeout
        table = json.loads((Path(__file__).parent / "fixtures" / "governance-timeout-cases.json").read_text())
        names = {case["name"] for case in table["cases"]}
        self.assertTrue({"unset", "blank empty", "min", "max", "zero", "above max", "exponent", "hex",
                         "underscore", "fraction", "text", "control char"} <= names)
        for case in table["cases"]:
            env = {} if case["env"] is None else {"EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS": case["env"]}
            with self.subTest(case=case["name"]), patch.dict("os.environ", env, clear=True):
                if case["error"] is None:
                    self.assertEqual(governance_timeout(), case["seconds"])
                else:
                    with self.assertRaises(ValueError) as caught:
                        governance_timeout()
                    self.assertEqual(str(caught.exception), case["error"])

    def test_dismissals_durable_and_no_fourth_dialog(self):
        self.initialize({"elicitation": {}})
        for action in ("cancel", "timeout", "cancel"):
            self.reply_action = action
            self.assertEqual(self.propose()["result"]["structuredContent"]["type"], "Block")
            c = self.service._coordinator
            self.service = compose(Workspace(), Harness(), c.runs, c.artifacts, None, self.profile, {}, None)
        count = len(self.sent)
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(result["reasons"][0]["code"], "governance.interaction_limit")
        self.assertEqual(len(self.sent), count)
        self.assertEqual(result["run"]["governance"]["interactions_remaining"], {"proposal": 0, "total": 125})
        self.assertNotEqual(result["run"]["governance"]["state"], "rejected")

    def test_replayed_presentation_never_redisplays_after_finalization(self):
        from types import SimpleNamespace
        self.initialize({"elicitation": {}})
        self.reply_action = "cancel"
        with patch("adapters.governance.uuid4", return_value=SimpleNamespace(hex="same-dialog")):
            self.assertEqual(self.propose()["result"]["structuredContent"]["type"], "Block")
            count = len(self.sent)
            self.assertEqual(self.propose()["result"]["structuredContent"]["type"], "Inert")
            self.assertEqual(len(self.sent), count)


    def test_ping_and_only_matching_call_cancellation_abort_dialog(self):
        self.session.active_call_id = "active"
        self.incoming.put({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "other"}})
        self.incoming.put({"jsonrpc": "2.0", "id": "health", "method": "ping"})
        result = self.session.elicit("test", {})
        self.assertEqual(result["action"], "accept")
        self.assertEqual(self.sent[-1], {"jsonrpc": "2.0", "id": "health", "result": {}})
        self.incoming.put({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "active"}})
        self.assertIsNone(self.session.elicit("test", {}))
        late = self.incoming.get_nowait()
        self.assertIsNone(self.session.process(late))


    def test_invalid_ui_content_dismisses_but_private_invalid_input_never_writes(self):
        self.initialize({"elicitation": {}})
        self.mediator.elicit = lambda *_: {"action": "accept", "content": {"invalid": True}}
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(result["run"]["governance"]["interactions_remaining"]["proposal"], 2)
        self.assertEqual(result["run"]["governance"]["state"], "pending")

    def test_real_service_accept_flat_form_then_investigation(self):
        self.initialize({"elicitation": {"form": {}}})
        response = self.propose()
        g = response["result"]["structuredContent"]["run"]["governance"]
        self.assertEqual(g["state"], "approved", response)
        self.assertEqual(g["approval_kind"], "host_ui")
        schema = self.sent[0]["params"]["requestedSchema"]
        self.assertEqual(schema["type"], "object")
        self.assertTrue(all(p["type"] in {"string", "boolean", "integer"} for p in schema["properties"].values()))
        self.dispatch({"type": "ObserveAction", "run_id": self.run, "action": {"kind": "route", "reason": "supplied"}})
        result = self.dispatch({"type": "ObserveAction", "run_id": self.run, "action": {"kind": "investigate"}})
        self.assertEqual(result["result"]["type"], "Allow")

    def test_removed_dispatch_action_is_rejected_on_private_wire(self):
        """The private bridge schema rejects the removed host action before core evaluation."""
        result = self.service.dispatch({
            "protocol": "empirica/v2", "request_id": "removed-dispatch",
            "command": {"type": "ObserveAction", "run_id": self.run,
                        "action": {"kind": "dispatch", "target": "codex"}},
        })["result"]
        self.assertEqual(result, {"type": "Fault", "code": "invalid_request",
                                  "fail_direction": "closed"})

    def test_cancel_timeout_and_wrong_id_never_approve(self):
        self.initialize({"elicitation": {}})
        for action in ("cancel", "timeout"):
            self.reply_action = action
            response = self.propose()["result"]["structuredContent"]
            self.assertEqual(response["type"], "Block")
            self.assertNotEqual(response["run"]["governance"]["state"], "approved")
            self.assertEqual(response["run"]["governance"]["budgets"]["passes_used"], 0)
        self.reply_action = "accept"
        self.wrong_id = True
        self.assertEqual(self.propose()["result"]["structuredContent"]["type"], "Block")

    def test_decline_rejects_without_admitting_work(self):
        self.initialize({"elicitation": {}})
        self.reply_action = "decline"
        response = self.propose()["result"]["structuredContent"]
        self.assertEqual(response["type"], "Allow")
        self.assertEqual(response["run"]["governance"]["state"], "rejected")
        self.assertIsNone(response["run"]["governance"]["approved_digest"])
        self.assertEqual(response["run"]["governance"]["budgets"]["passes_used"], 0)

    def test_request_with_matching_id_is_not_a_reply_and_concurrency_rejected(self):
        self.initialize({"elicitation": {"form": {}}})
        self.inject_request = True
        self.assertEqual(self.propose()["result"]["structuredContent"]["type"], "Block")
        self.assertEqual(self.sent[-1]["error"]["code"], -32000)

    def test_no_capability_url_only_and_codex_are_unavailable(self):
        self.initialize({"elicitation": {"url": {}}})
        self.assertEqual(self.propose()["result"]["structuredContent"]["reasons"][0]["code"], "governance.approval_unavailable")
        self.assertEqual(self.sent, [])
        run = self.dispatch({"type": "GetRun", "run_id": self.run})["result"]["run"]
        self.assertEqual(run["governance"]["interactions_remaining"], {"proposal": 3, "total": 128})
        self.mediator.profile = "codex-cli@0.146.0"
        self.mediator.elicit = self.session.elicit
        self.assertEqual(self.propose()["result"]["structuredContent"]["type"], "Block")
        self.assertEqual(self.sent, [])

    def test_stdio_subprocess_correlated_approval_reaches_real_git_backed_service(self):
        import os
        import select
        import subprocess
        import tempfile
        from adapters import bridge
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            env = {**os.environ, "EMPIRICA_HOME": str(root / "state"),
                   "EMPIRICA_REPO_DIR": str(root), "EMPIRICA_HOST_PROFILE_ID": self.profile}
            with patch.dict(os.environ, env):
                def call(command):
                    return bridge.handle({"protocol": "empirica/v2", "request_id": "setup",
                                          "command": command}, self.profile)["result"]
                run = call({"type": "StartRun", "control_mode": "deliberative", "goal": "subprocess approval",
                            "invocation": dict(TEST_INVOCATION),
                            "selector": {"project": "p", "session": "stdio"}})["run"]["id"]
                call({"type": "ObserveAction", "run_id": run, "action": {"kind": "graph", "payload": GRAPH}})
                bridge.trusted_governance_context(self.profile, run, {
                    "author": AUTHOR, "ingress": "mcp_elicitation"})
                script = Path(__file__).resolve().parents[1] / "adapters/mcp_server.py"
                process = subprocess.Popen([sys.executable, str(script)], cwd=root, env=env,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                def send(message):
                    process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
                    process.stdin.flush()
                def receive():
                    self.assertTrue(select.select([process.stdout], [], [], 20)[0], "MCP reply timeout")
                    return json.loads(process.stdout.readline())
                try:
                    send({"id": "init", "method": "initialize", "params": {
                        "protocolVersion": "2025-11-25", "capabilities": {"elicitation": {"form": {}}}}})
                    self.assertEqual(receive()["id"], "init")
                    send({"id": "proposal", "method": "tools/call", "params": {
                        "name": "empirica_observe", "arguments": {"run_id": run,
                        "action": {"kind": "configure_run", "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}, "rationale": SIZED_RATIONALE}}}})
                    dialog = receive()
                    self.assertEqual(dialog["method"], "elicitation/create")
                    send({"id": dialog["id"], "result": {"action": "accept", "content": {}}})
                    response = receive()
                    self.assertEqual(response["id"], "proposal")
                    self.assertEqual(set(response["result"]), {"content", "isError"})
                    self.assertNotIn("structuredContent", response["result"])
                    state = call({"type": "GetRun", "run_id": run})["run"]
                    self.assertEqual(state["governance"]["state"], "approved")
                    self.assertEqual(state["governance"]["approval_kind"], "host_ui")
                finally:
                    process.stdin.close()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                    process.stdout.close()
                    process.stderr.close()
                self.assertEqual(process.returncode, 0)

    def test_work_and_author_are_not_in_configuration_form(self):
        self.initialize({"elicitation": {}})
        self.propose()
        message = self.sent[0]["params"]["message"]
        self.assertIn("supplied task", message)
        self.assertNotIn(GRAPH["claims"][0]["text"], message)
        self.assertNotIn(AUTHOR["model_id"], message)
        self.assertEqual(len(self.session.tools.definitions()), 3)
        self.assertNotIn("governance_decision", repr(self.session.tools.definitions()))
        view = self.dispatch({"type": "GetRun", "run_id": self.run})["result"]["run"]
        from core.projection import governance_dialog
        dialog = governance_dialog(view["goal"], view["governance"],
                                   _proto.projection_controls(), view.get("invocation"))
        form_message, _ = review_form(dialog, 900)
        self.assertNotIn(view["governance"]["proposal_digest"], form_message)


class GovernancePresentationTests(unittest.TestCase):
    """Pure presentation checks; real host/service transitions are tested above."""

    def view(self):
        from core.governance import initial, revise
        budgets = {"max_passes": 8, "max_spawns": 0, "max_audit_spawns": 1}
        invocation = {"host": "test", "interactive": True,
                      "signal": "operator", "delegation": False}
        g = revise("Exact goal", initial("Exact goal", budgets, "deliberative", invocation, None),
                   proposal={"budgets": budgets, "rationale": SIZED_RATIONALE})
        g.update(scope=copy.deepcopy(GRAPH), context=copy.deepcopy(CONTEXT),
                 budgets={**budgets, "passes_used": 2, "spawns_used": 0, "audit_spawns_used": 0},
                 interactions_remaining={"proposal": 3, "total": 128})
        from core.projection import governance_dialog
        g["dialog"] = governance_dialog("Exact goal", g, _proto.projection_controls())
        return {"goal": "Exact goal", "governance": g}

    def test_configuration_presentation_excludes_the_work_graph(self):
        from core.projection import governance_dialog
        view = self.view()
        graph = view["governance"]["scope"]
        graph["claims"][0]["text"] = "work graph is not approvable"
        dialog = governance_dialog(view["goal"], view["governance"], _proto.projection_controls())
        message, _ = review_form(dialog, 900)
        self.assertIn("Goal:", message)
        self.assertIn("Exact goal", message)
        self.assertNotIn(view["governance"]["proposal_digest"], message)
        self.assertNotIn("work graph is not approvable", message)
        self.assertNotIn(AUTHOR["model_id"], message)


if __name__ == "__main__":
    unittest.main()
