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
    get_argument,
    action_child_reserve,
    action_investigate,
    action_route,
    get_run,
    observe_action,
)


class BlockerProjectionTests(ConformanceCase):
    def _audit_row(self, run):
        rows = [row for row in run["obligations"]["active"]
                if row["id"] == "obligation.audit"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["required"])
        return rows[0]

    def _assert_audit_parity(self, drv, run_id, expected):
        gate = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        self.assert_block_reason(gate, expected)
        reason = gate["result"]["reasons"][0]
        self.assertEqual(reason["affected"], {"obligation_id": "obligation.audit"})
        row = self._audit_row(gate["result"]["run"])
        self.assertEqual(row["status"], "residual")
        self.assertEqual(row["missing"]["code"], expected)
        self.assertEqual(row["missing"]["parameters"], reason["parameters"])
        self.assertEqual(row["next"], reason["next_actions"])
        stopped = self.stop(drv, run_id)["result"]["run"]
        terminal = self._audit_row(stopped)
        self.assertEqual(terminal["missing"]["code"], expected)
        self.assertEqual(terminal["next"], stopped["next_actions"])

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
        stopped = self.stop(drv, run_id)["result"]["run"]
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
                    audit = self._audit_row(run)
                    self.assertEqual(audit["status"], "residual")
                    self.assertEqual(audit["missing"]["code"], "audit.required")
                    stopped = self.stop(drv, run_id)["result"]["run"]
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
                stopped = self.stop(drv, run_id)["result"]["run"]
                self.assertEqual(stopped["status"], "stopped_residual")
                self.assertEqual([row["code"] for row in stopped["residuals"]], [expected])

    def test_audit_obligation_pending_failed_identity_and_satisfied(self):
        for verdict, identity_variant, expected in (
            (None, None, "audit.pending"),
            ("fail", "distinct", "audit.failed"),
            ("pass", "same_model", "audit.same_model"),
            ("pass", "unverified", "audit.independence_unverified"),
        ):
            with self.subTest(expected=expected):
                drv = self.bind_driver("blocker-projection", expected,
                                       "Audit obligation shares the convergence blocker")
                run_id = self.start_run(drv)
                self.require_audit_scope(drv, run_id)
                child_id = self.require_pending_audit_child(drv, run_id)
                if verdict is not None:
                    self.require_trusted_audit_attribution(
                        drv, run_id, child_id, variant=identity_variant)
                    payload = self.build_audit_verdict_payload(
                        drv, run_id, verdict=verdict, scope_review="pass")
                    self.assertEqual(
                        drv.trusted_audit_verdict(run_id, child_id, payload)["result"]["type"],
                        "Allow")
                self._assert_audit_parity(drv, run_id, expected)

        drv = self.bind_driver("blocker-projection", "satisfied",
                               "Distinct current audit satisfies the audit obligation")
        run_id = self.start_run(drv)
        self.require_audit_scope(drv, run_id)
        child_id = self.require_pending_audit_child(drv, run_id)
        self.require_trusted_audit_attribution(drv, run_id, child_id, variant="distinct")
        payload = self.build_audit_verdict_payload(
            drv, run_id, verdict="pass", scope_review="pass")
        admitted = drv.trusted_audit_verdict(run_id, child_id, payload)["result"]["run"]
        row = self._audit_row(admitted)
        self.assertEqual(row["status"], "satisfied")
        self.assertIsNone(row["missing"])
        converged = self.dispatch(
            drv, evaluate(run_id=run_id, intent="report_convergence"))["result"]
        self.assertTrue(converged["converged"])
        self.assertEqual(self._audit_row(converged["run"])["status"], "satisfied")

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

        for case_drv, case_run, reason in cases:
            with self.subTest(reason=reason):
                self._stop_and_assert_parity(case_drv, case_run, reason)

    def test_a_claim_reason_names_the_claim_it_is_about(self):
        """G15 F-P2-2: "Record research for this claim." left the author asking which one. The contract's parameter names
        it and the text carries it, in the gate's reason and in the obligation row, through a redirect to the claim that
        must be discharged."""
        drv = self.bind_driver("blocker-projection", "named", "Named claim")
        run_id = self.start_run(drv)
        graph = {"root": "P", "claims": [
            {"id": "P", "text": "Parent text", "gating": True, "kind": "ordinary"},
            {"id": "M", "text": "Missing research text", "gating": True, "kind": "ordinary"}],
            "edges": [{"from": "P", "to": "M", "type": "SupportedBy"}]}
        self.require_graph_admitted(drv, run_id, graph)
        self.require_research_recorded(drv, run_id, "P")
        blocked = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))["result"]
        reason = blocked["reasons"][0]
        self.assertEqual((reason["code"], reason["parameters"]), ("claim.research_missing", {"claim_id": "M"}))
        self.assertEqual(reason["message"], "Record research for claim M.")
        rows = {row["id"]: row["missing"] for row in blocked["run"]["obligations"]["active"]}
        self.assertEqual(rows["claim:P"]["parameters"], {"claim_id": "M"})  # the redirect names the target

        drv = self.bind_driver("blocker-projection", "named-refuted", "Named refuted")
        run_id = self.start_run(drv)
        root = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))["root"]
        self.dispatch(drv, observe_action(run_id=run_id, action=action_research(
            claim_id=root, source_kind="code", result="refutes")))
        refuted = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))["result"]["reasons"][0]
        self.assertEqual(refuted["message"], f"Refuted by active evidence; claim {root} still blocks this scope.")

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

        stopped = self.stop(drv, run_id)["result"]["run"]
        claim_residuals = [(row["claim_id"], row["target_claim_id"], row["code"])
                           for row in stopped["residuals"]]
        self.assertEqual(claim_residuals, [
            ("M", "M", "claim.research_missing"),
            ("P", "M", "claim.research_missing"),
            ("R", "R", "evidence.confirmation_required"),
        ])


    def test_INVARIANT_the_progress_basis_is_moved_by_every_recorded_observation_and_by_no_read(self):
        """G17b item 2: `run.basis` (core.evaluation.progress_basis) is the one thing hosts compare to know the run moved.
        Every admitted observation kind changes it; any number of reads (GetRun, GetArgument, EvaluateRun) does not."""
        drv = self.bind_driver("progress-basis", "moves", "the basis moves only with recorded work")
        run_id = self.start_run(drv, budgets={"max_passes": 20})

        def basis() -> str:
            return self.dispatch(drv, get_run(run_id=run_id))["result"]["run"]["basis"]

        def reads_leave_it() -> None:
            seen = basis()
            for _ in range(3):
                self.dispatch(drv, get_run(run_id=run_id))
                self.dispatch(drv, get_argument(run_id=run_id))
                self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
                self.assertEqual(basis(), seen, "a read never moves the basis")

        seen = [basis()]
        reads_leave_it()
        steps = []
        steps.append(("route", lambda: self.require_route_admitted(drv, run_id)))
        steps.append(("graph", lambda: self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="needs-experiment"))))
        root = canonical_graph(n_claims=1, kind="needs-experiment")["root"]
        steps.append(("research", lambda: self.require_research_recorded(drv, run_id, root)))
        steps.append(("spike", lambda: self.require_approved_spike(drv, run_id, root, ["src/mod.py"])))
        steps.append(("freeze", lambda: self.dispatch(drv, observe_action(run_id=run_id, action=action_freeze()))))
        for name, step in steps:
            step()
            current = basis()
            self.assertNotIn(current, seen, f"{name} is recorded work: the basis moves")
            seen.append(current)
            reads_leave_it()


    def test_INVARIANT_the_progress_basis_also_moves_on_receipts_approvals_audit_and_children(self):
        """G17c (Astra CORE3-01): the controls the route/graph/research/spike steps above cannot reach, each through a real
        application transaction and each isolated so that no claim blocker changes alongside and masks the loss of a term:
        a human's receipt on an already-approved claim (the work term), a governance approval on its own (the governance term),
        an audit's launch, attribution and verdict, and a child's state transitions (the children term)."""
        import test_evidence_confirmation as evidence_tests
        from application.governance import transact
        from governance_setup import AUTHOR, SIZED_RATIONALE, TEST_INVOCATION

        fixture = evidence_tests.EvidenceFixture()

        def basis(drv, run) -> str:
            return fixture.run_view(drv, run)["basis"]

        def moved(label, drv, run, action) -> None:
            before = basis(drv, run)
            action()
            self.assertNotEqual(before, basis(drv, run), f"{label}: recorded work must move the basis")

        def unchanged(label, drv, run, action) -> None:
            before = basis(drv, run)
            action()
            self.assertEqual(before, basis(drv, run), f"{label}: must not move the basis")

        # a receipt on an approved claim: the research is recorded and the claim stays approved before and after it
        drv, run = fixture.start()
        fixture.research(drv, run, "code", evidence_tests.WORKSPACE_REF)
        fixture.research(drv, run, "web", evidence_tests.URL)
        record = fixture.last_id(drv, run)
        self.assertEqual(fixture.present_now(drv, run, "receipt-1")["type"], "Allow")
        moved("confirmation receipt on an approved claim", drv, run,
              lambda: fixture.answer(drv, run, "receipt-1", evidence_tests.confirm(record)))
        unchanged("exact receipt replay", drv, run,
                  lambda: fixture.answer(drv, run, "receipt-1", evidence_tests.confirm(record)))

        # a governance approval on its own, apart from sizing, context and presentation
        drv = fixture.bind_driver("g17c", "approval-progress", "governance receipt")
        run = fixture.start_run(drv, invocation={**TEST_INVOCATION, "human_surface": "human"})
        fixture.dispatch(drv, observe_action(run_id=run, action=action_route(reason="review")))
        fixture.dispatch(drv, observe_action(run_id=run, action=action_graph(canonical_graph(kind="ordinary"))))
        fixture.dispatch(drv, observe_action(run_id=run, action={
            "kind": "configure_run", "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2},
            "rationale": SIZED_RATIONALE}))
        coordinator = drv._service._coordinator
        transact(coordinator, run, {"author": AUTHOR, "ingress": "mcp_elicitation"}, context=True)
        governed = fixture.run_view(drv, run)["governance"]
        decision = {"run_id": run, "receipt_id": "config-1", "proposal_digest": governed["proposal_digest"],
                    "plan_revision": governed["plan_revision"], "approval_kind": "host_ui"}
        unchanged("governance presentation", drv, run,
                  lambda: transact(coordinator, run, {**decision, "outcome": "present"}))
        submitted = {**decision, "submission": {"action": "approve",
                                                 "configuration": {"budgets": governed["proposal"]["budgets"]}}}
        moved("isolated governance approval", drv, run, lambda: transact(coordinator, run, submitted))
        unchanged("governance approval replay", drv, run, lambda: transact(coordinator, run, submitted))
        moved("investigation stamp", drv, run,
              lambda: fixture.dispatch(drv, observe_action(run_id=run, action=action_investigate())))

        # an audit: its reservation, the host-observed attribution and the verdict
        drv, run = fixture.start(human_surface="unavailable")
        fixture.research(drv, run, "code", evidence_tests.WORKSPACE_REF)
        seen = basis(drv, run)
        child = fixture.require_pending_audit_child(drv, run)
        self.assertNotEqual(seen, basis(drv, run), "an audit launch and its pending child move the basis")
        moved("host-observed audit attribution", drv, run,
              lambda: fixture.require_trusted_audit_attribution(drv, run, child, variant="distinct"))
        payload = fixture.build_audit_verdict_payload(drv, run, verdict="pass", scope_review=None)
        moved("audit verdict", drv, run, lambda: drv.trusted_audit_verdict(run, child, payload))

        # an investigation child has no audit or claim-blocker side effect: its state alone must move the basis
        drv, run = fixture.start()
        reservation = fixture.dispatch(drv, observe_action(run_id=run, action=action_child_reserve(
            purpose="investigate", resource_class="investigation", role_profile=fixture.DEFAULT_PROFILE,
            execution="foreground")))
        self.assertEqual(reservation["result"]["type"], "Allow", reservation)
        child = reservation["result"]["run"]["children"][-1]["child_id"]
        for state in ("launching", "pending", "completed"):
            moved(f"investigation child {state}", drv, run, lambda: fixture.require_child_state(drv, run, child, state))
        for read in (get_run, get_argument):
            unchanged(f"{read.__name__} x12", drv, run,
                      lambda: [fixture.dispatch(drv, read(run_id=run)) for _ in range(12)])

        # each term on its own, on a real snapshot: other terms (a blocker, a child state) cannot mask the loss of one
        from dataclasses import replace
        from core.evaluation import claim_blockers, derive_claims, progress_basis
        snapshot = fixture.snapshot(drv, run)
        blockers = claim_blockers(snapshot, derive_claims(snapshot))

        def basis_of(changed) -> str:
            return progress_basis(changed, blockers)

        base = basis_of(snapshot)
        # the list is written out here, not read from `PROGRESS_KINDS`: dropping a kind there must fail this test
        for kind in ("graph", "research", "spike_request", "spike", "attribution", "audit_verdict", "evidence_confirmation"):
            with self.subTest(work=kind):
                extra = {"artifact_id": f"sha256:{'e' * 64}", "kind": kind}
                self.assertNotEqual(basis_of(replace(snapshot, history=(*snapshot.history, extra))), base)
        for kind in ("evidence_presentation", "observation_basis"):  # a presentation is a host event, not work
            with self.subTest(not_work=kind):
                extra = {"artifact_id": f"sha256:{'e' * 64}", "kind": kind}
                self.assertEqual(basis_of(replace(snapshot, history=(*snapshot.history, extra))), base)
        for key in ("plan_revision", "proposal_digest"):
            with self.subTest(governance=key):
                governed = {**snapshot.state.governance, key: f"changed-{snapshot.state.governance.get(key)}"}
                self.assertNotEqual(basis_of(replace(snapshot, state=replace(snapshot.state, governance=governed))), base)
        child = {"child_id": "child-x", "purpose": "audit", "resource_class": "audit", "state": "pending"}
        with_child = replace(snapshot, state=replace(snapshot.state, children=(*snapshot.state.children, child)))
        self.assertNotEqual(basis_of(with_child), base, "a child appearing")
        settled = replace(with_child, state=replace(with_child.state, children=(*snapshot.state.children, {**child, "state": "completed"})))
        self.assertNotEqual(basis_of(settled), basis_of(with_child), "a child changing state")


if __name__ == "__main__":
    unittest.main()
