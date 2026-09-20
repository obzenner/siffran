"""D4 cases 7–15: evidence, order, freshness, re-gate, pure-core boundary.

Owner: D5/D5-F (pure evidence/workspace/spike contracts + live observation/freshness/no-I/O proof).
These are the heart of the freshness reversal of the quarantined two-fold ``2F5`` defect: every
state-bearing request must freshly observe every active bound file, and re-gate is requested by a
fresh canonical ``spike_request`` (the ``spike.regate`` path), never an invented wire action.

D4-S2/D2C: harness results are staged BEFORE dispatching ``spike_request``; the application must
append the sealed request before invoking the harness and appending the result. Artifact assertions
use the canonical D2C ``ArgumentView.artifacts`` ordered union (direct field access, ``sequence``
order), never raw ``drv.artifacts()`` or JSON substring search. Cases 1–21 must not call
``drv.artifacts()``.
"""
from __future__ import annotations

import unittest

from assertions import (  # noqa: E402
    ConformanceCase, action_attribution, action_evidence_leaf,
    action_graph, action_spike_request, canonical_graph, evaluate,
    get_argument, get_run, observe_action,
)

_BOUND = "src/mod.py"
_BOUND_C0 = "src/c0.py"
_BOUND_C1 = "src/c1.py"
_COMMAND = "python -m pytest"


class EvidenceFreshnessTests(ConformanceCase):
    GOAL = "Prove the spike freshness contract holds across reads."

    # 7 — Research and spike submitted in separate requests combine on the same active claim
    def test_separate_research_and_spike_combine(self):
        drv = self.bind_driver(
            "D5", "case-7",
            "Admit graph and supporting research in one request, then stage a passing harness and "
            "submit the spike in a separate request. Assert exact sealed request prerequisites and "
            "result binding; GetArgument proves the same gating claim is approved from the complete "
            "active set")
        run_id = self.start_run(drv, goal=self.GOAL)
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
        root_id = graph["root"]
        research_id = self.require_research_recorded(drv, run_id, root_id)
        # Stage a passing harness BEFORE the spike_request (D4-S2).
        drv.workspace_write(_BOUND, b"separate-folds")
        drv.harness_complete(_COMMAND, 0)
        self.dispatch(drv, observe_action(run_id=run_id, action=action_spike_request(
            claim_id=root_id, command=_COMMAND, dependent_files=[_BOUND])))
        # D2C: read artifacts from ArgumentView.artifacts.
        arts = self.get_argument_artifacts(drv, run_id)
        # Assert exact sealed request prerequisites.
        req = self.find_one_argument_artifact(arts, kind="spike_request", claim_id=root_id)
        self.assert_spike_request_artifact(req, claim_id=root_id, command=_COMMAND,
                                           dependent_files=[_BOUND],
                                           prerequisite_ids=[research_id])
        # Assert result binding: same harness_request_id, exact gate, active, file bindings.
        res = self.find_one_argument_artifact(arts, kind="spike", claim_id=root_id, active=True)
        self.assert_spike_result_artifact(res, harness_request_id=req["harness_request_id"],
                                           claim_id=root_id, exit_code=0, spike_gate="pass",
                                           command=_COMMAND, command_digest=req["command_digest"],
                                           prerequisite_ids=req["prerequisite_research_ids"],
                                           active=True)
        self.assertEqual(res["claim_digest"], req["claim_digest"],
                         "spike result claim_digest must match the request")
        bindings = res.get("file_bindings", [])
        self.assertEqual(len(bindings), 1, "spike result must have exactly one file binding")
        self.assertEqual(bindings[0].get("path"), _BOUND,
                         "spike result file binding path must match the dependent file")
        # GetArgument proves the same gating claim is approved from the complete active set.
        claim = self.get_argument_claim(drv, run_id, root_id)
        self.assertEqual(claim["state"], "approved",
                         "the gating claim must be approved from the complete active set")
        self.assertIn(res["artifact_id"], claim["active_evidence_ids"],
                      "the spike result must be in the active evidence set")
        self.assertIn(research_id, claim["active_evidence_ids"],
                      "the research artifact must be in the active evidence set")

    # 8 — Caller-supplied ok, verdict, timestamp, artifact digest, or attribution cannot approve
    def test_caller_supplied_approval_fields_cannot_approve(self):
        for forged_kind, forged_action in (
            ("ok", action_evidence_leaf(payload={"ok": True})),
            ("verdict", action_evidence_leaf(payload={"verdict": "approved"})),
            ("timestamp", action_evidence_leaf(
                payload={"timestamp": "2026-09-14T00:00:00Z"})),
            ("artifact_digest", action_evidence_leaf(
                payload={"artifact_digest": "sha256:" + "0" * 64})),
            ("attribution", action_attribution(
                payload={"actor": "author", "model": "self"}, boundary="application")),
        ):
            with self.subTest(forged=forged_kind):
                drv = self.bind_driver(
                    "D5", "case-8",
                    "For each caller-forged approval field, author-path ObserveAction is "
                    "Block/Fault, artifact history is unchanged, and GetArgument/Evaluate shows "
                    "no approved claim/convergence")
                run_id = self.start_run(drv, goal=self.GOAL)
                self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
                # Capture artifact history before the forged action (D4-S2 correction).
                arts_before = self.get_argument_artifacts(drv, run_id)
                resp = self.raw_dispatch(drv, observe_action(run_id=run_id, action=forged_action))
                self.assertIn(resp["result"]["type"], ("Block", "Fault"),
                              "forged caller-approval evidence must be rejected, not admitted")
                # Artifact history is unchanged after the forged action.
                arts_after = self.get_argument_artifacts(drv, run_id)
                self.assertEqual(arts_after, arts_before,
                                 "a forged caller-approval action must not change artifact history")
                # GetArgument proves no claim is approved.
                arg_resp = self.dispatch(drv, get_argument(run_id=run_id))
                arg = self.assert_argument_view(arg_resp["result"])
                for c in arg["claims"]:
                    self.assertNotEqual(c["state"], "approved",
                                        "no claim may be approved by a forged caller field")
                ev = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
                self.assertNotEqual(ev["result"].get("converged", False), True,
                                    "forged caller approval must not converge the run")

    # 9 — Research is bound to current claim wording; wording change makes it inapplicable
    def test_research_bound_to_claim_wording_change_inapplicable(self):
        drv = self.bind_driver(
            "D5", "case-9",
            "Admit graph/research, then replace the graph with the same ID and changed text/digest; "
            "old research remains in public history but is absent from current active evidence IDs; "
            "exact claim.research_unbound identifies C0")
        run_id = self.start_run(drv, goal=self.GOAL)
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
        root_id = graph["root"]
        old_research_id = self.require_research_recorded(drv, run_id, root_id)
        # Replace the graph with the same ID and changed exact text/digest.
        # Preserve the original D2C claim kind through the wording change (D4-S2 correction).
        reworded = {
            "root": root_id,
            "claims": [{"id": root_id, "text": "reworded claim text", "gating": True,
                        "kind": "needs-experiment"}],
            "edges": [],
        }
        self.dispatch(drv, observe_action(
            run_id=run_id, action=action_graph(payload=reworded)))
        resp = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        result = self.assert_block_reason(resp, "claim.research_unbound")
        # Correlate nonempty affected.obligation_id to returned RunView obligation (D4-S2
        # correction). No invented empirica/{claim} formula.
        aff = result["reasons"][0].get("affected", {})
        obligation_id = aff.get("obligation_id")
        self.assertTrue(obligation_id,
                        "claim.research_unbound must carry a nonempty affected.obligation_id")
        run = result["run"]
        all_obs = (run.get("obligations", {}).get("active", [])
                   + run.get("obligations", {}).get("deferred", []))
        matched = [o for o in all_obs if o.get("id") == obligation_id]
        self.assertTrue(matched,
                        f"claim.research_unbound affected.obligation_id {obligation_id!r} must "
                        f"correlate to an obligation in the returned RunView")
        # Old research remains in public history but is absent from current active evidence IDs.
        arts_after = self.get_argument_artifacts(drv, run_id)
        old_in_history = [a for a in arts_after
                          if a.get("artifact_id") == old_research_id]
        self.assertTrue(old_in_history, "old research must remain in public artifact history")
        self.assertFalse(old_in_history[0].get("active", True),
                         "old research must be inactive after the wording change")
        # The old research is absent from current active evidence IDs; kind preserved (D4-S2).
        claim = self.get_argument_claim(drv, run_id, root_id, kind="needs-experiment")
        self.assertNotIn(old_research_id, claim["active_evidence_ids"],
                         "old research must be absent from current active evidence IDs")

    # 10 — Trusted sealed spike request with nonempty dependent files precedes the harness result
    def test_sealed_spike_request_precedes_harness_result(self):
        drv = self.bind_driver(
            "D5", "case-10",
            "Admit graph/research, stage deterministic completion, dispatch spike_request, and "
            "assert distinct typed request then result artifacts in that exact order with "
            "matching harness_request_id, prerequisite IDs, command/digest and file binding. "
            "This must fail if result handling is omitted or result precedes request")
        run_id = self.start_run(drv, goal=self.GOAL)
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
        root_id = graph["root"]
        research_id = self.require_research_recorded(drv, run_id, root_id)
        drv.workspace_write(_BOUND, b"def f(): pass")
        # Stage deterministic completion BEFORE the spike_request (D4-S2).
        drv.harness_complete(_COMMAND, 0)
        self.dispatch(drv, observe_action(run_id=run_id, action=action_spike_request(
            claim_id=root_id, command=_COMMAND, dependent_files=[_BOUND])))
        # D2C: assert distinct typed request then result in D2C sequence order.
        arts = self.get_argument_artifacts(drv, run_id)
        self.assert_artifact_sequence_order(arts, "spike_request", "spike")
        req = self.find_one_argument_artifact(arts, kind="spike_request", claim_id=root_id)
        res = self.find_one_argument_artifact(arts, kind="spike", claim_id=root_id, active=True)
        # Matching harness_request_id.
        self.assertEqual(res["harness_request_id"], req["harness_request_id"],
                         "spike result harness_request_id must match the request")
        # Prerequisite research IDs, command/digest, file binding in the result.
        self.assertEqual(list(req.get("prerequisite_research_ids", [])), [research_id],
                         "spike_request must carry the ordered prerequisite research artifact IDs")
        self.assertEqual(res["command"], _COMMAND, "spike result command must match")
        self.assertEqual(res["command_digest"], req["command_digest"],
                         "spike result command_digest must match the request")
        self.assertEqual(res["claim_digest"], req["claim_digest"],
                         "spike result claim_digest must match the request")
        bindings = res.get("file_bindings", [])
        self.assertEqual(len(bindings), 1, "spike result must have exactly one file binding")
        self.assertEqual(bindings[0].get("path"), _BOUND,
                         "spike result file binding path must match the dependent file")
        self.assertEqual(res["exit_code"], 0, "spike result exit_code must be 0")
        self.assertEqual(res["spike_gate"], "pass", "spike result spike_gate must be pass")

    # 11 — Passing harness exit is the only machine approval; agent/audit cannot manufacture it
    def test_only_passing_harness_exit_approves(self):
        # Independent fresh runs for forged audit, failing exit, and passing exit.
        for label, do_setup, expect_approved in (
            ("forged_audit", "forged", False),
            ("failing_exit", "failing", False),
            ("passing_exit", "passing", True),
        ):
            with self.subTest(variant=label):
                drv = self.bind_driver(
                    "D5", "case-11",
                    "Only exit 0 yields a spike gate pass and derived approved claim in "
                    "GetArgument; nonzero yields fail and not approved; forged agent/audit "
                    "input never changes machine gate. Do not equate claim approval with final "
                    "run convergence/audit completion")
                run_id = self.start_run(drv, goal=self.GOAL)
                graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
                root_id = graph["root"]
                self.require_research_recorded(drv, run_id, root_id)
                drv.workspace_write(_BOUND, b"v1")
                if do_setup == "forged":
                    # Forged agent/audit input never changes machine gate.
                    self.raw_dispatch(drv, observe_action(run_id=run_id,
                        action=action_evidence_leaf(
                            payload={"verdict": "pass", "approved": True})))
                else:
                    exit_code = 0 if do_setup == "passing" else 1
                    drv.harness_complete(_COMMAND, exit_code)
                    self.dispatch(drv, observe_action(run_id=run_id,
                        action=action_spike_request(
                            claim_id=root_id, command=_COMMAND,
                            dependent_files=[_BOUND])))
                # D2C: inspect the typed spike result and assert exact gate.
                claim = self.get_argument_claim(drv, run_id, root_id)
                if expect_approved:
                    self.assertEqual(claim["state"], "approved",
                                     "exit 0 must yield an approved claim")
                    arts = self.get_argument_artifacts(drv, run_id)
                    res = self.find_one_argument_artifact(arts, kind="spike",
                                                          claim_id=root_id, active=True)
                    self.assertEqual(res["spike_gate"], "pass",
                                     "exit 0 must produce spike_gate=pass")
                    self.assertEqual(res["exit_code"], 0,
                                     "exit 0 must produce exit_code=0")
                else:
                    self.assertNotEqual(claim["state"], "approved",
                                        "nonzero exit or forged audit must not approve the claim")
                    if do_setup == "failing":
                        arts = self.get_argument_artifacts(drv, run_id)
                        res = self.find_one_argument_artifact(arts, kind="spike",
                                                              claim_id=root_id, active=True)
                        self.assertEqual(res["spike_gate"], "fail",
                                         "nonzero exit must produce spike_gate=fail")
                        self.assertNotEqual(res["exit_code"], 0,
                                             "nonzero exit must produce nonzero exit_code")
                # Do not equate claim approval with final run convergence/audit completion.
                ev = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
                self.assertNotEqual(ev["result"].get("converged", False), True,
                                    "claim approval must not equal final run convergence")

    # 12 — Every state-bearing request freshly observes every active bound file (per path)
    def test_state_bearing_requests_freshly_observe_bound_files(self):
        drv = self.bind_driver(
            "D5-F", "case-12",
            "Establish a genuinely approved active spike first. Exercise each state-bearing read "
            "(GetRun, GetArgument, EvaluateRun) and assert workspace observation history grows "
            "for that operation with the exact active bound path; assert RunView/Argument "
            "freshness path/state where the branch exposes it")
        run_id = self.start_run(drv, goal=self.GOAL)
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
        root_id = graph["root"]
        self.require_research_recorded(drv, run_id, root_id)
        self.require_approved_spike(drv, run_id, root_id, [_BOUND], content=b"v1", exit_code=0)
        # Exercise each state-bearing read.
        for label, env in (
            ("GetRun", get_run(run_id=run_id)),
            ("GetArgument", get_argument(run_id=run_id)),
            ("EvaluateRun", evaluate(run_id=run_id, intent="report_convergence")),
        ):
            with self.subTest(read=label):
                before = drv.workspace_observe_history()
                resp = self.dispatch(drv, env)
                after = drv.workspace_observe_history()
                self.assertGreater(len(after), len(before),
                                   f"{label} must grow workspace observation history")
                new_entry = after[-1]
                # Compare exact normalized observation batches, not just membership/counters.
                observed_paths = new_entry[0]
                self.assertEqual(observed_paths, (_BOUND,),
                                 f"{label} must observe exactly the active bound path batch")
                observations = new_entry[1]
                self.assertEqual(observations, ({"path": _BOUND, "state": "present",
                                                 "sha256": observations[0]["sha256"]},),
                                 f"{label} must capture one present bound-file observation")
                # A current observation is not a freshness *change*; RunView changes remains empty.
                self.assertEqual(resp["result"].get("run", {}).get("freshness", {}).get("changes"), [])

    # 13 — edit/missing/unreadable/non_regular/outside_workspace each stale + stable on repeat
    def test_bound_file_changes_immediately_stale_then_stable(self):
        variants = (
            ("edit", lambda drv: drv.workspace_write(_BOUND, b"v2"), "present"),
            ("missing", lambda drv: drv.workspace_delete(_BOUND), "missing"),
            ("unreadable", lambda drv: drv.workspace_error(_BOUND, "permission_denied"),
             "unreadable"),
            ("non_regular", lambda drv: drv.workspace_error(_BOUND, "non_regular"),
             "non_regular"),
            ("outside_workspace", lambda drv: drv.workspace_error(_BOUND, "outside_workspace"),
             "outside_workspace"),
        )
        for name, mutate, expected_state in variants:
            with self.subTest(variant=name):
                drv = self.bind_driver(
                    "D5-F", "case-13",
                    "For each fresh variant edit/missing/unreadable/non_regular/"
                    "outside_workspace, first establish supporting research plus an approved "
                    "passing baseline over v1; mutate only the fake workspace fact; next read "
                    "must Block claim.spike_stale with exact path/state matching RunView "
                    "freshness. Repeating the identical observation remains the same stale "
                    "result and adds no evidence artifact. No duplicate delete/absent "
                    "pseudo-variant")
                run_id = self.start_run(drv, goal=self.GOAL)
                graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
                root_id = graph["root"]
                self.require_research_recorded(drv, run_id, root_id)
                self.require_approved_spike(drv, run_id, root_id, [_BOUND],
                                            content=b"v1", exit_code=0)
                # Mutate only the fake workspace fact.
                mutate(drv)
                resp = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
                result = self.assert_block_reason(resp, "claim.spike_stale")
                # Exact path/state matching RunView freshness.
                changes = result["run"]["freshness"]["changes"]
                stale_change = [c for c in changes if c.get("path") == _BOUND]
                self.assertTrue(stale_change, "claim.spike_stale must name the changed path")
                self.assertEqual(stale_change[0].get("state"), expected_state,
                                 f"freshness state for {name!r} must be {expected_state!r}")
                # Exact ordered freshness changes equal reason parameters changes (D4-S2 correction).
                reason_changes = result["reasons"][0].get("parameters", {}).get("changes", [])
                self.assertEqual(reason_changes, changes,
                                 "claim.spike_stale parameters.changes must equal "
                                 "RunView freshness.changes exactly and in order")
                # Repeating the identical observation: same exact stale fact, no evidence added.
                arts_before_repeat = self.get_argument_artifacts(drv, run_id)
                resp2 = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
                result2 = self.assert_block_reason(resp2, "claim.spike_stale")
                arts_after_repeat = self.get_argument_artifacts(drv, run_id)
                self.assertEqual(arts_after_repeat, arts_before_repeat,
                                 "repeating the identical stale observation must add no evidence")
                # Same exact stale fact on repeat: repeated reason changes exactly equal
                # repeated RunView changes and equal the first exact changes (D4-S2 correction).
                changes2 = result2["run"]["freshness"]["changes"]
                reason_changes2 = result2["reasons"][0].get("parameters", {}).get("changes", [])
                self.assertEqual(reason_changes2, changes2,
                                 "repeated claim.spike_stale parameters.changes must equal "
                                 "repeated RunView freshness.changes exactly and in order")
                self.assertEqual(reason_changes2, reason_changes,
                                 "repeated claim.spike_stale reason changes must equal the "
                                 "first exact changes")
                self.assertEqual(changes2, changes,
                                 "repeated freshness changes must equal the first exact changes")
                stale2 = [c for c in changes2 if c.get("path") == _BOUND]
                self.assertTrue(stale2, "repeated stale must still name the path")
                self.assertEqual(stale2[0].get("state"), expected_state,
                                 "repeated stale state must be identical")

    # 14 — Re-gate runs only stale spikes, appends a new attestation, preserves history, restores
    def test_regate_appends_attestation_preserves_history_restores_approval(self):
        drv = self.bind_driver(
            "D5-F", "case-14",
            "Use two approved gating claims/spikes over separate files. Mutate only C0. Prove "
            "only C0 is stale; stage one completion and submit a fresh canonical spike_request "
            "for C0. Assert exactly one harness invocation and one superseding C0 result, prior "
            "history preserved, C1 result not superseded or rerun, both claims approved/fresh "
            "afterward. Assert approval through GetArgument; final convergence may still await "
            "audit and must not be fabricated")
        run_id = self.start_run(drv, goal=self.GOAL)
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=2))
        root_id = graph["root"]
        c1_id = graph["claims"][1]["id"]
        # Two approved gating claims/spikes over separate files, proved via require_approved_spike.
        self.require_research_recorded(drv, run_id, root_id)
        self.require_approved_spike(drv, run_id, root_id, [_BOUND_C0], content=b"v1", exit_code=0)
        self.require_research_recorded(drv, run_id, c1_id)
        self.require_approved_spike(drv, run_id, c1_id, [_BOUND_C1], content=b"v1", exit_code=0)
        # Prove both baseline claims are approved/fresh before mutation.
        c0_before = self.get_argument_claim(drv, run_id, root_id)
        c1_before = self.get_argument_claim(drv, run_id, c1_id)
        self.assertEqual(c0_before["state"], "approved", "C0 must be approved before mutation")
        self.assertEqual(c1_before["state"], "approved", "C1 must be approved before mutation")
        # Find the baseline C0 spike result artifact_id (for supersedes check).
        arts_before_mutate = self.get_argument_artifacts(drv, run_id)
        old_c0_spike = self.find_one_argument_artifact(arts_before_mutate, kind="spike",
                                                         claim_id=root_id, active=True)
        old_c0_result_id = old_c0_spike["artifact_id"]
        old_c1_spike = self.find_one_argument_artifact(arts_before_mutate, kind="spike",
                                                         claim_id=c1_id, active=True)
        # Mutate only C0.
        drv.workspace_write(_BOUND_C0, b"v2")
        stale = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        stale_result = self.assert_block_reason(stale, "claim.spike_stale")
        # Prove only C0 is stale: the stale change names only C0's file, not C1's.
        stale_paths = {c.get("path") for c in stale_result["run"]["freshness"]["changes"]}
        self.assertIn(_BOUND_C0, stale_paths, "only C0's file must be stale")
        self.assertNotIn(_BOUND_C1, stale_paths, "C1's file must not be stale")
        # Stage one completion and submit a fresh canonical spike_request for C0.
        invocations_before = len(drv.harness_invocations())
        drv.harness_complete(_COMMAND, 0)
        self.dispatch(drv, observe_action(run_id=run_id, action=action_spike_request(
            claim_id=root_id, command=_COMMAND, dependent_files=[_BOUND_C0])))
        # Assert exactly one harness invocation.
        invocations_after = len(drv.harness_invocations())
        self.assertEqual(invocations_after - invocations_before, 1,
                         "re-gate must invoke the harness exactly once")
        # D2C: one superseding C0 result with exact supersedes.
        arts_after = self.get_argument_artifacts(drv, run_id)
        c0_results_after = self.find_argument_artifacts(arts_after, kind="spike",
                                                          claim_id=root_id, active=True)
        self.assertEqual(len(c0_results_after), 1, "exactly one active C0 result after re-gate")
        new_c0_result = c0_results_after[0]
        self.assertEqual(new_c0_result.get("supersedes"), old_c0_result_id,
                         "the new C0 result must supersede the prior C0 result")
        # Prior history preserved: old C0 spike is now inactive.
        old_c0_in_history = [a for a in arts_after
                             if a.get("artifact_id") == old_c0_result_id]
        self.assertTrue(old_c0_in_history, "prior C0 result must remain in public history")
        self.assertFalse(old_c0_in_history[0].get("active", True),
                         "prior C0 result must be inactive after supersession")
        # C1 result not superseded or rerun.
        c1_results_after = self.find_argument_artifacts(arts_after, kind="spike",
                                                          claim_id=c1_id, active=True)
        self.assertEqual(len(c1_results_after), 1, "exactly one active C1 result (unchanged)")
        self.assertEqual(c1_results_after[0]["artifact_id"], old_c1_spike["artifact_id"],
                         "C1 result must be the same artifact (not rerun)")
        # D2C requires every spike result to carry supersedes (digest256 | null).
        # An unsuperseded C1 result carries supersedes=null, not an absent field.
        self.assertIn("supersedes", c1_results_after[0],
                      "D2C requires every spike result to carry supersedes")
        self.assertIsNone(c1_results_after[0]["supersedes"],
                         "C1 unsuperseded result must carry supersedes=null (D2C)")
        # Both claims approved/fresh afterward (GetArgument).
        c0_after = self.get_argument_claim(drv, run_id, root_id)
        c1_after = self.get_argument_claim(drv, run_id, c1_id)
        self.assertEqual(c0_after["state"], "approved",
                         "C0 must be approved after re-gate")
        self.assertEqual(c1_after["state"], "approved",
                         "C1 must be approved after re-gate")
        # Both claims must be fresh afterward: no stale freshness changes for either binding
        # (D4-S2 correction).
        fresh_resp = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        fresh_changes = fresh_resp["result"].get("run", {}).get(
            "freshness", {}).get("changes", [])
        stale_after = {c.get("path") for c in fresh_changes}
        self.assertNotIn(_BOUND_C0, stale_after,
                         "C0 must be fresh after re-gate (no stale change)")
        self.assertNotIn(_BOUND_C1, stale_after,
                         "C1 must be fresh after re-gate (no stale change)")
        # Final convergence may still await audit and must not be fabricated.
        arg_resp = self.dispatch(drv, get_argument(run_id=run_id))
        self.assertNotEqual(arg_resp["result"].get("converged", True), True,
                            "final convergence may still await audit and must not be fabricated")

    # 15 — Pure-core boundary: workspace reads are requested through the fake port, not hidden
    def test_workspace_reads_go_through_fake_port(self):
        drv = self.bind_driver(
            "D5-F", "case-15",
            "With an approved active spike, Evaluate must add an exact workspace "
            "observation-history entry for the active bound path through FakeWorkspace; no "
            "aggregate counter-only assertion")
        run_id = self.start_run(drv, goal=self.GOAL)
        graph = self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1))
        root_id = graph["root"]
        self.require_research_recorded(drv, run_id, root_id)
        self.require_approved_spike(drv, run_id, root_id, [_BOUND], content=b"v1", exit_code=0)
        before = drv.workspace_observe_history()
        self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        after = drv.workspace_observe_history()
        self.assertGreater(len(after), len(before),
                           "Evaluate must add a workspace observation-history entry")
        new_entry = after[-1]
        # Compare exact normalized observation batches, not just membership/counters.
        observed_paths = new_entry[0]
        self.assertEqual(observed_paths, (_BOUND,),
                         "the new observation-history entry must be exactly the active bound path")


if __name__ == "__main__":
    unittest.main()
