#!/usr/bin/env python3
"""Red-first stale pending-audit recovery against the real coordinator/protocol."""
from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from test_d7_transactions import (Artifacts, Coordinator, Harness, Runs, Workspace,
                                  activate_investigation, chain, decode_state, encode_state,
                                  traverse_history)
from adapters.audit import child_event
from adapters.audit_protocol import AuditProtocol, AuditProtocolError, IdentityObservation
from core.evaluation import audit_binding
from core.records import Corrupt, Present, Revision
from governance_setup import approve_current

PROFILE = "pi@0.84.1+pi-subagents@0.50.0"


class StaleAuditRetryTests(unittest.TestCase):
    def setUp(self):
        self.runs, self.artifacts, self.workspace = Runs(), Artifacts(), Workspace()
        self.coordinator = Coordinator(self.workspace, Harness(), self.runs, self.artifacts, PROFILE)
        result = self.coordinator.handle({"type": "StartRun", "selector": {
            "project": "stale", "session": "retry"}, "goal": "stale audit retry",
            "budgets": {"max_spawns": 1, "max_audit_spawns": 2}}, "start")["result"]
        self.run_id = result["run"]["id"]
        self.key = next(iter(self.runs.data))
        self.graph = {"root": "G0", "claims": [{"id": "G0", "text": "tested",
            "gating": True, "kind": "ordinary"}], "edges": []}
        self.action({"kind": "graph", "payload": self.graph})
        approve_current(self.coordinator, self.run_id)
        activate_investigation(self.coordinator, self.run_id)
        self.action({"kind": "research", "claim_id": "G0", "source_kind": "code",
                     "result": "supports", "payload": {"source_ref": "source.py",
                                                           "citation": "tested"}})
        self.restore()

    def restore(self):
        self.coordinator = Coordinator(self.workspace, Harness(), self.runs, self.artifacts, PROFILE)
        self.protocol = AuditProtocol(PROFILE,
            dispatch=lambda request, _profile: self.coordinator.handle(request["command"], "protocol"),
            child_event_ingress=lambda _p, run, child, event:
                self.coordinator.trusted_child_event(run, child, event),
            attribution_ingress=lambda _p, run, payload:
                self.coordinator.trusted_attribution(run, payload),
            verdict_ingress=lambda _p, run, child, payload:
                self.coordinator.trusted_audit_verdict(run, child, payload),
            plan_ingress=lambda _p, run, child: self.coordinator.trusted_audit_plan(run, child))

    def action(self, action):
        return self.coordinator.handle({"type": "ObserveAction", "run_id": self.run_id,
                                        "action": action}, "action")["result"]

    def state(self):
        return decode_state(self.runs.data[self.key].value)

    def prepare(self):
        return self.protocol.prepare(self.run_id, role_profile="canonical-auditor")

    def pending(self):
        plan = self.prepare()
        self.protocol.observe_started(plan, "native:" + plan.child_id)
        return plan

    def change_argument(self):
        graph = copy.deepcopy(self.graph)
        graph["claims"][0]["text"] = "changed argument"
        self.assertEqual(self.action({"kind": "graph", "payload": graph})["type"], "Allow")
        approve_current(self.coordinator, self.run_id)

    def identities(self, plan, auditor_model="claude-opus-4-6"):
        self.protocol.observe_identities(plan, "native:" + plan.child_id,
            author=IdentityObservation("anthropic", "claude-sonnet-4-6", "host", "session"),
            auditor=IdentityObservation("anthropic", auditor_model, "host", "session"))

    def verdict(self):
        self.coordinator.handle({"type": "GetArgument", "run_id": self.run_id}, "argument")
        return {"verdict": "pass", **audit_binding(self.coordinator.last_snapshot)}

    def test_current_pending_is_not_replaced_or_charged(self):
        self.pending()
        before = self.runs.data[self.key]
        writes = self.artifacts.append_calls
        with self.assertRaises(AuditProtocolError):
            self.prepare()
        self.assertEqual(self.runs.data[self.key], before)
        self.assertEqual(self.artifacts.append_calls, writes)

    def test_stale_pending_retires_once_and_reserves_fresh_without_borrowing(self):
        self.action({"kind": "child_reserve", "purpose": "audit", "role_profile": "worker",
                     "execution": "foreground", "resource_class": "investigation"})
        plan = self.pending()
        original = self.state().children[-1]
        prior_artifacts = dict(self.artifacts.values[self.key])
        self.change_argument()
        self.runs.conflicts = 1
        fresh = self.prepare()
        state = self.state()
        self.assertNotEqual(fresh.child_id, plan.child_id)
        self.assertNotEqual(fresh.argument["argument_digest"], plan.argument["argument_digest"])
        ordinary, old, new = state.children
        self.assertEqual(ordinary["state"], "reserved")
        self.assertEqual(old["state"], "cancelled")
        self.assertTrue(old["spent"])
        self.assertFalse(old["refunded"])
        self.assertEqual(old["native_id"], original["native_id"])
        self.assertEqual(old["audit_argument"], original["audit_argument"])
        self.assertEqual(old["audit_operation_id"], original["audit_operation_id"])
        self.assertIsNotNone(old["first_terminal_fingerprint"])
        self.assertEqual(new["state"], "reserved")
        self.assertEqual(state.budgets["spawns_used"], 1)
        self.assertEqual(state.budgets["audit_spawns_used"], 2)
        self.assertEqual(state.budgets["passes_used"], 0)
        self.assertTrue(all(self.artifacts.values[self.key][k] == v
                            for k, v in prior_artifacts.items()))
        traverse_history(state, self.artifacts.values[self.key].values())
        before = self.runs.data[self.key]
        for target in ("completed", "failed", "cancelled"):
            result = self.coordinator.trusted_child_event(self.run_id, plan.child_id,
                child_event(target, "native:" + plan.child_id))["result"]
            self.assertEqual(result["type"], "Fault")
            self.assertEqual(self.runs.data[self.key], before)

    def test_exhaustion_never_retires_refunds_or_raises_budget(self):
        self.action({"kind": "configure_run", "budgets": {"max_audit_spawns": 1}})
        approve_current(self.coordinator, self.run_id)
        self.pending()
        self.change_argument()
        before, writes = self.runs.data[self.key], self.artifacts.append_calls
        with self.assertRaises(AuditProtocolError):
            self.prepare()
        self.assertEqual(self.runs.data[self.key], before)
        self.assertEqual(self.artifacts.append_calls, writes)
        self.assertEqual(self.state().children[0]["state"], "pending")
        self.assertEqual(self.state().budgets["max_audit_spawns"], 1)
        self.assertEqual(self.state().budgets["audit_spawns_used"], 1)
        before, writes = self.runs.data[self.key], self.artifacts.append_calls
        result = self.action({"kind": "child_reserve", "purpose": "audit",
            "resource_class": "audit", "role_profile": "canonical-auditor", "execution": "foreground"})
        self.assertEqual(result["reasons"][0]["code"], "budget.exhausted")
        self.assertEqual(result["reasons"][0]["parameters"], {"resource": "audit_spawn"})
        self.assertEqual(self.runs.data[self.key], before)
        self.assertEqual(self.artifacts.append_calls, writes)
        self.action({"kind": "configure_run", "budgets": {"max_audit_spawns": 2}})
        approve_current(self.coordinator, self.run_id)
        self.prepare()
        self.assertEqual(self.state().budgets["audit_spawns_used"], 2)

    def test_old_pending_cannot_admit_a_rebound_current_verdict(self):
        plan = self.pending()
        self.change_argument()
        before = self.runs.data[self.key]
        result = self.coordinator.trusted_audit_verdict(
            self.run_id, plan.child_id, self.verdict())["result"]
        self.assertEqual(result["type"], "Fault")
        self.assertEqual(self.runs.data[self.key], before)

    def test_freeze_only_change_invalidates_pending_binding(self):
        old = self.pending()
        self.action({"kind": "freeze"})
        fresh = self.prepare()
        self.assertEqual(old.argument["argument_digest"], fresh.argument["argument_digest"])
        self.assertNotEqual(old.argument["frozen_scope_digest"], fresh.argument["frozen_scope_digest"])
        self.assertEqual(self.state().children[0]["state"], "cancelled")

    def test_retry_keeps_observed_identity_for_unchanged_evidence(self):
        old = self.pending()
        self.identities(old)
        self.action({"kind": "freeze"})
        fresh = self.pending()
        self.identities(fresh)
        candidate = self.verdict()
        with self.assertRaises(AuditProtocolError):
            self.protocol.observe_verdict(old, "native:" + old.child_id, candidate)
        self.assertTrue(self.protocol.observe_verdict(fresh, "native:" + fresh.child_id, candidate))
        result = self.coordinator.handle({"type": "EvaluateRun", "run_id": self.run_id,
                                         "intent": "report_convergence"}, "gate")["result"]
        self.assertEqual(result["type"], "Allow")
        self.assertTrue(result["converged"])

    def test_deferred_addition_retry_can_readmit_the_same_observed_evidence_producer(self):
        self.action({"kind": "freeze"})
        old = self.pending()
        self.identities(old)
        graph = copy.deepcopy(self.graph)
        graph["claims"].append({"id": "G1", "text": "deferred", "gating": False, "kind": "ordinary"})
        graph["edges"].append({"from": "G0", "to": "G1", "type": "SupportedBy"})
        self.action({"kind": "graph", "payload": graph})
        approve_current(self.coordinator, self.run_id)
        fresh = self.pending()
        self.assertEqual(old.evidence_ids, fresh.evidence_ids)
        self.identities(fresh)
        self.assertTrue(self.protocol.observe_verdict(fresh, "native:" + fresh.child_id, self.verdict()))

    def test_file_freshness_alone_invalidates_pending_coverage(self):
        self.graph["claims"][0]["kind"] = "needs-experiment"
        self.action({"kind": "graph", "payload": self.graph})
        approve_current(self.coordinator, self.run_id)
        self.action({"kind": "research", "claim_id": "G0", "source_kind": "code",
            "result": "supports", "payload": {"source_ref": "source.py", "citation": "tested"}})
        self.workspace.write("source.py", b"original")
        self.action({"kind": "spike_request", "claim_id": "G0", "command": "test",
                     "dependent_files": ["source.py"]})
        old = self.pending()
        self.workspace.write("source.py", b"changed")
        fresh = self.prepare()
        self.assertEqual(old.argument["argument_digest"], fresh.argument["argument_digest"])
        self.assertNotEqual(old.argument["claims"][0]["state"], fresh.argument["claims"][0]["state"])
        self.assertEqual(self.state().children[0]["state"], "cancelled")

    def test_malformed_durable_dossier_is_fixed_safe_corruption_not_staleness(self):
        self.pending()
        state = self.state()
        history = traverse_history(state, self.artifacts.values[self.key].values())
        child = {**state.children[0], "audit_argument": {"canary": "DO_NOT_PROJECT"}}
        damaged, values = chain(replace(state, children=(child,)),
            tuple({"body": {k: v for k, v in item.items() if k != "artifact_id"}} for item in history))
        self.runs.data[self.key] = Present(encode_state(damaged), Revision("damaged"))
        self.artifacts.values[self.key] = {item.artifact_id: item for item in values}
        before, writes = self.runs.data[self.key], self.artifacts.append_calls
        for command in ({"type": "GetRun", "run_id": self.run_id},
                        {"type": "ObserveAction", "run_id": self.run_id, "action": {
                            "kind": "child_reserve", "purpose": "audit", "resource_class": "audit",
                            "role_profile": "canonical-auditor", "execution": "foreground"}}):
            result = self.coordinator.handle(command, "corrupt")["result"]
            self.assertEqual(result["type"], "Block")
            self.assertEqual(result["reasons"][0]["code"], "run.corrupt")
            self.assertEqual(result["run"]["goal"], "Unsupported run state.")
            self.assertEqual(result["run"]["children"], [])
            self.assertNotIn("DO_NOT_PROJECT", str(result))
        self.assertIsNone(self.coordinator.trusted_audit_plan(self.run_id, child["child_id"]))
        self.assertEqual(self.runs.data[self.key], before)
        self.assertEqual(self.artifacts.append_calls, writes)

    def test_successful_failure_and_restore_reconciliation_accept_terminal_blocks(self):
        old = self.pending()
        self.protocol.observe_failure(old, "native:" + old.child_id, "timed_out")
        self.assertEqual(self.state().children[0]["state"], "timed_out")
        self.protocol.observe_failure(old, "native:" + old.child_id, "timed_out")
        fresh = self.pending()
        self.restore()
        self.assertEqual(self.protocol.reconcile_orphans(self.run_id, native_prefix="restore"), 1)
        self.assertEqual(self.state().children[1]["state"], "orphaned")
        self.assertNotEqual(old.child_id, fresh.child_id)

    def test_stale_replacement_uses_only_durable_state_after_restore(self):
        old = self.pending()
        original = self.state().children[0]
        self.change_argument()
        old_coordinator, old_protocol = self.coordinator, self.protocol
        self.restore()
        self.assertIsNot(self.coordinator, old_coordinator)
        self.assertIsNot(self.protocol, old_protocol)
        loaded = self.coordinator.trusted_audit_plan(self.run_id, old.child_id)
        self.assertEqual(loaded["argument"], old.argument)
        fresh = self.pending()
        retired = self.state().children[0]
        self.assertEqual(retired["state"], "cancelled")
        for key in ("audit_argument", "audit_operation_id", "native_id", "spent", "refunded"):
            self.assertEqual(retired[key], original[key])
        self.assertNotEqual(fresh.child_id, old.child_id)
        self.assertNotEqual(fresh.argument["argument_digest"], old.argument["argument_digest"])
        self.assertEqual(self.state().budgets["audit_spawns_used"], 2)
        before, writes = self.runs.data[self.key], self.artifacts.append_calls
        with self.assertRaises(AuditProtocolError):
            self.prepare()
        result = self.coordinator.trusted_audit_verdict(
            self.run_id, old.child_id, self.verdict())["result"]
        self.assertEqual(result["type"], "Fault")
        self.assertEqual(self.runs.data[self.key], before)
        self.assertEqual(self.artifacts.append_calls, writes)

    def test_concurrent_terminal_wins_over_stale_recovery_cas(self):
        old = self.pending()
        self.change_argument()
        cas = self.runs.compare_and_set
        raced = False

        def race(key, value, expected):
            nonlocal raced
            if not raced:
                raced = True
                self.coordinator.trusted_child_event(self.run_id, old.child_id,
                    child_event("timed_out", "native:" + old.child_id))
            return cas(key, value, expected)

        self.runs.compare_and_set = race
        fresh = self.prepare()
        self.assertNotEqual(old.child_id, fresh.child_id)
        self.assertEqual(self.state().children[0]["state"], "timed_out")
        self.assertEqual(self.state().children[0]["first_terminal_fingerprint"],
                         child_event("timed_out", "native:" + old.child_id)["fingerprint"])
        self.assertEqual(self.state().budgets["audit_spawns_used"], 2)
        self.assertEqual(len(self.state().children), 2)

    def test_stale_reserved_and_launching_are_not_assumed_abandoned(self):
        old = self.prepare()
        self.change_argument()
        for stage in ("reserved", "launching"):
            if stage == "launching":
                self.coordinator.trusted_child_event(self.run_id, old.child_id,
                    child_event(stage, "native:" + old.child_id))
            before, writes = self.runs.data[self.key], self.artifacts.append_calls
            with self.assertRaises(AuditProtocolError):
                self.prepare()
            self.assertEqual(self.runs.data[self.key], before)
            self.assertEqual(self.artifacts.append_calls, writes)

    def test_retry_does_not_inherit_the_cancelled_auditors_identity(self):
        old = self.pending()
        self.identities(old)
        self.action({"kind": "freeze"})
        fresh = self.pending()
        with self.assertRaises(AuditProtocolError):
            self.protocol.observe_identities(fresh, "native:" + fresh.child_id,
                author=IdentityObservation("anthropic", "claude-sonnet-4-6", "host", "session"),
                auditor=IdentityObservation(None, None, "unverified", "missing-session"))
        result = self.coordinator.handle({"type": "EvaluateRun", "run_id": self.run_id,
                                         "intent": "report_convergence"}, "gate")["result"]
        self.assertEqual(result["type"], "Block")
        self.assertEqual(result["reasons"][0]["code"], "governance.revision_required")

    def test_corrupt_and_terminal_runs_never_recover(self):
        self.pending()
        self.change_argument()
        self.coordinator.handle({"type": "EvaluateRun", "run_id": self.run_id,
                                 "intent": "stop"}, "stop")
        before = self.runs.data[self.key]
        with self.assertRaises(AuditProtocolError):
            self.prepare()
        self.assertEqual(self.runs.data[self.key], before)
        self.runs.data[self.key] = Corrupt("canary")
        writes = self.artifacts.append_calls
        with self.assertRaises(AuditProtocolError):
            self.prepare()
        result = self.action({"kind": "child_reserve", "purpose": "audit",
            "resource_class": "audit", "role_profile": "canonical-auditor", "execution": "foreground"})
        self.assertEqual(result["reasons"][0]["code"], "run.corrupt")
        self.assertEqual(result["run"]["goal"], "Unsupported run state.")
        self.assertEqual(result["run"]["children"], [])
        self.assertNotIn("canary", str(result))
        self.assertEqual(self.artifacts.append_calls, writes)


if __name__ == "__main__":
    unittest.main()
