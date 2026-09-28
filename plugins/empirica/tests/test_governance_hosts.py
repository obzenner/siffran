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
from adapters.governance import HostGovernance, form
from adapters.mcp_server import McpSession
from adapters.public_tools import PublicTools
from application import protocol as _proto
from application.v2 import compose
from test_governance import CONTEXT, GRAPH, AUTHOR
from governance_setup import TEST_INVOCATION
from test_d7_transactions import Runs, Artifacts, Workspace, Harness


class GovernanceHostTests(unittest.TestCase):
    def setUp(self):
        self.profile = "claude-code@2.1.278"
        self.service = compose(Workspace(), Harness(), Runs(), Artifacts(), None, self.profile, {}, None)
        self.sequence = 0
        response = self.dispatch({"type": "StartRun", "goal": "supplied task",
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
        if command.get("type") == "StartRun":
            command = {"invocation": dict(TEST_INVOCATION), **command}
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
        content = {"decision": "approve"}
        self.incoming.put({"jsonrpc": "2.0", "id": "wrong" if self.wrong_id else message["id"],
                           "result": {"action": self.reply_action, "content": content}})

    def initialize(self, caps):
        return self.session.process({"jsonrpc": "2.0", "id": "init", "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": caps}})

    def propose(self):
        with patch.dict("os.environ", {}):
            return self.session.process_internal({"jsonrpc": "2.0", "id": "call", "method": "tools/call",
                "params": {"name": "empirica_observe", "arguments": {"run_id": self.run,
                            "action": {"kind": "configure_run"}}}})

    def test_host_owned_approval_views_do_not_offer_feedback_input(self):
        """The native incident must be impossible through the offered form fields."""
        self.initialize({"elicitation": {"form": {}}})
        seen = []
        def answer(message, schema):
            seen.append(message)
            self.assertNotIn("change_request", schema["properties"],
                             "An approval view must not also collect scope-change text")
            content = {"decision": "approve"}
            if len(seen) == 1:
                content.update(max_passes=8, max_spawns=2, max_audit_spawns=2,
                               multi_provider=True, cli_exec=True)
            else:
                self.assertEqual(len(seen), 2)
                self.assertIn("FINAL CONFIRMATION", message)
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
                return {"action": "accept", "content": {"decision": "approve",
                    "max_passes": 8, "max_spawns": 2,
                    "max_audit_spawns": 3, "cli_exec": True, "multi_provider": True}}
            self.assertEqual(len(seen), 2)
            self.assertIn("FINAL CONFIRMATION", message)
            self.assertIn("Child spawns: proposed 2", message)
            self.assertIn("Audit spawns: proposed 3", message)
            self.assertNotIn("max_spawns", schema["properties"])
            self.assertNotIn("cli_exec", schema["properties"])
            current = self.dispatch({"type": "GetRun", "run_id": self.run})["result"]["run"]
            self.assertEqual(current["governance"]["state"], "pending")
            self.assertEqual(current["governance"]["budgets"]["max_audit_spawns"], 1)
            return {"action": "accept", "content": {"decision": "approve"}}
        self.mediator.elicit = answer
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(len(seen), 2)
        g = result["run"]["governance"]
        self.assertEqual(g["state"], "approved")
        self.assertEqual(g["approved_digest"], g["proposal_digest"])
        self.assertEqual(g["budgets"]["max_spawns"], 2)
        self.assertEqual(g["budgets"]["max_audit_spawns"], 3)
        self.assertEqual(result["run"]["modes"], {"cli_exec": True, "multi_provider": True})

    def test_host_owned_confirmation_cancel_preserves_pending_edit(self):
        self.initialize({"elicitation": {}})
        replies = iter([{"action": "accept", "content": {"decision": "approve",
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
                        return {"action": "accept", "content": {"decision": "approve",
                            "max_passes": 6}}
                    self.assertEqual(count, 2)
                    content = {"decision": "approve"}
                    if scenario == "edited_reply":
                        content["max_passes"] = 7
                    elif scenario == "timeout":
                        return None
                    elif scenario == "reject":
                        content = {"decision": "reject"}
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
                return {"action": "accept", "content": {"decision": "approve", "max_passes": 6}}
            self.dispatch({"type": "ObserveAction", "run_id": self.run,
                           "action": {"kind": "configure_run", "budgets": {"max_passes": 7}}})
            return {"action": "accept", "content": {"decision": "approve"}}

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
        self.assertEqual(stop_result(stopped).exit_code, 0)
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
            {"action": "accept", "content": {"decision": "approve",
             "max_passes": 6}},
            {"action": "accept", "content": {"decision": "decline"}},
        ])
        seen = []
        self.mediator.elicit = lambda message, schema: (seen.append((message, schema)), next(replies))[1]
        with patch.dict("os.environ", {}):
            result = self.mediator(self.dispatch({"type": "ObserveAction", "run_id": self.run,
                "action": {"kind": "configure_run"}})["result"])
        g = result["run"]["governance"]
        self.assertEqual(len(seen), 2)
        self.assertIn("FINAL CONFIRMATION", seen[1][0])
        self.assertNotIn("change_request", seen[1][1]["properties"])
        self.assertEqual(g["proposal"]["budgets"]["max_passes"], 6)
        self.assertEqual(g["state"], "pending")
        self.assertIsNone(g["approved_digest"])
        self.assertNotIn("change_request", g)


    def test_reject_needs_no_model_affirmation_and_safe_rendering_is_reversible(self):
        self.initialize({"elicitation": {}})
        self.mediator.elicit = lambda *_: {"action": "accept", "content": {"decision": "reject"}}
        with patch.dict("os.environ", {}):
            rejected = self.mediator(self.dispatch({"type": "ObserveAction", "run_id": self.run,
                "action": {"kind": "configure_run"}})["result"])
        self.assertEqual(rejected["run"]["governance"]["state"], "rejected")
        from core.projection import safe_text as safe
        self.assertEqual(safe("actual\x1b literal \\x1b \u202e emoji 😀 中"),
                         "actual\\x1b literal \\\\x1b \\u202e emoji 😀 中")

    def test_documented_timeout_default_and_validated_host_override(self):
        from adapters.governance import governance_timeout
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(governance_timeout(), 900)
        for raw in ("0", "1501", "nan", "inf", "invalid"):
            with patch.dict("os.environ", {"EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS": raw}):
                self.assertEqual(governance_timeout(), 900)
        with patch.dict("os.environ", {"EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS": "1"}):
            self.assertEqual(governance_timeout(), 1)

    def test_dismissals_durable_and_no_fourth_dialog(self):
        self.initialize({"elicitation": {}})
        for action in ("cancel", "decline", "timeout"):
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

    def test_hook_translation_of_an_actor_cli_still_fails_closed(self):
        """The Claude PreToolUse hook is dispatch's only producer: it translates an actor-CLI Bash
        call into an ObserveAction(dispatch) submitted through the bridge. dispatch is a host
        action with no core evaluation, so even a fully approved and investigated run refuses it
        with the exact unsupported/closed Fault. No public author schema exposes the kind."""
        from adapters.claude.dispatch import build_dispatch_request
        self.initialize({"elicitation": {"form": {}}})
        self.assertEqual(self.propose()["result"]["structuredContent"]["run"]["governance"]["state"],
                         "approved")
        self.dispatch({"type": "ObserveAction", "run_id": self.run,
                       "action": {"kind": "route", "reason": "supplied"}})
        self.assertEqual(self.dispatch({"type": "ObserveAction", "run_id": self.run,
                                        "action": {"kind": "investigate"}})["result"]["type"], "Allow")
        payload = {"session_id": "claude-session", "cwd": ".", "tool_name": "Bash",
                   "tool_input": {"command": "codex exec --model openai.gpt-5 resolve G0"}}
        request = build_dispatch_request(payload, self.run, claim_id="C0",
                                         correlation_id="claude-dispatch")
        self.assertEqual(request["command"]["action"],
                         {"kind": "dispatch", "target": "codex", "claim_id": "C0"})
        result = self.service.dispatch(request)["result"]
        self.assertEqual(result, {"type": "Fault", "code": "unsupported",
                                  "fail_direction": "closed"})

    def test_cancel_decline_timeout_and_wrong_id_never_approve(self):
        self.initialize({"elicitation": {}})
        for action in ("cancel", "decline", "timeout"):
            self.reply_action = action
            response = self.propose()["result"]["structuredContent"]
            self.assertEqual(response["type"], "Block")
            self.assertNotEqual(response["run"]["governance"]["state"], "approved")
            self.assertEqual(response["run"]["governance"]["budgets"]["passes_used"], 0)
        self.reply_action = "accept"
        self.wrong_id = True
        self.assertEqual(self.propose()["result"]["structuredContent"]["type"], "Block")

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
                run = call({"type": "StartRun", "goal": "subprocess approval",
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
                        "action": {"kind": "configure_run"}}}})
                    dialog = receive()
                    self.assertEqual(dialog["method"], "elicitation/create")
                    send({"id": dialog["id"], "result": {"action": "accept", "content": {
                        "decision": "approve"}}})
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
        # review_text now lives in the private presentation, not the author RunView; rebuild it
        # here to assert the form body carries the proposal digest but no work/author content.
        from core.projection import review_text
        review = review_text(view["goal"], view["governance"], _proto._PROJECTION_CONTROLS,
                             view.get("invocation"))
        self.assertIn(view["governance"]["proposal_digest"], form(view, review)[0])


class GovernancePresentationTests(unittest.TestCase):
    """Pure presentation checks; real host/service transitions are tested above."""

    def view(self):
        from core.governance import initial
        budgets = {"max_passes": 8, "max_spawns": 0, "max_audit_spawns": 1}
        g = initial("Exact goal", budgets, {"cli_exec": False, "multi_provider": False})
        g.update(scope=copy.deepcopy(GRAPH), context=copy.deepcopy(CONTEXT),
                 budgets={**budgets, "passes_used": 2, "spawns_used": 0, "audit_spawns_used": 0},
                 interactions_remaining={"proposal": 3, "total": 128})
        from core.projection import review_text
        g["review_text"] = review_text("Exact goal", g, _proto._PROJECTION_CONTROLS)
        return {"goal": "Exact goal", "governance": g}


    def test_configuration_presentation_excludes_the_work_graph(self):
        from core.projection import review_text
        view = self.view()
        graph = view["governance"]["scope"]
        graph["claims"][0]["text"] = "work graph is not approvable"
        text = review_text(view["goal"], view["governance"], _proto._PROJECTION_CONTROLS)
        self.assertIn("GOAL (READ-ONLY)", text)
        self.assertIn("Exact goal", text)
        self.assertIn(view["governance"]["proposal_digest"], text)
        self.assertNotIn("work graph is not approvable", text)
        self.assertNotIn(AUTHOR["model_id"], text)


if __name__ == "__main__":
    unittest.main()
