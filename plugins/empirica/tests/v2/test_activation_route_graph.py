"""D4 cases 1–6: activation, route, graph, historical UX.

Owners: D9 (activation RunView/progressive card), D6 (v2 protocol/route/strict state),
D7/D7-W (derived claim state + graph fail-closed), D5 (refutation history).

Each case has ONE primary exact observable postcondition that rejects a no-op SUT; normal
requests go through central ``dispatch`` (request validated before the SUT sees it). Host profile
selection is a driver-factory fact — StartRun has no profile_id. Prerequisites use the ``require_*``
helpers (HarnessDefect). The canonical graph/scenario helper submits a canonical selected graph
with explicit root, ordered claims (id, exact text, gating) and edges; claim IDs and texts used by
later research/spikes must come from that graph (D4-S2/D2C). D2C ``ArgumentView.artifacts`` is the
sole artifact provenance source; raw ``drv.artifacts()`` is not called by cases 1–21.
"""
from __future__ import annotations

import unittest

from assertions import (  # noqa: E402
    ConformanceCase, action_graph, action_investigate, action_research, action_route,
    canonical_graph, evaluate, get_argument, get_run, observe_action, start_run, REASONS,
)


class ActivationRouteGraphTests(ConformanceCase):
    # 1 — Start exposes goal, modes, contract identity/digest, host profile/tier (no full dump)
    def test_start_exposes_goal_modes_contract_identity_host_profile(self):
        drv = self.bind_driver(
            "D9", "case-1",
            "StartRun exposes goal, exact effective modes requested (and required boolean sibling), "
            "canonical contract identity/digest, exact selected host profile/tier, active status, "
            "complete bounded RunView arrays, and no full/private dump")
        env = start_run(goal=self.DEFAULT_GOAL,
                        budgets={"max_passes": 3, "max_spawns": 2},
                        modes={"multi_provider": False})
        resp = self.dispatch(drv, env)
        self.assert_protocol_identity(resp, env["request_id"])
        result = self.assert_allow(resp, converged=False)
        run = result["run"]
        self.assertEqual(run["goal"], self.DEFAULT_GOAL, "StartRun must echo the goal")
        self.assert_status(run, "active")
        # Exact complete effective modes object, not just key presence.
        self.assertEqual(run["modes"], {"multi_provider": False, "cli_exec": False},
                         "RunView modes must be the exact complete effective modes object")
        # Complete bounded RunView arrays.
        self.assertIn("obligations", run, "RunView must carry obligations")
        self.assertIn("active", run["obligations"], "obligations must carry active array")
        self.assertIn("deferred", run["obligations"], "obligations must carry deferred array")
        self.assertEqual(run["obligations"]["deferred"], [],
                         "a fresh run must have no deferred obligations")
        self.assertIn("residuals", run, "RunView must carry residuals array")
        self.assertIn("freshness", run, "RunView must carry freshness")
        self.assertIn("changes", run["freshness"], "freshness must carry changes array")
        self.assertEqual(run["freshness"]["changes"], [],
                         "a fresh run must have no freshness changes")
        self.assertIn("children", run, "RunView must carry children array")
        self.assertEqual(run["children"], [], "a fresh run must have no children")
        self.assertIn("host", run, "RunView must carry host")
        self.assert_no_full_contract_dump(run)
        self.assert_host_view(run, self.DEFAULT_PROFILE)
        self.assert_no_private_capability(run)

    # 2 — Route must precede investigation; investigate-first Blocks (routing reason, action-local)
    def test_investigate_before_route_blocks_with_routing_reason(self):
        drv = self.bind_driver(
            "D6", "case-2",
            "Investigate-first Blocks with exact single route.required reason payload, affected "
            "values, ordered next_actions/sections, and exact relevant sections (ordered where "
            "contract order matters)")
        run_id = self.start_run(drv)
        resp = self.dispatch(drv, observe_action(run_id=run_id, action=action_investigate()))
        result = self.assert_block_only(resp, ["route.required"])
        spec = REASONS["route.required"]
        r = result["reasons"][0]
        self.assertEqual(r["parameters"], {}, "route.required parameters must be empty")
        # route.required requires nonempty affected.obligation_id correlated to an obligation
        # in the returned RunView (D4-S2 correction).
        affected = r.get("affected", {})
        obligation_id = affected.get("obligation_id")
        self.assertTrue(obligation_id,
                        "route.required must carry a nonempty affected.obligation_id")
        run = result["run"]
        active_obs = run.get("obligations", {}).get("active", [])
        deferred_obs = run.get("obligations", {}).get("deferred", [])
        all_obs = active_obs + deferred_obs
        matched = [o for o in all_obs if o.get("id") == obligation_id]
        self.assertTrue(matched,
                        f"route.required affected.obligation_id {obligation_id!r} must "
                        f"correlate to an obligation in the returned RunView")
        self.assertEqual(r["next_actions"], spec["next_actions"],
                         "route.required next action must be action-local (route.record)")
        self.assertEqual(r["sections"], spec["sections"],
                         "route.required sections must match registry order")
        # Exact relevant sections — ordered, not sorted.
        self.assertEqual(run["contract"]["relevant_sections"], spec["sections"],
                         "investigate-first must inject only the route section in exact order")

    # 3 — Valid route then investigation proceeds; a late route is a permanent violation
    def test_route_then_investigate_proceeds_late_route_blocks(self):
        drv = self.bind_driver(
            "D6", "case-3",
            "Graph not required here. Record route; investigation must Allow non-converged, and a "
            "subsequent route must Block with exact permanent route.late payload without replacing "
            "either first-write-wins witness, leaving the exact full obligation rows from "
            "route+investigate unchanged")
        run_id = self.start_run(drv)
        # Capture the RunView before route to derive obligation IDs changed by route+investigate.
        before_run = self.dispatch(drv, get_run(run_id=run_id))["result"]["run"]
        before_idx = self._obligation_index(before_run)
        # Record route and capture the returned RunView.
        route_resp = self.require_route_admitted(drv, run_id)
        route_run = route_resp["result"]["run"]
        route_idx = self._obligation_index(route_run)
        # Derive route-changed obligation IDs by comparing full obligation rows (added OR changed,
        # active+deferred) across before→route public RunViews (D4-S2 correction).
        route_changed_ids = self._changed_obligation_ids(before_idx, route_idx)
        self.assertTrue(route_changed_ids,
                        "route must add or change public RunView obligations")
        invest = self.dispatch(drv, observe_action(run_id=run_id, action=action_investigate()))
        # Investigation must Allow non-converged (derived obligation state, not private IDs).
        self.assertEqual(invest["result"]["type"], "Allow",
                         "a valid route then investigation must be admitted (Allow), not Block")
        self.assert_allow(invest, converged=False)
        # Derive investigation-changed obligation IDs by comparing full obligation rows (added OR
        # changed, active+deferred) across route→investigate public RunViews (D4-S2 correction).
        invest_run = invest["result"]["run"]
        invest_idx = self._obligation_index(invest_run)
        invest_changed_ids = self._changed_obligation_ids(route_idx, invest_idx)
        self.assertTrue(invest_changed_ids,
                        "investigation must add or change public RunView obligations")
        changed_ids = route_changed_ids | invest_changed_ids
        # Before the first late route, capture the exact full obligation rows from invest_idx for
        # every route-changed|invest-changed obligation id (D4-S2 correction).
        changed_rows = {oid: invest_idx[oid] for oid in changed_ids}
        # A late route is a permanent reported violation (route.late).
        late = self.dispatch(drv, observe_action(run_id=run_id, action=action_route(reason="after-the-fact")))
        late_result = self.assert_block_reason(late, "route.late")
        late_run = late_result["run"]
        self.assertEqual(late_result["reasons"][0]["next_actions"],
                         REASONS["route.late"]["next_actions"],
                         "route.late next_actions must match registry order")
        self.assertEqual(late_result["reasons"][0]["parameters"], {},
                         "route.late parameters must be empty")
        # Late route must retain the derived obligation IDs (D4-S2 correction).
        late_idx = self._obligation_index(late_run)
        late_ids = set(late_idx.keys())
        self.assertTrue(changed_ids <= late_ids,
                        "a late route must retain the obligation IDs derived from route+investigate")
        # A late route must leave the exact full obligation rows from route+investigate unchanged,
        # not merely retain the derived obligation id subset (D4-S2 correction).
        self.assertEqual(changed_rows, {oid: late_idx.get(oid) for oid in changed_ids},
                         "a late route must leave the exact full obligation rows from "
                         "route+investigate unchanged, not only the id subset")
        # Expose affected obligation_id correlated to the returned violated obligation (D4-S2
        # correction). No invented names/private IDs.
        affected = late_result["reasons"][0].get("affected", {})
        late_obligation_id = affected.get("obligation_id")
        self.assertTrue(late_obligation_id,
                        "route.late must carry a nonempty affected.obligation_id")
        violated = [o for o in late_run.get("obligations", {}).get("active", [])
                    if o.get("id") == late_obligation_id and o.get("status") == "violated"]
        self.assertTrue(violated,
                        f"route.late affected.obligation_id {late_obligation_id!r} must "
                        f"correlate to a violated obligation in the returned RunView")
        # Prove permanent stable route.late across a repeat: a second late route produces
        # the same permanent Block with identical reason payload and unchanged obligations.
        late2 = self.dispatch(drv, observe_action(
            run_id=run_id, action=action_route(reason="again-too-late")))
        late2_result = self.assert_block_reason(late2, "route.late")
        self.assertEqual(late2_result["reasons"], late_result["reasons"],
                         "repeated late route must have identical reason payload")
        self.assertEqual(late2_result["run"].get("obligations", {}),
                         late_run.get("obligations", {}),
                         "a repeated late route must not change the derived obligation snapshot")

    # 4 — Claim state is derived; without committed approved scope convergence is refused
    def test_claim_state_derived_committed_scope_gates(self):
        drv = self.bind_driver(
            "D7", "case-4",
            "First admit a valid one-gating-claim graph; without research, Evaluate must Block "
            "specifically on that claim with claim.research_missing, and GetArgument must derive "
            "it open/gating")
        run_id = self.start_run(drv)
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
        root_id = graph["root"]
        # Without research, Evaluate must Block specifically on that claim.
        resp = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        result = self.assert_block_reason(resp, "claim.research_missing")
        # Correlate nonempty affected.obligation_id to returned RunView obligation (D4-S2
        # correction). No invented empirica/{claim} formula.
        aff = result["reasons"][0].get("affected", {})
        obligation_id = aff.get("obligation_id")
        self.assertTrue(obligation_id,
                        "claim.research_missing must carry a nonempty affected.obligation_id")
        run = result["run"]
        all_obs = (run.get("obligations", {}).get("active", [])
                   + run.get("obligations", {}).get("deferred", []))
        matched = [o for o in all_obs if o.get("id") == obligation_id]
        self.assertTrue(matched,
                        f"claim.research_missing affected.obligation_id {obligation_id!r} must "
                        f"correlate to an obligation in the returned RunView")
        # Prove the target claim through the single typed ArgumentView claim/state.
        arg_resp = self.dispatch(drv, get_argument(run_id=run_id))
        arg = self.assert_argument_view(arg_resp["result"])
        claims = [c for c in arg["claims"] if c["claim_id"] == root_id]
        self.assertEqual(len(claims), 1, "GetArgument must list exactly one claim for the root")
        self.assertEqual(claims[0]["state"], "open",
                         "claim must be derived open without research")
        self.assertTrue(claims[0]["gating"], "claim must be gating")

    # 5 — Malformed/missing selected graph fails closed with a structured reason
    def test_malformed_missing_selected_graph_fails_closed(self):
        # Independent fresh variants for missing selected graph and malformed selected graph.
        # The missing variant Evaluate/GetRun without submitting any graph; the malformed variant
        # submits a malformed graph. Each fails closed with exact graph.invalid, no generic
        # exception/fallback.
        for label in ("missing", "malformed"):
            with self.subTest(variant=label):
                drv = self.bind_driver(
                    "D7", "case-5",
                    "Independent fresh variants for missing and malformed selected graph each "
                    "fail closed with exact graph.invalid, no generic exception/fallback")
                run_id = self.start_run(drv)
                if label == "missing":
                    # Missing variant: Evaluate without submitting any graph.
                    resp = self.dispatch(drv, evaluate(
                        run_id=run_id, intent="report_convergence"))
                else:
                    # Malformed variant: submit a malformed graph.
                    resp = self.dispatch(drv, observe_action(
                        run_id=run_id, action=action_graph(payload={"malformed": True})))
                self.assert_block_only(resp, ["graph.missing" if label == "missing"
                                               else "graph.invalid"])

    def test_structurally_invalid_dependency_graphs_are_rejected_without_replacement(self):
        """Seam 4A: the selected argument is one strict root-connected dependency DAG."""
        def claim(cid):
            return {"id": cid, "text": f"Claim {cid}", "gating": True,
                    "kind": "ordinary"}
        variants = {
            "unknown-endpoint": {"root": "C0", "claims": [claim("C0")],
                                 "edges": [{"from": "C0", "to": "C1",
                                            "type": "SupportedBy"}]},
            "self-loop": {"root": "C0", "claims": [claim("C0")],
                          "edges": [{"from": "C0", "to": "C0",
                                     "type": "SupportedBy"}]},
            "duplicate-edge": {"root": "C0", "claims": [claim("C0"), claim("C1")],
                               "edges": [{"from": "C0", "to": "C1",
                                          "type": "SupportedBy"},
                                         {"from": "C0", "to": "C1",
                                          "type": "SupportedBy"}]},
            "cycle": {"root": "C0", "claims": [claim("C0"), claim("C1")],
                      "edges": [{"from": "C0", "to": "C1", "type": "SupportedBy"},
                                {"from": "C1", "to": "C0", "type": "SupportedBy"}]},
            "detached": {"root": "C0", "claims": [claim("C0"), claim("C1")],
                         "edges": []},
            "in-context-edge": {"root": "C0", "claims": [claim("C0"), claim("C1")],
                                "edges": [{"from": "C0", "to": "C1",
                                           "type": "InContextOf"}]},
        }
        for label, candidate in variants.items():
            with self.subTest(variant=label):
                drv = self.bind_driver(
                    "D7", "seam-4a",
                    "Invalid dependency shape Blocks as graph.invalid and cannot replace the "
                    "previous selected graph")
                run_id = self.start_run(drv)
                self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
                before = self.dispatch(drv, get_argument(run_id=run_id))["result"]["argument"]
                response = self.dispatch(drv, observe_action(
                    run_id=run_id, action=action_graph(payload=candidate)))
                self.assert_block_only(response, ["graph.invalid"])
                after = self.dispatch(drv, get_argument(run_id=run_id))["result"]["argument"]
                self.assertEqual(after, before,
                                 "an invalid candidate must not replace the selected graph")

    def test_branching_and_shared_dependency_dag_is_admitted(self):
        def claim(cid):
            return {"id": cid, "text": f"Claim {cid}", "gating": True,
                    "kind": "ordinary"}
        graph = {"root": "C0", "claims": [claim(cid) for cid in ("C0", "C1", "C2", "C3")],
                 "edges": [
                     {"from": "C0", "to": "C1", "type": "SupportedBy"},
                     {"from": "C0", "to": "C2", "type": "SupportedBy"},
                     {"from": "C1", "to": "C3", "type": "SupportedBy"},
                     {"from": "C2", "to": "C3", "type": "SupportedBy"},
                 ]}
        drv = self.bind_driver("D7", "seam-4a",
                               "A rooted branching DAG with a shared dependency is valid")
        run_id = self.start_run(drv)
        response = self.dispatch(drv, observe_action(run_id=run_id,
                                                     action=action_graph(payload=graph)))
        self.assert_allow(response, converged=False)
        argument = self.dispatch(drv, get_argument(run_id=run_id))["result"]["argument"]
        self.assertEqual(argument["edges"], graph["edges"])

    def test_scoped_support_dependencies_are_conjunctive_and_leaf_explained(self):
        def claim(cid, kind="ordinary"):
            return {"id": cid, "text": f"Claim {cid}", "gating": True, "kind": kind}

        def research(drv, run_id, cid, result="supports"):
            return self.dispatch(drv, observe_action(
                run_id=run_id, action=action_research(
                    claim_id=cid, source_kind="code", result=result)))

        # Own evidence plus only one of two required supports is insufficient.
        drv = self.bind_driver("D7", "seam-4b", "Scoped supports are conjunctive")
        run_id = self.start_run(drv)
        graph = {"root": "C0", "claims": [claim("C0"), claim("C1"), claim("C2")],
                 "edges": [{"from": "C0", "to": "C1", "type": "SupportedBy"},
                           {"from": "C0", "to": "C2", "type": "SupportedBy"}]}
        self.require_graph_admitted(drv, run_id, graph)
        research(drv, run_id, "C0")
        research(drv, run_id, "C1")
        self.assertEqual(self.get_argument_claim(drv, run_id, "C0")["state"], "open")
        result = self.assert_block_reason(
            self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence")),
            "claim.research_missing")
        self.assertEqual(result["reasons"][0]["affected"], {"obligation_id": "claim:C2"})
        research(drv, run_id, "C2")
        self.assertEqual(self.get_argument_claim(drv, run_id, "C0")["state"], "approved")

        # Approved children cannot substitute for the parent's own evidence.
        own_drv = self.bind_driver("D7", "seam-4b", "Parent keeps its own evidence requirement")
        own_run = self.start_run(own_drv)
        self.require_graph_admitted(own_drv, own_run, graph)
        research(own_drv, own_run, "C1")
        research(own_drv, own_run, "C2")
        self.assertEqual(self.get_argument_claim(own_drv, own_run, "C0")["state"], "open")
        own_result = self.assert_block_reason(
            self.dispatch(own_drv, evaluate(run_id=own_run, intent="report_convergence")),
            "claim.research_missing")
        self.assertEqual(own_result["reasons"][0]["affected"], {"obligation_id": "claim:C0"})

        # A refuted child blocks support but does not refute its parent.
        refute_drv = self.bind_driver("D7", "seam-4b", "Refutation never propagates upward")
        refute_run = self.start_run(refute_drv)
        refute_graph = {"root": "P", "claims": [claim("P"), claim("R")],
                        "edges": [{"from": "P", "to": "R", "type": "SupportedBy"}]}
        self.require_graph_admitted(refute_drv, refute_run, refute_graph)
        research(refute_drv, refute_run, "P")
        research(refute_drv, refute_run, "R", "refutes")
        self.assertEqual(self.get_argument_claim(refute_drv, refute_run, "P")["state"], "open")
        self.assertEqual(self.get_argument_claim(refute_drv, refute_run, "R")["state"], "discarded")

        # Dependency-only human hold names the supporting claim, not missing parent evidence.
        hold_drv = self.bind_driver("D7", "seam-4b", "Dependency residual names its leaf")
        hold_run = self.start_run(hold_drv)
        hold_graph = {"root": "P", "claims": [claim("P"), claim("H", "needs-decision")],
                      "edges": [{"from": "P", "to": "H", "type": "SupportedBy"}]}
        self.require_graph_admitted(hold_drv, hold_run, hold_graph)
        research(hold_drv, hold_run, "P")
        self.assertEqual(self.get_argument_claim(hold_drv, hold_run, "P")["state"], "open")
        hold_result = self.assert_block_reason(
            self.dispatch(hold_drv, evaluate(run_id=hold_run, intent="report_convergence")),
            "claim.human_decision")
        self.assertEqual(hold_result["reasons"][0]["affected"], {"obligation_id": "claim:H"})

        own_hold_drv = self.bind_driver("D7", "seam-4b",
                                        "Dependency evaluation preserves parent human hold")
        own_hold_run = self.start_run(own_hold_drv)
        own_hold_graph = {"root": "H", "claims": [claim("H", "needs-decision"), claim("O")],
                          "edges": [{"from": "H", "to": "O", "type": "SupportedBy"}]}
        self.require_graph_admitted(own_hold_drv, own_hold_run, own_hold_graph)
        self.assertEqual(self.get_argument_claim(
            own_hold_drv, own_hold_run, "H")["state"], "blocked")

    def test_discard_pruning_is_path_sensitive_and_never_vacuously_converges(self):
        def claim(cid):
            return {"id": cid, "text": f"Claim {cid}", "gating": True, "kind": "ordinary"}

        drv = self.bind_driver("D7", "seam-4b", "Shared live paths survive branch discard")
        run_id = self.start_run(drv)
        graph = {"root": "P", "claims": [claim(cid) for cid in ("P", "A", "B", "S")],
                 "edges": [{"from": "P", "to": "A", "type": "SupportedBy"},
                           {"from": "P", "to": "B", "type": "SupportedBy"},
                           {"from": "A", "to": "S", "type": "SupportedBy"},
                           {"from": "B", "to": "S", "type": "SupportedBy"}]}
        self.require_graph_admitted(drv, run_id, graph)
        for cid, result in (("P", "supports"), ("A", "refutes"), ("B", "supports")):
            self.dispatch(drv, observe_action(run_id=run_id, action=action_research(
                claim_id=cid, source_kind="code", result=result)))
        self.assertEqual(self.get_argument_claim(drv, run_id, "A")["state"], "discarded")
        self.assertEqual(self.get_argument_claim(drv, run_id, "B")["state"], "open",
                         "shared S remains required through live branch B")

        root_drv = self.bind_driver("D7", "seam-4b", "Discarded root cannot vacuously converge")
        root_run = self.start_run(root_drv)
        root_graph = {"root": "R", "claims": [claim("R"), claim("S")],
                      "edges": [{"from": "R", "to": "S", "type": "SupportedBy"}]}
        self.require_graph_admitted(root_drv, root_run, root_graph)
        self.dispatch(root_drv, observe_action(run_id=root_run, action=action_research(
            claim_id="R", source_kind="code", result="refutes")))
        response = self.dispatch(root_drv, evaluate(
            run_id=root_run, intent="report_convergence"))
        result = self.assert_block_reason(response, "claim.refuted")
        self.assertFalse(result.get("converged", False))
        self.assertEqual(result["reasons"][0]["affected"], {"obligation_id": "claim:R"})

    # 6 — Refuted/discarded claim remains append-only history and needs real refuting evidence
    def test_refuted_claim_append_only_needs_real_evidence(self):
        drv = self.bind_driver(
            "D5", "case-6",
            "Admit graph before refuting research; typed refuting research artifact remains "
            "append-only; GetArgument derives the claim discarded/refuted with matching evidence "
            "digest; terminal evaluation reports exact claim.refuted/stopped_residual "
            "non-convergence")
        run_id = self.start_run(drv)
        # Research-only terminal (refuting research, no spike): claim kind is ordinary.
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))
        root_id = graph["root"]
        # Admit graph before refuting research.
        self.dispatch(drv, observe_action(run_id=run_id, action=action_research(
            claim_id=root_id, source_kind="code", result="refutes")))
        # D2C: read the typed refuting research artifact from ArgumentView.artifacts.
        arts = self.get_argument_artifacts(drv, run_id)
        research = self.find_argument_artifacts(arts, kind="research", claim_id=root_id,
                                                 active=True, outcome="refuting")
        self.assertTrue(research, "real refuting evidence must append a typed active research artifact")
        self.assert_research_artifact(research[-1], claim_id=root_id, outcome="refuting", active=True)
        research_artifact_id = research[-1]["artifact_id"]
        # GetArgument derives the claim discarded/refuted with matching evidence digest.
        arg_resp = self.dispatch(drv, get_argument(run_id=run_id))
        arg = self.assert_argument_view(arg_resp["result"])
        claim = [c for c in arg["claims"] if c["claim_id"] == root_id]
        self.assertTrue(claim, "GetArgument must list the refuted claim")
        self.assertEqual(claim[0]["state"], "discarded",
                         "a refuted claim must be derived discarded")
        # D2C invariant §10: evidence_digest equals the canonical registry digest of the
        # exact ordered active_evidence_ids (replaces the prior self-comparison tautology).
        self.assert_evidence_digest(claim[0])
        self.assertIn(research_artifact_id, claim[0]["active_evidence_ids"],
                      "the refuting research artifact must be in the active evidence set")
        # Terminal evaluation reports exact claim.refuted/stopped_residual non-convergence.
        stop_resp = self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))
        stop_result = stop_resp["result"]
        self.assertFalse(stop_result.get("converged", False),
                         "a refuted root must not converge")
        self.assert_status(stop_result["run"], "stopped_residual")
        residual_codes = {r["code"] for r in stop_result["run"].get("residuals", [])}
        self.assertIn("claim.refuted", residual_codes,
                      "terminal evaluation must report claim.refuted residual")

        # Simultaneous supporting and refuting research is not resolved by choosing refutation.
        conflict_drv = self.bind_driver(
            "D5", "case-6",
            "Complete active supporting and refuting research remains unresolved as the exact "
            "evidence.conflict reason; it must not project approved or discarded")
        conflict_run = self.start_run(conflict_drv)
        conflict_graph = self.require_graph_admitted(
            conflict_drv, conflict_run, canonical_graph(n_claims=1, kind="ordinary"))
        conflict_root = conflict_graph["root"]
        self.dispatch(conflict_drv, observe_action(
            run_id=conflict_run, action=action_research(
                claim_id=conflict_root, source_kind="code", result="supports")))
        self.dispatch(conflict_drv, observe_action(
            run_id=conflict_run, action=action_research(
                claim_id=conflict_root, source_kind="docs", result="refutes")))
        conflict_claim = self.get_argument_claim(conflict_drv, conflict_run, conflict_root)
        self.assertEqual(conflict_claim["state"], "open",
                         "conflicting research must not select approval or refutation")
        conflict_response = self.dispatch(
            conflict_drv, evaluate(run_id=conflict_run, intent="report_convergence"))
        self.assert_block_reason(conflict_response, "evidence.conflict")
        self.dispatch(conflict_drv, evaluate(run_id=conflict_run, intent="stop"))
        stopped = self.dispatch(conflict_drv, get_run(run_id=conflict_run))["result"]["run"]
        self.assertIn("evidence.conflict", {r["code"] for r in stopped["residuals"]},
                      "honest stop must preserve the conflict residual")


if __name__ == "__main__":
    unittest.main()
