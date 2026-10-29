#!/usr/bin/env python3
"""PR 10 (ADR-0072): evidence presentation, confirmation and terminal consent bound to their HITL document.

Two layers. The pure layer proves the derivations: one core function selects the terminal residual
rows, ``evidence_for`` derives every input of ``evidence_document`` for the batch a payload names from a
GetRun-shaped snapshot, and the derivation is invariant under the presentation artifact's own landing
(the review's soundness demand, as PR 07 proved for the receipt). The service layer drives the real
composition through the trusted evidence legs (in-memory, nothing under any ``EMPIRICA_HOME``) and proves
the expand rules: an absent ``document_digest`` keeps today's behaviour exactly; ``None`` is an explicit
fetch — refused with ``evidence.document_mismatch``, nothing spent, the refusal's private result carrying
``document: {text, digest}`` for the batch named by ``key``; a wrong digest the same; the right digest is
recorded in the presentation artifact body, and a confirmation must name the document its own presentation
recorded. The document never reaches a public result.
"""
import copy
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: E402  (tests run from anywhere)

import jsonschema  # noqa: E402

from application.v2 import compose
from application import protocol
from core import confirmation, hitl
from core.evaluation import artifact
from core.projection import project_runview
from governance_setup import (TEST_INVOCATION, approve_current, sized_configure_run)  # noqa: E402
from test_d7_transactions import Artifacts, Harness, Runs, Workspace  # noqa: E402
from test_hitl_document import evidence_snapshot, batch_document  # noqa: E402
from application.protocol import host_profile_id  # noqa: E402

PROFILE = host_profile_id("pi")
GRAPH = {"root": "C0", "claims": [{"id": "C0", "text": "supplied uncertainty", "gating": True,
                                   "kind": "ordinary"}], "edges": []}
SIZED_BUDGETS = {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}
RUN_ID = "run-fx"  # the evidence_document fixture's own run


def forged(digest: str) -> str:
    """A digest one hex off (``0`` last unless the real one ends ``0``: test_governance's helper)."""
    return digest[:-1] + ("0" if digest[-1] != "0" else "1")


# --------------------------------------------------------------------------- #
# The pure derivations (core.hitl).
# --------------------------------------------------------------------------- #
class TerminalResidualSelectionTests(unittest.TestCase):
    """One core function selects what a terminal consent leaves unresolved."""

    def test_it_keeps_unresolved_rows_and_drops_the_consent_row_and_satisfied_ones(self):
        run_view = {"obligations": {"active": [
            {"id": "obligation.approval", "required": "Obtain approval.", "status": "satisfied"},
            {"id": "obligation.audit", "required": "Obtain an audit.", "status": "residual"},
            {"id": "claim:C0", "required": "Root claim text", "status": "satisfied"},
            {"id": "claim:C1", "required": "Leaf claim text", "status": "residual"},
            {"id": "obligation.terminal", "required": "Get consent to end.", "status": "residual"},
        ]}}
        self.assertEqual([dict(row) for row in hitl.terminal_residuals(run_view)], [
            {"id": "obligation.audit", "required": "Obtain an audit."},
            {"id": "claim:C1", "required": "Leaf claim text"},
        ])

    def test_it_refuses_a_run_view_without_its_rows(self):
        with self.assertRaises(ValueError):
            hitl.terminal_residuals({"obligations": {"active": "broken"}})
        with self.assertRaises(ValueError):
            hitl.terminal_residuals({})


class EvidenceForTests(unittest.TestCase):
    """``evidence_for`` derives every input of ``evidence_document`` from one snapshot."""

    def snapshot(self):
        return replace(evidence_snapshot(), command={"type": "GetRun", "run_id": RUN_ID})

    def batch_key(self, snapshot):
        from core.evaluation import batch_candidates, derive_claims
        candidates = batch_candidates(snapshot, derive_claims(snapshot))
        self.assertTrue(candidates, "the fixture snapshot must have a pending batch")
        return candidates[0]["key"]

    def test_it_reproduces_the_batch_document_from_real_state(self):
        snapshot = self.snapshot()
        document = hitl.evidence_for(run_id=RUN_ID, snapshot=snapshot, key=self.batch_key(snapshot))
        self.assertEqual(document, batch_document() | {"run_id": RUN_ID})
        self.assertEqual(hitl.render(document), hitl.render(batch_document()))  # byte-identity holds

    def test_the_terminal_document_carries_the_one_residual_function_s_rows(self):
        from core.evaluation import terminal_batch, derive_claims
        snapshot = self.snapshot()
        batch = terminal_batch(snapshot, derive_claims(snapshot), "stopped_residual")
        self.assertIsNotNone(batch)
        document = hitl.evidence_for(run_id=RUN_ID, snapshot=snapshot, key=batch["key"])
        self.assertEqual(document["terminal"]["status"], "stopped_residual")
        rows = hitl.terminal_residuals(project_runview(snapshot))
        self.assertEqual([dict(row) for row in document["terminal"]["residuals"]],
                         [dict(row) for row in rows])
        self.assertTrue(document["terminal"]["residuals"], "today's fixture leaves an audit owed")

    def test_the_same_document_results_whether_or_not_the_presentation_artifact_exists(self):
        """The derivation reads only state and evidence, never its own presentation receipt."""
        snapshot = self.snapshot()
        key = self.batch_key(snapshot)
        before = hitl.document_digest(hitl.evidence_for(run_id=RUN_ID, snapshot=snapshot, key=key))
        state = snapshot.state
        landing = artifact(confirmation.PRESENTATION, {
            "receipt_id": "presenter", "key": key, "record_ids": (),
            "claims": (), "approval_kind": "host_ui",
            "route_stamp": state.route_stamp, "investigation_stamp": state.investigation_stamp})
        presented = replace(snapshot, history=snapshot.history
                            + ({"artifact_id": landing["artifact_id"], **landing["body"]},))
        after = hitl.document_digest(hitl.evidence_for(run_id=RUN_ID, snapshot=presented, key=key))
        self.assertEqual(after, before)

    def test_it_refuses_any_snapshot_but_the_run_view_shape(self):
        snapshot = evidence_snapshot()  # the fixture's own command is GetArgument
        with self.assertRaises(ValueError):
            hitl.evidence_for(run_id=RUN_ID, snapshot=snapshot, key=self.batch_key(snapshot))

    def test_it_refuses_a_key_the_core_would_not_admit_now(self):
        snapshot = self.snapshot()
        with self.assertRaises(ValueError):
            hitl.evidence_for(run_id=RUN_ID, snapshot=snapshot, key="sha256:" + "0" * 64)

    def test_the_pair_carries_the_exact_rendered_bytes(self):
        snapshot = self.snapshot()
        key = self.batch_key(snapshot)
        pair = hitl.evidence_pair(run_id=RUN_ID, snapshot=snapshot, key=key)
        document = hitl.evidence_for(run_id=RUN_ID, snapshot=snapshot, key=key)
        self.assertEqual(pair["text"].encode("utf-8"), hitl.render(document))
        self.assertEqual(pair["digest"], hitl.document_digest(document))

    def test_the_pair_is_none_when_no_batch_admits_the_key(self):
        snapshot = self.snapshot()
        self.assertIsNone(hitl.evidence_pair(run_id=RUN_ID, snapshot=snapshot,
                                             key="sha256:" + "0" * 64))


# --------------------------------------------------------------------------- #
# The expand rules through the real composition's trusted legs.
# --------------------------------------------------------------------------- #
class EvidenceLegBindingTests(unittest.TestCase):
    """Present, confirm and terminal-consent digests through the real service (in-memory)."""

    def setUp(self):
        self.runs, self.artifacts = Runs(), Artifacts()
        self.service = compose(Workspace(), Harness(), self.runs, self.artifacts, None, PROFILE, {}, None)
        self.run_id = self.request({"type": "StartRun", "control_mode": "deliberative",
                                    "goal": "govern the evidence binding",
                                    "invocation": dict(TEST_INVOCATION),
                                    "selector": {"project": "p", "session": "s"}})["run"]["id"]

    # -- the harness ------------------------------------------------------- #
    def request(self, command):
        return self.service.dispatch({"protocol": "empirica/v2", "request_id": "test",
                                      "command": command})["result"]

    def action(self, kind, **kwargs):
        return self.request({"type": "ObserveAction", "run_id": self.run_id, "action": {"kind": kind, **kwargs}})

    def view(self):
        return self.request({"type": "GetRun", "run_id": self.run_id})["run"]

    # -- reaching a pending evidence batch ---------------------------------- #
    def bring_to_a_pending_record(self):
        self.assertEqual(self.action("route", reason="supplied context")["type"], "Allow")
        self.assertEqual(self.action("graph", payload=copy.deepcopy(GRAPH))["type"], "Allow")
        self.assertEqual(self.action(**sized_configure_run(budgets=SIZED_BUDGETS,
                                                           rationale="sized for the binding tests"))["type"],
                        "Allow")
        approve_current(self.service._coordinator, self.run_id)
        self.assertEqual(self.action("investigate")["type"], "Allow")
        self.assertEqual(self.action("research", claim_id="C0", source_kind="web",
                                     result="supports",
                                     payload={"source_ref": "https://example.test/spec",
                                              "citation": "the observed source supports the claim"})["type"],
                         "Allow")
        self.assertEqual(self.service.trusted_human_surface(
            run_id=self.run_id, payload={"run_id": self.run_id, "surface": "human"})["result"]["type"],
            "Allow")
        batch = self.view()["confirmation_batch"]
        self.assertIsNotNone(batch, "a human-record research must now wait on a human")
        self.assertFalse(confirmation.is_terminal(batch))
        return batch

    def state_snapshot(self):
        """The coordinator's latest snapshot (reassembled by a read)."""
        self.view()
        return self.service._coordinator.last_snapshot

    def presentation_bodies(self):
        return {item["receipt_id"]: item for item in self.state_snapshot().history
                if item.get("kind") == confirmation.PRESENTATION}

    def confirmation_bodies(self):
        return [item for item in self.state_snapshot().history
                if item.get("kind") == confirmation.KIND]

    def spent(self):
        return confirmation.interactions(self.state_snapshot())

    def present(self, *, key, receipt="p", digest="absent"):
        payload = {"run_id": self.run_id, "receipt_id": receipt, "key": key}
        if digest != "absent":
            payload["document_digest"] = digest
        return self.service.trusted_evidence_presentation(run_id=self.run_id, payload=payload)["result"]

    def confirm(self, *, receipt="p", digest="absent"):
        shown = confirmation.presentation(self.state_snapshot(), receipt)
        decisions = [{"subject": "research", "artifact_id": shown["record_ids"][0], "outcome": "confirm"}]
        payload = {"run_id": self.run_id, "receipt_id": receipt, "decisions": decisions}
        if digest != "absent":
            payload["document_digest"] = digest
        return self.service.trusted_evidence_confirmation(run_id=self.run_id, payload=payload)["result"]

    def expected_digest(self, key):
        return hitl.document_digest(
            hitl.evidence_for(run_id=self.run_id, snapshot=self.state_snapshot(), key=key))

    # -- the flow ----------------------------------------------------------- #
    def setUp_documented_batch(self):
        batch = self.bring_to_a_pending_record()
        right = self.expected_digest(batch["key"])
        return batch["key"], right

    # -- 1. null is the fetch; nothing is spent ----------------------------- #
    def test_a_null_digest_fetches_the_document_and_spends_nothing(self):
        key, right = self.setUp_documented_batch()
        before = self.spent()
        refused = self.present(key=key, receipt="fetch", digest=None)
        self.assertEqual(refused["type"], "Block")
        self.assertEqual(refused["reasons"][0]["code"], "evidence.document_mismatch")
        self.assertEqual(self.spent(), before)
        self.assertEqual(self.presentation_bodies(), {})
        document = refused["document"]
        self.assertEqual(set(document), {"text", "digest"})
        self.assertEqual(document["digest"], right)
        parsed = json.loads(document["text"])
        self.assertEqual(document["text"].encode("utf-8"), hitl.render(parsed))
        self.assertEqual(parsed, hitl.evidence_for(run_id=self.run_id, snapshot=self.state_snapshot(),
                                                   key=key))

    # -- 2. the right digest is recorded on present and on confirm ---------- #
    def test_the_right_digest_records_in_both_artifact_bodies(self):
        key, right = self.setUp_documented_batch()
        shown = self.present(key=key, receipt="bound", digest=right)
        self.assertEqual(shown["type"], "Allow", shown)
        self.assertEqual(self.presentation_bodies()["bound"]["document_digest"], right)
        answer = self.confirm(receipt="bound", digest=right)
        self.assertEqual(answer["type"], "Allow", answer)
        bodies = self.confirmation_bodies()
        self.assertEqual([body["document_digest"] for body in bodies], [right])

    # -- 3. one hex off: refused, nothing spent ------------------------------ #
    def test_a_digest_one_hex_off_is_refused_and_spends_nothing(self):
        key, right = self.setUp_documented_batch()
        before = self.spent()
        refused = self.present(key=key, receipt="forged", digest=forged(right))
        self.assertEqual(refused["type"], "Block")
        self.assertEqual(refused["reasons"][0]["code"], "evidence.document_mismatch")
        self.assertEqual(self.presentation_bodies(), {})
        self.assertEqual(self.spent(), before)
        self.assertEqual(refused["document"]["digest"], right)  # the refusal is the fetch

    # -- 4. ordering: the existing staleness refusals win ------------------- #
    def test_a_stale_key_with_a_wrong_digest_gets_the_existing_refusal(self):
        key, right = self.setUp_documented_batch()
        # the presentation names a batch the core would not admit: that refusal wins (Q3)
        refused = self.present(key=forged(key), receipt="stale", digest=forged(right))
        self.assertEqual(refused["type"], "Fault")
        self.assertEqual(refused["code"], "conflict")
        self.assertEqual(self.spent(), 0)
        # and a confirmation whose presentation is no longer current: the same precedence
        self.assertEqual(self.present(key=key, receipt="later", digest=right)["type"], "Allow",
                         "the presentation lands first")
        # a second research record changes the derivation, so the shown key is no longer current
        self.assertEqual(self.action("research", claim_id="C0", source_kind="web",
                                     result="supports",
                                     payload={"source_ref": "https://example.test/second",
                                              "citation": "a second observed source"})["type"],
                         "Allow")
        stale = self.confirm(receipt="later", digest=forged(right))
        self.assertEqual(stale["type"], "Fault")
        self.assertEqual(stale["code"], "conflict")

    # -- 5. the confirmation names the document its own presentation bound -- #
    def test_a_confirmation_for_a_legacy_presentation_cannot_claim_a_digest(self):
        key, right = self.setUp_documented_batch()
        self.assertEqual(self.present(key=key, receipt="legacy")["type"], "Allow")  # absent: legacy
        refused = self.confirm(receipt="legacy", digest=right)
        self.assertEqual(refused["type"], "Block")
        self.assertEqual(refused["reasons"][0]["code"], "evidence.document_mismatch")
        self.assertEqual(self.confirmation_bodies(), [])  # nothing admitted

    def test_a_legacy_presentation_and_a_legacy_confirmation_still_admit(self):
        key, right = self.setUp_documented_batch()
        self.assertEqual(self.present(key=key, receipt="olde")["type"], "Allow")
        self.assertEqual(self.confirm(receipt="olde")["type"], "Allow")
        self.assertIsNone(self.presentation_bodies()["olde"]["document_digest"])
        self.assertEqual([body["document_digest"] for body in self.confirmation_bodies()], [None])

    def test_a_wrong_digest_on_the_confirmation_refuses_every_item(self):
        key, right = self.setUp_documented_batch()
        self.assertEqual(self.present(key=key, receipt="mismatch", digest=right)["type"], "Allow")
        refused = self.confirm(receipt="mismatch", digest=forged(right))
        self.assertEqual(refused["type"], "Block")
        self.assertEqual(refused["reasons"][0]["code"], "evidence.document_mismatch")
        self.assertEqual(self.confirmation_bodies(), [])

    # -- 6. absent is today's behaviour byte for byte ------------------------ #
    def test_an_absent_digest_keeps_the_presentation_body_of_today(self):
        key, _ = self.setUp_documented_batch()
        shown = self.present(key=key, receipt="today")
        self.assertEqual(shown["type"], "Allow", shown)
        body = self.presentation_bodies()["today"]
        state = self.state_snapshot().state
        # exactly today's members plus the new binding member, carried on the history row's own
        # landing shape (its ``kind`` and ``artifact_id`` are the row's, not the body's)
        today = {"receipt_id": "today", "key": key,
                 "record_ids": tuple(body["record_ids"]), "claims": tuple(body["claims"]),
                 "approval_kind": "host_ui",
                 "route_stamp": state.route_stamp, "investigation_stamp": state.investigation_stamp}
        self.assertEqual({k: v for k, v in body.items() if k not in ("document_digest", "artifact_id", "kind")},
                         today)
        self.assertEqual(sorted(body), sorted([*today, "document_digest", "artifact_id", "kind"]))
        self.assertIsNone(body["document_digest"])

    # -- 7. the terminal consent binds the same way --------------------------- #
    def test_the_terminal_consent_binds_its_document_with_the_residual_rows(self):
        key, _ = self.setUp_documented_batch()
        # a record not confirmed keeps the gate from stopping: answer it the pre-PR-10 way
        self.assertEqual(self.present(key=key, receipt="legacy")["type"], "Allow")
        self.assertEqual(self.confirm(receipt="legacy")["type"], "Allow")
        stop = self.request({"type": "EvaluateRun", "run_id": self.run_id, "intent": "stop"})
        self.assertEqual(stop["type"], "Block")
        self.assertEqual(stop["reasons"][0]["code"], "governance.terminal_stop_pending", stop)
        batch = stop["run"]["confirmation_batch"]
        self.assertTrue(confirmation.is_terminal(batch), "the stop asks its consent batch")
        before = self.spent()
        fetch = self.present(key=batch["key"], receipt="end", digest=None)
        self.assertEqual(fetch["type"], "Block")
        self.assertEqual(fetch["reasons"][0]["code"], "evidence.document_mismatch")
        document = hitl.evidence_for(run_id=self.run_id, snapshot=self.state_snapshot(),
                                     key=batch["key"])
        self.assertEqual(fetch["document"]["digest"],
                         hitl.document_digest(document))
        self.assertEqual(fetch["document"]["text"].encode("utf-8"), hitl.render(document))
        for row in document["terminal"]["residuals"]:
            self.assertNotEqual(row["id"], "obligation.terminal")
        right = fetch["document"]["digest"]
        self.assertEqual(self.spent(), before)
        self.assertEqual(self.present(key=batch["key"], receipt="end", digest=right)["type"], "Allow")
        shown = confirmation.presentation(self.state_snapshot(), "end")
        self.assertEqual(shown["document_digest"], right)
        decided = {"subject": "claim", "claim_id": batch["terminal"]["status"],
                   "claim_digest": batch["terminal"]["digest"], "outcome": "approve"}
        refused = self.service.trusted_evidence_confirmation(
            run_id=self.run_id, payload={"run_id": self.run_id, "receipt_id": "end",
                                        "decisions": [decided],
                                        "document_digest": forged(right)})["result"]
        self.assertEqual(refused["type"], "Block", refused)
        answered = self.service.trusted_evidence_confirmation(
            run_id=self.run_id, payload={"run_id": self.run_id, "receipt_id": "end",
                                        "decisions": [decided], "document_digest": right})["result"]
        self.assertEqual(answered["type"], "Allow", answered)
        ended = self.request({"type": "EvaluateRun", "run_id": self.run_id, "intent": "stop"})
        self.assertEqual(ended["type"], "Allow", ended)
        self.assertTrue(ended["run"]["status"].startswith("stopped_"))

    # -- 8. the document never reaches a public result ------------------------ #
    def test_public_results_never_carry_the_document(self):
        key, right = self.setUp_documented_batch()
        refused = self.present(key=key, receipt="private", digest=None)
        self.assertEqual(refused["reasons"][0]["code"], "evidence.document_mismatch")
        # the public wire schema itself cannot carry it: block is additionalProperties:false
        with self.assertRaises(jsonschema.ValidationError):
            protocol.schema_validator("response", "block").validate(refused)
        for command in ({"type": "EvaluateRun", "run_id": self.run_id, "intent": "report_convergence"},
                        {"type": "GetRun", "run_id": self.run_id},
                        {"type": "GetArgument", "run_id": self.run_id}):
            result = self.request(command)
            self.assertNotIn("document", result, command)
            self.assertNotIn("presentation", result, command)

    def test_the_private_evidence_response_validator_admits_refusals_and_rejects_strays(self):
        key, right = self.setUp_documented_batch()
        refused = self.present(key=key, receipt="validator", digest=None)
        self.assertEqual(refused["reasons"][0]["code"], "evidence.document_mismatch")
        self.assertTrue(protocol.validate_private_evidence_response(
            {"protocol": "empirica/v2", "request_id": "t", "result": refused}))
        # an ordinary Allow cannot grow a document member: only the refusal leg may carry it
        issued = self.request({"type": "GetRun", "run_id": self.run_id})
        stray = {**issued, "document": copy.copy(refused["document"])}
        self.assertFalse(protocol.validate_private_evidence_response(
            {"protocol": "empirica/v2", "request_id": "t", "result": stray}))


if __name__ == "__main__":
    unittest.main()
