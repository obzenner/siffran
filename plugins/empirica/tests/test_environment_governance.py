#!/usr/bin/env python3
"""Empirica 7.2 E7: the environment deliberative receipts and the admission decisions (spec §5).

The behaviour of the private environment receipt (host ingress, never a public author action):
the write-first presentation of a pending revision recorded as the member's own receipt row, the
approve/reject that names it by its entire persisted identity (id, document digest, revision and
proposal digest — one binding, nothing substituted), replay and staleness through the one receipt
engine the configuration decisions share (``core.governance.replay_verdict``), the document-digest
equality with the recorded presentation, the auto mode that derives approval and therefore refuses
every receipt, and the compare-and-swap races the transaction's retry replans (a superseding
submission committed between the approval's planning and its commit loses the CAS and the replay
refuses the stale approval) <plugins/../doc/design/specs/empirica-7.2-run-scoped-environment-qualification.md> (§5).
Plus the two pure decisions E7 owns and later nodes build on: ``qualify``'s admission (including
the attempt ceiling) and the spike prerequisite.

No contract value is restated as a literal: reason codes are read from ``core.environment``'s own
constants (the registry rows behind them are compared where a response is rendered), and the
attempt ceiling from the generated module. The receipt shape's closed coverage belongs to the
contract conformance harness (``environmentDecisionPayload``, registered with
``x-core-validator``).
"""
import copy
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application.location import decode_handle
from application import protocol
from application.run_state import classify_and_decode
from application.transaction import Coordinator
from core import environment
from core.canonical import canonical_digest
from core.evaluation import ENVIRONMENT_NOT_AVAILABLE
from test_d7_transactions import Runs
from test_environment_actions import (CONFIGURE_RUN, ENVIRONMENT_ACTION, QUALIFY_ACTION, ServiceEnv,
                                       approve_current)

_V2 = Path(__file__).resolve().parents[3] / "contracts/empirica/v2"
_RECEIPT_ID_MAX = json.loads((_V2 / "request.schema.json").read_text(encoding="utf-8"))[
    "$defs"]["environmentDecisionPayload"]["properties"]["receipt_id"]["maxLength"]
_PUBLIC_CONTRACT = json.loads((_V2 / "public-contract.json").read_text(encoding="utf-8"))


def _document_digest(seed: str) -> str:
    """A digest-shaped document binding: E8 renders the real document; E7 only checks equality."""
    return canonical_digest({"seed": seed})


def _new_receipt_id() -> str:
    return uuid4().hex[:_RECEIPT_ID_MAX]


def _receipt(member, run_id, outcome, receipt_id, digest):
    """One closed receipt payload, built exactly as the request schema owns its shape."""
    return {"run_id": run_id, "receipt_id": receipt_id, "revision": member["revision"],
            "proposal_digest": member["digest"], "outcome": outcome, "document_digest": digest}


def _revise(action_payload):
    """A second body: the same closed shape with a different rationale, so a different digest."""
    payload = copy.deepcopy(action_payload)
    payload["rationale"] = f"a further revision: {payload['rationale']}"
    return payload


def _submit(env, payload):
    return env.observe({**copy.deepcopy(ENVIRONMENT_ACTION), "payload": payload})


def _revised_configure_run():
    """One further author configuration revision, a changed rationale against the contract's
    own sample action — a real revision, sizes bounded by the sample its owner holds."""
    action = copy.deepcopy(CONFIGURE_RUN)
    action["rationale"] = "a further revision two audit retries need the change"
    return action


def _revised_payload():
    """Yet another distinct environment body for the ceiling test's refused submission."""
    payload = copy.deepcopy(ENVIRONMENT_ACTION["payload"])
    payload["rationale"] = f"one more distinct body: {payload['rationale']}"
    return {**copy.deepcopy(ENVIRONMENT_ACTION), "payload": payload}


class DeliberationEnv(ServiceEnv):
    """A fully approved run with one pending environment revision and its presentation."""

    def __init__(self, **start):
        super().__init__(**start)
        self.start().fully_approved()
        _submit(self, copy.deepcopy(ENVIRONMENT_ACTION["payload"]))
        self.member = self.state().environment
        self.receipt_id = _new_receipt_id()
        # E8: the only digest a present may name is the core-rendered document's own — the
        # write-first fetch leg returns the pair the host writes before it presents, and the
        # receipt transaction recomputes and checks exactly that digest. Auto runs derive
        # their approvals and fetch no document (the leg reads Inert), so their receipts are
        # refused on the engine's own words whatever digest a caller fabricates.
        self.digest = _document_digest("no-document-fetched")
        if (document := self.fetch_document()) is not None:
            self.digest = document["digest"]

    def fetch_document(self):
        """The private fetch leg's document pair for the current pending revision (E8), or
        ``None`` where the leg reads Inert (no pending human decision: an auto run derives its
        approvals, a decided revision has no form)."""
        result = self.service.trusted_environment_document(run_id=self.run_id)["result"]
        if result["type"] == "Inert":
            return None
        assert result["type"] == "Allow", result
        return result["presentation"]["document"]

    def present(self, *, digest=None):
        result = self.service.trusted_environment_decision(
            run_id=self.run_id,
            payload=_receipt(self.member, self.run_id, "present", self.receipt_id,
                             self.digest if digest is None else digest))
        inner = result["result"]
        assert protocol.validate_public_result(inner), inner
        return inner

    def decide(self, outcome, *, digest=None, receipt_id=None, member=None):
        member = self.member if member is None else member
        result = self.service.trusted_environment_decision(
            run_id=self.run_id,
            payload=_receipt(member, self.run_id, outcome,
                             self.receipt_id if receipt_id is None else receipt_id,
                            self.digest if digest is None else digest))
        inner = result["result"]
        assert protocol.validate_public_result(inner), inner
        return inner

    def submit_revision(self):
        response = _submit(self, _revise(ENVIRONMENT_ACTION["payload"]))
        assert response["type"] == "Allow", response
        self.member = self.state().environment
        self.receipt_id = _new_receipt_id()
        self.digest = self.fetch_document()["digest"]
        return self.member


class ReceiptTests(unittest.TestCase):
    """The receipt races and replays of a deliberative run (spec §5)."""

    def test_a_presentation_records_its_row_and_the_revision_stays_pending(self):
        env = DeliberationEnv()
        result = env.present()
        self.assertEqual(result["type"], "Allow", result)
        member = env.state().environment
        self.assertEqual(member["state"], environment.PENDING)
        self.assertIsNone(member["receipt_id"])
        self.assertEqual(len(member["receipts"]), 1)
        row = member["receipts"][0]
        self.assertEqual(row["id"], env.receipt_id)
        self.assertEqual(row["outcome"], "present")
        self.assertEqual(row["fingerprint"], row["presentation_fingerprint"])
        self.assertEqual(row["document_digest"], env.digest)
        self.assertEqual(row["revision"], member["revision"])
        self.assertEqual(row["proposal_digest"], member["digest"])

    def test_an_exact_presentation_replay_is_inert_and_no_other_content_fits_the_id(self):
        env = DeliberationEnv()
        env.present()
        self.assertEqual(env.present()["type"], "Inert")
        self.assertEqual(env.present(digest=_document_digest("other"))["type"], "Fault")
        self.assertEqual(len(env.state().environment["receipts"]), 1)
        self.assertEqual(env.state().environment["state"], environment.PENDING)

    def test_nothing_finalizes_without_its_presentation_and_its_recorded_digest(self):
        """An approve under an unshown id, or with a digest its present did not record, decides
        nothing; ADR-0072's write-first word is what the presentation row recorded."""
        env = DeliberationEnv()
        self.assertEqual(env.decide("approve", receipt_id=_new_receipt_id())["type"], "Fault")
        env.present()
        self.assertEqual(env.decide("approve", digest=_document_digest("impostor"))["type"],
                         "Fault")
        self.assertEqual(env.state().environment["state"], environment.PENDING)

    def test_an_approval_finalizes_the_revision_and_its_replay_is_inert(self):
        env = DeliberationEnv()
        env.present()
        result = env.decide("approve")
        self.assertEqual(result["type"], "Allow", result)
        member = env.state().environment
        self.assertEqual(member["state"], environment.APPROVED)
        self.assertEqual(member["receipt_id"], env.receipt_id)
        decided_row = member["receipts"][0]
        self.assertEqual(decided_row["id"], env.receipt_id)
        self.assertEqual(decided_row["outcome"], "approve")
        self.assertNotEqual(decided_row["fingerprint"], decided_row["presentation_fingerprint"])
        self.assertEqual(env.decide("approve")["type"], "Inert")
        self.assertEqual(dict(env.state().environment), dict(member))
        self.assertEqual(env.decide("reject")["type"], "Fault")
        self.assertEqual(env.state().environment["state"], environment.APPROVED)

    def test_an_exact_decided_receipt_replays_inert_after_a_newer_revision(self):
        """The engine's one replay rule, exactly as configuration governance states it: a replay
        of an old decided receipt — even after its revision was superseded — decides nothing and
        mutates nothing, while a stale first reply still refuses."""
        env = DeliberationEnv()
        env.present()
        decided_one = _receipt(env.member, env.run_id, "approve", env.receipt_id, env.digest)
        self.assertEqual(env.service.trusted_environment_decision(
            run_id=env.run_id, payload=dict(decided_one))["result"]["type"], "Allow")
        env.member = env.submit_revision()
        replay = env.service.trusted_environment_decision(
            run_id=env.run_id, payload=dict(decided_one))["result"]
        self.assertEqual(replay["type"], "Inert", replay)
        member = env.state().environment
        self.assertEqual(member["revision"], env.member["revision"])
        self.assertEqual(member["state"], environment.PENDING)

    def test_a_reject_closes_the_presentation_without_approving(self):
        """Reject ends that presentation's question; a later approve under its id decides nothing."""
        env = DeliberationEnv()
        env.present()
        result = env.decide("reject")
        self.assertEqual(result["type"], "Allow", result)
        member = env.state().environment
        self.assertEqual(member["state"], environment.REJECTED)
        self.assertEqual(member["receipt_id"], env.receipt_id)
        self.assertEqual(env.decide("approve")["type"], "Fault")
        self.assertEqual(env.state().environment["receipt_id"], member["receipt_id"])

    def test_a_stale_reply_names_a_superseded_revision_and_decides_nothing(self):
        """An earlier revision's receipt never decides the newer member (the A-B-A word, spec §5)."""
        env = DeliberationEnv()
        env.present()
        superseded = dict(env.member)
        member_two = env.submit_revision()
        presented_two = _receipt(member_two, env.run_id, "present", _new_receipt_id(), env.digest)
        self.assertEqual(env.service.trusted_environment_decision(
            run_id=env.run_id, payload=presented_two)["result"]["type"], "Allow")
        decided_one = env.decide("approve", receipt_id=_new_receipt_id(), member=superseded)
        self.assertEqual(decided_one["type"], "Fault")
        member = env.state().environment
        self.assertEqual(member["revision"], member_two["revision"])
        self.assertEqual(member["state"], environment.PENDING)

    def test_a_superseded_presentations_identity_cannot_approve_the_new_revision(self):
        """Review blocking 1's first seed: finalization binds the full persisted presentation
        identity. Present revision 1, submit revision 2, approve with revision 2's number and
        proposal digest but revision 1's presentation identifier and document digest — the
        approval is never a presentation of revision 2, and it decides nothing."""
        env = DeliberationEnv()
        env.present()
        presented_identity = {"receipt_id": env.receipt_id, "document_digest": env.digest}
        member_two = env.submit_revision()
        substituted = {"run_id": env.run_id, "revision": member_two["revision"],
                       "proposal_digest": member_two["digest"], "outcome": "approve",
                       **presented_identity}
        result = env.service.trusted_environment_decision(
            run_id=env.run_id, payload=substituted)["result"]
        self.assertEqual(result["type"], "Fault", result)
        member = env.state().environment
        self.assertEqual(member["revision"], member_two["revision"])
        self.assertEqual(member["state"], environment.PENDING)
        self.assertIsNone(member["receipt_id"])

    def test_an_a_b_a_revisions_third_is_never_approved_by_the_firsts_presentation(self):
        """Review blocking 1's second seed: in A → B → A the third revision carries the first's
        proposal digest, so a digest-only binding would accept it — the persisted presentation
        identity (id and document digest with it) is what stays bound, and revision 3 has no
        presentation of its own; the substitution decides nothing."""
        env = DeliberationEnv()
        env.present()
        first_digest = env.member["digest"]
        presented_identity = {"receipt_id": env.receipt_id, "document_digest": env.digest}
        original_payload = copy.deepcopy(ENVIRONMENT_ACTION["payload"])
        _submit(env, _revise(ENVIRONMENT_ACTION["payload"]))  # B: a different body
        _submit(env, copy.deepcopy(original_payload))  # A again: revision 3
        member_three = env.state().environment
        self.assertEqual(member_three["revision"], 3)
        self.assertEqual(member_three["digest"], first_digest)
        substituted = {"run_id": env.run_id, "revision": member_three["revision"],
                       "proposal_digest": member_three["digest"], "outcome": "approve",
                       **presented_identity}
        result = env.service.trusted_environment_decision(
            run_id=env.run_id, payload=substituted)["result"]
        self.assertEqual(result["type"], "Fault", result)
        self.assertEqual(env.state().environment["state"], environment.PENDING)
        self.assertIsNone(env.state().environment["receipt_id"])

    def test_a_lost_compare_and_swap_refuses_the_stale_approval_on_the_retry(self):
        """Review non-blocking 1's interleaving, committed as the regression it is: the approval
        is planned against revision 1, a superseding submission wins the repository between that
        planning and the commit — the commit's compare-and-swap is lost — and the real retry
        replans against the committed revision 2 and refuses the stale approval. The revision's
        own presentation row stays immutable in the history the peer's commit recorded."""
        env = DeliberationEnv()
        env.present()
        coordinator = env.service._coordinator
        runs = coordinator.runs
        key = decode_handle(env.run_id)
        peer_runs = Runs()
        peer_runs.data.update(runs.data)
        peer = Coordinator(coordinator.workspace, coordinator.harness, peer_runs,
                           coordinator.artifacts, coordinator.profile_id,
                           {}, coordinator.spike_authority_selector, coordinator.image_resolver)
        raced = peer.handle({"type": "ObserveAction", "run_id": env.run_id,
                             "action": {**copy.deepcopy(ENVIRONMENT_ACTION),
                                        "payload": _revise(ENVIRONMENT_ACTION["payload"])}},
                            "peer-submit")
        self.assertEqual(raced["result"]["type"], "Allow", raced)
        raced_doc = peer_runs.data[key].value
        baseline = runs.read_calls
        cas_baseline = runs.cas_calls
        runs.conflict_on.add(cas_baseline + 1)  # the approval's first compare-and-swap is lost
        runs.replace_on_read[baseline + 3] = raced_doc
        result = env.decide("approve")
        self.assertEqual(result["type"], "Fault", result)
        self.assertTrue(runs.cas_calls - cas_baseline >= 1, "the approval's commit must be attempted")
        self.assertNotIn(cas_baseline + 1, runs.conflict_on, "the compare-and-swap must be lost once")
        self.assertTrue(runs.read_calls - baseline >= 3, "the retry must have replanned on a fresh read")
        stored = classify_and_decode(runs.read(key).value).state
        member = stored.environment
        self.assertEqual(member["revision"], 2)
        self.assertEqual(member["state"], environment.PENDING)
        self.assertIsNone(member["receipt_id"])
        self.assertEqual([row["revision"] for row in stored.environment_history], [1])
        self.assertEqual(stored.environment_history[0]["receipts"][0]["id"], env.receipt_id)
        self.assertEqual(env.view()["status"], "active")

    def test_a_receipt_of_another_run_or_generation_decides_nothing(self):
        env = DeliberationEnv()
        env.present()
        payload = _receipt(env.member, "someone-elses-run", "approve", env.receipt_id, env.digest)
        self.assertEqual(env.service.trusted_environment_decision(
            run_id=env.run_id, payload=payload)["result"]["type"], "Fault")
        self.assertEqual(env.state().environment["state"], environment.PENDING)

    def test_a_receipt_of_the_current_revision_with_a_strangers_digest_decides_nothing(self):
        """The staleness gate is a disjunction of identities: a receipt that names the current
        revision but a digest no member ever had is as stale as a wrong revision, and it never
        reaches the presentation lookup."""
        env = DeliberationEnv()
        payload = _receipt(env.member, env.run_id, "present", env.receipt_id,
                           _document_digest("stranger"))
        payload["proposal_digest"] = _document_digest("never-a-proposal")
        result = env.service.trusted_environment_decision(
            run_id=env.run_id, payload=payload)["result"]
        self.assertEqual(result["type"], "Fault", payload)
        self.assertEqual(env.state().environment["state"], environment.PENDING)
        self.assertIsNone(env.state().environment["receipt_id"])

    def test_an_earlier_revisions_decision_receipt_cannot_decide_the_new_revision(self):
        """A presentation alone finalizes a revision: its approved predecessor's own receipt is
        not the new revision's presentation, whatever revision numbers it swaps in."""
        env = DeliberationEnv()
        env.present()
        old_receipt_id = env.receipt_id
        approved_digest = env.digest
        self.assertEqual(env.decide("approve")["type"], "Allow")
        env.member = env.submit_revision()
        env.receipt_id = _new_receipt_id()
        replayed = {"run_id": env.run_id, "receipt_id": old_receipt_id,
                    "revision": env.member["revision"], "proposal_digest": env.member["digest"],
                    "outcome": "approve", "document_digest": approved_digest}
        result = env.service.trusted_environment_decision(run_id=env.run_id, payload=replayed)["result"]
        self.assertEqual(result["type"], "Fault", result)
        member = env.state().environment
        self.assertEqual(member["state"], environment.PENDING)
        self.assertIsNone(member["receipt_id"])

    def test_a_terminal_run_fetches_no_document_and_a_memberless_run_reads_inert(self):
        """The fetch leg is a plain read: a terminal run (and one whose revision never was
        pending a human) has no document to show, and the leg reads Inert — never a refusal,
        never a form, never a spend, whatever the store now holds for the stopped run."""
        env = ServiceEnv().start()
        stopped = env.request({"type": "EvaluateRun", "run_id": env.run_id, "intent": "stop"})
        self.assertEqual(stopped["type"], "Allow", stopped)
        fetched = env.service.trusted_environment_document(run_id=env.run_id)["result"]
        self.assertEqual(fetched["type"], "Inert", fetched)
        self.assertNotIn("presentation", fetched)
        # a fresh run before any proposal fetches the same honest nothing, run unnamed
        proposed = ServiceEnv().start().fully_approved()
        fetched = proposed.service.trusted_environment_document(run_id=proposed.run_id)["result"]
        self.assertEqual(fetched["type"], "Inert", fetched)
        self.assertNotIn("presentation", fetched)

    def test_an_impostor_document_digest_refuses_the_presentation_and_spends_nothing(self):
        """E8's write-first binding, the present leg: the only digest a fresh presentation may
        name is the document the core renders for this revision (the pair the private fetch
        returned, which the host writes before it presents). Any other digest is the human's
        stale fetch — the closed reason with its own recovery — refused before a single row is
        written. A decision that names a digest its own recorded presentation did not record
        keeps being the engine's fail-closed replay refusal, which wins before this gate."""
        env = DeliberationEnv()
        row = protocol.public_contract()["reasons"]["environment.document_mismatch"]
        impostor = _receipt(env.member, env.run_id, "present", _new_receipt_id(),
                            _document_digest("impostor-document"))
        blocked = env.service.trusted_environment_decision(
            run_id=env.run_id, payload=impostor)["result"]
        self.assertEqual(blocked["type"], "Block", blocked)
        reason = blocked["reasons"][0]
        self.assertEqual(reason["code"], "environment.document_mismatch")
        self.assertEqual(reason["message"], row["message"])
        self.assertEqual(reason["next_actions"], row["next_actions"])
        after = env.state().environment
        self.assertEqual(len(tuple(after["receipts"])), 0)  # nothing written, no allowance spent
        self.assertEqual(after["state"], environment.PENDING)
        self.assertIsNone(after["receipt_id"])
        # the honest present under the fetched document's digest is unaffected
        self.assertEqual(env.present()["type"], "Allow")
        self.assertEqual(len(tuple(env.state().environment["receipts"])), 1)

    def test_a_reject_under_the_current_document_closes_the_revision(self):
        """The same binding admits the decline of the very document the form showed: a reject
        that names the current document's digest closes the revision, and the run keeps the
        whole receipt row as its record."""
        env = DeliberationEnv()
        env.present()
        rejected = env.decide("reject")
        self.assertEqual(rejected["type"], "Allow", rejected)
        member = env.state().environment
        self.assertEqual(member["state"], environment.REJECTED)
        self.assertEqual(member["receipt_id"], env.receipt_id)
        # an exact replay of the reject reads Inert, whatever else an impostor may try
        replay = env.decide("reject")
        self.assertEqual(replay["type"], "Inert", replay)

    def test_a_stale_document_after_a_newer_revision_cannot_present(self):
        """A document fetched for an older revision cannot present the next one: after a new
        revision supersedes the pending pair, a present carrying the earlier fetched digest is
        the stale fetch of E8's reason — refused with nothing written, while the same present
        under the fresh revision's own fetched document admits, and its approve decides."""
        env = DeliberationEnv()
        stale_document = env.fetch_document()
        env.member = env.submit_revision()
        self.assertNotEqual(env.digest, stale_document["digest"])
        stale = _receipt(env.member, env.run_id, "present", _new_receipt_id(),
                         stale_document["digest"])
        blocked = env.service.trusted_environment_decision(
            run_id=env.run_id, payload=stale)["result"]
        self.assertEqual(blocked["type"], "Block", blocked)
        self.assertEqual(blocked["reasons"][0]["code"], "environment.document_mismatch")
        self.assertEqual(len(tuple(env.state().environment["receipts"])), 0)
        # the fresh fetched document is the write the host actually made under this revision
        self.assertEqual(env.present()["type"], "Allow")
        decided = env.decide("approve")
        self.assertEqual(decided["type"], "Allow", decided)
        self.assertEqual(env.state().environment["state"], environment.APPROVED)

    def test_a_configuration_revision_closes_a_pending_revision_and_its_reply_decides_nothing(self):
        """Spec §5's mutual exclusion in both directions: an author's configuration revision is
        admitted while an environment revision is pending and closes it as superseded without
        approval — the member row, its recorded presentation inside it, moves to the immutable
        history — so no reply of the closed revision can approve under the now-unapproved
        configuration, and the agent resubmits once the new configuration is approved."""
        env = DeliberationEnv()
        env.present()
        closed_before = dict(env.state().environment)
        revised = _revised_configure_run()
        self.assertEqual(env.observe(revised)["type"], "Allow")
        state = env.state()
        self.assertIsNone(state.environment, "the pending revision is closed, not left pending")
        self.assertEqual([row["revision"] for row in state.environment_history],
                         [closed_before["revision"]])
        self.assertEqual(dict(state.environment_history[0]), closed_before)
        approve_current(env.service._coordinator, env.run_id)  # the human approves the new configuration
        decided = env.decide("approve")  # the old reply, now against the approved new configuration
        self.assertEqual(decided["type"], "Fault", decided)
        self.assertIsNone(env.state().environment)

    def test_a_reply_of_a_configuration_closed_revision_replays_inert_only_when_exact(self):
        """The closed revision's own recorded presentation is still its history: an exact replay
        is the engine's historical Inert, and anything else decides nothing."""
        env = DeliberationEnv()
        env.present()
        exact = _receipt(env.member, env.run_id, "present", env.receipt_id, env.digest)
        revised = _revised_configure_run()
        self.assertEqual(env.observe(revised)["type"], "Allow")
        replay = env.service.trusted_environment_decision(
            run_id=env.run_id, payload=dict(exact))["result"]
        self.assertEqual(replay["type"], "Inert", replay)
        self.assertIsNone(env.state().environment)

    def test_a_configuration_revision_never_closes_a_decided_revision(self):
        """Only a pending revision closes: a decided one (chosen under the configuration it was
        decided with) keeps its decision across later configuration revisions, its receipt
        immutable."""
        env = DeliberationEnv()
        env.present()
        self.assertEqual(env.decide("approve")["type"], "Allow")
        approved_before = dict(env.state().environment)
        revised = _revised_configure_run()
        self.assertEqual(env.observe(revised)["type"], "Allow")
        self.assertEqual(dict(env.state().environment), approved_before)

    def test_an_auto_run_derives_its_approval_and_refuses_every_receipt(self):
        env = DeliberationEnv(control_mode="auto")
        self.assertEqual(env.member["state"], environment.APPROVED)
        self.assertIsNone(env.member["receipt_id"])
        for outcome in ("present", "approve", "reject"):
            with self.subTest(outcome=outcome):
                self.assertEqual(env.decide(outcome)["type"], "Fault")
        self.assertEqual(env.state().environment["state"], environment.APPROVED)

    def test_the_presentation_allowance_refuses_a_further_presentation_after_its_bound(self):
        """Re-review 1 (non-blocking 3): the per-revision presentation allowance is behavioural,
        not merely a storage bound. Once this revision has recorded its 128 presentations, a
        further presentation refuses with the closed reason environment-presentations-exhausted
        (its own registry row, validated on the wire, its recovery named in next_actions:
        submit a new environment revision) — nothing is failed closed, the run stays active,
        and the limit stays separate from the run's revision ceiling."""
        env = DeliberationEnv()
        member = env.member
        bound = environment.MAX_MEMBER_RECEIPTS
        presented = []
        for index in range(bound):
            payload = _receipt(member, env.run_id, "present", _new_receipt_id(),
                               env.digest)
            result = env.service.trusted_environment_decision(
                run_id=env.run_id, payload=payload)["result"]
            self.assertEqual(result["type"], "Allow", f"presentation {index}: {result}")
            presented.append(payload)
        self.assertEqual(len(env.state().environment["receipts"]), bound)
        over = _receipt(member, env.run_id, "present", _new_receipt_id(),
                        _document_digest("over-the-allowance"))
        refused = env.service.trusted_environment_decision(
            run_id=env.run_id, payload=over)["result"]
        self.assertTrue(protocol.validate_public_result(refused), refused)
        self.assertEqual(refused["type"], "Block", refused)
        self.assertEqual([row["code"] for row in refused["reasons"]],
                         [environment.ENVIRONMENT_PRESENTATIONS_EXHAUSTED])
        self.assertIn(_PUBLIC_CONTRACT["reasons"][environment.ENVIRONMENT_PRESENTATIONS_EXHAUSTED][
            "message"], refused["reasons"][0]["message"])
        self.assertEqual(refused["reasons"][0]["next_actions"], ["environment.propose"])
        self.assertEqual(len(env.state().environment["receipts"]), bound, "no row was written")
        self.assertEqual(env.state().environment["state"], environment.PENDING)
        self.assertEqual(env.view()["status"], "active")

    def test_a_recorded_presentation_still_finalizes_over_the_allowance(self):
        """The allowance refuses only a further presentation: a presentation the revision
        already recorded can still be approved at and past the bound, its replay stays the
        engine's Inert, and a new environment revision starts a fresh allowance."""
        env = DeliberationEnv()
        member = env.member
        bound = environment.MAX_MEMBER_RECEIPTS
        first_id = None
        for index in range(bound):
            receipt_id = _new_receipt_id()
            if first_id is None:
                first_id = receipt_id
            payload = _receipt(member, env.run_id, "present", receipt_id,
                               env.digest)
            self.assertEqual(env.service.trusted_environment_decision(
                run_id=env.run_id, payload=payload)["result"]["type"], "Allow")
        # the approve that names the first recorded presentation, digest and all
        approve = _receipt(member, env.run_id, "approve", first_id, env.digest)
        result = env.service.trusted_environment_decision(
            run_id=env.run_id, payload=approve)["result"]
        self.assertEqual(result["type"], "Allow", result)
        self.assertEqual(env.state().environment["state"], environment.APPROVED)
        self.assertEqual(env.state().environment["receipt_id"], first_id)
        self.assertEqual(env.service.trusted_environment_decision(
            run_id=env.run_id, payload=approve)["result"]["type"], "Inert")
        # a new revision starts a fresh presentation allowance
        env.member = env.submit_revision()
        fresh = _receipt(env.member, env.run_id, "present", _new_receipt_id(),
                         env.digest)
        result = env.service.trusted_environment_decision(
            run_id=env.run_id, payload=fresh)["result"]
        self.assertEqual(result["type"], "Allow", result)
        self.assertEqual(len(env.state().environment["receipts"]), 1)
        self.assertFalse(environment.revisions_exhausted(env.state()))

    def test_a_malformed_receipt_never_reaches_the_domain_logic(self):
        """An outcome outside the closed receipt set is invalid at the service boundary: a cancel,
        a timeout or a missing surface is never a decision — it leaves the revision pending."""
        env = DeliberationEnv()
        bad = _receipt(env.member, env.run_id, "present", env.receipt_id, env.digest)
        bad["outcome"] = "cancel"
        self.assertEqual(env.service.trusted_environment_decision(
            run_id=env.run_id, payload=bad)["result"]["type"], "Fault")
        self.assertEqual(dict(env.state().environment), dict(env.member))


class AdmissionDecisionTests(unittest.TestCase):
    """The pure decisions E7 owns: qualify admission (with the attempt ceiling) and the spike
    prerequisite. The ceiling is the generated module's own word, never restated here."""

    def test_every_environment_reason_constant_is_a_published_registry_row(self):
        """The emission vocabulary has one owner: every environment admission reason the core
        can emit names a row of the public reason registry (the registry key must exist), so a
        core constant can never drift from the published codes."""
        registry = _PUBLIC_CONTRACT["reasons"]
        emitted = {reason for reason in (
            environment.ENVIRONMENT_REQUIRED,
            environment.ENVIRONMENT_PENDING,
            environment.ENVIRONMENT_REJECTED,
            environment.PROFILE_NOT_ALLOWED,
            environment.QUALIFICATION_REQUIRED,
            environment.QUALIFICATION_ATTEMPTS_EXHAUSTED,
            environment.ENVIRONMENT_REVISIONS_EXHAUSTED,
            environment.ENVIRONMENT_PRESENTATIONS_EXHAUSTED,
        )}
        for reason in emitted:
            self.assertIn(reason, registry, reason)

    def _attempt_plan(self, state, count):
        revision = state.environment["revision"]
        return tuple({**{"attempt_id": "qualification-1", "revision": revision,
                        "started_stamp": 1, "outcome": "failed", "record_digest": None,
                        "reason": "attempt-failed", "probe": None},
                      "attempt_id": f"qualification-{index + 1}", "started_stamp": index + 1}
                     for index in range(count))

    def test_a_pending_revision_holds_qualify_for_the_human(self):
        env = DeliberationEnv()
        env.present()
        result = env.observe(copy.deepcopy(QUALIFY_ACTION))
        self.assertEqual(result["type"], "Block", result)
        self.assertEqual([reason["code"] for reason in result["reasons"]],
                         [environment.ENVIRONMENT_PENDING])

    def test_an_approved_revision_admits_but_the_effect_still_refuses_until_its_node_lands(self):
        """E7 owns the decision only: with an approved revision the admission passes and the
        missing host effect (E9) refuses with the one closed reason, charging nothing."""
        env = DeliberationEnv(control_mode="auto")
        result = env.observe(copy.deepcopy(QUALIFY_ACTION))
        self.assertEqual(result["type"], "Block", result)
        self.assertEqual([reason["code"] for reason in result["reasons"]],
                         [ENVIRONMENT_NOT_AVAILABLE])

    def test_the_attempt_ceiling_decides_admission(self):
        """One attempt short of the ceiling is admissible; at the ceiling the closed reason is the
        exhausted one, read against the generated bound. The rows are charged only by E9's
        transaction, so the pure decision is what E7 can cover deterministically."""
        env = DeliberationEnv(control_mode="auto")
        state = env.service._coordinator.last_state

        def _with_attempts(count):
            return replace(state, qualification_attempts=self._attempt_plan(state, count))

        self.assertIsNone(environment.qualify_admission(state))
        ceiling = environment.MAX_QUALIFICATION_ATTEMPTS
        self.assertIsNone(environment.qualify_admission(_with_attempts(ceiling - 1)))
        self.assertEqual(environment.qualify_admission(_with_attempts(ceiling)),
                         environment.QUALIFICATION_ATTEMPTS_EXHAUSTED)

    def test_approval_problems_outrank_the_ceiling(self):
        """An unapproved revision never names exhaustion: the missing approval is the nearer fact."""
        env = DeliberationEnv(control_mode="auto")
        state = env.service._coordinator.last_state
        pending = replace(state, environment=environment.revision_member(
            _revise(ENVIRONMENT_ACTION["payload"]), environment.next_revision(
                [state.environment["revision"]]), approved=False))
        self.assertEqual(environment.qualify_admission(pending), environment.ENVIRONMENT_PENDING)
        rejected = replace(state, environment={**dict(
            environment.revision_member(_revise(ENVIRONMENT_ACTION["payload"]),
                                        environment.next_revision(
                                            [state.environment["revision"]]), approved=False)),
            "state": environment.REJECTED})
        self.assertEqual(environment.qualify_admission(rejected), environment.ENVIRONMENT_REJECTED)

    def test_the_spike_prerequisite_names_the_missing_step(self):
        """A spike with no qualified binding reads the two decisions E10 will wire: no approved
        revision needs a proposal first; an approved one with nothing bound needs the attempt.
        StartRun still binds spikes_unavailable, so the missing binding is the real run fact."""
        without_proposal = ServiceEnv().start().fully_approved()
        self.assertIsNone(without_proposal.state().environment)
        self.assertEqual(environment.spike_prerequisite(without_proposal.state()),
                         environment.ENVIRONMENT_REQUIRED)
        pending = DeliberationEnv()
        self.assertEqual(environment.spike_prerequisite(pending.state()),
                         environment.ENVIRONMENT_REQUIRED)
        approved = DeliberationEnv(control_mode="auto")
        self.assertEqual(environment.spike_prerequisite(approved.state()),
                         environment.QUALIFICATION_REQUIRED)

    def test_the_revision_ceiling_refuses_a_further_submission_and_never_drops_a_row(self):
        """Spec §4.2's retention rule: every revision and its receipts stay immutable, and the
        bound the state contract carries is the per-run environment revision ceiling — a further
        submission past it Blocks with its own closed reason, and nothing is ever evicted (a
        qualification may still refer to an earlier revision)."""
        env = ServiceEnv().start().fully_approved()
        member = env.observe(copy.deepcopy(ENVIRONMENT_ACTION))
        self.assertEqual(member["type"], "Allow", member)
        bound = environment.MAX_ENVIRONMENT_HISTORY
        for revision in range(1, bound):
            payload = copy.deepcopy(ENVIRONMENT_ACTION["payload"])
            payload["rationale"] = f"revision {revision + 1}: {payload['rationale']}"
            further = env.observe({**copy.deepcopy(ENVIRONMENT_ACTION), "payload": payload})
            self.assertEqual(further["type"], "Allow", further)
        state = env.state()
        self.assertEqual(state.environment["revision"], bound)
        self.assertEqual(len(state.environment_history), bound - 1)
        self.assertEqual([row["revision"] for row in state.environment_history],
                         list(range(1, bound)))
        refused = env.observe(_revised_payload())
        self.assertTrue(protocol.validate_public_result(refused), refused)
        self.assertEqual(refused["type"], "Block", refused)
        self.assertEqual([row["code"] for row in refused["reasons"]],
                         [environment.ENVIRONMENT_REVISIONS_EXHAUSTED])
        row = _PUBLIC_CONTRACT["reasons"][environment.ENVIRONMENT_REVISIONS_EXHAUSTED]
        self.assertIn(row["message"], refused["reasons"][0]["message"])
        self.assertTrue(environment.revisions_exhausted(env.state()))
        after = env.state()
        self.assertEqual([r["revision"] for r in after.environment_history], list(range(1, bound)))
        self.assertEqual(after.environment["revision"], bound)

    def test_one_revision_short_of_the_ceiling_still_admits_and_the_pure_word_holds(self):
        """The pure decision, read against the generated bound: one revision short is admitted,
        at and past the ceiling the word is the exhausted one — the ceiling counts the current
        member and every immutable row, so a configuration-closed run may also face it."""
        env = ServiceEnv().start().fully_approved()
        env.observe(copy.deepcopy(ENVIRONMENT_ACTION))
        state = env.state()
        bound = environment.MAX_ENVIRONMENT_HISTORY
        self.assertFalse(environment.revisions_exhausted(state))
        rows = tuple(environment.revision_member(ENVIRONMENT_ACTION["payload"], revision, approved=False)
                     for revision in range(1, bound + 1))
        at_ceiling = replace(state, environment_history=rows[:-1], environment=rows[-1])
        self.assertTrue(environment.revisions_exhausted(at_ceiling))
        closed = replace(state, environment=None, environment_history=rows)
        self.assertTrue(environment.revisions_exhausted(closed))


if __name__ == "__main__":
    unittest.main()
