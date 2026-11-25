#!/usr/bin/env python3
"""Fake host/MCP dialogs exercise the real public tool and private service lanes."""
import copy
import json
import os
import queue
import re
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sandbox_isolation import force_absent_sandbox_home  # noqa: E402

from adapters import bridge
from adapters.governance import (Approve, Dismiss, HostEnvironment, HostGovernance,
                                Reject, _duration, _is_document_mismatch, confirm_form,
                                governance_timeout,
                                 decision, expected_approval_kind, review_form)
from adapters.mcp_server import INGRESS, McpSession, profile_from_environment
from adapters.public_tools import PublicTools
from adapters.state.hitl import DocumentMismatch, HitlDocuments
from application import protocol as _proto
from application.location import decode_handle
from application.v2 import compose
from core import environment as core_environment, hitl as core_hitl
from test_governance import CONTEXT, GRAPH, AUTHOR
from governance_setup import TEST_INVOCATION, SIZED_RATIONALE, approve_current
from test_d7_transactions import Runs, Artifacts, Workspace, Harness
from application.protocol import host_profile_id  # noqa: E402
force_absent_sandbox_home()
CLAUDE_PROFILE = host_profile_id("claude-code")
_CONTRACTS = Path(__file__).resolve().parents[3] / "contracts" / "empirica" / "v2"
HITL_FIXTURES = _CONTRACTS / "hitl-fixtures"
HITL_SCHEMA = json.loads((_CONTRACTS / "hitl.schema.json").read_text("utf-8"))


#: A canonical bound document (ADR-0072 Q4 shape) for pure presentation tests.
TEST_DOCUMENT = {"digest": "sha256:" + "a" * 64,
                "path": "/Users/operator/empirica-projects/p-b0415a68d5f/r-b0415a68d5f7e3b9c1a6f04"
                         "c2d85790/gen-1/hitl/" + "a" * 64 + ".json",
                "amendment": {"budgets": {"max_audit_spawns": {"value": 2, "was": 2},
                                           "max_passes": {"value": 6, "was": 8},
                                           "max_spawns": {"value": 1, "was": 1}}}}


class GovernanceHostTests(unittest.TestCase):
    def setUp(self):
        self.profile = CLAUDE_PROFILE
        self.runs, self.artifacts = Runs(), Artifacts()
        self.service = compose(Workspace(), Harness(), self.runs, self.artifacts, None, self.profile, {}, None)
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
        # PR 08: the mediator writes its HITL documents once, write-first (ADR-0072), into a
        # sidecar store under a test-local machine home — never the author's real $EMPIRICA_HOME.
        self.document_home = tempfile.mkdtemp(prefix="hitl-home-")
        self.addCleanup(shutil.rmtree, self.document_home, ignore_errors=True)
        self.documents = HitlDocuments(home=Path(self.document_home))
        self.mediator = HostGovernance(self.profile,
            context_ingress=lambda _p, r, v: self.service.trusted_governance_context(run_id=r, payload=v),
            decision_ingress=lambda _p, r, v: self.service.trusted_governance_decision(run_id=r, payload=v),
            documents=self.documents)
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
        schema = review_form(dialog, TEST_DOCUMENT, 900)[1]
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
        confirm_schema = confirm_form(dialog, TEST_DOCUMENT, 900)[1]
        for answer in ({"action": "accept"}, {"action": "accept", "content": {}}):
            self.assertIsInstance(decision(answer, dialog, confirm_schema,
                                           confirmation=True), Approve)
        self.assertIsInstance(decision({"action": "accept", "content": {"max_passes": 6}},
                                       dialog, confirm_schema, confirmation=True), Dismiss)
        for answer in ({"action": "decline"}, {"action": "cancel"}, None):
            self.assertIsInstance(decision(answer, dialog, confirm_schema,
                                           confirmation=True), Dismiss)

    def governance_dialog(self):
        return self.service.trusted_governance_context(
            run_id=self.run, payload=copy.deepcopy(CONTEXT))["result"]["presentation"]["dialog"]

    def test_the_review_form_opens_with_the_lines_claude_code_shows(self):
        """PR 19 (finding N7 of the Lima qualification of ac401739): Claude Code 2.1.304 shows
        only the first ``CLAUDE_VISIBLE_LINES`` lines of a form's message and folds the rest,
        so the review opens with the heading (countdown riding it), the bound document's
        ``Details:`` and the goal the human judges the configuration by — never a blank line
        among them. The rationale pointer, the document's remaining lines and the legend stay
        below the fold in full, both with a live observer's link and in the fallback."""
        from adapters.confirmation import CLAUDE_VISIBLE_LINES
        dialog = self.governance_dialog()
        linked = {**TEST_DOCUMENT,
                 "url": "http://127.0.0.1:18770/app/?run=aaaabbbbcccc&decision=" + "0" * 12}
        for document in (TEST_DOCUMENT, linked):
            with self.subTest(linked="url" in document):
                message, _schema = review_form(dialog, document, 900)
                lines = message.splitlines()
                window = lines[:CLAUDE_VISIBLE_LINES]
                self.assertNotIn("", window, "no blank line in what Claude Code shows")
                self.assertTrue(window[0].startswith("Empirica · approve run configuration (epoch "), window[0])
                self.assertIn("reviews left", window[0], "the countdown rides the heading")
                self.assertTrue(window[1].startswith("Details: "), window[1])
                self.assertEqual(window[2], 'Goal: "supplied task"',
                                 "the goal is the line the form is judged by")
                # the folded tail keeps the whole record
                self.assertIn(f"{dialog['rationale_label']} and full detail: in the document", message)
                document_line = next(index for index, line in enumerate(lines)
                                     if line.startswith("Document sha256:"))
                self.assertGreater(document_line, CLAUDE_VISIBLE_LINES - 1,
                                  "the digest stays below the visible lines")
                if "url" in document:
                    self.assertNotIn("File: ", message, "a verified link replaces the path entirely (O5)")
                else:
                    self.assertTrue(lines[document_line + 1].startswith("File: /"),
                                   "the fallback keeps the exact absolute path")
                self.assertIn("Accept = approve as shown · edit values → confirm again", message)
                self.assertIn("Decline = reject · Esc = decide later", message)
                for line in lines:
                    if not line.startswith(("Details: http://", "File: ")):
                        self.assertLessEqual(len(line), 72, line)

    def test_B1_a_raise_form_says_it_raises_the_approved_ceilings_and_shows_each_as_was(self):
        """A raise is not a first approval: the heading says it raises the approved ceilings, and the line the human judges it by
        shows each raised ceiling as ``N (was M)``, M being the value the run approved (the way the edited-configuration form
        shows a prior). Everything else of the review form, the document binding included, is as before."""
        from adapters.confirmation import CLAUDE_VISIBLE_LINES
        dialog = copy.deepcopy(self.governance_dialog())
        dialog["epoch"] = 2
        dialog["budgets"][0]["value"] = 10  # passes: proposed above what was approved
        approved = {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}
        plain, _ = review_form(dialog, TEST_DOCUMENT, 900)
        message, schema = review_form(dialog, TEST_DOCUMENT, 900, approved=approved)
        lines = message.splitlines()
        window = lines[:CLAUDE_VISIBLE_LINES]
        self.assertNotIn("", window)
        self.assertTrue(window[0].startswith("Empirica · raise approved ceilings (epoch 2 · "), window[0])
        self.assertNotIn("approve run configuration", message)
        self.assertEqual(window[1], "passes 10 (was 8) · spawns 1 · audits 2", "only a raised ceiling carries a prior")
        self.assertTrue(window[2].startswith("Details: "), window[2])
        for kept in ('Goal: "supplied task"', "Accept = approve as shown · edit values → confirm again",
                     "Decline = reject · Esc = decide later", f"{dialog['rationale_label']} and full detail: in the document"):
            self.assertIn(kept, message)
        self.assertEqual(sorted(schema["properties"]), sorted(row["key"] for row in dialog["budgets"]))
        self.assertIn("(was 8)", schema["properties"]["max_passes"]["title"])
        self.assertNotIn("(was", schema["properties"]["max_spawns"]["title"])
        for line in lines:
            if not line.startswith(("Details: http://", "File: ")):
                self.assertLessEqual(len(line), 72, line)
        # a ceiling proposed below the approved one is part of the change the human judges: it carries its prior too, as the
        # edited-configuration form's does
        dialog["budgets"][1]["value"] = 0
        lowered, lowered_schema = review_form(dialog, TEST_DOCUMENT, 900, approved=approved)
        self.assertEqual(lowered.splitlines()[1], "passes 10 (was 8) · spawns 0 (was 1) · audits 2")
        self.assertIn("(was 1)", lowered_schema["properties"]["max_spawns"]["title"])
        dialog["budgets"][1]["value"] = 1
        # no raise, no change: the first approval's form is exactly what it was
        self.assertTrue(plain.startswith("Empirica · approve run configuration (epoch "))
        self.assertEqual(review_form(dialog, TEST_DOCUMENT, 900, approved=None), (plain, review_form(dialog, TEST_DOCUMENT, 900)[1]))

    def test_the_confirm_form_opens_with_its_budgets_and_rationale_cue(self):
        """PR 19 (finding N7) with the B4 amendment: the confirm's visible lines carry what the
        human judges — the heading, the budgets line complete with its ``(was …)`` priors, and
        the compact cue that the rationale was not regenerated — and only then the ``Details:``
        line. The contract-owned amendment warning and the bound document stay below, whole."""
        from adapters.confirmation import CLAUDE_VISIBLE_LINES
        dialog = copy.deepcopy(self.governance_dialog())
        dialog["epoch"] = 1
        dialog["budgets"][0]["value"] = 6
        linked = {**TEST_DOCUMENT,
                 "url": "http://127.0.0.1:18770/app/?run=aaaabbbbcccc&decision=" + "0" * 12}
        for document in (TEST_DOCUMENT, linked):
            with self.subTest(linked="url" in document):
                message, _schema = confirm_form(dialog, document, 900)
                lines = message.splitlines()
                window = lines[:CLAUDE_VISIBLE_LINES]
                self.assertNotIn("", window, "no blank line in what Claude Code shows")
                self.assertTrue(lines[0].startswith("Empirica · confirm edited configuration (epoch "), lines[0])
                budgets = window[1]
                self.assertTrue(budgets.startswith("passes 6 (was 8)"), budgets)
                for short in ("passes", "spawns", "audits"):
                    self.assertIn(short + " ", budgets + " ",
                                  f"every budget of the proposal rides the line: {budgets}")
                self.assertEqual(window[2], "Rationale not regenerated for these values · details below",
                                 "the compact cue names what the full warning below explains")
                self.assertTrue(lines[3].startswith("Details: "), lines[3])
                self.assertIn("not regenerated for these", " ".join(message.split()),
                              "the contract-owned amendment warning stays below, whole")
                self.assertIn("human-edited values", " ".join(message.split()))
                document_line = next(index for index, line in enumerate(lines)
                                     if line.startswith("Document sha256:"))
                self.assertGreater(document_line, 3, "the document's digest stays below the cue")
                self.assertIn("Accept = approve exactly this · Decline/Esc = keep edits pending", message
                             .splitlines()[-1])
                for line in lines:
                    if not line.startswith(("Details: http://", "File: ")):
                        self.assertLessEqual(len(line), 72, line)

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
                self.assertEqual(result["reasons"][0]["code"], "governance.dialog_closed")
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
        self.assertEqual(stop_result(typed, stop_hook_active=False).exit_code, 0)
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

    # --- the Claude forms are short and bound to the HITL document (ADR-0072, PR 08) ---

    def _stored_receipts(self):
        self.view_fresh = self.dispatch({"type": "GetRun", "run_id": self.run})
        return self.service._coordinator.last_snapshot.state.governance["receipts"]

    def _context_document(self, mutate=None):
        """The private context answer's document pair, optionally mutated by a test."""
        context = copy.deepcopy(CONTEXT)
        context["ingress"] = "mcp_elicitation"
        response = self.service.trusted_governance_context(run_id=self.run, payload=context)
        presentation = response["result"]["presentation"]
        if mutate is not None:
            mutate(presentation)
        return presentation.get("document")

    def test_the_approve_form_is_short_and_bound_to_the_document(self):
        """The form keeps what is judged (the numbers, the goal's first line) and binds to the
        written document: the rationale text lives only in the document, and the short digest and
        the document's absolute path stand on their own lines."""
        self.initialize({"elicitation": {"form": {}}})
        seen = []
        def answer(message, _schema):
            seen.append(message)
            return {"action": "accept", "content": {}}
        self.mediator.elicit = answer
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(result["run"]["governance"]["state"], "approved")
        self.assertEqual(len(seen), 1)
        lines = seen[0].splitlines()
        self.assertIn('Goal: "supplied task"', lines)
        self.assertNotIn(SIZED_RATIONALE, seen[0])
        self.assertIn("unverified", seen[0])
        # PR 16 (A11), follow-up review 16-1: the fallback Details is one short arrow line naming
        # this host's own command and the run (never wrapping mid-phrase), the document's short
        # digest follows it, and the exact absolute path (the one copy-paste surface, ``File: ``)
        # is kept only because no observer of this temp home is live.
        details = next(line for line in lines if line.startswith("Details: "))
        self.assertRegex(details, r"^Details: /empirica:ui → run [0-9a-f]{12} → Human decisions$")
        self.assertLessEqual(len(details), 76, "the arrow line never wraps mid-phrase (review 16-1)")
        document_line = next(index for index, line in enumerate(lines)
                             if line.startswith("Document sha256:"))
        self.assertIsNotNone(re.fullmatch(r"Document sha256:[0-9a-f]{12}", lines[document_line]))
        path_line = lines[document_line + 1]
        self.assertTrue(path_line.startswith("File: /"), path_line)
        path = path_line[len("File: "):]
        self.assertTrue(path.startswith("/"), path)
        self.assertTrue(path.endswith(".json"), path)
        self.assertNotIn("…", path)
        # the path names a file that exists and holds exactly what the core rendered
        written = Path(path).read_bytes()
        receipt = self._stored_receipts()[-1]
        self.assertEqual(core_hitl.digest(written), receipt["document_digest"])

    def test_host_owned_form_links_the_document_when_an_observer_serves_this_home(self):
        """PR 16 (A11): with a live observer serving this home, the form shows the deep link to
        the run's decision row in the UI and drops the document path from the form entirely (O5
        as decided: the path is dropped only when a verified link is shown); the digest stays
        the bound identity, and the discovery probe happens once per form presentation."""
        from adapters.observer import discovery
        self.initialize({"elicitation": {"form": {}}})
        seen = []
        def answer(message, _schema):
            seen.append(message)
            return {"action": "accept", "content": {}}
        self.mediator.elicit = answer
        with patch.object(discovery, "observer_url", return_value="http://127.0.0.1:18770") as probe:
            result = self.propose()["result"]["structuredContent"]
        self.assertEqual(result["run"]["governance"]["state"], "approved")
        receipt = self._stored_receipts()[-1]
        short = receipt["document_digest"][len("sha256:"):len("sha256:") + 12]
        link = ("Details: http://127.0.0.1:18770/app/?run="
                + discovery.compact_run_id(self.run) + "&decision=" + short)
        self.assertIn(link, seen[0])
        self.assertNotIn("File: ", seen[0], "a verified link replaces the path entirely")
        self.assertIn(f"Document sha256:{short}", seen[0])
        self.assertEqual(probe.call_count, 1, "one probe per form presentation, not per item")

    def test_the_document_is_written_before_the_present_and_binds_the_receipt(self):
        """Write-first order (ADR-0072): the sidecar file already holds exactly the document's
        bytes, mode 0400 (owner read-only), before the present call reaches the service; the present and the
        decision envelopes both carry the digest, and the stored receipt names it."""
        self.initialize({"elicitation": {"form": {}}})
        order = []
        real_ingress = self.mediator.decision_ingress
        def probe(_profile, run_id, payload):
            if payload.get("outcome") == "present":
                key = decode_handle(run_id)
                path = self.documents.path(key, payload["document_digest"])
                data = path.read_bytes()
                order.append({"file-exists-before-present": True,
                             "mode": oct(stat.S_IMODE(os.stat(path).st_mode)),
                             "bytes-are-the-document": core_hitl.digest(data) == payload["document_digest"],
                             "digest": payload["document_digest"], "path": str(path)})
            else:
                order.append({"decision": True,
                              "submission": "submission" in payload,
                              "carries-digest": payload.get("document_digest") is not None,
                              "digest": payload.get("document_digest")})
            return real_ingress(_profile, run_id, payload)
        self.mediator.decision_ingress = probe
        self.mediator.elicit = lambda *_: {"action": "accept", "content": {}}
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(result["run"]["governance"]["state"], "approved")
        self.assertEqual(len(order), 2)
        self.assertEqual([event.get("file-exists-before-present") for event in order], [True, None])
        self.assertEqual(order[0]["mode"], "0o400")
        self.assertTrue(order[0]["bytes-are-the-document"])
        self.assertTrue(order[1]["carries-digest"])
        self.assertEqual(order[1]["digest"], order[0]["digest"])
        receipt = self._stored_receipts()[-1]
        self.assertEqual(receipt["document_digest"], order[1]["digest"])

    def test_a_missing_or_malformed_document_refuses_before_any_present(self):
        """No document, no form: a context answer whose document is absent or not exactly the
        rendered pair binds nothing, and the mediation says unavailable — nothing spent."""
        def mutant(kind):
            def mutate(presentation):
                document = presentation.get("document")
                if kind == "absent":
                    presentation.pop("document", None)
                elif kind == "digest-mismatch":
                    presentation["document"] = {
                        **document,
                        "digest": document["digest"][:-1] + ("0" if document["digest"][-1] != "0" else "1")}
                elif kind == "text-not-a-string":
                    presentation["document"] = {**document, "text": b"not text"}
                elif kind == "short-digest":
                    presentation["document"] = {**document, "digest": document["digest"][:17]}
                elif kind == "empty-document":
                    # X3 (Copilot #49): a digest-CONSISTENT pair whose text parses to {} — the
                    # object and digest checks pass, and without the question check the
                    # mediation would present a form chosen from nothing and eat the mismatch.
                    data = core_hitl.render({})
                    presentation["document"] = {"text": data.decode("utf-8"),
                                                "digest": core_hitl.digest(data)}
                elif kind == "unsupported-question":
                    data = core_hitl.render({"question": "other"})
                    presentation["document"] = {"text": data.decode("utf-8"),
                                                "digest": core_hitl.digest(data)}
            return mutate
        for kind in ("absent", "digest-mismatch", "text-not-a-string", "short-digest",
                     "empty-document", "unsupported-question"):
            with self.subTest(document=kind):
                self.setUp()
                self.initialize({"elicitation": {"form": {}}})
                presents = []
                real_ingress = self.mediator.decision_ingress
                def decision_probe(_p, r, v):
                    if v.get("outcome") == "present":
                        presents.append(v)
                    return real_ingress(_p, r, v)
                self.mediator.decision_ingress = decision_probe
                real_context = self.mediator.context_ingress
                def context_probe(_p, r, v):
                    response = real_context(_p, r, v)
                    mutant(kind)(response["result"]["presentation"])
                    return response
                self.mediator.context_ingress = context_probe
                self.mediator.elicit = lambda *_: (_ for _ in ()).throw(AssertionError("never elicit"))
                result = self.propose()["result"]["structuredContent"]
                self.assertEqual(result["type"], "Block")
                self.assertEqual(result["reasons"][0]["code"], "governance.dialog_closed")  # nothing shown: the chooser reads the run's facts
                self.assertEqual(presents, [])
                self.assertEqual(len(self._stored_receipts()), 0)
                self.assertIsNone(result["run"]["governance"]["approved_digest"])

    def test_a_sidecar_write_failure_is_unavailable_before_any_present(self):
        """Storage cannot lie into presenting: any write error names the refusal, nothing spent."""
        class Broken:
            def write(self, _key, _data):
                raise OSError(28, "no space left")
        class Mismatched:
            def write(self, _key, _data):
                raise DocumentMismatch("different bytes already published")
        for store, label in ((Broken(), "oserror"), (Mismatched(), "document-mismatch")):
            with self.subTest(error=label):
                self.setUp()
                self.initialize({"elicitation": {"form": {}}})
                self.mediator.documents = store
                presents = []
                real_ingress = self.mediator.decision_ingress
                def decision_probe(_p, r, v):
                    if v.get("outcome") == "present":
                        presents.append(v)
                    return real_ingress(_p, r, v)
                self.mediator.decision_ingress = decision_probe
                self.mediator.elicit = lambda *_: (_ for _ in ()).throw(AssertionError("never elicit"))
                result = self.propose()["result"]["structuredContent"]
                self.assertEqual(result["type"], "Block")
                self.assertEqual(result["reasons"][0]["code"], "governance.dialog_closed")  # nothing shown: the chooser reads the run's facts
                self.assertEqual(presents, [])
                self.assertEqual(len(self._stored_receipts()), 0)
                self.assertEqual(self.service._coordinator.last_snapshot.state.budgets["passes_used"], 0)

    def test_a_document_mismatch_block_reaches_the_result_unchanged(self):
        """The core's own refusal is never retried or reworded by the adapter (ADR-0072): a
        present bound to another document's digest comes back as governance.document_mismatch."""
        self.initialize({"elicitation": {"form": {}}})
        real_ingress = self.mediator.decision_ingress
        def tamper(_profile, run_id, payload):
            if payload.get("outcome") == "present" and payload.get("document_digest"):
                document_digest = payload["document_digest"]
                payload = {**payload,
                           "document_digest": document_digest[:-1] + ("0" if document_digest[-1] != "0" else "1")}
            return real_ingress(_profile, run_id, payload)
        self.mediator.decision_ingress = tamper
        self.mediator.elicit = lambda *_: (_ for _ in ()).throw(AssertionError("never elicit"))
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(result["type"], "Block")
        self.assertEqual(result["reasons"][0]["code"], "governance.document_mismatch")
        self.assertEqual(len(self._stored_receipts()), 0)
        self.assertIsNone(result["run"]["governance"]["approved_digest"])

    def test_pre_approval_amend_shows_the_core_owned_priors_in_the_confirm_form(self):
        """The confirm form's (was …) values come from the document's amendment priors (run/4's
        superseded ceilings reached through the core), and the confirm document is written
        beside the run before the confirmation is asked."""
        self.initialize({"elicitation": {"form": {}}})
        seen = []
        def answer(message, _schema):
            seen.append(message)
            if len(seen) == 1:
                return {"action": "accept", "content": {"max_passes": 6}}
            return {"action": "accept", "content": {}}
        self.mediator.elicit = answer
        result = self.propose()["result"]["structuredContent"]
        self.assertEqual(result["run"]["governance"]["state"], "approved")
        self.assertEqual(len(seen), 2)
        confirm = seen[1]
        self.assertIn("passes 6 (was 8)", confirm)
        self.assertNotIn(SIZED_RATIONALE, confirm)
        lines = confirm.splitlines()
        document_line = next(index for index, line in enumerate(lines)
                             if line.startswith("Document sha256:"))
        self.assertIsNotNone(re.fullmatch(r"Document sha256:[0-9a-f]{12}", lines[document_line]))
        self.assertTrue(lines[document_line + 1].startswith("File: /"),
                        "the fallback keeps the confirm document's exact absolute path (A11)")
        files = sorted(Path(self.document_home).glob("**/hitl/*.json"))
        self.assertEqual(len(files), 2,
                         "the approve document and the confirm document are both written")
        amend = next(receipt for receipt in self._stored_receipts()
                     if receipt["outcome"] == "amend")
        self.assertEqual(amend["superseded_budgets"]["max_passes"], 8)
        # the confirm document's (was …) priors equal those core-owned superseded ceilings
        confirm_path = next(path for path in files
                            if json.loads(path.read_bytes())["question"] == "confirm_amendment")
        confirm_document = json.loads(confirm_path.read_bytes())
        self.assertEqual(confirm_document["amendment"]["budgets"]["max_passes"]["was"], 8)
        # and the receipt that finalises names exactly the confirm document the human judged
        final = next(receipt for receipt in self._stored_receipts()
                     if receipt["outcome"] == "approve")
        self.assertEqual(final["document_digest"], core_hitl.digest(confirm_path.read_bytes()))
        self.assertEqual(result["run"]["governance"]["budgets"]["max_passes"], 6)

    def test_the_form_follows_the_documents_question_after_a_dismissed_confirm(self):
        """M1 (review 2): the form is chosen by the core-derived document question, never by the
        adapter's own recursion flag. Amend 8→6, dismiss the confirm form, then re-propose the
        amended values (a no-op revision): the next mediation must show the CONFIRM form, with
        its (was 8) priors and the amendment warning, bound to the confirm_amendment document."""
        self.initialize({"elicitation": {"form": {}}})
        answers = iter([{"action": "accept", "content": {"max_passes": 6}},
                        {"action": "cancel"}, {"action": "cancel"}])
        seen = []
        self.mediator.elicit = lambda message, schema: (seen.append((message, schema)), next(answers))[1]
        first = self.propose()["result"]["structuredContent"]
        self.assertEqual(first["type"], "Block")
        self.assertEqual(first["run"]["governance"]["state"], "pending")
        self.assertEqual(first["run"]["governance"]["proposal"]["budgets"]["max_passes"], 6)
        # the no-op re-proposal of the same values: the epoch's document is a confirm_amendment
        # document (the amend receipt is the epoch's own), so the next mediation shows the
        # confirm form the record says the human is answering
        self.mediator(self.dispatch({"type": "ObserveAction", "run_id": self.run,
            "action": {"kind": "configure_run", "budgets": {"max_passes": 6, "max_spawns": 1,
                           "max_audit_spawns": 2}, "rationale": SIZED_RATIONALE}})["result"])
        self.assertEqual(len(seen), 3)
        confirm, schema = seen[2]
        self.assertIn("confirm edited configuration", confirm)
        self.assertIn("passes 6 (was 8)", confirm)
        self.assertIn("not regenerated for these", confirm)  # the amendment warning
        self.assertNotIn(SIZED_RATIONALE, confirm)
        self.assertEqual(schema["properties"], {})  # buttons-only, no editable fields
        # the form the human saw is the one document the epoch bound: confirm_amendment
        files = sorted(Path(self.document_home).glob("**/hitl/*.json"))
        questions = [json.loads(path.read_bytes())["question"] for path in files]
        self.assertEqual(questions.count("confirm_amendment"), len(questions) - 1)
        # the re-proposal's confirm document is byte-identical to the first one, so the
        # write-once publish is idempotent: approve + one confirm document, two files
        self.assertEqual(len(files), 2)
        confirm_document = json.loads(next(
            path.read_bytes() for path in files
            if json.loads(path.read_bytes())["question"] == "confirm_amendment"))
        self.assertEqual(confirm_document["amendment"]["budgets"]["max_passes"]["was"], 8)

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
            decision_ingress=lambda _p, r, v: self.service.trusted_governance_decision(run_id=r, payload=v),
            documents=self.documents)
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
                "rationale": "raise the pass allowance back"}})["result"]
        self.assertTrue(raised["run"]["governance"]["raises"])
        self.assertTrue(raised["run"]["governance"]["human_wait"])
        # What the turn end does with the real raise view: the core's actor is the human, and both hosts' Stop settles with
        # the configuration wait on the first turn end and on a later one, never a continuation over the dialog.
        from adapters.claude.completion import stop_result
        from adapters.claude.transport import Response
        self.dispatch({"type": "ObserveAction", "run_id": auto, "action": {"kind": "route", "reason": "supplied context"}})
        envelope = self.dispatch({"type": "EvaluateRun", "run_id": auto, "intent": "report_convergence"})
        self.assertEqual((envelope["result"]["type"], envelope["result"]["run"]["turn_end_actor"]), ("Block", "human"))
        for flag in (False, True):
            stop = stop_result(Response.from_envelope({"request_id": envelope["request_id"]}, envelope), stop_hook_active=flag)
            self.assertEqual((stop.exit_code, json.loads(stop.stdout)), (0, {"systemMessage": _proto.HUMAN_WAIT_NOTICE}), flag)
        asked = mediator(raised)  # A11d: a raise above the approved ceilings is the human's to approve
        self.assertEqual(asked["run"]["governance"]["approval_kind"], "host_ui")
        self.assertEqual(asked["run"]["governance"]["state"], "approved")
        self.assertEqual(len(calls), 2, "the raise opens the one further dialog")
        # B1: that dialog says it is a raise of the approved ceilings, naming each raised one with the approved value
        raise_message = calls[1][0]
        self.assertTrue(raise_message.startswith("Empirica · raise approved ceilings (epoch "), raise_message)
        self.assertIn("passes 8 (was 7)", raise_message)
        self.assertTrue(calls[0][0].startswith("Empirica · approve run configuration (epoch "), "the first approval is unchanged")

    def test_a_reduction_through_a_profile_without_ingress_reports_the_registry_wire_value(self):
        """An automatic reduction of an approved run needs no human, so a profile that cannot ask
        one still mediates it: its context submission carries the registry's own value for "no
        approval ingress" — the wire the configuration flow always used — and the reduction is
        approved without a form."""
        auto = self.dispatch({"type": "StartRun", "control_mode": "auto", "goal": "auto task",
                              "invocation": {**TEST_INVOCATION, "delegation": True},
                              "selector": {"project": "p", "session": "auto-no-ingress"}})["result"]["run"]["id"]
        self.dispatch({"type": "ObserveAction", "run_id": auto, "action": {"kind": "graph", "payload": GRAPH}})
        approve_current(self.service._coordinator, auto)
        lower = self.dispatch({"type": "ObserveAction", "run_id": auto, "action": {"kind": "configure_run", "budgets": {
            "max_passes": 7, "max_spawns": 1, "max_audit_spawns": 2}, "rationale": "same graph, lower pass allowance"}})["result"]
        other = next(p for p in _proto.profile_ids() if _proto.approval_ingress(p) is None)
        service = compose(Workspace(), Harness(), self.runs, self.artifacts, None, other, {}, None)
        contexts = []
        mediator = HostGovernance(other, elicit=None, documents=self.documents,
            context_ingress=lambda _p, r, v: contexts.append(v) or service.trusted_governance_context(run_id=r, payload=v),
            decision_ingress=lambda _p, r, v: service.trusted_governance_decision(run_id=r, payload=v))
        lowered = mediator(lower)
        self.assertEqual([context["ingress"] for context in contexts],
                         [_proto.host_profile(other)["approval_ingress"]])
        self.assertEqual((lowered["type"], lowered["run"]["governance"]["state"],
                          lowered["run"]["governance"]["approval_kind"]), ("Allow", "approved", "auto"))
        self.assertEqual(lowered["run"]["governance"]["budgets"]["max_passes"], 7)

    def test_F2_a_raise_nobody_can_approve_names_the_proposal_within_the_ceilings_and_the_stop(self):
        """Onto-S F2: after the first approval a pending raise needs a human; with none to ask and a revision left, the reason
        and the run's Next agree on "propose within the approved ceilings, or stop" (not the stop alone)."""
        auto = self.dispatch({"type": "StartRun", "control_mode": "auto", "goal": "auto raise no ui",
                              "invocation": {**TEST_INVOCATION, "delegation": True},
                              "selector": {"project": "p", "session": "auto-raise-no-ui"}})["result"]["run"]["id"]
        self.dispatch({"type": "ObserveAction", "run_id": auto, "action": {"kind": "route", "reason": "supplied context"}})
        self.dispatch({"type": "ObserveAction", "run_id": auto, "action": {"kind": "graph", "payload": GRAPH}})
        ingress = dict(context_ingress=lambda _p, r, v: self.service.trusted_governance_context(run_id=r, payload=v),
                       decision_ingress=lambda _p, r, v: self.service.trusted_governance_decision(run_id=r, payload=v),
                       documents=self.documents)
        mediator = HostGovernance(self.profile, elicit=lambda message, schema: {"action": "accept", "content": {}}, **ingress)
        budgets = {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}
        first = self.dispatch({"type": "ObserveAction", "run_id": auto, "action": {
            "kind": "configure_run", "budgets": {**budgets, "max_passes": 7}, "rationale": SIZED_RATIONALE}})["result"]
        self.assertEqual(mediator(first)["run"]["governance"]["state"], "approved")
        raised = self.dispatch({"type": "ObserveAction", "run_id": auto, "action": {
            "kind": "configure_run", "budgets": budgets, "rationale": "raise the pass allowance back"}})["result"]
        self.assertTrue(raised["run"]["governance"]["raises"])
        # the host's start hook reported its author, so "nobody can be asked" is a fact the core holds, not "not reported yet"
        self.service.trusted_governance_context(run_id=auto, payload={"author": AUTHOR, "ingress": "mcp_elicitation"})
        raised = self.dispatch({"type": "GetRun", "run_id": auto})["result"]
        no_ui = HostGovernance(self.profile, elicit=None, **ingress)  # this host can show no dialog now
        out = no_ui(raised)
        reason = out["reasons"][0]
        self.assertEqual((out["type"], reason["code"], reason["next_actions"]),
                         ("Block", "governance.raise_unavailable", ["governance.propose", "residual.accept"]))
        self.assertEqual(out["run"]["next_actions"], ["governance.propose", "residual.accept"])
        self.assertIn("within the approved ceilings", reason["message"])

    def _raise_pending_after_approval(self, run, mode, session):
        """A run approved by its human at 7 passes, then a proposal back at 8 (a raise) waiting; returns the mediators' ingress."""
        self.dispatch({"type": "ObserveAction", "run_id": run, "action": {"kind": "route", "reason": "supplied context"}})
        self.dispatch({"type": "ObserveAction", "run_id": run, "action": {"kind": "graph", "payload": GRAPH}})
        ingress = dict(context_ingress=lambda _p, r, v: self.service.trusted_governance_context(run_id=r, payload=v),
                       decision_ingress=lambda _p, r, v: self.service.trusted_governance_decision(run_id=r, payload=v),
                       documents=self.documents)
        mediator = HostGovernance(self.profile, elicit=lambda message, schema: {"action": "accept", "content": {}}, **ingress)
        budgets = {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}
        first = self.dispatch({"type": "ObserveAction", "run_id": run, "action": {
            "kind": "configure_run", "budgets": {**budgets, "max_passes": 7}, "rationale": SIZED_RATIONALE}})["result"]
        self.assertEqual(mediator(first)["run"]["governance"]["state"], "approved")
        raised = self.dispatch({"type": "ObserveAction", "run_id": run, "action": {
            "kind": "configure_run", "budgets": budgets, "rationale": "raise the pass allowance back"}})["result"]
        self.assertTrue(raised["run"]["governance"]["raises"])
        self.assertEqual(raised["run"]["governance"]["control_mode"], mode)
        return ingress

    def test_F1_a_deliberative_raise_nobody_can_approve_names_the_stop_alone(self):
        """Every deliberative proposal goes to the human, so with no ingress no revision is automatic: the reason and the run's
        Next agree on the stop alone (the within-the-ceilings way on belongs to interactive auto)."""
        ingress = self._raise_pending_after_approval(self.run, "deliberative", "delib")
        self.service.trusted_governance_context(run_id=self.run, payload={"author": AUTHOR, "ingress": "mcp_elicitation"})
        raised = self.dispatch({"type": "GetRun", "run_id": self.run})["result"]
        out = HostGovernance(self.profile, elicit=None, **ingress)(raised)  # this host can show no dialog now
        reason = out["reasons"][0]
        self.assertEqual((out["type"], reason["code"], reason["next_actions"]),
                         ("Block", "governance.approval_unavailable", ["residual.accept"]))
        self.assertEqual(out["run"]["next_actions"], ["residual.accept"])

    def test_F2_a_raise_with_the_run_total_spent_names_the_proposal_within_the_ceilings_and_the_stop(self):
        """The run's interactions are spent while a human could otherwise be reached: nothing can be asked, but a revision
        within the approved ceilings asks nobody, so the reason and the run's Next agree on propose or stop."""
        auto = self.dispatch({"type": "StartRun", "control_mode": "auto", "goal": "auto raise spent",
                              "invocation": {**TEST_INVOCATION, "delegation": True},
                              "selector": {"project": "p", "session": "auto-raise-spent"}})["result"]["run"]["id"]
        ingress = self._raise_pending_after_approval(auto, "auto", "auto")
        from core import governance as core_governance
        receipts = len(self.service._coordinator.last_snapshot.state.governance["receipts"])
        with patch.object(core_governance, "MAX_INTERACTIONS", receipts):
            raised = self.dispatch({"type": "GetRun", "run_id": auto})["result"]
            self.assertEqual(raised["run"]["governance"]["interactions_remaining"]["total"], 0)
            shown = []
            out = HostGovernance(self.profile, elicit=lambda message, schema: shown.append(message) or {"action": "accept", "content": {}},
                                 **ingress)(raised)
        reason = out["reasons"][0]
        self.assertEqual(shown, [], "no dialog opens when the allowance is spent")
        self.assertEqual((out["type"], reason["code"], reason["next_actions"]),
                         ("Block", "governance.raise_unavailable", ["governance.propose", "residual.accept"]))
        self.assertEqual(out["run"]["next_actions"], ["governance.propose", "residual.accept"])

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
            {"control_mode": "deliberative", "first_approval": False, "raises": False,
             "context": {"interactive": True}},
            {"control_mode": "auto", "first_approval": False, "raises": False,
             "context": {"interactive": True}},
            {"control_mode": "auto", "first_approval": True, "raises": False,
             "context": {"interactive": True}},
            {"control_mode": "auto", "first_approval": True, "raises": True,
             "context": {"interactive": True}},
            {"control_mode": "auto", "first_approval": True, "raises": True,
             "context": {"interactive": False}},
            {"control_mode": "auto", "first_approval": False, "raises": False,
             "context": {"interactive": False}},
            {"control_mode": "auto", "first_approval": False, "raises": False,
             "context": {"interactive": None}},
            {"control_mode": "deliberative", "first_approval": False, "raises": False,
             "context": {"interactive": False}},
        ]
        expected = [expected_approval_kind(case, case["raises"]) for case in cases]
        self.assertEqual(expected[-2:], ["auto", "host_ui"])
        self.assertEqual(expected[2:5], ["auto", "host_ui", "auto"])  # a raise is the interactive human's, once approved
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
        self.assertEqual(result["reasons"][0]["code"], "governance.configuration_shown")
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
        self.mediator.profile = host_profile_id("codex-cli")
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
        form_message, _ = review_form(dialog, TEST_DOCUMENT, 900)
        self.assertNotIn(view["governance"]["proposal_digest"], form_message)


class GovernancePresentationTests(unittest.TestCase):
    """Pure presentation checks; real host/service transitions are tested above."""

    def view(self):
        from core.governance import initial, revise
        budgets = {"max_passes": 8, "max_spawns": 0, "max_audit_spawns": 1}
        invocation = {"host": "test", "interactive": True,
                      "signal": "operator", "delegation": False, "audit_retrieval": ["local", "workspace"], "human_surface": "unavailable"}
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
        message, _ = review_form(dialog, TEST_DOCUMENT, 900)
        self.assertIn("Goal:", message)
        self.assertIn("Exact goal", message)
        self.assertNotIn(view["governance"]["proposal_digest"], message)
        self.assertNotIn("work graph is not approvable", message)
        self.assertNotIn(AUTHOR["model_id"], message)


class EnvironmentHostTests(unittest.TestCase):
    """The Claude mediation of one pending environment revision (E8, spec §5/§6, ADR-0072):
    the session's own public tool admits the revision, then the host completes the human
    approval the core waits on — the document written first through the one bound sidecar
    helper, its digest binding the present and the decision, the elicit form keeping exactly
    what the human decides by. The session's real elicitation path carries every
    answer (initialize, matching wire replies, cancel, the session's own ``None`` on timeout);
    only the private legs reach the in-process service, as the governance tests do."""

    def setUp(self):
        self.profile = CLAUDE_PROFILE
        self.runs, self.artifacts = Runs(), Artifacts()
        self.service = compose(Workspace(), Harness(), self.runs, self.artifacts, None, self.profile, {}, None)
        self.sequence = 0
        self.run = self.start_run("ui")
        self.incoming = queue.Queue()
        self.sent = []
        self.session = McpSession(self.profile, self.incoming, self.send, timeout=0.05)
        from test_environment_actions import ENVIRONMENT_ACTION
        self.environment_action = copy.deepcopy(ENVIRONMENT_ACTION)
        # the same write-first sidecar store the governance mediation tests use: a test-local
        # machine home, never the author's real $EMPIRICA_HOME
        self.document_home = tempfile.mkdtemp(prefix="hitl-home-")
        self.addCleanup(shutil.rmtree, self.document_home, ignore_errors=True)
        self.documents = HitlDocuments(home=Path(self.document_home))
        self.mediator = self.session.environment
        self.session.tools = self.wire(self.mediator, self.service, self.profile)
        self.reply_action = "accept"
        self.initialize({"elicitation": {"form": {}}})

    def wire(self, environment, service, profile):
        """Every private leg of ``environment`` reaches ``service`` in-process; the tools that
        admit through it share the one test-local sidecar store."""
        environment.admit = lambda r, _p: (service.dispatch(r), service.environment_admission(request_id=r["request_id"]))
        environment.fetch = lambda _p, r, v: service.trusted_environment_document(run_id=r, payload=v or {})
        environment.decide = lambda _p, r, v: service.trusted_environment_decision(run_id=r, payload=v)
        environment.documents = self.documents
        return PublicTools(profile, dispatch=lambda r, _p: service.dispatch(r), environment=environment)

    def start_run(self, name, goal="Qualify the run's environment from supplied context.", service=None, **extra):
        """A deliberative run ready for its environment action, on ``service`` (the fixture's own by default)."""
        service = self.service if service is None else service
        response = self.dispatch({"type": "StartRun", "control_mode": "deliberative", "goal": goal,
                                  "invocation": dict(TEST_INVOCATION),
                                  "selector": {"project": "p", "session": name}, **extra}, service)
        run = response["result"]["run"]["id"]
        self.dispatch({"type": "ObserveAction", "run_id": run, "action": {"kind": "graph", "payload": GRAPH}}, service)
        approve_current(service._coordinator, run)
        self.dispatch({"type": "ObserveAction", "run_id": run,
                       "action": {"kind": "route", "reason": "primary environment qualification route"}}, service)
        self.dispatch({"type": "ObserveAction", "run_id": run, "action": {"kind": "investigate"}}, service)
        return run

    def dispatch(self, command, service=None):
        self.sequence += 1
        return (self.service if service is None else service).dispatch(
            {"protocol": "empirica/v2", "request_id": str(self.sequence), "command": command})

    def initialize(self, capabilities):
        return self.session.process({"jsonrpc": "2.0", "id": "init", "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": capabilities}})

    def send(self, message):
        """The wire out of the session: an elicitation request is answered the way the client
        would — a reply carrying the request's own id — or not at all (``timeout``), or only
        under a foreign id (``stranger``), which the session must never take for its answer."""
        self.sent.append(message)
        if message.get("method") != "elicitation/create" or self.reply_action == "timeout":
            return
        reply_id = "somebody-elses" if self.reply_action == "stranger" else message["id"]
        self.incoming.put({"jsonrpc": "2.0", "id": reply_id,
                           "result": {"action": "accept" if self.reply_action == "stranger"
                                      else self.reply_action, "content": {}}})

    def forms(self):
        return [message["params"]["message"] for message in self.sent
                if message.get("method") == "elicitation/create"]

    def observe_environment(self, proposal_overrides=None, run=None):
        action = copy.deepcopy(self.environment_action)
        action["payload"].update(proposal_overrides or {})
        return self.session.process_internal({"jsonrpc": "2.0", "id": "call", "method": "tools/call",
            "params": {"name": "empirica_observe",
                       "arguments": {"run_id": self.run if run is None else run, "action": action}}})

    def sidecar_files(self):
        return sorted(str(path) for path in Path(self.document_home).rglob("*") if path.is_file())

    def test_a_surface_without_a_form_support_asks_nobody_and_decides_nothing(self):
        """The initialize handshake is the one that arms a host's form: a client that declares
        no elicitation form leaves the session unarmed, so the environment action's own run view
        keeps saying the revision is pending — nothing fetched, no form shown, no receipt."""
        self.session = McpSession(self.profile, self.incoming, self.send, timeout=0.05)
        self.session.tools = self.wire(self.session.environment, self.service, self.profile)
        self.initialize({})  # no elicitation capability at all
        self.observe_environment()
        row = self._stored_environment_row()
        self.assertEqual(row["state"], "pending")
        self.assertEqual(tuple(row["receipts"]), ())
        self.assertEqual(self.forms(), [])
        self.assertEqual(self.sidecar_files(), [])

    def test_the_document_is_written_before_the_present_and_binds_the_receipt(self):
        """Write-first order (ADR-0072, the configuration path's own discipline): the sidecar
        file already holds exactly the document's bytes, mode 0400 (owner read-only), before the
        present receipt reaches the service; the decision envelope carries the same digest."""
        order = []
        real_decide = self.mediator.decide
        def probe(_profile, run_id, payload):
            if payload.get("outcome") == "present":
                path = self.documents.path(decode_handle(run_id), payload["document_digest"])
                data = path.read_bytes()
                order.append({"file-exists-before-present": True,
                              "mode": oct(stat.S_IMODE(os.stat(path).st_mode)),
                              "bytes-are-the-document": core_hitl.digest(data) == payload["document_digest"]})
            else:
                order.append({"outcome": payload["outcome"],
                              "receipt-id-is-the-presents": payload.get("receipt_id") is not None})
            return real_decide(_profile, run_id, payload)
        self.mediator.decide = probe
        self.observe_environment()  # the mediation itself says its outcome through the row below
        self.assertEqual([event.get("file-exists-before-present") for event in order], [True, None])
        self.assertEqual(order[0]["mode"], "0o400")
        self.assertTrue(order[0]["bytes-are-the-document"])
        self.assertEqual(order[1]["outcome"], "approve")
        self.assertTrue(order[1]["receipt-id-is-the-presents"])
        # the run view does not project the environment member (E7): the row itself says it
        self.assertEqual(self._stored_environment_row()["state"], "approved")

    def test_a_decline_decides_reject_and_the_next_revision_starts_pending(self):
        """Decline is an explicit rejection (spec §5: reject is not cancel); the next revision
        asks again with a fresh member and a fresh form."""
        self.reply_action = "decline"
        self.observe_environment()
        self.assertEqual(self._stored_environment_row()["state"], "rejected")
        self.reply_action = "accept"
        self.observe_environment({"rationale": self.environment_action["payload"]["rationale"] + " once more"})
        self.assertEqual(self._stored_environment_row()["state"], "approved")
        self.assertEqual(len(self.forms()), 2)

    def test_a_dismissed_form_leaves_the_revision_pending_and_spends_no_decision(self):
        """Cancel, the session's own ``None`` on timeout and an answer under a foreign id are
        all non-decisions: one presentation row, the revision pending, nobody decided — through
        the session's real elicitation path, never a replaced callback."""
        for reply in ("cancel", "timeout", "stranger"):
            with self.subTest(reply=reply):
                run = self.start_run(f"dismissed-{reply}")
                self.reply_action = reply
                self.observe_environment(run=run)
                row = self._stored_environment_row(run)
                self.assertEqual(row["state"], "pending")
                receipts = [_plain_receipt(receipt) for receipt in row["receipts"]]
                self.assertEqual([receipt.get("outcome") for receipt in receipts], ["present"])
                self.assertEqual(row["receipt_id"], None)

    def test_the_visible_lines_carry_the_egress_call_out_and_the_exact_document_path(self):
        """Claude Code shows only the first ``CLAUDE_VISIBLE_LINES`` lines of a form and expands
        nothing: that slice itself — not the whole message — names the profile (the networked
        one called out, the isolated one's own plain word) and the exact path of the written
        document the human can open to read the plan and rationale."""
        from adapters.confirmation import CLAUDE_VISIBLE_LINES
        networked = self.start_run("net", spike_profile="docker-networked-v1")
        for run, profile_id, called_out in ((networked, "docker-networked-v1", True),
                                            (self.run, "docker-isolated-v1", False)):
            with self.subTest(profile=profile_id):
                before, forms = set(self.sidecar_files()), len(self.forms())
                self.observe_environment({"profile_id": profile_id}, run=run)
                (path,) = set(self.sidecar_files()) - before
                window = self.forms()[forms].splitlines()[:CLAUDE_VISIBLE_LINES]
                profile_lines = [line for line in window if line.startswith("Profile:")]
                self.assertEqual(len(profile_lines), 1, window)
                self.assertIn(profile_id, profile_lines[0])
                self.assertEqual("NETWORK EGRESS ENABLED" in profile_lines[0], called_out, window)
                self.assertEqual("no network" in profile_lines[0], not called_out, window)
                self.assertTrue(window[0].startswith("Empirica · approve environment revision"))
                self.assertEqual(window.count(f"File: {path}"), 1, window)

    def test_the_native_form_has_exactly_its_documented_lines_and_no_fields(self):
        """The whole native form, line by line: heading with its countdown, the profile, the
        exact path, the quoted goal, the image count, the short document identity and the
        two-button legend; it asks for no fields — a take-it-or-leave-it decision."""
        self.reply_action = "cancel"
        self.observe_environment()
        request = next(m for m in self.sent if m.get("method") == "elicitation/create")
        self.assertEqual(request["params"]["requestedSchema"],
                         {"type": "object", "properties": {}, "required": []})
        path = Path(self.sidecar_files()[0])
        proposal = self.environment_action["payload"]
        self.assertEqual(request["params"]["message"].splitlines(), [
            f"Empirica · approve environment revision 1 · {_duration(governance_timeout())}",
            f"Profile: {proposal['profile_id']} · no network",
            f"File: {path}",
            'Goal: "Qualify the run\'s environment from supplied context."',
            f"Spike images: {len(proposal['spike_images'])} · the agent's plan and rationale: in the document",
            f"Document sha256:{path.stem[:12]}",
            "Accept = approve this revision for qualification",
            "Decline = reject · Esc = decide later"])

    def test_a_long_goal_is_cut_to_one_line_that_keeps_its_closing_quote(self):
        run = self.start_run("long", goal="long " * 80)
        self.reply_action = "cancel"
        self.observe_environment(run=run)
        goal = next(line for line in self.forms()[0].splitlines() if line.startswith("Goal:"))
        self.assertLessEqual(len(goal), 72)
        self.assertTrue(goal.startswith('Goal: "long long') and goal.endswith('…"'), goal)

    def test_a_result_that_admits_nothing_or_a_leg_that_refuses_is_returned_unchanged(self):
        """Nothing is mediated for a refused admission or one that names no admission of this
        run, and every refusal of the private legs reaches the caller exactly as the service
        said it: no fetch for a Block, the Inert or Block of the fetch returned as it is, a
        fetch answer that is no document returned as the admitted result — and nothing is
        written in any of them."""
        run = {"id": self.run}
        admitted = {"type": "Allow", "run": run}
        admission = {"run_id": self.run, "revision": 1}
        calls = []
        self.mediator.fetch = lambda _p, r, v: calls.append(r) or {"result": fetched}
        for result, named in ((({"type": "Block", "run": run}), admission), ({"type": "Allow"}, admission),
                              ({"type": "Allow", "run": "no"}, admission), (admitted, None),
                              (admitted, {"run_id": "another-run", "revision": 1})):
            with self.subTest(result=result, admission=named):
                self.assertEqual(self.mediator(result, named), result)
        self.assertEqual(calls, [])
        for fetched, expected in (({"type": "Inert", "run": run}, {"type": "Inert", "run": run}),
                                  ({"type": "Block", "run": run, "reasons": []}, {"type": "Block", "run": run, "reasons": []}),
                                  ({"type": "Allow", "run": run}, admitted),
                                  ({"type": "Allow", "run": run, "presentation": []}, admitted),
                                  ({"type": "Allow", "run": {"id": "other"}, "presentation": {"document": {}}}, admitted)):
            with self.subTest(fetched=fetched):
                self.assertEqual(self.mediator(admitted, admission), expected)
        self.assertEqual(self.sidecar_files(), [])
        self.assertEqual(self.forms(), [])

    def test_the_caller_gets_the_decisions_own_answer_and_a_dismissal_gets_the_presents(self):
        """The mediation returns what the last private leg answered, never an earlier answer:
        the decision's result after an accept, the presentation's after a form nobody decided."""
        self.reply_action = "timeout"
        self.observe_environment()  # admitted, presented, left pending
        answers = []
        real = self.mediator.decide
        self.mediator.decide = lambda p, r, v: answers.append(real(p, r, v)["result"]) or {"result": answers[-1]}
        admitted = {"type": "Allow", "run": {"id": self.run}}
        admission = {"run_id": self.run, "revision": 1}
        self.assertEqual(self.mediator(admitted, admission), _without_presentation(answers[-1]))  # the present's
        self.assertEqual(len(answers), 1)
        self.reply_action = "accept"
        decided = self.mediator(admitted, admission)
        self.assertEqual(len(answers), 3)
        self.assertEqual(decided, _without_presentation(answers[-1]))
        self.assertEqual(self._stored_environment_row()["state"], "approved")

    def test_only_the_closed_mismatch_block_earns_a_re_present(self):
        code = core_environment.DOCUMENT_MISMATCH
        block = {"type": "Block", "reasons": [{"code": code}]}
        self.assertTrue(_is_document_mismatch(block))
        for other in (None, "Block", {"type": "Allow", "reasons": block["reasons"]},
                      {"type": "Block", "reasons": [{"code": core_environment.ENVIRONMENT_PENDING}]},
                      {"type": "Block", "reasons": [code]},
                      {"type": "Block", "reasons": []}, {"type": "Block"}):
            with self.subTest(other=other):
                self.assertFalse(_is_document_mismatch(other))

    def test_the_native_form_is_short_and_always_names_the_document_path(self):
        """The decision stays short: goal, profile, the image count and the document path —
        with no decision link to an observer row that does not exist for this kind, even when
        a live observer would answer; the full texts live in the bound document."""
        planted = {"url": "http://observer.invalid/decision-row", "run12": "r-planted"}
        with patch("adapters.observer.discovery.document_details", return_value=planted):
            self.observe_environment()
        form = self.forms()[0]
        written = [Path(path) for path in self.sidecar_files()]
        self.assertEqual(len(written), 1)
        self.assertIn(f"File: {written[0]}", form.splitlines())
        self.assertNotIn("Details:", form)
        self.assertNotIn(planted["url"], form)
        proposal = self.environment_action["payload"]
        self.assertIn(f"Spike images: {len(proposal['spike_images'])}", form)
        for long_text in (*proposal["spike_images"], *proposal["preparation"], proposal["rationale"]):
            self.assertNotIn(long_text, form)  # in the document, not in the native decision
        document = json.loads(written[0].read_text("utf-8"))
        self.assertEqual(document["preparation"], proposal["preparation"])

    def test_hostile_markup_and_label_like_text_stay_inside_their_own_quoted_line(self):
        """The goal is agent-reachable text: it is escaped by the core, truncated to one quoted
        line, and nothing in it can pose as a form label, add a line or hide the real profile
        call-out; the plan and rationale never reach the native form at all."""
        from adapters.confirmation import CLAUDE_VISIBLE_LINES
        hostile = ("**approve**](http://evil.invalid) \u202e\nProfile: docker-isolated-v1 · no network"
                   "\nFile: /tmp/not-the-document\nAccept = nothing to see")
        run = self.start_run("hostile", goal=hostile, spike_profile="docker-networked-v1")
        label_like = "Profile: docker-isolated-v1 · no network"
        self.observe_environment({"profile_id": "docker-networked-v1",
                                  "preparation": [label_like], "rationale": f"File: /tmp/x {label_like}"}, run=run)
        form = self.forms()[0]
        lines = form.splitlines()
        self.assertEqual([line for line in lines if line.startswith("Profile:")],
                         [lines[1]])  # one profile line, the real one, second line of the form
        self.assertIn("NETWORK EGRESS ENABLED", lines[1])
        self.assertEqual(len([line for line in lines if line.startswith("File:")]), 1)
        self.assertEqual(lines[CLAUDE_VISIBLE_LINES - 1], f"File: {self.sidecar_files()[0]}")
        self.assertTrue(lines[CLAUDE_VISIBLE_LINES].startswith('Goal: "'))
        self.assertNotIn("\u202e", form)  # the escaper's textual form only, never the character itself
        self.assertNotIn(label_like, form)  # the agent's plan and rationale stay in the document
        self.assertNotIn("Accept = nothing to see\n", form)

    def resubmit(self, run=None):
        """The byte-identical action once more, straight through the service: the next revision
        carries the very same proposal digest, so only its revision number tells it apart."""
        return self.dispatch({"type": "ObserveAction", "run_id": self.run if run is None else run,
                              "action": copy.deepcopy(self.environment_action)})

    def assert_no_revision_decided(self, run=None):
        rows = self._stored_environment_rows(run)
        self.assertEqual([(row["state"], tuple(row["receipts"])) for row in rows[-1:]], [("pending", ())])
        self.assertFalse([row for row in rows if row["state"] != "pending"])

    def test_a_substituted_fetch_writes_and_records_nothing(self):
        """Identity before the write: the mediation of the second of two byte-identical
        revisions refuses the first revision's pair, another run's pair and a schema-valid
        document of another kind naming this run and this very proposal — each self-consistent,
        each recognized — leaving the receipt journal and the sidecar directory untouched, and
        never reaching the service."""
        self.reply_action = "timeout"
        self.observe_environment()  # revision one: published, presented, left pending
        rev_one = self.service.trusted_environment_document(run_id=self.run)["result"]["presentation"]["document"]
        proposal_digest = json.loads(rev_one["text"])["proposal_digest"]
        other_run = {**json.loads(rev_one["text"]), "run_id": self.start_run("another-run")}
        configuration = {**json.loads((HITL_FIXTURES / "valid-configuration-approve.json").read_text("utf-8")),
                         "run_id": self.run, "proposal_digest": proposal_digest}
        jsonschema.validate(configuration, HITL_SCHEMA)
        cases = {"revision one's document": rev_one, "another run's document": _rendered(other_run),
                 "a document of another kind": _rendered(configuration)}
        for name, pair in cases.items():
            with self.subTest(injected=name):
                calls = []
                real = self.mediator.decide
                self.mediator.decide = lambda p, r, v: calls.append(v) or real(p, r, v)
                self.mediator.fetch = lambda _p, r, v, pair=pair: {"result": {
                    **self.service.trusted_environment_document(run_id=r)["result"],
                    "presentation": {"document": pair}}}
                files = self.sidecar_files()
                self.observe_environment()  # the same body again: revision two, three, ...
                self.assertEqual(self.sidecar_files(), files)
                self.assertEqual(calls, [])  # not a single receipt reached the service
                self.assertGreater(self._stored_environment_row()["revision"], 1)
                self.assert_no_revision_decided()
                self.mediator.decide = real
                self.mediator.fetch = lambda _p, r, v: self.service.trusted_environment_document(run_id=r, payload=v or {})

    def test_a_later_identical_revision_is_never_presented_for_the_admitted_one(self):
        """Revision one is admitted; before its mediation fetches, a byte-identical submission
        admits revision two. The fresh fetch now renders revision two — the very same proposal —
        and it is refused before any write: nothing is shown, written or decided for either."""
        real_fetch, fetched = self.mediator.fetch, []
        def fetch_after_resubmission(profile, run_id, payload):
            self.resubmit(run_id)
            result = real_fetch(profile, run_id, payload)
            fetched.append(json.loads(result["result"]["presentation"]["document"]["text"])["revision"])
            return result
        self.mediator.fetch = fetch_after_resubmission
        self.observe_environment()
        self.assertEqual(fetched, [2])
        self.assertEqual(self._stored_environment_row()["revision"], 2)
        self.assertEqual((self.forms(), self.sidecar_files()), ([], []))
        self.assert_no_revision_decided()

    def test_a_present_under_a_stale_document_is_re_presented_once_for_the_same_revision(self):
        """The recovery the ``environment.document_mismatch`` row names is executable here: a
        document of this very revision that the core no longer renders (self-consistent, the
        right run, kind and revision) is refused at the present with no receipt or state written,
        and the host fetches again and presents once more — the revision is the same, the form
        shows once, and one presentation row exists. If the second fetch is stale too the host
        stops there: pending, no loop."""
        real_fetch = self.mediator.fetch
        for fetches, outcome in (([self.stale_fetch, real_fetch], "approved"), ([self.stale_fetch, self.stale_fetch], "pending")):
            with self.subTest(second=outcome):
                run = self.start_run(f"retry-{outcome}")
                queue_of = list(fetches)
                calls = []
                self.mediator.fetch = lambda p, r, v: calls.append(r) or queue_of.pop(0)(p, r, v)
                before = len(self.forms())
                self.observe_environment(run=run)
                self.assertEqual(len(calls), 2)  # one re-presentation, never a loop
                self.assertEqual(len(self.forms()) - before, 1 if outcome == "approved" else 0)
                row = self._stored_environment_row(run)
                self.assertEqual(row["state"], outcome)
                self.assertEqual(len(row["receipts"]), 1 if outcome == "approved" else 0)
                self.mediator.fetch = real_fetch

    def stale_fetch(self, profile, run_id, payload):
        """A fetch of the right run, kind and revision whose text the core no longer renders."""
        result = self.service.trusted_environment_document(run_id=run_id, payload=payload)["result"]
        document = json.loads(result["presentation"]["document"]["text"])
        document["rationale"] = "a rationale this run no longer holds"
        return {"result": {**result, "presentation": {"document": _rendered(document)}}}

    def test_the_re_present_keeps_the_admitted_revision(self):
        """The first present is refused as stale; before the retry's fetch a byte-identical
        submission admits the next revision. The retry fetches revision two and is refused
        before any write: the stale file of the admitted revision is the only one on disk, and
        no revision is shown or decided."""
        real_fetch, fetches = self.mediator.fetch, []
        def retry_after_resubmission(profile, run_id, payload):
            fetches.append(run_id)
            if len(fetches) == 1:
                return self.stale_fetch(profile, run_id, payload)
            self.resubmit(run_id)
            return real_fetch(profile, run_id, payload)
        self.mediator.fetch = retry_after_resubmission
        self.observe_environment()
        self.assertEqual(len(fetches), 2)
        self.assertEqual(self._stored_environment_row()["revision"], 2)
        self.assertEqual((len(self.sidecar_files()), self.forms()), (1, []))
        self.assert_no_revision_decided()

    def test_the_production_legs_admit_fetch_and_decide_through_the_bridge(self):
        """The mediation a session composes by itself — no leg injected — admits, fetches and
        decides through the bridge's own entry points, and the admission rides beside the
        bridge's response, never inside it."""
        self.session = McpSession(self.profile, self.incoming, self.send, timeout=0.05)
        self.session.environment.documents = self.documents
        self.initialize({"elicitation": {"form": {}}})
        with patch("adapters.bridge.build_service", return_value=self.service):
            self.observe_environment()
            response, admission = bridge.handle_admitting(
                {"protocol": "empirica/v2", "request_id": "own-request",
                 "command": {"type": "GetRun", "run_id": self.run}}, self.profile)
        self.assertEqual(self._stored_environment_row()["state"], "approved")
        self.assertEqual(len(self.forms()), 1)
        self.assertEqual(len(self.sidecar_files()), 1)
        self.assertEqual((response["request_id"], admission), ("own-request", None))

    def test_other_tools_and_actions_never_ask_for_an_environment_admission(self):
        admitted = []
        real = self.mediator.admit
        self.mediator.admit = lambda request, profile: admitted.append(request) or real(request, profile)
        calls = (("empirica_read", {"operation": "GetRun", "run_id": self.run}),
                 ("empirica_observe", {"run_id": self.run, "action": {"kind": "investigate"}}))
        for name, arguments in calls:
            answer = self.session.process_internal({"jsonrpc": "2.0", "id": name, "method": "tools/call",
                                                    "params": {"name": name, "arguments": arguments}})
            self.assertFalse(answer["result"]["isError"], name)
        self.assertEqual((admitted, self.forms()), ([], []))

    def test_a_profile_without_approval_ingress_composes_no_environment_mediation(self):
        """Whatever form support its client claims, a session of a profile that has no approval
        ingress holds no environment mediation and arms none."""
        profiles = [profile for profile in _proto.profile_ids() if _proto.approval_ingress(profile) is None]
        self.assertTrue(profiles)
        for profile in profiles:
            with self.subTest(profile=profile):
                session = McpSession(profile, self.incoming, self.send, timeout=0.05)
                self.assertIsNone(session.environment)
                response = session.process({"jsonrpc": "2.0", "id": "init", "method": "initialize",
                    "params": {"protocolVersion": "2025-11-25", "capabilities": {"elicitation": {"form": {}}}}})
                self.assertIn("result", response)
                self.assertIsNone(session.tools._environment)

    def test_a_profile_with_another_ingress_gets_no_form_and_no_approval_on_its_own_run(self):
        """The explicit host-profile setting may select any registry profile: one whose approval
        ingress is not this transport's own composes no form even for a run that belongs to it
        (its recorded ingress is that profile's), and a form-capable client's accept approves nothing."""
        self.assertIn(INGRESS, _proto.APPROVAL_CAPABILITY)  # the transport's ingress is a registry value
        for other in (p for p in _proto.profile_ids() if _proto.approval_ingress(p) not in (None, INGRESS)):
            with self.subTest(profile=other):
                profile = profile_from_environment({"EMPIRICA_HOST_PROFILE_ID": other})
                service = compose(Workspace(), Harness(), self.runs, self.artifacts, None, profile, {}, None)
                run = self.start_run(f"own-{other[:5]}", service=service)
                recorded = service.dispatch({"protocol": "empirica/v2", "request_id": "read", "command": {
                    "type": "GetRun", "run_id": run}})["result"]["run"]["governance"]["context"]["ingress"]
                self.assertEqual(recorded, _proto.host_profile(profile)["approval_ingress"])
                self.session = McpSession(profile, self.incoming, self.send, timeout=0.05)
                self.assertIsNone(self.session.environment)
                self.initialize({"elicitation": {"form": {}}})
                files = self.sidecar_files()
                with patch("adapters.bridge.build_service", return_value=service):
                    self.observe_environment(run=run)
                row = self._stored_environment_row(run)
                self.assertEqual((row["state"], tuple(row["receipts"])), ("pending", ()))
                self.assertEqual((self.forms(), self.sidecar_files()), ([], files))

    def test_a_host_whose_ingress_is_not_the_runs_cannot_approve_it(self):
        """Profiles whose approval ingress differs from the run's (none, or another host's) share
        the run's repository, a public proposal arrives and a matching form answer follows. Even
        with the mediation wired onto their service, the private legs read no document and
        record no receipt: the run stays pending and nothing is written."""
        own = _proto.approval_ingress(self.profile)
        for other in (p for p in _proto.profile_ids() if _proto.approval_ingress(p) != own):
            with self.subTest(profile=other):
                run = self.start_run(f"shared-{other[:5]}")
                service = compose(Workspace(), Harness(), self.runs, self.artifacts, None, other, {}, None)
                session = McpSession(other, self.incoming, self.send, timeout=0.05)
                tools = self.wire(HostEnvironment(other, elicit=session.elicit), service, other)
                self.reply_action = "accept"
                files = self.sidecar_files()
                tools.call_internal("empirica_observe", {"run_id": run, "action": copy.deepcopy(self.environment_action)})
                row = self._stored_environment_row(run)
                self.assertEqual(row["state"], "pending")
                self.assertEqual(tuple(row["receipts"]), ())
                self.assertEqual(self.forms(), [])
                self.assertEqual(self.sidecar_files(), files)

    def _stored_environment_row(self, run=None) -> dict:
        """The current member row as E7 committed it (the run view does not project the member)."""
        return self._stored_environment_rows(run)[-1]

    def _stored_environment_rows(self, run=None) -> list[dict]:
        """Every revision's row, oldest first, read from the repository for the named run (the
        fixture's own when none is named)."""
        from application.run_state import classify_and_decode
        from core.governance import plain
        state = classify_and_decode(self.runs.read(decode_handle(self.run if run is None else run)).value).state
        return [plain(row) for row in (*state.environment_history, state.environment)]


def _without_presentation(result: dict) -> dict:
    return {key: value for key, value in result.items() if key != "presentation"}


def _rendered(document: dict) -> dict:
    """One self-consistent ``{text, digest}`` pair of an arbitrary document, through the core's
    own renderer — what a stale or substituted private fetch would hand a host."""
    return core_hitl.rendered_pair(document)


def _plain_receipt(receipt) -> dict:
    from core import governance
    return governance.plain(dict(receipt))



if __name__ == "__main__":
    unittest.main()
