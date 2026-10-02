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
from types import SimpleNamespace
from application.v2 import compose
from application import protocol
from core import governance
from core.run import start_admission
from application.location import decode_handle
from application.run_state import classify_and_decode
from application.snapshot import traverse_history
from test_d7_transactions import Runs, Artifacts, Workspace, Harness
from governance_setup import (AUTHOR, AUDITOR, TEST_INVOCATION, SIZED_RATIONALE,
                              sized_configure_run)

PROFILE = "pi@0.84.1+pi-subagents@0.50.0"
GRAPH = {"root": "C0", "claims": [{"id": "C0", "text": "supplied uncertainty", "gating": True,
                                   "kind": "ordinary"}], "edges": []}
CONTEXT = {"author": AUTHOR, "ingress": "pi_ui"}
# ADR-0063: configure_run must carry all three ceilings and a nonblank rationale.
SIZED_BUDGETS = {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}
DELEGATED_INVOCATION = {"host": "test", "interactive": False,
                        "signal": "operator", "delegation": True}


class HostProfileApprovalTests(unittest.TestCase):
    def _profiles_document(self):
        root = Path(__file__).resolve().parents[3]
        return json.loads((root / "contracts/empirica/v2/host-profiles.json").read_text())

    def _profile(self, host_id):
        return next(p for p in self._profiles_document()["profiles"] if p["host_id"] == host_id)

    def _document_of(self, profile):
        """A schema-complete host-profiles document holding only ``profile`` (levels from the contract)."""
        return {"protocol": "empirica/v2", "thinking_levels": self._profiles_document()["thinking_levels"],
                "profiles": [profile]}

    def _admit_context(self, profile, ingress):
        """Compose a real service for ``profile`` and submit one governance context."""
        with mock.patch.dict(protocol._PROFILES, {profile["profile_id"]: profile}):
            service = compose(Workspace(), Harness(), Runs(), Artifacts(), None,
                              profile["profile_id"], {}, None)
            result = service.dispatch({"protocol": "empirica/v2", "request_id": "start",
                "command": {"type": "StartRun", "control_mode": "deliberative", "goal": "synthetic profile",
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
        jsonschema.validate(self._document_of(synthetic), schema)
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
        jsonschema.validate(self._document_of(synthetic), schema)
        admitted = self._admit_context(synthetic, "unavailable")
        self.assertEqual(admitted["type"], "Allow", admitted)
        self.assertEqual(admitted["run"]["governance"]["context"]["approval_capability"],
                         "unavailable")
        self.assertEqual(self._admit_context(synthetic, "pi_ui")["type"], "Block")


class StartAdmissionTests(unittest.TestCase):
    def test_core_boundary_has_no_environment_or_host_dependencies(self):
        import ast
        core = Path(__file__).resolve().parents[1] / "core"
        forbidden_literals = ("claude", "codex", "pi_ui", "mcp_elicitation")
        for path in core.glob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            imports = {alias.name.split(".")[0] for node in ast.walk(tree)
                       if isinstance(node, (ast.Import, ast.ImportFrom))
                       for alias in (node.names if isinstance(node, ast.Import) else
                                     [SimpleNamespace(name=node.module or "")])}
            self.assertNotIn("os", imports, path)
            source = path.read_text().lower()
            for literal in forbidden_literals:
                self.assertNotIn(literal, source, (path, literal))

    def test_default_ceiling_policy_is_immutable(self):
        with self.assertRaises(TypeError):
            governance.DEFAULT_CEILINGS["max_passes"] = 9
        self.assertEqual(dict(governance.DEFAULT_CEILINGS), SIZED_BUDGETS)

    def test_start_admission_table(self):
        full = dict(governance.DEFAULT_CEILINGS)
        cases = [
            ({"goal": "", "control_mode": "deliberative", "invocation": TEST_INVOCATION},
             "run.goal_required"),
            ({"goal": "goal", "control_mode": "auto",
              "invocation": {**TEST_INVOCATION, "interactive": False, "delegation": False}},
             "governance.auto_invocation_required"),
            ({"goal": "goal", "control_mode": "auto", "invocation": TEST_INVOCATION}, None),
            ({"goal": "goal", "control_mode": "deliberative",
              "invocation": {**TEST_INVOCATION, "interactive": False}}, None),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(
                    start_admission({**command, "budgets": full}, full), expected)

    def test_sources_are_completed_once_without_widening_the_operator_limit(self):
        from application.transaction import complete_start_ceilings
        defaults = dict(governance.DEFAULT_CEILINGS)
        command = {"type": "StartRun", "goal": "g"}
        completed, limits = complete_start_ceilings(command, {})
        self.assertEqual((completed["budgets"], limits), (defaults, defaults))
        self.assertNotIn("budgets", command)
        completed, limits = complete_start_ceilings(command, {"max_passes": 2})
        self.assertEqual(limits, {**defaults, "max_passes": 2})
        self.assertEqual(completed["budgets"], {**defaults, "max_passes": 2})
        completed, limits = complete_start_ceilings(
            {**command, "budgets": {"max_spawns": 0}}, {"max_passes": 2})
        self.assertEqual(completed["budgets"], {**defaults, "max_passes": 2, "max_spawns": 0})


def _seed_budgets(**over):
    b = {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2,
         "passes_used": 0, "spawns_used": 0, "audit_spawns_used": 0}
    b.update(over)
    return b


def _placeholder(control_mode="deliberative", invocation=None):
    invocation = ({"host": "test", "interactive": True,
                   "signal": "test", "delegation": False}
                  if invocation is None else invocation)
    delegated = (control_mode == "auto" and invocation["interactive"] is not True
                 and invocation["delegation"] is True)
    envelope = _seed_budgets() if delegated else None
    return governance.initial("goal", _seed_budgets(), control_mode, invocation, envelope)


def _sized(gov, budgets, rationale):
    proposal = {"budgets": dict(budgets), "rationale": rationale}
    return governance.revise("goal", gov, proposal=proposal)


def _capable(gov, capability=governance.HUMAN_CONFIGURATION, ingress="pi_ui"):
    ctx = {**gov["context"], "approval_capability": capability, "ingress": ingress, "author": AUTHOR}
    return governance.revise("goal", gov, context=ctx)


def _opstate(gov, **budgets):
    return SimpleNamespace(budgets=_seed_budgets(**budgets), governance=gov)


def _invariant_doc(gov, **budgets):
    return {"invocation": {"delegation": gov["context"]["delegation"],
                           "interactive": gov["context"]["interactive"]},
            "budgets": _seed_budgets(**budgets), "governance": gov}


_APPROVE_ACTIONS = (("approve", {"changed_outcome": "amend", "unchanged_outcome": "approve"}),)


class GovernanceTransitionMatrixTests(unittest.TestCase):
    """ADR-0063 Confirmation: the core policy transition matrix and every listed negative."""

    # --- placeholder is unapprovable ---
    def test_placeholder_is_unsized_and_unapprovable(self):
        g = _placeholder()
        self.assertFalse(governance.sized(g))
        self.assertIsNone(g["proposal"]["rationale"])
        decision = {"run_id": "r", "receipt_id": "x", "proposal_digest": g["proposal_digest"],
                    "plan_revision": 0, "approval_kind": "host_ui", "outcome": "present"}
        self.assertEqual(governance.decision_error("r", _capable(g), decision),
                         "governance.approval_required")
        submission = {k: v for k, v in decision.items() if k != "outcome"}
        submission["submission"] = {"action": "approve", "configuration": {
            "budgets": g["proposal"]["budgets"]}}
        self.assertEqual(governance.decision_error("r", _capable(g), submission),
                         "governance.approval_required")

    # --- initial proposal above the seed is allowed before the first approval ---
    def test_initial_proposal_above_seed_allowed_before_first_approval(self):
        for mode, inv in (("deliberative", {"interactive": True, "delegation": False}),
                         ("auto", {"interactive": True, "delegation": True})):
            gov = _sized(_placeholder(mode, inv),
                         {**SIZED_BUDGETS, "max_passes": 1024, "max_spawns": 128},
                         SIZED_RATIONALE)
            self.assertIsNone(governance.configuration_error(_opstate(gov), gov["proposal"]), mode)

    # --- delegated envelope caps proposals above 8/1/2 before the first approval ---
    def test_delegated_envelope_refuses_oversize_before_first_approval(self):
        gov = _sized(_placeholder("auto", {"interactive": False, "delegation": True}),
                     {**SIZED_BUDGETS, "max_passes": 9}, SIZED_RATIONALE)
        self.assertEqual(governance.configuration_error(_opstate(gov), gov["proposal"]),
                         "governance.auto_ceiling")
        within = _sized(_placeholder("auto", {"interactive": False, "delegation": True}),
                        SIZED_BUDGETS, SIZED_RATIONALE)
        self.assertIsNone(governance.configuration_error(_opstate(within), within["proposal"]))

    # --- a later raise after a decrease is refused (post-approval auto) ---
    def test_post_approval_auto_refuses_raise_after_decrease(self):
        gov = _sized(_placeholder("auto", {"interactive": True, "delegation": False}),
                     {**SIZED_BUDGETS, "max_passes": 6}, SIZED_RATIONALE)
        gov = governance.plain(gov)
        gov.update(state="approved", approved_digest=gov["proposal_digest"],
                   approval_kind="host_ui", first_approval=True)
        state = _opstate(gov, max_passes=6, passes_used=0)
        lowered = {"budgets": {"max_passes": 5, "max_spawns": 1, "max_audit_spawns": 2},
                    "rationale": gov["proposal"]["rationale"]}
        self.assertIsNone(governance.configuration_error(state, lowered))  # decrease accepted
        raised = {"budgets": {"max_passes": 7, "max_spawns": 1, "max_audit_spawns": 2},
                  "rationale": gov["proposal"]["rationale"]}
        self.assertEqual(governance.configuration_error(state, raised), "governance.auto_ceiling")

    # --- delegated lower-then-raise is refused ---
    def test_delegated_lower_then_raise_refused(self):
        gov = _sized(_placeholder("auto", {"interactive": False, "delegation": True}),
                     {**SIZED_BUDGETS, "max_passes": 6}, SIZED_RATIONALE)
        gov = governance.plain(gov)
        gov.update(state="approved", approved_digest=gov["proposal_digest"],
                   approval_kind="auto", first_approval=True)
        state = _opstate(gov, max_passes=6, passes_used=0)
        raised = {"budgets": {"max_passes": 7, "max_spawns": 1, "max_audit_spawns": 2},
                  "rationale": gov["proposal"]["rationale"]}
        self.assertEqual(governance.configuration_error(state, raised), "governance.auto_ceiling")

    # --- inflation through a seed or environment override is refused for delegated auto only ---
    def test_start_admission_envelope_binds_delegated_auto_only(self):
        delegated = {"host": "t", "interactive": False, "signal": "op", "delegation": True}
        interactive = {"host": "t", "interactive": True, "signal": "op", "delegation": True}
        deliberative = {"host": "t", "interactive": True, "signal": "op", "delegation": False}
        over = {**governance.DEFAULT_CEILINGS, "max_passes": 9}
        # delegated auto: enlarging the 8/1/2 envelope is contradictory
        self.assertEqual(start_admission({"goal": "g", "control_mode": "auto",
                                           "invocation": delegated, "budgets": over},
                                          over),
                         "governance.budget_contradictory")
        # Interactive auto and deliberative sizes come from proposal + approval.
        for inv in (interactive, deliberative):
            self.assertIsNone(start_admission({"goal": "g",
                "control_mode": "auto" if inv is interactive else "deliberative",
                "invocation": inv, "budgets": over}, over))

    # --- consumed-counter minimums refuse a ceiling below what is used ---
    def test_consumed_counter_minimums_refuse_below_used(self):
        gov = _sized(_placeholder(), SIZED_BUDGETS, SIZED_RATIONALE)
        state = _opstate(gov, passes_used=3, max_passes=8)
        below = {"budgets": {"max_passes": 2, "max_spawns": 1, "max_audit_spawns": 2},
                 "rationale": gov["proposal"]["rationale"]}
        self.assertEqual(governance.configuration_error(state, below), "governance.budget_invalid")

    # --- revision exhaustion: eight material revisions per allowance ---
    def test_revision_exhaustion_refuses_ninth_material_revision(self):
        gov = _sized(_placeholder("auto", {"interactive": False, "delegation": True}),
                     SIZED_BUDGETS, SIZED_RATIONALE)
        gov = governance.plain(gov)
        gov.update(state="approved", approved_digest=gov["proposal_digest"],
                   approval_kind="auto", first_approval=True)
        gov["post_approval_revisions"] = governance.MAX_REVISIONS
        raised = {"budgets": {"max_passes": 7, "max_spawns": 1, "max_audit_spawns": 2},
                  "rationale": "a distinct revision"}
        self.assertEqual(governance.revision_error(gov, "goal", raised),
                         "governance.revision_exhausted")
        # an exact no-op replay does not count
        self.assertIsNone(governance.revision_error(gov, "goal", gov["proposal"]))

    # --- expected approval kind per phase ---
    def test_expected_approval_kind_matrix(self):
        self.assertEqual(governance.expected_approval_kind(_placeholder("deliberative")), "host_ui")
        delegated = _placeholder("auto", {"interactive": False, "delegation": True})
        self.assertEqual(governance.expected_approval_kind(delegated), "auto")
        interactive = _sized(_placeholder("auto", {"interactive": True, "delegation": True}),
                             SIZED_BUDGETS, SIZED_RATIONALE)
        self.assertEqual(governance.expected_approval_kind(interactive), "host_ui")
        interactive = governance.plain(interactive)
        interactive["first_approval"] = True
        self.assertEqual(governance.expected_approval_kind(interactive), "auto")

    # --- auto decisions without delegated or prior-human authority are refused ---
    def test_auto_decision_without_authority_refused(self):
        cases = (
            _capable(_sized(_placeholder(), SIZED_BUDGETS, SIZED_RATIONALE)),
            _sized(_placeholder("auto", {"interactive": False, "delegation": False}),
                   SIZED_BUDGETS, SIZED_RATIONALE),
        )
        for gov in cases:
            decision = {"run_id": "r", "receipt_id": "x",
                        "proposal_digest": gov["proposal_digest"],
                        "plan_revision": gov["plan_revision"], "approval_kind": "auto",
                        "outcome": "approve"}
            self.assertEqual(governance.decision_error("r", gov, decision),
                             "governance.approval_unavailable")

    # --- a human amendment keeps the rationale ---
    def test_amendment_keeps_rationale(self):
        gov = _sized(_placeholder(), SIZED_BUDGETS, SIZED_RATIONALE)
        submission = {"action": "approve", "configuration": {"budgets": {"max_passes": 6,
                        "max_spawns": 1, "max_audit_spawns": 2}}}
        resolved, reason = governance.resolve_submission(gov, submission, _APPROVE_ACTIONS)
        self.assertIsNone(reason)
        self.assertEqual(resolved["outcome"], "amend")
        self.assertEqual(resolved["configuration"]["rationale"], gov["proposal"]["rationale"])

    # --- kind-forged receipts fail the invariant ---
    def test_kind_forged_receipt_fails_invariant(self):
        gov = _sized(_placeholder("auto", {"interactive": False, "delegation": True}),
                     SIZED_BUDGETS, SIZED_RATIONALE)
        gov = governance.plain(gov)
        gov.update(state="approved", approved_digest=gov["proposal_digest"],
                   approval_kind="auto", first_approval=True)
        # A host_ui receipt forged into a delegated-auto run has no matching presentation
        # reservation; the invariant couples the fingerprint to the kind.
        receipt = {"id": "r1", "fingerprint": "f", "presentation_fingerprint": None,
                   "outcome": "approve", "plan_revision": gov["plan_revision"],
                   "proposal_digest": gov["proposal_digest"]}
        gov["receipts"] = [{**receipt, "approval_kind": "host_ui"}]
        self.assertFalse(governance.invariant(_invariant_doc(gov)))
        # a correct auto receipt at the exact approved revision and digest passes
        gov["receipts"] = [{**receipt, "approval_kind": "auto"}]
        self.assertTrue(governance.invariant(_invariant_doc(gov)))

    # --- a zero-audit proposal is a valid sized proposal ---
    def test_zero_audit_proposal_is_sized(self):
        gov = _sized(_placeholder(),
                     {**SIZED_BUDGETS, "max_audit_spawns": 0}, SIZED_RATIONALE)
        self.assertTrue(governance.sized(gov))
        self.assertIsNone(governance.configuration_error(_opstate(gov), gov["proposal"]))


class GovernanceServiceTests(unittest.TestCase):
    def setUp(self):
        self.runs, self.artifacts = Runs(), Artifacts()
        self.service = compose(Workspace(), Harness(), self.runs, self.artifacts, None, PROFILE, {}, None)
        self.run_id = self.request({"type": "StartRun", "control_mode": "deliberative", "goal": "governed task",
                                    "invocation": dict(TEST_INVOCATION),
                                    "selector": {"project": "p", "session": "s"}})["run"]["id"]

    def request(self, command):
        return self.service.dispatch({"protocol": "empirica/v2", "request_id": "test", "command": command})["result"]

    def test_missing_start_invocation_fails_through_normal_dispatch(self):
        result = self.request({"type": "StartRun", "control_mode": "deliberative", "goal": "missing provenance",
                               "selector": {"project": "p", "session": "missing"}})
        self.assertEqual(result["type"], "Fault")
        self.assertEqual(result["code"], "invalid_request")
        self.assertEqual(result["fail_direction"], "closed")

    def action(self, kind, **kwargs):
        action = {"kind": kind, **kwargs}
        return self.request({"type": "ObserveAction", "run_id": self.run_id,
                             "action": action})

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
            # A human submission carries ceilings only; the schema forbids injecting a
            # rationale (ADR-0063), so strip it from the current proposal.
            source = kwargs.get("amendment", {}).get("configuration", g["proposal"])
            proposal = {"budgets": copy.deepcopy(source["budgets"])}
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
        self.assertEqual(self.action(**sized_configure_run(
            budgets=SIZED_BUDGETS, rationale=SIZED_RATIONALE))["type"], "Allow")
        result = self.service.trusted_governance_context(run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
        self.assertEqual(result["result"]["type"], "Allow", result)

    def test_escaped_rationale_bound_survives_private_response_validation(self):
        self.assertEqual(self.action("route", reason="supplied context")["type"], "Allow")
        self.assertEqual(self.action("graph", payload=copy.deepcopy(GRAPH))["type"], "Allow")
        cases = (("backslashes", "\\" * 600, 1200),
                 ("terminal controls", "\x1b" * 600, 2400),
                 ("unicode format controls", "\u202e" * 600, 3600))
        for label, rationale, escaped_length in cases:
            with self.subTest(label=label):
                configured = self.action(**sized_configure_run(
                    budgets=SIZED_BUDGETS, rationale=rationale))
                self.assertEqual(configured["type"], "Allow", configured)
                response = self.service.trusted_governance_context(
                    run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
                self.assertIn(response["result"]["type"], {"Allow", "Inert"}, response)
                escaped = response["result"]["presentation"]["dialog"]["rationale"]
                self.assertEqual(len(escaped), escaped_length)

    def test_placeholder_submission_refuses_with_approval_required_without_mutation(self):
        self.assertEqual(self.action("route", reason="supplied context")["type"], "Allow")
        self.assertEqual(self.action("graph", payload=copy.deepcopy(GRAPH))["type"], "Allow")
        self.service.trusted_governance_context(run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
        g = self.view()["governance"]
        self.assertFalse(g["display_ready"])
        payload = {"run_id": self.run_id, "receipt_id": "placeholder",
                   "proposal_digest": g["proposal_digest"],
                   "plan_revision": g["plan_revision"], "approval_kind": "host_ui",
                   "submission": {"action": "approve", "configuration": {
                       "budgets": g["proposal"]["budgets"]}}}
        before = copy.deepcopy(self.runs.data)
        blocked = self.admit(payload)
        self.assertEqual(blocked["type"], "Block")
        self.assertEqual(blocked["reasons"][0]["code"],
                         "governance.approval_required")
        self.assertFalse(blocked["run"]["governance"]["display_ready"])
        self.assertEqual(self.runs.data, before)

    def test_configure_run_schema_rejects_reviewer_field(self):
        before = copy.deepcopy(self.runs.data)
        result = self.action(**sized_configure_run(
            budgets=SIZED_BUDGETS, rationale=SIZED_RATIONALE), auditor=AUDITOR)
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
        proposed = self.action(**sized_configure_run(
            budgets=SIZED_BUDGETS, rationale=SIZED_RATIONALE))
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
        self.action(**sized_configure_run(budgets=SIZED_BUDGETS, rationale=SIZED_RATIONALE))
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
        self.action(**sized_configure_run(budgets=SIZED_BUDGETS, rationale=SIZED_RATIONALE))
        self.service.trusted_governance_context(run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
        self.assertEqual(self.admit(self.decision())["type"], "Allow")
        self.assertEqual(self.view()["next_actions"], ["route.record"])
        self.assertEqual(self.action("investigate")["reasons"][0]["code"], "route.required")

    def test_bootstrap_refresh_and_capacity_have_distinct_recovery(self):
        self.action("graph", payload=copy.deepcopy(GRAPH))
        self.action(**sized_configure_run(budgets=SIZED_BUDGETS, rationale=SIZED_RATIONALE))
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

    def test_auto_terminal_restore_has_restart_only_recovery_and_inert_private_decisions(self):
        self._prepare_auto(DELEGATED_INVOCATION)
        self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")
        terminal = self.request({"type": "EvaluateRun", "run_id": self.run_id,
                                 "intent": "stop"})
        self.assertEqual(terminal["run"]["status"], "stopped_residual")
        restored_service = self.service.reload()
        restored = restored_service.dispatch({"protocol": "empirica/v2", "request_id": "restore",
            "command": {"type": "GetRun", "run_id": self.run_id}})["result"]
        self.assertEqual(restored["type"], "Allow", restored)
        run = restored["run"]
        self.assertFalse(run["governance"]["display_ready"])
        self.assertNotIn("budget.raise", json.dumps(run))
        decision = self.decision("terminal", approval_kind="auto")
        private = restored_service.trusted_governance_decision(
            run_id=self.run_id, payload=decision)["result"]
        self.assertEqual(private["type"], "Inert")

    def test_interactive_auto_refuses_new_human_episode_while_ui_remains_capable(self):
        invocation = {"host": "test", "interactive": True,
                      "signal": "operator", "delegation": True}
        self._prepare_auto(invocation)
        self.assertEqual(self.admit(self.decision("first-human"))["type"], "Allow")
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 7},
            rationale=SIZED_RATIONALE))["type"], "Allow")
        self.assertEqual(self.view()["governance"]["context"]["approval_capability"],
                         governance.HUMAN_CONFIGURATION)
        presentation = self.decision("second-human", "present", reserve=False,
                                     approval_kind="host_ui")
        before = copy.deepcopy(self.runs.data)
        refused = self.admit(presentation)
        self.assertEqual(refused["type"], "Block")
        self.assertEqual(refused["reasons"][0]["code"],
                         "governance.approval_unavailable")
        self.assertEqual(self.runs.data, before)
        self.assertEqual(self.admit(self.decision(
            "automatic", approval_kind="auto"))["type"], "Allow")
        self.assertEqual(self.view()["governance"]["budgets"]["max_passes"], 7)

    def test_deliberative_post_approval_human_amendment_locks_exact_raise_without_counter_reset(self):
        self.prepare()
        self.assertEqual(self.admit(self.decision("first"))["type"], "Allow")
        self.assertEqual(self.action("investigate")["type"], "Allow")
        self.assertEqual(self.action(
            "research", claim_id="C0", source_kind="code", result="supports",
            payload={"source_ref": "counter-proof", "citation": "admitted evidence"})
            ["type"], "Allow")
        convergence = self.request({"type": "EvaluateRun", "run_id": self.run_id,
                                    "intent": "report_convergence"})
        self.assertEqual(convergence["type"], "Block")
        self.assertEqual(convergence["reasons"][0]["code"], "audit.required")
        for purpose, resource in (("investigation", "investigation"), ("audit", "audit")):
            admitted = self.action("child_reserve", purpose=purpose,
                                   resource_class=resource,
                                   role_profile="empirica:empirica-auditor",
                                   execution="foreground")
            self.assertEqual(admitted["type"], "Allow", admitted)
        counter_keys = ("passes_used", "spawns_used", "audit_spawns_used")
        counters = {key: self.view()["governance"]["budgets"][key]
                    for key in counter_keys}
        self.assertTrue(all(value > 0 for value in counters.values()), counters)
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 7},
            rationale=SIZED_RATIONALE))["type"], "Allow")
        self.assertEqual({key: self.view()["governance"]["budgets"][key]
                          for key in counter_keys}, counters)
        amended_budgets = {"max_passes": 12, "max_spawns": 3,
                           "max_audit_spawns": 4}
        amendment = self.decision("human-amendment", outcome="amend",
                                  amendment={"configuration": {"budgets": amended_budgets}})
        self.assertEqual(self.admit(amendment)["type"], "Allow")
        pending = self.view()["governance"]
        self.assertEqual(pending["state"], "revision_pending")
        self.assertEqual(pending["proposal"]["budgets"], amended_budgets)
        self.assertEqual({key: pending["budgets"][key] for key in counter_keys}, counters)
        self.assertEqual(self.admit(self.decision("locked-confirmation"))["type"], "Allow")
        installed = self.view()["governance"]["budgets"]
        self.assertEqual({key: installed[key] for key in amended_budgets}, amended_budgets)
        self.assertEqual({key: installed[key] for key in counter_keys}, counters)
        self.service = self.service.reload()
        reloaded = self.view()["governance"]["budgets"]
        self.assertEqual({key: reloaded[key] for key in counter_keys}, counters)

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
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 9}, rationale=SIZED_RATIONALE))["type"], "Allow")
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
        self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 12}, rationale=SIZED_RATIONALE))
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
               "submission": {"action": "approve", "configuration": {"budgets": g["proposal"]["budgets"]}}}
        before = copy.deepcopy(self.runs.data)
        denied = self.admit(raw)
        self.assertEqual(denied["type"], "Block")
        self.assertEqual(denied["reasons"][0]["code"], "governance.approval_unavailable")
        self.assertEqual(self.runs.data, before)
        self.assertEqual(self.admit(self.decision())["type"], "Allow")
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 9}, rationale=SIZED_RATIONALE))
            ["reasons"][0]["code"], "governance.auto_ceiling")
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

    def audit_protocol(self):
        from adapters.audit_protocol import AuditProtocol
        from adapters.identity import observe
        c = self.service._coordinator
        return AuditProtocol(PROFILE, dispatch=lambda r, _p: self.service.dispatch(r),
            child_event_ingress=lambda _p, r, ch, v: c.trusted_child_event(r, ch, v),
            attribution_ingress=lambda _p, r, v: c.trusted_attribution(
                r, {**v, **observe(v.get("provider_id"), v.get("model_id"), source=v["source"]),
                    "observed_by": "host"}),
            verdict_ingress=lambda _p, r, ch, v: c.trusted_audit_verdict(r, ch, v),
            plan_ingress=lambda _p, r, ch: c.trusted_audit_plan(r, ch))

    def audit_ready(self):
        from governance_setup import approve_current
        self.prepare()
        approve_current(self.service._coordinator, self.run_id)
        self.assertEqual(self.action("investigate")["type"], "Allow")
        self.assertEqual(self.action("research", claim_id="C0", source_kind="code", result="supports",
                                    payload={"source_ref": "supplied", "citation": "observed"})["type"], "Allow")
        protocol = self.audit_protocol()
        plan = protocol.prepare(self.run_id, role_profile="empirica:empirica-auditor")
        protocol.observe_started(plan, "native-test")
        return protocol, plan

    def finish_audit(self, protocol, plan, provider, model, *, verdict="pass",
                     findings=("bound audit",)):
        from adapters.audit_protocol import IdentityObservation
        from core.evaluation import audit_binding
        protocol.observe_reviewer(plan, "native-test",
            auditor=IdentityObservation(provider, model, "test-host"))
        c = self.service._coordinator
        key = next(iter(self.runs.data))
        state = classify_and_decode(self.runs.data[key].value).state
        snapshot = c._assemble(key, state, {"type": "GetArgument", "run_id": self.run_id}, require_graph=True)
        verdict = {"verdict": verdict, "findings": list(findings), **audit_binding(snapshot)}
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

    def fail_audit(self, protocol, plan):
        return self.finish_audit(protocol, plan, "bedrock", "eu.anthropic.claude-opus-4-6-v1",
                                 verdict="fail", findings=("citation names line 895, not 891",))

    def relaunch_audit(self, protocol):
        plan = protocol.prepare(self.run_id, role_profile="empirica:empirica-auditor")
        protocol.observe_started(plan, "native-test")
        return plan

    def assert_exhausted_audit(self, result):
        self.assertEqual(result["type"], "Block")
        self.assertEqual([(r["code"], r["parameters"]) for r in result["reasons"]],
                         [("budget.exhausted", {"resource": "audit_spawn"})])
        audit_row = next(row for row in result["run"]["obligations"]["active"]
                         if row["id"] == "obligation.audit")
        self.assertEqual(audit_row["missing"]["code"], "budget.exhausted")

    def test_failed_audit_names_audit_spawn_exhaustion_and_keeps_findings_visible(self):
        protocol, plan = self.audit_ready()
        result = self.fail_audit(protocol, plan)
        self.assertEqual(result["reasons"][0]["code"], "audit.failed")  # budget left: unchanged
        self.assertIn("child.retry", result["reasons"][0]["next_actions"])
        self.assert_exhausted_audit(self.fail_audit(protocol, self.relaunch_audit(protocol)))
        budgets = self.view()["governance"]["budgets"]
        self.assertEqual(budgets["audit_spawns_used"], budgets["max_audit_spawns"])
        audit = self.view()["audit"]
        self.assertEqual(audit["state"], "failed")
        self.assertEqual(audit["findings"], ["citation names line 895, not 891"])

    def test_required_audit_with_exhausted_budget_names_audit_spawn_exhaustion(self):
        protocol, plan = self.audit_ready()
        protocol.observe_failure(plan, "native-test", "timed_out")
        protocol.observe_failure(self.relaunch_audit(protocol), "native-test", "timed_out")
        budgets = self.view()["governance"]["budgets"]
        self.assertEqual(budgets["audit_spawns_used"], budgets["max_audit_spawns"])
        self.assertEqual(self.view()["audit"]["state"], "required")
        self.assert_exhausted_audit(self.request({
            "type": "EvaluateRun", "run_id": self.run_id, "intent": "report_convergence"}))

    def test_pending_audit_is_never_converted_to_exhaustion(self):
        protocol, plan = self.audit_ready()
        protocol.observe_failure(plan, "native-test", "timed_out")
        self.relaunch_audit(protocol)
        budgets = self.view()["governance"]["budgets"]
        self.assertEqual(budgets["audit_spawns_used"], budgets["max_audit_spawns"])
        result = self.request({"type": "EvaluateRun", "run_id": self.run_id,
                               "intent": "report_convergence"})
        self.assertEqual([r["code"] for r in result["reasons"]], ["audit.pending"])

    def test_same_class_reviewer_across_bedrock_spelling_is_blocked(self):
        protocol, plan = self.audit_ready()
        result = self.finish_audit(
            protocol, plan, "bedrock", "eu.anthropic.claude-sonnet-4-6")
        self.assertEqual(result["type"], "Block")
        self.assertEqual(result["reasons"][0]["code"], "audit.same_model")

    def test_mixed_covered_producers_are_blocked_and_research_does_not_supersede(self):
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
        audit_row = next(row for row in result["run"]["obligations"]["active"]
                         if row["id"] == "obligation.audit")
        self.assertEqual(audit_row["missing"]["code"], "audit.producers_mixed")
        self.assertEqual(audit_row["next"], result["reasons"][0]["next_actions"])

        # Research artifacts remain active as an accumulated source set. Recording the same
        # claim again under one producer therefore cannot erase the earlier mixed producer.
        self.assertIn(self.service.trusted_governance_context(
            run_id=self.run_id, payload={"author": AUTHOR, "ingress": "pi_ui"})
            ["result"]["type"], {"Allow", "Inert"})
        self.assertEqual(self.action(
            "research", claim_id="C0", source_kind="code", result="supports",
            payload={"source_ref": "third", "citation": "first observer again"})["type"],
            "Allow")
        recovered = self.request({"type": "GetArgument", "run_id": self.run_id})["argument"]
        claim = next(row for row in recovered["claims"] if row["claim_id"] == "C0")
        self.assertEqual(len(claim["active_evidence_ids"]), 3)
        self.assertEqual(recovered["audit"]["independence"], "mixed")

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
        fresh = self.request({"type": "StartRun", "control_mode": "deliberative", "goal": "fresh",
                              "invocation": dict(TEST_INVOCATION),
                              "selector": {"project": "p", "session": "s"}})
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


    def test_same_epoch_concurrent_reservations_survive_first_finalization_and_reload(self):
        invocation = {"host": "test", "interactive": True,
                      "signal": "operator", "delegation": True}
        self._prepare_auto(invocation)
        presentations = [self.decision(label, "present", reserve=False)
                         for label in ("concurrent-a", "concurrent-b")]
        for presentation in presentations:
            self.assertEqual(self.admit(presentation)["type"], "Allow")
        approval = {key: value for key, value in presentations[0].items()
                    if key != "outcome"}
        approval["submission"] = {"action": "approve", "configuration": {
            "budgets": self.view()["governance"]["proposal"]["budgets"]}}
        self.assertEqual(self.admit(approval)["type"], "Allow")
        self.service = self.service.reload()
        restored = self.view()
        self.assertEqual(restored["governance"]["state"], "approved")
        self.assertEqual(self.admit(approval)["type"], "Inert")
        self.assertEqual(self.admit(presentations[1])["type"], "Inert")

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
        final["submission"] = {"action": "approve",
                                "configuration": {"budgets": self.view()["governance"]["proposal"]["budgets"]}}
        for changed in ({"run_id": "other"}, {"plan_revision": 1000}, {"proposal_digest": "sha256:" + "a" * 64}):
            self.assertEqual(self.admit({**final, **changed})["type"], "Block")
            self.assertEqual(self.runs.data, before)
        forged_kind = self.admit({**final, "approval_kind": "auto"})
        self.assertEqual(forged_kind["type"], "Block")
        self.assertEqual(forged_kind["reasons"][0]["code"], "governance.receipt_replay")
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


    def test_total_interaction_cap_allows_existing_finalization_but_no_new_reservation(self):
        self.prepare()
        latest = None
        for revision in range(governance.MAX_INTERACTIONS):
            if revision:
                configured = self.action(**sized_configure_run(
                    budgets=SIZED_BUDGETS,
                    rationale=f"distinct deliberative interaction revision {revision}"))
                self.assertEqual(configured["type"], "Allow", configured)
            latest = self.decision(f"total-{revision}", "present", reserve=False)
            self.assertEqual(self.admit(latest)["type"], "Allow")
        self.assertEqual(self.view()["governance"]["interactions_remaining"]["total"], 0)
        before = copy.deepcopy(self.runs.data)
        overflow = self.decision("total-overflow", "present", reserve=False)
        refused = self.admit(overflow)
        self.assertEqual(refused["type"], "Block")
        self.assertEqual(refused["reasons"][0]["code"], "governance.interaction_limit")
        self.assertEqual(self.runs.data, before)
        final = {key: value for key, value in latest.items() if key != "outcome"}
        final["submission"] = {"action": "approve", "configuration": {
            "budgets": self.view()["governance"]["proposal"]["budgets"]}}
        self.assertEqual(self.admit(final)["type"], "Allow")

    def test_raw_submission_conflict_and_amendment_replay(self):
        self.prepare()
        g = self.view()["governance"]
        envelope = {"run_id": self.run_id, "receipt_id": "raw", "proposal_digest": g["proposal_digest"],
                    "plan_revision": g["plan_revision"], "approval_kind": "host_ui"}
        self.assertEqual(self.admit({**envelope, "outcome": "present"})["type"], "Allow")
        conflict = {**envelope, "submission": {"action": "approve", "feedback": "approved",
            "configuration": {"budgets": g["proposal"]["budgets"]}}}
        blocked = self.admit(conflict)
        self.assertEqual(blocked["type"], "Fault")
        self.assertIsNone(self.view()["governance"]["approved_digest"])

        envelope["receipt_id"] = "raw-amend"
        self.assertEqual(self.admit({**envelope, "outcome": "present"})["type"], "Allow")
        proposal = {"budgets": {**g["proposal"]["budgets"], "max_passes": 6}}
        raw = {**envelope, "submission": {"action": "approve",
                                           "configuration": proposal}}
        amended = self.admit(raw)
        self.assertEqual(amended["type"], "Allow")
        self.assertEqual(amended["run"]["governance"]["proposal"]["budgets"]["max_passes"], 6)
        self.assertEqual(self.admit(raw)["type"], "Inert")
        self.assertIsNone(self.view()["governance"]["approved_digest"])

    def test_start_requires_goal_and_attested_auto_authority(self):
        base = {"type": "StartRun", "control_mode": "deliberative", "selector": {"project": "p", "session": "provenance"},
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

    # --- ADR-0063 Confirmation: application transition matrix ---

    def _prepare_auto(self, invocation, *, budgets=None):
        self.run_id = self.request({"type": "StartRun", "goal": "auto task",
                                    "control_mode": "auto", "invocation": dict(invocation),
                                    "selector": {"project": "p", "session": "auto"}})["run"]["id"]
        self.assertEqual(self.action("route", reason="supplied context")["type"], "Allow")
        self.assertEqual(self.action("graph", payload=copy.deepcopy(GRAPH))["type"], "Allow")
        proposal_budgets = SIZED_BUDGETS if budgets is None else budgets
        self.assertEqual(self.action(**sized_configure_run(
            budgets=proposal_budgets, rationale=SIZED_RATIONALE))["type"], "Allow")
        result = self.service.trusted_governance_context(run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
        self.assertEqual(result["result"]["type"], "Allow", result)

    def test_contract_ceiling_bounds_are_the_application_boundary_policy(self):
        schema = protocol.request_schema()["$defs"]["actionConfigureRun"]["properties"][
            "budgets"
        ]["properties"]
        expected = {name: (definition["minimum"], definition["maximum"])
                    for name, definition in schema.items()}
        self.assertEqual(dict(protocol.CEILING_BOUNDS), expected)
        self.assertEqual(set(protocol.CEILING_BOUNDS), set(governance.CEILINGS))

    def test_application_limits_are_validated_once_before_service_construction(self):
        invalid = (
            ({"budgets": {"max_passes": 2}}, "unknown keys: budgets"),
            ({"unknown": 1}, "unknown keys: unknown"),
            ({"max_passes": "2"}, "max_passes must be an integer"),
            ({"max_passes": True}, "max_passes must be an integer"),
            ({"max_passes": 0}, "max_passes must be between 1 and 1024"),
            ({"max_spawns": 129}, "max_spawns must be between 0 and 128"),
        )
        for limits, diagnostic in invalid:
            with self.subTest(limits=limits):
                runs, artifacts = Runs(), Artifacts()
                with self.assertRaisesRegex(ValueError, diagnostic):
                    compose(Workspace(), Harness(), runs, artifacts, None,
                            PROFILE, limits, None)
                self.assertEqual(runs.data, {})
                self.assertEqual(artifacts.values, {})
        for limits in ({}, {"max_passes": 2},
                       {"max_spawns": 0, "max_audit_spawns": 0}):
            with self.subTest(valid=limits):
                service = compose(Workspace(), Harness(), Runs(), Artifacts(), None,
                                  PROFILE, limits, None)
                self.assertEqual(dict(service._limits), limits)
                with self.assertRaises(TypeError):
                    service._limits["max_passes"] = 1

    def test_delegated_sources_narrow_initial_authority_componentwise(self):
        narrowed = {"max_passes": 2, "max_spawns": 0, "max_audit_spawns": 1}
        for source_name in ("limits", "start"):
            for ceiling, value in narrowed.items():
                with self.subTest(source=source_name, ceiling=ceiling):
                    limits = {ceiling: value} if source_name == "limits" else {}
                    start_budgets = {ceiling: value} if source_name == "start" else {}
                    runs, artifacts = Runs(), Artifacts()
                    service = compose(Workspace(), Harness(), runs, artifacts, None,
                                      PROFILE, limits, None)
                    command = {"type": "StartRun", "goal": "narrowed delegated",
                               "control_mode": "auto", "invocation": DELEGATED_INVOCATION,
                               "selector": {"project": source_name, "session": ceiling}}
                    if start_budgets:
                        command["budgets"] = start_budgets
                    started = service.dispatch({"protocol": "empirica/v2", "request_id": "start",
                                                "command": command})["result"]
                    self.assertEqual(started["type"], "Allow", started)
                    run_id = started["run"]["id"]
                    for action in ({"kind": "route", "reason": "supplied"},
                                   {"kind": "graph", "payload": copy.deepcopy(GRAPH)}):
                        self.assertEqual(service.dispatch({"protocol": "empirica/v2",
                            "request_id": "prepare", "command": {"type": "ObserveAction",
                            "run_id": run_id, "action": action}})["result"]["type"], "Allow")
                    refused = service.dispatch({"protocol": "empirica/v2", "request_id": "size",
                        "command": {"type": "ObserveAction", "run_id": run_id,
                                    "action": sized_configure_run(
                                        budgets=SIZED_BUDGETS,
                                        rationale=SIZED_RATIONALE)}})["result"]
                    self.assertEqual(refused["type"], "Block", refused)
                    self.assertEqual(refused["reasons"][0]["code"],
                                     "governance.auto_ceiling")
                    key = decode_handle(run_id)
                    restored = classify_and_decode(runs.data[key].value)
                    self.assertEqual(restored.kind, "valid")
                    self.assertEqual(restored.state.governance["delegation_envelope"][ceiling],
                                     value)

    def test_delegated_sources_refuse_policy_and_cross_source_contradictions(self):
        cases = (
            ({"max_passes": 100}, {"max_passes": 8}),
            ({"max_passes": 2}, {"max_passes": 8}),
            ({}, {"max_audit_spawns": 3}),
        )
        for index, (limits, start_budgets) in enumerate(cases):
            with self.subTest(limits=limits, start_budgets=start_budgets):
                service = compose(Workspace(), Harness(), Runs(), Artifacts(), None,
                                  PROFILE, limits, None)
                result = service.dispatch({"protocol": "empirica/v2", "request_id": "start",
                    "command": {"type": "StartRun", "goal": "contradictory",
                                "control_mode": "auto", "invocation": DELEGATED_INVOCATION,
                                "budgets": start_budgets,
                                "selector": {"project": "conflict", "session": str(index)}}})["result"]
                self.assertEqual(result["type"], "Block", result)
                self.assertEqual(result["reasons"][0]["code"],
                                 "governance.budget_contradictory")

    def test_restore_rejects_delegated_pending_proposal_only_outside_envelope(self):
        self._prepare_auto(DELEGATED_INVOCATION)
        self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 7},
            rationale=SIZED_RATIONALE))["type"], "Allow")
        key = decode_handle(self.run_id)
        pending = copy.deepcopy(self.runs.data[key].value)
        self.assertEqual(pending["budgets"]["max_passes"], 8)
        self.assertEqual(pending["governance"]["delegation_envelope"]["max_passes"], 8)
        self.assertEqual(classify_and_decode(pending).kind, "valid")
        outside = copy.deepcopy(pending)
        outside["governance"]["proposal"]["budgets"]["max_passes"] = 9
        outside["governance"]["proposal_digest"] = governance.proposal_digest(
            outside["goal"], outside["governance"])
        self.assertEqual(classify_and_decode(outside).kind, "current_corrupt")

    def test_restore_semantic_predicates_are_closed_with_reachable_positive_controls(self):
        placeholder = copy.deepcopy(self.runs.data[decode_handle(self.run_id)].value)
        self.prepare()
        self.assertEqual(self.admit(self.decision("semantic-pre-reject", outcome="reject"))["type"],
                         "Allow")
        pre_rejected = copy.deepcopy(self.runs.data[decode_handle(self.run_id)].value)
        self.assertEqual(classify_and_decode(pre_rejected).kind, "valid")
        self.assertEqual(self.action(**sized_configure_run(
            budgets=SIZED_BUDGETS,
            rationale="sizing after a legitimate preapproval rejection"))["type"], "Allow")
        self.assertEqual(self.admit(self.decision("semantic-first"))["type"], "Allow")
        self.assertEqual(self.action("investigate")["type"], "Allow")
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 7},
            rationale="first post-approval semantic revision"))["type"], "Allow")
        self.assertEqual(self.admit(self.decision("semantic-second"))["type"], "Allow")
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 6},
            rationale="second post-approval semantic revision"))["type"], "Allow")
        key = decode_handle(self.run_id)
        post_pending = copy.deepcopy(self.runs.data[key].value)
        self.assertEqual(classify_and_decode(post_pending).kind, "valid")
        self.assertEqual(self.admit(self.decision("semantic-reject", outcome="reject"))["type"],
                         "Allow")
        post_rejected = copy.deepcopy(self.runs.data[key].value)
        self.assertEqual(classify_and_decode(post_rejected).kind, "valid")
        self.assertEqual(self.service.trusted_governance_context(
            run_id=self.run_id, payload={"author": AUTHOR, "ingress": "unavailable"})
            ["result"]["type"], "Allow")
        capability_loss = copy.deepcopy(self.runs.data[key].value)

        placeholder = copy.deepcopy(placeholder)
        self.assertEqual(classify_and_decode(placeholder).kind, "valid")
        positives = (placeholder, pre_rejected, post_pending, post_rejected, capability_loss)
        for index, positive in enumerate(positives):
            with self.subTest(positive=index):
                self.assertEqual(classify_and_decode(positive).kind, "valid")

        self.runs, self.artifacts = Runs(), Artifacts()
        self.service = compose(Workspace(), Harness(), self.runs, self.artifacts,
                               None, PROFILE, {}, None)
        interactive = {"host": "test", "interactive": True,
                       "signal": "operator", "delegation": True}
        self._prepare_auto(interactive)
        self.assertEqual(self.action(**sized_configure_run(
            budgets=SIZED_BUDGETS, rationale="auto preapproval revision"))["type"], "Allow")
        auto_pre = copy.deepcopy(self.runs.data[decode_handle(self.run_id)].value)
        self.assertEqual(classify_and_decode(auto_pre).kind, "valid")
        self.assertEqual(self.admit(self.decision("auto-semantic-human"))["type"], "Allow")
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 7},
            rationale="auto postapproval revision"))["type"], "Allow")
        auto_post = copy.deepcopy(self.runs.data[decode_handle(self.run_id)].value)
        self.assertEqual(classify_and_decode(auto_post).kind, "valid")

        mutations = {
            "placeholder must remain pending": (placeholder, lambda raw:
                raw["governance"].update(state="rejected")),
            "placeholder revision is zero": (placeholder, lambda raw:
                raw["governance"].update(plan_revision=1)),
            "placeholder allowances are zero": (placeholder, lambda raw:
                raw["governance"].update(pre_approval_revisions=1)),
            "placeholder ceilings equal effective seed": (placeholder, lambda raw:
                raw["governance"]["proposal"]["budgets"].update(max_passes=7)),
            "first approval requires metadata": (post_pending, lambda raw:
                raw["governance"].update(approved_digest=None, approval_kind=None)),
            "metadata requires first approval": (post_pending, lambda raw:
                raw["governance"].update(first_approval=False)),
            "metadata names latest successful receipt": (post_pending, lambda raw:
                raw["governance"].update(
                    approved_digest=raw["governance"]["receipts"][0]["proposal_digest"],
                    approval_kind=raw["governance"]["receipts"][0]["approval_kind"])),
            "pending is preapproval": (post_pending, lambda raw:
                raw["governance"].update(state="pending")),
            "previously approved proposal cannot become placeholder": (post_pending, lambda raw:
                raw["governance"]["proposal"].update(rationale=None)),
            "placeholder cannot acquire receipt history": (placeholder, lambda raw:
                raw["governance"]["receipts"].append(
                    copy.deepcopy(post_pending["governance"]["receipts"][0]))),
            "revision pending is postapproval": (auto_pre, lambda raw:
                raw["governance"].update(state="revision_pending")),
            "post allowance is unavailable before approval": (auto_pre, lambda raw:
                raw["governance"].update(post_approval_revisions=1)),
            "auto revision arithmetic cannot reset": (auto_post, lambda raw:
                raw["governance"].update(post_approval_revisions=0)),
            "first approval epoch follows pre revisions": (auto_post, lambda raw:
                raw["governance"]["receipts"][0].update(plan_revision=1)),
            "deliberative allowances remain zero": (post_pending, lambda raw:
                raw["governance"].update(post_approval_revisions=1)),
            "pending proposal respects consumed passes": (post_pending, lambda raw:
                raw["budgets"].update(passes_used=7)),
            "post-first auto proposal is monotone": (auto_post, lambda raw:
                raw["governance"]["proposal"]["budgets"].update(max_passes=9)),
            "ingress capability follows contract mapping": (capability_loss, lambda raw:
                raw["governance"]["context"].update(
                    approval_capability=governance.HUMAN_CONFIGURATION)),
        }
        for label, (base, mutate) in mutations.items():
            with self.subTest(predicate=label):
                raw = copy.deepcopy(base)
                mutate(raw)
                self.assertEqual(classify_and_decode(raw).kind, "current_corrupt")

        same_epoch = copy.deepcopy(post_pending)
        receipt = copy.deepcopy(same_epoch["governance"]["receipts"][0])
        receipt.update(id="same-epoch-conflict", proposal_digest="sha256:" + "f" * 64,
                       outcome="reject", fingerprint="sha256:" + "e" * 64)
        same_epoch["governance"]["receipts"].insert(1, receipt)
        self.assertEqual(classify_and_decode(same_epoch).kind, "current_corrupt")

    def test_restore_requires_successful_approval_for_each_governed_progress_category(self):
        self.prepare()
        self.assertEqual(self.admit(self.decision("progress"))["type"], "Allow")
        self.assertEqual(self.action("investigate")["type"], "Allow")
        key = decode_handle(self.run_id)
        investigated = copy.deepcopy(self.runs.data[key].value)
        placeholder_governance = governance.initial(
            investigated["goal"], investigated["budgets"], "deliberative",
            investigated["invocation"], None)
        controls = {}
        for label, mutation in (
                ("investigation", lambda raw: None),
                ("convergence", lambda raw: raw.update(status="converged"))):
            raw = copy.deepcopy(investigated)
            raw["governance"] = copy.deepcopy(placeholder_governance)
            mutation(raw)
            controls[label] = raw
        self.assertEqual(self.action(
            "child_reserve", purpose="investigation", resource_class="investigation",
            role_profile="empirica:empirica-auditor", execution="foreground")["type"], "Allow")
        child = copy.deepcopy(self.runs.data[key].value)
        child["governance"] = copy.deepcopy(placeholder_governance)
        controls["children"] = child
        for label, raw in controls.items():
            with self.subTest(progress=label):
                self.assertEqual(classify_and_decode(raw).kind, "current_corrupt")

    def test_restore_requires_exact_successful_receipt_and_invocation_provenance(self):
        self._prepare_auto(DELEGATED_INVOCATION)
        self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")
        key = decode_handle(self.run_id)
        base = copy.deepcopy(self.runs.data[key].value)
        self.assertEqual(classify_and_decode(base).kind, "valid")

        mutations = {
            "receipt approve to reject": lambda raw: raw["governance"]["receipts"][0].update(
                outcome="reject"),
            "receipt revision to zero": lambda raw: raw["governance"]["receipts"][0].update(
                plan_revision=0),
            "state host_ui with auto receipt": lambda raw: raw["governance"].update(
                approval_kind="host_ui"),
            "revision pending denies prior approval": lambda raw: raw["governance"].update(
                state="revision_pending", first_approval=False),
            "forged context facts": lambda raw: raw["governance"]["context"].update(
                interactive=True),
            "approved placeholder": lambda raw: raw["governance"]["proposal"].update(
                rationale=None),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                raw = copy.deepcopy(base)
                mutate(raw)
                self.assertEqual(classify_and_decode(raw).kind, "current_corrupt")

    def test_restore_rejects_impossible_authority_histories(self):
        # Genuine delegated approval cannot outgrow its envelope or lose invocation authority.
        self._prepare_auto(DELEGATED_INVOCATION)
        self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")
        delegated = copy.deepcopy(self.runs.data[decode_handle(self.run_id)].value)
        mutations = {
            "effective ceilings outside persisted envelope": lambda raw: raw["governance"][
                "delegation_envelope"].update(max_passes=2),
            "delegated branch without delegated invocation": lambda raw: (
                raw["invocation"].update(delegation=False),
                raw["governance"]["context"].update(delegation=False),
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                raw = copy.deepcopy(delegated)
                mutate(raw)
                self.assertEqual(classify_and_decode(raw).kind, "current_corrupt")

        # Build the genuine interactive human-then-auto chronology used by both mutations.
        self.runs, self.artifacts = Runs(), Artifacts()
        self.service = compose(Workspace(), Harness(), self.runs, self.artifacts,
                               None, PROFILE, {}, None)
        invocation = {"host": "test", "interactive": True,
                      "signal": "operator", "delegation": True}
        self._prepare_auto(invocation)
        self.assertEqual(self.admit(self.decision("human"))["type"], "Allow")
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 7},
            rationale=SIZED_RATIONALE))["type"], "Allow")
        self.assertEqual(self.admit(self.decision(
            "automatic", approval_kind="auto"))["type"], "Allow")
        interactive = copy.deepcopy(self.runs.data[decode_handle(self.run_id)].value)
        self.assertEqual(classify_and_decode(interactive).kind, "valid")

        second_human = copy.deepcopy(interactive)
        latter = second_human["governance"]["receipts"][-1]
        latter["approval_kind"] = "host_ui"
        latter["presentation_fingerprint"] = "sha256:" + "a" * 64
        second_human["governance"]["approval_kind"] = "host_ui"
        self.assertEqual(classify_and_decode(second_human).kind, "current_corrupt")

        auto_before_human = copy.deepcopy(interactive)
        receipts = auto_before_human["governance"]["receipts"]
        receipts[0]["plan_revision"], receipts[-1]["plan_revision"] = (
            receipts[-1]["plan_revision"], receipts[0]["plan_revision"])
        auto_before_human["governance"]["state"] = "revision_pending"
        self.assertEqual(classify_and_decode(auto_before_human).kind, "current_corrupt")

    def test_initial_delegated_auto_approval_installs_budgets_without_human(self):
        self._prepare_auto(DELEGATED_INVOCATION)
        self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")
        g = self.view()["governance"]
        self.assertEqual(g["state"], "approved")
        self.assertTrue(g["first_approval"])
        self.assertEqual(g["approval_kind"], "auto")
        self.assertEqual(g["budgets"]["max_audit_spawns"], 2)
        # Restored state decodes valid and preserves the durable approval facts. Select
        # this test's run explicitly: setUp also creates a separate deliberative run.
        key = decode_handle(self.run_id)
        self.assertIsNotNone(key)
        decoded = classify_and_decode(self.runs.data[key].value)
        self.assertEqual(decoded.kind, "valid")
        self.assertTrue(decoded.state.governance["first_approval"])
        self.assertEqual(decoded.state.governance["approval_kind"], "auto")

    def test_delegated_auto_envelope_refuses_oversize_before_first_approval(self):
        self._prepare_auto(DELEGATED_INVOCATION)
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 9}, rationale=SIZED_RATIONALE))
            ["reasons"][0]["code"], "governance.auto_ceiling")
        self.assertEqual(self.view()["governance"]["state"], "pending")

    def test_every_ceiling_raise_is_refused_after_auto_approval(self):
        interactive = {"host": "test", "interactive": True,
                       "signal": "operator", "delegation": True}
        raises = (("max_passes", 9), ("max_spawns", 2), ("max_audit_spawns", 3))
        for invocation, approval_kind in ((DELEGATED_INVOCATION, "auto"),
                                          (interactive, "host_ui")):
            for ceiling, value in raises:
                with self.subTest(invocation=approval_kind, ceiling=ceiling):
                    self.runs, self.artifacts = Runs(), Artifacts()
                    self.service = compose(Workspace(), Harness(), self.runs, self.artifacts,
                                           None, PROFILE, {}, None)
                    self._prepare_auto(invocation)
                    self.assertEqual(self.admit(self.decision(
                        approval_kind=approval_kind))["type"], "Allow")
                    refused = self.action(**sized_configure_run(
                        budgets={**SIZED_BUDGETS, ceiling: value},
                        rationale=SIZED_RATIONALE))
                    self.assertEqual(refused["type"], "Block", refused)
                    self.assertEqual(refused["reasons"][0]["code"],
                                     "governance.auto_ceiling")

    def test_post_approval_auto_accepts_lower_and_refuses_raise(self):
        self._prepare_auto(DELEGATED_INVOCATION)
        self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")
        # a non-raising revision is auto-accepted without a human
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 6}, rationale=SIZED_RATIONALE))["type"], "Allow")
        self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")
        self.assertEqual(self.view()["governance"]["budgets"]["max_passes"], 6)
        # a raise after the first approval is refused
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 7}, rationale=SIZED_RATIONALE))
            ["reasons"][0]["code"], "governance.auto_ceiling")

    def test_interactive_auto_requires_human_first_episode_then_auto(self):
        self.run_id = self.request({"type": "StartRun", "goal": "interactive auto",
                                   "control_mode": "auto",
                                   "invocation": {"host": "test", "interactive": True,
                                                  "signal": "op", "delegation": True},
                                   "selector": {"project": "p", "session": "ia"}})["run"]["id"]
        self.prepare()
        # auto before the first approval is refused: interactive needs the human episode
        denied = self.admit(self.decision(approval_kind="auto"))
        self.assertEqual(denied["type"], "Block")
        self.assertEqual(denied["reasons"][0]["code"],
                         "governance.approval_unavailable")
        self.assertEqual(self.admit(self.decision())["type"], "Allow")  # host_ui first episode
        self.assertTrue(self.view()["governance"]["first_approval"])
        # after the episode, auto accepts a non-raising revision
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 6}, rationale=SIZED_RATIONALE))["type"], "Allow")
        self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")

    def test_unavailable_ingress_fails_closed_for_human_approval(self):
        self.prepare()
        self.service.trusted_governance_context(run_id=self.run_id,
            payload={"author": AUTHOR, "ingress": "unavailable"})
        g = self.view()["governance"]
        payload = {"run_id": self.run_id, "receipt_id": "no-ui", "proposal_digest": g["proposal_digest"],
                   "plan_revision": g["plan_revision"], "approval_kind": "host_ui",
                   "outcome": "present"}
        blocked = self.admit(payload)
        self.assertEqual(blocked["type"], "Block")
        self.assertEqual(blocked["reasons"][0]["code"], "governance.approval_unavailable")
        self.assertIsNone(self.view()["governance"]["approved_digest"])

    def test_dismissal_and_rejection_grant_no_consent(self):
        """Timeout is host-adapter mediation owned by G2; core covers dismiss/reject here."""
        self.prepare()
        g = self.view()["governance"]
        dismiss = {"run_id": self.run_id, "receipt_id": "d", "proposal_digest": g["proposal_digest"],
                   "plan_revision": g["plan_revision"], "approval_kind": "host_ui",
                   "outcome": "dismiss"}
        # Dismissal finalizes the exact prior presentation reservation.
        self.assertEqual(self.admit({**dismiss, "outcome": "present"})["type"], "Allow")
        self.assertEqual(self.admit(dismiss)["type"], "Allow")
        self.assertEqual(self.view()["governance"]["state"], "pending")
        self.assertIsNone(self.view()["governance"]["approved_digest"])
        self.action(**sized_configure_run(budgets=SIZED_BUDGETS, rationale=SIZED_RATIONALE))  # refresh the reservation epoch
        g = self.view()["governance"]
        reject = {"run_id": self.run_id, "receipt_id": "r", "proposal_digest": g["proposal_digest"],
                  "plan_revision": g["plan_revision"], "approval_kind": "host_ui",
                  "submission": {"action": "reject", "configuration": {"budgets": g["proposal"]["budgets"]}}}
        presentation = {k: v for k, v in reject.items() if k != "submission"}
        presentation["outcome"] = "present"
        self.assertEqual(self.admit(presentation)["type"], "Allow")
        self.assertEqual(self.admit(reject)["type"], "Allow")
        self.assertEqual(self.view()["governance"]["state"], "rejected")
        self.assertIsNone(self.view()["governance"]["approved_digest"])

    def test_configure_run_requires_rationale_and_every_ceiling_at_boundary(self):
        self.assertEqual(self.action("route", reason="supplied context")["type"], "Allow")
        self.assertEqual(self.action("graph", payload=copy.deepcopy(GRAPH))["type"], "Allow")
        invalid = [
            {"kind": "configure_run", "budgets": dict(SIZED_BUDGETS)},
            {"kind": "configure_run", "budgets": dict(SIZED_BUDGETS), "rationale": ""},
            {"kind": "configure_run", "budgets": dict(SIZED_BUDGETS), "rationale": "   "},
            {"kind": "configure_run", "budgets": dict(SIZED_BUDGETS), "rationale": "x" * 601},
            {"kind": "configure_run", "budgets": {"max_passes": 8, "max_spawns": 1},
             "rationale": SIZED_RATIONALE},
        ]
        for action in invalid:
            with self.subTest(action=action):
                before = copy.deepcopy(self.runs.data)
                result = self.request({"type": "ObserveAction", "run_id": self.run_id,
                                       "action": action})
                self.assertEqual(result["type"], "Fault")
                self.assertEqual(result["code"], "invalid_request")
                self.assertEqual(result["fail_direction"], "closed")
                self.assertEqual(self.runs.data, before)
        self.assertEqual(self.action(**sized_configure_run(
            budgets=SIZED_BUDGETS, rationale=SIZED_RATIONALE))["type"], "Allow")

    def test_initial_human_amendment_above_seed_is_allowed_and_keeps_rationale(self):
        self.prepare()
        original = self.view()["governance"]["proposal"]["rationale"]
        budgets = {**SIZED_BUDGETS, "max_passes": 9}
        amendment = self.decision("raise", outcome="amend",
                                  amendment={"configuration": {"budgets": budgets}})
        self.assertEqual(self.admit(amendment)["type"], "Allow")
        pending = self.view()["governance"]
        self.assertEqual(pending["proposal"]["budgets"]["max_passes"], 9)
        self.assertEqual(pending["proposal"]["rationale"], original)
        self.assertFalse(pending["first_approval"])
        self.assertEqual(self.admit(self.decision("confirm"))["type"], "Allow")
        self.assertEqual(self.view()["governance"]["budgets"]["max_passes"], 9)

    def test_pre_approval_auto_revision_exhaustion_is_separate_and_nonmutating(self):
        invocation = {"host": "test", "interactive": True, "signal": "op", "delegation": True}
        self._prepare_auto(invocation)
        for n in range(governance.MAX_REVISIONS):
            self.assertEqual(self.action(**sized_configure_run(
                budgets=SIZED_BUDGETS, rationale=f"pre-approval sizing revision {n}"))["type"],
                             "Allow")
        before = copy.deepcopy(self.runs.data)
        exhausted = self.action(**sized_configure_run(
            budgets=SIZED_BUDGETS, rationale="ninth pre-approval revision"))
        self.assertEqual(exhausted["reasons"][0]["code"], "governance.revision_exhausted")
        self.assertEqual(self.runs.data, before)

    def test_historical_human_replay_after_auto_kind_transition_is_inert(self):
        self.run_id = self.request({"type": "StartRun", "goal": "kind transition",
                                   "control_mode": "auto",
                                   "invocation": {"host": "test", "interactive": True,
                                                  "signal": "op", "delegation": True},
                                   "selector": {"project": "p", "session": "kind"}})["run"]["id"]
        self.prepare()
        human = self.decision("human")
        self.assertEqual(self.admit(human)["type"], "Allow")
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 7}, rationale=SIZED_RATIONALE))["type"], "Allow")
        self.assertEqual(self.admit(self.decision("automatic", approval_kind="auto"))["type"], "Allow")
        before = copy.deepcopy(self.runs.data)
        self.assertEqual(self.admit(human)["type"], "Inert")
        self.assertEqual(self.runs.data, before)

    def test_lost_ui_after_first_auto_approval_does_not_reopen_human_episode(self):
        interactive = {"host": "test", "interactive": True,
                       "signal": "operator", "delegation": True}
        self._prepare_auto(interactive)
        self.assertEqual(self.admit(self.decision(approval_kind="host_ui"))["type"], "Allow")
        refreshed = self.service.trusted_governance_context(
            run_id=self.run_id, payload={"author": AUTHOR, "ingress": "unavailable"})
        self.assertEqual(refreshed["result"]["type"], "Allow")
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_passes": 7},
            rationale=SIZED_RATIONALE))["type"], "Allow")
        self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")
        g = self.view()["governance"]
        present = {"run_id": self.run_id, "receipt_id": "no-second-episode",
                   "proposal_digest": g["proposal_digest"],
                   "plan_revision": g["plan_revision"], "approval_kind": "host_ui",
                   "outcome": "present"}
        blocked = self.admit(present)
        self.assertEqual(blocked["type"], "Block")
        self.assertEqual(blocked["reasons"][0]["code"],
                         "governance.approval_unavailable")

    def test_human_amendment_consumes_preapproval_allowance_without_renewal(self):
        interactive = {"host": "test", "interactive": True,
                       "signal": "operator", "delegation": True}
        self._prepare_auto(interactive)
        for n in range(governance.MAX_REVISIONS - 1):
            self.assertEqual(self.action(**sized_configure_run(
                budgets=SIZED_BUDGETS, rationale=f"author preapproval revision {n}"))["type"],
                             "Allow")
        budgets = {**SIZED_BUDGETS, "max_passes": 9}
        amendment = self.decision("allowance-amend", outcome="amend",
                                  amendment={"configuration": {"budgets": budgets}})
        self.assertEqual(self.admit(amendment)["type"], "Allow")
        self.assertEqual(self.view()["governance"]["pre_approval_revisions"],
                         governance.MAX_REVISIONS)
        refused = self.action(**sized_configure_run(
            budgets=budgets, rationale="ninth preapproval revision"))
        self.assertEqual(refused["reasons"][0]["code"], "governance.revision_exhausted")
        self.assertEqual(self.admit(self.decision("allowance-approve"))["type"], "Allow")
        self.assertEqual(self.view()["governance"]["pre_approval_revisions"],
                         governance.MAX_REVISIONS)
        for n in range(governance.MAX_REVISIONS):
            self.assertEqual(self.action(**sized_configure_run(
                budgets=budgets, rationale=f"postapproval revision {n}"))["type"], "Allow")
            self.assertEqual(self.admit(self.decision(
                f"post-{n}", approval_kind="auto"))["type"], "Allow")
        refused = self.action(**sized_configure_run(
            budgets=budgets, rationale="ninth postapproval revision"))
        self.assertEqual(refused["reasons"][0]["code"], "governance.revision_exhausted")

    def test_tampered_persisted_rationale_is_reported_corrupt(self):
        self.prepare()
        self.assertEqual(self.admit(self.decision())["type"], "Allow")
        key = decode_handle(self.run_id)
        entry = self.runs.data[key]
        raw = copy.deepcopy(entry.value)
        raw["governance"]["proposal"]["rationale"] = "valid but tampered rationale"
        self.runs.data[key] = type(entry)(entry.revision, raw)
        blocked = self.request({"type": "GetRun", "run_id": self.run_id})
        self.assertEqual(blocked["type"], "Block")
        self.assertEqual(blocked["reasons"][0]["code"], "run.corrupt")

    def test_zero_audit_auto_projection_drops_budget_raise_everywhere(self):
        self._prepare_auto(DELEGATED_INVOCATION,
                           budgets={**SIZED_BUDGETS, "max_audit_spawns": 0})
        self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")
        self.assertEqual(self.action("investigate")["type"], "Allow")
        self.assertEqual(self.action("research", claim_id="C0", source_kind="code",
            result="supports", payload={"source_ref": "supplied", "citation": "observed"})
            ["type"], "Allow")
        denied = self.action("child_reserve", purpose="audit", resource_class="audit",
                             role_profile="empirica:empirica-auditor", execution="foreground")
        self.assertEqual(denied["reasons"][0]["code"], "budget.exhausted")
        self.assertNotIn("budget.raise", denied["reasons"][0]["next_actions"])
        self.assertIn("run.start_fresh", denied["reasons"][0]["next_actions"])
        audit_row = next(row for row in denied["run"]["obligations"]["active"]
                         if row["id"] == "obligation.audit")
        self.assertNotIn("budget.raise", audit_row["next"])
        self.assertTrue(all("budget.raise" not in row["next_actions"]
                            for row in denied["run"]["residuals"]))

    def test_zero_audit_proposal_honest_inability_to_converge(self):
        self.assertEqual(self.action("route", reason="supplied context")["type"], "Allow")
        self.assertEqual(self.action("graph", payload=copy.deepcopy(GRAPH))["type"], "Allow")
        self.assertEqual(self.action(**sized_configure_run(
            budgets={**SIZED_BUDGETS, "max_audit_spawns": 0}, rationale=SIZED_RATIONALE))["type"], "Allow")
        self.assertEqual(self.service.trusted_governance_context(
            run_id=self.run_id, payload=copy.deepcopy(CONTEXT))["result"]["type"], "Allow")
        self.assertEqual(self.admit(self.decision())["type"], "Allow")
        self.assertEqual(self.view()["governance"]["budgets"]["max_audit_spawns"], 0)
        self.assertEqual(self.action("investigate")["type"], "Allow")
        self.assertEqual(self.action("research", claim_id="C0", source_kind="code", result="supports",
                                    payload={"source_ref": "supplied", "citation": "observed"})["type"], "Allow")
        denied = self.action("child_reserve", purpose="audit", resource_class="audit",
                             role_profile="empirica:empirica-auditor", execution="foreground")
        self.assertEqual(denied["reasons"][0]["code"], "budget.exhausted")
        self.assertIn("budget.raise", denied["reasons"][0]["next_actions"])
        audit_row = next(row for row in denied["run"]["obligations"]["active"]
                         if row["id"] == "obligation.audit")
        # The audit row names the same exhaustion as the reason, so both offer the same recovery.
        self.assertEqual(audit_row["missing"]["code"], "budget.exhausted")
        self.assertEqual(audit_row["next"], denied["reasons"][0]["next_actions"])
        self.assertEqual(denied["reasons"][0]["parameters"], {"resource": "audit_spawn"})

    def test_human_submission_cannot_inject_or_tamper_rationale(self):
        self.prepare()
        g = self.view()["governance"]
        original = g["proposal"]["rationale"]
        # a human submission carrying a rationale field is rejected by the schema
        tampered = {"run_id": self.run_id, "receipt_id": "tamper", "proposal_digest": g["proposal_digest"],
                    "plan_revision": g["plan_revision"], "approval_kind": "host_ui",
                    "submission": {"action": "approve",
                                    "configuration": {"budgets": g["proposal"]["budgets"],
                                                       "rationale": "forged reasoning"}}}
        result = self.admit(tampered)
        self.assertEqual(result["type"], "Fault")
        self.assertEqual(result["code"], "invalid_request")
        self.assertEqual(result["fail_direction"], "closed")
        self.assertEqual(self.view()["governance"]["proposal"]["rationale"], original)

    def test_revision_exhaustion_stops_honestly_after_first_approval(self):
        self._prepare_auto(DELEGATED_INVOCATION)
        self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")
        # Distinct rationale changes are material and let this exercise all eight
        # revisions without violating the monotone budget rule or schema minima.
        for n in range(governance.MAX_REVISIONS):
            self.assertEqual(self.action(**sized_configure_run(
                budgets=SIZED_BUDGETS, rationale=f"post-approval sizing revision {n}"))["type"],
                             "Allow")
            self.assertEqual(self.admit(self.decision(approval_kind="auto"))["type"], "Allow")
        before = copy.deepcopy(self.runs.data)
        exhausted = self.action(**sized_configure_run(
            budgets=SIZED_BUDGETS, rationale="ninth post-approval revision"))
        self.assertEqual(exhausted["reasons"][0]["code"], "governance.revision_exhausted")
        self.assertEqual(self.runs.data, before)  # refused before mutation


if __name__ == "__main__":
    unittest.main()
