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
from application.v2 import compose
from test_governance import CONTEXT, GRAPH, AUDITOR, AUTHOR
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
        context["inventory"]["source"] = "operator_declared"
        self.inventory = context["inventory"]
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
        content = {"decision": "approve", "inventory_confirmed": True,
                   "auditor": AUDITOR["provider_id"] + "/" + AUDITOR["model_id"]}
        if "allow_same_model" in message["params"]["requestedSchema"].get("properties", {}):
            content["allow_same_model"] = False
        if "auditor" not in message["params"]["requestedSchema"].get("properties", {}):
            content.pop("auditor")
        self.incoming.put({"jsonrpc": "2.0", "id": "wrong" if self.wrong_id else message["id"],
                           "result": {"action": self.reply_action, "content": content}})

    def initialize(self, caps):
        return self.session.process({"jsonrpc": "2.0", "id": "init", "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": caps}})

    def propose(self):
        with patch("adapters.governance.operator_inventory", return_value=self.inventory):
            return self.session.process({"jsonrpc": "2.0", "id": "call", "method": "tools/call",
                "params": {"name": "empirica_observe", "arguments": {"run_id": self.run,
                            "action": {"kind": "configure_run", "auditor": AUDITOR}}}})

    def test_host_owned_confirmation_preserves_submitted_values(self):
        self.initialize({"elicitation": {"form": {}}})
        seen = []
        def answer(message, schema):
            seen.append((message, schema))
            if len(seen) == 1:
                return {"action": "accept", "content": {"decision": "approve",
                    "inventory_confirmed": True, "max_passes": 8, "max_spawns": 2,
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
            return {"action": "accept", "content": {"decision": "approve", "inventory_confirmed": True}}
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
            "inventory_confirmed": True, "max_passes": 6}}, {"action": "cancel"}])
        self.mediator.elicit = lambda *_: next(replies)
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(result["type"], "Block")
        g = result["run"]["governance"]
        self.assertEqual(g["state"], "pending")
        self.assertEqual(g["proposal"]["budgets"]["max_passes"], 6)
        self.assertEqual(g["budgets"]["max_passes"], 8)
        self.assertIsNone(g["approved_digest"])

    def test_host_owned_confirmation_cannot_accept_stale_or_edited_reply(self):
        for scenario in ("edited_reply", "stale_reply", "missing_confirmation", "timeout", "reject"):
            with self.subTest(scenario=scenario):
                self.setUp()
                self.initialize({"elicitation": {}})
                count = 0
                def answer(_message, _schema):
                    nonlocal count
                    count += 1
                    if count == 1:
                        return {"action": "accept", "content": {"decision": "approve",
                            "inventory_confirmed": True, "max_passes": 6}}
                    self.assertEqual(count, 2)
                    content = {"decision": "approve", "inventory_confirmed": True}
                    if scenario == "edited_reply":
                        content["max_passes"] = 7
                    elif scenario == "stale_reply":
                        graph = copy.deepcopy(GRAPH)
                        graph["claims"][0]["text"] = "changed during final confirmation"
                        self.dispatch({"type": "ObserveAction", "run_id": self.run,
                            "action": {"kind": "graph", "payload": graph}})
                    elif scenario == "missing_confirmation":
                        content.pop("inventory_confirmed")
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
                if scenario == "missing_confirmation":
                    self.assertIn("inventory", result["reasons"][0]["message"])

    def test_host_owned_confirmation_checks_revision_even_if_digest_returns(self):
        self.initialize({"elicitation": {}})
        refresh = self.mediator.context_ingress
        refreshes = 0
        def context_ingress(profile, run, context):
            nonlocal refreshes
            refreshes += 1
            if refreshes == 2:
                graph = copy.deepcopy(GRAPH)
                graph["claims"][0]["text"] = "concurrent edit then revert"
                for value in (graph, GRAPH):
                    self.dispatch({"type": "ObserveAction", "run_id": run,
                        "action": {"kind": "graph", "payload": value}})
            return refresh(profile, run, context)
        self.mediator.context_ingress = context_ingress
        seen = []
        def answer(message, _schema):
            seen.append(message)
            self.assertEqual(len(seen), 1)
            return {"action": "accept", "content": {"decision": "approve",
                "inventory_confirmed": True, "max_passes": 6}}
        self.mediator.elicit = answer
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(refreshes, 2)
        self.assertEqual(len(seen), 1)
        self.assertEqual(result["reasons"][0]["code"], "governance.stale_proposal")
        self.assertIsNone(result["run"]["governance"]["approved_digest"])

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

    def test_host_owned_singleton_final_feedback_needs_no_consent(self):
        for final_decision, feedback in (("request_changes", True), ("approve", True), ("approve", False)):
            with self.subTest(final_decision=final_decision, feedback=feedback):
                self.setUp()
                self.initialize({"elicitation": {}})
                self.inventory["members"] = [AUTHOR]
                replies = iter([
                    {"action": "accept", "content": {"decision": "approve", "inventory_confirmed": True,
                     "allow_same_model": True}},
                    {"action": "accept", "content": {"decision": final_decision, "allow_same_model": False,
                     **({"change_request": "  revise the scope instead  "} if feedback else {"inventory_confirmed": True})}},
                ])
                self.mediator.elicit = lambda *_: next(replies)
                with patch("adapters.governance.operator_inventory", return_value=self.inventory):
                    result = self.mediator(self.dispatch({"type": "ObserveAction", "run_id": self.run,
                        "action": {"kind": "configure_run", "auditor": AUTHOR}})["result"])
                g = result["run"]["governance"]
                if feedback:
                    self.assertEqual(result["reasons"][0]["code"], "governance.changes_requested")
                    self.assertEqual(g["change_request"]["text"], "  revise the scope instead  ")
                else:
                    self.assertIn("Same-model", result["reasons"][0]["message"])
                    self.assertIsNone(g["change_request"])
                self.assertTrue(g["proposal"]["allow_same_model"])
                self.assertEqual(g["state"], "pending")
                self.assertIsNone(g["approved_digest"])

    def test_change_request_combines_feedback_and_numeric_edit_then_reapproves(self):
        self.initialize({"elicitation": {"form": {}}})
        original = self.dispatch({"type": "GetRun", "run_id": self.run})["result"]["run"]["governance"]
        self.mediator.elicit = lambda *_: {"action": "accept", "content": {
            "decision": "approve", "inventory_confirmed": True,
            "auditor": "anthropic/claude-opus-4-6", "max_passes": "6",
            "change_request": "  clarify restore behavior  "}}
        with patch("adapters.governance.operator_inventory", return_value=self.inventory):
            requested = self.mediator(self.dispatch({"type": "ObserveAction", "run_id": self.run,
                "action": {"kind": "configure_run", "auditor": AUDITOR}})["result"])
            self.assertEqual(requested["reasons"][0]["code"], "governance.changes_requested")
            g = requested["run"]["governance"]
            # Exact human text is kept; guidance points at the displayed revision, not the one it created.
            self.assertEqual(g["change_request"], {"text": "  clarify restore behavior  ",
                "plan_revision": g["plan_revision"] - 1,
                "proposal_digest": g["change_request"]["proposal_digest"]})
            self.assertGreater(g["plan_revision"] - 1, original["plan_revision"] - 1)
            self.assertNotEqual(g["change_request"]["proposal_digest"], g["proposal_digest"])
            self.assertEqual(g["proposal"]["budgets"]["max_passes"], 6)
            self.assertEqual(g["budgets"]["max_passes"], 8)
            self.assertEqual(g["budgets"]["passes_used"], 0)
            self.mediator.elicit = lambda *_: {"action": "accept", "content": {
                "decision": "approve", "inventory_confirmed": True,
                "auditor": "anthropic/claude-opus-4-6"}}
            approved = self.mediator(self.dispatch({"type": "ObserveAction", "run_id": self.run,
                "action": {"kind": "configure_run"}})["result"])
            self.assertEqual(approved["run"]["governance"]["state"], "approved")
            self.assertIsNone(approved["run"]["governance"]["change_request"])
            self.assertEqual(approved["run"]["governance"]["budgets"]["max_passes"], 6)

    def test_reject_needs_no_auditor_or_inventory_affirmation_and_safe_rendering_is_reversible(self):
        self.initialize({"elicitation": {}})
        self.mediator.elicit = lambda *_: {"action": "accept", "content": {"decision": "reject"}}
        with patch("adapters.governance.operator_inventory", return_value=self.inventory):
            rejected = self.mediator(self.dispatch({"type": "ObserveAction", "run_id": self.run,
                "action": {"kind": "configure_run", "auditor": AUDITOR}})["result"])
        self.assertEqual(rejected["run"]["governance"]["state"], "rejected")
        from adapters.governance import safe
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

    def test_presented_interaction_survives_revision_before_stale_reply(self):
        self.initialize({"elicitation": {}})
        def answer(_message, _schema):
            graph = copy.deepcopy(GRAPH)
            graph["claims"][0]["text"] = "revised while dialog open " + str(self.sequence)
            changed = self.dispatch({"type": "ObserveAction", "run_id": self.run,
                "action": {"kind": "graph", "payload": graph}})
            self.assertEqual(changed["result"]["type"], "Allow")
            return {"action": "cancel"}
        self.mediator.elicit = answer
        for n in range(3):
            result = self.propose()["result"]["structuredContent"]
            self.assertEqual(result["reasons"][0]["code"], "governance.stale_proposal")
            self.assertEqual(result["run"]["governance"]["interactions_remaining"]["total"], 127 - n)
            self.assertEqual(result["run"]["governance"]["state"], "pending")
            c = self.service._coordinator
            self.service = compose(Workspace(), Harness(), c.runs, c.artifacts, None, self.profile, {}, None)

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

    def test_initial_null_auditor_selection_amends_then_second_dialog_approves(self):
        self.initialize({"elicitation": {}})
        with patch("adapters.governance.operator_inventory", return_value=self.inventory):
            def propose():
                result = self.dispatch({"type": "ObserveAction", "run_id": self.run,
                    "action": {"kind": "configure_run"}})["result"]
                return self.mediator(result)
            approved = propose()
            self.assertEqual(approved["run"]["governance"]["state"], "approved")
            self.assertEqual(approved["run"]["governance"]["proposal"]["auditor"], AUDITOR)
            self.assertEqual(len(self.sent), 2)

    def test_singleton_control_refusal_acceptance_and_fresh_consent(self):
        self.initialize({"elicitation": {}})
        self.inventory["members"] = [AUTHOR]
        self.dispatch({"type": "ObserveAction", "run_id": self.run,
            "action": {"kind": "configure_run", "auditor": AUTHOR, "allow_same_model": True}})
        consent = False
        def answer(_message, schema):
            self.assertFalse(schema["properties"]["allow_same_model"]["default"])
            content = {"decision": "approve", "inventory_confirmed": True, "allow_same_model": consent}
            if "auditor" in schema["properties"]:
                content["auditor"] = "anthropic/" + AUTHOR["model_id"]
            return {"action": "accept", "content": content}
        self.mediator.elicit = answer
        with patch("adapters.governance.operator_inventory", return_value=self.inventory):
            def propose():
                return self.mediator(self.dispatch({"type": "ObserveAction", "run_id": self.run,
                    "action": {"kind": "configure_run"}})["result"])
            refused = propose()
            self.assertFalse(refused["run"]["governance"]["proposal"]["allow_same_model"])
            self.assertNotEqual(refused["run"]["governance"]["state"], "approved")
            consent = True
            self.assertEqual(propose()["run"]["governance"]["state"], "approved")

    def test_invalid_ui_content_dismisses_but_private_invalid_input_never_writes(self):
        self.initialize({"elicitation": {}})
        self.mediator.elicit = lambda *_: {"action": "accept", "content": {"invalid": True}}
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(result["run"]["governance"]["interactions_remaining"]["proposal"], 2)
        self.assertEqual(result["run"]["governance"]["state"], "pending")

    def test_known_pair_with_unmapped_inventory_member_reaches_ui(self):
        self.initialize({"elicitation": {}})
        self.inventory["members"].append({"provider_id": "unknown", "model_id": "private"})
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(result["run"]["governance"]["state"], "approved")
        self.assertIn("private", self.sent[0]["params"]["message"])

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
            config = root / "operator.json"
            config.write_text(json.dumps({"version": 1, "inventory": self.inventory}))
            env = {**os.environ, "EMPIRICA_HOME": str(root / "state"),
                   "EMPIRICA_REPO_DIR": str(root), "EMPIRICA_HOST_PROFILE_ID": self.profile,
                   "EMPIRICA_GOVERNANCE_CONFIG": str(config)}
            with patch.dict(os.environ, env):
                def call(command):
                    return bridge.handle({"protocol": "empirica/v2", "request_id": "setup",
                                          "command": command}, self.profile)["result"]
                run = call({"type": "StartRun", "goal": "subprocess approval",
                            "selector": {"project": "p", "session": "stdio"}})["run"]["id"]
                call({"type": "ObserveAction", "run_id": run, "action": {"kind": "graph", "payload": GRAPH}})
                bridge.trusted_governance_context(self.profile, run, {
                    "inventory": self.inventory, "author": AUTHOR, "ingress": "mcp_elicitation"})
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
                        "decision": "approve", "inventory_confirmed": True,
                        "auditor": "anthropic/claude-opus-4-6"}}})
                    confirmation = receive()
                    self.assertEqual(confirmation["method"], "elicitation/create")
                    self.assertNotEqual(confirmation["id"], dialog["id"])
                    self.assertIn("FINAL CONFIRMATION", confirmation["params"]["message"])
                    self.assertNotIn("auditor", confirmation["params"]["requestedSchema"]["properties"])
                    send({"id": confirmation["id"], "result": {"action": "accept", "content": {
                        "decision": "approve", "inventory_confirmed": True}}})
                    response = receive()
                    self.assertEqual(response["id"], "proposal")
                    self.assertEqual(response["result"]["structuredContent"]["run"]["governance"]["state"], "approved")
                    self.assertEqual(call({"type": "GetRun", "run_id": run})["run"]["governance"]["approval_kind"], "host_ui")
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

    def test_operator_inventory_not_public_and_form_contains_exact_scope(self):
        self.initialize({"elicitation": {}})
        self.propose()
        self.assertIn(GRAPH["claims"][0]["text"], self.sent[0]["params"]["message"])
        self.assertIn(AUTHOR["model_id"], self.sent[0]["params"]["message"])
        self.assertEqual(len(self.session.tools.definitions()), 3)
        self.assertNotIn("governance_decision", repr(self.session.tools.definitions()))
        view = self.dispatch({"type": "GetRun", "run_id": self.run})["result"]["run"]
        self.assertIn(view["governance"]["proposal_digest"], form(view)[0])


class GovernancePresentationTests(unittest.TestCase):
    """Pure presentation checks; real host/service transitions are tested above."""

    def view(self):
        from core.governance import initial
        budgets = {"max_passes": 8, "max_spawns": 0, "max_audit_spawns": 1}
        g = initial("Exact goal", budgets, {"cli_exec": False, "multi_provider": False})
        g.update(scope=copy.deepcopy(GRAPH), context=copy.deepcopy(CONTEXT),
                 budgets={**budgets, "passes_used": 2, "spawns_used": 0, "audit_spawns_used": 0},
                 inventory_status="multiple", interactions_remaining={"proposal": 3, "total": 128})
        return {"goal": "Exact goal", "governance": g}

    def test_safe_choices_bidi_and_nonzero_budget_floor(self):
        from adapters.governance import safe
        view = self.view()
        view["governance"]["context"]["inventory"]["members"].append(
            {"provider_id": "unknown", "model_id": "private\x1b[2J\u061c"})
        message, schema = form(view)
        self.assertEqual(safe("\u061c"), "\\u061c")
        self.assertNotEqual(safe("\u061c"), safe("\\u061c"))
        self.assertNotIn("\u061c", message)
        self.assertEqual(schema["properties"]["max_passes"]["minimum"], 2)
        self.assertEqual(schema["properties"]["max_passes"]["default"], 8)
        self.assertIn("Investigation passes: proposed 8, already used 2", message)
        choices = schema["properties"]["auditor"]["enum"]
        self.assertIn("unknown/private\\x1b[2J\\u061c", choices)
        self.assertTrue(all("\x1b" not in choice and "\u061c" not in choice for choice in choices))

    def test_maximal_scope_is_complete_without_relying_on_native_rendering(self):
        from adapters.governance import readable, safe
        view = self.view()
        graph = view["governance"]["scope"]
        graph["claims"] = [{"id": f"C{i}", "text": f"claim-{i} " + "中" * 2038,
                            "kind": "needs-experiment", "gating": i % 2 == 0} for i in range(32)]
        graph["root"] = "C0"
        graph["edges"] = [{"from": f"C{i}", "to": f"C{j}", "type": "SupportedBy"}
                          for i in range(32) for j in range(i + 1, 32)][:128]
        text = readable(view)
        for claim in graph["claims"]:
            self.assertIn(safe(claim["text"]), text)
        for edge in graph["edges"]:
            self.assertIn(f"{edge['from']} SupportedBy {edge['to']}", text)
        for value in ("Exact goal", "C0", "deliberative", "64", "complete=True", "authorized=True",
                      view["governance"]["proposal_digest"], AUTHOR["model_id"], AUDITOR["model_id"]):
            self.assertIn(value, text)


if __name__ == "__main__":
    unittest.main()
