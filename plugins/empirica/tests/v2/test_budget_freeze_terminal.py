"""D4 cases 16–21: budget, freeze, terminal honesty.

Owner: D7/D7-W (evaluation snapshot, budget derivation, freeze first-write-wins, terminal decision,
single-writer transaction; terminal honesty invariant #3 from the final DAG).

D4-S2/D2C: harness results are staged BEFORE dispatching spike_request. Trusted late evidence,
audit, and child terminal events are delivered through the real private composition ingress
(``drv.trusted_evidence_leaf`` / ``drv.trusted_audit_verdict`` / ``drv.trusted_child_event``), not
fake-only telemetry. Cases 17/21 first assert the returned response reflects the delivered event
before inspecting subsequent RunView. D2C ``ArgumentView.artifacts`` is the sole artifact source.
"""
from __future__ import annotations

import unittest

from assertions import (  # noqa: E402
    ConformanceCase, REASONS, STATUSES, action_child_reserve,
    action_evidence_leaf, action_freeze, action_graph, action_research,
    action_spike_request, build_child_event_payload, canonical_graph, evaluate, get_argument, get_run,
    observe_action,
)

_BOUND = "src/mod.py"
_COMMAND = "python -m pytest"

# Terminal non-converged statuses, DERIVED from the registry (every status except active/converged).
TERMINAL_NONCONVERGED = set(STATUSES) - {"active", "converged"}


class BudgetFreezeTerminalTests(ConformanceCase):
    GOAL = "Prove budget, freeze, and terminal honesty hold."

    # 16 — Derivation and spawn limits enforce structured budget Blocks
    def test_derivation_and_spawn_limits_block(self):
        # Independently verify zero spawn budget and exhausted derivation-pass budget.
        # Each variant explicitly configures the budget it claims to exhaust.
        with self.subTest(variant="zero_spawn"):
            drv = self.bind_driver(
                "D7", "case-16",
                "Independently verify zero spawn budget: assert exact "
                "budget.exhausted.parameters.resource, ordered actions/sections, no reservation "
                "on spawn Block")
            run_id = self.start_run(drv, goal=self.GOAL,
                                    budgets={"max_spawns": 0, "max_passes": 5})
            resp = self.dispatch(drv, observe_action(run_id=run_id, action=action_child_reserve(
                purpose="audit", role_profile=self.DEFAULT_PROFILE, execution="foreground")))
            result = self.assert_block_reason(resp, "budget.exhausted",
                                               parameters={"resource": "spawn"})
            spec = REASONS["budget.exhausted"]
            self.assertEqual(result["reasons"][0]["next_actions"], spec["next_actions"],
                             "budget.exhausted next_actions must match registry order")
            self.assertEqual(result["reasons"][0]["sections"], spec["sections"],
                             "budget.exhausted sections must match registry order")
            # No reservation on spawn Block — no child admitted.
            self.assertEqual(result["run"].get("children", []),
                             [], "a spawn Block must not admit a child")

        with self.subTest(variant="pass_exhausted"):
            drv = self.bind_driver(
                "D7", "case-16",
                "Independently verify exhausted derivation-pass budget: assert exact "
                "budget.exhausted.parameters.resource, ordered actions/sections, and terminal "
                "stopped_budget/non-converged")
            run_id = self.start_run(drv, goal=self.GOAL,
                                    budgets={"max_spawns": 5, "max_passes": 1})
            # Make semantic progress to consume the single derivation pass.
            graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
            root_id = graph["root"]
            self.require_research_recorded(drv, run_id, root_id)
            drv.workspace_write(_BOUND, b"v1")
            drv.harness_complete(_COMMAND, 0)
            self.dispatch(drv, observe_action(run_id=run_id, action=action_spike_request(
                claim_id=root_id, command=_COMMAND, dependent_files=[_BOUND])))
            # Evaluate to consume the derivation pass (semantic progress).
            self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
            # Stop after pass budget exhausts -> stopped_budget.
            resp = self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))
            result = self.assert_allow(resp, converged=False)
            self.assert_status(result["run"], "stopped_budget")
            spec = REASONS["budget.exhausted"]
            residual = [r for r in result["run"].get("residuals", [])
                        if r["code"] == "budget.exhausted"]
            self.assertTrue(residual, "must carry a budget.exhausted residual")
            self.assertEqual(residual[0]["parameters"].get("resource"), "pass",
                             "budget.exhausted resource must be 'pass'")
            # Exact canonical actions/sections on the budget.exhausted residual (D4-S2 correction).
            self.assertEqual(residual[0].get("next_actions"), spec["next_actions"],
                             "budget.exhausted residual next_actions must match registry order")
            self.assertEqual(residual[0].get("sections"), spec["sections"],
                             "budget.exhausted residual sections must match registry order")
            self.assertFalse(result["converged"], "a budget stop must not converge")

    # 17 — Idle waiting and pending audit/child consume no derivation pass
    def test_idle_and_pending_child_consume_no_pass(self):
        drv = self.bind_driver(
            "D7", "case-17",
            "Admit an actual child and drive canonical reserved→launching→pending through "
            "trusted ingress responses before repeated Evaluate/GetRun polling. Compare exact "
            "pass-use fact before/after; pending/idle polls do not consume a pass")
        run_id = self.start_run(drv, goal=self.GOAL, budgets={"max_passes": 2})
        child_id = self.require_admitted_child(drv, run_id)
        # Drive canonical reserved→launching→pending through trusted ingress responses.
        # First assert the returned response reflects the delivered event.
        resp_launch = drv.trusted_child_event(
            run_id, child_id, build_child_event_payload("launching", native_id="n1"))
        self.assert_valid_response(resp_launch)
        self.assert_child_summary(resp_launch["result"]["run"], child_id, state="launching")
        resp_pending = drv.trusted_child_event(
            run_id, child_id, build_child_event_payload("pending", native_id="n1"))
        self.assert_valid_response(resp_pending)
        self.assert_child_summary(resp_pending["result"]["run"], child_id, state="pending")
        # Require a present exact passes_used operational fact (no fallback zero).
        passes_before = drv.operational_state().get("passes_used")
        self.assertIsNotNone(passes_before,
                             "operational_state must expose a present passes_used fact")
        passes_before = int(passes_before)
        for _ in range(3):
            self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        for _ in range(3):
            self.dispatch(drv, get_run(run_id=run_id))
        passes_after = drv.operational_state().get("passes_used")
        self.assertIsNotNone(passes_after,
                             "operational_state must expose a present passes_used fact after polling")
        passes_after = int(passes_after)
        self.assertEqual(passes_after, passes_before,
                         "pending/idle polls must not consume a derivation pass")

    # 18 — Freeze is first-write-wins under repeated/conflicting requests
    def test_freeze_first_write_wins(self):
        drv = self.bind_driver(
            "D7", "case-18",
            "Admit graph C0, freeze, then add C1/change candidate scope and issue "
            "repeated/conflicting freeze. Assert frozen claim IDs/digest remain byte-for-byte "
            "first-write-wins while C1 is deferred")
        run_id = self.start_run(drv, goal=self.GOAL)
        # Research-only freeze (no spike): claim kind is ordinary.
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))
        root_id = graph["root"]
        self.dispatch(drv, observe_action(run_id=run_id, action=action_research(
            claim_id=root_id, source_kind="code", result="supports")))
        self.assertEqual(self.get_argument_claim(drv, run_id, root_id)["state"], "approved")
        self.require_frozen_scope(drv, run_id)
        # Capture the typed first frozen-scope digest before graph expansion (D4-S2 correction).
        arg_before = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        frozen_digest_before = arg_before["frozen_scope_digest"]
        self.assertIsNotNone(frozen_digest_before,
                             "frozen scope digest must be non-null before expansion")
        # C0 is in the frozen scope (gating); no deferred claims yet.
        c0_before = [c for c in arg_before["claims"] if c["claim_id"] == root_id]
        self.assertTrue(c0_before, "C0 must be in the argument before expansion")
        self.assertTrue(c0_before[0].get("gating"),
                        "C0 must be gating (in frozen scope) before expansion")
        # Add C1: replace graph with two-claim graph (C0 text/digest is stable per canonical builder).
        self.dispatch(drv, observe_action(run_id=run_id,
            action=action_graph(payload=canonical_graph(n_claims=2, kind="ordinary"))))
        # Issue repeated/conflicting freeze.
        self.dispatch(drv, observe_action(run_id=run_id, action=action_freeze()))
        # Assert frozen digest remains byte-for-byte first-write-wins while C1 is deferred.
        arg_after = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        self.assertEqual(arg_after["frozen_scope_digest"], frozen_digest_before,
                         "frozen scope digest must remain byte-for-byte first-write-wins")
        # C0 is still gating (frozen); C1 is deferred (not gating).
        c0_after = [c for c in arg_after["claims"] if c["claim_id"] == root_id]
        c1_after = [c for c in arg_after["claims"] if c["claim_id"] == "C1"]
        self.assertTrue(c0_after, "C0 must be in the argument after expansion")
        self.assertTrue(c0_after[0].get("gating"),
                        "C0 must still be gating (frozen) after a second freeze")
        self.assertTrue(c1_after, "C1 must be in the argument after expansion")
        self.assertFalse(c1_after[0].get("gating", True),
                         "C1 must be deferred (not gating) after a second freeze")
        self.assertEqual(c0_after[0]["state"], "approved",
                         "a newly deferred support must not expand frozen adjudication")

    def test_deferred_intermediary_cannot_shrink_frozen_commitment(self):
        drv = self.bind_driver("D7", "seam-4b",
                               "Frozen IDs remain committed through a deferred intermediary")
        run_id = self.start_run(drv, goal=self.GOAL)
        original = canonical_graph(n_claims=2, kind="ordinary")
        self.require_graph_admitted(drv, run_id, original)
        for cid in (original["root"], "C1"):
            self.dispatch(drv, observe_action(run_id=run_id, action=action_research(
                claim_id=cid, source_kind="code", result="supports")))
        self.require_frozen_scope(drv, run_id)
        before = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        deferred = {"id": "D", "text": "Deferred intermediary", "gating": True,
                    "kind": "ordinary"}
        replacement = {"root": original["root"],
                       "claims": [original["claims"][0], original["claims"][1], deferred],
                       "edges": [
                           {"from": original["root"], "to": "D", "type": "SupportedBy"},
                           {"from": "D", "to": "C1", "type": "SupportedBy"},
                       ]}
        self.assert_allow(self.dispatch(drv, observe_action(
            run_id=run_id, action=action_graph(payload=replacement))), converged=False)
        after = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        self.assertEqual(after["frozen_scope_digest"], before["frozen_scope_digest"])
        rows = {claim["claim_id"]: claim for claim in after["claims"]}
        self.assertTrue(rows[original["root"]]["gating"])
        self.assertTrue(rows["C1"]["gating"],
                        "frozen C1 remains committed despite a deferred path")
        self.assertFalse(rows["D"]["gating"])
        self.assertEqual(rows[original["root"]]["state"], "approved")
        self.assertEqual(rows["C1"]["state"], "approved")

        pruned_drv = self.bind_driver("D7", "seam-4b",
                                      "Discarded ancestor cannot shrink frozen IDs")
        pruned_run = self.start_run(pruned_drv, goal=self.GOAL)
        claims = [{"id": cid, "text": cid, "gating": True, "kind": "ordinary"}
                  for cid in ("P", "A", "F")]
        chain = {"root": "P", "claims": claims,
                 "edges": [{"from": "P", "to": "A", "type": "SupportedBy"},
                           {"from": "A", "to": "F", "type": "SupportedBy"}]}
        self.require_graph_admitted(pruned_drv, pruned_run, chain)
        self.require_frozen_scope(pruned_drv, pruned_run)
        frozen_before = self.assert_argument_view(
            self.dispatch(pruned_drv, get_argument(run_id=pruned_run))["result"])
        self.dispatch(pruned_drv, observe_action(run_id=pruned_run, action=action_research(
            claim_id="A", source_kind="code", result="refutes")))
        frozen_after = self.assert_argument_view(
            self.dispatch(pruned_drv, get_argument(run_id=pruned_run))["result"])
        self.assertEqual(frozen_after["frozen_scope_digest"],
                         frozen_before["frozen_scope_digest"])
        frozen_rows = {claim["claim_id"]: claim for claim in frozen_after["claims"]}
        self.assertTrue(frozen_rows["F"]["gating"],
                        "path pruning cannot remove frozen descendant F from commitment")

    def test_frozen_claim_deletion_fails_closed(self):
        drv = self.bind_driver(
            "D7", "case-18",
            "Freeze C0 and C1, then reject a replacement graph that silently deletes C1; the "
            "committed graph remains selected while graph additions remain covered separately")
        run_id = self.start_run(drv, goal=self.GOAL)
        frozen_graph = self.require_graph_admitted(
            drv, run_id, canonical_graph(n_claims=2, kind="ordinary"))
        frozen_ids = [c["id"] for c in frozen_graph["claims"]]
        self.require_frozen_scope(drv, run_id)
        response = self.dispatch(drv, observe_action(
            run_id=run_id, action=action_graph(
                payload=canonical_graph(n_claims=1, kind="ordinary"))))
        self.assert_block_reason(response, "graph.invalid")
        argument = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        self.assertEqual([c["claim_id"] for c in argument["claims"]], frozen_ids,
                         "rejected deletion must leave the committed graph selected")

    # 19 — Committed frozen scope remains the only gating/audited scope; later claims deferred
    def test_committed_frozen_scope_only_gating_scope(self):
        drv = self.bind_driver(
            "D7", "case-19",
            "Admit C0, freeze it, then add C1 after freeze. Stop/evaluate and GetArgument must "
            "show C0 as the only frozen/audited gating scope, C1 in deferred scope, non-null "
            "distinct frozen/deferred digests, and exact freeze.deferred parameters. Do not "
            "expect deferred residual without creating C1")
        run_id = self.start_run(drv, goal=self.GOAL)
        # Research-only freeze (no spike): claim kind is ordinary.
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))
        root_id = graph["root"]
        self.require_frozen_scope(drv, run_id)
        # Add C1 after freeze.
        two_claim = canonical_graph(n_claims=2, kind="ordinary")
        c1_id = two_claim["claims"][1]["id"]
        self.dispatch(drv, observe_action(run_id=run_id,
            action=action_graph(payload=two_claim)))
        # Stop/evaluate.
        resp = self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))
        result = self.assert_allow(resp, converged=False)
        self.assert_status(result["run"], "stopped_frozen")
        # GetArgument must show C0 as the only frozen/audited claim, C1 in deferred scope.
        arg_resp = self.dispatch(drv, get_argument(run_id=run_id))
        arg = self.assert_argument_view(arg_resp["result"])
        self.assertIsNotNone(arg["frozen_scope_digest"],
                            "frozen scope digest must be non-null")
        self.assertIsNotNone(arg["deferred_scope_digest"],
                             "deferred scope digest must be non-null")
        self.assertNotEqual(arg["frozen_scope_digest"], arg["deferred_scope_digest"],
                             "frozen and deferred digests must be distinct")
        # C0 is the only frozen/audited gating claim; C1 is deferred.
        c0_claim = [c for c in arg["claims"] if c["claim_id"] == root_id]
        c1_claim = [c for c in arg["claims"] if c["claim_id"] == c1_id]
        self.assertTrue(c0_claim, "C0 must be in the argument")
        self.assertTrue(c1_claim, "C1 must be in the argument")
        self.assertTrue(c0_claim[0].get("gating"),
                        "C0 must be gating (frozen/audited)")
        self.assertFalse(c1_claim[0].get("gating", True),
                        "C1 must be deferred (not gating)")
        # Exact freeze.deferred parameters: ordered claim_ids=[C1], deferred_scope_digest.
        residuals = result["run"].get("residuals", [])
        deferred = [r for r in residuals if r["code"] == "freeze.deferred"]
        self.assertTrue(deferred, "later claims must be surfaced as freeze.deferred residuals")
        params = deferred[0].get("parameters", {})
        self.assertEqual(list(params.get("claim_ids", [])), [c1_id],
                         "freeze.deferred must identify exactly the deferred claim C1 in order")
        self.assertEqual(params.get("deferred_scope_digest"), arg["deferred_scope_digest"],
                         "freeze.deferred deferred_scope_digest must equal "
                         "ArgumentView deferred_scope_digest")
        self.assertFalse(result["converged"], "a frozen stop must not converge")

    # 20 — Terminal non-converged runs are honestly non-converged
    def test_terminal_nonconverged_runs_honest(self):
        # Fresh variants establish all canonical terminal non-converged outcomes expressible by D1.
        for label in ("stopped_residual", "stopped_frozen", "stopped_budget"):
            with self.subTest(variant=label):
                drv = self.bind_driver(
                    "D7", "case-20",
                    "Fresh variants establish all canonical terminal non-converged outcomes: "
                    "unresolved/root-refuted → stopped_residual, committed discharged scope plus "
                    "later claim → stopped_frozen, exhausted pass budget → stopped_budget. "
                    "Assert exact status and converged=false; do not invent a failed run status "
                    "absent from the registry")
                run_id = self.start_run(drv, goal=self.GOAL)
                expected_status = self._terminalize(drv, run_id, label)
                self.assertIn(expected_status, TERMINAL_NONCONVERGED,
                              f"{expected_status} must be a canonical terminal non-converged status")

    def _terminalize(self, drv, run_id, variant):
        if variant == "stopped_residual":
            # Research-only terminal (refuting research, no spike): claim kind is ordinary.
            graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))
            root_id = graph["root"]
            self.dispatch(drv, observe_action(run_id=run_id, action=action_research(
                claim_id=root_id, source_kind="code", result="refutes")))
            resp = self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))
            result = self.assert_allow(resp, converged=False)
            self.assert_status(result["run"], "stopped_residual")
            self.assertFalse(result["converged"], "a refuted root must not converge")
            return "stopped_residual"
        elif variant == "stopped_frozen":
            # First discharge frozen C0 as an ordinary claim with supporting research, then
            # add C1 after freeze (review correction). Research-only freeze: claim kind is ordinary.
            graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))
            root_id = graph["root"]
            self.require_research_recorded(drv, run_id, root_id)
            self.require_frozen_scope(drv, run_id)
            self.dispatch(drv, observe_action(run_id=run_id,
                action=action_graph(payload=canonical_graph(n_claims=2, kind="ordinary"))))
            resp = self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))
            result = self.assert_allow(resp, converged=False)
            self.assert_status(result["run"], "stopped_frozen")
            self.assertFalse(result["converged"], "a frozen stop must not converge")
            return "stopped_frozen"
        elif variant == "stopped_budget":
            drv2 = self.bind_driver(
                "D7", "case-20",
                "exhausted pass budget → stopped_budget, exact status and converged=false")
            run_id = self.start_run(drv2, goal=self.GOAL, budgets={"max_spawns": 5, "max_passes": 1})
            graph = self.require_graph_admitted(drv2, run_id, canonical_graph(n_claims=1))
            root_id = graph["root"]
            self.require_research_recorded(drv2, run_id, root_id)
            drv2.workspace_write(_BOUND, b"v1")
            drv2.harness_complete(_COMMAND, 0)
            self.dispatch(drv2, observe_action(run_id=run_id,
                action={"kind": "spike_request", "claim_id": root_id,
                        "command": _COMMAND, "dependent_files": [_BOUND]}))
            self.dispatch(drv2, evaluate(run_id=run_id, intent="report_convergence"))
            resp = self.dispatch(drv2, evaluate(run_id=run_id, intent="stop"))
            result = self.assert_allow(resp, converged=False)
            self.assert_status(result["run"], "stopped_budget")
            self.assertFalse(result["converged"], "a budget stop must not converge")
            return "stopped_budget"

    # 21 — A late result after any terminal state never reconverges or reopens the run
    def test_late_result_after_terminal_never_reconverges(self):
        # For each terminal status on a fresh run, deliver real trusted late evidence, audit, and
        # child terminal events through the private composition ingress; first terminal remains
        # unchanged and no event reopens/reconverges the run.
        for label in ("stopped_residual", "stopped_frozen", "stopped_budget"):
            with self.subTest(variant=label):
                drv = self.bind_driver(
                    "D7", "case-21",
                    "For each terminal status on a fresh run, admit any needed child before "
                    "terminalization, then deliver real trusted late evidence, audit, and child "
                    "terminal events through the private host test seam. Compare complete public "
                    "RunView status/converged and operational terminal fingerprint before and "
                    "after each event: first terminal remains unchanged and no event "
                    "reopens/reconverges the run. An author-forged trusted action or a new child "
                    "reservation is not a substitute for a late result")
                run_id = self.start_run(
                    drv, goal=self.GOAL,
                    budgets={"max_passes": 1} if label == "stopped_budget" else None)
                child_id = self.require_admitted_child(drv, run_id)
                expected_status = self._terminalize_for_late(drv, run_id, label)
                # Assert the setup produced the expected terminal status before deliveries.
                before_get = self.dispatch(drv, get_run(run_id=run_id))
                self.assertEqual(before_get["result"]["run"]["status"], expected_status,
                                 f"setup must produce terminal status {expected_status!r}")
                before_run = before_get["result"]["run"]
                before_state = drv.operational_state()
                # Deliver real trusted late evidence through the private composition ingress.
                # First assert the returned response reflects the delivered event.
                resp_ev = drv.trusted_evidence_leaf(run_id, {"leaf": "late-evidence"})
                self.assert_valid_response(resp_ev)
                self.assertEqual(resp_ev["result"]["type"], "Inert",
                              "a late evidence leaf on a terminal run must be Inert")
                self._assert_terminal_unchanged(drv, run_id, expected_status, before_run, before_state)
                # Deliver real trusted late audit through the private composition ingress.
                resp_audit = drv.trusted_audit_verdict(run_id, child_id, {"verdict": "pass"})
                self.assert_valid_response(resp_audit)
                self.assertEqual(resp_audit["result"]["type"], "Inert",
                              "a late audit verdict on a terminal run must be Inert")
                self._assert_terminal_unchanged(drv, run_id, expected_status, before_run, before_state)
                # Deliver real trusted late child terminal event through the composition ingress.
                resp_child = drv.trusted_child_event(run_id, child_id,
                                                       {"state": "completed", "native_id": "n-late"})
                self.assert_valid_response(resp_child)
                self.assertEqual(resp_child["result"]["type"], "Inert",
                              "a late child event on a terminal run must be Inert")
                self._assert_terminal_unchanged(drv, run_id, expected_status, before_run, before_state)
                # An author-forged trusted action is not a substitute for a late result.
                forged = self.raw_dispatch(drv, observe_action(run_id=run_id,
                    action=action_evidence_leaf(payload={"ok": True})))
                self.assertIn(forged["result"]["type"], ("Block", "Fault", "Inert"),
                              "an author-forged trusted action must not reopen a terminal run")
                # A new child reservation is not a substitute for a late result.
                reservation = self.dispatch(drv, observe_action(run_id=run_id,
                    action=action_child_reserve(purpose="audit",
                                                role_profile=self.DEFAULT_PROFILE,
                                                execution="foreground")))
                self.assertIn(reservation["result"]["type"], ("Block", "Fault", "Inert"),
                              "a new child reservation must not reopen a terminal run")
                self._assert_terminal_unchanged(drv, run_id, expected_status, before_run, before_state)

    def _assert_terminal_unchanged(self, drv, run_id, expected_status, before_run, before_state):
        after_get = self.dispatch(drv, get_run(run_id=run_id))
        after_run = after_get["result"]["run"]
        self.assertEqual(after_run["status"], expected_status,
                         "a late event must not change the terminal status")
        self.assertNotEqual(after_get["result"].get("converged", True), True,
                            "a late event must not reconverge a terminal run")
        # Complete bounded RunView equality.
        self.assertEqual(after_run, before_run,
                         "a late event must not change the complete bounded public RunView")
        after_state = drv.operational_state()
        self.assertEqual(after_state, before_state,
                         "a late event must not change the operational terminal fingerprint")

    def _terminalize_for_late(self, drv, run_id, variant):
        if variant == "stopped_residual":
            # Research-only terminal (refuting research, no spike): claim kind is ordinary.
            graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))
            root_id = graph["root"]
            self.dispatch(drv, observe_action(run_id=run_id, action=action_research(
                claim_id=root_id, source_kind="code", result="refutes")))
            self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))
            return "stopped_residual"
        elif variant == "stopped_frozen":
            # Research-only freeze (no spike): claim kind is ordinary.
            graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))
            root_id = graph["root"]
            self.require_research_recorded(drv, run_id, root_id)
            self.require_frozen_scope(drv, run_id)
            self.dispatch(drv, observe_action(run_id=run_id,
                action=action_graph(payload=canonical_graph(n_claims=2, kind="ordinary"))))
            self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))
            return "stopped_frozen"
        elif variant == "stopped_budget":
            graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
            root_id = graph["root"]
            self.require_research_recorded(drv, run_id, root_id)
            drv.workspace_write(_BOUND, b"v1")
            drv.harness_complete(_COMMAND, 0)
            self.dispatch(drv, observe_action(run_id=run_id,
                action={"kind": "spike_request", "claim_id": root_id,
                        "command": _COMMAND, "dependent_files": [_BOUND]}))
            self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
            self.dispatch(drv, evaluate(run_id=run_id, intent="stop"))
            return "stopped_budget"
        raise ValueError(f"unknown variant {variant!r}")


if __name__ == "__main__":
    unittest.main()
