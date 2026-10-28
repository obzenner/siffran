"""The pure ``empirica.hitl/1`` document builders (ADR-0072): goldens, fail-closed cases, properties.

The goldens prove the builders against real core output, not hand-made inputs: the configuration
documents from the production ``core.projection.governance_dialog`` (the same construction
``scripts/gen_governance_dialog_golden.py`` uses), the evidence documents from a real
``EvaluationSnapshot`` (assembled exactly as the d7 transaction tests assemble theirs) through the
production ``confirmation_batch``/``terminal_batch`` and ``project_argument`` (the same
rows ``adapters.confirmation.build_items`` reads). Every built document must validate against
``contracts/empirica/v2/hitl.schema.json`` and render to the pinned byte form whose digest is the
document's identity (PR 03). No test writes anything; the fixture files are read-only goldens.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: E402  (tests run from anywhere)

import jsonschema  # noqa: E402
from hypothesis import given, settings, strategies as st  # noqa: E402

from application import protocol as _protocol  # noqa: E402
from application.protocol import projection_controls  # noqa: E402
from core import governance as core_governance  # noqa: E402
from core.canonical import CanonicalJSONError  # noqa: E402
from core import hitl  # noqa: E402
from core.evaluation import (EvaluationSnapshot, artifact, claim_digest,  # noqa: E402
                             confirmation_batch, derive_claims, terminal_batch)
from core.projection import governance_dialog, project_argument  # noqa: E402
from core.run import OperationalState  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "contracts" / "empirica" / "v2" / "hitl-fixtures"
SCHEMA = json.loads((ROOT / "contracts" / "empirica" / "v2" / "hitl.schema.json").read_text("utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)

RUN_ID = "run-fx"
GOAL = "Confirm the exact run configuration"
RATIONALE = ("Sized for the supplied claims — two gating claims, three sources in every layer, "
             "one spike retry.")
PROPOSAL_DIGEST = "sha256:" + "7" * 64
INVOCATION = {"host": "test", "interactive": True, "signal": "operator", "delegation": False,
              "audit_retrieval": ["local", "workspace"], "human_surface": "unavailable"}
CEILING_PRIORS = {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}
BUDGETS = {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}
TERMINAL_RESIDUALS = ({"id": "obligation.audit",
                       "required": "Obtain a current independent audit of every approved gating claim."},)


def _reversed_items(value):
    """The same JSON value with every mapping's pairs in reverse insertion order."""
    if isinstance(value, dict):
        return {k: _reversed_items(v) for k, v in reversed(list(value.items()))}
    if isinstance(value, (list, tuple)):
        return [_reversed_items(v) for v in value]
    return value


def dialog() -> dict:
    """The approve-run-configuration dialog through the production projection
    (``scripts/gen_governance_dialog_golden.py``'s exact construction)."""
    value = {
        "plan_revision": 0, "control_mode": "deliberative", "state": "pending",
        "interactions_remaining": {"proposal": 2, "total": 127},
        "proposal": {"budgets": dict(BUDGETS), "rationale": RATIONALE},
        "budgets": {"passes_used": 0, "spawns_used": 0, "audit_spawns_used": 0},
    }
    return governance_dialog(GOAL, value, projection_controls(), INVOCATION)


def edited_dialog() -> dict:
    """The confirm-edited dialog: the amendment applied the way the golden's confirmation form shows it."""
    value = copy.deepcopy(dialog())
    value["epoch"] = 1
    value["budgets"][0]["value"] = 6
    return value


def approve_document() -> dict:
    return hitl.configuration_document(
        run_id=RUN_ID, dialog=dialog(), proposal_digest=PROPOSAL_DIGEST, governance_state="pending")


def confirm_edited_document() -> dict:
    return hitl.configuration_document(
        run_id=RUN_ID, dialog=edited_dialog(), proposal_digest=PROPOSAL_DIGEST,
        governance_state="revision_pending", priors=CEILING_PRIORS)


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

    def test_digest_is_the_sha256_of_the_given_bytes(self):
        data = hitl.render(approve_document())
        self.assertEqual(hitl.digest(data), "sha256:" + hashlib.sha256(data).hexdigest())
        self.assertEqual(hitl.digest(b""), "sha256:" + hashlib.sha256(b"").hexdigest())
        self.assertEqual(hitl.document_digest(approve_document()), hitl.digest(data))


class ConfigurationGoldenTests(unittest.TestCase):
    """The configuration documents reproduce the PR 03 fixtures byte for byte
    from real core projection output plus the explicit decision inputs."""

    def test_approve_reproduces_the_fixture(self):
        self.assertEqual(hitl.render(approve_document()), fixture_bytes("valid-configuration-approve"))

    def test_confirm_edited_reproduces_the_fixture(self):
        self.assertEqual(hitl.render(confirm_edited_document()),
                         fixture_bytes("valid-configuration-confirm-edited"))

    def test_built_documents_are_schema_valid(self):
        for doc, name in ((approve_document(), "approve"), (confirm_edited_document(), "confirm")):
            assert_schema(doc, f"configuration/{name}")


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


class FailClosedTests(unittest.TestCase):
    """The builders refuse what the forms would never show; the failure is loud, not silent."""

    def test_configuration_state(self):
        with self.assertRaises(ValueError):
            hitl.configuration_document(run_id=RUN_ID, dialog=dialog(),
                                        proposal_digest=PROPOSAL_DIGEST, governance_state="approved")
        with self.assertRaises(ValueError):
            hitl.configuration_document(run_id=RUN_ID, dialog=edited_dialog(),
                                        proposal_digest=PROPOSAL_DIGEST, governance_state="revision_pending")
        with self.assertRaises(ValueError):
            hitl.configuration_document(run_id=RUN_ID, dialog=dialog(),
                                        proposal_digest=PROPOSAL_DIGEST, governance_state="pending",
                                        priors=CEILING_PRIORS)
        with self.assertRaises(ValueError):
            hitl.configuration_document(run_id=RUN_ID, dialog=edited_dialog(),
                                        proposal_digest=PROPOSAL_DIGEST, governance_state="revision_pending",
                                        priors={"max_passes": 8, "max_audit_spawns": 2})

    def test_configuration_dialog_must_carry_its_fields(self):
        for missing in ("epoch", "goal", "rationale", "budgets", "reviews_left", "invocation"):
            broken = copy.deepcopy(dialog())
            del broken[missing]
            with self.assertRaises(ValueError):
                hitl.configuration_document(run_id=RUN_ID, dialog=broken,
                                            proposal_digest=PROPOSAL_DIGEST, governance_state="pending")

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

    def test_evidence_terminal_shape(self):
        snapshot = evidence_snapshot()
        records, claims = _argument_rows(snapshot)
        stale = copy.deepcopy(terminal_batch(snapshot, derive_claims(snapshot), "stopped_residual"))
        stale["terminal"]["status"] = "stopped_nothing"  # not a terminal status
        with self.assertRaises(ValueError):
            hitl.evidence_document(run_id=RUN_ID, batch=stale, records=records, claims=claims,
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

_ALPHABET = " abcXYZ 019 .-—é字"
_TEXT = st.text(alphabet=_ALPHABET, min_size=1, max_size=64).filter(lambda s: s.strip())
_RATIONALE = st.one_of(st.none(),
                      st.text(alphabet=_ALPHABET, min_size=1, max_size=400).filter(lambda s: s.strip()))
_SCORE = st.integers(min_value=0, max_value=64)
_HEX64 = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
_GOVERNANCE_STATE = st.sampled_from(["pending", "revision_pending"])
_PRIORS = st.fixed_dictionaries({"max_passes": _SCORE, "max_spawns": _SCORE,
                                  "max_audit_spawns": _SCORE})

_DIALOG_WITHOUT_BUDGETS = st.fixed_dictionaries({
    "epoch": st.integers(min_value=0, max_value=9),
    "control_mode": st.just("deliberative"),
    "state": st.just("pending"),
    "reviews_left": st.fixed_dictionaries({"proposal": st.integers(0, 40),
                                            "total": st.integers(0, 40)}),
    "goal": _TEXT,
    "rationale": _RATIONALE,
    "invocation": st.fixed_dictionaries({
        "host": _TEXT, "interactive": st.one_of(st.none(), st.booleans()), "signal": _TEXT,
        "delegation": st.booleans(),
        "audit_retrieval": st.lists(st.sampled_from(["local", "web", "workspace"]),
                                    min_size=0, max_size=3, unique=True),
        "human_surface": st.sampled_from(["human", "unavailable", "unobserved"])}),
})


def _budget_rows(order):
    return st.tuples(*[st.builds(
        lambda key, value, used: {
            "key": key, "label": "label " + key, "short": key[:3], "help": "help " + key,
            "value": value, "used": used, "minimum": used, "maximum": 1 + value,
        },
        key=st.just(key), value=_SCORE, used=_SCORE) for key in order])


def dialog_strategy():
    return st.permutations(["max_passes", "max_spawns", "max_audit_spawns"]).flatmap(
        lambda order: st.tuples(_budget_rows(order), _DIALOG_WITHOUT_BUDGETS)
    ).map(lambda packed: {**packed[1], "budgets": list(packed[0])})


class ConfigurationPropertyTests(unittest.TestCase):
    """For generated dialogs, the built document is schema-valid, deterministic and order-independent,
    and its digest follows the decision, not the host's key order."""

    @_PROPERTY
    @given(dialog=dialog_strategy(), state=_GOVERNANCE_STATE, priors=_PRIORS,
           run_id=_TEXT, proposal_digest=_HEX64, shuffled=st.booleans())
    def test_schema_valid_deterministic_order_independent(self, dialog, state, priors, run_id,
                                                          proposal_digest, shuffled):
        document = hitl.configuration_document(
            run_id=run_id, dialog=dialog, proposal_digest="sha256:" + proposal_digest,
            governance_state=state,
            priors=None if state == "pending" else priors)
        assert_schema(document, f"configuration/{state}")
        data = hitl.render(document)
        assert data == json.dumps(json.loads(data.decode("utf-8")), sort_keys=True, indent=2,
                                  ensure_ascii=False).encode("utf-8") + b"\n"
        if shuffled:  # the same values in reverse insertion order render to the same bytes
            twin = hitl.configuration_document(
                run_id=run_id, dialog=_reversed_items(dialog),
                proposal_digest="sha256:" + proposal_digest, governance_state=state,
                priors=None if state == "pending" else _reversed_items(priors))
            assert hitl.render(twin) == data

    @_PROPERTY
    @given(dialog=dialog_strategy(), priors=_PRIORS, mutated=st.sampled_from(
        ["goal", "run_id", "proposal_digest", "plan_revision", "budget", "priors"]))
    def test_digest_follows_the_decision(self, dialog, priors, mutated):
        document = hitl.configuration_document(
            run_id=RUN_ID, dialog=dialog, proposal_digest=PROPOSAL_DIGEST,
            governance_state="revision_pending", priors=priors)
        changed = copy.deepcopy(document)
        if mutated == "goal":
            changed["goal"] = document["goal"] + " changed"
        elif mutated == "run_id":
            changed["run_id"] = "another-run"
        elif mutated == "proposal_digest":
            changed["proposal_digest"] = "sha256:" + "0" * 64
        elif mutated == "plan_revision":
            changed["plan_revision"] += 1
        elif mutated == "budget":
            changed["budgets"][0] = {**changed["budgets"][0],
                                     "value": changed["budgets"][0]["value"] + 1}
        else:
            ceiling = changed["amendment"]["budgets"]["max_passes"]
            changed["amendment"]["budgets"]["max_passes"] = {
                "value": ceiling["value"], "was": ceiling["was"] + 1}
        assert hitl.document_digest(changed) != hitl.document_digest(document)

    @_PROPERTY
    @given(dialog=dialog_strategy(), priors=_PRIORS)
    def test_context_when_shown_is_recorded_not_decided(self, dialog, priors):
        """Changing only ``context_when_shown`` changes the bytes (it is part of the document, so part
        of its digest) while every decided field stays identical — the ADR's separation (the block
        is recorded, never part of the decision)."""
        document = hitl.configuration_document(
            run_id=RUN_ID, dialog=dialog, proposal_digest=PROPOSAL_DIGEST,
            governance_state="revision_pending", priors=priors)
        re_shown = copy.deepcopy(document)
        re_shown["context_when_shown"] = {
            "budgets_used": {ceiling: used + 1 for ceiling, used in
                             re_shown["context_when_shown"]["budgets_used"].items()},
            "reviews_left": {key: value + 1 for key, value in
                             re_shown["context_when_shown"]["reviews_left"].items()},
            "invocation": re_shown["context_when_shown"]["invocation"]}
        decided = {key: value for key, value in document.items() if key != "context_when_shown"}
        re_decided = {key: value for key, value in re_shown.items() if key != "context_when_shown"}
        assert decided == re_decided
        assert hitl.render(re_shown) != hitl.render(document)


def _projected_pair(record_id, key, quote):
    """One asked-about record and its claim: projected rows shaped like ``project_argument``'s."""
    wording = "sha256:" + key
    claim = {"claim_id": "G0", "text": "Claim G0", "wording_digest": wording}
    record = {"artifact_id": record_id, "claim_id": "G0", "claim_digest": wording,
              "kind": "research", "source_kind": "web",
              "source_ref": "https://example.test/source", "citation": quote,
              "verification": {"class": "retrievable", "status": "pending", "retrieve": "web"}}
    return record, claim


class EvidencePropertyTests(unittest.TestCase):
    """Generated evidence documents stay schema-valid and deterministic, and the digest
    still follows the shown content."""

    @_PROPERTY
    @given(run_id=_TEXT, key=_HEX64, record=_HEX64, quote=_TEXT)
    def test_batch_schema_valid_deterministic_and_digest_follows_content(self, run_id, key,
                                                                         record, quote):
        record_id = "sha256:" + record
        record_row, claim_row = _projected_pair(record_id, key, quote)
        batch = {"key": "sha256:" + key, "record_ids": [record_id],
                 "claims": [{"claim_id": "G0", "claim_digest": record_row["claim_digest"]}]}
        arguments = ({"run_id": run_id, "batch": batch, "records": {record_id: record_row},
                      "claims": {"G0": claim_row}},)  # built twice below, identical bytes
        document = hitl.evidence_document(**arguments[0])
        assert_schema(document, "evidence/batch")
        data = hitl.render(document)
        reshuffled = hitl.evidence_document(**{**arguments[0],
                                               "records": {record_id: _reversed_items(record_row)},
                                               "batch": {k: v for k, v in reversed(list(batch.items()))}})
        assert hitl.render(reshuffled) == data
        for altered in ({"records": {record_id: {**record_row, "citation": quote + " changed"}}},
                        {"run_id": "another-run"}):
            variant = hitl.evidence_document(**{**arguments[0], **altered})
            assert hitl.document_digest(variant) != hitl.document_digest(document)

    @_PROPERTY
    @given(key=_HEX64, wording=_HEX64, status=st.sampled_from(
        ["stopped_residual", "stopped_budget", "stopped_frozen"]))
    def test_terminal_document(self, key, wording, status):
        batch = {"key": "sha256:" + key, "record_ids": [],
                 "claims": [{"claim_id": status, "claim_digest": "sha256:" + wording}],
                 "terminal": {"status": status, "digest": "sha256:" + wording}}
        document = hitl.evidence_document(run_id=RUN_ID, batch=batch, records={},
                                          claims={}, residuals=TERMINAL_RESIDUALS)
        assert_schema(document, "evidence/terminal")
        assert document["terminal"]["residuals"][0]["id"] == "obligation.audit"


if __name__ == "__main__":
    unittest.main()
