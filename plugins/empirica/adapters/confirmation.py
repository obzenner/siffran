"""Host mediation of the end-of-pass human confirmation (ADR-0067); the one flow every host shares.

The core blocks ``report_convergence`` with ``evidence.confirmation_required`` and names what waits.
A host then (1) records that it showed the human the batch (the *presentation*, which spends one
interaction), (2) asks, one decision per item (Confirm / Reject with an optional note / Later for a
record; Approve / Reject with an optional answer / Later for a ``needs-decision`` claim), and
(3) submits one receipt with the decisions. **Later is an omitted item.** There is no "approve all".

This module decides no policy. It builds the batch from the run's own dossier, maps the human's answers
to the receipt the core defines, and fails closed: a cancelled, timed-out, malformed or missing answer
submits nothing beyond the presentation already spent, and grants nothing. Claude drives it with an MCP
form; Pi drives the same ``present``/``submit`` through its private bridge and shows the items with its
own dialogs.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import textwrap
from typing import Any
from uuid import uuid4

from adapters import bridge
from application import protocol

#: A decision's answer is at most this long (``evidenceConfirmationPayload``); a research note may be longer.
_ANSWER_LIMIT = 2000
_NOTE_LIMIT = 2048

#: choice -> the core outcome; ``later`` is deliberately absent (an omitted item).
_RESEARCH = {"confirm": "confirm", "reject": "reject"}
_CLAIM = {"approve": "approve", "reject": "reject"}
CHOICES = {"research": ("later", *_RESEARCH), "claim": ("later", *_CLAIM)}


def _limit(subject: str) -> int:
    return _ANSWER_LIMIT if subject == "claim" else _NOTE_LIMIT


def presentable(result: object) -> dict[str, Any] | None:
    """The run view's ``confirmation_batch`` when a human can be shown it now, else ``None``.

    The core projects the batch whatever else blocks the run and says why it cannot be shown
    (``prompt_error``); the host presents exactly when there is a batch and no error. It decides nothing."""
    run = result.get("run") if isinstance(result, dict) else None
    batch = run.get("confirmation_batch") if isinstance(run, dict) else None
    return batch if isinstance(batch, dict) and batch.get("prompt_error") is None else None


def _call(profile: str, run_id: str, command: dict, dispatch: Callable[..., dict]) -> dict[str, Any]:
    request = {"protocol": protocol.protocol_id(), "request_id": uuid4().hex,
               "command": {**command, "run_id": run_id}}
    result = dispatch(request, profile).get("result", {})
    return result if isinstance(result, dict) else {}


def build_items(argument: Mapping[str, Any], batch: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The items the human is asked about, exactly the batch's ids and in its order (decisions first)."""
    text = {row["claim_id"]: row for row in argument.get("claims", ())}
    research = {row["artifact_id"]: row for row in argument.get("artifacts", ()) if row.get("kind") == "research"}
    items: list[dict[str, Any]] = []
    for claim_id in batch.get("claim_ids", ()):
        if claim_id in text:
            items.append({"subject": "claim", "id": claim_id, "digest": text[claim_id]["wording_digest"],
                          "text": text[claim_id]["text"]})
    for record_id in batch.get("record_ids", ()):
        row = research.get(record_id)
        if row is None:
            continue
        item = {"subject": "research", "id": record_id, "claim": text.get(row["claim_id"], {}).get("text", ""),
                "source_kind": row["source_kind"], "source_ref": row["source_ref"],
                "quote": row["citation"], "class": row["verification"]["class"]}
        if row.get("spike_unavailable"):
            item["reason"] = row["spike_unavailable"]
        items.append(item)
    return items


def observe_surface(profile: str, run_id: str, can_present: bool, *,
                    ingress: Callable[..., dict] = bridge.trusted_human_surface) -> bool:
    """Tell the core whether this host can show a dialog to a human now; the latest observation counts. True when the
    core took it (or already held it); False when the ingress faulted or raised, so the caller can fail closed."""
    response = ingress(profile, run_id, {"run_id": run_id, "surface": "human" if can_present else "unavailable"})
    result = response.get("result", response) if isinstance(response, dict) else None
    return isinstance(result, dict) and result.get("type") != "Fault"


def present(profile: str, run_id: str, *, dispatch: Callable[..., dict] = bridge.handle,
            ingress: Callable[..., dict] = bridge.trusted_evidence_presentation,
            ) -> tuple[str, list[dict[str, Any]]] | dict[str, Any]:
    """Record the presentation of the core's current batch, then return ``(receipt_id, items)``.

    The batch is read from the run, never supplied by the caller, and the presentation names its key: the core
    admits it only while that is still the current batch, so what is recorded as shown is exactly what the human
    sees. When the batch changed in between the host reads again and tries once more; a second change shows
    nothing and spends nothing. Anything else the core refuses (already presented, interaction limit, nothing
    pending) is returned as that typed response instead, and the caller shows nothing."""
    stored: dict[str, Any] = {"type": "Fault", "code": "unavailable", "fail_direction": "closed"}
    for _attempt in range(2):
        batch = presentable(_call(profile, run_id, {"type": "GetRun"}, dispatch))
        argument = _call(profile, run_id, {"type": "GetArgument"}, dispatch).get("argument") if batch else None
        items = build_items(argument, batch) if batch and isinstance(argument, dict) else []
        if not items:
            return {"type": "Fault", "code": "unavailable", "fail_direction": "closed"}
        receipt_id = uuid4().hex
        stored = ingress(profile, run_id, {"run_id": run_id, "receipt_id": receipt_id, "key": batch["key"]}).get("result", {})
        if stored.get("type") == "Allow":
            return receipt_id, items
        if not (stored.get("type") == "Fault" and stored.get("code") == "conflict"):
            return stored
    return stored


def decisions(items: Sequence[Mapping[str, Any]], answers: object) -> list[dict[str, Any]]:
    """Receipt decisions for the answers given, or ``[]`` when any answer is malformed.

    ``answers`` is ``[{id, choice, note?}]``. An answer about something that was not shown, a repeated
    subject or an unknown choice makes the whole submission empty: a half-understood dialog grants
    nothing."""
    if not isinstance(answers, (list, tuple)):
        return []
    shown = {(item["subject"], item["id"]): item for item in items}
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for answer in answers:
        if not isinstance(answer, Mapping):
            return []
        subject, ident, choice = answer.get("subject"), answer.get("id"), answer.get("choice")
        note = answer.get("note")
        item = shown.get((subject, ident))
        if item is None or (subject, ident) in seen or choice not in CHOICES[subject]:
            return []
        if note is not None and (not isinstance(note, str) or len(note) > _limit(subject)):
            return []
        seen.add((subject, ident))
        if choice == "later":
            continue
        note = note.strip() if isinstance(note, str) else ""
        if subject == "research":
            row: dict[str, Any] = {"subject": "research", "artifact_id": ident, "outcome": _RESEARCH[choice]}
            if note:
                row["note"] = note
        else:
            row = {"subject": "claim", "claim_id": ident, "claim_digest": item["digest"],
                   "outcome": _CLAIM[choice]}
            if note:
                row["answer"] = note
        out.append(row)
    return out


def submit(profile: str, run_id: str, receipt_id: str, items: Sequence[Mapping[str, Any]],
           answers: object, *, ingress: Callable[..., dict] = bridge.trusted_evidence_confirmation,
           ) -> dict[str, Any] | None:
    """Submit one receipt for the answers; ``None`` when they grant nothing (Later, cancel, malformed)."""
    chosen = decisions(items, answers)
    if not chosen:
        return None
    return ingress(profile, run_id, {"run_id": run_id, "receipt_id": receipt_id,
                                     "decisions": chosen}).get("result", {})


# ---- the Claude form ------------------------------------------------------------------------


def _lines(text: str, indent: str = "   ") -> list[str]:
    return textwrap.wrap(text, width=72, initial_indent=indent, subsequent_indent=indent,
                         break_long_words=True, break_on_hyphens=False) or [indent]


def _item_text(number: int, item: Mapping[str, Any]) -> list[str]:
    if item["subject"] == "claim":
        return [f"{number}. Decision needed", *_lines(item["text"])]
    head = [f"{number}. {item['source_kind']} source: {item['source_ref']}"]
    body = [*_lines(f'Quote: "{item["quote"]}"'), *_lines(f"For claim: {item['claim']}")]
    if item.get("reason"):
        body += _lines("Why no deterministic check: " + item["reason"])
    return head + body


def form(items: Sequence[Mapping[str, Any]], timeout: float) -> tuple[str, dict[str, Any]]:
    """Map the batch to one flat MCP form: per item an enum decision and an optional note or answer."""
    minutes = f"{timeout / 60:g} min" if timeout >= 60 else f"{timeout:g} sec"
    message = ["Empirica · evidence that needs you  (" + minutes + ")",
               "Decide each item. Leave it on Later to decide another time.",
               "Accept = send these decisions · Decline/Esc = send none", ""]
    properties: dict[str, Any] = {}
    for number, item in enumerate(items, 1):
        message += _item_text(number, item)
        claim = item["subject"] == "claim"
        properties[f"d{number}"] = {
            "type": "string", "title": f"{number}. " + ("Your decision" if claim else "Is the source sound?"),
            "enum": list(CHOICES[item["subject"]]), "default": "later"}
        properties[f"n{number}"] = {
            "type": "string", "title": f"{number}. " + ("Your answer (optional)" if claim
                                                         else "Note to the author (optional)"),
            "maxLength": _limit(item["subject"])}
    return "\n".join(message), {"type": "object", "properties": properties, "required": []}


def answers_from_form(answer: object, items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]] | None:
    """Translate an accepted MCP form answer to ``[{subject, id, choice, note}]``; None grants nothing."""
    if not isinstance(answer, Mapping) or answer.get("action") != "accept":
        return None
    content = answer.get("content")
    if not isinstance(content, Mapping):
        return None
    allowed = {f"{prefix}{n}" for n in range(1, len(items) + 1) for prefix in "dn"}
    if not set(content) <= allowed:
        return None
    rows = []
    for number, item in enumerate(items, 1):
        choice, note = content.get(f"d{number}", "later"), content.get(f"n{number}")
        if choice not in CHOICES[item["subject"]] or (note is not None and not isinstance(note, str)):
            return None
        rows.append({"subject": item["subject"], "id": item["id"], "choice": choice, "note": note or None})
    return rows


class HostConfirmation:
    """Claude mediation of one ``report_convergence``; ``elicit`` is None without a form."""

    def __init__(self, profile: str, *, elicit: Callable[[str, dict], dict | None] | None = None,
                 timeout: Callable[[], float] | None = None, dispatch: Callable[..., dict] = bridge.handle,
                 presentation_ingress: Callable[..., dict] = bridge.trusted_evidence_presentation,
                 confirmation_ingress: Callable[..., dict] = bridge.trusted_evidence_confirmation,
                 surface_ingress: Callable[..., dict] = bridge.trusted_human_surface):
        self.profile, self.elicit, self._timeout = profile, elicit, timeout
        self._dispatch, self._presentation, self._confirmation = dispatch, presentation_ingress, confirmation_ingress
        self._surface = surface_ingress
        self._unrecorded = False

    def observe(self, run_id: str) -> bool:
        """Before an evaluation: record whether a form can be shown to a human now. Never raises. A surface that could
        not be recorded as present is retried as absent; if even that is not recorded the mediator shows nothing for
        this call (a stale stored `human` can only change the wording of the route, never cause a pause: the Claude Stop
        does not pause for evidence at all). True when the core holds the latest observation."""
        can_present = self.elicit is not None
        recorded = self._record(run_id, can_present) or (can_present and self._record(run_id, False))
        self._unrecorded = not recorded
        return recorded

    def _record(self, run_id: str, can_present: bool) -> bool:
        try:
            return observe_surface(self.profile, run_id, can_present, ingress=self._surface)
        except Exception:  # noqa: BLE001 - an unrecorded observation is reported to the caller as such
            return False

    def __call__(self, result: dict, reevaluate: Callable[[], dict]) -> dict:
        """Mediate, then return the fresh evaluation whenever the batch was shown, whatever the human did: Later, cancel,
        timeout and a failing dialog grant nothing but spend the derivation, and the fresh response says so. The original
        response when nothing was shown. Mediation never grants and never hides the typed Block."""
        try:
            return reevaluate() if self._ask(result) else result
        except Exception:  # noqa: BLE001
            return result

    def _ask(self, result: dict) -> bool:
        """Show the core's batch once; True once a presentation was admitted (the human may then have answered or not)."""
        if presentable(result) is None or self.elicit is None or self._unrecorded:
            return False
        run_id = result["run"]["id"]
        shown = present(self.profile, run_id, dispatch=self._dispatch, ingress=self._presentation)
        if isinstance(shown, dict):
            return False
        receipt_id, items = shown
        try:
            message, schema = form(items, self._timeout() if self._timeout else 900)
            answers = answers_from_form(self.elicit(message, schema), items)
            submit(self.profile, run_id, receipt_id, items, answers, ingress=self._confirmation)
        except Exception:  # noqa: BLE001 - shown and spent; nothing is granted by a failure
            pass
        return True
