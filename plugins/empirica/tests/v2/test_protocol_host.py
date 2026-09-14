"""D4 cases 45–50: strict v2 protocol, host honesty, private capability.

Owners: D6 (strict v2 / old-state refusal / unknown-field fail-closed) + D10 (host adapter
profile/tier matching and private-capability redaction).

Strict-decoder negatives (45–47) use ``raw_dispatch`` (no request validation; the envelope is
intentionally malformed/old/unknown) and raw-state injection through the fake run repository
(``inject_run_state``) before RestoreRun — test input, not a public command (D4 spec §8/§8A).
Each assertion is an exact result type (Fault OR Block), never a Fault|Block union (D4-S3b).
Host tiers and missing-capability projections are derived from the D2 registry, never copied.
Case 50 asserts no private material in any public surface and never inspects ``drv.artifacts()``
or internal operational state as if public.
"""
from __future__ import annotations

import unittest

from assertions import (  # noqa: E402
    ConformanceCase, PROFILE_IDS,
    build_child_event_payload, profile_missing_capabilities, profile_tier, protocol,
    evaluate, get_argument, get_contract, get_run, observe_action, start_run,
)


class ProtocolHostTests(ConformanceCase):
    GOAL = "Prove strict v2 identity, host honesty, and private-capability redaction."

    # 45 — Exact empirica/v2 identity is checked before semantic decoding
    def test_v2_identity_checked_before_decoding(self):
        drv = self.bind_driver(
            "D6", "case-45",
            "Exact empirica/v2 state/protocol identity is checked before semantic decoding")
        # Persisted v1 identity with hostile/partial semantic fields → exact sole run.old_version
        # Block with canonical actions/sections before semantic decode (raw-state test input).
        drv.inject_run_state("r-legacy", {"protocol": "empirica/v1", "status": "active",
                                           "hostile": "payload", "partial": {"x": 1}})
        restored = self.raw_dispatch(drv, {"protocol": protocol(), "request_id": "r",
                                            "command": {"type": "RestoreRun", "run_id": "r-legacy"}})
        self.assert_block_sole_reason(restored, "run.old_version")
        # a v1 protocol wire envelope → exact Fault/closed (raw wire, not Block)
        v1 = {"protocol": "empirica/v1", "request_id": "v1-probe",
              "command": {"type": "GetRun", "run_id": "r"}}
        resp = self.raw_dispatch(drv, v1)
        self.assert_fault(resp, fail_direction="closed")

    # 46 — Wire variants Fault/closed; persisted identities sole run.old_version; v2+corrupt sole run.corrupt
    def test_v1_and_old_state_rejected_with_fresh_run_recovery(self):
        drv = self.bind_driver(
            "D6", "case-46",
            "Wire protocol variants null/empty/v1/future/partial are exact Fault/closed; "
            "persisted identities null/missing/v1/future are exact sole run.old_version Blocks "
            "with start-fresh recovery; exact v2 identity with malformed/partial encoding is "
            "sole run.corrupt Block")
        # Wire protocol variants null/empty/v1/future/partial → exact Fault/closed
        for proto in (None, "", "empirica/v1", "empirica/v3", "empirica/v2-rc"):
            with self.subTest(protocol=proto):
                env = {"protocol": proto, "request_id": "probe",
                       "command": {"type": "GetContract", "target": "index"}}
                r = self.raw_dispatch(drv, env)
                self.assert_fault(r, fail_direction="closed")
        # Persisted identities null/missing/v1/future → exact sole run.old_version Block w/ start-fresh
        for state in ({"protocol": None, "status": "active"},
                      {"status": "active"},                       # missing protocol
                      {"protocol": "empirica/v1", "status": "active"},
                      {"protocol": "empirica/v3", "status": "active"}):
            with self.subTest(persisted=state):
                drv.inject_run_state("r-old", state)
                r = self.raw_dispatch(drv, {"protocol": protocol(), "request_id": "r",
                                             "command": {"type": "RestoreRun", "run_id": "r-old"}})
                self.assert_block_sole_reason(r, "run.old_version")
                self.assertIn("run.start_fresh", r["result"]["reasons"][0]["next_actions"],
                              "run.old_version must offer run.start_fresh recovery")
        # Exact v2 identity with malformed/partial encoding → sole run.corrupt Block
        drv.inject_run_state("r-corrupt", {"protocol": protocol(), "status": "active",
                                            "obligations": "not-an-object"})
        r2 = self.raw_dispatch(drv, {"protocol": protocol(), "request_id": "r2",
                                      "command": {"type": "RestoreRun", "run_id": "r-corrupt"}})
        self.assert_block_sole_reason(r2, "run.corrupt")
        self.assertIn("run.start_fresh", r2["result"]["reasons"][0]["next_actions"],
                      "run.corrupt must offer run.start_fresh recovery")

    # 47 — Unknown fields/actions exact Fault/closed; v2+unknown status sole run.corrupt; no unions
    def test_unknown_fields_actions_fail_closed(self):
        drv = self.bind_driver(
            "D6", "case-47", "Unknown top-level/command/action fields and action kinds fail closed; "
            "exact-v2 persisted unknown status is sole run.corrupt Block; no Fault|Block unions")
        # Use an opaque probe run ID: every malformed request must fail before lookup, so the
        # run need not be started (D6-A: case 47's incidental StartRun is removed).
        run_id = "probe-run-opaque-47"
        # Unknown top-level request field → exact Fault/closed
        env_top = {"protocol": protocol(), "request_id": "p1",
                   "command": {"type": "GetRun", "run_id": run_id}, "unknown_top_field": True}
        self.assert_fault(self.raw_dispatch(drv, env_top), fail_direction="closed")
        # Unknown command field → exact Fault/closed
        env_cmd = {"protocol": protocol(), "request_id": "p2",
                   "command": {"type": "GetRun", "run_id": run_id, "unknown_cmd_field": True}}
        self.assert_fault(self.raw_dispatch(drv, env_cmd), fail_direction="closed")
        # Unknown action kind → exact Fault/closed
        resp_kind = self.raw_dispatch(drv, observe_action(
            run_id=run_id, action={"kind": "not_a_real_action"}))
        self.assert_fault(resp_kind, fail_direction="closed")
        # Unknown action field → exact Fault/closed
        resp_field = self.raw_dispatch(drv, observe_action(
            run_id=run_id, action={"kind": "graph", "unknown_action_field": True}))
        self.assert_fault(resp_field, fail_direction="closed")
        # Exact-v2 persisted unknown status → sole run.corrupt Block with start-fresh recovery
        drv.inject_run_state("r-badstatus", {"protocol": protocol(), "status": "not_a_status"})
        r2 = self.raw_dispatch(drv, {"protocol": protocol(), "request_id": "r3",
                                      "command": {"type": "RestoreRun", "run_id": "r-badstatus"}})
        self.assert_block_sole_reason(r2, "run.corrupt")
        self.assertIn("run.start_fresh", r2["result"]["reasons"][0]["next_actions"],
                      "run.corrupt must offer run.start_fresh recovery")

    # 48 — Default host RunView profile/tier/missing-capabilities equal exact registry entry
    def test_host_profile_and_tier_exact_registry(self):
        drv = self.bind_driver(
            "D10", "case-48",
            "Default host RunView profile ID and tier equal exact registry entry; missing "
            "capabilities equal exact registry list/order; no schema parity inference or "
            "unknown fields")
        resp = self.dispatch(drv, start_run(goal=self.GOAL))
        run = self.assert_allow(resp, converged=False)["run"]
        host = run["host"]
        self.assertEqual(host["profile_id"], self.DEFAULT_PROFILE,
                         "default host profile_id must equal the exact registry entry")
        self.assertEqual(host["tier"], profile_tier(self.DEFAULT_PROFILE),
                         "default host tier must equal the exact registry entry")
        self.assertEqual(host["missing_capabilities"],
                         profile_missing_capabilities(self.DEFAULT_PROFILE),
                         "missing capabilities must equal the exact registry list and order")

    # 49 — Iterate all canonical profile IDs; exact projection; all observed exactly once
    def test_current_host_tiers_match_registry(self):
        seen: set[str] = set()
        for pid in PROFILE_IDS:
            with self.subTest(profile=pid):
                drv = self.bind_driver(
                    "D10", "case-49",
                    "Each canonical profile's StartRun public host view equals the exact registry "
                    "profile/tier/missing-capability projection; all observed exactly once",
                    profile_id=pid)
                resp = self.dispatch(drv, start_run(goal=self.GOAL))
                run = self.assert_allow(resp, converged=False)["run"]
                host = run["host"]
                self.assertEqual(host["profile_id"], pid)
                self.assertEqual(host["tier"], profile_tier(pid),
                                 f"profile {pid} tier must match the registry")
                self.assertEqual(host["missing_capabilities"],
                                 profile_missing_capabilities(pid),
                                 f"profile {pid} missing capabilities must match the registry exactly")
                # all registry profiles observed exactly once (no duplicate across the iteration)
                self.assertNotIn(pid, seen,
                                 f"profile {pid} must be observed exactly once, not duplicated")
                seen.add(pid)
        # after the loop, every canonical profile was observed exactly once
        self.assertEqual(seen, set(PROFILE_IDS),
                         "all registry profiles must be observed exactly once")

    # 50 — Forged capability rejected; private material never public; no drv.artifacts() as public
    def test_private_capability_never_public(self):
        drv = self.bind_driver(
            "D10", "case-50",
            "A forged-capability child_event is rejected with RunView/ArgumentView unchanged; "
            "no capability/capability_ref/native_id/fingerprint/provider-model/secret appears in "
            "any public response, GetRun, GetArgument, Evaluate Block, GetContract index, "
            "compacted view, or rejection diagnostic")
        run_id = self.start_run(drv, goal=self.GOAL)
        child_id = self.require_admitted_child(drv, run_id, execution="foreground")
        # snapshot complete RunView and ArgumentView before the forged attempt
        before_run = self.dispatch(drv, get_run(run_id=run_id))["result"]["run"]
        before_arg = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        # Publicly dispatch a syntactically valid closed D2D child_event with forged capability.
        # The payload is a valid reserved→launching transition using the SAME closed payload as the
        # later private launching event, so only the forged capability can reject it.
        forged_payload = build_child_event_payload("launching", native_id="n-real")
        resp_forged = self.dispatch(drv, observe_action(
            run_id=run_id,
            action={"kind": "child_event", "child_id": child_id,
                    "trusted": {"capability_ref": "topsecret", "boundary": "host"},
                    "payload": forged_payload}))
        # require Fault/Block capability rejection (not Allow)
        self.assertIn(resp_forged["result"]["type"], ("Fault", "Block"),
                      "a forged-capability child_event must be rejected, not admitted")
        # complete RunView/ArgumentView unchanged after the rejected attempt
        after_run = self.dispatch(drv, get_run(run_id=run_id))["result"]["run"]
        after_arg = self.assert_argument_view(
            self.dispatch(drv, get_argument(run_id=run_id))["result"])
        self.assertEqual(after_run, before_run,
                         "the rejected forged event must leave the RunView unchanged")
        self.assertEqual(after_arg, before_arg,
                         "the rejected forged event must leave the ArgumentView unchanged")
        # Then deliver a valid launching event through private ingress (the real boundary).
        # The private trusted launch response is only validated structurally; it is NOT a public
        # surface (private ingress returns private transport facts, not a public projection).
        launch_resp = drv.trusted_child_event(
            run_id, child_id, build_child_event_payload("launching", native_id="n-real"))
        self.assert_valid_response(launch_resp)
        # Immediately GetRun, assert the admitted child reached exact launching, and reuse that
        # GetRun response as the public privacy surface (D4-S3b correction).
        get_run_resp = self.dispatch(drv, get_run(run_id=run_id))
        self.assert_child_summary(get_run_resp["result"]["run"], child_id, state="launching")
        # Assert no private material in ANY public surface (not drv.artifacts()/operational state)
        public_surfaces = [
            resp_forged,
            get_run_resp,
            self.dispatch(drv, get_argument(run_id=run_id)),
            self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence")),
            self.dispatch(drv, get_contract(target="index")),
            drv.compact(),
        ]
        for surface in public_surfaces:
            self.assert_no_private_capability(surface)


if __name__ == "__main__":
    unittest.main()
