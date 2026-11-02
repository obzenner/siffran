"""The pure ``empirica.hitl/1`` document builders (ADR-0072).

Each human decision of the forms workstream binds to one immutable core-owned JSON document
(``doc/adr/0072-bind-human-decisions-to-a-core-owned-immutable-hitl-document.md``): the native
forms shrink to the decision plus its facts, and everything else — the whole sizing case for a
configuration, every quote, class, outcome and why-no-spike reason for evidence, the terminal consent's
residue — moves into a document rendered here and digested by its own bytes. ``hitl.schema.json``
(``contracts/empirica/v2/hitl.schema.json``, PR 03, review-1 corrected) is the closed shape; the byte
form below is the one its contract validator pins; the digest is what the decision payloads and the
write-once sidecar will record (PRs 06–10).

Review-1 decisions this module implements (ADR-0072, decided sub-decisions Q1–Q4):

- **Nothing volatile is digested**: ``context_when_shown`` carries only the run-fixed invocation
  facts (host, signal, interactive, delegation, audit_retrieval — recorded at StartRun and never
  rewritten). A present receipt changes the reviews left (``application/governance.py:96-101``,
  counted at ``core/governance.py:168-177``) and the host's answer rewrites ``human_surface``
  (``application/transaction.py:417-419``), so neither can be inside the document or the core's
  recompute at the decision would refuse every one.
- **The question is the form discriminator** (:func:`question_of`), never the adapter's and never the
  state word alone: ``confirm_amendment`` iff some receipt has ``outcome == "amend"`` and its
  ``plan_revision + 1`` is the governed plan revision — a pre-approval amendment still parses
  ``pending`` (``core/governance.py:137-138``), a later author revision yields ``revision_pending``
  without a human amendment (``core/evaluation.py:1607-1622``), and ``rejected`` still admits a
  re-present (``core/governance.py:189``).
- **A record answers a claim**: it carries the claim's own text (checked against the wording digest
  the record is bound to) and its outcome — a human cannot judge a quote without them.
- **One escaper**: every free-text value that does not arrive already escaped is display-escaped here
  with the core's one escaper (``core.projection.safe_text``, core/projection.py:287-293). The
  dialog's goal, rationale and invocation facts arrive already escaped (``governance_dialog``,
  core/projection.py:308-338); the raw wording stays bound by its digests, computed before the
  document is built.

This module is pure: no I/O, no clock, no adapter or application imports (only ``core``), no
mutation of its inputs, and — since PR 07 — it is imported by the application's governance
transactions (``application/governance.py``), which is why ``core/hitl.py`` is in
``IMPLEMENTATION_SOURCE_PATHS`` (the path list changed in PR 07; closure content already changed
in PR 06, review-1 finding 23).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from core.canonical import CanonicalJSONError, canonical_json
from core.evaluation import TERMINAL_STATUSES, claim_digest, terminal_key
from core.governance import HUMAN_WAIT_STATES, sized as _proposal_sized
from core.projection import project_presentation, safe_text

#: The document's protocol identity (the schema's ``schema`` const, ``contracts/empirica/v2/hitl.schema.json``).
SCHEMA_ID = "empirica.hitl/1"
#: The two decisions a document exists for (ADR-0072): the run's configuration, its evidence (the
#: schema's ``kind`` enum — written as constants below, never compared as booleans anywhere;
#: the architecture rule guards the removed trusted ``evidence`` action name, not this kind).
CONFIGURATION = "configuration"
EVIDENCE = "evidence"
#: The configuration form's question (ADR-0072's discriminator, the schema's ``question`` enum).
APPROVE = "approve"
CONFIRM_AMENDMENT = "confirm_amendment"
#: The dialog's budget-row keys, copied closed (core/projection.py:312-320 builds every row for
#: exactly these, from ``core.governance.CEILINGS``, core/governance.py:46-47).
_BUDGET_KEYS = ("key", "label", "short", "help", "value", "used", "minimum", "maximum")
#: The run-fixed invocation facts the digested context carries (ADR-0072; the five that persist for
#: the run's lifetime — never ``human_surface``, which the host's own answer rewrites).
_INVOCATION_KEYS = ("host", "interactive", "signal", "delegation", "audit_retrieval")
#: The ceilings an amendment names (the decision payload's own budget set,
#: ``contracts/empirica/v2/request.schema.json`` ``governanceDecisionPayload.amendment``).
_CEILINGS = ("max_passes", "max_spawns", "max_audit_spawns")
_CONTROL_MODES = ("deliberative", "auto")
_OUTCOMES = ("supporting", "refuting")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def render(document: Any) -> bytes:
    """The document as exactly the bytes that are its identity.

    The value is first validated through ``core.canonical.canonical_json`` (the one owner: non-finite
    floats, non-string keys, sets and unsupported types are refused here, before any bytes exist),
    then serialized as ``json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False)`` plus
    one trailing newline, UTF-8 — the form the contract validator pins in every fixture
    (``scripts/validate_contracts.py`` ``check_hitl_bytes``) and ADR-0072's Q2 decided. A value that
    cannot be encoded as UTF-8 (a lone surrogate) refuses here as a :class:`CanonicalJSONError`,
    like every other refusal of the one canonical owner.
    """
    canonical_json(document)
    try:
        return (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    except UnicodeEncodeError as broken:
        raise CanonicalJSONError(f"the document is not encodable as UTF-8: {broken}") from broken


def digest(data: bytes) -> str:
    """The SHA-256 identity of rendered bytes, the same textual form as ``core.canonical.canonical_digest``."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def document_digest(document: Any) -> str:
    """The document's ``document_digest`` (ADR-0072): the digest of exactly its rendered bytes."""
    return digest(render(document))


def question_of(governance: Mapping[str, Any]) -> str:
    """Which configuration question the run's governance asks now (ADR-0072's discriminator).

    ``confirm_amendment`` iff some receipt has ``outcome == "amend"`` (a human amendment performed
    on the proposal this revision supersedes, ``application/governance.py:81-90,94-102``) whose
    ``plan_revision + 1`` is the governed plan revision; ``approve`` otherwise. The state word alone
    cannot tell them apart: a pre-approval amendment leaves ``pending`` (``core/governance.py:137-138``)
    and a rejected decision re-presents, so the receipts are the only witness of an amendment.
    Malformed input is refused as :class:`ValueError`, never as a stray ``KeyError``.
    """
    try:
        plan_revision = governance["plan_revision"]
        receipts = governance["receipts"]
    except (KeyError, TypeError) as broken:
        raise ValueError(f"the governance state lacks {broken!r}") from broken
    if type(plan_revision) is not int:
        raise ValueError("the governed plan revision is an integer")
    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            raise ValueError("a governance receipt is a mapping")
        if (receipt.get("outcome") == "amend"
                and receipt.get("plan_revision", -1) + 1 == plan_revision):
            return CONFIRM_AMENDMENT
    return APPROVE


def configuration_for(*, run_id: str, snapshot: Any) -> dict[str, Any]:
    """Derive every input of :func:`configuration_document` from one run snapshot (ADR-0072, PR 07).

    The one derivation both private legs use — the core recomputes the document from the state it
    is given, and only an exact match of the rendered bytes' digest admits the decision's
    ``document_digest``. Reused, never rebuilt: the production dialog
    (``core.projection.project_presentation`` → ``governance_dialog`` — a present cannot change it
    in ways the document reads); the question (:func:`question_of`) and the ``(was …)`` priors, from
    the run's own receipts (the amend receipt whose ``plan_revision + 1`` is the governed revision
    carries the superseded ceilings PR 06 records); the run-fixed invocation facts and the control
    mode, with the dialog. Nothing volatile is digested, so the derivation gives the same document
    whether or not the receipt being presented is already in state — the review's soundness demand.
    """
    try:
        governed = snapshot.state.governance
        plan_revision = governed["plan_revision"]
        receipts = governed["receipts"]
        proposal_digest = governed["proposal_digest"]
    except (AttributeError, KeyError, TypeError) as missing:
        raise ValueError(f"the snapshot lacks its governance: {missing!r}") from missing
    dialog = project_presentation(snapshot)["dialog"]
    priors = None
    for receipt in receipts:
        try:
            if (receipt["outcome"] == "amend"
                    and receipt["plan_revision"] + 1 == plan_revision):
                priors = receipt["superseded_budgets"]
                break
        except (KeyError, TypeError) as broken:
            raise ValueError(f"a governance receipt lacks {broken!r}") from broken
    return configuration_document(run_id=run_id, dialog=dialog,
                                  proposal_digest=proposal_digest, governance=governed,
                                  priors=priors)


def configuration_pair(*, run_id: str, snapshot: Any) -> dict[str, str] | None:
    """The core-rendered ``document: {text, digest}`` pair (ADR-0072 Q1) for one snapshot.

    The exact rendered UTF-8 string plus its digest — the pair the private ``governance_context``
    presentation result carries, so no adapter ever re-renders a dict. ``None`` exactly where no
    configuration document exists for this state: its judgment is not a human's now — only the
    human-wait states have a form to show (``HUMAN_WAIT_STATES``), and only once the author has
    sized the proposal (``core.governance.sized``): the unsized StartRun placeholder is
    unapprovable, so no document is fetched or written for it (review-2 m2). A derivation that
    fails on state that should have one refuses as :class:`ValueError`, never as ``None``.
    """
    try:
        governed = snapshot.state.governance
        name = governed["state"]
    except (AttributeError, KeyError, TypeError) as missing:
        raise ValueError(f"the snapshot lacks its governance: {missing!r}") from missing
    if name not in HUMAN_WAIT_STATES or not _proposal_sized(governed):
        return None
    document = configuration_for(run_id=run_id, snapshot=snapshot)
    data = render(document)
    return {"text": data.decode("utf-8"), "digest": digest(data)}


def _escaped(name: str, value: Any, where: str) -> str:
    """One free-text value through the core's one escaper (ADR-0072); nonempty text, nothing else."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where} carries its {name} as nonempty text")
    return safe_text(value)


def _integer(name: str, value: Any, where: str) -> int:
    """One numeric value as an exact ``int``: a bool or a float hides, so neither is admitted (review-1)."""
    if type(value) is not int:
        raise ValueError(f"{where} carries its {name} as an integer")
    return value


def configuration_document(*, run_id: str, dialog: Mapping[str, Any], proposal_digest: str,
                          governance: Mapping[str, Any],
                          priors: Mapping[str, int] | None = None,
                          question: str | None = None) -> dict[str, Any]:
    """Build the ``configuration`` document shown for approve-run-configuration and confirm-edited forms.

    Pure function of explicit inputs (ADR-0072): nothing is read, nothing is chosen here. Sources:

    - ``run_id``: the id every governance decision names (``contracts/empirica/v2/request.schema.json``
      ``governanceDecisionPayload.run_id``; the transaction passes ``encode_handle(key)``,
      application/location.py:63) — the host's receipt id is NOT here and never will be.
    - ``dialog``: the production dialog ``core/projection.py:308-338`` ``governance_dialog`` returns
      (built by ``project_presentation``, core/projection.py:365-372) — its ``epoch`` is the plan
      revision, its ``goal``/``rationale`` and its invocation facts arrive display-escaped there, its
      ``budgets`` rows are the closed display rows. Its ``reviews_left`` and its invocation's
      ``human_surface`` are read by nobody here: values that change under the human's own answer can
      never be digested, or the document the human read is not the one the decision names.
    - ``proposal_digest``: ``governance["proposal_digest"]`` (core/governance.py:79-97) — the digest
      the decision binds again. The two must agree exactly.
    - ``governance``: the run's governed state (``state.governance``, the honest witness): its
      ``state`` must be the dialog's state word, its ``plan_revision`` the dialog's epoch, its
      ``control_mode`` the dialog's, and its receipts (with :func:`question_of`) decide which
      question this document asks.
    - ``question``: optional; ``question_of(governance)`` by default. An explicit value must agree
      with the derived one — an adapter re-deriving the discriminator is exactly what this refuses.
    - ``priors``: the superseded ceilings recorded at the amend commit (the run/4 field PR 06/07
      adds; the confirm form's ``(was …)`` values, adapters/governance.py:110-115). Required for
      ``confirm_amendment``, refused for ``approve``.

    ``context_when_shown`` is context, never part of the decision (ADR-0072): the run-fixed
    invocation facts the dialog stood beside, digested only because they cannot change between the
    fetch, present and the decision.
    """
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("the configuration document names its run")
    if not isinstance(proposal_digest, str) or not _DIGEST.match(proposal_digest):
        raise ValueError("the configuration document binds a sha256 proposal digest")
    try:
        governed_state = governance["state"]
        governed_revision = governance["plan_revision"]
        governed_digest = governance["proposal_digest"]
        governed_mode = governance["control_mode"]
        epoch = dialog["epoch"]
        goal = dialog["goal"]
        rationale = dialog["rationale"]
        budgets = dialog["budgets"]
        state = dialog["state"]
        control_mode = dialog["control_mode"]
        invocation = dialog["invocation"]
    except (KeyError, TypeError, IndexError) as missing:
        raise ValueError(f"the governance inputs lack {missing!r}") from missing
    if state != governed_state:
        raise ValueError(f"the dialog says state {state!r} but the governance says {governed_state!r}")
    epoch = _integer("epoch/plan_revision", epoch, "the configuration document")
    if epoch != governed_revision:
        raise ValueError(f"the dialog shows revision {epoch} but the governance is at {governed_revision}")
    if proposal_digest != governed_digest:
        raise ValueError("the dialog's proposal digest and the governed one disagree")
    if control_mode != governed_mode:
        raise ValueError(f"the dialog says control mode {control_mode!r} but the governance says "
                         f"{governed_mode!r}")
    if control_mode not in _CONTROL_MODES:
        raise ValueError(f"unknown control mode {control_mode!r}")
    if state not in HUMAN_WAIT_STATES:
        raise ValueError(f"no configuration document for governance state {state!r}")
    derived = question_of(governance)
    if question is None:
        question = derived
    elif question != derived:
        raise ValueError(f"the dialog asks {question!r} but the governed answer is {derived!r}")
    if not isinstance(goal, str) or not goal:
        raise ValueError("the configuration document carries the run's goal in full")
    if rationale is not None and (not isinstance(rationale, str) or not rationale):
        raise ValueError("the dialog's rationale is null or nonempty text")
    if not isinstance(budgets, (list, tuple)) or len(budgets) != len(_CEILINGS):
        raise ValueError(f"the dialog shows exactly the {len(_CEILINGS)} ceiling rows")
    rows = []
    for row in budgets:
        if not isinstance(row, Mapping) or any(key not in row for key in _BUDGET_KEYS):
            raise ValueError("a dialog budget row is missing its display fields")
        if row["key"] not in _CEILINGS:
            raise ValueError(f"unknown ceiling {row['key']!r} in the dialog")
        rows.append({"key": row["key"], "label": _escaped("label", row["label"], "a dialog budget row"),
                     "short": _escaped("short", row["short"], "a dialog budget row"),
                     "help": _escaped("help", row["help"], "a dialog budget row"),
                     "value": _integer("value", row["value"], "a dialog budget row"),
                     "used": _integer("used", row["used"], "a dialog budget row"),
                     "minimum": _integer("minimum", row["minimum"], "a dialog budget row"),
                     "maximum": _integer("maximum", row["maximum"], "a dialog budget row")})
    if len({row["key"] for row in rows}) != len(_CEILINGS):
        raise ValueError("the dialog's budget rows do not name each ceiling once")
    if not isinstance(invocation, Mapping) or any(key not in invocation for key in _INVOCATION_KEYS):
        raise ValueError("the dialog's run-fixed invocation facts are missing")

    document: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "kind": CONFIGURATION,
        "run_id": run_id,
        "question": question,
        "governance_state": state,
        "plan_revision": epoch,
        "proposal_digest": proposal_digest,
        "goal": goal,
        "rationale": rationale,
        "rationale_source": "author_unverified",
        "control_mode": control_mode,
        "budgets": rows,
        "context_when_shown": {
            "invocation": {key: invocation[key] for key in _INVOCATION_KEYS},
        },
    }
    if question == CONFIRM_AMENDMENT:
        if priors is None:
            raise ValueError("a confirm_amendment document names the amendment it confirms (priors)")
        if set(priors) != set(_CEILINGS):
            raise ValueError("the amendment names exactly the three ceilings")
        document["amendment"] = {"budgets": {
            row["key"]: {"value": row["value"],
                         "was": _integer(row["key"], priors[row["key"]], "the amendment's priors")}
            for row in rows}}
    elif priors is not None:
        raise ValueError("an approve document confirms no amendment")
    return document


def evidence_document(*, run_id: str, batch: Mapping[str, Any],
                      records: Mapping[str, Mapping[str, Any]],
                      claims: Mapping[str, Mapping[str, Any]],
                      residuals: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Build the ``evidence`` document shown for the end-of-pass batch and the terminal consent.

    Pure function of explicit inputs (ADR-0072). Sources:

    - ``run_id``: the id the evidence presentation and confirmation payloads name
      (``contracts/empirica/v2/request.schema.json`` ``evidencePresentationPayload``/``evidenceConfirmationPayload``).
    - ``batch``: the core's own batch — ``confirmation_batch`` (core/evaluation.py:549-564) for the
      end-of-pass items or ``terminal_batch`` (core/evaluation.py:499-501) for the terminal consent.
      A terminal batch's ``key`` must be the core's own ``terminal_key`` of its status and wording
      (core/evaluation.py:486-489) — the same key the presentation admission checks — or nothing
      is shown. Malformed input is refused as :class:`ValueError`.
    - ``records``: the batch's research rows by artifact id, as the projected argument carries them
      (core/projection.py:586-590 — the same rows ``adapters.confirmation.build_items`` reads):
      ``citation`` is the quote a human confirms, ``spike_unavailable`` the why-no-spike reason that
      moves out of the form, ``verification`` the dossier's own class (required: the record is
      verified the way the dossier says, never re-derived here), ``outcome`` the record's own
      supporting/refuting verdict, and ``claim_id`` naming the claim row whose text the record
      answers — bound by the record's ``claim_digest``, exactly as the batch's claims are.
    - ``claims``: the batch's claim rows by claim id, the argument's claim rows
      (core/projection.py:566-573 — ``text`` with ``wording_digest``, the digest of this id+text,
      core/evaluation.py:277-280 ``claim_digest``); the batch's own ``claim_digest`` must bind
      exactly that wording or nothing is shown.
    - ``residuals``: what ending now would leave unresolved, the run-view obligation rows
      ``{id, required}`` (core/projection.py:139-147 ``_obligation``; report_requirements,
      core/evaluation.py:1408-1480) — the terminal consent's residue (coordinator decision on PR 03).

    The terminal consent is one decision on one item (core/evaluation.py:498-500): its document
    holds nothing else. No host-generated receipt id and no wall-clock time is ever copied.
    """
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("the evidence document names its run")
    if not isinstance(batch, Mapping) or not _DIGEST.match(str(batch.get("key", ""))):
        raise ValueError("the document binds the batch's sha256 key")
    terminal = batch.get("terminal")
    if terminal is not None:
        if not isinstance(terminal, Mapping):
            raise ValueError("the terminal consent is a mapping")
        try:
            status = terminal["status"]
            wording = terminal["digest"]
        except KeyError as missing:
            raise ValueError(f"the terminal consent lacks {missing!r}") from missing
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"unknown terminal status {status!r}")
        if batch["key"] != terminal_key(status, wording):
            raise ValueError("the terminal batch's key does not bind its status and wording digest")
        rows = []
        for row in residuals:
            if not isinstance(row, Mapping) or not isinstance(row.get("id"), str) or not row["id"]:
                raise ValueError("a residual row is missing its id")
            rows.append({"id": row["id"],
                         "required": _escaped("required line", row.get("required"), "a residual row")})
        return {
            "schema": SCHEMA_ID,
            "kind": EVIDENCE,
            "run_id": run_id,
            "key": batch["key"],
            "records": [],
            "claims": [],
            "terminal": {"status": terminal["status"], "digest": terminal["digest"],
                         "residuals": rows},
        }
    if not isinstance(batch.get("claims"), (list, tuple)):
        raise ValueError("the batch names its claim rows")
    record_rows = []
    for record_id in batch.get("record_ids", ()):
        row = records.get(record_id)
        if row is None:
            raise ValueError(f"the batch names record {record_id!r} but it was not provided")
        for field in ("claim_id", "source_kind", "source_ref"):
            if not isinstance(row.get(field), str) or not row[field]:
                raise ValueError(f"record {record_id!r} lacks its {field}")
        verification = row.get("verification")
        if not isinstance(verification, Mapping) \
                or not isinstance(verification.get("class"), str) or not verification["class"]:
            raise ValueError(f"record {record_id!r} lacks the dossier's verification class")
        if row.get("outcome") not in _OUTCOMES:
            raise ValueError(f"record {record_id!r} lacks its supporting/refuting outcome")
        claim = claims.get(row["claim_id"])
        if claim is None:
            raise ValueError(f"record {record_id!r} answers claim {row['claim_id']!r} "
                             "but the claim row was not provided")
        text = claim.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError(f"the claim {row['claim_id']!r} the record answers lacks its text")
        bound = claim.get("wording_digest", claim_digest({"id": row["claim_id"], "text": text}))
        if row.get("claim_digest") != bound:
            raise ValueError(f"record {record_id!r} is not bound to the wording of the claim "
                             f"{row['claim_id']!r} it answers")
        record = {"record_id": record_id, "claim_id": row["claim_id"],
                  "claim_text": _escaped("claim text", text, f"record {record_id!r}"),
                  "outcome": row["outcome"],
                  "source_kind": row["source_kind"],
                  "source_ref": _escaped("source_ref", row["source_ref"], f"record {record_id!r}"),
                  "citation": _escaped("citation", row.get("citation"), f"record {record_id!r}"),
                  "verification_class": verification["class"],
                  "claim_digest": row["claim_digest"]}
        if row.get("spike_unavailable") is not None:
            record["spike_unavailable"] = _escaped("why-no-spike reason", row["spike_unavailable"],
                                                  f"record {record_id!r}")
        record_rows.append(record)
    claim_rows = []
    for asked in batch["claims"]:
        claim = claims.get(asked["claim_id"])
        if claim is None:
            raise ValueError(f"the batch names claim {asked['claim_id']!r} but it was not provided")
        text = claim.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError(f"claim {asked['claim_id']!r} lacks its text")
        bound = claim.get("wording_digest", claim_digest({"id": asked["claim_id"], "text": text}))
        if asked.get("claim_digest") != bound:
            raise ValueError(
                f"the batch and the claim rows disagree on {asked['claim_id']!r}'s wording"
                " — the document shows only what the batch actually asked")
        claim_rows.append({"claim_id": asked["claim_id"], "claim_digest": asked["claim_digest"],
                           "text": _escaped("claim text", text, "a claim row")})
    return {
        "schema": SCHEMA_ID,
        "kind": EVIDENCE,
        "run_id": run_id,
        "key": batch["key"],
        "records": record_rows,
        "claims": claim_rows,
    }
