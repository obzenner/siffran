#!/usr/bin/env python3
"""Governance real service/manifest/CAS regressions, not a policy-only scaffold."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from application.v2 import compose
from application.run_state import classify_and_decode
from application.snapshot import traverse_history
from core.governance import identity_relation
from test_d7_transactions import Runs, Artifacts, Workspace, Harness

PROFILE = "pi@0.84.1+pi-subagents@0.50.0"
AUTHOR = {"provider_id": "anthropic", "model_id": "claude-sonnet-4-6"}
AUDITOR = {"provider_id": "anthropic", "model_id": "claude-opus-4-6"}
GRAPH = {"root": "C0", "claims": [{"id": "C0", "text": "supplied uncertainty", "gating": True,
                                   "kind": "ordinary"}], "edges": []}
CONTEXT = {"inventory": {"members": [AUTHOR, AUDITOR], "source": "pi_registry",
                          "complete": True, "authorized": True}, "author": AUTHOR, "ingress": "pi_ui"}


class GovernanceServiceTests(unittest.TestCase):
    def setUp(self):
        self.runs, self.artifacts = Runs(), Artifacts()
        self.service = compose(Workspace(), Harness(), self.runs, self.artifacts, None, PROFILE, {}, None)
        self.run_id = self.request({"type": "StartRun", "goal": "governed task",
                                    "selector": {"project": "p", "session": "s"}})["run"]["id"]

    def request(self, command):
        return self.service.dispatch({"protocol": "empirica/v2", "request_id": "test", "command": command})["result"]

    def action(self, kind, **kwargs):
        return self.request({"type": "ObserveAction", "run_id": self.run_id,
                             "action": {"kind": kind, **kwargs}})

    def view(self):
        return self.request({"type": "GetRun", "run_id": self.run_id})["run"]

    def decision(self, receipt="receipt", outcome="approve", *, reserve=True, **kwargs):
        """Explicit simulated-host reservation before constructing a final UI decision."""
        g = self.view()["governance"]
        payload = {"run_id": self.run_id, "receipt_id": receipt + "-" + str(g["plan_revision"]), "proposal_digest": g["proposal_digest"],
                   "plan_revision": g["plan_revision"], "approval_kind": "host_ui", "outcome": outcome, **kwargs}
        if reserve and outcome != "present" and payload["approval_kind"] == "host_ui" and g["state"] != "approved" and not g["prompt_error"]:
            present = {k: v for k, v in payload.items() if k not in {"amendment", "change_request"}}
            present["outcome"] = "present"
            self.assertIn(self.admit(present)["type"], {"Allow", "Inert"})
        return payload

    def admit(self, payload):
        return self.service.trusted_governance_decision(run_id=self.run_id, payload=payload)["result"]

    def prepare(self):
        self.assertEqual(self.action("route", reason="supplied context")["type"], "Allow")
        self.assertEqual(self.action("graph", payload=copy.deepcopy(GRAPH))["type"], "Allow")
        self.assertEqual(self.action("configure_run", auditor=AUDITOR)["type"], "Allow")
        result = self.service.trusted_governance_context(run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
        self.assertEqual(result["result"]["type"], "Allow", result)

    def test_graphless_configure_and_private_present_are_effect_free_blocks(self):
        initial = self.view()
        self.assertEqual([row["id"] for row in initial["obligations"]["active"]],
                         ["obligation.route", "obligation.graph"])
        self.assertEqual(initial["next_actions"], ["route.record", "graph.record"])
        self.assertFalse(initial["governance"]["request_ready"])
        self.assertFalse(initial["governance"]["display_ready"])
        before = copy.deepcopy(self.runs.data)
        proposed = self.action("configure_run", auditor=AUDITOR)
        self.assertEqual(proposed["type"], "Block")
        self.assertEqual(proposed["reasons"][0]["code"], "graph.missing")
        self.assertEqual(proposed["reasons"][0]["next_actions"], ["graph.record"])
        self.assertEqual(self.runs.data, before)

        g = self.view()["governance"]
        presented = {"run_id": self.run_id, "receipt_id": "graphless-present",
                     "proposal_digest": g["proposal_digest"],
                     "plan_revision": g["plan_revision"], "approval_kind": "host_ui",
                     "outcome": "present"}
        before = copy.deepcopy(self.runs.data)
        blocked = self.admit(presented)
        self.assertEqual(blocked["type"], "Block")
        self.assertEqual(blocked["reasons"][0]["code"], "graph.missing")
        self.assertEqual(self.runs.data, before)
        self.assertEqual(self.view()["governance"]["interactions_remaining"],
                         {"proposal": 3, "total": 128})

    def test_bootstrap_contract_examples_have_real_postconditions(self):
        from application import protocol
        examples = protocol._PUBLIC_CONTRACT["bootstrap"]["actions"]
        self.assertEqual(self.action(**examples["route"]["example"])["type"], "Allow")
        self.assertIsNotNone(self.view()["obligations"]["active"][0]["status"])
        self.assertEqual(self.action(**examples["graph"]["example"])["type"], "Allow")
        self.assertEqual(self.view()["governance"]["scope"]["root"], "C0")
        before = self.view()["governance"]["budgets"]["max_passes"]
        self.assertEqual(self.action(**examples["configure_run"]["example"])["type"], "Allow")
        governed = self.view()["governance"]
        self.assertEqual(governed["proposal"]["budgets"]["max_passes"], 5)
        self.assertEqual(governed["budgets"]["max_passes"], before)
        self.service.trusted_governance_context(run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
        self.action("configure_run", auditor=AUDITOR)
        self.assertEqual(self.admit(self.decision())["type"], "Allow")
        self.assertEqual(self.action(**examples["investigate"]["example"])["type"], "Allow")
        self.assertEqual(self.view()["obligations"]["active"][3]["status"], "satisfied")

    def test_bootstrap_graphless_convergence_is_preparation_not_human_wait(self):
        self.action("route", reason="supplied context")
        result = self.request({"type": "EvaluateRun", "run_id": self.run_id,
                               "intent": "report_convergence"})
        self.assertEqual(result["type"], "Block")
        self.assertEqual(result["reasons"][0]["code"], "graph.missing")

    def test_bootstrap_approved_before_route_does_not_advertise_investigation(self):
        self.assertEqual(self.action("graph", payload=copy.deepcopy(GRAPH))["type"], "Allow")
        self.action("configure_run", auditor=AUDITOR)
        self.service.trusted_governance_context(run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
        self.assertEqual(self.admit(self.decision())["type"], "Allow")
        self.assertEqual(self.view()["next_actions"], ["route.record"])
        self.assertEqual(self.action("investigate")["reasons"][0]["code"], "route.required")

    def test_bootstrap_refresh_and_capacity_have_distinct_recovery(self):
        self.action("graph", payload=copy.deepcopy(GRAPH))
        self.action("configure_run", auditor=AUDITOR)
        self.assertEqual(self.view()["next_actions"], ["route.record", "governance.propose"])
        unusable = copy.deepcopy(CONTEXT)
        unusable["inventory"]["members"] = []
        self.service.trusted_governance_context(run_id=self.run_id, payload=unusable)
        self.assertEqual(self.view()["next_actions"], ["route.record", "host.repair_context"])

    def test_bootstrap_terminal_run_has_no_preparation_actions(self):
        result = self.request({"type": "EvaluateRun", "run_id": self.run_id, "intent": "stop"})
        self.assertEqual(result["run"]["status"], "stopped_residual")
        self.assertEqual(result["run"]["next_actions"], [])
        self.assertFalse(result["run"]["governance"]["request_ready"])
        self.assertFalse(result["run"]["governance"]["display_ready"])

    def test_real_pending_approval_investigation_then_revision_revokes_all_paths(self):
        self.assertEqual(self.view()["governance"]["state"], "pending")
        self.prepare()
        self.assertEqual(self.action("investigate")["reasons"][0]["code"], "governance.approval_required")
        counters = self.view()["governance"]["budgets"]
        self.assertEqual(self.admit(self.decision())["type"], "Allow")
        self.assertEqual(self.action("investigate")["type"], "Allow")
        graph = copy.deepcopy(GRAPH)
        graph["claims"][0]["text"] = "revised scope"
        self.assertEqual(self.action("graph", payload=graph)["type"], "Allow")
        self.assertEqual(self.view()["governance"]["state"], "revision_pending")
        for kind, fields in [("investigate", {}), ("freeze", {}),
                             ("research", {"claim_id": "C0", "source_kind": "docs", "result": "supports",
                                           "payload": {"source_ref": "provided", "citation": "text"}}),
                             ("spike_request", {"claim_id": "C0", "command": "false", "dependent_files": ["f.py"]})]:
            result = self.action(kind, **fields)
            self.assertEqual(result["type"], "Block", result)
            self.assertEqual(result["reasons"][0]["code"], "governance.revision_required")
        self.assertEqual(self.view()["governance"]["budgets"], counters)
        decision = self.request({"type": "EvaluateRun", "run_id": self.run_id,
                                 "intent": "report_convergence"})
        self.assertEqual(decision["reasons"][0]["code"], "governance.revision_required")
        self.assertEqual(self.request({"type": "EvaluateRun", "run_id": self.run_id, "intent": "stop"})["type"], "Allow")

    def test_historical_graphless_final_receipt_remains_exactly_replayable(self):
        from dataclasses import replace
        from core import governance
        self.prepare()
        decision = self.decision("historical")
        self.assertEqual(self.admit(decision)["type"], "Allow")
        key = next(iter(self.runs.data))
        entry = self.runs.data[key]
        state = classify_and_decode(entry.value).state
        snapshot = self.service._coordinator._assemble(
            key, state, {"type": "GetRun", "run_id": self.run_id}, require_graph=False)
        governed = governance.revise(state.goal, None, state.governance)
        historical = replace(state, selected_graph_artifact_id=None, governance=governed)
        self.service._coordinator._commit(key, entry.revision, snapshot, historical, ())
        self.assertIsNone(self.view()["governance"]["scope"])
        self.assertEqual(self.admit(decision)["type"], "Inert")

    def test_private_exact_replay_conflict_stale_cross_run_and_cas(self):
        self.prepare()
        decision = self.decision()
        before = copy.deepcopy(self.runs.data)
        for changed in [{"run_id": "other"}, {"plan_revision": 900}, {"approval_kind": "auto"},
                        {"proposal_digest": "sha256:" + "a" * 64}]:
            self.assertEqual(self.admit({**decision, **changed})["type"], "Block")
            self.assertEqual(self.runs.data, before)
        self.runs.conflicts = 1
        self.assertEqual(self.admit(decision)["type"], "Allow")
        after = copy.deepcopy(self.runs.data)
        self.assertEqual(self.admit(decision)["type"], "Inert")
        self.assertEqual(self.admit({**decision, "outcome": "reject"})["type"], "Block")
        self.assertEqual(self.runs.data, after)
        key = next(iter(self.runs.data))
        decoded = classify_and_decode(self.runs.data[key].value)
        self.assertEqual(decoded.kind, "valid")
        traverse_history(decoded.state, self.artifacts.read(key))

    def test_feedback_replay_conflict_cross_run_and_staleness_never_overwrite_guidance(self):
        self.prepare()
        feedback = self.decision("feedback", "request_changes", change_request="  Retain this request.\n")
        before = copy.deepcopy(self.runs.data)
        for changed in ({"run_id": "other"}, {"plan_revision": 900},
                        {"proposal_digest": "sha256:" + "a" * 64}):
            self.assertEqual(self.admit({**feedback, **changed})["type"], "Block")
            self.assertEqual(self.runs.data, before)
        self.runs.conflicts = 1
        self.assertEqual(self.admit(feedback)["type"], "Allow")
        accepted = copy.deepcopy(self.runs.data)
        self.assertEqual(self.admit(feedback)["type"], "Inert")
        self.assertEqual(self.admit({**feedback, "change_request": "overwrite"})["type"], "Block")
        self.assertEqual(self.runs.data, accepted)
        stale = self.decision("stale-feedback", "request_changes", change_request="Do not store me.")
        graph = copy.deepcopy(GRAPH)
        graph["claims"][0]["text"] = "new proposal while the form is open"
        self.assertEqual(self.action("graph", payload=graph)["type"], "Allow")
        revised = copy.deepcopy(self.runs.data)
        self.assertEqual(self.admit(stale)["reasons"][0]["code"], "governance.stale_proposal")
        self.assertEqual(self.action("configure_run", change_request="forged")["type"], "Fault")
        self.assertEqual(self.runs.data, revised)
        self.assertEqual(self.view()["governance"]["change_request"]["text"], feedback["change_request"])

    def test_configuration_is_proposal_only_and_freeze_preserves_approval(self):
        self.prepare()
        self.admit(self.decision())
        before = self.view()["governance"]
        self.assertEqual(self.action("freeze")["type"], "Allow")
        self.assertEqual(self.view()["governance"]["approved_digest"], before["approved_digest"])
        self.action("configure_run", budgets={"max_passes": 12})
        g = self.view()["governance"]
        self.assertEqual(g["budgets"]["max_passes"], 8)
        self.assertEqual(g["proposal"]["budgets"]["max_passes"], 12)
        self.assertEqual(g["state"], "revision_pending")
        self.assertEqual(self.admit(self.decision("second"))["type"], "Allow")
        self.assertEqual(self.view()["governance"]["budgets"]["max_passes"], 12)

    def test_cancel_unknown_inventory_singleton_and_no_public_approval(self):
        self.prepare()
        self.assertEqual(self.action("governance_decision", payload=self.decision())["type"], "Fault")
        context = copy.deepcopy(CONTEXT)
        context["inventory"]["complete"] = False
        self.service.trusted_governance_context(run_id=self.run_id, payload=context)
        self.assertEqual(self.admit(self.decision())["reasons"][0]["code"], "governance.inventory_unknown")
        context["inventory"].update(complete=True, members=[AUTHOR])
        self.service.trusted_governance_context(run_id=self.run_id, payload=context)
        self.action("configure_run", auditor=AUTHOR)
        self.assertEqual(self.admit(self.decision())["reasons"][0]["code"], "audit.same_model")
        self.action("configure_run", allow_same_model=True)
        self.assertEqual(self.admit(self.decision())["type"], "Allow")

    def test_auto_explicit_no_ceiling_increase_and_bounded_revisions(self):
        self.run_id = self.request({"type": "StartRun", "goal": "auto task", "control_mode": "auto",
                                    "selector": {"project": "p", "session": "auto"}})["run"]["id"]
        self.prepare()
        self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")
        self.assertEqual(self.action("configure_run", budgets={"max_audit_spawns": 2})["reasons"][0]["code"], "governance.auto_ceiling")
        for i in range(8):
            graph = copy.deepcopy(GRAPH)
            graph["claims"][0]["text"] = str(i)
            self.assertEqual(self.action("graph", payload=graph)["type"], "Allow")
            self.assertEqual(self.admit(self.decision(str(i), approval_kind="auto"))["type"], "Allow")
        graph["claims"][0]["text"] = "ninth"
        self.assertEqual(self.action("graph", payload=graph)["reasons"][0]["code"], "governance.revision_limit")

    def test_change_request_lifecycle_persists_grants_nothing_and_fails_closed(self):
        self.prepare()
        g = self.view()["governance"]
        displayed = (g["plan_revision"], g["proposal_digest"])
        # Whitespace-only guidance is malformed, not a stored request.
        self.assertEqual(self.admit(self.decision("blank", "request_changes", change_request="  \n "))["type"], "Fault")
        self.assertIsNone(self.view()["governance"]["change_request"])
        # Exact text is stored against the displayed revision; the run stays pending and grants nothing.
        self.assertEqual(self.admit(self.decision("ask", "request_changes",
                                                  change_request="  split C0 into restore/replay  "))["type"], "Allow")
        g = self.view()["governance"]
        self.assertEqual(g["state"], "pending")
        self.assertEqual(g["change_request"], {"text": "  split C0 into restore/replay  ",
                                               "plan_revision": displayed[0], "proposal_digest": displayed[1]})
        self.assertEqual(self.action("investigate")["reasons"][0]["code"], "governance.approval_required")
        # Author graph and context revisions preserve the guidance; it survives a real restore.
        revised = copy.deepcopy(GRAPH)
        revised["claims"][0]["text"] = "restore/replay split"
        self.assertEqual(self.action("graph", payload=revised)["type"], "Allow")
        context = copy.deepcopy(CONTEXT)
        context["inventory"]["members"].append({"provider_id": "private", "model_id": "unknown"})
        self.service.trusted_governance_context(run_id=self.run_id, payload=context)
        g = self.view()["governance"]
        self.assertEqual(g["change_request"]["text"], "  split C0 into restore/replay  ")
        self.assertEqual(g["change_request"]["plan_revision"], displayed[0])
        self.assertGreater(g["plan_revision"], displayed[0])
        restored = self.request({"type": "RestoreRun", "run_id": self.run_id})
        self.assertEqual(restored["run"]["governance"]["change_request"], g["change_request"])
        # Combined feedback + numeric edit records guidance against the displayed digest, not the created one.
        proposal = copy.deepcopy(g["proposal"])
        proposal["budgets"]["max_passes"] = 6
        self.assertEqual(self.admit(self.decision("both", "request_changes", change_request="lower passes too",
            amendment={"graph": revised, "configuration": proposal}))["type"], "Allow")
        after = self.view()["governance"]
        self.assertEqual(after["proposal"]["budgets"]["max_passes"], 6)
        self.assertEqual(after["change_request"]["plan_revision"], g["plan_revision"])
        self.assertEqual(after["change_request"]["proposal_digest"], g["proposal_digest"])
        self.assertNotEqual(after["proposal_digest"], g["proposal_digest"])
        self.assertEqual(self.view()["governance"]["budgets"]["max_passes"], 8)
        # Reject clears guidance; a later approval of a fresh proposal stores none.
        self.assertEqual(self.admit(self.decision("no", "reject"))["type"], "Allow")
        self.assertIsNone(self.view()["governance"]["change_request"])
        self.assertEqual(self.view()["status"], "active")
        self.assertEqual(self.admit(self.decision("again", "request_changes", change_request="one more"))["type"], "Allow")
        self.assertEqual(self.admit(self.decision("yes"))["type"], "Allow")
        approved = self.view()["governance"]
        self.assertEqual(approved["state"], "approved")
        self.assertIsNone(approved["change_request"])
        self.assertEqual(self.view()["governance"]["budgets"]["max_passes"], 6)
        # Stored guidance claiming a future revision, blank text, or an approved state fails closed.
        raw = copy.deepcopy(next(iter(self.runs.data.values())).value)
        for corrupt in ({"text": "x", "plan_revision": raw["governance"]["plan_revision"] + 1,
                         "proposal_digest": approved["proposal_digest"]},
                        {"text": " ", "plan_revision": 0, "proposal_digest": approved["proposal_digest"]},
                        {"text": "x", "plan_revision": 0, "proposal_digest": approved["proposal_digest"]}):
            broken = copy.deepcopy(raw)
            broken["governance"]["change_request"] = corrupt
            self.assertEqual(classify_and_decode(broken).kind, "current_corrupt", corrupt)
        del raw["governance"]["change_request"]
        self.assertEqual(classify_and_decode(raw).kind, "current_corrupt")

    def test_auto_mode_never_stores_human_guidance(self):
        run = self.request({"type": "StartRun", "goal": "auto", "selector": {"project": "p", "session": "auto"},
                            "control_mode": "auto"})["run"]
        self.run_id = run["id"]
        self.assertEqual(self.action("graph", payload=copy.deepcopy(GRAPH))["type"], "Allow")
        self.service.trusted_governance_context(run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
        g = self.view()["governance"]
        payload = {"run_id": self.run_id, "receipt_id": "auto-ask", "proposal_digest": g["proposal_digest"],
                   "plan_revision": g["plan_revision"], "approval_kind": "auto", "outcome": "request_changes",
                   "change_request": "not from a human"}
        result = self.admit(payload)
        self.assertEqual(result["type"], "Block")
        self.assertEqual(result["reasons"][0]["code"], "governance.approval_unavailable")
        self.assertIsNone(self.view()["governance"]["change_request"])

    def test_honest_stop_without_graph_and_strict_old_state(self):
        self.assertEqual(self.request({"type": "EvaluateRun", "run_id": self.run_id, "intent": "stop"})["type"], "Allow")
        raw = copy.deepcopy(next(iter(self.runs.data.values())).value)
        del raw["governance"]
        self.assertEqual(classify_and_decode(raw).kind, "current_corrupt")

    def audit_ready(self, *, singleton=False, partial=False):
        from governance_setup import approve_current
        from adapters.audit_protocol import AuditProtocol
        self.prepare()
        approve_current(self.service._coordinator, self.run_id,
                        auditor=AUTHOR if singleton else AUDITOR, singleton=singleton)
        if partial:
            context = copy.deepcopy(CONTEXT)
            context["inventory"]["members"].append({"provider_id": "private", "model_id": "unknown"})
            self.service.trusted_governance_context(run_id=self.run_id, payload=context)
            self.assertEqual(self.admit(self.decision("partial"))["type"], "Allow")
        self.assertEqual(self.action("investigate")["type"], "Allow")
        self.assertEqual(self.action("research", claim_id="C0", source_kind="code", result="supports",
                                    payload={"source_ref": "supplied", "citation": "observed"})["type"], "Allow")
        c = self.service._coordinator
        protocol = AuditProtocol(PROFILE,
            dispatch=lambda r, _p: self.service.dispatch(r),
            child_event_ingress=lambda _p, r, ch, v: c.trusted_child_event(r, ch, v),
            attribution_ingress=lambda _p, r, v: c.trusted_attribution(r, v),
            verdict_ingress=lambda _p, r, ch, v: c.trusted_audit_verdict(r, ch, v),
            plan_ingress=lambda _p, r, ch: c.trusted_audit_plan(r, ch))
        plan = protocol.prepare(self.run_id, role_profile="empirica:empirica-auditor")
        protocol.observe_started(plan, "native-test")
        return protocol, plan

    def test_singleton_exception_real_bound_audit_converges_with_raw_alias_provenance(self):
        from adapters.audit_protocol import IdentityObservation
        from core.evaluation import audit_binding
        protocol, plan = self.audit_ready(singleton=True)
        protocol.observe_identities(plan, "native-test",
            author=IdentityObservation("anthropic", AUTHOR["model_id"], "host", "test-host"),
            auditor=IdentityObservation("bedrock", "eu.anthropic.claude-sonnet-4-6", "host", "test-host"))
        c = self.service._coordinator
        key = next(iter(self.runs.data))
        state = classify_and_decode(self.runs.data[key].value).state
        snapshot = c._assemble(key, state, {"type": "GetArgument", "run_id": self.run_id}, require_graph=True)
        verdict = {"verdict": "pass", "findings": ["bound singleton audit"], **audit_binding(snapshot)}
        self.assertTrue(protocol.observe_verdict(plan, "native-test", verdict))
        result = self.request({"type": "EvaluateRun", "run_id": self.run_id, "intent": "report_convergence"})
        self.assertTrue(result["converged"], result)
        self.assertEqual(result["run"]["governance"]["inventory_status"], "singleton")
        self.assertEqual(result["run"]["governance"]["context"]["inventory"]["source"], "pi_registry")
        history = traverse_history(classify_and_decode(self.runs.data[key].value).state,
                                   self.artifacts.read(key))
        self.assertTrue(any(a.get("model_id") == "eu.anthropic.claude-sonnet-4-6" for a in history))

    def test_partial_inventory_real_bound_different_model_audit_converges(self):
        from adapters.audit_protocol import IdentityObservation
        from core.evaluation import audit_binding
        protocol, plan = self.audit_ready(partial=True)
        protocol.observe_identities(plan, "native-test",
            author=IdentityObservation("anthropic", AUTHOR["model_id"], "host", "test-host"),
            auditor=IdentityObservation("bedrock", "eu.anthropic.claude-opus-4-6-v1", "host", "test-host"))
        c = self.service._coordinator
        key = next(iter(self.runs.data))
        state = classify_and_decode(self.runs.data[key].value).state
        snapshot = c._assemble(key, state, {"type": "GetArgument", "run_id": self.run_id}, require_graph=True)
        verdict = {"verdict": "pass", "findings": ["bound singleton audit"], **audit_binding(snapshot)}
        self.assertTrue(protocol.observe_verdict(plan, "native-test", verdict))
        result = self.request({"type": "EvaluateRun", "run_id": self.run_id, "intent": "report_convergence"})
        self.assertTrue(result["converged"], result)
        self.assertEqual(result["run"]["governance"]["inventory_status"], "partial")
        self.assertEqual(result["run"]["governance"]["context"]["inventory"]["source"], "pi_registry")
        history = traverse_history(classify_and_decode(self.runs.data[key].value).state,
                                   self.artifacts.read(key))
        self.assertTrue(any(a.get("model_id") == "eu.anthropic.claude-opus-4-6-v1" for a in history))

    def test_selected_observed_mismatch_revokes_and_cannot_admit_verdict(self):
        from adapters.audit_protocol import IdentityObservation, AuditProtocolError
        protocol, plan = self.audit_ready()
        before = self.view()["governance"]["budgets"]
        with self.assertRaises(AuditProtocolError):
            protocol.observe_identities(plan, "native-test",
                author=IdentityObservation("anthropic", AUTHOR["model_id"], "host", "test-host"),
                auditor=IdentityObservation("anthropic", AUTHOR["model_id"], "host", "test-host"))
        self.assertEqual(self.view()["governance"]["state"], "revision_pending")
        self.assertEqual(self.view()["governance"]["budgets"], before)
        protocol.observe_failure(plan, "native-test", "failed")
        self.assertEqual(self.view()["children"][0]["state"], "failed")
        self.assertEqual(self.view()["governance"]["budgets"], before)
        self.assertEqual(self.action("investigate")["reasons"][0]["code"], "governance.revision_required")
        self.assertEqual(self.request({"type": "EvaluateRun", "run_id": self.run_id,
                                      "intent": "stop"})["type"], "Allow")

    def test_inventory_change_and_canonical_reordering_and_strict_rejects(self):
        self.prepare()
        self.admit(self.decision())
        before = self.view()["governance"]
        reordered = copy.deepcopy(CONTEXT)
        reordered["inventory"]["members"].reverse()
        result = self.service.trusted_governance_context(run_id=self.run_id, payload=reordered)
        self.assertEqual(result["result"]["type"], "Inert")
        self.assertEqual(self.view()["governance"], before)
        persisted = copy.deepcopy(self.runs.data)
        self.assertEqual(self.admit({**self.decision("bad"), "extra": True})["type"], "Fault")
        self.assertEqual(self.runs.data, persisted)
        reordered["inventory"]["complete"] = False
        self.service.trusted_governance_context(run_id=self.run_id, payload=reordered)
        self.assertEqual(self.view()["governance"]["state"], "revision_pending")
        self.assertEqual(self.admit(self.decision("new"))["reasons"][0]["code"], "governance.inventory_unknown")

    def test_canonical_graph_order_preserves_consent_and_oversize_cannot_replace_scope(self):
        self.prepare()
        graph = copy.deepcopy(GRAPH)
        graph["claims"].append({"id": "C1", "text": "dependent uncertainty", "gating": True, "kind": "ordinary"})
        graph["edges"] = [{"from": "C0", "to": "C1", "type": "SupportedBy"}]
        self.assertEqual(self.action("graph", payload=graph)["type"], "Allow")
        self.assertEqual(self.admit(self.decision())["type"], "Allow")
        before = self.view()["governance"]
        graph["claims"].reverse()
        self.assertEqual(self.action("graph", payload=graph)["type"], "Allow")
        self.assertEqual(self.view()["governance"], before)
        persisted = copy.deepcopy(self.runs.data)
        graph["claims"][0]["text"] = "x" * 2049
        self.assertIn(self.action("graph", payload=graph)["type"], {"Fault", "Block"})
        self.assertEqual(self.runs.data, persisted)

    def test_partial_inventory_known_pair_unknown_subjects_and_singleton_adversaries(self):
        self.prepare()
        unknown = {"provider_id": "private", "model_id": "moving"}
        for author, auditor, members, expected in [
            (AUTHOR, AUDITOR, [AUTHOR, AUDITOR, unknown], None),
            (unknown, AUDITOR, [unknown, AUDITOR], "governance.author_unknown"),
            (AUTHOR, unknown, [AUTHOR, unknown], "governance.auditor_unknown"),
            (AUTHOR, AUTHOR, [AUTHOR, unknown], "audit.same_model"),
            (AUTHOR, AUTHOR, [AUTHOR, AUDITOR], "audit.same_model"),
            (AUTHOR, AUDITOR, [], "governance.inventory_unknown"),
        ]:
            with self.subTest(expected=expected):
                context = copy.deepcopy(CONTEXT)
                context.update(author=author)
                context["inventory"]["members"] = members
                self.service.trusted_governance_context(run_id=self.run_id, payload=context)
                self.action("configure_run", auditor=auditor, allow_same_model=author == auditor)
                result = self.admit(self.decision("pair-" + str(expected)))
                self.assertEqual(result["type"], "Block" if expected else "Allow", result)
                if expected:
                    self.assertEqual(result["reasons"][0]["code"], expected)
        context["author"] = AUTHOR
        context["inventory"]["members"] = [AUTHOR, {"provider_id": "amazon-bedrock-eu", "model_id": "eu.anthropic.claude-sonnet-4-6"}]
        self.service.trusted_governance_context(run_id=self.run_id, payload=context)
        self.action("configure_run", auditor=AUTHOR, allow_same_model=True)
        self.assertEqual(self.view()["governance"]["inventory_status"], "singleton")
        self.assertEqual(self.admit(self.decision("alias"))["type"], "Allow")
        before = copy.deepcopy(self.runs.data)
        context["inventory"]["members"] = [AUTHOR, AUTHOR]
        self.assertEqual(self.service.trusted_governance_context(run_id=self.run_id, payload=context)["result"]["type"], "Fault")
        self.assertEqual(self.runs.data, before)

    def test_auto_null_selection_prepared_before_digest_and_never_human(self):
        unknown = {"provider_id": "private", "model_id": "moving"}
        for index, members, selected, error in [
            (0, [AUTHOR, AUDITOR, unknown], AUDITOR, None),
            (1, [AUTHOR], AUTHOR, None),
            (2, [AUTHOR, unknown], None, "governance.auditor_required"),
            (3, [unknown], None, "governance.author_unknown"),
        ]:
            self.run_id = self.request({"type": "StartRun", "goal": "auto", "control_mode": "auto",
                "selector": {"project": "p", "session": "auto-null-" + str(index)}})["run"]["id"]
            self.action("graph", payload=GRAPH)
            context = copy.deepcopy(CONTEXT)
            context["inventory"]["members"] = members
            if index == 3:
                context["author"] = unknown
            self.service.trusted_governance_context(run_id=self.run_id, payload=context)
            g = self.view()["governance"]
            self.assertEqual(g["proposal"]["auditor"], selected)
            result = self.admit(self.decision(approval_kind="auto"))
            if error:
                self.assertEqual(result["reasons"][0]["code"], error)
            else:
                self.assertEqual(result["run"]["governance"]["approved_digest"], g["proposal_digest"])
                self.assertEqual(result["run"]["governance"]["approval_kind"], "auto")
                self.action("route", reason="supplied")
                self.assertEqual(self.action("investigate")["type"], "Allow")

    def test_present_reservation_replay_final_binding_and_third_completion(self):
        self.prepare()
        unreserved = self.decision("unreserved", reserve=False)
        before = copy.deepcopy(self.runs.data)
        self.assertEqual(self.admit(unreserved)["reasons"][0]["code"], "governance.receipt_replay")
        self.assertEqual(self.runs.data, before)
        presents = [self.decision(str(n), "present", reserve=False) for n in range(4)]
        for presented in presents[:3]:
            self.runs.conflicts = 1
            self.assertEqual(self.admit(presented)["type"], "Allow")
        before = copy.deepcopy(self.runs.data)
        self.assertEqual(self.admit(presents[3])["reasons"][0]["code"], "governance.interaction_limit")
        self.assertEqual(self.admit(presents[2])["type"], "Inert")
        self.assertEqual(self.runs.data, before)
        final = {**presents[2], "outcome": "approve"}
        for changed in ({"run_id": "other"}, {"plan_revision": 1000}, {"proposal_digest": "sha256:" + "a" * 64}, {"approval_kind": "auto"}):
            self.assertEqual(self.admit({**final, **changed})["type"], "Block")
            self.assertEqual(self.runs.data, before)
        self.assertEqual(self.admit(final)["run"]["governance"]["state"], "approved")
        before = copy.deepcopy(self.runs.data)
        self.assertEqual(self.admit(final)["type"], "Inert")
        self.assertEqual(self.admit(presents[2])["type"], "Inert")
        self.assertEqual(self.admit({**final, "outcome": "reject"})["type"], "Block")
        self.assertEqual(self.runs.data, before)
        raw = copy.deepcopy(next(iter(self.runs.data.values())).value)
        raw["governance"]["receipts"][0].pop("presentation_fingerprint")
        self.assertEqual(classify_and_decode(raw).kind, "current_corrupt")

    def test_dismiss_receipt_replay_strict_no_write_and_global_cap_across_initial_revisions(self):
        self.prepare()
        first = self.decision("dismiss", "dismiss")
        self.assertEqual(self.admit(first)["type"], "Allow")
        self.service = compose(Workspace(), Harness(), self.runs, self.artifacts, None, PROFILE, {}, None)
        self.assertEqual(self.admit(first)["type"], "Inert")
        before = copy.deepcopy(self.runs.data)
        for payload in ({**first, "outcome": "reject"}, {**first, "extra": True},
                        {**first, "receipt_id": "new", "plan_revision": 1000}):
            self.assertIn(self.admit(payload)["type"], {"Fault", "Block"})
            self.assertEqual(self.runs.data, before)
        for n in range(127):
            graph = copy.deepcopy(GRAPH)
            graph["claims"][0]["text"] = str(n)
            self.assertEqual(self.action("graph", payload=graph)["type"], "Allow")
            if n % 2:
                self.assertEqual(self.admit(self.decision(str(n), "dismiss"))["type"], "Allow")
            else:
                # An abandoned dialog remains charged after a material revision.
                presented = self.decision(str(n), "present", reserve=False)
                self.assertEqual(self.admit(presented)["type"], "Allow")
        graph["claims"][0]["text"] = "after cap"
        self.action("graph", payload=graph)
        g = self.view()["governance"]
        self.assertEqual(g["revisions_used"], 0)
        self.assertEqual(g["interactions_remaining"], {"proposal": 3, "total": 0})
        self.assertEqual(g["prompt_error"], "governance.interaction_limit")
        self.assertEqual(self.view()["next_actions"], ["residual.accept"])
        self.assertEqual(self.admit(self.decision("overflow", "dismiss"))["reasons"][0]["code"], "governance.interaction_limit")
        self.assertEqual(self.admit(first)["type"], "Inert")
        before = copy.deepcopy(self.runs.data)
        self.assertEqual(self.admit({**presented, "outcome": "dismiss"})["reasons"][0]["code"], "governance.stale_proposal")
        self.assertEqual(self.admit(presented)["type"], "Inert")
        self.assertEqual(self.runs.data, before)

    def test_maximal_escaped_graph_amendment_round_trip(self):
        import json
        from adapters.governance import readable
        from core.evaluation import valid_graph
        self.prepare()
        ids = [chr(0x10000 + n) * 128 for n in range(32)]
        claims = [{"id": key, "text": "\U0001f600" * 2048, "kind": "needs-experiment", "gating": False} for key in ids]
        pairs = [(0, n) for n in range(1, 32)] + [(a, b) for a in range(1, 32) for b in range(a + 1, 32)]
        graph = {"root": ids[0], "claims": claims, "edges": [{"from": ids[a], "to": ids[b], "type": "SupportedBy"} for a, b in pairs[:128]]}
        self.assertTrue(valid_graph(graph))
        raw = json.dumps(graph, ensure_ascii=True)
        self.assertGreater(len(raw), 1000000)
        self.assertEqual(self.admit(self.decision("max", "amend", amendment={
            "graph": json.loads(raw), "configuration": self.view()["governance"]["proposal"]}))["type"], "Allow")
        self.assertEqual(self.view()["governance"]["scope"], graph)
        for text in ('"' * 2048, "\\" * 2048, "\u0000" * 2048):
            graph["claims"][0]["text"] = text
            self.action("graph", payload=graph)
            rendered = readable(self.view())
            self.assertIn("| ", rendered)
            self.assertNotIn("\u0000", rendered)

    def test_exact_model_normalization_unknown_never_decorrelates(self):
        def host(row):
            return {**row, "observed_by": "host"}
        self.assertEqual(identity_relation(host(AUTHOR), host({"provider_id": "bedrock", "model_id": "eu.anthropic.claude-sonnet-4-6"})), "same_model")
        self.assertEqual(identity_relation(host(AUTHOR), host(AUDITOR)), "different_model")
        self.assertEqual(identity_relation(host(AUTHOR), host({"provider_id": "other", "model_id": "latest"})), "unknown_equivalence")


if __name__ == "__main__":
    unittest.main()
