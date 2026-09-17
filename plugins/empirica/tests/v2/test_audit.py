"""D4 cases 30–35: audit.

Owner: D9 (audit dossier/independence projection) + D8 (audit child completion path) + D7 (frozen
scope audit coverage). Author never grades its own convergence (invariant #2). Audit child IDs
are SUT-admitted (D4 spec §8A), never fabricated by the test. All real audit/attribution events
use the dedicated private composition ingress (``drv.trusted_audit_verdict`` /
``drv.trusted_attribution``); author-forgery negatives use public dispatch. Independence is never
supplied by the verdict; it is derived from trusted attribution ingress.
"""
from __future__ import annotations

import unittest

from assertions import (  # noqa: E402
    ConformanceCase, UNTRUSTED_CLOSE, UNTRUSTED_OPEN,
    action_attribution, action_audit_verdict, action_configure_run, action_research,
    build_attribution_payload,
    evaluate, get_argument, get_run, observe_action,
)


class AuditTests(ConformanceCase):
    GOAL = "Prove audit independence and coverage."

    def test_covered_actor_attribution_requires_complete_active_evidence_set(self):
        drv = self.bind_driver(
            "D11", "attribution-complete-coverage",
            "Covered actor identity must bind every current approved evidence artifact")
        run_id = self.start_run(drv, goal=self.GOAL)
        scope = self.require_audit_scope(drv, run_id)
        second = self.dispatch(drv, observe_action(
            run_id=run_id, action=action_research(
                claim_id=scope["root_id"], source_kind="web", result="supports",
                payload={"source_ref": "https://example.test/second"})))
        artifacts = second["result"]["run"]
        self.assertIsInstance(artifacts, dict)
        child_id = self.require_pending_audit_child(drv, run_id)
        partial = build_attribution_payload(
            subject_kind="covered_actor", subject_id="author", child_id=None,
            provider_id="p1", model_id="m1", observed_by="host",
            covered_artifact_ids=[scope["c0_artifact_id"]])
        response = drv.trusted_attribution(run_id, partial)
        self.assertEqual(response["result"]["type"], "Fault")
        self.assertEqual(response["result"]["code"], "conflict")
        # The pending audit remains open; malformed coverage cannot establish independence.
        self.assert_child_summary(response["result"]["run"], child_id, state="pending")

    def test_malformed_private_attribution_fails_closed_without_persistence(self):
        drv = self.bind_driver(
            "D11", "trusted-attribution-schema",
            "Private ingress validates canonical payload closure before persistence")
        run_id = self.start_run(drv, goal=self.GOAL)
        self.require_graph_admitted(drv, run_id)
        before = self.snapshot_run_state(drv, run_id)
        response = drv.trusted_attribution(run_id, {"subject_kind": "auditor"})
        self.assertEqual(response["result"]["type"], "Fault")
        self.assertEqual(response["result"]["code"], "invalid_request")
        self.assert_run_state_unchanged(before, self.snapshot_run_state(drv, run_id))

    # 30 — Author cannot submit an admitted audit verdict or trusted attribution
    def test_author_cannot_submit_audit_verdict_or_attribution(self):
        drv = self.bind_driver(
            "D9", "case-30",
            "Author cannot submit an admitted audit verdict or trusted attribution")
        run_id = self.start_run(drv, goal=self.GOAL)
        self.require_graph_admitted(drv, run_id)
        # Snapshot before author attempts: complete RunView, operational, ArgumentView.
        snap_before = self.snapshot_run_state(drv, run_id)
        arg_before = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        # author public attempt for audit_verdict with a fabricated child_id must be
        # Block/Fault. Use a structurally valid D2D audit_verdict payload; the SUT rejects
        # the forged capability_ref, not the payload structure.
        resp_v = self.dispatch(drv, observe_action(run_id=run_id, action=action_audit_verdict(
            child_id="ch-forged", payload={
                "verdict": "pass",
                "findings": ["forged"],
                "argument_digest": "sha256:" + "0" * 64,
                "goal_digest": "sha256:" + "1" * 64,
                "frozen_scope_digest": None,
                "deferred_scope_digest": "sha256:" + "2" * 64,
                "reviewed_claims": [],
                "scope_review": None,
            })))
        self.assertIn(resp_v["result"]["type"], ("Block", "Fault"),
                      "an author-forged audit verdict must be rejected, not admitted as Allow")
        # author public attempt for attribution must be Block/Fault.
        resp_a = self.dispatch(drv, observe_action(run_id=run_id, action=action_attribution(
            payload=build_attribution_payload(
                subject_kind="auditor", subject_id="forged-author",
                child_id="ch-forged", provider_id="p1", model_id="m1",
                observed_by="host", covered_artifact_ids=[]))))
        self.assertIn(resp_a["result"]["type"], ("Block", "Fault"),
                      "an author-forged attribution must be rejected, not admitted")
        # Snapshots unchanged: ArgumentView audit, artifacts, complete RunView, operational
        # fingerprint. No fabricated child ID may affect state.
        snap_after = self.snapshot_run_state(drv, run_id)
        self.assert_run_state_unchanged(snap_before, snap_after)
        arg_after = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        self.assertEqual(arg_after, arg_before,
                         "ArgumentView (audit/artifacts) must be unchanged after author attempts")
        self.assertEqual(snap_after["run"].get("children", []),
                         snap_before["run"].get("children", []),
                         "a fabricated child ID must not affect state")

    # 31 — Dossier binds deferred digests via structured (delimited) residuals; no private/dump
    def test_dossier_binds_deferred_digests_with_untrusted_delimiters(self):
        drv = self.bind_driver(
            "D9", "case-31",
            "GetArgument binds deferred-claim digests as structured (untrusted-data-delimited) "
            "freeze.deferred residuals and the typed ArgumentView, with no private capability or "
            "full dump")
        run_id = self.start_run(drv, goal=self.GOAL)
        # Build C0 ordinary approved, freeze, then add C1 deferred (ordinary).
        scope = self.require_audit_scope(drv, run_id, deferred_kind="ordinary")
        root_id = scope["root_id"]
        c1_id = scope["c1_id"]
        dossier = scope["dossier"]
        # canonical delimiters.
        self.assertEqual(dossier["untrusted_delimiters"],
                         {"open": UNTRUSTED_OPEN, "close": UNTRUSTED_CLOSE},
                         "ArgumentView delimiters must equal the canonical PublicContract values")
        # goal/argument/frozen/deferred digests (non-null, distinct).
        self.assertIsNotNone(dossier["goal_digest"],
                            "goal_digest must be non-null")
        self.assertIsNotNone(dossier["argument_digest"],
                            "argument_digest must be non-null")
        self.assertIsNotNone(dossier["frozen_scope_digest"],
                            "frozen_scope_digest must be non-null")
        self.assertIsNotNone(dossier["deferred_scope_digest"],
                            "deferred_scope_digest must be non-null")
        self.assertNotEqual(dossier["frozen_scope_digest"], dossier["deferred_scope_digest"],
                            "frozen and deferred digests must be distinct")
        # C0 gating=true, C1 false.
        c0 = [c for c in dossier["claims"] if c["claim_id"] == root_id][0]
        c1 = [c for c in dossier["claims"] if c["claim_id"] == c1_id][0]
        self.assertTrue(c0["gating"], "C0 must be gating (frozen)")
        self.assertFalse(c1["gating"], "C1 must be deferred (not gating)")
        # ordered freeze.deferred.parameters={claim_ids:[C1],deferred_scope_digest:<exact>}.
        run = self.dispatch(drv, get_run(run_id=run_id))["result"]["run"]
        deferred = [r for r in run.get("residuals", []) if r["code"] == "freeze.deferred"]
        self.assertTrue(deferred,
                        "the argument view must surface deferred claims as a freeze.deferred residual")
        params = deferred[0].get("parameters", {})
        self.assertEqual(list(params.get("claim_ids", [])), [c1_id],
                         "freeze.deferred claim_ids must be [C1] in order")
        self.assertEqual(params.get("deferred_scope_digest"), dossier["deferred_scope_digest"],
                         "freeze.deferred deferred_scope_digest must equal ArgumentView")
        # D2C artifacts/active digest.
        for claim in dossier["claims"]:
            self.assert_evidence_digest(claim)
        # audit state is exactly 'required' before child admission (no pending child yet).
        self.assertEqual(dossier["audit"]["state"], "required",
                         "audit state must be exactly 'required' before child admission")
        # no full/private dump.
        self.assert_no_private_capability(dossier)
        self.assert_no_full_contract_dump(run)

    # 32 — Audit verdict observed from trusted boundary, admitted once, cannot approve machine evidence
    def test_audit_verdict_admitted_once_cannot_approve_machine_evidence(self):
        drv = self.bind_driver(
            "D9", "case-32",
            "Audit verdict is observed from the trusted host boundary, admitted once, and cannot "
            "approve deterministic machine evidence")
        run_id = self.start_run(drv, goal=self.GOAL)
        # Build frozen scope with C0 ordinary approved and C1 needs-experiment missing machine
        # spike.
        scope = self.require_audit_scope(drv, run_id, deferred_kind="needs-experiment")
        c1_id = scope["c1_id"]
        dossier = scope["dossier"]
        # C1 is explicitly non-gating (deferred after freeze) and non-approved (needs-experiment
        # missing machine spike).
        c1 = [c for c in dossier["claims"] if c["claim_id"] == c1_id][0]
        self.assertFalse(c1["gating"], "deferred C1 must be non-gating (gating=false)")
        self.assertNotEqual(c1["state"], "approved",
                            "C1 needs-experiment must be non-approved without a spike")
        # Admit pending audit child (reserved→launching→pending via private ingress).
        child_id = self.require_pending_audit_child(drv, run_id)
        # Build exact verdict payload from current public argument/claim digests.
        payload = self.build_audit_verdict_payload(drv, run_id, verdict="pass",
                                                    scope_review="pass")
        # Every dossier binding is checked before the verdict can complete the child.
        invalid_payloads = []
        for field in ("argument_digest", "goal_digest", "frozen_scope_digest",
                      "deferred_scope_digest"):
            bad = dict(payload)
            bad[field] = "sha256:" + "9" * 64
            invalid_payloads.append((field, bad))
        bad_claims = dict(payload)
        bad_claims["reviewed_claims"] = []
        invalid_payloads.append(("reviewed_claims", bad_claims))
        bad_scope = dict(payload)
        bad_scope["scope_review"] = "fail"
        invalid_payloads.append(("scope_review", bad_scope))
        for label, invalid in invalid_payloads:
            with self.subTest(invalid_binding=label):
                rejected = drv.trusted_audit_verdict(run_id, child_id, invalid)
                self.assert_fault(rejected)
                self.assert_child_summary(rejected["result"]["run"], child_id, state="pending")
        # Deliver one exact current audit verdict through private ingress.
        resp_first = drv.trusted_audit_verdict(run_id, child_id, payload)
        self.assert_valid_response(resp_first)
        # Trusted audit_verdict is the atomic pending→completed child event. Assert its
        # returned child is completed. Never send a second child completion afterward.
        self.assert_child_summary(resp_first["result"]["run"], child_id, state="completed")
        # Snapshot after first delivery: audit projection, artifact count, child state, budgets,
        # operational fingerprint.
        snap_first = self.snapshot_run_state(drv, run_id)
        arg_first = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        # Duplicate it identically — exact Inert, admitted once.
        resp_dup = drv.trusted_audit_verdict(run_id, child_id, payload)
        self.assert_valid_response(resp_dup)
        self.assert_inert(resp_dup)
        snap_dup = self.snapshot_run_state(drv, run_id)
        self.assert_run_state_unchanged(snap_first, snap_dup)
        arg_dup = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        self.assertEqual(arg_dup, arg_first,
                         "audit projection/artifact count must be unchanged after duplicate")
        # Conflicting verdict: derive from the admitted payload with only the verdict
        # changed (copy dict, not rebuild) so all digests/scope_review/reviewed_claims
        # are identical to the admitted payload — exact Fault, unchanged.
        payload_conflict = dict(payload)
        payload_conflict["verdict"] = "fail"
        resp_conflict = drv.trusted_audit_verdict(run_id, child_id, payload_conflict)
        self.assert_valid_response(resp_conflict)
        self.assert_fault(resp_conflict)
        snap_conflict = self.snapshot_run_state(drv, run_id)
        self.assert_run_state_unchanged(snap_first, snap_conflict)
        # After the conflicting verdict, the full ArgumentView is exactly equal to arg_first
        # (audit/budget/convergence/artifacts unchanged); C1 remains non-approved, proving
        # audit cannot manufacture machine evidence.
        arg_final = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        self.assertEqual(arg_final, arg_first,
                         "full ArgumentView must be exactly unchanged after conflicting verdict")
        c1_final = [c for c in arg_final["claims"] if c["claim_id"] == c1_id][0]
        self.assertNotEqual(c1_final["state"], "approved",
                            "audit cannot manufacture machine evidence: C1 remains non-approved")
        # Run never converges: exact Allow converged=false and stopped_frozen (deferred
        # C1 needs-experiment cannot converge); no .get fallback.
        ev = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        result = self.assert_allow(ev, converged=False)
        self.assert_status(result["run"], "stopped_frozen")

        # A verdict that was exact when admitted becomes stale when active evidence changes.
        drv2 = self.bind_driver("D9", "case-32", "Audit bindings are rechecked at convergence")
        run2 = self.start_run(drv2, goal=self.GOAL)
        scope2 = self.require_audit_scope(drv2, run2)
        child2 = self.require_pending_audit_child(drv2, run2)
        self.require_trusted_audit_attribution(
            drv2, run2, child2, scope2["c0_artifact_id"], variant="decorrelated")
        verdict2 = self.build_audit_verdict_payload(drv2, run2, verdict="pass", scope_review="pass")
        self.assertEqual(drv2.trusted_audit_verdict(run2, child2, verdict2)["result"]["type"], "Allow")
        self.dispatch(drv2, observe_action(run_id=run2, action=action_research(
            claim_id=scope2["root_id"], source_kind="web", result="supports",
            payload={"source_ref": "https://example.test/new-support"})))
        stale = self.dispatch(drv2, evaluate(run_id=run2, intent="report_convergence"))
        self.assert_block_only(stale, ["audit.failed"])

    # 33 — Independence derived from trusted observed attribution only; reported honestly
    def test_independence_derived_reported_honestly(self):
        for variant, expected_independence, expected_reason in (
            ("same_model", "same_model", "audit.same_model"),
            ("unverified", "unverified", "audit.independence_unverified"),
            ("alias", "unverified", "audit.independence_unverified"),
            ("configuration", "unverified", "audit.independence_unverified"),
        ):
            with self.subTest(variant=variant):
                drv = self.bind_driver(
                    "D9", "case-33",
                    "Independence is derived from trusted observed attribution only and reports "
                    "same_model | independence_unverified honestly (never decorrelated by a no-op)")
                run_id = self.start_run(drv, goal=self.GOAL)
                # otherwise approvable frozen scope.
                scope = self.require_audit_scope(drv, run_id)
                c0_artifact_id = scope["c0_artifact_id"]
                child_id = self.require_pending_audit_child(drv, run_id)
                # Establish both covered-actor and auditor normalized identities through
                # trusted ingress (same_model: equal pairs; unverified: one pair null).
                self.require_trusted_audit_attribution(
                    drv, run_id, child_id, c0_artifact_id, variant=variant)
                # exact audit verdict.
                payload = self.build_audit_verdict_payload(drv, run_id, verdict="pass",
                                                            scope_review="pass")
                resp_v = drv.trusted_audit_verdict(run_id, child_id, payload)
                self.assert_valid_response(resp_v)
                self.assert_child_summary(resp_v["result"]["run"], child_id, state="completed")
                # GetArgument audit.independence equals exact variant.
                arg = self.assert_argument_view(
                    self.dispatch(drv, get_argument(run_id=run_id))["result"])
                self.assert_audit_view(arg, independence=expected_independence)
                # Evaluate exact sole matching public reason (no set-of-reasons
                # shortcut); failed independence leaves the run active.
                ev = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
                result = self.assert_block_only(ev, [expected_reason])
                self.assert_status(result["run"], "active")

    # 34 — Same-model/unverified can Block per public reasons; never upgraded by author input
    def test_same_model_unverified_blocks_never_upgraded_by_author(self):
        for variant, expected_independence, expected_reason in (
            ("same_model", "same_model", "audit.same_model"),
            ("unverified", "unverified", "audit.independence_unverified"),
            ("alias", "unverified", "audit.independence_unverified"),
            ("configuration", "unverified", "audit.independence_unverified"),
        ):
            with self.subTest(variant=variant):
                drv = self.bind_driver(
                    "D9", "case-34",
                    "Same-model/unverified independence can Block according to public reasons but "
                    "is never upgraded to passing by author input")
                run_id = self.start_run(drv, goal=self.GOAL)
                scope = self.require_audit_scope(drv, run_id)
                c0_artifact_id = scope["c0_artifact_id"]
                child_id = self.require_pending_audit_child(drv, run_id)
                # Establish trusted same_model or unverified through both covered-actor and
                # auditor trusted ingress.
                self.require_trusted_audit_attribution(
                    drv, run_id, child_id, c0_artifact_id, variant=variant)
                payload = self.build_audit_verdict_payload(drv, run_id, verdict="pass",
                                                            scope_review="pass")
                resp_v = drv.trusted_audit_verdict(run_id, child_id, payload)
                self.assert_valid_response(resp_v)
                self.assert_child_summary(resp_v["result"]["run"], child_id, state="completed")
                # Snapshot before author attempt.
                snap_before = self.snapshot_run_state(drv, run_id)
                arg_before = self.assert_argument_view(
                    self.dispatch(drv, get_argument(run_id=run_id))["result"])
                # Author publicly claims decorrelation via a structurally valid D2D
                # attribution with a distinct provider/model pair — must NOT upgrade
                # independence. The SUT rejects the forged capability_ref.
                resp_author = self.dispatch(drv, observe_action(run_id=run_id,
                    action=action_attribution(
                        payload=build_attribution_payload(
                            subject_kind="auditor", subject_id="author-claimed",
                            child_id=child_id, provider_id="p-decorrelated",
                            model_id="m-decorrelated",
                            observed_by="host", covered_artifact_ids=[]))))
                self.assertIn(resp_author["result"]["type"], ("Block", "Fault", "Inert"),
                              "author-claimed decorrelation must be rejected/ignored")
                # Snapshots unchanged.
                snap_after = self.snapshot_run_state(drv, run_id)
                self.assert_run_state_unchanged(snap_before, snap_after)
                arg_after = self.assert_argument_view(
                    self.dispatch(drv, get_argument(run_id=run_id))["result"])
                self.assertEqual(arg_after, arg_before,
                                 "ArgumentView must be unchanged after author attempt")
                # Exact subsequent audit projection: independence remains the original value,
                # never upgraded to decorrelated.
                self.assert_audit_view(arg_after, independence=expected_independence)
                # Sole exact Block reason remains the original non-decorrelated value.
                ev = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
                self.assert_block_only(ev, [expected_reason])

    def test_independence_is_bound_to_verdict_child_and_reaudit_can_replace_it(self):
        drv = self.bind_driver(
            "D11", "audit-operation-binding",
            "A verdict uses only its own child identity; a later bound re-audit may replace it")
        run_id = self.start_run(drv, goal=self.GOAL)
        scope = self.require_audit_scope(drv, run_id)
        self.dispatch(drv, observe_action(
            run_id=run_id, action=action_configure_run(budgets={"max_spawns": 2})))

        child_a = self.require_pending_audit_child(drv, run_id)
        self.require_trusted_audit_attribution(
            drv, run_id, child_a, scope["c0_artifact_id"], variant="same_model")
        verdict_a = self.build_audit_verdict_payload(
            drv, run_id, verdict="pass", scope_review="pass")
        self.assertEqual(
            drv.trusted_audit_verdict(run_id, child_a, verdict_a)["result"]["type"], "Allow")

        # A later child's identity cannot retroactively decorate child A's verdict.
        child_b = self.require_pending_audit_child(drv, run_id)
        self.require_trusted_audit_attribution(
            drv, run_id, child_b, scope["c0_artifact_id"], variant="decorrelated")
        blocked = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        self.assert_block_only(blocked, ["audit.same_model"])

        # Once child B supplies its own exact verdict, its bound identity may satisfy the audit.
        verdict_b = self.build_audit_verdict_payload(
            drv, run_id, verdict="pass", scope_review="pass")
        self.assertEqual(
            drv.trusted_audit_verdict(run_id, child_b, verdict_b)["result"]["type"], "Allow")
        allowed = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        self.assert_allow(allowed, converged=True)

    # 35 — Frozen scope audit covers committed scope and deferred digest exactly
    def test_frozen_scope_audit_covers_committed_and_deferred(self):
        drv = self.bind_driver(
            "D9", "case-35",
            "Frozen scope audit covers committed scope and deferred digest exactly: a passing "
            "frozen-scope audit with decorrelated independence still leaves stopped_frozen "
            "non-converged; deferred scope cannot become convergence")
        run_id = self.start_run(drv, goal=self.GOAL)
        # Build C0 ordinary approved, freeze, add C1 deferred (ordinary).
        scope = self.require_audit_scope(drv, run_id, deferred_kind="ordinary")
        root_id = scope["root_id"]
        c1_id = scope["c1_id"]
        c0_artifact_id = scope["c0_artifact_id"]
        # admitted pending audit child (reserved→launching→pending via private ingress).
        child_id = self.require_pending_audit_child(drv, run_id)
        # trusted decorrelated identities (both covered-actor and auditor through ingress).
        self.require_trusted_audit_attribution(
            drv, run_id, child_id, c0_artifact_id, variant="decorrelated")
        # Capture typed dossier.
        self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        # Build exact passing verdict binding current argument, goal, frozen/deferred digests,
        # approved gating C0 evidence digest, and scope_review pass. Deferred C1 is bound only
        # by deferred_scope_digest + scope_review, NOT by reviewed_claims.
        payload = self.build_audit_verdict_payload(drv, run_id, verdict="pass",
                                                    scope_review="pass")
        # Deliver exact passing verdict through private ingress; audit verdict atomically
        # completes the child (pending→completed). Never send a second child completion.
        resp_v = drv.trusted_audit_verdict(run_id, child_id, payload)
        self.assert_valid_response(resp_v)
        self.assert_child_summary(resp_v["result"]["run"], child_id, state="completed")
        # GetArgument audit is passed, independence decorrelated, exact reviewed
        # coverage/digests, no stale/extra claim.
        arg = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        audit = self.assert_audit_view(arg, state="passed", independence="decorrelated")
        self.assertEqual(audit["reviewed_argument_digest"], arg["argument_digest"],
                         "reviewed argument digest must match current argument")
        self.assertEqual(audit["reviewed_goal_digest"], arg["goal_digest"],
                         "reviewed goal digest must match current argument")
        self.assertEqual(audit["reviewed_frozen_scope_digest"], arg["frozen_scope_digest"],
                         "reviewed frozen scope digest must match current argument")
        self.assertEqual(audit["reviewed_deferred_scope_digest"], arg["deferred_scope_digest"],
                         "reviewed deferred scope digest must match current argument")
        # reviewed_claims is exactly ordered [C0], with exact current C0 evidence digest.
        # C1 never appears there.
        reviewed = audit["reviewed_claims"]
        reviewed_ids = [r["claim_id"] for r in reviewed]
        self.assertEqual(reviewed_ids, [root_id],
                         "reviewed_claims must be exactly [C0] in order; C1 never appears")
        c0_reviewed = [r for r in reviewed if r["claim_id"] == root_id][0]
        c0_claim = [c for c in arg["claims"] if c["claim_id"] == root_id][0]
        self.assertEqual(c0_reviewed["evidence_digest"], c0_claim["evidence_digest"],
                         "reviewed C0 evidence digest must match current claim digest")
        self.assertNotIn(c1_id, reviewed_ids,
                         "deferred C1 must never appear in reviewed_claims")
        # Deferred C1 is bound only by reviewed_deferred_scope_digest, run residual IDs,
        # and scope_review pass.
        run = self.dispatch(drv, get_run(run_id=run_id))["result"]["run"]
        deferred_residuals = [r for r in run.get("residuals", [])
                              if r["code"] == "freeze.deferred"]
        self.assertTrue(deferred_residuals,
                        "deferred C1 must be bound by a freeze.deferred residual")
        residual_claim_ids = deferred_residuals[0].get("parameters", {}).get("claim_ids", [])
        self.assertEqual(list(residual_claim_ids), [c1_id],
                         "freeze.deferred claim_ids must be exactly [C1] in order")
        self.assertEqual(deferred_residuals[0].get("parameters", {}).get("deferred_scope_digest"),
                         arg["deferred_scope_digest"],
                         "freeze.deferred residual deferred_scope_digest must match ArgumentView")
        # Evaluate remains exact stopped_frozen, converged=false; deferred scope cannot
        # become convergence.
        ev = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        result = self.assert_allow(ev, converged=False)
        self.assert_status(result["run"], "stopped_frozen")


if __name__ == "__main__":
    unittest.main()
