"""Host mediation for public proposal calls; never a public approval tool."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import textwrap
from typing import Any, Mapping
from uuid import uuid4

import jsonschema

from adapters import bridge
from adapters.state.fsio import SymlinkRefused
from adapters.state.hitl import (DocumentMismatch, HardLinkUnsupported, HitlDocuments, publish)
from application import protocol
from core import hitl as core_hitl
from core.governance import expected_approval_kind

#: The short digest length (ADR-0072 Q4): twelve hex — the scan length the repository's
#: identifiers already use (``application.observer_index.ID_SIZE``). Kept a local literal so the
#: host governance runtime does not inherit the observer identity family into the manifest.
_SHORT_BODY = 12
#: The command that opens this host's observer UI (README.md "Usage"): the fallback Details
#: line names only this host's command (A11) — Pi names its own (``/empirica-ui``) in its own
#: dialog sources, and the cross-host caption pin (``tests/v2/test_evidence_hosts.py``) holds
#: the words both sides.
_UI_COMMAND = "/empirica:ui"


@dataclass(frozen=True)
class Approve:
    """A validated approval carrying the exact displayed configuration."""

    configuration: dict[str, dict[str, int]]


@dataclass(frozen=True)
class Reject:
    """A deliberate rejection from the initial review dialog."""


@dataclass(frozen=True)
class Dismiss:
    """A non-decision or invalid host answer that grants no authority."""


_TIMEOUT_SPACE = " \t\n\r\f\v"
_TIMEOUT_SECONDS = re.compile(r"0*([1-9][0-9]{0,3})")


def governance_timeout() -> float:
    """Return the host decision timeout in seconds.

    Unset or blank ``EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS`` means the 900-second default. A set value must be
    ASCII decimal digits only (after trimming ASCII whitespace) from 1 to 1500 inclusive, leading zeros allowed;
    the significant digits are bounded before conversion so no input length can change the outcome. Anything else raises
    ``ValueError`` rather than silently becoming the default. The Pi mirror (``governanceTimeout``) accepts the
    identical syntax and formats the identical message; the shared case table pins both.
    """
    raw = os.environ.get("EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS")
    text = None if raw is None else raw.strip(_TIMEOUT_SPACE)
    if not text:
        return 900
    match = _TIMEOUT_SECONDS.fullmatch(text)
    if match and int(match.group(1)) <= 1500:
        return int(match.group(1))
    raise ValueError("EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS must be a whole number of seconds from 1 to 1500, "
                     f"got {json.dumps(raw, ensure_ascii=False)}")


def unavailable(result: dict, code: str = "governance.approval_unavailable", *, message: str | None = None) -> dict:
    row = protocol.public_contract()["reasons"][code]
    return {"type": "Block", "run": result["run"], "reasons": [{"code": code,
            "parameters": {}, "message": message or row["message"], "sections": row["sections"],
            "next_actions": row["next_actions"]}]}


def _duration(timeout: float) -> str:
    minutes = timeout / 60
    return f"{minutes:g} min" if minutes >= 1 and minutes.is_integer() else f"{timeout:g} sec"


def _line(text: str, limit: int = 72, *, closing: str = "") -> str:
    """Truncate one safe display line while preserving a required suffix."""
    if len(text) <= limit:
        return text
    return text[:limit - len(closing) - 1] + "…" + closing


def _budget_property(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map one budget row to an editable integer property."""
    description = f"used {row['used']} · allowed {row['minimum']}–{row['maximum']}"
    with_help = description + " · " + row["help"]
    return {"type": "integer", "title": row["label"],
            "description": with_help if len(with_help) <= 60 else description,
            "minimum": row["minimum"], "maximum": row["maximum"], "default": row["value"]}


def _rationale_lines(dialog: Mapping[str, Any]) -> tuple[str, ...]:
    """Render the already-escaped rationale completely beneath its contract-owned label."""
    return (dialog["rationale_label"], *textwrap.wrap(dialog["rationale"], width=72,
                                                       break_long_words=True,
                                                       break_on_hyphens=False))

def _document_lines(document: Mapping[str, Any]) -> tuple[str, ...]:
    """The bound document's Details (PR 16, ADR-0072 Q5's amendment): a live observer's link to
    the decision's row in the UI — plus the document's own short digest — or, when no observer of
    this home answers, the honest fallback that keeps the document's exact absolute path (O5,
    as decided: the path is dropped only when a verified link is shown). Presentation only: the
    URL is built by the discovery helper beside the store, once per presentation, and the core
    never sees it. The ``File:`` line keeps its copy-paste guarantee: never shortened,
    never ``~-``shortened, never wrapped (Q4's one exempt line, new spelling)."""
    short = f"Document {document['digest'][:7 + _SHORT_BODY]}"
    url = document.get("url")
    if isinstance(url, str) and url:
        return (f"Details: {url}", short)
    run12 = document.get("run12")
    # Follow-up (review 16-1): one short arrow line, at most 76 columns, never wrapping mid-phrase
    # (the old two-line fallback broke after "Human" at the wrap width). The arrows read as one
    # path to the human decisions: the host command, the run's compact id.
    where = (f"{_UI_COMMAND} → run {run12} → Human decisions"
             if run12 else f"{_UI_COMMAND} → Human decisions")
    return (*textwrap.wrap(f"Details: {where}", width=72, break_long_words=False,
                           break_on_hyphens=False),
            short, f"File: {document['path']}")

def review_form(dialog: Mapping[str, Any], document: Mapping[str, Any], timeout: float) -> tuple[str, dict]:
    """Map a validated dialog and its written document to Claude's editable elicitation form.

    The form keeps exactly what the human judges (ADR-0072 "What the forms keep"): the numbers
    and the goal's first line; the rationale text lives in the document, pointed at by one line
    under the contract-owned unverified label."""
    reviews = dialog["reviews_left"]["proposal"]
    count = f"{reviews} {'review' if reviews == 1 else 'reviews'} left"
    message = "\n".join((
        _line(f"Empirica · approve run configuration (epoch {dialog['epoch']} · {count} · {_duration(timeout)})"),
        _line(f'Goal: "{dialog["goal"]}"', closing='"'),
        _line(f"{dialog['rationale_label']} and full detail: in the document"),
        *_document_lines(document),
        "Accept = approve as shown · edit values → confirm again",
        "Decline = reject · Esc = decide later",
    ))
    properties = {row["key"]: _budget_property(row) for row in dialog["budgets"]}
    return message, {"type": "object", "properties": properties, "required": []}


def _part(row: Mapping[str, Any], document: Mapping[str, Any]) -> str:
    """Render one compact confirmation value and its prior when changed.

    The ``(was …)`` priors are the document's own amendment values — the core-owned run/4
    ``superseded_budgets`` reached through the rendered document, never a host-held dialog
    snapshot (the host-held ``before`` dialog of the pre-PR-08 forms is gone: a host cannot be
    trusted to hold what the record must prove)."""
    show = (lambda value: "on" if value else "off") if isinstance(row["value"], bool) else str
    value = show(row["value"])
    priors = (document.get("amendment") or {}).get("budgets", {}) if isinstance(document, Mapping) else {}
    old = priors[row["key"]]["was"] if row["key"] in priors else row["value"]
    suffix = f" (was {show(old)})" if old != row["value"] else ""
    return f"{row['short']} {value}{suffix}"

def confirm_form(dialog: Mapping[str, Any], document: Mapping[str, Any], timeout: float) -> tuple[str, dict]:
    """Map an amended dialog and its written document to a buttons-only confirmation."""
    budget_parts = [_part(row, document) for row in dialog["budgets"]]
    message = "\n".join((
        _line(f"Empirica · confirm edited configuration (epoch {dialog['epoch']} · {_duration(timeout)})"),
        _line(" · ".join(budget_parts)),
        _line(f"{dialog['rationale_label']} and full detail: in the document"),
        *textwrap.wrap(dialog["amendment_warning"], width=72,
                       break_long_words=False, break_on_hyphens=False),
        *_document_lines(document),
        "Accept = approve exactly this · Decline/Esc = keep edits pending",
    ))
    return message, {"type": "object", "properties": {}}


def decision(answer: object, dialog: Mapping[str, Any], schema: Mapping[str, Any], *, confirmation: bool) -> Approve | Reject | Dismiss:
    """Validate one MCP answer and return a typed, fail-closed host decision."""
    if not isinstance(answer, dict):
        return Dismiss()
    action = answer.get("action")
    if action == "decline":
        return Dismiss() if confirmation else Reject()
    if action != "accept":
        return Dismiss()
    raw_content = answer.get("content", {}) if confirmation else answer.get("content")
    if not isinstance(raw_content, dict):
        return Dismiss()
    content = dict(raw_content)
    for row in dialog["budgets"]:
        key = row["key"]
        if isinstance(content.get(key), str) and re.fullmatch(r"[0-9]{1,4}", content[key]):
            content[key] = int(content[key])
        if key in content and (isinstance(content[key], bool) or not isinstance(content[key], int)):
            return Dismiss()
    try:
        jsonschema.validate(content, {**schema, "additionalProperties": False})
    except (TypeError, jsonschema.ValidationError):
        return Dismiss()
    if confirmation:
        return Approve(_configuration(dialog, {})) if not content else Dismiss()
    return Approve(_configuration(dialog, content))


def _configuration(dialog: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """Build an exact configuration from validated form values and displayed defaults."""
    return {
        "budgets": {row["key"]: content.get(row["key"], row["value"]) for row in dialog["budgets"]},
    }


def _strip_presentation(result: dict) -> dict:
    """Return an envelope copy without the private presentation key."""
    if isinstance(result, dict) and "presentation" in result:
        return {key: value for key, value in result.items() if key != "presentation"}
    return result


class HostGovernance:
    def __init__(self, profile: str, *, elicit=None, context_ingress=bridge.trusted_governance_context,
                 decision_ingress=bridge.trusted_governance_decision, documents=None):
        self.profile, self.elicit = profile, elicit
        self.context_ingress, self.decision_ingress = context_ingress, decision_ingress
        # PR 08 (ADR-0072 write-first): every config document is published once through the
        # sidecar store under the real $EMPIRICA_HOME before any form is shown; tests inject a
        # store under a temporary machine home.
        self.documents = documents if documents is not None else HitlDocuments()

    def __call__(self, result: dict, *, confirmation: bool = False) -> dict:
        """Finalize one host mediation and convert all failures to typed closed results."""
        try:
            mediated = self._mediate(result, confirmation=confirmation)
        except Exception:
            run = result.get("run") if isinstance(result, dict) else None
            if isinstance(run, dict):
                return _strip_presentation(unavailable(result))
            return _strip_presentation(result)
        return _strip_presentation(mediated)

    def _bound_document(self, context_result: dict) -> Mapping[str, Any] | None:
        """Write-first (ADR-0072, PR 08): verify the private answer's rendered ``document`` pair
        and publish it once through :func:`adapters.state.hitl.publish` — the one bound helper
        (review-2 m1) that parses and object-checks the pair before the write — then return
        the shown-document record: the digest, the written file's absolute path, the
        core-owned amendment priors the confirm form reads, and the parsed document's
        ``question`` — the core-derived form discriminator (ADR-0072), so the mediation
        chooses the form from the document it binds, never from an adapter flag.
        ``None`` means the mediation must refuse (the document impossible to bind, or the
        storage refused the write): the caller returns unavailable before any present,
        nothing spent."""
        presentation = context_result.get("presentation")
        document = presentation.get("document") if isinstance(presentation, Mapping) else None
        run = context_result.get("run") if isinstance(context_result, dict) else None
        run_id = run.get("id") if isinstance(run, dict) else None
        try:
            published = publish(self.documents, run_id, document)
        except (DocumentMismatch, SymlinkRefused, HardLinkUnsupported, OSError, ValueError):
            return None
        if published is None:
            return None
        # X3 (Copilot #49), defense in depth beside the shared publish helper: a
        # digest-matching document whose parsed question is missing or not one of the two the
        # core derives would choose a form from nothing — the present the core then refuses as a
        # document mismatch, when nothing was verifiable in the first place. Refuse here: the
        # mediation answers ``governance.approval_unavailable`` before any present, nothing
        # spent.
        question = published.parsed.get("question")
        if question not in (core_hitl.APPROVE, core_hitl.CONFIRM_AMENDMENT):
            return None
        return {"digest": published.digest, "path": str(published.path),
                "amendment": published.parsed.get("amendment"),
                "question": question}


    def _mediate(self, result: dict, *, confirmation: bool = False) -> dict:
        run = result.get("run")
        if result.get("type") != "Allow" or not isinstance(run, dict) or not run.get("governance"):
            return result
        g = run["governance"]
        expected = (g["plan_revision"], g["proposal_digest"])
        if run["status"] != "active" or g["state"] == "approved":
            return result
        approval_kind = expected_approval_kind(g)
        approval_ingress = protocol.host_profile(self.profile)["approval_ingress"]
        if approval_kind == "host_ui" and (approval_ingress == "unavailable" or self.elicit is None):
            return unavailable(result)
        context = {"author": g["context"]["author"], "ingress": approval_ingress}
        result = self.context_ingress(self.profile, run["id"], context).get("result", {})
        if result.get("type") not in {"Allow", "Inert"} or not result.get("run"):
            return result
        run, g = result["run"], result["run"]["governance"]
        if confirmation and expected != (g["plan_revision"], g["proposal_digest"]):
            return unavailable(result, "governance.stale_proposal")
        if g["prompt_error"]:
            return unavailable(result, g["prompt_error"])
        envelope = {"run_id": run["id"], "receipt_id": uuid4().hex, "proposal_digest": g["proposal_digest"],
                    "plan_revision": g["plan_revision"], "approval_kind": approval_kind}
        document = None
        if approval_kind == "host_ui":
            # write-first: bind the document (verify, publish once, read the path back) before any
            # present; a document this state cannot bind refuses the mediation outright
            document = self._bound_document(result)
            if document is None:
                return unavailable(result)
            envelope["document_digest"] = document["digest"]
            # PR 16 (A10/A11): the one Details link per presentation — a live observer of this
            # home's link to the decision row beside the short digest, else the honest fallback
            # keeping the path. Presentation only: it never enters the decision envelope.
            from adapters.observer.discovery import document_details  # lazy: a no-document refresh never pays it
            document = {**document, **document_details(getattr(self.documents, "home", None),
                                                        run["id"], document)}

        def dismiss(message=None):
            stored = self.decision_ingress(self.profile, run["id"], {**envelope, "outcome": "dismiss"})["result"]
            return unavailable(stored, message=message) if stored.get("type") in {"Allow", "Inert"} else stored

        if approval_kind == "auto":
            decision_envelope = {**envelope, "outcome": "approve"}
            action = "approve"
            dialog = None
        else:
            presented = self.decision_ingress(self.profile, run["id"], {**envelope, "outcome": "present"})["result"]
            if presented.get("type") != "Allow":
                return presented
            dialog = presented["presentation"]["dialog"]
            if not isinstance(dialog, dict):
                return dismiss()
            timeout = governance_timeout()
            # M1 (review 2): the form is chosen by the bound document's core-derived question,
            # never by the adapter's own recursion flag (ADR-0072 "The question and the
            # amendment's priors" — ``question`` is derived by the core). The recursion
            # flag can only agree here (an amend commit always makes the epoch's question
            # ``confirm_amendment``); where it could disagree, the document wins — the flag
            # stays only for the re-entry guard (the stale proposal re-check below and the
            # recursion), no longer for the form choice.
            confirm = document["question"] == core_hitl.CONFIRM_AMENDMENT
            message, schema = (confirm_form(dialog, document, timeout) if confirm
                               else review_form(dialog, document, timeout))
            resolved = decision(self.elicit(message, schema), dialog, schema, confirmation=confirm)
            if isinstance(resolved, Dismiss):
                return dismiss()
            action = "reject" if isinstance(resolved, Reject) else "approve"
            configuration = _configuration(dialog, {}) if isinstance(resolved, Reject) else resolved.configuration
            decision_envelope = {**envelope, "submission": {"action": action, "configuration": configuration}}
        admitted = self.decision_ingress(self.profile, run["id"], decision_envelope)["result"]
        if approval_kind == "host_ui" and admitted.get("type") in {"Fault", "Block"}:
            stored = dismiss()
            if admitted.get("type") == "Block" and stored.get("run"):
                admitted["run"] = stored["run"]
                return admitted
            return stored
        revised = admitted.get("run", {}).get("governance", {}).get("plan_revision") != g["plan_revision"]
        if not confirmation and action == "approve" and admitted.get("type") == "Allow" and revised:
            return self._mediate(admitted, confirmation=True)
        return admitted
