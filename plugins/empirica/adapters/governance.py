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
from adapters.confirmation import CLAUDE_VISIBLE_LINES
from adapters.state.fsio import SymlinkRefused
from adapters.state.hitl import (DocumentMismatch, HardLinkUnsupported, HitlDocuments, publish)
from application import protocol
from core import environment as core_environment, hitl as core_hitl
from application.host_profile_values import HOST_PROFILE_APPROVAL_INGRESS_UNAVAILABLE
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


def closed_code(result: dict) -> str:
    """The one contract code for a mediation that ended without a decision, chosen from the run's own governance
    facts (never from the adapter's guess): the row it names owns the actions.

    The run's allowance spent, nobody who can be asked with no automatic way on, or no way left to change a shown-out
    configuration (the view's own ``next_action`` is the stop) → ``governance.approval_unavailable`` (the stop alone);
    a raise nobody can approve, or none can be asked for want of an interaction, while a proposal within the approved
    ceilings is still admitted (the view's ``next_action`` is to propose) → ``governance.raise_unavailable``; this configuration's
    presentations spent with a change still possible → ``governance.configuration_shown``; a human who closed the dialog
    → ``governance.dialog_closed``. Nobody to ask is tested first, so every host answers a resumed run the same way."""
    governed = (result.get("run") or {}).get("governance") or {}
    if governed.get("next_action") == "residual.accept":
        return "governance.approval_unavailable"
    spent = (governed.get("interactions_remaining") or {}).get("total") == 0
    if (governed.get("context") or {}).get("approval_capability") == "unavailable" or (spent and governed.get("prompt_error")):
        # nobody can approve a pending raise, or nothing is left to ask with, and the view still offers a proposal
        return "governance.raise_unavailable" if governed.get("raises") is True else "governance.approval_unavailable"
    if governed.get("prompt_error"):
        return ("governance.approval_unavailable" if (governed.get("interactions_remaining") or {}).get("total") == 0
                else "governance.configuration_shown")
    return "governance.dialog_closed" if governed.get("human_wait") is True else "governance.approval_unavailable"


def unavailable(result: dict, code: str | None = None, *, message: str | None = None) -> dict:
    code = code or closed_code(result)
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


def _approved_value(row: Mapping[str, Any], approved: Mapping[str, Any] | None) -> int | None:
    """The ceiling the run approved, for a row whose proposed value differs from it (a raise or a lowering: the whole change
    the human judges, as the edited-configuration form shows it); else ``None``. ``approved`` is the run view's own
    ``governance.budgets`` of a run that holds a first approval and is asked about a raise, and ``None`` otherwise."""
    if approved is None or row["key"] not in approved or row["value"] == approved[row["key"]]:
        return None
    return approved[row["key"]]


def _budget_property(row: Mapping[str, Any], approved: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Map one budget row to an editable integer property; a raised ceiling's title carries the approved value."""
    description = f"used {row['used']} · allowed {row['minimum']}–{row['maximum']}"
    with_help = description + " · " + row["help"]
    was = _approved_value(row, approved)
    return {"type": "integer", "title": row["label"] + ("" if was is None else f" (was {was})"),
            "description": with_help if len(with_help) <= 60 else description,
            "minimum": row["minimum"], "maximum": row["maximum"], "default": row["value"]}


def _rationale_lines(dialog: Mapping[str, Any]) -> tuple[str, ...]:
    """Render the already-escaped rationale completely beneath its contract-owned label."""
    return (dialog["rationale_label"], *textwrap.wrap(dialog["rationale"], width=72,
                                                       break_long_words=True,
                                                       break_on_hyphens=False))

#: The compact cue of the amended-configuration confirm (PR 19, finding B4): the human judges
#: the budgets and this cue, so it rides the visible lines while the full contract-owned
#: amendment warning stays below, whole.
_RATIONALE_CUE = "Rationale not regenerated for these values · details below"


def _visible(*lines: str) -> tuple[str, ...]:
    """The opening of a Claude governance form (PR 19, finding N7 of the Lima qualification of
    ``ac401739``): Claude Code 2.1.304 shows only the first ``CLAUDE_VISIBLE_LINES`` lines of an
    elicitation message and folds the rest — nothing expands it in place — so each form composes
    its opening through here: the heading (its countdown riding it), the bound document's
    ``Details:`` line and the line the human judges the form by — the review's goal; the
    confirm's budgets line with its cue, the cue being judged with the numbers so the confirm
    alone carries its ``Details:`` line one line under the fold (B4). No blank line may open a
    form, and everything each line says must fit what the window actually shows."""
    assert "" not in lines[:CLAUDE_VISIBLE_LINES], "a Claude form opened with a blank line (PR 19)"
    return tuple(lines)


def _document_lines(document: Mapping[str, Any], *, linked: bool = True) -> tuple[str, ...]:
    """The bound document's Details (PR 16, ADR-0072 Q5's amendment): a live observer's link to
    the decision's row in the UI — plus the document's own short digest — or, when no observer of
    this home answers, the honest fallback that keeps the document's exact absolute path (O5,
    as decided: the path is dropped only when a verified link is shown). Presentation only: the
    URL is built by the discovery helper beside the store, once per presentation, and the core
    never sees it. The ``File:`` line keeps its copy-paste guarantee: never shortened,
    never ``~-``shortened, never wrapped (Q4's one exempt line, new spelling). A document the
    observer projects no row for (``linked=False``) is named by its ``File:`` line alone, then
    its short digest."""
    short = f"Document {document['digest'][:7 + _SHORT_BODY]}"
    path = f"File: {document['path']}"
    if not linked:
        return (path, short)
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
            short, path)

def review_form(dialog: Mapping[str, Any], document: Mapping[str, Any], timeout: float, *,
                approved: Mapping[str, Any] | None = None) -> tuple[str, dict]:
    """Map a validated dialog and its written document to Claude's editable elicitation form.

    The form keeps exactly what the human judges (ADR-0072 "What the forms keep"): the numbers
    and the goal's first line; the rationale text lives in the document, pointed at by one line
    under the contract-owned unverified label. PR 19 (finding N7 of the Lima qualification of
    ``ac401739``): Claude Code 2.1.304 shows only the first ``CLAUDE_VISIBLE_LINES`` lines, so
    the heading, the document's ``Details:`` and the goal open the form — never a blank line —
    while the rationale pointer, the document's remaining lines and the legend stay below, whole.

    ``approved`` is given when the human is asked about a raise (the run view's ``governance.raises``): the ceilings the run
    approved. The form then says it raises them, and the line the human judges it by is the budgets with each raised ceiling
    as ``N (was M)``, as the confirm form shows a prior; the goal, which a raise does not change, stays below the fold."""
    reviews = dialog["reviews_left"]["proposal"]
    count = f"{reviews} {'review' if reviews == 1 else 'reviews'} left"
    details, *document_tail = _document_lines(document)
    facts = f"(epoch {dialog['epoch']} · {count} · {_duration(timeout)})"
    goal = _line(f'Goal: "{dialog["goal"]}"', closing='"')
    if approved is None:
        opening = (_line(f"Empirica · approve run configuration {facts}"), details, goal)
        below: tuple[str, ...] = ()
    else:
        parts = [_raise_part(row, approved) for row in dialog["budgets"]]
        opening = (_line(f"Empirica · raise approved ceilings {facts}"), _line(" · ".join(parts)), details)
        below = (goal,)
    message = "\n".join((
        *_visible(*opening),
        *below,
        _line(f"{dialog['rationale_label']} and full detail: in the document"),
        *document_tail,
        "Accept = approve as shown · edit values → confirm again",
        "Decline = reject · Esc = decide later",
    ))
    properties = {row["key"]: _budget_property(row, approved) for row in dialog["budgets"]}
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

def _raise_part(row: Mapping[str, Any], approved: Mapping[str, Any]) -> str:
    """One compact value of a raise form: the proposed ceiling, with the approved one when it differs (``_part``'s form)."""
    was = _approved_value(row, approved)
    return f"{row['short']} {row['value']}" + ("" if was is None else f" (was {was})")


def confirm_form(dialog: Mapping[str, Any], document: Mapping[str, Any], timeout: float) -> tuple[str, dict]:
    """Map an amended dialog and its written document to a buttons-only confirmation.

    PR 19 (finding N7 of the Lima qualification of ``ac401739``), with the finding-B4 grace:
    Claude Code 2.1.304 shows only the first ``CLAUDE_VISIBLE_LINES`` lines, and the confirm has
    no field descriptions to carry more — so its opening is the heading, the budgets line with
    its complete ``(was …)`` priors, the compact cue that the rationale was not regenerated for
    these values (the line judged with the numbers), and only then the ``Details:`` line; the
    full contract-owned amendment warning and the document stay below, whole."""
    budget_parts = [_part(row, document) for row in dialog["budgets"]]
    details, *document_tail = _document_lines(document)
    message = "\n".join((
        *(_visible(
            _line(f"Empirica · confirm edited configuration (epoch {dialog['epoch']} · {_duration(timeout)})"),
            _line(" · ".join(budget_parts)),
            _RATIONALE_CUE,
            details)),
        *textwrap.wrap(dialog["amendment_warning"], width=72,
                       break_long_words=False, break_on_hyphens=False),
        *document_tail,
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


def environment_form(document: Mapping[str, Any], timeout: float) -> tuple[str, dict]:
    """Map the published environment document to Claude's buttons-only approval form (ADR-0072).

    The decision is take-it-or-leave-it, so no fields ride the schema, and the form stays short:
    the full texts (the agent's plan, the rationale, the image references, the previous binding)
    live in the bound document. The visible lines are the heading, the profile with its
    network-egress call-out and the document's reference: a live observer's ``Details:`` link to
    the decision's row, else the document's exact ``File:`` path (``document_details``)."""
    doc = document["parsed"]
    profile = (f"Profile: {doc['profile_id']} · NETWORK EGRESS ENABLED" if doc["networked"]
               else f"Profile: {doc['profile_id']} · no network")
    reference, *document_tail = _document_lines(document, linked=bool(document.get("url")))
    message = "\n".join((
        *_visible(
            _line(f"Empirica · approve environment revision {doc['revision']}"
                  f" · {_duration(timeout)}"),
            _line(profile),
            reference),
        _line(f'Goal: "{doc["goal"]}"', closing='"'),
        f"Spike images: {len(doc['spike_images'])} · the agent's plan and rationale: in the document",
        *document_tail,
        "Accept = approve this revision for qualification",
        "Decline = reject · Esc = decide later",
    ))
    return message, {"type": "object", "properties": {}, "required": []}


def environment_decision(answer: object) -> bool | None:
    """Validate one MCP answer to the environment form: ``True`` approves the revision as shown,
    ``False`` deliberately rejects it, ``None`` is a non-decision (dismiss) that changes nothing
    — the revision stays pending, decided by nobody."""
    if not isinstance(answer, dict):
        return None
    action = answer.get("action")
    if action == "accept":
        return True
    if action == "decline":
        return False
    return None


class HostEnvironment:
    """The Claude mediation for one pending environment revision (E8, spec §5/§6): the public
    action admitted the revision, then the host completes the human approval the core waits on —
    write-first through the same bound sidecar helper, one present receipt, one elicit form, one
    decision receipt. Every refusal of the private legs is returned unchanged (the revision
    stays pending and the run's own recovery says what to do); nothing a host cannot show is
    ever fabricated (ADR-0072 never held for less)."""

    def __init__(self, profile: str, *, elicit=None, admit=None, fetch=None, decide=None, documents=None):
        self.profile, self.elicit = profile, elicit
        self.admit = admit if admit is not None else bridge.handle_admitting
        self.fetch = fetch if fetch is not None else bridge.trusted_environment_document
        self.decide = decide if decide is not None else bridge.trusted_environment_decision
        # the same write-first sidecar store (ADR-0072 PR 08): the environment document is
        # published once under the real $EMPIRICA_HOME before any form is shown
        self.documents = documents if documents is not None else HitlDocuments()

    def __call__(self, result: dict, admission: Mapping[str, Any] | None = None) -> dict:
        """Mediate one admitted environment action; no failure may change a typed result.
        ``admission`` is the private fact the admitting transaction recorded (the run and the
        revision it admitted): no other revision is ever mediated."""
        try:
            return _strip_presentation(self._mediate(result, admission))
        except Exception:
            return _strip_presentation(result)

    def _mediate(self, result: dict, admission: Mapping[str, Any] | None) -> dict:
        run = result.get("run") if isinstance(result, dict) else None
        if result.get("type") != "Allow" or not isinstance(run, dict):
            return result
        # a host surface without a form (the initialize capability says none) asks nobody:
        # the admitted action's own run view keeps saying the revision is pending
        if self.elicit is None or not isinstance(admission, Mapping) or admission.get("run_id") != run["id"]:
            return result
        shown, response = self.present(run["id"], admission["revision"], result)
        if shown is None:
            return response
        published, receipt_id = shown
        # one Details link per presentation: a live observer's link to the decision row, else the path
        from adapters.observer.discovery import document_details  # lazy, as the configuration form's
        document = {"digest": published.digest, "path": str(published.path), "parsed": published.parsed}
        document = {**document, **document_details(getattr(self.documents, "home", None), run["id"], document)}
        message, schema = environment_form(document, governance_timeout())
        outcome = environment_decision(self.elicit(message, schema))
        if outcome is None:
            return response  # nobody decided: the revision stays pending, its allowance
            # untouched (a dismissed form spends no presentation row)
        decided = (self.decide(self.profile, run["id"], {
            "run_id": run["id"], "receipt_id": receipt_id,
            "revision": published.parsed["revision"],
            "proposal_digest": published.parsed["proposal_digest"],
            "outcome": "approve" if outcome else "reject",
            "document_digest": published.digest}) or {}).get("result")
        return decided if isinstance(decided, dict) else response

    def present(self, run_id: str, revision: int, fallback: dict | None):
        """The write-first presentation of the admitted ``revision`` of ``run_id``: the present,
        and one re-presentation of the same revision after a document mismatch. Every fetched
        document must name this run, the environment kind and that revision before anything is
        written. ``((published, receipt_id), presented)`` when the form may be shown, else
        ``(None, the response to return)``."""
        expect = {"run_id": run_id, "kind": core_hitl.ENVIRONMENT, "revision": revision}
        for _ in range(2):
            shown, response = self._present(run_id, expect, fallback)
            if shown is not None or not _is_document_mismatch(response):
                break
        return shown, response

    def _present(self, run_id: str, expect: Mapping[str, object], fallback: dict | None):
        """One write-first present: fetch the pair, publish it once under the expected identity,
        present one receipt. ``((published, receipt_id), presented)`` when the form may be shown,
        else ``(None, the response to return)`` — every refusal of a private leg unchanged."""
        fetched = (self.fetch(self.profile, run_id, {}) or {}).get("result")
        if (not isinstance(fetched, dict) or fetched.get("type") != "Allow"
                or not isinstance(fetched.get("presentation"), Mapping)
                or not isinstance(fetched.get("run"), dict)):
            return None, fetched if isinstance(fetched, dict) else fallback
        # the fetch, the action and the document all name one run — before any write
        if fetched["run"]["id"] != run_id:
            return None, fallback
        # write-first: publish the fetched pair once, through the one bound helper, under the
        # expected run, kind and admitted revision; a document this state cannot bind (or a
        # storage refusal) is never a fabricated form
        try:
            published = publish(self.documents, run_id, fetched["presentation"].get("document"),
                                expect=expect)
        except (DocumentMismatch, SymlinkRefused, HardLinkUnsupported, OSError, ValueError):
            return None, fallback
        if published is None:
            return None, fallback
        receipt_id = uuid4().hex
        # the full receipt shape the member validates (E7): its revision and proposal digest come
        # from the bound document itself — the very bytes just written whose digest the receipt
        # names — so the mediation never carries state facts from anywhere but the write it owns
        presented = (self.decide(self.profile, run_id, {
            "run_id": run_id, "receipt_id": receipt_id,
            "revision": published.parsed["revision"],
            "proposal_digest": published.parsed["proposal_digest"],
            "outcome": "present", "document_digest": published.digest}) or {}).get("result")
        if not isinstance(presented, dict) or presented.get("type") != "Allow":
            return None, presented if isinstance(presented, dict) else fallback
        return (published, receipt_id), presented


def _is_document_mismatch(response: object) -> bool:
    """Whether a private leg refused with the closed document-mismatch reason: the one refusal
    whose recovery is a same-revision re-present (fetch the document again, present again)."""
    return (isinstance(response, dict) and response.get("type") == "Block"
            and any(isinstance(row, dict) and row.get("code") == core_environment.DOCUMENT_MISMATCH
                    for row in response.get("reasons", ())))


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
        approval_kind = expected_approval_kind(g, g["raises"])
        approval_ingress = protocol.host_profile(self.profile)["approval_ingress"]
        if approval_kind == "host_ui" and (approval_ingress == HOST_PROFILE_APPROVAL_INGRESS_UNAVAILABLE or self.elicit is None):
            # Report what this host can do NOW, so the run's own facts (and its Next) say nobody can be asked,
            # whatever an earlier context said; then the view names the stop alone.
            reported = self.context_ingress(self.profile, run["id"],
                                            {"author": g["context"]["author"], "ingress": HOST_PROFILE_APPROVAL_INGRESS_UNAVAILABLE}).get("result", {})
            return unavailable(reported if reported.get("run") else result)
        context = {"author": g["context"]["author"], "ingress": approval_ingress}
        result = self.context_ingress(self.profile, run["id"], context).get("result", {})
        if result.get("type") not in {"Allow", "Inert"} or not result.get("run"):
            return result
        run, g = result["run"], result["run"]["governance"]
        if confirmation and expected != (g["plan_revision"], g["proposal_digest"]):
            return unavailable(result, "governance.stale_proposal")
        if g["prompt_error"]:
            return unavailable(result)
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
            # a raise is shown as one, beside the ceilings the run approved (the view's own, never recomputed here)
            approved = g.get("budgets") if g.get("raises") is True else None
            message, schema = (confirm_form(dialog, document, timeout) if confirm
                               else review_form(dialog, document, timeout, approved=approved))
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
