#!/usr/bin/env python3
"""Governance real service/manifest/CAS regressions, not a policy-only scaffold."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from application.v2 import compose
from application import protocol
from application.run_state import classify_and_decode
from application.snapshot import traverse_history
from test_d7_transactions import Runs, Artifacts, Workspace, Harness
from governance_setup import AUTHOR, AUDITOR, TEST_INVOCATION
from core.run import start_admission

PROFILE = "pi@0.84.1+pi-subagents@0.50.0"
GRAPH = {"root": "C0", "claims": [{"id": "C0", "text": "supplied uncertainty", "gating": True,
                                   "kind": "ordinary"}], "edges": []}
CONTEXT = {"author": AUTHOR, "ingress": "pi_ui"}


class HostProfileApprovalTests(unittest.TestCase):
    def _profile(self, host_id):
        root = Path(__file__).resolve().parents[3]
        profiles = json.loads((root / "contracts/empirica/v2/host-profiles.json").read_text())
        return next(p for p in profiles["profiles"] if p["host_id"] == host_id)

    def _admit_context(self, profile, ingress):
        """Compose a real service for ``profile`` and submit one governance context."""
        with mock.patch.dict(protocol._PROFILES, {profile["profile_id"]: profile}):
            service = compose(Workspace(), Harness(), Runs(), Artifacts(), None,
                              profile["profile_id"], {}, None)
            result = service.dispatch({"protocol": "empirica/v2", "request_id": "start",
                "command": {"type": "StartRun", "goal": "synthetic profile",
                "invocation": TEST_INVOCATION,
                "selector": {"project": "p", "session": "s"}}})["result"]
            run_id = result["run"]["id"]
            return service.trusted_governance_context(run_id=run_id,
                payload={"author": AUTHOR, "ingress": ingress})["result"]

    def test_synthetic_profile_admits_configured_ingress_without_host_branch(self):
        root = Path(__file__).resolve().parents[3]
        schema = json.loads((root / "contracts/empirica/v2/host-profiles.schema.json").read_text())
        synthetic = copy.deepcopy(self._profile("pi"))
        synthetic.update(host_id="synthetic", profile_id="synthetic-transport@9.9.9",
                         version="9.9.9", compatibility={"minimum": "9.9.9",
                         "maximum_exclusive": "10.0.0"})
        jsonschema.validate({"protocol": "empirica/v2", "profiles": [synthetic]}, schema)
        admitted = self._admit_context(synthetic, synthetic["approval_ingress"])
        self.assertEqual(admitted["type"], "Allow")
        self.assertEqual(admitted["run"]["governance"]["context"]["approval_capability"],
                         "human_configuration")

    def test_capable_profile_admits_unavailable_ingress_as_unavailable_capability(self):
        """Headless sessions submit ``unavailable`` and must not be blocked (quality #1)."""
        admitted = self._admit_context(self._profile("pi"), "unavailable")
        self.assertEqual(admitted["type"], "Allow", admitted)
        self.assertEqual(admitted["run"]["governance"]["context"]["approval_capability"],
                         "unavailable")

    def test_mismatched_transport_is_blocked(self):
        """A transport that is neither ``unavailable`` nor the profile's ingress blocks."""
        admitted = self._admit_context(self._profile("pi"), "mcp_elicitation")
        self.assertEqual(admitted["type"], "Block")
        self.assertEqual([r["code"] for r in admitted["reasons"]],
                         ["governance.approval_unavailable"])

    def test_unavailable_profile_never_yields_human_configuration(self):
        """A profile whose ingress is ``unavailable`` cannot grant host-UI capability."""
        root = Path(__file__).resolve().parents[3]
        schema = json.loads((root / "contracts/empirica/v2/host-profiles.schema.json").read_text())
        synthetic = copy.deepcopy(self._profile("pi"))
        synthetic.update(host_id="synthetic", profile_id="synthetic-headless@9.9.9",
                         version="9.9.9", approval_ingress="unavailable",
                         compatibility={"minimum": "9.9.9", "maximum_exclusive": "10.0.0"})
        jsonschema.validate({"protocol": "empirica/v2", "profiles": [synthetic]}, schema)
        admitted = self._admit_context(synthetic, "unavailable")
        self.assertEqual(admitted["type"], "Allow", admitted)
        self.assertEqual(admitted["run"]["governance"]["context"]["approval_capability"],
                         "unavailable")
        self.assertEqual(self._admit_context(synthetic, "pi_ui")["type"], "Block")


class StartAdmissionTests(unittest.TestCase):
    def test_start_admission_table(self):
        cases = [
            ({"goal": "", "control_mode": "deliberative", "invocation": TEST_INVOCATION},
             "run.goal_required"),
            ({"goal": "goal", "control_mode": "auto"},
             "governance.auto_invocation_required"),
            ({"goal": "goal", "control_mode": "auto", "invocation": {}},
             "governance.auto_invocation_required"),
            ({"goal": "goal", "control_mode": "auto",
              "invocation": {**TEST_INVOCATION, "interactive": False, "delegation": False}},
             "governance.auto_invocation_required"),
            ({"goal": "goal", "control_mode": "auto", "invocation": TEST_INVOCATION}, None),
            ({"goal": "goal", "control_mode": "deliberative",
              "invocation": {**TEST_INVOCATION, "interactive": False}}, None),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(start_admission(command), expected)


class GovernanceServiceTests(unittest.TestCase):
    def setUp(self):
        self.runs, self.artifacts = Runs(), Artifacts()
        self.service = compose(Workspace(), Harness(), self.runs, self.artifacts, None, PROFILE, {}, None)
        self.run_id = self.request({"type": "StartRun", "goal": "governed task",
                                    "selector": {"project": "p", "session": "s"}})["run"]["id"]

    def request(self, command):
        if command.get("type") == "StartRun":
            command = {"invocation": dict(TEST_INVOCATION), **command}
        return self.service.dispatch({"protocol": "empirica/v2", "request_id": "test", "command": command})["result"]

    def action(self, kind, **kwargs):
        return self.request({"type": "ObserveAction", "run_id": self.run_id,
                             "action": {"kind": kind, **kwargs}})

    def view(self):
        return self.request({"type": "GetRun", "run_id": self.run_id})["run"]

    def presentation(self):
        """QUAL-1: dialog and scope live only in the private governance presentation.
        Read them from project_presentation over the current snapshot, never the author RunView."""
        from core.projection import project_presentation
        self.view()
        return project_presentation(self.service._coordinator.last_snapshot)

    def decision(self, receipt="receipt", outcome="approve", *, reserve=True, **kwargs):
        """Explicit simulated-host reservation before constructing a final UI decision."""
        g = self.view()["governance"]
        payload = {"run_id": self.run_id, "receipt_id": receipt + "-" + str(g["plan_revision"]), "proposal_digest": g["proposal_digest"],
                   "plan_revision": g["plan_revision"], "approval_kind": kwargs.pop("approval_kind", "host_ui")}
        if payload["approval_kind"] == "auto" or outcome in {"present", "dismiss"}:
            payload.update(outcome=outcome, **kwargs)
        else:
            proposal = copy.deepcopy(kwargs.get("amendment", {}).get("configuration", g["proposal"]))
            action = "approve" if outcome in {"approve", "amend"} else outcome
            submission = {"action": action, "configuration": proposal}
            payload["submission"] = submission
        if reserve and outcome != "present" and payload["approval_kind"] == "host_ui" and g["state"] != "approved" and not g["prompt_error"]:
            present = {k: v for k, v in payload.items() if k not in {"amendment", "submission"}}
            present["outcome"] = "present"
            self.assertIn(self.admit(present)["type"], {"Allow", "Inert"})
        return payload

    def admit(self, payload):
        return self.service.trusted_governance_decision(run_id=self.run_id, payload=payload)["result"]

    def prepare(self):
        self.assertEqual(self.action("route", reason="supplied context")["type"], "Allow")
        self.assertEqual(self.action("graph", payload=copy.deepcopy(GRAPH))["type"], "Allow")
        self.assertEqual(self.action("configure_run")["type"], "Allow")
        result = self.service.trusted_governance_context(run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
        self.assertEqual(result["result"]["type"], "Allow", result)

    def test_configure_run_schema_rejects_reviewer_field(self):
        before = copy.deepcopy(self.runs.data)
        result = self.action("configure_run", auditor=AUDITOR)
        self.assertEqual(result["type"], "Fault")
        self.assertEqual(result["code"], "invalid_request")
        self.assertEqual(self.runs.data, before)

    def test_graphless_configure_and_private_present_are_effect_free_blocks(self):
        initial = self.view()
        self.assertEqual([row["id"] for row in initial["obligations"]["active"]],
                         ["obligation.route", "obligation.graph"])
        self.assertEqual(initial["next_actions"], ["route.record", "graph.record"])
        self.assertFalse(initial["governance"]["request_ready"])
        self.assertFalse(initial["governance"]["display_ready"])
        before = copy.deepcopy(self.runs.data)
        proposed = self.action("configure_run")
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
        self.assertEqual(self.presentation()["scope"]["root"], "C0")
        before = self.view()["governance"]["budgets"]["max_passes"]
        self.assertEqual(self.action(**examples["configure_run"]["example"])["type"], "Allow")
        governed = self.view()["governance"]
        self.assertEqual(governed["proposal"]["budgets"]["max_passes"], 5)
        self.assertEqual(governed["budgets"]["max_passes"], before)
        self.service.trusted_governance_context(run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
        self.action("configure_run")
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
        self.action("configure_run")
        self.service.trusted_governance_context(run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
        self.assertEqual(self.admit(self.decision())["type"], "Allow")
        self.assertEqual(self.view()["next_actions"], ["route.record"])
        self.assertEqual(self.action("investigate")["reasons"][0]["code"], "route.required")

    def test_bootstrap_refresh_and_capacity_have_distinct_recovery(self):
        self.action("graph", payload=copy.deepcopy(GRAPH))
        self.action("configure_run")
        self.assertEqual(self.view()["next_actions"], ["route.record", "governance.propose"])
        unusable = {**CONTEXT, "author": {"provider_id": "private", "model_id": "unknown"}}
        self.service.trusted_governance_context(run_id=self.run_id, payload=unusable)
        self.assertEqual(self.view()["next_actions"], ["route.record", "governance.propose"])

    def test_bootstrap_terminal_run_has_no_preparation_actions(self):
        result = self.request({"type": "EvaluateRun", "run_id": self.run_id, "intent": "stop"})
        self.assertEqual(result["run"]["status"], "stopped_residual")
        self.assertEqual(result["run"]["next_actions"],
                         protocol._PUBLIC_CONTRACT["reasons"]["run.terminal"]["next_actions"])
        self.assertFalse(result["run"]["governance"]["request_ready"])
        self.assertFalse(result["run"]["governance"]["display_ready"])

    def test_graph_change_does_not_revoke_configuration_approval(self):
        self.assertEqual(self.view()["governance"]["state"], "pending")
        self.prepare()
        self.assertEqual(self.action("investigate")["reasons"][0]["code"], "governance.approval_required")
        self.assertEqual(self.admit(self.decision())["type"], "Allow")
        before = self.view()["governance"]
        graph = copy.deepcopy(GRAPH)
        graph["claims"][0]["text"] = "revised scope"
        self.assertEqual(self.action("graph", payload=graph)["type"], "Allow")
        after = self.view()["governance"]
        self.assertEqual(after["state"], "approved")
        self.assertEqual(after["proposal_digest"], before["proposal_digest"])
        self.assertEqual(after["plan_revision"], before["plan_revision"])
        self.assertEqual(self.action("investigate")["type"], "Allow")

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
        governed = governance.revise(state.goal, state.governance)
        historical = replace(state, selected_graph_artifact_id=None, governance=governed)
        self.service._coordinator._commit(key, entry.revision, snapshot, historical, ())
        self.assertIsNone(self.presentation()["scope"])
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
        conflicting = copy.deepcopy(decision)
        conflicting["submission"]["action"] = "reject"
        self.assertEqual(self.admit(conflicting)["type"], "Block")
        self.assertEqual(self.runs.data, after)
        key = next(iter(self.runs.data))
        decoded = classify_and_decode(self.runs.data[key].value)
        self.assertEqual(decoded.kind, "valid")
        traverse_history(decoded.state, self.artifacts.read(key))


    def test_claude_current_model_sequences_attribute_subsequent_artifacts(self):
        from adapters.claude import lifecycle
        self.prepare()
        self.assertEqual(self.admit(self.decision())["type"], "Allow")
        self.assertEqual(self.action("investigate")["type"], "Allow")
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "main.jsonl"
            sequences = (
                ("claude-opus-4-6", "claude-sonnet-4-6", "claude-opus-4-6"),
                ("claude-opus-4-6", "claude-sonnet-4-6", "claude-opus-4-6",
                 "claude-sonnet-4-6"),
                ("claude-opus-4-6", None),
            )
            expected = ("claude-opus-4-6", "claude-sonnet-4-6", None)
            with mock.patch.dict(protocol._PROFILES[PROFILE],
                                 {"approval_ingress": "mcp_elicitation"}), mock.patch.object(
                lifecycle.application_bridge, "trusted_governance_context",
                side_effect=lambda _profile, run_id, payload:
                    self.service.trusted_governance_context(run_id=run_id, payload={
                        **payload, "author": lifecycle.application_bridge._identity(payload["author"])}),
            ):
                for index, models in enumerate(sequences):
                    transcript.write_text("\n".join(json.dumps({"message": {
                        "role": "assistant", "model": model, "content": "served"}})
                        if model is not None else json.dumps({"message": {
                            "role": "assistant", "content": [{"type": "tool_use", "name": "tool"}]}})
                        for model in models) + "\n")
                    lifecycle._governance_context(
                        {"transcript_path": str(transcript)}, self.run_id)
                    admitted = self.action("research", claim_id="C0", source_kind="code",
                        result="supports", payload={"source_ref": f"sequence-{index}",
                        "citation": "Current producer observation."})
                    self.assertEqual(admitted["type"], "Allow")
        key = next(iter(self.runs.data))
        history = traverse_history(classify_and_decode(self.runs.data[key].value).state,
                                   self.artifacts.read(key))
        research = [item for item in history if item.get("kind") == "research"][-3:]
        self.assertEqual([item["producer"]["model_id"] if item["producer"] else None
                          for item in research], list(expected))

    def test_configuration_amendment_makes_prior_decision_stale(self):
        self.prepare()
        prior = self.decision("prior")
        before = self.view()["governance"]
        self.assertEqual(self.action("configure_run", budgets={"max_passes": 9})["type"], "Allow")
        current = self.view()["governance"]
        self.assertGreater(current["plan_revision"], before["plan_revision"])
        self.assertNotEqual(current["proposal_digest"], before["proposal_digest"])
        stale = self.admit(prior)
        self.assertEqual(stale["type"], "Block")
        self.assertEqual(stale["reasons"][0]["code"], "governance.stale_proposal")

    def test_host_ui_final_outcome_without_submission_conflicts(self):
        self.prepare()
        g = self.view()["governance"]
        payload = {"run_id": self.run_id, "receipt_id": "missing-submission",
                   "proposal_digest": g["proposal_digest"], "plan_revision": g["plan_revision"],
                   "approval_kind": "host_ui", "outcome": "approve"}
        conflict = self.admit(payload)
        self.assertEqual(conflict["type"], "Block")
        self.assertEqual(conflict["reasons"][0]["code"], "governance.decision_conflict")

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


    def test_auto_explicit_cannot_raise_ceiling_and_graph_does_not_change_digest(self):
        self.run_id = self.request({"type": "StartRun", "goal": "auto task", "control_mode": "auto",
                                    "invocation": {**TEST_INVOCATION, "signal": "test operator"},
                                    "selector": {"project": "p", "session": "auto"}})["run"]["id"]
        self.prepare()
        g = self.view()["governance"]
        raw = {"run_id": self.run_id, "receipt_id": "human-in-auto", "proposal_digest": g["proposal_digest"],
               "plan_revision": g["plan_revision"], "approval_kind": "auto",
               "submission": {"action": "approve", "configuration": g["proposal"]}}
        before = copy.deepcopy(self.runs.data)
        denied = self.admit(raw)
        self.assertEqual(denied["type"], "Block")
        self.assertEqual(denied["reasons"][0]["code"], "governance.approval_unavailable")
        self.assertEqual(self.runs.data, before)
        self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")
        self.assertEqual(self.action("configure_run", budgets={"max_audit_spawns": 2})["reasons"][0]["code"], "governance.auto_ceiling")
        approved = self.view()["governance"]
        graph = copy.deepcopy(GRAPH)
        graph["claims"][0]["text"] = "changed work"
        self.assertEqual(self.action("graph", payload=graph)["type"], "Allow")
        current = self.view()["governance"]
        self.assertEqual(current["proposal_digest"], approved["proposal_digest"])
        self.assertEqual(current["plan_revision"], approved["plan_revision"])



    def test_honest_stop_without_graph_and_strict_old_state(self):
        self.assertEqual(self.request({"type": "EvaluateRun", "run_id": self.run_id, "intent": "stop"})["type"], "Allow")
        raw = copy.deepcopy(next(iter(self.runs.data.values())).value)
        del raw["governance"]
        self.assertEqual(classify_and_decode(raw).kind, "current_corrupt")

    def audit_ready(self):
        from governance_setup import approve_current
        from adapters.audit_protocol import AuditProtocol
        from adapters.identity import observe
        self.prepare()
        approve_current(self.service._coordinator, self.run_id)
        self.assertEqual(self.action("investigate")["type"], "Allow")
        self.assertEqual(self.action("research", claim_id="C0", source_kind="code", result="supports",
                                    payload={"source_ref": "supplied", "citation": "observed"})["type"], "Allow")
        c = self.service._coordinator
        protocol = AuditProtocol(PROFILE, dispatch=lambda r, _p: self.service.dispatch(r),
            child_event_ingress=lambda _p, r, ch, v: c.trusted_child_event(r, ch, v),
            attribution_ingress=lambda _p, r, v: c.trusted_attribution(
                r, {**v, **observe(v.get("provider_id"), v.get("model_id"), source=v["source"]),
                    "observed_by": "host"}),
            verdict_ingress=lambda _p, r, ch, v: c.trusted_audit_verdict(r, ch, v),
            plan_ingress=lambda _p, r, ch: c.trusted_audit_plan(r, ch))
        plan = protocol.prepare(self.run_id, role_profile="empirica:empirica-auditor")
        protocol.observe_started(plan, "native-test")
        return protocol, plan

    def finish_audit(self, protocol, plan, provider, model):
        from adapters.audit_protocol import IdentityObservation
        from core.evaluation import audit_binding
        protocol.observe_reviewer(plan, "native-test",
            auditor=IdentityObservation(provider, model, "test-host"))
        c = self.service._coordinator
        key = next(iter(self.runs.data))
        state = classify_and_decode(self.runs.data[key].value).state
        snapshot = c._assemble(key, state, {"type": "GetArgument", "run_id": self.run_id}, require_graph=True)
        verdict = {"verdict": "pass", "findings": ["bound audit"], **audit_binding(snapshot)}
        self.assertTrue(protocol.observe_verdict(plan, "native-test", verdict))
        return self.request({"type": "EvaluateRun", "run_id": self.run_id,
                             "intent": "report_convergence"})

    def test_distinct_bound_audit_converges_with_raw_alias_provenance(self):
        protocol, plan = self.audit_ready()
        result = self.finish_audit(
            protocol, plan, "bedrock", "eu.anthropic.claude-opus-4-6-v1")
        self.assertTrue(result["converged"], result)
        key = next(iter(self.runs.data))
        history = traverse_history(classify_and_decode(self.runs.data[key].value).state,
                                   self.artifacts.read(key))
        self.assertTrue(any(a.get("model_id") == "eu.anthropic.claude-opus-4-6-v1" for a in history))
        research = next(a for a in history if a.get("kind") == "research")
        self.assertEqual(research["producer"]["identity"], AUTHOR["identity"])

    def test_same_class_reviewer_across_bedrock_spelling_is_blocked(self):
        protocol, plan = self.audit_ready()
        result = self.finish_audit(
            protocol, plan, "bedrock", "eu.anthropic.claude-sonnet-4-6")
        self.assertEqual(result["type"], "Block")
        self.assertEqual(result["reasons"][0]["code"], "audit.same_model")

    def test_mixed_covered_producers_are_blocked(self):
        from governance_setup import approve_current
        from adapters.audit_protocol import AuditProtocol
        from adapters.identity import observe
        self.prepare()
        approve_current(self.service._coordinator, self.run_id)
        self.assertEqual(self.action("investigate")["type"], "Allow")
        self.assertEqual(self.action("research", claim_id="C0", source_kind="code", result="supports",
                                    payload={"source_ref": "first", "citation": "first observer"})["type"], "Allow")
        context = {"author": AUDITOR, "ingress": "pi_ui"}
        self.assertIn(self.service.trusted_governance_context(
            run_id=self.run_id, payload=context)["result"]["type"], {"Allow", "Inert"})
        self.assertEqual(self.action("research", claim_id="C0", source_kind="code", result="supports",
                                    payload={"source_ref": "second", "citation": "second observer"})["type"], "Allow")
        c = self.service._coordinator
        protocol = AuditProtocol(PROFILE, dispatch=lambda r, _p: self.service.dispatch(r),
            child_event_ingress=lambda _p, r, ch, v: c.trusted_child_event(r, ch, v),
            attribution_ingress=lambda _p, r, v: c.trusted_attribution(
                r, {**v, **observe(v.get("provider_id"), v.get("model_id"), source=v["source"]),
                    "observed_by": "host"}),
            verdict_ingress=lambda _p, r, ch, v: c.trusted_audit_verdict(r, ch, v),
            plan_ingress=lambda _p, r, ch: c.trusted_audit_plan(r, ch))
        fresh = protocol.prepare(self.run_id, role_profile="empirica:empirica-auditor")
        protocol.observe_started(fresh, "native-test-2")
        result = self.finish_audit(protocol, fresh, "xai", "grok-4-20260101")
        argument = self.request({"type": "GetArgument", "run_id": self.run_id})
        self.assertEqual(argument["type"], "Allow")
        self.assertEqual(argument["argument"]["audit"]["independence"], "mixed")
        self.assertEqual(result["reasons"][0]["code"], "audit.producers_mixed")

    def test_private_bridge_audit_reject_refunds_and_relaunches(self):
        """quality #8: exercise the Pi private bridge audit_prepare -> audit_reject path through the
        real service. A rejected reservation yields a launch_rejected child, refunds the audit
        budget, and a subsequent prepare reserves a different child in the same run."""
        from adapters.pi import private_bridge
        from adapters.audit_protocol import AuditProtocol
        from adapters.identity import observe
        from governance_setup import approve_current
        self.prepare()
        approve_current(self.service._coordinator, self.run_id)
        self.assertEqual(self.action("investigate")["type"], "Allow")
        self.assertEqual(self.action("research", claim_id="C0", source_kind="code",
            result="supports", payload={"source_ref": "supplied",
                                        "citation": "observed"})["type"], "Allow")
        c = self.service._coordinator

        def make_protocol(profile, **_kw):
            return AuditProtocol(profile, dispatch=lambda r, _p: self.service.dispatch(r),
                child_event_ingress=lambda _p, r, ch, v: c.trusted_child_event(r, ch, v),
                attribution_ingress=lambda _p, r, v: c.trusted_attribution(
                    r, {**v, **observe(v.get("provider_id"), v.get("model_id"), source=v["source"]),
                        "observed_by": "host"}),
                verdict_ingress=lambda _p, r, ch, v: c.trusted_audit_verdict(r, ch, v),
                plan_ingress=lambda _p, r, ch: c.trusted_audit_plan(r, ch))

        class BridgeShim:
            def trusted_audit_plan(self, _profile, run_id, child_id):
                return c.trusted_audit_plan(run_id, child_id)

        def used():
            return self.view()["governance"]["budgets"]["audit_spawns_used"]

        with mock.patch.object(private_bridge, "AuditProtocol", make_protocol), \
             mock.patch.object(private_bridge, "bridge", BridgeShim()):
            prepared = private_bridge._dispatch(PROFILE, {"operation": "audit_prepare",
                "run_id": self.run_id, "role_profile": "empirica:empirica-auditor"}, {})
            self.assertEqual(prepared["type"], "audit_plan")
            plan = prepared["plan"]
            self.assertEqual(used(), 1)
            rejected = private_bridge._dispatch(PROFILE, {"operation": "audit_reject",
                "run_id": self.run_id, "plan": plan}, {})
            self.assertEqual(rejected["type"], "audit_terminal")
            child = next(ch for ch in self.view()["children"]
                         if ch["child_id"] == plan["child_id"])
            self.assertEqual(child["state"], "launch_rejected")
            self.assertEqual(used(), 0)  # the audit budget is refunded
            again = private_bridge._dispatch(PROFILE, {"operation": "audit_prepare",
                "run_id": self.run_id, "role_profile": "empirica:empirica-auditor"}, {})
            self.assertEqual(again["type"], "audit_plan")
            self.assertNotEqual(again["plan"]["child_id"], plan["child_id"])
            self.assertEqual(used(), 1)



    def test_old_inventory_shape_fails_closed_and_fresh_generation_opens(self):
        self.prepare()
        self.admit(self.decision())
        before = copy.deepcopy(self.runs.data)
        bad_context = {**CONTEXT, "inventory": {"members": [], "source": "unknown", "complete": False, "authorized": False}}
        self.assertEqual(self.service.trusted_governance_context(run_id=self.run_id, payload=bad_context)["result"]["type"], "Fault")
        self.assertEqual(self.runs.data, before)
        key = next(iter(self.runs.data))
        old = copy.deepcopy(self.runs.data[key].value)
        old["governance"]["context"]["inventory"] = bad_context["inventory"]
        self.runs.data[key] = type(self.runs.data[key])(self.runs.data[key].revision, old)
        self.assertEqual(self.request({"type": "GetRun", "run_id": self.run_id})["reasons"][0]["code"], "run.corrupt")
        fresh = self.request({"type": "StartRun", "goal": "fresh", "selector": {"project": "p", "session": "s"}})
        self.assertEqual(fresh["type"], "Allow")
        self.assertNotEqual(fresh["run"]["id"], self.run_id)

    def test_canonical_graph_order_preserves_consent_and_oversize_cannot_replace_scope(self):
        self.prepare()
        graph = copy.deepcopy(GRAPH)
        graph["claims"].append({"id": "C1", "text": "dependent uncertainty", "gating": True, "kind": "ordinary"})
        graph["edges"] = [{"from": "C0", "to": "C1", "type": "SupportedBy"}]
        self.assertEqual(self.action("graph", payload=graph)["type"], "Allow")
        self.assertEqual(self.admit(self.decision())["type"], "Allow")
        before = self.view()["governance"]
        graph["claims"].reverse()
        graph["claims"][0]["text"] = "changed graph content"
        self.assertEqual(self.action("graph", payload=graph)["type"], "Allow")
        current = self.view()["governance"]
        for key in ("state", "proposal_digest", "plan_revision", "approved_digest", "approval_kind"):
            self.assertEqual(current[key], before[key])
        persisted = copy.deepcopy(self.runs.data)
        graph["claims"][0]["text"] = "x" * 2049
        self.assertIn(self.action("graph", payload=graph)["type"], {"Fault", "Block"})
        self.assertEqual(self.runs.data, persisted)

    def test_pair_only_context_and_digest_ignore_environment(self):
        import os
        self.prepare()
        digest = self.view()["governance"]["proposal_digest"]
        with mock.patch.dict(os.environ, {"EMPIRICA_GOVERNANCE_CONFIG": "/must/not/be/read"}):
            result = self.service.trusted_governance_context(run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
        self.assertEqual(result["result"]["type"], "Inert")
        self.assertEqual(self.view()["governance"]["proposal_digest"], digest)


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
        final = {k: v for k, v in presents[2].items() if k != "outcome"}
        final["submission"] = {"action": "approve", "configuration": self.view()["governance"]["proposal"]}
        for changed in ({"run_id": "other"}, {"plan_revision": 1000}, {"proposal_digest": "sha256:" + "a" * 64}, {"approval_kind": "auto"}):
            self.assertEqual(self.admit({**final, **changed})["type"], "Block")
            self.assertEqual(self.runs.data, before)
        self.assertEqual(self.admit(final)["run"]["governance"]["state"], "approved")
        before = copy.deepcopy(self.runs.data)
        self.assertEqual(self.admit(final)["type"], "Inert")
        self.assertEqual(self.admit(presents[2])["type"], "Inert")
        conflicting = copy.deepcopy(final)
        conflicting["submission"]["action"] = "reject"
        self.assertEqual(self.admit(conflicting)["type"], "Block")
        self.assertEqual(self.runs.data, before)
        raw = copy.deepcopy(next(iter(self.runs.data.values())).value)
        raw["governance"]["receipts"][0].pop("presentation_fingerprint")
        self.assertEqual(classify_and_decode(raw).kind, "current_corrupt")

    def test_dismiss_receipt_replay_and_graph_changes_do_not_reset_presentation_cap(self):
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
        graph = copy.deepcopy(GRAPH)
        graph["claims"][0]["text"] = "graph edit does not create a configuration epoch"
        self.assertEqual(self.action("graph", payload=graph)["type"], "Allow")
        for n in range(2):
            self.assertEqual(self.admit(self.decision(str(n), "present", reserve=False))["type"], "Allow")
        g = self.view()["governance"]
        self.assertEqual(g["interactions_remaining"]["proposal"], 0)
        self.assertEqual(g["prompt_error"], "governance.interaction_limit")
        self.assertEqual(self.admit(self.decision("overflow", "dismiss"))["reasons"][0]["code"],
                         "governance.interaction_limit")
        self.assertEqual(self.admit(first)["type"], "Inert")


    def test_raw_submission_conflict_and_amendment_replay(self):
        self.prepare()
        g = self.view()["governance"]
        envelope = {"run_id": self.run_id, "receipt_id": "raw", "proposal_digest": g["proposal_digest"],
                    "plan_revision": g["plan_revision"], "approval_kind": "host_ui"}
        self.assertEqual(self.admit({**envelope, "outcome": "present"})["type"], "Allow")
        conflict = {**envelope, "submission": {"action": "approve", "feedback": "approved",
            "configuration": g["proposal"]}}
        blocked = self.admit(conflict)
        self.assertEqual(blocked["type"], "Fault")
        self.assertIsNone(self.view()["governance"]["approved_digest"])

        envelope["receipt_id"] = "raw-amend"
        self.assertEqual(self.admit({**envelope, "outcome": "present"})["type"], "Allow")
        proposal = copy.deepcopy(g["proposal"])
        proposal["budgets"]["max_passes"] = 6
        raw = {**envelope, "submission": {"action": "approve",
                                           "configuration": proposal}}
        amended = self.admit(raw)
        self.assertEqual(amended["type"], "Allow")
        self.assertEqual(amended["run"]["governance"]["proposal"]["budgets"]["max_passes"], 6)
        self.assertEqual(self.admit(raw)["type"], "Inert")
        self.assertIsNone(self.view()["governance"]["approved_digest"])

    def test_start_requires_goal_and_attested_auto_authority(self):
        base = {"type": "StartRun", "selector": {"project": "p", "session": "provenance"},
                "invocation": {"host": "test", "interactive": False,
                               "signal": "test noninteractive", "delegation": False}}
        for goal in ("", "   "):
            result = self.request({**base, "goal": goal})
            self.assertEqual(result["reasons"][0]["code"], "run.goal_required")
        denied = self.request({**base, "goal": "auto", "control_mode": "auto"})
        self.assertEqual(denied["reasons"][0]["code"], "governance.auto_invocation_required")
        for suffix, interactive in (("noninteractive", False), ("unknown", None)):
            invocation = {**base["invocation"], "interactive": interactive}
            allowed = self.request({**base, "selector": {"project": "p", "session": suffix},
                                    "goal": "deliberative", "invocation": invocation})
            self.assertEqual(allowed["type"], "Allow")
            self.assertEqual(allowed["run"]["invocation"], invocation)
        for suffix, invocation in (("interactive", {**base["invocation"], "interactive": True}),
                                   ("delegated", {**base["invocation"], "delegation": True})):
            command = {**base, "selector": {"project": "p", "session": suffix},
                       "goal": "  verbatim goal  ", "control_mode": "auto", "invocation": invocation}
            allowed = self.request(command)
            self.assertEqual(allowed["run"]["goal"], "  verbatim goal  ")
            self.assertEqual(allowed["run"]["invocation"], invocation)

    def test_graphless_dialog_shows_read_only_goal_and_invocation(self):
        dialog = self.presentation()["dialog"]
        self.assertEqual(dialog["goal"], "governed task")
        self.assertEqual(dialog["invocation"], TEST_INVOCATION)
        self.assertNotIn("scope", dialog)

    def test_maximal_escaped_graph_amendment_round_trip(self):
        import json
        from core.evaluation import valid_graph
        self.prepare()
        # Maximal claim ids: 64 characters, the claimId ceiling (ids are ASCII by contract).
        ids = [f"{n:02d}".ljust(64, "x") for n in range(32)]
        self.assertTrue(all(len(key) == 64 for key in ids))
        claims = [{"id": key, "text": "\U0001f600" * 2048, "kind": "needs-experiment", "gating": False} for key in ids]
        pairs = [(0, n) for n in range(1, 32)] + [(a, b) for a in range(1, 32) for b in range(a + 1, 32)]
        graph = {"root": ids[0], "claims": claims, "edges": [{"from": ids[a], "to": ids[b], "type": "SupportedBy"} for a, b in pairs[:128]]}
        self.assertTrue(valid_graph(graph))
        raw = json.dumps(graph, ensure_ascii=True)
        # Every text is 2048 astral code points, each escaped to 12 ASCII characters.
        self.assertGreater(len(raw), 32 * 2048 * 12)
        self.assertEqual(self.action("graph", payload=json.loads(raw))["type"], "Allow")
        self.assertEqual(self.presentation()["scope"], graph)
        for text in ('"' * 2048, "\\" * 2048, "\u0000" * 2048):
            graph["claims"][0]["text"] = text
            self.action("graph", payload=graph)
            presentation = self.presentation()
            self.assertEqual(presentation["scope"], graph)
            self.assertNotIn(text, json.dumps(presentation["dialog"]))


if __name__ == "__main__":
    unittest.main()
