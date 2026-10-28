"""The pure ``empirica.hitl/1`` document builders (ADR-0072): goldens, fail-closed cases, properties.

The goldens prove the builders against real core output, not hand-made inputs (review-1 finding 12):
the configuration documents from a real ``core.governance`` progression — ``initial`` → ``revise``
of a sized proposal → human decisions through ``resolve_submission`` and ``revise``, exactly the
application's own write path (application/governance.py:57-102) — projected by the production
``project_presentation`` (core/projection.py:365-372), with the real ``proposal_digest``, the real
state word and the priors of the real superseded proposal; the evidence documents from a real
``EvaluationSnapshot`` through the production ``confirmation_batch``/``terminal_batch`` and
``project_argument`` (the same rows ``adapters.confirmation.build_items`` reads). Every built document
must validate against ``contracts/empirica/v2/hitl.schema.json``, render to the pinned byte form
whose digest is the document's identity (PR 03), ask the question the receipts witness
(:func:`core.hitl.question_of`), and escape every free-text value through the core's one escaper.
No test writes anything; the fixture files are read-only goldens.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: E402  (tests run from anywhere)

import jsonschema  # noqa: E402
from hypothesis import given, settings, strategies as st  # noqa: E402

from application import protocol as _protocol  # noqa: E402
from core import governance as core_governance  # noqa: E402
from core import hitl  # noqa: E402
from core.canonical import CanonicalJSONError  # noqa: E402
from core.evaluation import (EvaluationSnapshot, artifact, claim_digest,  # noqa: E402
                             confirmation_batch, derive_claims, terminal_batch, terminal_key)
from core.projection import project_argument, project_presentation, safe_text  # noqa: E402
from core.run import OperationalState  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "contracts" / "empirica" / "v2" / "hitl-fixtures"
SCHEMA = json.loads((ROOT / "contracts" / "empirica" / "v2" / "hitl.schema.json").read_text("utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)

RUN_ID = "run-fx"
GOAL = "Confirm the exact run configuration"
RATIONALE = ("Sized for the supplied claims — two gating claims, three sources in every layer, "
             "one spike retry.")
INVOCATION = {"host": "test", "interactive": True, "signal": "operator", "delegation": False,
              "audit_retrieval": ["local", "workspace"], "human_surface": "unavailable"}
BUDGETS = {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}
AMENDED_BUDGETS = {"max_passes": 6, "max_spawns": 1, "max_audit_spawns": 2}
SIZED = {"budgets": dict(BUDGETS), "rationale": RATIONALE}
TERMINAL_RESIDUALS = ({"id": "obligation.audit",
                       "required": "Obtain a current independent audit of every approved gating claim."},)


def _reversed_items(value):
    """The same JSON value with every mapping's pairs in reverse insertion order."""
    if isinstance(value, dict):
        return {k: _reversed_items(v) for k, v in reversed(list(value.items()))}
    if isinstance(value, (list, tuple)):
        return [_reversed_items(v) for v in value]
    return value


def _used_budgets(ceilings=BUDGETS):
    return {"max_passes": ceilings["max_passes"], "passes_used": 0,
            "max_spawns": ceilings["max_spawns"], "spawns_used": 0,
            "max_audit_spawns": ceilings["max_audit_spawns"], "audit_spawns_used": 0}


# --------------------------------------------------------------------------- #
# The real governance progressions (review-1 finding 12), core functions only,
# exactly the application's own write path (application/governance.py:57-102).
# --------------------------------------------------------------------------- #
def _decide(governed: dict, payload: dict, budgets: dict) -> tuple[dict, dict]:
    """Resolve one human decision through the real policy and write the receipt the application writes.
    The checks the application runs (``configuration_error``, ``revision_error``, ``revise``,
    ``resolve_submission``) are all core; a refusal here means the fixture path is not real."""
    if "submission" in payload:
        resolved, reason = core_governance.resolve_submission(
            governed, payload["submission"], _protocol.governance_decisions())
        assert reason is None, reason
        decision = {**payload, **resolved}
        if "configuration" in decision:
            decision["amendment"] = decision.pop("configuration")
    else:
        decision = dict(payload)
    observing = SimpleNamespace(budgets=dict(budgets), governance=governed)
    outcome = decision["outcome"]
    if outcome == "approve":
        error = core_governance.configuration_error(observing, governed["proposal"])
        assert error is None, error
        governed = {**governed, "state": "approved",
                    "approved_digest": governed["proposal_digest"],
                    "approval_kind": payload["approval_kind"], "first_approval": True}
        budgets = _used_budgets(governed["proposal"]["budgets"])
    elif outcome == "amend" and "amendment" in decision:
        proposed = {**decision["amendment"], "rationale": governed["proposal"]["rationale"]}
        error = core_governance.configuration_error(observing, proposed)
        assert error is None, error
        error = core_governance.revision_error(governed, GOAL, proposed)
        assert error is None, error
        governed = core_governance.revise(GOAL, governed, proposal=proposed)
    elif outcome == "reject":
        governed = {**governed, "state": "rejected"}
    elif outcome != "present":
        raise AssertionError(f"the path builder was given an unknown outcome {outcome!r}")
    governor = core_governance.canonical_digest
    receipt = {"id": payload["receipt_id"], "fingerprint": governor(payload),
              "presentation_fingerprint": governor(payload) if outcome == "present" else None,
              "outcome": outcome, "plan_revision": payload["plan_revision"],
              "proposal_digest": payload["proposal_digest"],
              "approval_kind": payload["approval_kind"]}
    return {**governed, "receipts": [*governed["receipts"], receipt]}, budgets


def _decision(governed: dict, action: str, ceilings: dict, receipt_id: str) -> dict:
    return {"run_id": RUN_ID, "receipt_id": receipt_id, "approval_kind": "host_ui",
            "plan_revision": governed["plan_revision"], "proposal_digest": governed["proposal_digest"],
            "submission": {"action": action, "configuration": {"budgets": dict(ceilings)}}}


def _sized_pending() -> dict:
    """The StartRun placeholder after one authored ``configure_run``: pending, sized, revision one."""
    start = core_governance.initial(GOAL, BUDGETS, "deliberative", INVOCATION, None)
    return core_governance.revise(GOAL, start, proposal=copy.deepcopy(SIZED))


def approve_governance() -> dict:
    """The pre-approval state a human first approves: no receipt yet, one sized proposal."""
    return _sized_pending()


def confirm_edited_governance() -> tuple[dict, dict]:
    """The post-approval amendment: an approved proposal the human re-opened (amend receipt);
    the confirm form asks about revision two with the superseded ceilings as priors."""
    governed = _sized_pending()
    governed, budgets = _decide(governed, _decision(governed, "approve", BUDGETS, "receipt-approve"),
                                _used_budgets())
    superseded = copy.deepcopy(governed["proposal"])
    governed, budgets = _decide(governed, _decision(governed, "edit", AMENDED_BUDGETS, "receipt-amend"),
                                budgets)
    return governed, superseded["budgets"]


def confirm_pre_approval_governance() -> tuple[dict, dict]:
    """The amendment before the first approval (ADR-0072, review-1 finding 2): the state word stays
    ``pending`` while the receipts witness an amendment — why the question, not the state, discriminates."""
    governed = _sized_pending()
    superseded = copy.deepcopy(governed["proposal"])
    governed, _ = _decide(governed, _decision(governed, "edit", AMENDED_BUDGETS, "receipt-amend"),
                          _used_budgets())
    return governed, superseded["budgets"]


def author_revision_governance() -> dict:
    """A post-approval author revision (``configure_run``): ``revision_pending`` with no human amendment —
    the state word alone would guess a confirm form; the receipts know better."""
    governed = _sized_pending()
    governed, budgets = _decide(governed, _decision(governed, "approve", BUDGETS, "receipt-approve"),
                                _used_budgets())
    return core_governance.revise(
        GOAL, governed,
        proposal={"budgets": {**BUDGETS, "max_passes": BUDGETS["max_passes"] + 1},
                  "rationale": RATIONALE})


def rejected_governance() -> dict:
    """A refused decision and the re-present that follows it: state ``rejected``, but the run
    still asks the human to approve the same sized proposal."""
    governed = _sized_pending()
    governed, budgets = _decide(governed, _decision(governed, "reject", BUDGETS, "receipt-reject"),
                                _used_budgets())
    return _decide(governed, {"run_id": RUN_ID, "receipt_id": "receipt-present",
                              "outcome": "present", "approval_kind": "host_ui",
                              "plan_revision": governed["plan_revision"],
                              "proposal_digest": governed["proposal_digest"]}, budgets)[0]


def _config_snapshot(governance: dict) -> EvaluationSnapshot:
    state = OperationalState(
        protocol="empirica/v2", state_schema="empirica.run/3",
        spike_backend={"kind": "spikes_unavailable"}, goal=GOAL, invocation=INVOCATION,
        status="active", budgets=_used_budgets(governance["proposal"]["budgets"]),
        governance=governance, selected_graph_artifact_id=None, frozen_claim_ids=None,
        frozen_semantic_digest=None, route_stamp=0, investigation_stamp=0, stamp_seq=0,
        last_derivation_digest=None, children=(), observation_basis_digest="sha256:" + "0" * 64,
        committed_artifact_head_id=None)
    return EvaluationSnapshot(state=state, history=(), graph=None,
                              contract=_protocol.CONTRACT_VIEW, run_id=RUN_ID,
                              command={"type": "GetRun", "run_id": RUN_ID})


def production_dialog(governance: dict) -> dict:
    """The dialog exactly the way the private governance presentations carry it: the production
    ``project_presentation`` over a real snapshot (core/projection.py:365-372)."""
    return project_presentation(_config_snapshot(governance))["dialog"]


def approve_document() -> dict:
    governed = approve_governance()
    return hitl.configuration_document(
        run_id=RUN_ID, dialog=production_dialog(governed),
        proposal_digest=governed["proposal_digest"], governance=governed)


def confirm_edited_document() -> dict:
    governed, priors = confirm_edited_governance()
    return hitl.configuration_document(
        run_id=RUN_ID, dialog=production_dialog(governed),
        proposal_digest=governed["proposal_digest"], governance=governed, priors=priors)


def confirm_pre_approval_document() -> dict:
    governed, priors = confirm_pre_approval_governance()
    return hitl.configuration_document(
        run_id=RUN_ID, dialog=production_dialog(governed),
        proposal_digest=governed["proposal_digest"], governance=governed, priors=priors)


def author_revision_document() -> dict:
    governed = author_revision_governance()
    return hitl.configuration_document(
        run_id=RUN_ID, dialog=production_dialog(governed),
        proposal_digest=governed["proposal_digest"], governance=governed)


def rejected_document() -> dict:
    governed = rejected_governance()
    return hitl.configuration_document(
        run_id=RUN_ID, dialog=production_dialog(governed),
        proposal_digest=governed["proposal_digest"], governance=governed)


# --------------------------------------------------------------------------- #
# The real evidence snapshot, as PR 04 built it.
# --------------------------------------------------------------------------- #
def _research(claim_id: str, wording: str, payload: dict) -> dict:
    return artifact("research", {
        "claim_id": claim_id, "claim_digest": wording, "statement_digest": "sha256:" + "7" * 64,
        "outcome": "supporting", "route_stamp": 0, "investigation_stamp": 0, "producer": None,
        **payload,
    })


def evidence_snapshot() -> EvaluationSnapshot:
    """A real evaluation snapshot, assembled the way ``test_d7_transactions`` assembles its
    projections: a sized deliberative run, its graph selected, research the human must confirm."""
    claims = [{"id": "G0", "text": "Root claim", "kind": "ordinary", "gating": True},
              {"id": "G1", "text": "The kernel size is positive.", "kind": "needs-decision", "gating": True}]
    wording = {c["id"]: claim_digest(c) for c in claims}
    web = _research("G0", wording["G0"], {
        "source_kind": "web", "source_ref": "https://example.test/source",
        "citation": "The observed source supports G0.",
    })
    runtime = _research("G1", wording["G1"], {
        "source_kind": "runtime", "source_ref": "runtime:kernel-size-observed",
        "citation": "The kernel size observed at runtime must be positive.",
        "spike_unavailable": "A fact of definition; no deterministic check can establish it.",
    })
    state = OperationalState(
        protocol="empirica/v2", state_schema="empirica.run/3",
        spike_backend={"kind": "spikes_unavailable"}, goal=GOAL, invocation=INVOCATION,
        status="active",
        budgets={"max_passes": 8, "passes_used": 0, "max_spawns": 1, "spawns_used": 0,
                 "max_audit_spawns": 2, "audit_spawns_used": 0},
        governance=core_governance.initial(GOAL, BUDGETS, "deliberative", INVOCATION, None),
        selected_graph_artifact_id=None, frozen_claim_ids=None, frozen_semantic_digest=None,
        route_stamp=0, investigation_stamp=0, stamp_seq=0, last_derivation_digest=None, children=(),
        observation_basis_digest="sha256:" + "0" * 64, committed_artifact_head_id=None)
    history = tuple({"artifact_id": a["artifact_id"], **a["body"]} for a in (web, runtime))
    graph = {"root": "G0", "claims": claims,
             "edges": [{"from": "G0", "to": "G1", "type": "SupportedBy"}]}
    return EvaluationSnapshot(state=state, history=history, graph=graph,
                               contract=_protocol.CONTRACT_VIEW, run_id=RUN_ID,
                               command={"type": "GetArgument"})


def _argument_rows(snapshot: EvaluationSnapshot) -> tuple[dict, dict]:
    """The projected argument rows the adapter's ``build_items`` reads, keyed the way it keys them."""
    argument = project_argument(snapshot)
    claims = {row["claim_id"]: row for row in argument["claims"]}
    records = {row["artifact_id"]: row for row in argument["artifacts"] if row.get("kind") == "research"}
    return records, claims


def batch_document() -> dict:
    """The end-of-pass evidence document from the real pending batch of the real snapshot."""
    snapshot = evidence_snapshot()
    records, claims = _argument_rows(snapshot)
    batch = confirmation_batch(snapshot, derive_claims(snapshot))
    assert batch is not None, "the fixture snapshot must have a pending human batch"
    return hitl.evidence_document(run_id=RUN_ID, batch=batch, records=records, claims=claims)


def terminal_document() -> dict:
    """The terminal consent document from the real terminal batch of the real snapshot."""
    snapshot = evidence_snapshot()
    records, claims = _argument_rows(snapshot)
    batch = terminal_batch(snapshot, derive_claims(snapshot), "stopped_residual")
    return hitl.evidence_document(run_id=RUN_ID, batch=batch, records=records, claims=claims,
                                  residuals=TERMINAL_RESIDUALS)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def assert_schema(doc: dict, where: str) -> None:
    errors = list(VALIDATOR.iter_errors(doc))
    assert not errors, f"{where} is not schema-valid: " + "; ".join(str(e) for e in errors)


class CanonicalFormTests(unittest.TestCase):
    """``render`` is the one pinned byte path; ``digest`` is the document identity (PR 03, ADR-0072)."""

    def test_render_is_the_pinned_pretty_form(self):
        self.assertEqual(
            hitl.render(approve_document()),
            json.dumps(approve_document(), sort_keys=True, indent=2, ensure_ascii=False).encode()
            + b"\n")

    def test_render_rejects_what_canonical_json_rejects(self):
        with self.assertRaises(CanonicalJSONError):
            hitl.render({"x": float("nan")})
        with self.assertRaises(CanonicalJSONError):
            hitl.render({"x": {"set"}})
        with self.assertRaises(CanonicalJSONError):
            hitl.render({1: "non-string keys are refused"})

    def test_render_maps_an_unencodable_value_to_canonical_json_error(self):
        # A lone surrogate serializes (ensure_ascii=False) but never encodes as UTF-8:
        # the refusal is the canonical owner's, not a stray UnicodeEncodeError (review-1 finding 10).
        with self.assertRaises(CanonicalJSONError):
            hitl.render({"x": "\ud800"})

    def test_digest_is_the_sha256_of_the_given_bytes(self):
        data = hitl.render(approve_document())
        self.assertEqual(hitl.digest(data), "sha256:" + hashlib.sha256(data).hexdigest())
        self.assertEqual(hitl.digest(b""), "sha256:" + hashlib.sha256(b"").hexdigest())
        self.assertEqual(hitl.document_digest(approve_document()), hitl.digest(data))


class ConfigurationGoldenTests(unittest.TestCase):
    """The configuration goldens come from a real ``core.governance`` progression and the production
    dialog, and reproduce the PR 03 fixtures byte for byte with the real digests (review-1 finding 12)."""

    def test_approve_reproduces_the_fixture(self):
        self.assertEqual(hitl.render(approve_document()), fixture_bytes("valid-configuration-approve"))

    def test_confirm_edited_reproduces_the_fixture(self):
        self.assertEqual(hitl.render(confirm_edited_document()),
                         fixture_bytes("valid-configuration-confirm-edited"))

    def test_confirm_pre_approval_reproduces_the_fixture(self):
        self.assertEqual(hitl.render(confirm_pre_approval_document()),
                         fixture_bytes("valid-configuration-confirm-pre-approval"))

    def test_rejected_reproduces_the_fixture(self):
        self.assertEqual(hitl.render(rejected_document()),
                         fixture_bytes("valid-configuration-rejected"))

    def test_built_documents_are_schema_valid(self):
        for doc, name in ((approve_document(), "approve"),
                          (confirm_edited_document(), "confirm-edited"),
                          (confirm_pre_approval_document(), "confirm-pre-approval"),
                          (author_revision_document(), "author-revision"),
                          (rejected_document(), "rejected")):
            assert_schema(doc, f"configuration/{name}")

    def test_the_goldens_bind_the_real_governance_facts(self):
        governed = approve_governance()
        document = approve_document()
        self.assertEqual(document["proposal_digest"], governed["proposal_digest"])
        self.assertEqual(document["plan_revision"], governed["plan_revision"])
        self.assertEqual(document["governance_state"], governed["state"])
        self.assertEqual(document["question"], hitl.question_of(governed))
        for path, priors in ((confirm_edited_document(), confirm_edited_governance()[1]),
                             (confirm_pre_approval_document(), confirm_pre_approval_governance()[1])):
            self.assertEqual(path["question"], "confirm_amendment")
            self.assertEqual(path["amendment"]["budgets"]["max_passes"],
                             {"value": AMENDED_BUDGETS["max_passes"], "was": priors["max_passes"]})
        self.assertEqual(author_revision_document()["question"], "approve")
        self.assertNotIn("amendment", author_revision_document())
        self.assertEqual(rejected_document()["governance_state"], "rejected")
        self.assertEqual(rejected_document()["question"], "approve")


class EvidenceGoldenTests(unittest.TestCase):
    """The evidence documents come from a real snapshot through the real batch
    builders and the real argument projection — not hand-made rows."""

    def test_batch_reproduces_the_fixture(self):
        self.assertEqual(hitl.render(batch_document()), fixture_bytes("valid-evidence-batch"))

    def test_terminal_reproduces_the_fixture(self):
        self.assertEqual(hitl.render(terminal_document()), fixture_bytes("valid-evidence-terminal"))

    def test_built_documents_are_schema_valid(self):
        assert_schema(batch_document(), "evidence/batch")
        assert_schema(terminal_document(), "evidence/terminal")

    def test_records_answer_their_claims_with_text_and_outcome(self):
        """A record carries the claim text it answers (bound by its digest) and its own outcome —
        the content a human needs before judging a quote (review-1 finding 3)."""
        document = batch_document()
        records, claims = _argument_rows(evidence_snapshot())
        for row in document["records"]:
            source = records[row["record_id"]]
            claim = claims[row["claim_id"]]
            self.assertEqual(row["claim_text"], safe_text(claim["text"]))
            self.assertEqual(row["outcome"], source["outcome"])
            self.assertEqual(row["claim_digest"], source["claim_digest"])

    def test_hostile_text_never_reaches_the_bytes(self):
        """Bidi overrides, zero-width characters and backslashes in a citation or claim text are
        escaped by the core's one escaper — the document a human opens cannot re-order their
        reading (review-1 finding 5)."""
        hostile = "quote \u202e reversed \u200b hidden \\ slashed"
        record_id = "sha256:" + "3" * 64
        claim = {"claim_id": "G0", "text": hostile, "wording_digest": claim_digest({"id": "G0", "text": hostile})}
        record = {"artifact_id": record_id, "claim_id": "G0",
                  "claim_digest": claim["wording_digest"], "kind": "research",
                  "outcome": "supporting", "source_kind": "web",
                  "source_ref": "https://example.test/source", "citation": hostile,
                  "verification": {"class": "retrievable"}}
        batch = {"key": "sha256:" + "5" * 64, "record_ids": [record_id],
                 "claims": [{"claim_id": "G0", "claim_digest": claim["wording_digest"]}]}
        document = hitl.evidence_document(run_id=RUN_ID, batch=batch,
                                          records={record_id: record}, claims={"G0": claim})
        self.assertEqual(document["records"][0]["citation"], safe_text(hostile))
        self.assertEqual(document["claims"][0]["text"], safe_text(hostile))
        data = hitl.render(document)
        for raw in ("\u202e", "\u200b"):
            self.assertNotIn(raw.encode("utf-8"), data)
        self.assertIn("\\u202e", data.decode("utf-8"))  # the escaper's textual form, never the character


class QuestionTests(unittest.TestCase):
    """``question_of`` is the ADR's discriminator: the receipts witness an amendment, the state word
    cannot (review-1 finding 2)."""

    def test_a_pre_approval_amendment_confirms(self):
        governed, _ = confirm_pre_approval_governance()
        self.assertEqual(hitl.question_of(governed), "confirm_amendment")
        document = confirm_pre_approval_document()
        self.assertEqual(document["question"], "confirm_amendment")
        self.assertEqual(document["governance_state"], "pending")  # the state word alone stays pending

    def test_an_author_revision_after_approval_approves(self):
        governed = author_revision_governance()
        self.assertEqual(governed["state"], "revision_pending")
        self.assertEqual(hitl.question_of(governed), "approve")
        self.assertEqual(author_revision_document()["question"], "approve")

    def test_a_refused_decision_re_presents_an_approve_document(self):
        governed = rejected_governance()
        self.assertEqual(governed["state"], "rejected")
        self.assertEqual(hitl.question_of(governed), "approve")
        self.assertEqual(rejected_document()["question"], "approve")

    def test_an_amender_receipt_off_by_one_revision_does_not_confirm(self):
        governed, _ = confirm_edited_governance()
        self.assertEqual(hitl.question_of(governed), "confirm_amendment")
        drifted = copy.deepcopy(governed)
        drifted["receipts"][-1]["plan_revision"] = governed["plan_revision"] - 2
        self.assertEqual(hitl.question_of(drifted), "approve")


class FailClosedTests(unittest.TestCase):
    """The builders refuse what the forms would never show; the failure is loud, not silent."""

    def test_configuration_state(self):
        governed = approve_governance()
        drifted = copy.deepcopy(governed)
        drifted["state"] = "approved"
        with self.assertRaises(ValueError):
            hitl.configuration_document(run_id=RUN_ID, dialog=production_dialog(drifted),
                                       proposal_digest=drifted["proposal_digest"], governance=drifted)

    def test_the_dialog_must_agree_with_the_governance(self):
        governed = approve_governance()
        dialog = production_dialog(governed)
        for key, value in (("state", "rejected"), ("epoch", dialog["epoch"] + 1),
                           ("control_mode", "auto")):
            broken = copy.deepcopy(dialog)
            broken[key] = value
            with self.assertRaises(ValueError):
                hitl.configuration_document(run_id=RUN_ID, dialog=broken,
                                           proposal_digest=governed["proposal_digest"],
                                           governance=governed)
        with self.assertRaises(ValueError):
            hitl.configuration_document(run_id=RUN_ID, dialog=dialog,
                                        proposal_digest="sha256:" + "c" * 64, governance=governed)

    def test_an_explicit_question_must_agree_with_the_receipts(self):
        governed, _ = confirm_edited_governance()
        with self.assertRaises(ValueError):
            hitl.configuration_document(run_id=RUN_ID, dialog=production_dialog(governed),
                                        proposal_digest=governed["proposal_digest"],
                                        governance=governed, priors=copy.deepcopy(BUDGETS),
                                        question="approve")

    def test_priors_follow_the_question_not_the_state(self):
        governed = approve_governance()
        with self.assertRaises(ValueError):
            hitl.configuration_document(run_id=RUN_ID, dialog=production_dialog(governed),
                                        proposal_digest=governed["proposal_digest"],
                                        governance=governed, priors=copy.deepcopy(BUDGETS))
        confirmed, _ = confirm_edited_governance()
        with self.assertRaises(ValueError):
            hitl.configuration_document(run_id=RUN_ID, dialog=production_dialog(confirmed),
                                        proposal_digest=confirmed["proposal_digest"],
                                        governance=confirmed)
        with self.assertRaises(ValueError):
            hitl.configuration_document(run_id=RUN_ID, dialog=production_dialog(confirmed),
                                        proposal_digest=confirmed["proposal_digest"],
                                        governance=confirmed,
                                        priors={"max_passes": 8, "max_audit_spawns": 2})

    def test_numbers_are_exact_integers(self):
        governed = approve_governance()
        for mutate in (lambda d: d.__setitem__("epoch", True),
                       lambda d: d["budgets"][0].__setitem__("value", 8.0),
                       lambda d: d["budgets"][1].__setitem__("used", "0")):
            broken = copy.deepcopy(production_dialog(governed))
            mutate(broken)
            with self.assertRaises(ValueError):
                hitl.configuration_document(run_id=RUN_ID, dialog=broken,
                                            proposal_digest=governed["proposal_digest"],
                                            governance=governed)
        confirmed, priors = confirm_edited_governance()
        drifted = dict(copy.deepcopy(priors), max_spawns=True)
        dialog = production_dialog(confirmed)
        with self.assertRaises(ValueError):
            hitl.configuration_document(run_id=RUN_ID, dialog=dialog,
                                        proposal_digest=confirmed["proposal_digest"],
                                        governance=confirmed, priors=drifted)

    def test_configuration_dialog_must_carry_its_fields(self):
        governed = approve_governance()
        for missing in ("epoch", "goal", "rationale", "budgets", "state", "control_mode",
                        "invocation"):
            broken = copy.deepcopy(production_dialog(governed))
            del broken[missing]
            with self.assertRaises(ValueError):
                hitl.configuration_document(run_id=RUN_ID, dialog=broken,
                                            proposal_digest=governed["proposal_digest"],
                                            governance=governed)

    def test_evidence_ids_must_be_named_by_the_batch(self):
        snapshot = evidence_snapshot()
        records, claims = _argument_rows(snapshot)
        batch = confirmation_batch(snapshot, derive_claims(snapshot))
        with self.assertRaises(ValueError):
            hitl.evidence_document(run_id=RUN_ID, batch=batch, records={}, claims=claims)
        with self.assertRaises(ValueError):
            hitl.evidence_document(run_id=RUN_ID, batch=batch, records=records, claims={})
        altered = copy.deepcopy(batch)
        altered["claims"][0]["claim_digest"] = "sha256:" + "c" * 64  # rows and batch no longer agree
        with self.assertRaises(ValueError):
            hitl.evidence_document(run_id=RUN_ID, batch=altered, records=records, claims=claims)

    def test_a_record_must_answer_the_claim_it_is_bound_to(self):
        snapshot = evidence_snapshot()
        records, claims = _argument_rows(snapshot)
        batch = confirmation_batch(snapshot, derive_claims(snapshot))
        record_id = batch["record_ids"][0]
        claim_id = records[record_id]["claim_id"]
        reworded = claim_digest({"id": claim_id, "text": "A different wording entirely."})
        # the claim and the batch agree on a newer wording: the record's binding is stale
        drifted = copy.deepcopy(claims)
        drifted[claim_id] = {**drifted[claim_id], "text": "A different wording entirely.",
                             "wording_digest": reworded}
        realigned = copy.deepcopy(batch)
        for asked in realigned["claims"]:
            if asked["claim_id"] == claim_id:
                asked["claim_digest"] = reworded
        with self.assertRaises(ValueError):
            hitl.evidence_document(run_id=RUN_ID, batch=realigned, records=records, claims=drifted)
        stolen = copy.deepcopy(records)
        stolen[record_id] = {**stolen[record_id], "claim_digest": "sha256:" + "d" * 64}
        with self.assertRaises(ValueError):
            hitl.evidence_document(run_id=RUN_ID, batch=batch, records=stolen, claims=claims)
        orphaned = copy.deepcopy(records)
        orphaned[record_id] = {**orphaned[record_id], "claim_id": "G-missing"}
        with self.assertRaises(ValueError):
            hitl.evidence_document(run_id=RUN_ID, batch=batch, records=orphaned, claims=claims)

    def test_a_record_needs_its_own_outcome_and_verification_class(self):
        snapshot = evidence_snapshot()
        records, claims = _argument_rows(snapshot)
        batch = confirmation_batch(snapshot, derive_claims(snapshot))
        record_id = batch["record_ids"][0]
        silently = copy.deepcopy(records)
        silently[record_id] = {k: v for k, v in silently[record_id].items() if k != "verification"}
        with self.assertRaises(ValueError):  # no fallback: the dossier owns the class (finding 9)
            hitl.evidence_document(run_id=RUN_ID, batch=batch, records=silently, claims=claims)
        drifted = copy.deepcopy(records)
        drifted[record_id] = {**drifted[record_id], "verification": {"class": ""}}
        with self.assertRaises(ValueError):
            hitl.evidence_document(run_id=RUN_ID, batch=batch, records=drifted, claims=claims)
        for outcome in (None, "maybe", True):
            drifted = copy.deepcopy(records)
            drifted[record_id] = {**drifted[record_id], "outcome": outcome}
            with self.assertRaises(ValueError):
                hitl.evidence_document(run_id=RUN_ID, batch=batch, records=drifted, claims=claims)

    def test_evidence_terminal_shape(self):
        snapshot = evidence_snapshot()
        records, claims = _argument_rows(snapshot)
        stale = copy.deepcopy(terminal_batch(snapshot, derive_claims(snapshot), "stopped_residual"))
        stale["terminal"]["status"] = "stopped_nothing"  # not a terminal status
        with self.assertRaises(ValueError):
            hitl.evidence_document(run_id=RUN_ID, batch=stale, records=records, claims=claims,
                                   residuals=TERMINAL_RESIDUALS)
        unbound = copy.deepcopy(terminal_batch(snapshot, derive_claims(snapshot), "stopped_residual"))
        unbound["key"] = terminal_key("stopped_residual", unbound["terminal"]["digest"])[:-1] + "0"
        with self.assertRaises(ValueError):  # the key must be the core's own (finding 8)
            hitl.evidence_document(run_id=RUN_ID, batch=unbound, records=records, claims=claims,
                                   residuals=TERMINAL_RESIDUALS)
        gutted = copy.deepcopy(terminal_batch(snapshot, derive_claims(snapshot), "stopped_residual"))
        del gutted["terminal"]["digest"]
        with self.assertRaises(ValueError):
            hitl.evidence_document(run_id=RUN_ID, batch=gutted, records=records, claims=claims,
                                   residuals=TERMINAL_RESIDUALS)
        with self.assertRaises(ValueError):
            hitl.evidence_document(run_id=RUN_ID,
                                   batch=terminal_batch(snapshot, derive_claims(snapshot),
                                                        "stopped_residual"),
                                   records=records, claims=claims, residuals=({"id": "x"},))

    def test_evidence_batch_must_be_shaped(self):
        snapshot = evidence_snapshot()
        records, claims = _argument_rows(snapshot)
        batch = confirmation_batch(snapshot, derive_claims(snapshot))
        for broken in ({"key": "sha256:" + "0" * 64, "record_ids": batch["record_ids"]},  # no claims
                       {"key": "not-a-digest", "record_ids": batch["record_ids"], "claims": batch["claims"]}):
            with self.assertRaises(ValueError):
                hitl.evidence_document(run_id=RUN_ID, batch=broken, records=records, claims=claims)
        record_id = batch["record_ids"][0]
        gutted = copy.deepcopy(records)
        gutted[record_id] = {k: v for k, v in gutted[record_id].items() if k != "citation"}
        with self.assertRaises(ValueError):
            hitl.evidence_document(run_id=RUN_ID, batch=batch, records=gutted, claims=claims)


_PROPERTY = settings(max_examples=25, deadline=None, database=None, derandomize=True)
#: A sentinel meaning "the caller did not touch the priors" (None itself is a live value).
_MISSING = object()

_ALPHABET = " abcXYZ 019 .-—é字"
_TEXT = st.text(alphabet=_ALPHABET, min_size=1, max_size=64).filter(lambda s: s.strip())
_RATIONALE = st.one_of(st.none(),
                      st.text(alphabet=_ALPHABET, min_size=1, max_size=400).filter(lambda s: s.strip()))
_SCORE = st.integers(min_value=0, max_value=64)
_HEX64 = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
_RECEIPT_OUTCOMES = st.sampled_from(["present", "dismiss", "reject", "approve"])
_STATE = st.sampled_from(["pending", "revision_pending", "rejected"])


def _budget_rows(order):
    return st.tuples(*[st.builds(
        lambda key, value, used: {
            "key": key, "label": "label " + key, "short": key[:3], "help": "help " + key,
            "value": value, "used": used, "minimum": used, "maximum": 1 + value,
        },
        key=st.just(key), value=_SCORE, used=_SCORE) for key in order])


@st.composite
def _dialog(draw, plan_revision, state):
    order = draw(st.permutations(["max_passes", "max_spawns", "max_audit_spawns"]))
    invocation = draw(st.fixed_dictionaries({
        "host": _TEXT, "interactive": st.one_of(st.none(), st.booleans()), "signal": _TEXT,
        "delegation": st.booleans(),
        "audit_retrieval": st.lists(st.sampled_from(["local", "web", "workspace"]),
                                    min_size=0, max_size=3, unique=True),
        "human_surface": st.sampled_from(["human", "unavailable", "unobserved"])}))
    return {**draw(st.fixed_dictionaries({
        "goal": _TEXT, "rationale": _RATIONALE,
        "reviews_left": st.fixed_dictionaries({"proposal": st.integers(0, 40),
                                                "total": st.integers(0, 40)}),
        "control_mode": st.just("deliberative"),
    })), "epoch": plan_revision, "state": state, "invocation": invocation,
        "budgets": list(draw(_budget_rows(order)))}


@st.composite
def _governed_case(draw, amend_receipt: bool):
    """A governance mapping and its consistent production dialog: a plan revision, a state word,
    receipts (one amend at plan-1 when ``amend_receipt``), and everything the builder reads."""
    plan_revision = draw(st.integers(min_value=2, max_value=9))
    state = draw(_STATE)
    receipts = []
    for _ in range(draw(st.integers(min_value=0, max_value=4))):
        outcome = draw(_RECEIPT_OUTCOMES)
        receipts.append({"outcome": outcome,
                         "plan_revision": draw(st.integers(min_value=1, max_value=plan_revision - 1))})
    if amend_receipt:
        receipts.append({"outcome": "amend", "plan_revision": plan_revision - 1})
    governance = {"state": state, "plan_revision": plan_revision,
                  "control_mode": "deliberative",
                  "proposal_digest": "sha256:" + draw(_HEX64), "receipts": receipts}
    return governance, draw(_dialog(plan_revision, state))


def _priors_for(governance):
    return (copy.deepcopy(BUDGETS) if hitl.question_of(governance) == "confirm_amendment" else None)


class ConfigurationPropertyTests(unittest.TestCase):
    """For generated governance and dialogs, the built document is schema-valid, deterministic and
    order-independent, its digest follows its inputs, and no volatile dialog fact can reach it."""

    def _build(self, governance, dialog, priors2=None):
        return hitl.configuration_document(
            run_id=RUN_ID, dialog=dialog, proposal_digest=governance["proposal_digest"],
            governance=governance, priors=self._priors(governance) if priors2 is None else priors2)

    @staticmethod
    def _priors(governance):
        return copy.deepcopy(BUDGETS) if hitl.question_of(governance) == "confirm_amendment" else None

    @staticmethod
    def _bump_revision(governance):
        """One plan revision later, the amender receipt following it (the question may not silently flip)."""
        return {**governance, "plan_revision": governance["plan_revision"] + 1,
                "receipts": [{**receipt, "plan_revision": receipt["plan_revision"] + 1}
                             if receipt["outcome"] == "amend"
                             and receipt["plan_revision"] + 1 == governance["plan_revision"]
                             else receipt for receipt in governance["receipts"]]}

    @_PROPERTY
    @given(case=_governed_case(amend_receipt=False), run_id=_TEXT, shuffled=st.booleans())
    def test_schema_valid_deterministic_order_independent(self, case, run_id, shuffled):
        governance, dialog = case
        document = hitl.configuration_document(
            run_id=run_id, dialog=dialog, proposal_digest=governance["proposal_digest"],
            governance=governance, priors=None)  # no amend receipt: an approve document
        assert document["question"] == "approve"
        assert_schema(document, f"configuration/{governance['state']}")
        data = hitl.render(document)
        assert data == json.dumps(json.loads(data.decode("utf-8")), sort_keys=True, indent=2,
                                  ensure_ascii=False).encode("utf-8") + b"\n"
        if shuffled:  # the same values in reverse insertion order render to the same bytes
            twin = hitl.configuration_document(
                run_id=run_id, dialog=_reversed_items(dialog),
                proposal_digest=governance["proposal_digest"], governance=_reversed_items(governance))
            assert hitl.render(twin) == data

    @_PROPERTY
    @given(case=_governed_case(amend_receipt=True) | _governed_case(amend_receipt=False))
    def test_digest_follows_the_decision_inputs(self, case):
        """The digest follows the builder's INPUTS (review-1 finding 11): mutate one dialog or
        governed fact or the run, and the identity changes — a builder that quietly drops a field
        cannot hide behind the unchanged parts."""
        governance, dialog = case
        priors = _priors_for(governance)
        document = self._build(governance, dialog)

        def build(governance2=None, dialog2=None, priors2=_MISSING, run_id2=RUN_ID):
            return hitl.configuration_document(
                run_id=run_id2,
                dialog=dialog if dialog2 is None else dialog2,
                proposal_digest=(governance if governance2 is None else governance2)["proposal_digest"],
                governance=governance if governance2 is None else governance2,
                priors=(priors if priors2 is _MISSING else priors2))

        variants = [
            ("a changed dialog goal",
             build(dialog2={**dialog, "goal": dialog["goal"] + " changed"})),
            ("a changed budget value",
             build(dialog2={**dialog, "budgets": [{**dialog["budgets"][0],
                  "value": dialog["budgets"][0]["value"] + 1}, *dialog["budgets"][1:]]})),
            ("a changed budget used",
             build(dialog2={**dialog, "budgets": [{**dialog["budgets"][0],
                  "used": dialog["budgets"][0]["used"] + 1}, *dialog["budgets"][1:]]})),
            ("a changed rationale",
             build(dialog2={**dialog, "rationale": None if dialog["rationale"] else "changed"})),
            ("a changed invocation fact (a run-fixed one)",
             build(dialog2={**dialog, "invocation": {**dialog["invocation"],
                  "signal": dialog["invocation"]["signal"] + " changed"}})),
            ("another run id", build(run_id2="another-run")),
            ("a changed proposal digest",
             build(governance2={**governance, "proposal_digest": "sha256:" + "e" * 64})),
            ("a later plan revision",
             build(governance2=self._bump_revision(governance),
                  dialog2={**dialog, "epoch": dialog["epoch"] + 1})),
            ("another control mode",
             build(governance2={**governance, "control_mode": "auto"},
                  dialog2={**dialog, "control_mode": "auto"})),
        ]
        for label, document2 in variants:
            assert hitl.document_digest(document2) != hitl.document_digest(document), label

    @_PROPERTY
    @given(case=_governed_case(amend_receipt=True) | _governed_case(amend_receipt=False))
    def test_priors_move_the_digest(self, case):
        """Only a confirm document carries priors, so the priors and the amendment always reach the
        bytes together."""
        governance, dialog = case
        if hitl.question_of(governance) == "approve":
            return  # an approve document has no priors to mutate (the builder refuses them)
        document = self._build(governance, dialog)
        lifted = {**copy.deepcopy(BUDGETS), "max_spawns": BUDGETS["max_spawns"] + 1}
        variant = hitl.configuration_document(
            run_id=RUN_ID, dialog=dialog, proposal_digest=governance["proposal_digest"],
            governance=governance, priors=lifted)
        assert hitl.document_digest(variant) != hitl.document_digest(document)

    @_PROPERTY
    @given(case=_governed_case(amend_receipt=False))
    def test_volatile_dialog_facts_cannot_reach_the_document(self, case):
        """Review-1 finding 1: reviews left and the human surface change under the human's own answer
        (a present receipt, a host observation), so no change to them may reach the document or its
        digest — ``context_when_shown`` carries only the run-fixed invocation facts."""
        governance, dialog = case
        document = self._build(governance, dialog)
        assert set(document["context_when_shown"]) == {"invocation"}
        assert set(document["context_when_shown"]["invocation"]) == {
            "host", "interactive", "signal", "delegation", "audit_retrieval"}
        livelier = copy.deepcopy(dialog)
        livelier["reviews_left"] = {key: value + 1 for key, value in livelier["reviews_left"].items()}
        livelier["invocation"]["human_surface"] = "human"
        spent_out = copy.deepcopy(dialog)
        spent_out["reviews_left"] = {key: 0 for key in spent_out["reviews_left"]}
        spent_out["invocation"]["human_surface"] = "unobserved"
        for other in (livelier, spent_out):
            twin = self._build(governance, other)
            assert hitl.render(twin) == hitl.render(document)
        shown = hitl.render(document).decode("utf-8")
        assert "human_surface" not in shown and "reviews_left" not in shown \
            and "budgets_used" not in shown and "PROPOSAL_INTERACTIONS" not in shown


def _projected_pair(record_id, key, quote):
    """One asked-about record and its claim: projected rows shaped like ``project_argument``'s."""
    wording = "sha256:" + key
    claim = {"claim_id": "G0", "text": "Claim G0", "wording_digest": wording}
    record = {"artifact_id": record_id, "claim_id": "G0", "claim_digest": wording,
              "kind": "research", "outcome": "supporting", "source_kind": "web",
              "source_ref": "https://example.test/source", "citation": quote,
              "verification": {"class": "retrievable", "status": "pending", "retrieve": "web"}}
    return record, claim


class EvidencePropertyTests(unittest.TestCase):
    """Generated evidence documents stay schema-valid and deterministic, and the digest
    still follows the shown content, a record's outcome included."""

    @_PROPERTY
    @given(run_id=_TEXT, key=_HEX64, record=_HEX64, quote=_TEXT)
    def test_batch_schema_valid_deterministic_and_digest_follows_content(self, run_id, key,
                                                                         record, quote):
        record_id = "sha256:" + record
        record_row, claim_row = _projected_pair(record_id, key, quote)
        batch = {"key": "sha256:" + key, "record_ids": [record_id],
                 "claims": [{"claim_id": "G0", "claim_digest": record_row["claim_digest"]}]}
        arguments = {"run_id": run_id, "batch": batch, "records": {record_id: record_row},
                     "claims": {"G0": claim_row}}
        document = hitl.evidence_document(**arguments)
        assert_schema(document, "evidence/batch")
        data = hitl.render(document)
        reshuffled = hitl.evidence_document(**{**arguments,
                                               "records": {record_id: _reversed_items(record_row)},
                                               "batch": {k: v for k, v in reversed(list(batch.items()))}})
        assert hitl.render(reshuffled) == data
        reworded = claim_digest({"id": "G0", "text": claim_row["text"] + " changed"})
        for altered in ({"records": {record_id: {**record_row, "citation": quote + " changed"}}},
                        {"records": {record_id: {**record_row, "outcome": "refuting"}}},
                        {"records": {record_id: {**record_row, "claim_digest": reworded}},
                         "claims": {"G0": {**claim_row, "text": claim_row["text"] + " changed",
                                       "wording_digest": reworded}},
                         "batch": {"key": batch["key"], "record_ids": [record_id],
                                   "claims": [{"claim_id": "G0", "claim_digest": reworded}]}},
                        {"run_id": "another-run"}):
            variant = hitl.evidence_document(**{**arguments, **altered})
            assert hitl.document_digest(variant) != hitl.document_digest(document)

    @_PROPERTY
    @given(key=_HEX64, wording=_HEX64, status=st.sampled_from(
        ["stopped_residual", "stopped_budget", "stopped_frozen"]))
    def test_terminal_document(self, key, wording, status):
        batch = {"key": terminal_key(status, "sha256:" + key),
                 "record_ids": [],
                 "claims": [{"claim_id": status, "claim_digest": "sha256:" + wording}],
                 "terminal": {"status": status, "digest": "sha256:" + key}}
        document = hitl.evidence_document(run_id=RUN_ID, batch=batch, records={},
                                          claims={}, residuals=TERMINAL_RESIDUALS)
        assert_schema(document, "evidence/terminal")
        assert document["terminal"]["residuals"][0]["id"] == "obligation.audit"


if __name__ == "__main__":
    unittest.main()
