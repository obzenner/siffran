"""Shared claim-blocker derivation and lossless obligation regressions."""
from __future__ import annotations

import unittest

from assertions import (
    ConformanceCase,
    action_freeze,
    action_graph,
    action_research,
    canonical_graph,
    evaluate,
    get_run,
    observe_action,
)


class BlockerProjectionTests(ConformanceCase):
    def _stop_and_assert_parity(self, drv, run_id, expected):
        gate = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        self.assert_block_reason(gate, expected)
        obligation_id = gate["result"]["reasons"][0]["affected"]["obligation_id"]
        run = self.dispatch(drv, get_run(run_id=run_id))["result"]["run"]
        obligation = next(row for row in run["obligations"]["active"]
                          if row["id"] == obligation_id)
        self.assertEqual(obligation["missing"]["code"], expected)
        self.assertEqual(obligation["missing"]["parameters"],
                         gate["result"]["reasons"][0]["parameters"])
        self.assertTrue(obligation["required"])
        self.assertTrue(obligation["next"])
        stopped = self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))["result"]["run"]
        self.assertEqual(stopped["status"], "stopped_residual")
        residual = next(row for row in stopped["residuals"] if row["code"] == expected)
        self.assertEqual(residual["code"], gate["result"]["reasons"][0]["code"])
        self.assertEqual(residual["parameters"], gate["result"]["reasons"][0]["parameters"])
        return stopped, residual

    def test_approved_claim_stop_never_invents_missing_research(self):
        for kind in ("ordinary", "needs-experiment"):
            for frozen in (False, True):
                with self.subTest(kind=kind, frozen=frozen):
                    drv = self.bind_driver("blocker-projection", "approved-stop", "Approved evidence stays lossless")
                    run_id = self.start_run(drv, budgets={"max_passes": 8})
                    graph = self.require_graph_admitted(
                        drv, run_id, canonical_graph(n_claims=1, kind=kind))
                    claim_id = graph["root"]
                    self.require_research_recorded(drv, run_id, claim_id)
                    if kind == "needs-experiment":
                        self.require_approved_spike(drv, run_id, claim_id, ["src/mod.py"])
                    if frozen:
                        self.assert_allow(self.dispatch(
                            drv, observe_action(run_id=run_id, action=action_freeze())),
                            converged=False)
                    before = self.get_argument_claim(drv, run_id, claim_id)
                    self.assertEqual(before["state"], "approved")
                    self.assertTrue(before["active_evidence_ids"])
                    run = self.dispatch(drv, get_run(run_id=run_id))["result"]["run"]
                    obligation = next(row for row in run["obligations"]["active"]
                                      if row["id"] == "claim:" + claim_id)
                    self.assertIsNone(obligation["missing"])
                    self.assertTrue(obligation["observed"])
                    self.assertEqual(obligation["required"],
                                     canonical_graph(n_claims=1, kind=kind)["claims"][0]["text"])
                    self.assert_block_reason(
                        self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence")),
                        "audit.required")
                    stopped = self.dispatch(
                        drv, evaluate(run_id=run_id, intent="stop"))["result"]["run"]
                    residual_codes = [row["code"] for row in stopped["residuals"]]
                    self.assertNotIn("claim.research_missing", residual_codes)
                    self.assertEqual(residual_codes, ["audit.required"])
                    after = self.get_argument_claim(drv, run_id, claim_id)
                    self.assertEqual(after["state"], "approved")
                    self.assertEqual(after["active_evidence_ids"], before["active_evidence_ids"])

    def test_all_approved_stop_reports_failed_and_same_model_audit_blockers(self):
        for verdict, identity_variant, expected in (
            ("fail", "distinct", "audit.failed"),
            ("pass", "same_model", "audit.same_model"),
        ):
            with self.subTest(expected=expected):
                drv = self.bind_driver("blocker-projection", expected, "Stopped residual preserves audit blocker")
                run_id = self.start_run(drv)
                self.require_audit_scope(drv, run_id)
                child_id = self.require_pending_audit_child(drv, run_id)
                self.require_trusted_audit_attribution(
                    drv, run_id, child_id, variant=identity_variant)
                payload = self.build_audit_verdict_payload(
                    drv, run_id, verdict=verdict, scope_review="pass")
                admitted = drv.trusted_audit_verdict(run_id, child_id, payload)
                self.assertEqual(admitted["result"]["type"], "Allow")
                self.assert_block_reason(
                    self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence")),
                    expected)
                stopped = self.dispatch(
                    drv, evaluate(run_id=run_id, intent="stop"))["result"]["run"]
                self.assertEqual(stopped["status"], "stopped_residual")
                self.assertEqual([row["code"] for row in stopped["residuals"]], [expected])

    def test_gate_and_terminal_projection_share_every_claim_reason(self):
        cases = []

        drv = self.bind_driver("blocker-projection", "missing", "Missing research")
        run_id = self.start_run(drv)
        self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))
        cases.append((drv, run_id, "claim.research_missing"))

        drv = self.bind_driver("blocker-projection", "unbound", "Unbound research")
        run_id = self.start_run(drv)
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))
        self.require_research_recorded(drv, run_id, graph["root"])
        revised = canonical_graph(n_claims=1, kind="ordinary")
        revised["claims"][0]["text"] += " revised"
        self.assert_allow(self.dispatch(drv, observe_action(
            run_id=run_id, action=action_graph(payload=revised))), converged=False)
        cases.append((drv, run_id, "claim.research_unbound"))

        drv = self.bind_driver("blocker-projection", "spike-missing", "Missing spike")
        run_id = self.start_run(drv)
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
        self.require_research_recorded(drv, run_id, graph["root"])
        cases.append((drv, run_id, "claim.spike_missing"))

        drv = self.bind_driver("blocker-projection", "spike-stale", "Stale spike")
        run_id = self.start_run(drv)
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
        self.require_research_recorded(drv, run_id, graph["root"])
        self.require_approved_spike(drv, run_id, graph["root"], ["src/stale.py"], content=b"old")
        drv.workspace_write("src/stale.py", b"new")
        cases.append((drv, run_id, "claim.spike_stale"))

        drv = self.bind_driver("blocker-projection", "refuted", "Refuted claim")
        run_id = self.start_run(drv)
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))
        self.dispatch(drv, observe_action(run_id=run_id, action=action_research(
            claim_id=graph["root"], source_kind="code", result="refutes")))
        cases.append((drv, run_id, "claim.refuted"))

        drv = self.bind_driver("blocker-projection", "conflict", "Conflicted claim")
        run_id = self.start_run(drv)
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))
        for source, result in (("code", "supports"), ("docs", "refutes")):
            self.dispatch(drv, observe_action(run_id=run_id, action=action_research(
                claim_id=graph["root"], source_kind=source, result=result)))
        cases.append((drv, run_id, "evidence.conflict"))

        drv = self.bind_driver("blocker-projection", "decision", "Human decision")
        run_id = self.start_run(drv)
        self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="needs-decision"))
        cases.append((drv, run_id, "claim.human_decision"))

        for case_drv, case_run, reason in cases:
            with self.subTest(reason=reason):
                self._stop_and_assert_parity(case_drv, case_run, reason)

    def test_obligations_are_lossless_redirected_and_report_all_blockers(self):
        drv = self.bind_driver("blocker-projection", "obligations", "Lossless obligations")
        run_id = self.start_run(drv)
        graph = {
            "root": "P",
            "claims": [
                {"id": "P", "text": "Parent text", "gating": True, "kind": "ordinary"},
                {"id": "R", "text": "Redirected support text", "gating": True,
                 "kind": "needs-decision"},
                {"id": "M", "text": "Missing research text", "gating": True,
                 "kind": "ordinary"},
            ],
            "edges": [
                {"from": "P", "to": "R", "type": "SupportedBy"},
                {"from": "P", "to": "M", "type": "SupportedBy"},
            ],
        }
        self.require_graph_admitted(drv, run_id, graph)
        self.require_research_recorded(drv, run_id, "P")
        run = self.dispatch(drv, get_run(run_id=run_id))["result"]["run"]
        obligations = {row["id"]: row for row in run["obligations"]["active"]}
        self.assertEqual(obligations["claim:P"]["required"], "Parent text")
        self.assertEqual(obligations["claim:P"]["missing"]["target_claim_id"], "M")
        self.assertEqual(obligations["claim:P"]["missing"]["code"], "claim.research_missing")
        self.assertEqual(obligations["claim:P"]["next"], ["research.record"])
        self.assertEqual(obligations["claim:P"]["observed"][0]["kind"], "research")
        self.assertEqual(obligations["claim:M"]["required"], "Missing research text")
        self.assertEqual(obligations["claim:M"]["missing"]["code"], "claim.research_missing")

        stopped = self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))["result"]["run"]
        claim_residuals = [(row["claim_id"], row["target_claim_id"], row["code"])
                           for row in stopped["residuals"]]
        self.assertEqual(claim_residuals, [
            ("M", "M", "claim.research_missing"),
            ("P", "M", "claim.research_missing"),
            ("R", "R", "claim.human_decision"),
        ])


if __name__ == "__main__":
    unittest.main()
