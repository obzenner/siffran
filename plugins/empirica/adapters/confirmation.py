"""Host mediation of the end-of-pass human confirmation (ADR-0067); the one flow every host shares.

The core blocks ``report_convergence`` with ``evidence.confirmation_required`` and names what waits.
A host then (1) asks the core for the batch's HITL document — the **write-first** step of ADR-0072
(PR 11): the first presentation carries ``document_digest: None``, the explicit fetch PR 10
staged, and is refused with ``evidence.document_mismatch``, nothing spent, the refusal's private
result carrying the core-rendered ``document: {text, digest}`` for the batch the ``key`` names —
verifies the pair, publishes the bytes **once** through the write-once sidecar beside the run's
own record, and repeats the same presentation with the digest —, then (2) asks, one decision per
item (Confirm / Reject with an optional note / Later for a record; Approve / Reject with an
optional answer / Later for a ``needs-decision`` claim), and (3) submits one receipt with the
decisions, carrying the digest the presentation recorded (the core requires the confirmation to
name what its own presentation showed). **Later is an omitted item.** There is no "approve all".

This module decides no policy. It builds the batch from the run's own dossier, maps the human's answers
to the receipt the core defines, and fails closed: a cancelled, timed-out, malformed or missing answer
submits nothing beyond the presentation already spent, and grants nothing. Claude drives it with an MCP
form; Pi drives the same ``present``/``submit`` through its private bridge and shows the items with its
own dialogs.

The forms are short (ADR-0072 "What the forms keep"): each item's source line and its quote (the
quote is the fact a human judges) stay in the message beside the decision enums and note fields;
the research records' per-claim texts and the reasons no deterministic check exists are in the
document, while a needs-decision claim's own text stays in the message in full — it is what the
human decides, exactly like a research item's quote — as are the terminal consent's residuals' own
count; the form points at the written file by its short digest plus a ``Details:`` link to the
observer UI's decision row (PR 16, A11) — or, when no live observer of this home answers, the
honest fallback naming the run and the exact absolute path — and Claude Code 2.1.304, which
shows only the first ``CLAUDE_VISIBLE_LINES`` lines of the message, finds them opened by exactly
those (PR 19, the Lima qualification of ``ac401739``, finding N7), with what each item decides
riding the full field description; many obligations ending now would leave unresolved.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import textwrap
from typing import Any
from uuid import uuid4

from adapters import bridge
from application import protocol
from core.confirmation import is_terminal, presentable as batch_presentable
from core.evaluation import TERMINAL_STATUSES, terminal_key
from core.projection import project_host_failure, project_prompt_error

#: A decision's answer is at most this long (``evidenceConfirmationPayload``); a research note may be longer.
_ANSWER_LIMIT = 2000
_NOTE_LIMIT = 2048
#: The one owner of the dialog's lengths (Pi renders what is built here). Claude cuts an elicitation field ``title`` at about
#: 45 visible columns and folds the long ``message`` after ``CLAUDE_VISIBLE_LINES`` lines, but renders a property's
#: ``description`` under the field in full: a title is at most ``TITLE_LIMIT`` columns with its "n. " prefix, and the item is
#: the description — the shared one here is at most ``DESCRIPTION_LIMIT`` and carries nothing of the quote (Pi's own dialog
#: renders the quote itself); Claude's :func:`form` additionally composes a host-only description beside it that repeats what
#: the human decides **whole** (the full quote and, when the title cut it, the full source; a decision claim's full text; the
#: terminal consent's residual count), without editing the items. The message keeps the full record, wrapped at ``_WIDTH``.
TITLE_LIMIT = 40
DESCRIPTION_LIMIT = 200
#: Claude Code 2.1.304 shows only this many lines of an elicitation ``message`` and folds the
#: rest as ``… (+N more lines)`` — nothing expands it in place (``ctrl+o`` opens the transcript
#: instead) — while a property's ``description`` renders under the field in full (Lima
#: qualification of ``ac401739``, finding N7). So every Claude form keeps exactly these first
#: lines to the heading, the bound document's ``Details:`` line and its copy surface (the short
#: digest beside a live observer's verified link, or the fallback's exact absolute ``File:`` path)
#: with no blank line among them; what the human decides per item rides the description
#: :func:`form` composes beside the items; and the message still keeps the full record below,
#: for transcripts, logs and hosts that do not fold.
CLAUDE_VISIBLE_LINES = 3
_WIDTH = 72
_PREFIX = 4  # "64. ": a batch holds at most 64 items
NOTE_TITLE = "Note (optional)"
#: The short digest length (ADR-0072 Q4): twelve hex — the scan length the repository's identifiers
#: already use (``application.observer_index.ID_SIZE``), kept a local literal for the same reason
#: as ``adapters/governance.py``'s: this module must not inherit the observer identity family
#: into the host-confirmation runtime identity.
_SHORT_BODY = 12
#: The command that opens this host's observer UI (README.md "Usage"): the fallback Details line
#: names only this host's command (A11) — Pi names its own (``/empirica-ui``) in its own dialog
#: sources, and the cross-host caption pin (``tests/v2/test_evidence_hosts.py``) holds the words.
_UI_COMMAND = "/empirica:ui"
#: The refusal that carries the fetch (PR 10's staging: ``null`` is the explicit fetch).
_MISMATCH = "evidence.document_mismatch"

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
    """The short field title of a research item (its source) and its shared description: the bare
    claim ids the record was proposed for and the question it is asked — and nothing of the
    quote (PR 16, A12: the quote is shown once, as `Quote: …` — the fact judged). The
    per-claim context ("For claim …", the claim texts) lives in the document (ADR-0072, the PR
    12 clarification; X6, Copilot #52). Both hosts read the same text — Pi keeps exactly this
    description (its own dialog renders the quote itself); the PR-19 amendment (finding N7 of
    the Lima qualification of ``ac401739``) makes Claude's :func:`form` compose the whole quote —
    and the whole source when this title cut it — into a host-only description beside this text,
    so the quote still shows exactly once per host and the shared items stay untouched."""
    ids = _claim_list([claim["id"] for claim in item["claims"]])
    question = "Is the observation right?" if item["class"] == "observed" else "Does the page say this?"
    item["title"] = _fit(short_ref(item["source_ref"]), TITLE_LIMIT - _PREFIX)
    item["description"] = f"{ids} · {question}"
    item["note_title"] = NOTE_TITLE


def presentable(result: object) -> dict[str, Any] | None:
    """The run view's ``confirmation_batch`` when a human can be shown it now, else ``None``.

    The core projects the batch whatever else blocks the run and says why it cannot be shown
    (``prompt_error``); the host presents exactly when there is a batch and no error, or the only thing against it is that
    it was already shown (``already_presented``): this runs only inside an explicit ``report_convergence``, which may ask
    again, and the core's interaction ceiling (``interaction_limit``) bounds it. It decides nothing."""
    run = result.get("run") if isinstance(result, dict) else None
    batch = run.get("confirmation_batch") if isinstance(run, dict) else None
    return batch if batch_presentable(batch) else None


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


def _conflict(stored: Mapping[str, Any]) -> bool:
    """The batch-key conflict: the one condition that re-enters the single retry, exactly as before
    PR 11 — that the flow is now two calls changes neither the budget (still one) nor what a
    conflict means: the batch the presentation named is no longer the current one."""
    return stored.get("type") == "Fault" and stored.get("code") == "conflict"


def _default_documents():
    """The sidecar store under the real `$EMPIRICA_HOME`, built at the presentation, not at import:
    the one-shot Pi private bridge processes that serve the operations with no document to write
    must not pay the sidecar chain's import (the PR 09 measurement). Tests inject their own store."""
    from adapters.state.hitl import HitlDocuments  # lazy: see the docstring
    return HitlDocuments()


def _bound_document(run_id: str, refused: Mapping[str, Any], store) -> dict[str, Any] | None:
    """The write-first gate (ADR-0072, PR 11): a fetch refusal must be ``evidence.document_mismatch``
    carrying exactly the core-rendered ``{text, digest}`` of the batch the fetch named. Publish
    the bytes **once** through :func:`adapters.state.hitl.publish` — the one bound helper
    (review-2 m1) that verifies, parses and object-checks the pair before the write — and return
    the shown-document record: the full ``digest``, the written file's absolute ``path``, and — a
    terminal batch only — ``residuals``, the count of obligations ending now would leave
    unresolved, **read out of the written document itself** (via the helper's parsed document)
    and never recomputed (the governance priors' precedent). ``None`` means nothing can be shown:
    the document was absent, malformed, digest-inconsistent, or the storage refused the write —
    nothing was spent and the second call never happens."""
    from adapters.state.fsio import SymlinkRefused
    from adapters.state.hitl import DocumentMismatch, HardLinkUnsupported, publish  # lazy, as above
    reasons = refused.get("reasons")
    if (refused.get("type") != "Block" or not isinstance(reasons, list) or not reasons
            or reasons[0].get("code") != _MISMATCH):
        return None
    try:
        published = publish(store, run_id, refused.get("document"))
    except (DocumentMismatch, SymlinkRefused, HardLinkUnsupported, OSError, ValueError):
        return None
    if published is None:
        return None
    record = {"digest": published.digest, "path": str(published.path)}
    terminal = published.parsed.get("terminal")
    if isinstance(terminal, Mapping) and isinstance(terminal.get("residuals"), list):
        record["residuals"] = len(terminal["residuals"])
    return record


def present(profile: str, run_id: str, *, dispatch: Callable[..., dict] = bridge.handle,
            ingress: Callable[..., dict] = bridge.trusted_evidence_presentation,
            documents: object = None, terminal: Mapping[str, Any] | None = None) -> tuple[str, list[dict[str, Any]], dict[str, Any]] | Outcome:
    """Record the presentation of the core's current batch, write-first, then return
    ``(receipt_id, items, {digest, path})``.

    The batch is read from the run, or is the core's own terminal batch of the evaluation in hand
    (``terminal``, checked below); the presentation names its key, and the core admits it only while
    that is still the current batch, so what is recorded as shown is exactly what the human sees.
    The presentation is now two calls (PR 11): the **fetch** (`document_digest: None`) — refused with
    ``evidence.document_mismatch``, nothing spent, its private result the core-rendered document of
    the batch the key named — then, once the document's pair is verified and its bytes published
    once beside the run's own record, the same presentation again with its digest. The receipt, the
    items and the document's ``{digest, path}`` come back only on the accepted second call, so two
    batches can never mix: the core re-checks the digest against the batch the key names and would
    refuse the receipt (this module adds no second check of its own).

    The one-retry behaviour for a conflicting batch key is unchanged: a genuine batch conflict on
    either call re-enters exactly the one retry of before, while the fetch's *designed* refusal —
    the mismatch that carries the document — is not a conflict: it neither consumes nor triggers
    the retry and spends nothing. A second change shows nothing and spends nothing. Anything else
    (the core refuses: already presented, interaction limit, nothing pending; or the ingress faults,
    or raises; or the document is absent, inconsistent, or the storage refuses the write) is
    ``PRESENTATION_FAILED``: the caller shows nothing, and the core's own re-evaluation words the
    refusal it knows (``already_presented`` and ``interaction_limit`` outrank the host's report).

    ``terminal`` is the terminal batch of the evaluation in hand (its ``confirmation_batch`` when that
    carries ``terminal``): the end of a deliberative run is asked by the evaluation that reaches it,
    so no later read of the run shows it. It is shown once, and only when it is the core's own: its
    status is one the core ends a run as and its key is the key of its status and digest
    (``terminal_key``), so a status or digest the core never derived is never rendered; the
    core still admits the presentation only while that key is current."""
    if terminal is not None and not (is_terminal(terminal) and genuine_terminal(terminal)):
        return PRESENTATION_FAILED
    store = documents if documents is not None else _default_documents()
    from adapters.observer.discovery import document_details  # lazy: the presentation pays it, once per presentation
    for _attempt in range(1 if terminal else 2):
        batch = terminal if terminal else presentable(_call(profile, run_id, {"type": "GetRun"}, dispatch))
        argument = _call(profile, run_id, {"type": "GetArgument"}, dispatch).get("argument") if batch else None
        items = build_items(argument, batch) if batch and isinstance(argument, dict) else []
        if not items:
            return PRESENTATION_FAILED
        receipt_id = uuid4().hex
        # the fetch: nothing spent, and its refusal's private result is the document for this very key
        fetched = ingress(profile, run_id, {"run_id": run_id, "receipt_id": receipt_id,
                                            "key": batch["key"], "document_digest": None}).get("result", {})
        if _conflict(fetched):
            continue
        document = _bound_document(run_id, fetched, store)
        if document is None:
            return PRESENTATION_FAILED
        # PR 16 (A10/A11): the one Details link per presentation — presentation-only fields next
        # to the digest the receipt binds (the core never sees a URL): a live observer's deep
        # link to the decision, or the honest fallback (the run's compact id, and the exact
        # absolute path, which each host's own form builder lines — O5: the path is dropped
        # only when a verified link is shown).
        document = {**document, **document_details(getattr(store, "home", None), run_id, document)}
        # the same presentation again, bound to the document the human will read
        stored = ingress(profile, run_id, {"run_id": run_id, "receipt_id": receipt_id,
                                          "key": batch["key"],
                                          "document_digest": document["digest"]}).get("result", {})
        if stored.get("type") == "Allow":
            return receipt_id, items, document
        if not _conflict(stored):
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
           document_digest: str | None = None) -> dict[str, Any] | None:
    """Submit one receipt for the answers; ``None`` when they grant nothing (Later, cancel, malformed).

    ``document_digest`` repeats the digest the presentation's own document recorded — the wire
    requires the member (PR 13, ADR-0072), and the core refuses (``evidence.document_mismatch``,
    nothing admitted) a value that is not the one its own presentation recorded: ``None`` here is
    the fetch, not a silent legacy shape, so a caller that never fetched its document is refused
    in-domain before anything is admitted."""
    chosen = decisions(items, answers)
    if not chosen:
        return None
    payload = {"run_id": run_id, "receipt_id": receipt_id, "decisions": chosen,
               "document_digest": document_digest}
    return ingress(profile, run_id, payload).get("result", {})


# ---- the Claude form ------------------------------------------------------------------------


def _lines(text: str, indent: str = "   ") -> list[str]:
    return textwrap.wrap(text, width=_WIDTH, initial_indent=indent, subsequent_indent=indent,
                         break_long_words=True, break_on_hyphens=False) or [indent]


def _item_text(number: int, item: Mapping[str, Any], document: Mapping[str, Any]) -> list[str]:
    """What the message shows of one item (ADR-0072 "What the forms keep"): a research item keeps
    its source line and its quote — the quote is the fact judged — while its per-item claim context
    and its reason no deterministic check exists are in the document. A decision claim's own text
    is what the human decides — exactly like a research item's quote — so it stays in the message
    in full (only the research records' per-claim context moved); a terminal item keeps its decision
    fully worded and names how many obligations ending now would leave unresolved, read out of the
    written document."""
    if item["subject"] == "claim":
        if item.get("terminal"):
            return [f"{number}. Decision needed", *_lines(item["text"]), *_residual_lines(document)]
        return [f"{number}. Decision needed", *_lines(item["text"])]
    head = [f"{number}. {item['source_kind']} source: {item['source_ref']}"]
    return head + _lines(f'Quote: "{item["quote"]}"')


def _residual_lines(document: Mapping[str, Any]) -> list[str]:
    """The terminal consent's count line: the residuals live in the document; their count is shown
    when it is known (``document["residuals"]``, read out of the written file by ``present``)."""
    left = document.get("residuals")
    if not isinstance(left, int) or isinstance(left, bool):
        return []
    if not left:
        return _lines("Nothing would stay unresolved; the record is in the document.")
    word = "obligation" if left == 1 else "obligations"
    return _lines(f"{left} unresolved {word} would remain — they are listed in the document.")


def _document_lines(document: Mapping[str, Any]) -> tuple[str, ...]:
    """The bound document's Details (PR 16, A11): a live observer's link to the run's decision
    in the UI — plus the document's short digest — or, when no observer of this home answers, the
    honest fallback that keeps the exact absolute path (O5: the path is dropped only when a
    verified link is shown). The ``File:`` line is the one line exempt from wrapping and
    truncation: the path is the copy-paste surface and a wrapped path is a corrupted paste."""
    short = f"Document {document['digest'][:7 + _SHORT_BODY]}"
    url = document.get("url")
    if isinstance(url, str) and url:
        return (f"Details: {url}", short)
    run12 = document.get("run12")
    # Follow-up (review 16-1): one short arrow line, at most 76 columns, never wrapping
    # mid-phrase (the old two-line fallback broke after "Human" at the wrap width).
    where = (f"{_UI_COMMAND} → run {run12} → Human decisions"
             if run12 else f"{_UI_COMMAND} → Human decisions")
    return (*textwrap.wrap(f"Details: {where}", width=_WIDTH, break_long_words=False,
                           break_on_hyphens=False),
            short, f"File: {document['path']}")


def _field_description(item: Mapping[str, Any], document: Mapping[str, Any]) -> str:
    """Claude's own field description (PR 19, finding N7 of the Lima qualification of
    ``ac401739``): Claude Code 2.1.304 folds the message after ``CLAUDE_VISIBLE_LINES``
    lines but renders a property's description under the field in full, so what the human
    decides rides it **whole** — a research item's entire quote and, when the field's short
    title cut it, its entire ``Source:`` reference; a decision claim's entire text; the terminal
    consent's sentence plus the residual count read out of the written document. Composed
    beside the items, never into them: the shared items every host relays (and Pi's dialogs
    read) keep the shared, truncated description, so Pi's output stays byte-identical; the
    message still carries the full record below the visible lines, so a quote may appear twice
    — on Claude only this description copy is visible."""
    if item["subject"] == "claim":
        if not item.get("terminal"):
            return f"{item['id']} · {item['text']}"
        left = document.get("residuals")
        if not isinstance(left, int) or isinstance(left, bool):
            return item["description"]  # no count was read: no suffix is invented
        suffix = ("Nothing would stay unresolved" if not left else
                  f"{left} unresolved {'obligation' if left == 1 else 'obligations'} would remain")
        return f"{item['description']} · {suffix}"
    description = item["description"]
    if len(short_ref(item["source_ref"])) > TITLE_LIMIT - _PREFIX:
        description += f" Source: {item['source_ref']}"  # the full reference the title folded away
    return f'{description} Quote: "{item["quote"]}"'


def form(items: Sequence[Mapping[str, Any]], timeout: float,
         document: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Map the batch and its written document to one flat MCP form: per item an enum decision and
    an optional note or answer, plus the document's short digest and absolute path.

    PR 19 (finding N7 of the Lima qualification of ``ac401739``): Claude Code 2.1.304 shows
    only the first ``CLAUDE_VISIBLE_LINES`` lines of the message and folds the rest, so the
    form opens with the heading, the document's ``Details:`` line and its copy surface — the
    short digest beside a live observer's verified link (the path dropped, O5), or the
    fallback's exact absolute ``File:`` path (the copy surface when there is nothing to click,
    the digest staying below the items) — with no blank line among them; the items, the
    document's remaining line and the legend follow below, the message keeping the full record
    (items first: whatever is folded away still carries the content, never the instructions
    the visible window would otherwise spend itself on). The fields' descriptions, rendered in
    full by Claude, carry what the human decides per item, composed without editing them."""
    minutes = f"{timeout / 60:g} min" if timeout >= 60 else f"{timeout:g} sec"
    ending = len(items) == 1 and bool(items[0].get("terminal"))
    heading = "end this run without converging?" if ending else "evidence that needs you"
    detail_lines = list(_document_lines(document))
    if isinstance(document.get("url"), str) and document["url"]:
        # linked: the visible window closes on the short digest; nothing remains below
        visible_details, below_details = (detail_lines[:CLAUDE_VISIBLE_LINES - 1],
                                         detail_lines[CLAUDE_VISIBLE_LINES - 1:])
    else:
        # fallback: the exact absolute path is the copy surface (the one line exempt from
        # wrapping); the short digest stays below the items, by caption, never by position
        visible_details, below_details = detail_lines[:1] + detail_lines[-1:], detail_lines[1:-1]
    message = [f"Empirica · {heading}  (" + minutes + ")", *visible_details]
    properties: dict[str, Any] = {}
    for number, item in enumerate(items, 1):
        message += _item_text(number, item, document)
        properties[f"d{number}"] = {
            "type": "string", "title": f"{number}. {item['title']}",
            "description": _field_description(item, document),
            "enum": list(CHOICES[item["subject"]]), "default": "later"}
        properties[f"n{number}"] = {
            "type": "string", "title": f"{number}. {item['note_title']}", "maxLength": _limit(item["subject"])}
    # The items come first: a host that folds the message shows its first lines, which carry the content, not the instructions.
    message += [""]
    if not ending:
        message += ["Claim texts and their no-deterministic-check reasons: in the document."]
    message += [*below_details,
                "",
                "Decide each item. Leave it on Later to decide another time.",
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
                 surface_ingress: Callable[..., dict] = bridge.trusted_human_surface,
                 documents: object = None):
        self.profile, self.elicit, self._timeout = profile, elicit, timeout
        self._dispatch, self._presentation, self._confirmation = dispatch, presentation_ingress, confirmation_ingress
        self._surface = surface_ingress
        # PR 11 (ADR-0072 write-first): the shared ``present`` publishes the batch's document once
        # through the sidecar store under the real $EMPIRICA_HOME before the form is shown; tests
        # inject a store under a temporary machine home (``None`` means: build it at the call).
        self._documents = documents
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
        answered or not), ``PRESENTATION_FAILED`` when it could not be recorded or the submission was refused: a Block
        or Fault admitted no answer, so nobody's answer was recorded (review-2 fix 14, decision 7c)."""
        batch = presentable(result)
        if batch is None or self.elicit is None or self._unrecorded:
            return None
        run_id = result["run"]["id"]
        try:
            shown = present(self.profile, run_id, dispatch=self._dispatch, ingress=self._presentation,
                            documents=self._documents, terminal=batch if is_terminal(batch) else None)
        except Exception:  # noqa: BLE001 - nothing was recorded as shown
            return PRESENTATION_FAILED
        if isinstance(shown, Outcome):
            return shown
        receipt_id, items, document = shown
        try:
            message, schema = form(items, self._timeout() if self._timeout else 900, document)
            answers = answers_from_form(self.elicit(message, schema), items)
            stored = submit(self.profile, run_id, receipt_id, items, answers, ingress=self._confirmation,
                            document_digest=document["digest"])
        except Exception:  # noqa: BLE001 - shown and spent; nobody's answer was recorded
            return PRESENTATION_FAILED
        if isinstance(stored, Mapping) and stored.get("type") in {"Block", "Fault"}:
            # The submission was refused (a stale receipt, a moved document, a refused transport):
            # the presentation is shown and spent, but no answer landed, so the fresh evaluation must
            # surface the host's own failure (`host_failed`, recovered only by showing the batch
            # again) — never word the refused attempt as the human's decline (review-2 fix 14).
            return PRESENTATION_FAILED
        return OK
