"""The pure ``empirica.hitl/1`` document builders (ADR-0072).

Each human decision of the forms workstream binds to one immutable core-owned JSON document
(``doc/adr/0072-bind-human-decisions-to-a-core-owned-immutable-hitl-document.md``): the native
forms shrink to the decision plus its facts, and everything else — the whole sizing case for a
configuration, every quote, class and why-no-spike reason for evidence, the terminal consent's
residue — moves into a document rendered here and digested by its own bytes. ``hitl.schema.json``
(``contracts/empirica/v2/hitl.schema.json``, PR 03) is the closed shape; the byte form below is the
one its contract validator pins; the digest is what the decision payloads and the write-once sidecar
will record (PRs 06–10).

This module is pure: no I/O, no clock, no adapter or application imports (only ``core``), no
mutation of its inputs, and it is imported by nothing yet — the transaction wiring that binds
decisions to these documents is PR 07, so ``IMPLEMENTATION_SOURCE_PATHS`` stays unchanged until then.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from core import citations
from core.canonical import canonical_json
from core.evaluation import TERMINAL_STATUSES, claim_digest

#: The document's protocol identity (the schema's ``schema`` const, ``contracts/empirica/v2/hitl.schema.json``).
SCHEMA_ID = "empirica.hitl/1"
#: The two decisions a document exists for (ADR-0072): the run's configuration, its evidence (the
#: schema's ``kind`` enum — written as constants below, never compared as booleans anywhere;
#: the architecture rule guards the removed trusted ``evidence`` action name, not this kind).
CONFIGURATION = "configuration"
EVIDENCE = "evidence"
#: The dialog's budget-row keys, copied closed (core/projection.py:312-320 builds every row for
#: exactly these, from ``core.governance.CEILINGS``, core/governance.py:46-47).
_BUDGET_KEYS = ("key", "label", "short", "help", "value", "used", "minimum", "maximum")
#: The ceilings an amendment names (the decision payload's own budget set,
#: ``contracts/empirica/v2/request.schema.json`` ``governanceDecisionPayload.amendment``).
_CEILINGS = ("max_passes", "max_spawns", "max_audit_spawns")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def render(document: Any) -> bytes:
    """The document as exactly the bytes that are its identity.

    The value is first validated through ``core.canonical.canonical_json`` (the one owner: non-finite
    floats, non-string keys, sets and unsupported types are refused here, before any bytes exist),
    then serialized as ``json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False)`` plus
    one trailing newline, UTF-8 — the form the contract validator pins in every fixture
    (``scripts/validate_contracts.py`` ``check_hitl_bytes``) and ADR-0072's Q2 decided.
    """
    canonical_json(document)
    return (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    """The SHA-256 identity of rendered bytes, the same textual form as ``core.canonical.canonical_digest``."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def document_digest(document: Any) -> str:
    """The document's ``document_digest`` (ADR-0072): the digest of exactly its rendered bytes."""
    return digest(render(document))


def configuration_document(*, run_id: str, dialog: Mapping[str, Any], proposal_digest: str,
                          governance_state: str,
                          priors: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Build the ``configuration`` document shown for approve-run-configuration and confirm-edited forms.

    Pure function of explicit inputs (ADR-0072): nothing is read, nothing is chosen here. Sources:

    - ``run_id``: the id every governance decision names (``contracts/empirica/v2/request.schema.json``
      ``governanceDecisionPayload.run_id``; the transaction passes ``encode_handle(key)``,
      application/location.py:63) — the host's receipt id is NOT here and never will be.
    - ``dialog``: the production dialog ``core/projection.py:308-338`` ``governance_dialog`` returns
      (how the projection builds it: core/projection.py:365-372 ``project_presentation``) — its
      ``epoch`` is the plan revision, its ``goal``/``rationale`` are the escaped full texts
      (core/projection.py:290-298), its ``budgets`` rows are the closed display rows
      (core/projection.py:312-320), its ``reviews_left`` and ``invocation`` are the facts
      (core/governance.py:168-177 ``interactions_remaining``; core/projection.py:299-305
      ``INVOCATION_FACTS``/``project_invocation``).
    - ``proposal_digest``: ``state.governance["proposal_digest"]`` (core/governance.py:79-97
      ``proposal_digest``; :104-116 where it is set) — the digest the decision binds again.
    - ``governance_state``: ``state.governance["state"]`` (core/governance.py:97-116 the pending
      placeholder; :128-146 ``revise`` setting ``revision_pending``; HUMAN_WAIT_STATES, :189):
      ``"pending"`` is the approve form, ``"revision_pending"`` the confirm-edited form
      (adapters/governance.py:95,118). Anything else never asks a human and raises here.
    - ``priors``: the superseded ceilings recorded at the amend commit (the run/4 field PR 06/07
      adds; the confirm form's ``(was …)`` values, adapters/governance.py:110-115). ``None`` for
      ``"pending"``; required and name-exact for ``"revision_pending"``.

    ``context_when_shown`` is recorded, never part of the decision (ADR-0072): what the dialog stood
    beside when the human saw it, so it can change without changing what is being approved.
    """
    if governance_state not in ("pending", "revision_pending"):
        raise ValueError(f"no configuration document for governance state {governance_state!r}")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("the configuration document names its run")
    if not isinstance(proposal_digest, str) or not _DIGEST.match(proposal_digest):
        raise ValueError("the configuration document binds a sha256 proposal digest")
    try:
        epoch = dialog["epoch"]
        goal = dialog["goal"]
        rationale = dialog["rationale"]
        budgets = dialog["budgets"]
        reviews_left = dialog["reviews_left"]
        invocation = dialog["invocation"]
    except (KeyError, TypeError, IndexError) as missing:
        raise ValueError(f"the governance dialog lacks {missing!r}") from missing
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
        rows.append({key: row[key] for key in _BUDGET_KEYS})
    if len({row["key"] for row in rows}) != len(_CEILINGS):
        raise ValueError("the dialog's budget rows do not name each ceiling once")
    if not isinstance(reviews_left, Mapping) or not isinstance(invocation, Mapping):
        raise ValueError("the dialog's shown facts are missing")

    document: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "kind": CONFIGURATION,
        "run_id": run_id,
        "governance_state": governance_state,
        "plan_revision": epoch,
        "proposal_digest": proposal_digest,
        "goal": goal,
        "rationale": rationale,
        "budgets": rows,
        "context_when_shown": {
            "budgets_used": {row["key"]: row["used"] for row in rows},
            "reviews_left": {key: reviews_left[key] for key in ("proposal", "total")},
            "invocation": {key: invocation[key] for key in
                           ("host", "interactive", "signal", "delegation",
                            "audit_retrieval", "human_surface")},
        },
    }
    if governance_state == "revision_pending":
        if priors is None:
            raise ValueError("a confirm-edited document names the amendment it confirms (priors)")
        if set(priors) != set(_CEILINGS):
            raise ValueError("the amendment names exactly the three ceilings")
        document["amendment"] = {"budgets": {
            row["key"]: {"value": row["value"], "was": priors[row["key"]]} for row in rows}}
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
      end-of-pass items or ``terminal_batch`` (core/evaluation.py:499-501) for the terminal consent;
      its ``key`` is what the presentation payload carries, its ``terminal`` member is
      ``terminal_digest``/``terminal_key``'s binding (core/evaluation.py:478-492, statuses :456).
    - ``records``: the batch's research rows by artifact id, as the projected argument carries them
      (core/projection.py:577-591 — the same rows ``adapters/confirmation.py:118-134`` ``build_items``
      reads): ``citation`` is the quote a human confirms, ``spike_unavailable`` the why-no-spike
      reason that moves out of the form, ``verification`` the projected class
      (fallback: ``core/citations.py:66-73`` ``verification_class``, the projection's own source).
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
        status = terminal.get("status")
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"unknown terminal status {status!r}")
        rows = []
        for row in residuals:
            if not isinstance(row, Mapping) or not isinstance(row.get("id"), str) or not row["id"] \
                    or not isinstance(row.get("required"), str) or not row["required"]:
                raise ValueError("a residual row is missing its id or required line")
            rows.append({"id": row["id"], "required": row["required"]})
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
        for field in ("claim_id", "source_kind", "source_ref", "citation"):
            if not isinstance(row.get(field), str) or not row[field]:
                raise ValueError(f"record {record_id!r} lacks its {field}")
        verification = row.get("verification")
        row_class = (verification or {}).get("class") if isinstance(verification, Mapping) else None
        if not isinstance(row_class, str):
            row_class = citations.verification_class(row["source_kind"], row["source_ref"])
        record = {"record_id": record_id, "claim_id": row["claim_id"],
                  "source_kind": row["source_kind"], "source_ref": row["source_ref"],
                  "citation": row["citation"], "verification_class": row_class}
        if not _DIGEST.match(str(row.get("claim_digest", ""))):
            raise ValueError(f"record {record_id!r} lacks its claim's wording digest")
        record["claim_digest"] = row["claim_digest"]
        if row.get("spike_unavailable") is not None:
            if not isinstance(row["spike_unavailable"], str) or not row["spike_unavailable"]:
                raise ValueError("a why-no-spike reason is nonempty text")
            record["spike_unavailable"] = row["spike_unavailable"]
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
                           "text": text})
    return {
        "schema": SCHEMA_ID,
        "kind": EVIDENCE,
        "run_id": run_id,
        "key": batch["key"],
        "records": record_rows,
        "claims": claim_rows,
    }
