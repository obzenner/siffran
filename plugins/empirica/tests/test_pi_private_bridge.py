"""The Pi private bridge binds the configuration document before the host shows it (ADR-0072).

PR 09: ``private_bridge._governance_context`` is the one document writer for Pi — and, since
the review-2 m2 fix, only the mediator's own refresh (a raw ``bind_document: true``) qualifies;
a plain refresh binds and writes nothing. The private answer it returns to the adapter replaces
the core's ``{text, digest}`` pair with the written file's identity —
``{digest, path, priors, question}``, with ``question`` the parsed document's core-derived
discriminator (M1, review 2) — so the Pi transport never carries document bytes and the human
opens the file at its absolute path. A pair that cannot be bound (malformed, digest
inconsistent, storage refused) loses its document and gains ``document_unavailable: true`` so the
adapter refuses ``governance.approval_unavailable`` before any present.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sandbox_isolation import force_absent_sandbox_home  # noqa: E402

force_absent_sandbox_home()

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters" / "pi"))
import private_bridge  # noqa: E402
from adapters import bridge, confirmation  # noqa: E402
from adapters.state.hitl import DocumentMismatch  # noqa: E402
from application import protocol as _proto  # noqa: E402
from core import environment as core_environment, hitl as core_hitl  # noqa: E402
from governance_setup import approve_current, invocation_for  # noqa: E402
from test_environment_actions import ENVIRONMENT_ACTION, QUALIFY_ACTION  # noqa: E402
from test_governance import GRAPH  # noqa: E402

PI_PROFILE = _proto.host_profile_id("pi")
PROTOCOL_REQUEST = {"protocol": "empirica/v2", "request_id": "pi-private-bridge"}


class PiPrivateBridgeDocumentTests(unittest.TestCase):
    def setUp(self):
        self.machine_root = Path(tempfile.mkdtemp(prefix="pi-bridge-home-"))
        self.addCleanup(shutil.rmtree, self.machine_root, ignore_errors=True)
        # a real machine home for the run store and the sidecar, a real git repo for the knowledge plane
        self.machine_home = self.machine_root / "home"
        self.machine_home.mkdir(parents=True)
        self.repo = self.machine_root / "repo"
        os.makedirs(self.repo, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        environment = {"EMPIRICA_HOME": str(self.machine_home), "EMPIRICA_REPO_DIR": str(self.repo)}
        self._env = patch.dict(os.environ, environment)
        self._env.start()
        self.addCleanup(self._env.stop)
        # the real production composition for the profile, over the isolated machine home
        self.service = bridge.build_service(PI_PROFILE)
        self.run_id = self._dispatch({"type": "StartRun", "control_mode": "deliberative",
                                      "goal": "supplied pi bridge task",
                                      "invocation": invocation_for(PI_PROFILE),
                                      "selector": {"project": "pi-bridge", "session": "governed"}})["run"]["id"]
        self._dispatch({"type": "ObserveAction", "run_id": self.run_id,
                        "action": {"kind": "route", "reason": "supplied context"}})
        self._dispatch({"type": "ObserveAction", "run_id": self.run_id,
                        "action": {"kind": "graph", "payload": copy.deepcopy(GRAPH)}})
        self._dispatch({"type": "ObserveAction", "run_id": self.run_id,
                        "action": {"kind": "configure_run",
                                   "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2},
                                   "rationale": "sized for the gating claims and one audit retry"}})
        self.context = {"author": {"provider_id": "anthropic", "model_id": "claude-sonnet-4-6",
                                    "source": "pi-context", "observed_by": "host"},
                        "ingress": "pi_ui"}

    def _dispatch(self, command: dict) -> dict:
        response = self.service.dispatch({**PROTOCOL_REQUEST, "command": command})
        self.assertEqual(response["result"]["type"], "Allow", response["result"])
        return response["result"]

    def _stored_receipts(self):
        """X5 (Copilot #50): the bridge answers through ``bridge.build_service`` — its own fresh
        service instance per call — so this class's coordinator snapshot predates the bridge's
        writes: reload the run from the store before every receipt-count assertion, exactly as
        the governance suite's ``_receipts`` helper reassembles (``GetRun`` reads the stored
        bytes, and the assertions face the persisted effects, never a cached setup snapshot)."""
        self._dispatch({"type": "GetRun", "run_id": self.run_id})
        return self.service._coordinator.last_snapshot.state.governance["receipts"]

    def test_the_context_answer_binds_the_written_document_without_its_text(self):
        """The bridge verifies the core's rendered pair, writes it once beside the run, and hands
        the adapter the file's identity — never the bytes. Binding spends nothing."""
        response = private_bridge._governance_context(PI_PROFILE, {"run_id": self.run_id, "bind_document": True},
                                                      copy.deepcopy(self.context))
        presentation = response["result"]["presentation"]
        document = presentation["document"]
        self.assertNotIn("text", document, "the Pi transport never carries document bytes")
        self.assertRegex(document["digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertTrue(document["path"].startswith(str(self.machine_home)))
        self.assertTrue(Path(document["path"]).is_absolute())
        # M1 (review 2): the answer also carries the parsed document's core-derived question —
        # the discriminator the TypeScript mediation chooses the dialog from, never an
        # adapter flag
        self.assertEqual(document["question"], "approve")
        self.assertNotIn("document_unavailable", presentation)
        # the file was written exactly once, with the core's own bytes, read-only
        data = Path(document["path"]).read_bytes()
        self.assertEqual(core_hitl.digest(data), document["digest"])
        self.assertEqual(stat.S_IMODE(os.stat(document["path"]).st_mode), 0o400)
        self.assertEqual(len(self._stored_receipts()), 0, "binding a document spends nothing")

    def test_a_plain_refresh_carries_no_document_and_writes_nothing(self):
        """m2 (review 2): only the mediator's own refresh (``bind_document: true``) binds.
        A plain refresh — the one the TypeScript adapter runs in front of every tool_call
        and session event — receives the answer with no document at all, marks nothing
        unavailable, and writes nothing under the machine home. Binding spends nothing."""
        response = private_bridge._governance_context(PI_PROFILE, {"run_id": self.run_id},
                                                      copy.deepcopy(self.context))
        presentation = response["result"]["presentation"]
        self.assertNotIn("document", presentation)
        self.assertNotIn("document_unavailable", presentation,
                         "a plain refresh is not a refusal: nothing was asked of the writer")
        self.assertEqual(list(self.machine_home.rglob("hitl/*.json")), [],
                         "a plain refresh writes no document")
        self.assertEqual(len(self._stored_receipts()), 0)

    def test_a_confirm_document_carries_the_core_owned_priors(self):
        """An amendment document binds too, and its priors are read out of the written document's
        own amendment block — the run/4 superseded ceilings, never recomputed by the host."""
        answer = private_bridge._governance_context(PI_PROFILE, {"run_id": self.run_id, "bind_document": True},
                                                    copy.deepcopy(self.context))
        g = answer["result"]["run"]["governance"]
        envelope = {"run_id": self.run_id, "receipt_id": "r" * 32,
                    "proposal_digest": g["proposal_digest"], "plan_revision": g["plan_revision"],
                    "approval_kind": "host_ui",
                    "document_digest": answer["result"]["presentation"]["document"]["digest"]}
        presented = bridge.trusted_governance_decision(PI_PROFILE, self.run_id,
                                                       {**envelope, "outcome": "present"})
        self.assertEqual(presented["result"]["type"], "Allow", presented["result"])
        amended = bridge.trusted_governance_decision(PI_PROFILE, self.run_id, {**envelope, "submission": {
            "action": "approve",
            "configuration": {"budgets": {"max_passes": 6, "max_spawns": 1, "max_audit_spawns": 2}}}})["result"]
        self.assertEqual(amended["run"]["governance"]["plan_revision"], g["plan_revision"] + 1)
        response = private_bridge._governance_context(PI_PROFILE, {"run_id": self.run_id, "bind_document": True},
                                                      copy.deepcopy(self.context))
        presentation = response["result"]["presentation"]
        self.assertNotIn("document_unavailable", presentation)
        document = presentation["document"]
        self.assertEqual(document["priors"], {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2})
        self.assertEqual(document["question"], "confirm_amendment")
        # the priors are the record's own: read the written document and compare its amendment
        written = json.loads(Path(document["path"]).read_bytes())
        self.assertEqual(written["question"], "confirm_amendment")
        self.assertEqual({key: row["was"] for key, row in written["amendment"]["budgets"].items()},
                         document["priors"])

    def test_an_unbindable_document_names_itself_unavailable_and_writes_nothing(self):
        """A context answer whose pair cannot be verified arrives at the adapter with the document
        gone and the marker present: nothing was written, nothing was spent."""
        def forged(profile, run_id, payload):
            response = real_context(profile, run_id, payload)
            document = response["result"]["presentation"]["document"]
            response["result"]["presentation"]["document"] = {
                **document,
                "digest": document["digest"][:-1] + ("0" if document["digest"][-1] != "0" else "1")}
            return response
        real_context = bridge.trusted_governance_context
        with patch.object(bridge, "trusted_governance_context", forged):
            response = private_bridge._governance_context(PI_PROFILE, {"run_id": self.run_id, "bind_document": True},
                                                          copy.deepcopy(self.context))
        presentation = response["result"]["presentation"]
        self.assertNotIn("document", presentation)
        self.assertIs(presentation["document_unavailable"], True)
        self.assertEqual(list(self.machine_home.rglob("hitl/*.json")), [])
        self.assertEqual(len(self._stored_receipts()), 0)

    def test_a_digest_matching_document_without_a_supported_question_is_unbindable(self):
        """X3 (Copilot #49), the parity pin for the leg that has carried the check since PR 13a:
        a pair whose bytes are digest-CONSISTENT objects — ``{}`` or ``{"question": "other"}`` —
        must refuse exactly as a malformed pair does, before any present: the parsed document
        already carries one of the two core-derived questions, or the adapter is told the document
        is unavailable and writes nothing."""
        real_context = bridge.trusted_governance_context
        for text in ("{}", '{"question": "other"}'):
            with self.subTest(document=text):
                def forged(profile, run_id, payload, text=text):
                    response = real_context(profile, run_id, payload)
                    data = core_hitl.render(json.loads(text))
                    response["result"]["presentation"]["document"] = {
                        "text": data.decode("utf-8"), "digest": core_hitl.digest(data)}
                    return response
                with patch.object(bridge, "trusted_governance_context", forged):
                    response = private_bridge._governance_context(
                        PI_PROFILE, {"run_id": self.run_id, "bind_document": True},
                        copy.deepcopy(self.context))
                presentation = response["result"]["presentation"]
                self.assertNotIn("document", presentation)
                self.assertIs(presentation["document_unavailable"], True)
                self.assertEqual(list(self.machine_home.rglob("hitl/*.json")), [])
                self.assertEqual(len(self._stored_receipts()), 0)

    def test_a_write_failure_also_names_itself_unavailable(self):
        """Storage cannot lie into presenting: the sidecar's own named failures become the same
        marker the adapter refuses on."""
        class Broken:
            def __init__(self, *args, **kwargs):
                pass

            def write(self, _key, _data):
                raise DocumentMismatch("different bytes already published")
        # the bridge loads its sidecar store lazily inside the writer, so the patch pins the
        # class where the function imports it from
        with patch("adapters.state.hitl.HitlDocuments", Broken):
            response = private_bridge._governance_context(PI_PROFILE, {"run_id": self.run_id, "bind_document": True},
                                                          copy.deepcopy(self.context))
        presentation = response["result"]["presentation"]
        self.assertNotIn("document", presentation)
        self.assertIs(presentation["document_unavailable"], True)
        self.assertEqual(list(self.machine_home.rglob("hitl/*.json")), [])


class PiEvidenceWriteFirstTests(unittest.TestCase):
    """PR 11: the shared evidence flow writes the document before it presents (ADR-0072).

    ``private_bridge._evidence_presentation`` calls the one shared ``present``
    (adapters/confirmation.py), so the fetch → verify → write → present-with-digest sequence and its
    sidecar file come to Pi with no bridge-specific logic. The screen data (the items) is the same
    data as before; the presentation answer additionally names the written document's identity
    (never its bytes) — what PR 12's dialog will render. The confirmation leg keeps today's shape:
    the TypeScript mediator does not round-trip the digest yet (PR 12 wires it), and the core's
    staging still admits a legacy confirmation of a bound presentation — pinned here so PR 12 has
    its before-state. Everything runs on the isolated machine home and repo of this module's setUp
    pattern; nothing may reach the developer's real ~/.empirica-plugin.
    """

    def setUp(self):
        self.machine_root = Path(tempfile.mkdtemp(prefix="pi-bridge-evidence-"))
        self.addCleanup(shutil.rmtree, self.machine_root, ignore_errors=True)
        self.machine_home = self.machine_root / "home"
        self.machine_home.mkdir(parents=True)
        self.repo = self.machine_root / "repo"
        os.makedirs(self.repo, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        environment = {"EMPIRICA_HOME": str(self.machine_home), "EMPIRICA_REPO_DIR": str(self.repo)}
        self._env = patch.dict(os.environ, environment)
        self._env.start()
        self.addCleanup(self._env.stop)
        self.service = bridge.build_service(PI_PROFILE)
        self.run_id = self._dispatch({"type": "StartRun", "control_mode": "deliberative",
                                      "goal": "pi writes its evidence document first",
                                      "invocation": invocation_for(PI_PROFILE),
                                      "selector": {"project": "pi-evidence", "session": "governed"}})["run"]["id"]
        self._dispatch({"type": "ObserveAction", "run_id": self.run_id,
                       "action": {"kind": "route", "reason": "supplied context"}})
        self._dispatch({"type": "ObserveAction", "run_id": self.run_id,
                       "action": {"kind": "graph", "payload": copy.deepcopy(GRAPH)}})
        self._dispatch({"type": "ObserveAction", "run_id": self.run_id,
                       "action": {"kind": "configure_run",
                                  "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2},
                                  "rationale": "sized for the gating claims"}})
        approve_current(self.service._coordinator, self.run_id)
        self._dispatch({"type": "ObserveAction", "run_id": self.run_id,
                       "action": {"kind": "investigate"}})
        self.assertEqual(self.service.trusted_human_surface(
            run_id=self.run_id, payload={"run_id": self.run_id, "surface": "human"})["result"]["type"],
            "Allow")
        self._dispatch({"type": "ObserveAction", "run_id": self.run_id,
                       "action": {"kind": "research", "claim_id": GRAPH["root"], "source_kind": "runtime",
                                  "result": "supports",
                                  "payload": {"source_ref": "deploy output", "citation": "observed directly",
                                              "spike_unavailable": "No harness."}}})

    def _dispatch(self, command: dict) -> dict:
        response = self.service.dispatch({**PROTOCOL_REQUEST, "command": command})
        self.assertEqual(response["result"]["type"], "Allow", response["result"])
        return response["result"]

    def _presentation_rows(self) -> list[dict]:
        view = self.service.dispatch({**PROTOCOL_REQUEST, "command": {"type": "GetRun", "run_id": self.run_id}})
        assert view["result"]["type"] == "Allow"
        return [row for row in self.service._coordinator.last_snapshot.history
                if row.get("kind") == "evidence_presentation"]

    def _confirmation_rows(self) -> list[dict]:
        return [row for row in self.service._coordinator.last_snapshot.history
                if row.get("kind") == "evidence_confirmation"]

    def raw(self) -> dict:
        return {"operation": "evidence_presentation", "run_id": self.run_id}

    def test_the_presentation_writes_the_batches_document_before_it_is_shown(self):
        answer = private_bridge._evidence_presentation(PI_PROFILE, self.raw(), {})
        self.assertEqual(answer["type"], "evidence_presentation")
        receipt_id, items = answer["receipt_id"], answer["items"]
        self.assertTrue(items, "the batch was shown")
        # the screen data Pi receives is today's: exactly the item keys the shared builder emits
        for item in items:
            self.assertEqual(item["subject"], "research")
            self.assertEqual(set(item), {"subject", "id", "record_ids", "claims", "source_kind",
                                        "source_ref", "quote", "class", "reason",
                                        "title", "description", "note_title"})
        document = answer["document"]
        # PR 16 (A10/A12): the bridge's answer also names the presentation-only link fields —
        # `url` (None here: this machine home serves no observer) and the run's compact id —
        # beside the identity. Never bytes: the document itself stays in the file.
        self.assertEqual(set(document), {"digest", "path", "url", "run12"},
                         "no bytes cross the transport")
        self.assertIsNone(document["url"])
        self.assertRegex(document["run12"], r"^[0-9a-f]{12}$")
        self.assertRegex(document["digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertTrue(document["path"].startswith(str(self.machine_home)))
        data = Path(document["path"]).read_bytes()
        self.assertEqual(core_hitl.digest(data), document["digest"])
        self.assertEqual(stat.S_IMODE(os.stat(document["path"]).st_mode), 0o400)
        # the core recorded the digested presentation, and the file WAS written before the show
        rows = self._presentation_rows()
        self.assertEqual([row["receipt_id"] for row in rows], [receipt_id])
        self.assertEqual(rows[0]["document_digest"], document["digest"])
        for path in self.machine_home.rglob("hitl/*.json"):
            self.assertEqual(core_hitl.digest(path.read_bytes()), document["digest"],
                             "exactly one document, the accepted presentation's own")

    def test_a_confirmation_without_a_digest_is_refused_and_admits_nothing(self):
        """PR 13: the digest is required on the wire, and the same private call PR 12 staged is
        now refused in-domain — a confirmation payload with no digest reaches the shared
        ``submit`` as ``document_digest=None`` (the explicit fetch), which refuses every item
        with ``evidence.document_mismatch`` before anything is admitted, carrying the document
        the presentation named: the presentation row keeps its digest and no confirmation row
        is written. The TypeScript mediator never sends this shape (PR 12); the guard is the
        bridge's own defense against a hand-crafted private call."""
        presented = private_bridge._evidence_presentation(PI_PROFILE, self.raw(), {})
        answer = private_bridge._evidence_confirmation(
            PI_PROFILE, {"operation": "evidence_confirmation", "run_id": self.run_id},
            {"receipt_id": presented["receipt_id"], "items": presented["items"],
             "answers": [{"subject": "research", "id": presented["items"][0]["id"],
                         "choice": "confirm", "note": None}]})
        self.assertEqual(answer["type"], "evidence_receipt")
        self.assertEqual(answer["result"]["type"], "Block")
        self.assertEqual(answer["result"]["reasons"][0]["code"], "evidence.document_mismatch")
        self.assertEqual(self._presentation_rows()[0]["receipt_id"], presented["receipt_id"])
        self.assertTrue(self._presentation_rows()[0]["document_digest"],
                        "the bound presentation keeps its digest")
        self.assertEqual(self._confirmation_rows(), [], "nothing admits without a document")

    def test_the_confirmation_round_trips_the_presentations_digest_to_the_core(self):
        """PR 12: the TypeScript mediator sends the presentation's digest — ``document_digest`` on
        the private confirmation payload — and the bridge forwards it to the shared ``submit``, so
        a Pi receipt, like a Claude one, names the document its own presentation wrote on BOTH
        history rows (the window pr11-report.md left open is closed)."""
        presented = private_bridge._evidence_presentation(PI_PROFILE, self.raw(), {})
        digest = presented["document"]["digest"]
        answer = private_bridge._evidence_confirmation(
            PI_PROFILE, {"operation": "evidence_confirmation", "run_id": self.run_id},
            {"receipt_id": presented["receipt_id"], "items": presented["items"],
             "answers": [{"subject": "research", "id": presented["items"][0]["id"],
                         "choice": "confirm", "note": None}],
             "document_digest": digest})
        self.assertEqual(answer["type"], "evidence_receipt")
        self.assertEqual(answer["result"]["type"], "Allow", answer["result"])
        self.assertEqual(self._presentation_rows()[0]["document_digest"], digest)
        self.assertEqual([row["document_digest"] for row in self._confirmation_rows()], [digest],
                         "the confirmation artifact carries the digest of the written file")
        self.assertEqual([row["receipt_id"] for row in self._confirmation_rows()],
                         [presented["receipt_id"]], "it confirms its own presentation")

    def test_a_terminal_presentation_hands_back_the_residual_count_read_from_the_document(self):
        """PR 12: a terminal batch's answer also carries ``residuals`` — the count of obligations
        ending now would leave unresolved, read out of the written document itself — so the dialog
        names the stakes without opening the file. The count is data read from the record, never
        recomputed by any host."""
        presented = private_bridge._evidence_presentation(PI_PROFILE, self.raw(), {})
        confirmed = private_bridge._evidence_confirmation(
            PI_PROFILE, {"operation": "evidence_confirmation", "run_id": self.run_id},
            {"receipt_id": presented["receipt_id"], "items": presented["items"],
             "answers": [{"subject": "research", "id": presented["items"][0]["id"],
                         "choice": "confirm", "note": None}],
             "document_digest": presented["document"]["digest"]})
        self.assertEqual(confirmed["result"]["type"], "Allow")
        stopping = self.service.dispatch({**PROTOCOL_REQUEST, "command":
                                          {"type": "EvaluateRun", "run_id": self.run_id, "intent": "stop"}})["result"]
        self.assertEqual(stopping["type"], "Block", stopping)
        batch = stopping["run"]["confirmation_batch"]
        self.assertTrue(confirmation.is_terminal(batch), "a stop of a deliberative run asks its consent")
        answer = private_bridge._evidence_presentation(
            PI_PROFILE, self.raw(), {"batch": batch})
        self.assertEqual(answer["type"], "evidence_presentation")
        document = answer["document"]
        written = json.loads(Path(document["path"]).read_bytes())
        self.assertEqual(sorted(document), ["digest", "path", "residuals", "run12", "url"],
                         "a terminal answer names the document and its residual count")
        self.assertEqual(document["residuals"], len(written["terminal"]["residuals"]),
                         "the count is the written document's own, read after the write")
        self.assertGreater(document["residuals"], 0, "the fixture leaves something unresolved")


class PiEnvironmentBridgeTests(unittest.TestCase):
    """The Pi private legs of one environment approval (Empirica 7.2 §5/§6, ADR-0072 write-first):
    the public ``environment`` action admitted in the bridge's own process with its private
    admission beside the response, the admitted revision's document written before its one present
    receipt through the shared environment mediation, and the decision naming the written
    document. Everything runs on an isolated machine home and repository."""

    def setUp(self):
        self.machine_root = Path(tempfile.mkdtemp(prefix="pi-bridge-environment-"))
        self.addCleanup(shutil.rmtree, self.machine_root, ignore_errors=True)
        self.machine_home = self.machine_root / "home"
        self.machine_home.mkdir(parents=True)
        self.repo = self.machine_root / "repo"
        os.makedirs(self.repo, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        self._env = patch.dict(os.environ, {"EMPIRICA_HOME": str(self.machine_home),
                                            "EMPIRICA_REPO_DIR": str(self.repo)})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.service = bridge.build_service(PI_PROFILE)
        self.sequence = 0
        self.run_id = self._dispatch({"type": "StartRun", "control_mode": "deliberative",
                                      "goal": "pi approves its environment",
                                      "invocation": invocation_for(PI_PROFILE),
                                      "selector": {"project": "pi-environment", "session": "governed"}})["run"]["id"]
        self._dispatch({"type": "ObserveAction", "run_id": self.run_id,
                        "action": {"kind": "graph", "payload": copy.deepcopy(GRAPH)}})
        approve_current(self.service._coordinator, self.run_id)
        self._dispatch({"type": "ObserveAction", "run_id": self.run_id,
                        "action": {"kind": "route", "reason": "supplied context"}})
        self._dispatch({"type": "ObserveAction", "run_id": self.run_id, "action": {"kind": "investigate"}})

    def _dispatch(self, command: dict) -> dict:
        self.sequence += 1
        response = self.service.dispatch({**PROTOCOL_REQUEST, "request_id": f"setup-{self.sequence}",
                                          "command": command})
        self.assertEqual(response["result"]["type"], "Allow", response["result"])
        return response["result"]

    def _admit(self, request_id: str = "environment-1") -> dict:
        request = {**PROTOCOL_REQUEST, "request_id": request_id, "command": {
            "type": "ObserveAction", "run_id": self.run_id, "action": copy.deepcopy(ENVIRONMENT_ACTION)}}
        return private_bridge._environment_admit(PI_PROFILE, {"operation": "environment_admit"},
                                                 {"request": request})

    def _row(self) -> dict:
        self._dispatch({"type": "GetRun", "run_id": self.run_id})
        from core.governance import plain
        return plain(self.service._coordinator.last_snapshot.state.environment)

    def _documents(self) -> list[Path]:
        return sorted(self.machine_home.rglob("hitl/*.json"))

    def _present(self, revision: int) -> dict:
        return private_bridge._environment_present(
            PI_PROFILE, {"operation": "environment_present", "run_id": self.run_id}, {"revision": revision})

    def test_the_admission_rides_beside_the_public_response_never_inside_it(self):
        admitted = self._admit()
        self.assertEqual(admitted["type"], "environment_admitted")
        self.assertEqual(admitted["admission"], {"run_id": self.run_id, "revision": self._row()["revision"]})
        self.assertTrue(_proto.validate_public_result(admitted["response"]["result"]),
                        "the response is the public one, carrying no admission")
        self.assertEqual(self._documents(), [], "admitting writes no document")

    def test_the_public_response_is_the_public_bridges_own_for_the_same_input(self):
        """The private leg answers with exactly what the public stdio bridge's ``handle`` (the
        function its ``main`` writes out) returns for the same request; the admission is never in
        it. A state-free invalid request answers byte-identically on both paths."""
        request = {**PROTOCOL_REQUEST, "request_id": "environment-same", "command": {
            "type": "ObserveAction", "run_id": self.run_id, "action": copy.deepcopy(ENVIRONMENT_ACTION)}}
        real, public = bridge.handle, []

        def spy(observed, profile, admission=None):
            public.append((copy.deepcopy(observed), json.dumps(real(observed, profile, admission))))
            return json.loads(public[-1][1])
        with patch.object(bridge, "handle", spy):
            admitted = private_bridge._environment_admit(PI_PROFILE, {"operation": "environment_admit"},
                                                         {"request": copy.deepcopy(request)})
        self.assertEqual(public[0][0], request)
        self.assertEqual(json.dumps(admitted["response"]), public[0][1])
        self.assertNotIn("admission", public[0][1])
        invalid = {**request, "request_id": "environment-invalid", "command": {**request["command"], "action": {
            **copy.deepcopy(ENVIRONMENT_ACTION), "payload": {}}}}
        self.assertEqual(
            json.dumps(private_bridge._environment_admit(PI_PROFILE, {"operation": "environment_admit"},
                                                         {"request": copy.deepcopy(invalid)})["response"]),
            json.dumps(bridge.handle(copy.deepcopy(invalid), PI_PROFILE)))

    def test_every_other_public_action_is_refused_before_dispatch(self):
        for action in (copy.deepcopy(QUALIFY_ACTION), {}, None):
            request = {**PROTOCOL_REQUEST, "request_id": "not-environment", "command": {
                "type": "ObserveAction", "run_id": self.run_id, "action": action}}
            with patch.object(bridge, "handle_admitting") as admitting, self.assertRaises(ValueError):
                private_bridge._environment_admit(PI_PROFILE, {"operation": "environment_admit"},
                                                  {"request": request})
            admitting.assert_not_called()
        for request in ({**PROTOCOL_REQUEST, "command": {"type": "GetRun", "run_id": self.run_id}}, None, []):
            with self.assertRaises(ValueError):
                private_bridge._environment_admit(PI_PROFILE, {"operation": "environment_admit"},
                                                  {"request": request})

    def test_the_document_is_written_before_its_present_and_the_decision_names_it(self):
        revision = self._admit()["admission"]["revision"]
        real = bridge.trusted_environment_decision
        seen = []

        def probe(profile, run_id, payload):
            seen.append((payload["outcome"], [path.name for path in self._documents()]))
            return real(profile, run_id, payload)
        with patch.object(bridge, "trusted_environment_decision", probe):
            shown = self._present(revision)
        self.assertEqual(shown["type"], "environment_presentation", shown)
        (path,) = self._documents()
        data = path.read_bytes()
        self.assertEqual(seen, [("present", [path.name])], "the file exists before the present receipt")
        self.assertEqual(shown["document"], {"digest": core_hitl.digest(data), "path": str(path)})
        written = json.loads(data)
        self.assertEqual(shown["environment"], {name: written[name] for name in shown["environment"]},
                         "every fact the dialog shows is the written document's own")
        self.assertEqual(shown["environment"]["revision"], revision)
        self.assertEqual(shown["response"]["result"]["type"], "Allow")
        self.assertNotIn("presentation", shown["response"]["result"])
        decided = private_bridge._environment_decision(
            PI_PROFILE, {"operation": "environment_decision", "run_id": self.run_id},
            {"run_id": self.run_id, "receipt_id": shown["receipt_id"], "revision": revision,
             "proposal_digest": shown["environment"]["proposal_digest"], "outcome": "approve",
             "document_digest": shown["document"]["digest"]})
        self.assertEqual(decided["result"]["type"], "Allow", decided)
        row = self._row()
        self.assertEqual(row["state"], core_environment.APPROVED)
        self.assertEqual({receipt["document_digest"] for receipt in row["receipts"]}, {shown["document"]["digest"]})

    def test_a_revision_other_than_the_admitted_one_is_refused_before_any_write(self):
        revision = self._admit()["admission"]["revision"]
        refused = self._present(revision + 1)
        self.assertEqual(refused["type"], "environment_refused")
        self.assertEqual(self._documents(), [], "a document of another revision is never written")
        self.assertEqual(self._row()["receipts"], [], "nothing is presented")
        self.assertEqual(self._row()["state"], core_environment.PENDING)


if __name__ == "__main__":
    unittest.main()
