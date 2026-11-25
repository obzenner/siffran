#!/usr/bin/env python3
"""Empirica 7.2 E7: the environment deliberative receipts and the admission decisions (spec §5).

The behaviour of the private environment receipt (host ingress, never a public author action):
the write-first presentation of a pending revision, the approve/reject that names it, replay and
staleness, the document-digest equality with the recorded presentation, the auto mode that derives
approval and therefore refuses every receipt, and the revision race the transaction's CAS loop
re-reads. Plus the two pure decisions E7 owns and later nodes build on: ``qualify``'s admission
(including the attempt ceiling) and the spike prerequisite
<plugins/../doc/design/specs/empirica-7.2-run-scoped-environment-qualification.md> (§5).

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
from core import environment
from core.canonical import canonical_digest
from core.evaluation import ENVIRONMENT_NOT_AVAILABLE
from test_environment_actions import ENVIRONMENT_ACTION, QUALIFY_ACTION, ServiceEnv

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


class DeliberationEnv(ServiceEnv):
    """A fully approved run with one pending environment revision and its presentation."""

    def __init__(self, **start):
        super().__init__(**start)
        self.start().fully_approved()
        _submit(self, copy.deepcopy(ENVIRONMENT_ACTION["payload"]))
        self.member = self.state().environment
        self.receipt_id = _new_receipt_id()
        self.digest = _document_digest("present")

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
        return self.state().environment


class ReceiptTests(unittest.TestCase):
    """The receipt races and replays of a deliberative run (spec §5)."""

    def test_a_presentation_records_and_the_revision_stays_pending(self):
        env = DeliberationEnv()
        result = env.present()
        self.assertEqual(result["type"], "Allow", result)
        self.assertEqual(env.state().environment["state"], environment.PENDING)

    def test_an_exact_presentation_replay_is_inert_and_no_other_content_fits_the_id(self):
        env = DeliberationEnv()
        env.present()
        self.assertEqual(env.present()["type"], "Inert")
        self.assertEqual(env.present(digest=_document_digest("other"))["type"], "Fault")
        self.assertEqual(env.state().environment["state"], environment.PENDING)

    def test_nothing_finalizes_without_its_presentation_and_its_recorded_digest(self):
        """An approve under an unshown id, or with a digest its present did not record, decides
        nothing; ADR-0072's write-first word is what the presentation artifact records."""
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
        self.assertEqual(env.decide("approve")["type"], "Inert")
        self.assertEqual(dict(env.state().environment), dict(member))
        self.assertEqual(env.decide("reject")["type"], "Fault")
        self.assertEqual(env.state().environment["state"], environment.APPROVED)

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

    def test_a_race_that_supersedes_mid_decision_leaves_the_new_revision_pending(self):
        """The transaction re-reads against every CAS loss: an approval pitched at revision 1
        commits nothing once a concurrent author — a second coordinator over the same stores, the
        production commit path — recorded revision 2. The reply is stale and refused, and the
        run stays honest: the superseding revision is pending, the history immutable."""
        from application.run_state import classify_and_decode
        from application.transaction import Coordinator
        env = DeliberationEnv()
        env.present()
        coordinator = env.service._coordinator
        peer = Coordinator(coordinator.workspace, coordinator.harness, coordinator.runs,
                           coordinator.artifacts, coordinator.profile_id,
                           {}, coordinator.spike_authority_selector, coordinator.image_resolver)
        raced = peer.handle({"type": "ObserveAction", "run_id": env.run_id,
                             "action": {**copy.deepcopy(ENVIRONMENT_ACTION),
                                        "payload": _revise(ENVIRONMENT_ACTION["payload"])}},
                            "peer-submit")
        self.assertEqual(raced["result"]["type"], "Allow", raced)
        result = env.decide("approve")
        self.assertEqual(result["type"], "Fault", result)
        stored = classify_and_decode(coordinator.runs.read(decode_handle(env.run_id)).value).state
        member = stored.environment
        self.assertEqual(member["revision"], 2)
        self.assertEqual(member["state"], environment.PENDING)
        self.assertEqual([row["revision"] for row in stored.environment_history], [1])
        observed = env.view()
        self.assertEqual(observed["status"], "active")

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
        """A presentation alone finalizes a revision: its approved predecessor's own receipt — or
        any decision artifact — is not the new revision's presentation, whatever digest it names."""
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

    def test_an_auto_run_derives_its_approval_and_refuses_every_receipt(self):
        env = DeliberationEnv(control_mode="auto")
        self.assertEqual(env.member["state"], environment.APPROVED)
        self.assertIsNone(env.member["receipt_id"])
        for outcome in ("present", "approve", "reject"):
            with self.subTest(outcome=outcome):
                self.assertEqual(env.decide(outcome)["type"], "Fault")
        self.assertEqual(env.state().environment["state"], environment.APPROVED)

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

    def test_the_history_bound_never_corrupts_a_run_that_keeps_revising(self):
        """The immutable history is bounded: the oldest row drops, the revisions stay monotonic."""
        env = DeliberationEnv()
        bound = environment.MAX_ENVIRONMENT_HISTORY
        for _ in range(bound):
            _submit(env, _revise(ENVIRONMENT_ACTION["payload"]))
        history = env.state().environment_history
        self.assertEqual(len(history), bound)
        oldest = history[0]["revision"]
        _submit(env, _revise(ENVIRONMENT_ACTION["payload"]))
        history = env.state().environment_history
        self.assertEqual(len(history), bound)
        self.assertEqual(history[0]["revision"], oldest + 1)
        self.assertGreater(env.state().environment["revision"], history[-1]["revision"])


if __name__ == "__main__":
    unittest.main()
