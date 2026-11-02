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
from core import hitl as core_hitl  # noqa: E402
from governance_setup import approve_current, invocation_for  # noqa: E402
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
        self.assertEqual(stat.S_IMODE(os.stat(document["path"]).st_mode), 0o444)
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
                    "approval_kind": "host_ui"}
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
        self.assertEqual(set(document), {"digest", "path"}, "no bytes cross the transport")
        self.assertRegex(document["digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertTrue(document["path"].startswith(str(self.machine_home)))
        data = Path(document["path"]).read_bytes()
        self.assertEqual(core_hitl.digest(data), document["digest"])
        self.assertEqual(stat.S_IMODE(os.stat(document["path"]).st_mode), 0o444)
        # the core recorded the digested presentation, and the file WAS written before the show
        rows = self._presentation_rows()
        self.assertEqual([row["receipt_id"] for row in rows], [receipt_id])
        self.assertEqual(rows[0]["document_digest"], document["digest"])
        for path in self.machine_home.rglob("hitl/*.json"):
            self.assertEqual(core_hitl.digest(path.read_bytes()), document["digest"],
                             "exactly one document, the accepted presentation's own")

    def test_a_legacy_confirmation_without_a_digest_still_fits_the_core_staging(self):
        """The core's staging still admits the legacy confirmation of a bound presentation, so a
        pre-PR-12 mediator keeps working until PR 13 makes ``document_digest`` required: the
        presentation row keeps its digest; a legacy confirmation row records none."""
        presented = private_bridge._evidence_presentation(PI_PROFILE, self.raw(), {})
        answer = private_bridge._evidence_confirmation(
            PI_PROFILE, {"operation": "evidence_confirmation", "run_id": self.run_id},
            {"receipt_id": presented["receipt_id"], "items": presented["items"],
             "answers": [{"subject": "research", "id": presented["items"][0]["id"],
                         "choice": "confirm", "note": None}]})
        self.assertEqual(answer["type"], "evidence_receipt")
        self.assertEqual(answer["result"]["type"], "Allow")
        self.assertEqual(self._presentation_rows()[0]["receipt_id"], presented["receipt_id"])
        self.assertTrue(self._presentation_rows()[0]["document_digest"],
                        "the bound presentation keeps its digest")
        self.assertEqual([row["document_digest"] for row in self._confirmation_rows()], [None])

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
        self.assertEqual(sorted(document), ["digest", "path", "residuals"],
                         "a terminal answer names the document and its residual count")
        self.assertEqual(document["residuals"], len(written["terminal"]["residuals"]),
                         "the count is the written document's own, read after the write")
        self.assertGreater(document["residuals"], 0, "the fixture leaves something unresolved")


if __name__ == "__main__":
    unittest.main()
