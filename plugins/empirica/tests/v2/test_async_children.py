"""D4 cases 22–29: async child lifecycle.

Owner: D8 (host-neutral durable async-child state machine, exact admitted-launch/native-start
binding, idempotent completion, reload recovery, timeout/orphan, late-result rule).

Child IDs are the SUT-admitted ids read from the run view, never fabricated by the test. Each
cancel/timeout/orphan variant starts from a fresh driver/run (D4 spec §4). Cases assert ACTUAL
before/after child states and side-effect counts (D4 spec §8A). All child-state transitions use
the real private composition ingress (``drv.trusted_child_event``); author-forgery negatives use
public dispatch.
"""
from concurrent.futures import ThreadPoolExecutor

import unittest

from assertions import (  # noqa: E402
    ConformanceCase, HOST_TIERS, PROFILE_IDS, REASONS,
    action_child_reserve, build_child_event_payload, build_evidence_leaf_payload,
    evaluate, get_argument, get_run, observe_action, profile_tier,
)

# Adverse terminal states that require a recovery_action (D2A §3).
_CHILD_TERMINAL_NEXT = REASONS["child.terminal"]["next_actions"]
_CHILD_TERMINAL_SECTIONS = REASONS["child.terminal"]["sections"]


class AsyncChildrenTests(ConformanceCase):
    GOAL = "Prove the async child lifecycle is durable and exactly-once."

    # 22 — Canonical transitions follow the D2 registry only
    def test_transitions_follow_registry(self):
        drv = self.bind_driver(
            "D8", "case-22",
            "Canonical requested/launching/pending/terminal transitions follow D2 registry only: "
            "a non-canonical transition is rejected")
        run_id = self.start_run(drv, goal=self.GOAL)
        child_id = self.require_admitted_child(drv, run_id)
        # Assert before state: reserved
        before = self.snapshot_run_state(drv, run_id)
        self.assert_child_summary(before["run"], child_id, state="reserved")
        # canonical edge: reserved -> launching through trusted ingress
        resp = drv.trusted_child_event(run_id, child_id,
                                        build_child_event_payload("launching", native_id="n1"))
        self.assert_valid_response(resp)
        self.assert_child_summary(resp["result"]["run"], child_id, state="launching")
        # Assert after state: launching
        after = self.snapshot_run_state(drv, run_id)
        self.assert_child_summary(after["run"], child_id, state="launching")
        # launching -> timed_out is not a registry edge (crash recovery uses orphaned).
        snap_pre = self.snapshot_run_state(drv, run_id)
        resp_bad = drv.trusted_child_event(run_id, child_id,
                                             build_child_event_payload("timed_out", native_id="n1"))
        self.assert_valid_response(resp_bad)
        self.assertIn(resp_bad["result"]["type"], ("Fault", "Block"),
                      "a non-canonical child transition must fail closed")
        snap_post = self.snapshot_run_state(drv, run_id)
        self.assert_run_state_unchanged(snap_pre, snap_post)
        # Iterate at least one accepted terminal path to prove registry transitions are not all
        # rejected: pending -> completed is a canonical registry edge.
        resp_pend = drv.trusted_child_event(run_id, child_id,
                                             build_child_event_payload("pending", native_id="n1"))
        self.assert_valid_response(resp_pend)
        self.assert_child_summary(resp_pend["result"]["run"], child_id, state="pending")
        resp_done = drv.trusted_child_event(run_id, child_id,
                                             build_child_event_payload(
                                                 "completed", native_id="n1",
                                                 result_digest="sha256:" + "a" * 64))
        self.assert_valid_response(resp_done)
        self.assert_child_summary(resp_done["result"]["run"], child_id, state="completed")

    def test_concurrent_audit_reservation_commits_exactly_one_operation(self):
        drv = self.bind_driver(
            "D11", "concurrent-audit-reserve",
            "The coordinator CAS enforces one active audit operation under interleaving")
        run_id = self.start_run(drv, goal=self.GOAL)
        self.require_graph_admitted(drv, run_id)
        self.dispatch(drv, observe_action(
            run_id=run_id,
            action={"kind": "configure_run", "budgets": {"max_spawns": 2}}))
        request = observe_action(run_id=run_id, action=action_child_reserve(
            purpose="audit", role_profile=self.DEFAULT_PROFILE, execution="foreground"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _index: self.dispatch(drv, request), range(2)))
        self.assertEqual(sorted(row["result"]["type"] for row in responses), ["Allow", "Block"])
        final = self.dispatch(drv, get_run(run_id=run_id))["result"]["run"]
        active = [child for child in final["children"]
                  if child["purpose"] == "audit"
                  and child["state"] in {"reserved", "launching", "pending"}]
        self.assertEqual(len(active), 1)

    def test_interrupted_preterminal_reservations_can_be_orphaned(self):
        for source in ("reserved", "launching"):
            with self.subTest(source=source):
                drv = self.bind_driver(
                    "D11", f"orphan-{source}",
                    "Host recovery closes interrupted foreground reservations")
                run_id = self.start_run(drv, goal=self.GOAL)
                child_id = self.require_admitted_child(drv, run_id)
                if source == "launching":
                    self.require_child_state(drv, run_id, child_id, "launching")
                response = drv.trusted_child_event(
                    run_id, child_id,
                    build_child_event_payload("orphaned", native_id=f"recovery-{source}"))
                self.assert_child_summary(response["result"]["run"], child_id, state="orphaned")
                self.assertEqual(response["result"]["type"], "Block")

    # 23 — Admission binds one run-scoped child ID and, where supported, exactly one native ID
    def test_admission_binds_one_child_id(self):
        drv = self.bind_driver(
            "D8", "case-23",
            "Admission binds one run-scoped child ID (a foreign child_id is rejected) and the "
            "private native id is never public")
        run_id = self.start_run(drv, goal=self.GOAL)
        self.require_graph_admitted(drv, run_id)
        resp = self.dispatch(drv, observe_action(run_id=run_id, action=action_child_reserve(
            purpose="audit", role_profile=self.DEFAULT_PROFILE, execution="foreground")))
        children = resp["result"]["run"].get("children", [])
        self.assertEqual(len(children), 1, "one run-scoped child record per reserve")
        self.assert_no_private_capability(children[0])
        child_id = children[0]["child_id"]
        # Drive launching with one host/native binding through trusted ingress.
        resp_launch = drv.trusted_child_event(run_id, child_id,
                                                build_child_event_payload("launching", native_id="n1"))
        self.assert_valid_response(resp_launch)
        self.assert_child_summary(resp_launch["result"]["run"], child_id, state="launching")
        # Repeating identical binding is idempotent.
        resp_dup = drv.trusted_child_event(run_id, child_id,
                                             build_child_event_payload("launching", native_id="n1"))
        self.assert_valid_response(resp_dup)
        self.assert_child_summary(resp_dup["result"]["run"], child_id, state="launching")
        # Conflicting binding (different native_id) fails closed.
        resp_conflict = drv.trusted_child_event(run_id, child_id,
                                                  build_child_event_payload("launching", native_id="n2"))
        self.assert_valid_response(resp_conflict)
        self.assertIn(resp_conflict["result"]["type"], ("Fault", "Block"),
                      "a conflicting native binding must fail closed")
        # Foreign child ID fails closed.
        resp_foreign = drv.trusted_child_event(
            run_id, "foreign-not-admitted",
            build_child_event_payload("completed", native_id="nx",
                                      result_digest="sha256:" + "b" * 64))
        self.assert_valid_response(resp_foreign)
        self.assertIn(resp_foreign["result"]["type"], ("Fault", "Block"),
                      "a child_event for an un-admitted child_id must be rejected")
        # Native ID/private capability never appears in response, compacted view, or ArgumentView.
        self.assert_no_private_capability(resp_launch["result"])
        compacted = drv.compact()
        self.assert_no_private_capability(compacted)
        arg = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        self.assert_no_private_capability(arg)

    # 24 — Completion is exactly once across duplicate and out-of-order events
    def test_completion_exactly_once_duplicate_and_out_of_order(self):
        drv = self.bind_driver(
            "D8", "case-24",
            "Completion is exactly once across duplicate and out-of-order events: a duplicate is "
            "idempotent and a non-identical terminal delivery is a Fault, never a second completion")
        run_id = self.start_run(drv, goal=self.GOAL)
        child_id = self.require_admitted_child(drv, run_id)
        # Drive reserved→launching→pending→completed canonically through trusted ingress.
        self.require_child_state(drv, run_id, child_id, "launching")
        self.require_child_state(drv, run_id, child_id, "pending")
        _RESULT_DIGEST = "sha256:" + "c" * 64
        resp_done = drv.trusted_child_event(run_id, child_id,
                                             build_child_event_payload(
                                                 "completed", native_id="n1",
                                                 result_digest=_RESULT_DIGEST))
        self.assert_valid_response(resp_done)
        self.assert_child_summary(resp_done["result"]["run"], child_id, state="completed")
        # Snapshot complete RunView, operational counters, artifacts/audit state after first
        # completion.
        snap_first = self.snapshot_run_state(drv, run_id)
        arg_first = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        # Identical duplicate is idempotent with all snapshots equal (exact Inert).
        resp_dup = drv.trusted_child_event(run_id, child_id,
                                             build_child_event_payload(
                                                 "completed", native_id="n1",
                                                 result_digest=_RESULT_DIGEST))
        self.assert_valid_response(resp_dup)
        self.assert_inert(resp_dup)
        self.assert_child_summary(resp_dup["result"]["run"], child_id, state="completed")
        snap_dup = self.snapshot_run_state(drv, run_id)
        self.assert_run_state_unchanged(snap_first, snap_dup)
        arg_dup = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        self.assertEqual(arg_dup, arg_first,
                         "ArgumentView (artifacts/audit state) must be unchanged after duplicate")
        # Out-of-order/non-identical terminal is exact Fault and leaves snapshots equal.
        resp_conflict = drv.trusted_child_event(run_id, child_id,
                                                  build_child_event_payload("failed", native_id="n1"))
        self.assert_valid_response(resp_conflict)
        self.assert_fault(resp_conflict)
        snap_conflict = self.snapshot_run_state(drv, run_id)
        self.assert_run_state_unchanged(snap_first, snap_conflict)
        arg_conflict = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        self.assertEqual(arg_conflict, arg_first,
                         "ArgumentView must be unchanged after conflicting terminal")

    # 25 — First terminal event wins; later events change nothing
    def test_first_terminal_wins(self):
        drv = self.bind_driver(
            "D8", "case-25",
            "First terminal event wins; every later event changes neither child state, budget, "
            "audit, nor convergence")
        run_id = self.start_run(drv, goal=self.GOAL)
        child_id = self.require_admitted_child(drv, run_id)
        self.require_child_state(drv, run_id, child_id, "launching")
        self.require_child_state(drv, run_id, child_id, "pending")
        # First terminal event: completed.
        _RESULT_DIGEST = "sha256:" + "d" * 64
        resp_first = drv.trusted_child_event(run_id, child_id,
                                              build_child_event_payload(
                                                  "completed", native_id="n1",
                                                  result_digest=_RESULT_DIGEST))
        self.assert_valid_response(resp_first)
        self.assert_child_summary(resp_first["result"]["run"], child_id, state="completed")
        # Snapshot after first terminal: complete RunView, operational fingerprint, ArgumentView.
        snap_first = self.snapshot_run_state(drv, run_id)
        arg_first = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        # Later child events through private ingress: first terminal child state unchanged,
        # complete RunView and operational fingerprint unchanged. Complete valid D2D payloads.
        for event in (build_child_event_payload("timed_out", native_id="n1"),
                      build_child_event_payload("failed", native_id="n1"),
                      build_child_event_payload("cancelled", native_id="n1")):
            resp = drv.trusted_child_event(run_id, child_id, event)
            self.assert_valid_response(resp)
            self.assert_fault(resp)
            self.assert_child_summary(resp["result"]["run"], child_id, state="completed")
        snap_after_child = self.snapshot_run_state(drv, run_id)
        self.assert_run_state_unchanged(snap_first, snap_after_child)
        # Later audit event through private ingress: structurally valid audit verdict, nothing
        # changes (first-terminal rejection before semantic admission).
        audit_payload = self.build_audit_verdict_payload(drv, run_id, verdict="pass")
        resp_audit = drv.trusted_audit_verdict(run_id, child_id, audit_payload)
        self.assert_valid_response(resp_audit)
        self.assert_fault(resp_audit)
        snap_after_audit = self.snapshot_run_state(drv, run_id)
        self.assert_run_state_unchanged(snap_first, snap_after_audit)
        # Later evidence event through private ingress: structurally valid but deliberately
        # unbound late payload (first-terminal rejection before semantic admission).
        ev_payload = build_evidence_leaf_payload(
            harness_request_id="hreq-late-unbound",
            command_digest="sha256:" + "e" * 64,
            prerequisite_research_ids=["sha256:" + "f" * 64],
            file_bindings=[{"path": "src/late.py", "sha256": "sha256:" + "0" * 64}],
            exit_code=0,
            result_digest="sha256:" + "1" * 64)
        resp_ev = drv.trusted_evidence_leaf(run_id, ev_payload)
        self.assert_valid_response(resp_ev)
        self.assert_inert(resp_ev)
        snap_after_ev = self.snapshot_run_state(drv, run_id)
        self.assert_run_state_unchanged(snap_first, snap_after_ev)
        # ArgumentView (audit/budget/convergence) unchanged.
        arg_after = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        self.assertEqual(arg_after, arg_first,
                         "ArgumentView must be unchanged after all later events")

    # 26 — Pending state survives reload and compaction without duplicate spawn or pass consumption
    def test_pending_survives_reload_and_compaction(self):
        drv = self.bind_driver(
            "D8", "case-26",
            "Pending state survives reload and compaction without duplicate spawn or pass "
            "consumption")
        run_id = self.start_run(drv, goal=self.GOAL)
        child_id = self.require_admitted_child(drv, run_id)
        # advance to pending via trusted ingress: reserved -> launching -> pending
        self.require_child_state(drv, run_id, child_id, "launching")
        self.require_child_state(drv, run_id, child_id, "pending")
        # exact spawns_used/passes_used (no fallback defaults)
        spawns_before = self.require_operational_int(drv, "spawns_used")
        passes_before = self.require_operational_int(drv, "passes_used")
        # Compact and assert public pending child preserved without private dump.
        compacted = drv.compact()
        self.assertIn("children", compacted, "compaction must preserve pending child state")
        kids = self.index_children(compacted)
        self.assertIn(child_id, kids, "compaction must preserve the pending child")
        self.assertEqual(kids[child_id]["state"], "pending",
                         "compaction must preserve the pending child state")
        self.assert_no_private_capability(compacted)
        # Reload, then use reloaded driver for GetRun/Evaluate.
        reloaded = drv.reload()
        resp_get = self.dispatch(reloaded, get_run(run_id=run_id))
        self.assert_child_summary(resp_get["result"]["run"], child_id, state="pending")
        self.dispatch(reloaded, evaluate(run_id=run_id, intent="continue"))
        # Assert pending child survives and both counters unchanged—no duplicate spawn/pass.
        spawns_after = self.require_operational_int(reloaded, "spawns_used")
        passes_after = self.require_operational_int(reloaded, "passes_used")
        self.assertEqual(spawns_after, spawns_before,
                         "reload must not duplicate a spawn")
        self.assertEqual(passes_after, passes_before,
                         "reload must not duplicate a pass")

    # 27 — Launch rejection refunds exactly once; post-start failure does not refund
    def test_launch_rejection_refunds_once_post_start_failure_does_not(self):
        with self.subTest(variant="launch_rejection"):
            drv = self.bind_driver(
                "D8", "case-27",
                "Launch rejection refunds exactly once; replay does not refund twice")
            run_id = self.start_run(drv, goal=self.GOAL, budgets={"max_spawns": 2})
            spawns0 = self.require_operational_int(drv, "spawns_used")
            child_id = self.require_admitted_child(drv, run_id)
            spawns_after_reserve = self.require_operational_int(drv, "spawns_used")
            self.assertEqual(spawns_after_reserve, spawns0 + 1,
                             "reserve must spend exactly spawns0+1")
            # trusted reserved→launch_rejected: exact refund to baseline.
            resp = drv.trusted_child_event(run_id, child_id,
                                            build_child_event_payload("launch_rejected"))
            self.assert_valid_response(resp)
            self.assert_child_summary(resp["result"]["run"], child_id, state="launch_rejected")
            spawns_after_reject = self.require_operational_int(drv, "spawns_used")
            self.assertEqual(spawns_after_reject, spawns0,
                             "launch rejection must refund the spawn exactly once")
            # Replay identical rejection: exact Inert, no second refund/side effect.
            snap_pre = self.snapshot_run_state(drv, run_id)
            arg_pre = self.assert_argument_view(
                self.dispatch(drv, get_argument(run_id=run_id))["result"])
            resp_replay = drv.trusted_child_event(run_id, child_id,
                                                   build_child_event_payload("launch_rejected"))
            self.assert_valid_response(resp_replay)
            self.assert_inert(resp_replay)
            snap_post = self.snapshot_run_state(drv, run_id)
            self.assert_run_state_unchanged(snap_pre, snap_post)
            arg_post = self.assert_argument_view(
                self.dispatch(drv, get_argument(run_id=run_id))["result"])
            self.assertEqual(arg_post, arg_pre,
                             "ArgumentView must be unchanged after identical rejection replay")

        with self.subTest(variant="post_start_failure"):
            drv = self.bind_driver(
                "D8", "case-27",
                "Post-start failure does not refund the spawn")
            run_id = self.start_run(drv, goal=self.GOAL, budgets={"max_spawns": 2})
            spawns0 = self.require_operational_int(drv, "spawns_used")
            child_id = self.require_admitted_child(drv, run_id)
            spawns_after_reserve = self.require_operational_int(drv, "spawns_used")
            self.assertEqual(spawns_after_reserve, spawns0 + 1,
                             "reserve must spend exactly spawns0+1")
            # reserve→launching→pending→failed; spawn remains spent exactly +1.
            # Never use reserved→pending (not a registry edge).
            self.require_child_state(drv, run_id, child_id, "launching")
            self.require_child_state(drv, run_id, child_id, "pending")
            resp_fail = drv.trusted_child_event(run_id, child_id,
                                                 build_child_event_payload("failed", native_id="n1"))
            self.assert_valid_response(resp_fail)
            self.assert_child_summary(resp_fail["result"]["run"], child_id, state="failed")
            spawns_after_fail = self.require_operational_int(drv, "spawns_used")
            self.assertEqual(spawns_after_fail, spawns_after_reserve,
                             "post-start failure must not refund the spawn (exactly +1)")

    # 28 — Cancellation, timeout, and orphan recovery produce their exact state/recovery action
    def test_cancel_timeout_orphan_exact_state_and_recovery(self):
        for terminal in ("cancelled", "timed_out", "orphaned"):
            with self.subTest(terminal=terminal):
                drv = self.bind_driver(
                    "D8", "case-28",
                    "Cancellation, timeout, and orphan recovery produce their exact state/"
                    "recovery action")
                run_id = self.start_run(drv, goal=self.GOAL)
                child_id = self.require_admitted_child(drv, run_id)
                # drive canonical path to pending through trusted ingress.
                self.require_child_state(drv, run_id, child_id, "launching")
                self.require_child_state(drv, run_id, child_id, "pending")
                # deliver terminal through trusted ingress.
                terminal_payload = build_child_event_payload(terminal, native_id="n1")
                resp = drv.trusted_child_event(run_id, child_id, terminal_payload)
                self.assert_valid_response(resp)
                # §8A: assert a child.terminal Block with exact parameters.state, sole reason,
                # ordered canonical next_actions/sections, and recovery_action == next_actions[0].
                result = self.assert_block_reason(resp, "child.terminal",
                                                   parameters={"state": terminal})
                self.assertEqual(len(result["reasons"]), 1,
                                 "child.terminal must be the sole reason")
                self.assertEqual(result["reasons"][0]["next_actions"], _CHILD_TERMINAL_NEXT,
                                 "child.terminal next_actions must match canonical order")
                self.assertEqual(result["reasons"][0]["sections"], _CHILD_TERMINAL_SECTIONS,
                                 "child.terminal sections must match canonical order")
                child = self.assert_child_summary(result["run"], child_id, state=terminal)
                self.assertEqual(child["recovery_action"], _CHILD_TERMINAL_NEXT[0],
                                 "child.recovery_action must equal canonical next_actions[0] "
                                 "(child.retry)")
                # Repeated identical terminal is idempotent (exact Inert, complete equality).
                snap_pre = self.snapshot_run_state(drv, run_id)
                arg_pre = self.assert_argument_view(
                    self.dispatch(drv, get_argument(run_id=run_id))["result"])
                resp_dup = drv.trusted_child_event(run_id, child_id, terminal_payload)
                self.assert_valid_response(resp_dup)
                self.assert_inert(resp_dup)
                snap_post = self.snapshot_run_state(drv, run_id)
                self.assert_run_state_unchanged(snap_pre, snap_post)
                arg_post = self.assert_argument_view(
                    self.dispatch(drv, get_argument(run_id=run_id))["result"])
                self.assertEqual(arg_post, arg_pre,
                                 "ArgumentView must be unchanged after identical terminal replay")

    # 29 — Foreground-only/observational hosts return typed unsupported reasons; tiers match registry
    def test_unsupported_hosts_return_typed_reasons_and_match_registry(self):
        for pid in PROFILE_IDS:
            with self.subTest(profile=pid):
                drv = self.bind_driver(
                    "D8", "case-29",
                    "Foreground-only/observational hosts return typed unsupported capability "
                    "reasons rather than silent fallback; current tiers match D1-H/D2 profiles",
                    profile_id=pid)
                run_id = self.start_run(drv, goal=self.GOAL)
                resp = self.dispatch(drv, observe_action(run_id=run_id, action=action_child_reserve(
                    purpose="audit", role_profile=pid, execution="async")))
                tier = profile_tier(pid)
                self.assertIn(tier, HOST_TIERS)
                if tier == "foreground_only":
                    result = self.assert_block_only(resp, ["host.async_unsupported"])
                elif tier == "observational":
                    result = self.assert_block_only(resp, ["host.audit_output_unobservable"])
                else:  # full_async: exact Allow with one reserved child
                    self.assertEqual(resp["result"]["type"], "Allow")
                    result = resp["result"]
                    kids = result["run"].get("children", [])
                    self.assertEqual(len(kids), 1,
                                     "full_async reserve must admit exactly one reserved child")
                    self.assertEqual(kids[0]["state"], "reserved",
                                     "the reserved child must be in state 'reserved'")
                # Assert exact host profile/tier in RunView and no silent fallback.
                self.assert_host_view(result["run"], pid)


if __name__ == "__main__":
    unittest.main()
