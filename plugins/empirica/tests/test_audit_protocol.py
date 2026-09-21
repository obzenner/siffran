#!/usr/bin/env python3
"""Canonical foreground-audit protocol traces shared by every host driver."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.audit_protocol import (  # noqa: E402
    AuditLaunchPlan, AuditProtocol, AuditProtocolError, IdentityObservation,
)


def response(result: dict) -> dict:
    return {"protocol": "empirica/v2", "request_id": "x", "result": result}


class AuditProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[tuple] = []
        self.requests: list[dict] = []
        self.children = [{"child_id": "ch-1", "purpose": "audit", "resource_class": "audit", "state": "reserved"}]
        self.argument = {
            "argument_digest": "sha256:" + "a" * 64,
            "claims": [{"claim_id": "G0", "gating": True, "state": "approved",
                        "active_evidence_ids": ["sha256:" + "1" * 64]}],
        }

        def dispatch(request: dict, _profile: str) -> dict:
            self.requests.append(request)
            command = request["command"]
            if command["type"] == "GetRun":
                return response({"type": "Allow", "converged": False,
                                 "run": {"children": []}})
            if command["type"] == "ObserveAction":
                return response({"type": "Allow", "converged": False,
                                 "run": {"children": list(self.children)}})
            if command["type"] == "GetArgument":
                return response({"type": "Allow", "argument": self.argument})
            raise AssertionError(command)

        def child(_profile: str, _run: str, child_id: str, event: dict) -> dict:
            self.events.append(("child", child_id, event["state"], event["native_id"]))
            return response({"type": "Allow", "converged": False, "run": {"children": [{
                "child_id": child_id, "state": event["state"], "resource_class": "audit"}]}})

        def attribution(_profile: str, _run: str, payload: dict) -> dict:
            self.events.append(("attribution", payload))
            return response({"type": "Allow", "converged": False, "run": {}})

        def verdict(_profile: str, _run: str, child_id: str, payload: dict) -> dict:
            self.events.append(("verdict", child_id, payload))
            return response({"type": "Allow", "converged": True, "run": {}})

        self.protocol = AuditProtocol(
            "test-profile", dispatch=dispatch, child_event_ingress=child,
            attribution_ingress=attribution, verdict_ingress=verdict,
            plan_ingress=lambda _profile, _run, child_id: {
                "operation_id": "sha256:" + "b" * 64,
                "child_id": child_id, "role_profile": "canonical-auditor",
                "argument": self.argument,
            })

    def test_happy_trace_has_one_reservation_and_ordered_observations(self) -> None:
        plan = self.protocol.prepare("run", role_profile="canonical-auditor")
        self.assertEqual(plan.child_id, "ch-1")
        reservation = next(row["command"]["action"] for row in self.requests
                           if row["command"]["type"] == "ObserveAction")
        self.assertEqual(reservation["resource_class"], "audit")
        self.assertEqual(plan.evidence_ids, ["sha256:" + "1" * 64])
        self.protocol.observe_started(plan, "native-1")
        self.protocol.observe_identities(
            plan, "native-1",
            author=IdentityObservation("p1", "m1", "host", "parent"),
            auditor=IdentityObservation("p2", "m2", "host", "child"))
        self.assertTrue(self.protocol.observe_verdict(
            plan, "native-1", {"verdict": "pass"}))
        self.assertEqual([row[0] for row in self.events],
                         ["child", "child", "attribution", "attribution", "verdict"])
        self.assertEqual([row[2] for row in self.events[:2]], ["launching", "pending"])
        auditor = self.events[3][1]
        self.assertEqual(auditor["child_id"], "ch-1")
        self.assertEqual(auditor["subject_id"], "auditor:ch-1")

    def test_purpose_collision_does_not_block_or_orphan_investigation_child(self) -> None:
        ordinary = {"child_id": "ordinary", "purpose": "audit",
                    "resource_class": "investigation", "state": "reserved"}
        audit = {"child_id": "ch-1", "purpose": "audit",
                 "resource_class": "audit", "state": "reserved"}
        gets = 0

        def dispatch(request: dict, _profile: str) -> dict:
            nonlocal gets
            kind = request["command"]["type"]
            if kind == "GetRun":
                gets += 1
                children = [ordinary] if gets == 1 else [ordinary, audit]
                return response({"type": "Allow", "converged": False,
                                 "run": {"children": children}})
            if kind == "ObserveAction":
                return response({"type": "Allow", "converged": False,
                                 "run": {"children": [ordinary, audit]}})
            if kind == "GetArgument":
                return response({"type": "Allow", "argument": self.argument})
            raise AssertionError(kind)

        protocol = AuditProtocol(
            "test-profile", dispatch=dispatch,
            child_event_ingress=self.protocol._child_event,
            attribution_ingress=self.protocol._attribution,
            verdict_ingress=self.protocol._verdict,
            plan_ingress=lambda _profile, _run, child_id: {
                "operation_id": "sha256:" + "b" * 64, "child_id": child_id,
                "role_profile": "canonical-auditor", "argument": self.argument,
            })
        self.assertEqual(protocol.prepare("run", role_profile="canonical-auditor").child_id,
                         "ch-1")
        self.events.clear()
        self.assertEqual(protocol.reconcile_orphans("run", native_prefix="restore"), 1)
        self.assertEqual([event[1] for event in self.events], ["ch-1"])

    def test_argument_failure_rejects_the_reserved_child(self) -> None:
        def dispatch(request: dict, _profile: str) -> dict:
            if request["command"]["type"] == "GetRun":
                return response({"type": "Allow", "run": {"children": []}})
            if request["command"]["type"] == "ObserveAction":
                return response({"type": "Allow", "run": {"children": self.children}})
            return response({"type": "Block", "reasons": []})
        protocol = AuditProtocol(
            "test-profile", dispatch=dispatch,
            child_event_ingress=self.protocol._child_event,
            attribution_ingress=self.protocol._attribution,
            verdict_ingress=self.protocol._verdict,
            plan_ingress=lambda _profile, _run, _child: None)
        with self.assertRaises(AuditProtocolError):
            protocol.prepare("run", role_profile="canonical-auditor")
        self.assertEqual(self.events[-1][2:], ("launch_rejected", None))

    def test_started_failure_closes_pending_operation(self) -> None:
        plan = AuditLaunchPlan("test-profile", "run", "ch-1", "canonical", self.argument)
        self.protocol.observe_started(plan, "native-1")
        self.protocol.observe_failure(plan, "native-1", "timed_out")
        self.assertEqual(self.events[-1][2], "timed_out")

    def test_rejected_verdict_closes_exact_operation_failed(self) -> None:
        protocol = AuditProtocol(
            "test-profile", dispatch=self.protocol._dispatch,
            child_event_ingress=self.protocol._child_event,
            attribution_ingress=self.protocol._attribution,
            verdict_ingress=lambda *_args: response({"type": "Fault", "code": "conflict"}),
            plan_ingress=self.protocol._plan)
        plan = AuditLaunchPlan("test-profile", "run", "ch-1", "canonical", self.argument)
        self.assertFalse(protocol.observe_verdict(plan, "native-1", {"verdict": "bad"}))
        self.assertEqual(self.events[-1][2], "failed")

    def test_identity_ingress_failure_closes_exact_operation_failed(self) -> None:
        protocol = AuditProtocol(
            "test-profile", dispatch=self.protocol._dispatch,
            child_event_ingress=self.protocol._child_event,
            attribution_ingress=lambda *_args: (_ for _ in ()).throw(OSError("down")),
            verdict_ingress=self.protocol._verdict, plan_ingress=self.protocol._plan)
        plan = AuditLaunchPlan("test-profile", "run", "ch-1", "canonical", self.argument)
        with self.assertRaises(AuditProtocolError):
            protocol.observe_identities(
                plan, "native-1",
                author=IdentityObservation("p1", "m1", "host", "parent"),
                auditor=IdentityObservation("p2", "m2", "host", "child"))
        self.assertEqual(self.events[-1][2], "failed")

    def test_recovery_orphans_every_active_audit_child(self) -> None:
        active = [
            {"child_id": "r", "purpose": "audit", "resource_class": "audit", "state": "reserved"},
            {"child_id": "l", "purpose": "audit", "resource_class": "audit", "state": "launching"},
            {"child_id": "p", "purpose": "audit", "resource_class": "audit", "state": "pending"},
            {"child_id": "done", "purpose": "audit", "resource_class": "audit", "state": "completed"},
        ]

        def dispatch(request: dict, _profile: str) -> dict:
            self.assertEqual(request["command"]["type"], "GetRun")
            return response({"type": "Allow", "run": {"children": active}})

        protocol = AuditProtocol(
            "test-profile", dispatch=dispatch,
            child_event_ingress=self.protocol._child_event,
            attribution_ingress=self.protocol._attribution,
            verdict_ingress=self.protocol._verdict)
        self.assertEqual(protocol.reconcile_orphans("run", native_prefix="restore"), 3)
        self.assertEqual([(row[1], row[2]) for row in self.events],
                         [("r", "orphaned"), ("l", "orphaned"), ("p", "orphaned")])

    def test_terminal_acknowledgement_accepts_only_exact_committed_block(self) -> None:
        plan = AuditLaunchPlan("test-profile", "run", "ch-1", "canonical", self.argument)
        exact = {"type": "Block", "reasons": [{"code": "child.terminal",
            "parameters": {"state": "timed_out"}}], "run": {"children": [
                {"child_id": "ch-1", "state": "timed_out", "resource_class": "audit"}]}}
        wrong_code = copy.deepcopy(exact)
        wrong_code["reasons"][0]["code"] = "run.corrupt"
        extra_reason = copy.deepcopy(exact)
        extra_reason["reasons"].append({"code": "run.corrupt"})
        wrong_reason_state = copy.deepcopy(exact)
        wrong_reason_state["reasons"][0]["parameters"]["state"] = "failed"
        missing_child = copy.deepcopy(exact)
        missing_child["run"]["children"] = []
        wrong_child = copy.deepcopy(exact)
        wrong_child["run"]["children"][0]["child_id"] = "other"
        wrong_child_state = copy.deepcopy(exact)
        wrong_child_state["run"]["children"][0]["state"] = "pending"
        for result in (wrong_code, extra_reason, wrong_reason_state, missing_child,
                       wrong_child, wrong_child_state):
            with self.subTest(result=result):
                self.protocol._child_event = lambda *_args: response(result)
                with self.assertRaises(AuditProtocolError):
                    self.protocol.observe_failure(plan, "native-1", "timed_out")
        self.protocol._child_event = lambda *_args: response(exact)
        self.protocol.observe_failure(plan, "native-1", "timed_out")

    def test_terminal_allow_or_inert_requires_exact_child_state(self) -> None:
        plan = AuditLaunchPlan("test-profile", "run", "ch-1", "canonical", self.argument)
        exact_child = {"child_id": "ch-1", "state": "timed_out", "resource_class": "audit"}
        for result_type in ("Allow", "Inert"):
            exact = {"type": result_type, "run": {"children": [exact_child]}}
            self.protocol._child_event = lambda *_args, exact=exact: response(exact)
            self.protocol.observe_failure(plan, "native-1", "timed_out")
            for children in ([], [{**exact_child, "child_id": "other"}],
                             [{**exact_child, "state": "pending"}]):
                with self.subTest(result_type=result_type, children=children):
                    invalid = {"type": result_type, "run": {"children": children}}
                    self.protocol._child_event = lambda *_args, invalid=invalid: response(invalid)
                    with self.assertRaises(AuditProtocolError):
                        self.protocol.observe_failure(plan, "native-1", "timed_out")

    def test_malformed_terminal_response_is_a_typed_reconciliation_error(self) -> None:
        plan = AuditLaunchPlan("test-profile", "run", "ch-1", "canonical", self.argument)
        for malformed in (None, [], {"result": None}, response({"type": "Block", "reasons": None}),
                          response({"type": "Block", "reasons": [None]})):
            with self.subTest(response=malformed):
                self.protocol._child_event = lambda *_args: malformed
                with self.assertRaises(AuditProtocolError):
                    self.protocol.observe_failure(plan, "native-1", "timed_out")

    def test_multiple_reserved_children_fail_closed(self) -> None:
        self.children.append({"child_id": "ch-2", "purpose": "audit", "resource_class": "audit", "state": "reserved"})
        with self.assertRaises(AuditProtocolError):
            self.protocol.prepare("run", role_profile="canonical-auditor")


if __name__ == "__main__":
    unittest.main()
