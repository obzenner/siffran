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
from dataclasses import dataclass
import textwrap
from typing import Any
from uuid import uuid4

from adapters import bridge
from application import protocol
from core.confirmation import is_terminal
from core.evaluation import TERMINAL_STATUSES, terminal_key
from core.projection import project_host_failure, project_prompt_error

#: A decision's answer is at most this long (``evidenceConfirmationPayload``); a research note may be longer.
_ANSWER_LIMIT = 2000
_NOTE_LIMIT = 2048
#: The one owner of the dialog's lengths (Pi renders what is built here). Claude cuts an elicitation field ``title`` at about
#: 45 visible columns (claude-5 Q5) and collapses the long ``message``, but renders a property's ``description`` under the
#: field: so a title is at most ``TITLE_LIMIT`` columns with its "n. " prefix, the item is the description (at most
#: ``DESCRIPTION_LIMIT``, its quote shortened with "…"), and the message keeps the full record, wrapped at ``_WIDTH``.
TITLE_LIMIT = 40
DESCRIPTION_LIMIT = 200
_WIDTH = 72
_PREFIX = 4  # "64. ": a batch holds at most 64 items
NOTE_TITLE = "Note (optional)"

#: choice -> the core outcome; ``later`` is deliberately absent (an omitted item).
_RESEARCH = {"confirm": "confirm", "reject": "reject"}
_CLAIM = {"approve": "approve", "reject": "reject"}
CHOICES = {"research": ("later", *_RESEARCH), "claim": ("later", *_CLAIM)}


@dataclass(frozen=True)
class Outcome:
    """What a mediation step did: ``failure`` is ``None`` when it did what it was asked, else the host's own failure
    (``core.confirmation.HOST_FAILURES``). A refusal the core already knows (already presented, interaction limit) is
    worded by the core's re-evaluation, not named here. A step that failed is reported on the same call; it is never a
    bare Boolean and never a silently unchanged result."""

    failure: str | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None


OK = Outcome()
SURFACE_UNRECORDED = Outcome("surface_unrecorded")
PRESENTATION_FAILED = Outcome("presentation_failed")


def _limit(subject: str) -> int:
    return _ANSWER_LIMIT if subject == "claim" else _NOTE_LIMIT


def _fit(text: str, room: int) -> str:
    return text if len(text) <= room else text[:max(1, room - 1)].rstrip() + "…"


def short_ref(ref: str) -> str:
    """A source as a person scans it: no scheme, no ``www.``, no trailing slash."""
    for scheme in ("https://", "http://"):
        if ref.lower().startswith(scheme):
            ref = ref[len(scheme):]
            break
    return (ref[4:] if ref.startswith("www.") else ref).rstrip("/")


def _claim_list(ids: Sequence[str]) -> str:
    return ", ".join(ids) if len(ids) <= 4 else ", ".join(ids[:3]) + f" +{len(ids) - 3}"


def _research_titles(item: dict[str, Any]) -> None:
    """The short field title of a research item (its source) and its description: the claims it is for, a prefix of the
    quote (the full quote stays in the message) and the question. Both hosts read the same text."""
    ids = _claim_list([claim["id"] for claim in item["claims"]])
    question = "Is the observation right?" if item["class"] == "observed" else "Does the page say this?"
    head, tail = f"For {ids} · Quote: \"", f"\" · {question}"
    item["title"] = _fit(short_ref(item["source_ref"]), TITLE_LIMIT - _PREFIX)
    item["description"] = head + _fit(item["quote"], DESCRIPTION_LIMIT - len(head) - len(tail) - 1).rstrip(" ") + tail
    item["note_title"] = NOTE_TITLE


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
    if is_terminal(batch):
        # The terminal step of a deliberative run (ADR-0063 amendment): one decision, no claim of the argument.
        terminal = batch["terminal"]
        status = terminal["status"]
        return [{"subject": "claim", "id": status, "digest": terminal["digest"], "terminal": True,
                 "text": f"End this run as {status}, without converging? Approve to end it now; Reject "
                         "or Later keeps it active and nothing is ended.",
                 "title": _fit(f"End the run as {status}?", TITLE_LIMIT - _PREFIX),
                 "description": f"Not converged. Approve ends the run now as {status}; Reject or Later keeps it active.",
                 "note_title": NOTE_TITLE}]
    for claim_id in batch.get("claim_ids", ()):
        if claim_id in text:
            items.append({"subject": "claim", "id": claim_id, "digest": text[claim_id]["wording_digest"],
                          "text": text[claim_id]["text"],
                          "title": _fit(f"Your decision on {claim_id}", TITLE_LIMIT - _PREFIX),
                          "description": _fit(f"{claim_id} · {text[claim_id]['text']}", DESCRIPTION_LIMIT),
                          "note_title": NOTE_TITLE})
    # One source quoted the same way for several claims is one question: the human reads the quote once, and the
    # answer is recorded as a decision for every record it groups (the receipt still decides per record).
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for record_id in batch.get("record_ids", ()):
        row = research.get(record_id)
        if row is None:
            continue
        key = (row["source_ref"], row["citation"])
        claim = {"id": row["claim_id"], "text": text.get(row["claim_id"], {}).get("text", "")}
        if key not in grouped:
            grouped[key] = {"subject": "research", "id": record_id, "record_ids": [], "claims": [],
                            "source_kind": row["source_kind"], "source_ref": row["source_ref"],
                            "quote": row["citation"], "class": row["verification"]["class"]}
            items.append(grouped[key])
        item = grouped[key]
        item["record_ids"].append(record_id)
        if all(claim["id"] != other["id"] for other in item["claims"]):
            item["claims"].append(claim)
        if row.get("spike_unavailable") and "reason" not in item:
            item["reason"] = row["spike_unavailable"]
    for item in items:
        if item["subject"] == "research":
            _research_titles(item)
    return items


def observe_surface(profile: str, run_id: str, can_present: bool, *,
                    ingress: Callable[..., dict] = bridge.trusted_human_surface) -> Outcome:
    """Tell the core whether this host can show a dialog to a human now; the latest observation counts. ``OK`` when the
    core took it (or already held it); ``SURFACE_UNRECORDED`` when the ingress faulted or raised, so the caller can
    fail closed and say so."""
    try:
        response = ingress(profile, run_id, {"run_id": run_id, "surface": "human" if can_present else "unavailable"})
    except Exception:  # noqa: BLE001 - an unrecorded observation is reported to the caller as such
        return SURFACE_UNRECORDED
    result = response.get("result", response) if isinstance(response, dict) else None
    return OK if isinstance(result, dict) and result.get("type") != "Fault" else SURFACE_UNRECORDED


def genuine_terminal(batch: Mapping[str, Any]) -> bool:
    """Whether a terminal batch is internally the core's: a status it ends runs as, and the key it derives from that status
    and digest. What a caller adds or edits breaks one of the two."""
    ended = batch["terminal"]
    return (ended.get("status") in TERMINAL_STATUSES and isinstance(ended.get("digest"), str)
            and batch.get("key") == terminal_key(ended["status"], ended["digest"]))


def present(profile: str, run_id: str, *, dispatch: Callable[..., dict] = bridge.handle,
            ingress: Callable[..., dict] = bridge.trusted_evidence_presentation,
            terminal: Mapping[str, Any] | None = None) -> tuple[str, list[dict[str, Any]]] | Outcome:
    """Record the presentation of the core's current batch, then return ``(receipt_id, items)``.

    The batch is read from the run, or is the core's own terminal batch of the evaluation in hand (``terminal``, checked
    below); the presentation names its key, and the core admits it only while that is still the current batch, so what is
    recorded as shown is exactly what the human sees. When the batch changed in between the host reads again and tries once more; a second change shows
    nothing and spends nothing. Anything else (the core refuses: already presented, interaction limit, nothing
    pending; or the ingress faults, or raises) is ``PRESENTATION_FAILED``: the caller shows nothing, and the core's
    own re-evaluation words the refusal it knows (``already_presented`` and ``interaction_limit`` outrank the
    host's report).

    ``terminal`` is the terminal batch of the evaluation in hand (its ``confirmation_batch`` when that carries
    ``terminal``): the end of a deliberative run is asked by the evaluation that reaches it, so no later read of the run
    shows it. It is shown once, and only when it is the core's own: its status is one the core ends a run as and its key is
    the key of its status and digest (``terminal_key``), so a status or digest the core never derived is never rendered; the
    core still admits the presentation only while that key is current."""
    if terminal is not None and not (is_terminal(terminal) and genuine_terminal(terminal)):
        return PRESENTATION_FAILED
    for _attempt in range(1 if terminal else 2):
        batch = terminal if terminal else presentable(_call(profile, run_id, {"type": "GetRun"}, dispatch))
        argument = _call(profile, run_id, {"type": "GetArgument"}, dispatch).get("argument") if batch else None
        items = build_items(argument, batch) if batch and isinstance(argument, dict) else []
        if not items:
            return PRESENTATION_FAILED
        receipt_id = uuid4().hex
        stored = ingress(profile, run_id, {"run_id": run_id, "receipt_id": receipt_id, "key": batch["key"]}).get("result", {})
        if stored.get("type") == "Allow":
            return receipt_id, items
        if not (stored.get("type") == "Fault" and stored.get("code") == "conflict"):
            return PRESENTATION_FAILED
    return PRESENTATION_FAILED


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
            for record_id in item.get("record_ids", [ident]):
                row: dict[str, Any] = {"subject": "research", "artifact_id": record_id, "outcome": _RESEARCH[choice]}
                if note:
                    row["note"] = note
                out.append(row)
            continue
        row = {"subject": "claim", "claim_id": ident, "claim_digest": item["digest"], "outcome": _CLAIM[choice]}
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
    return textwrap.wrap(text, width=_WIDTH, initial_indent=indent, subsequent_indent=indent,
                         break_long_words=True, break_on_hyphens=False) or [indent]


def _item_text(number: int, item: Mapping[str, Any]) -> list[str]:
    if item["subject"] == "claim":
        return [f"{number}. Decision needed", *_lines(item["text"])]
    head = [f"{number}. {item['source_kind']} source: {item['source_ref']}"]
    body = [*_lines(f'Quote: "{item["quote"]}"'),
            *(line for claim in item["claims"] for line in _lines(f"For claim {claim['id']}: {claim['text']}"))]
    if item.get("reason"):
        body += _lines("Why no deterministic check: " + item["reason"])
    return head + body


def form(items: Sequence[Mapping[str, Any]], timeout: float) -> tuple[str, dict[str, Any]]:
    """Map the batch to one flat MCP form: per item an enum decision and an optional note or answer."""
    minutes = f"{timeout / 60:g} min" if timeout >= 60 else f"{timeout:g} sec"
    ending = len(items) == 1 and bool(items[0].get("terminal"))
    heading = "end this run without converging?" if ending else "evidence that needs you"
    message = [f"Empirica · {heading}  (" + minutes + ")", ""]
    properties: dict[str, Any] = {}
    for number, item in enumerate(items, 1):
        message += _item_text(number, item)
        properties[f"d{number}"] = {
            "type": "string", "title": f"{number}. {item['title']}", "description": item["description"],
            "enum": list(CHOICES[item["subject"]]), "default": "later"}
        properties[f"n{number}"] = {
            "type": "string", "title": f"{number}. {item['note_title']}", "maxLength": _limit(item["subject"])}
    # The items come first: a host that collapses the message shows its first lines, which should carry the content.
    message += ["", "Decide each item. Leave it on Later to decide another time.",
                "Accept = send these decisions · Decline/Esc = send none"]
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

    def observe(self, run_id: str) -> Outcome:
        """Before an evaluation: record whether a form can be shown to a human now. Never raises. A surface that could
        not be recorded as present is retried as absent; if even that is not recorded the mediator shows nothing for
        this call and returns ``SURFACE_UNRECORDED`` for the caller to send with the evaluation, so the author is told
        nobody was asked. A stale stored `human` changes only the route's wording; when a Stop pauses is ADR-0068's rule.
        ``OK`` when the core holds the latest observation."""
        can_present = self.elicit is not None
        outcome = observe_surface(self.profile, run_id, can_present, ingress=self._surface)
        if not outcome.ok and can_present:
            outcome = observe_surface(self.profile, run_id, False, ingress=self._surface)
        self._unrecorded = not outcome.ok
        return outcome

    def __call__(self, result: dict, reevaluate: Callable[[str | None], dict]) -> dict:
        """Mediate, then return the fresh evaluation whenever the host tried to show the batch, whatever came of it. A
        batch the host could not present is evaluated again with ``host_failure``; when that evaluation itself fails,
        the host applies what it knows to the response in hand through the core's own rule (``project_host_failure``).
        The original response when there was nothing to ask. Mediation never grants and never hides the typed Block."""
        try:
            asked = self._ask(result)
        except Exception:  # noqa: BLE001 - nothing was recorded as shown
            asked = PRESENTATION_FAILED
        if asked is None:
            return result
        try:
            fresh = reevaluate(asked.failure)
            if fresh.get("type") in {"Allow", "Block"}:
                return fresh
        except Exception:  # noqa: BLE001 - the transport is down: fall through to what this call knows
            pass
        reasons = protocol.public_contract()["reasons"]
        return (project_host_failure(result, asked.failure, reasons) if asked.failure
                else project_prompt_error(result, "already_presented", reasons))

    def _ask(self, result: dict) -> Outcome | None:
        """Show the core's batch once. ``None`` when there was nothing to ask (no batch the core allows presenting, no
        form, or a surface that was not recorded), ``OK`` once a presentation was admitted (the human may then have
        answered or not), ``PRESENTATION_FAILED`` when it could not be recorded."""
        if presentable(result) is None or self.elicit is None or self._unrecorded:
            return None
        run_id = result["run"]["id"]
        try:
            batch = presentable(result)
            shown = present(self.profile, run_id, dispatch=self._dispatch, ingress=self._presentation,
                            terminal=batch if is_terminal(batch) else None)
        except Exception:  # noqa: BLE001 - nothing was recorded as shown
            return PRESENTATION_FAILED
        if isinstance(shown, Outcome):
            return shown
        receipt_id, items = shown
        try:
            message, schema = form(items, self._timeout() if self._timeout else 900)
            answers = answers_from_form(self.elicit(message, schema), items)
            submit(self.profile, run_id, receipt_id, items, answers, ingress=self._confirmation)
        except Exception:  # noqa: BLE001 - shown and spent; nothing is granted by a failure
            pass
        return OK
