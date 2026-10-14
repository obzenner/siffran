"""When each run-state write happened: an append-only timeline beside each run generation (ADR-0070).

    <home>/projects/<project_id>/runs/<run_id>/gen-<n>/timeline.jsonl

One JSON line per successful write of that generation's ``run.json``, appended by the bridge adapter after the write
landed (``adapters.bridge._RecordingRuns``)::

    {"seq": 1, "previous_revision": null, "revision": "<sha256 of run.json>", "artifact_head": "<manifest id>",
     "status": "active", "plan_revision": 0, "proposal_digest": "<sha256>", "receipts": [], "at": 1790586000.0}

``seq`` counts the entries from 1; ``previous_revision`` is the revision the write replaced (``null`` for the create);
``artifact_head`` is the new state's ``committed_artifact_head_id``, the manifest that write committed; ``plan_revision``,
``proposal_digest`` and ``receipts`` are the configuration the written state holds (its governance revision, the digest
of its proposal and its receipt ids); ``at`` is the writer's wall clock in UTC epoch seconds.

The timeline is display data. It is machine-local, never in a Git ref, never read by a run's decisions and never part of
a digest: a time is a label (unsigned, it may skew or go backwards), and ordering stays on ``stamp_seq`` and history
order. Appends happen under ``fsio.lock`` and replace the file atomically. A reader tolerates a torn last line (the
entries before it stand); anything else malformed makes the whole timeline unknown (``None``), never zero.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from core.records import RunKey

from . import fsio
from .repository import FilesystemRunRepository, empirica_home

TIMELINE = "timeline.jsonl"
KEYS = frozenset({"seq", "previous_revision", "revision", "artifact_head", "status", "plan_revision", "proposal_digest",
                  "receipts", "at"})


def entry(seq: int, previous: str | None, revision: str, value: Any, at: float) -> dict[str, Any]:
    """The entry for one write: ``value`` is the state document it wrote. Its configuration (governance ``plan_revision``,
    ``proposal_digest`` and the ids of its receipts) is read from it as its ``status`` is, so a reader can tell which
    write proposed a configuration or recorded an answer to one; each is ``None`` when the document does not hold it."""
    doc = value if isinstance(value, Mapping) else {}
    head, status = doc.get("committed_artifact_head_id"), doc.get("status")
    gov = doc.get("governance") if isinstance(doc.get("governance"), Mapping) else {}
    plan, proposal, receipts = gov.get("plan_revision"), gov.get("proposal_digest"), gov.get("receipts")
    ids = ([r.get("id") for r in receipts] if isinstance(receipts, list)
           and all(isinstance(r, Mapping) and isinstance(r.get("id"), str) for r in receipts) else None)
    return {"seq": seq, "previous_revision": previous, "revision": revision,
            "artifact_head": head if isinstance(head, str) else None,
            "status": status if isinstance(status, str) else None,
            "plan_revision": plan if type(plan) is int else None,
            "proposal_digest": proposal if isinstance(proposal, str) else None,
            "receipts": ids, "at": float(at)}


def _token(value: Any) -> str | None:
    """A revision as stored: a ``Revision``'s token, or the string itself."""
    token = getattr(value, "token", value)
    return token if isinstance(token, str) else None


def _valid(item: Any, seq: int) -> bool:
    if not isinstance(item, dict) or set(item) != KEYS:
        return False
    at, previous = item["at"], item["previous_revision"]
    return (type(item["seq"]) is int and item["seq"] == seq
            and (previous is None or (isinstance(previous, str) and previous != ""))
            and isinstance(item["revision"], str) and item["revision"] != ""
            and (item["artifact_head"] is None or isinstance(item["artifact_head"], str))
            and (item["status"] is None or isinstance(item["status"], str))
            and (item["plan_revision"] is None or type(item["plan_revision"]) is int)
            and (item["proposal_digest"] is None or isinstance(item["proposal_digest"], str))
            and (item["receipts"] is None or (isinstance(item["receipts"], list)
                                              and all(isinstance(r, str) for r in item["receipts"])))
            and type(at) in (int, float) and math.isfinite(at))


def _reject_constant(_name: str) -> Any:
    raise ValueError("non-finite JSON constant")


def _complete(raw: bytes) -> bytes:
    """The whole lines of ``raw``: a torn last line (no newline yet) is dropped."""
    return raw[:raw.rfind(b"\n") + 1]


def parse(raw: bytes | None) -> tuple[dict[str, Any], ...] | None:
    """The entries of a timeline's bytes; ``None`` (unknown) when there are none recorded or any whole line is malformed."""
    if raw is None:
        return None
    entries = []
    for seq, line in enumerate(_complete(raw).splitlines(), start=1):
        try:
            item = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError):
            return None
        if not _valid(item, seq):
            return None
        entries.append(item)
    return tuple(entries)


def encode(entries) -> bytes:
    """Canonical bytes of ``entries``: one compact, sorted-key JSON line each."""
    return b"".join(json.dumps(e, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
                    for e in entries)


def appended(raw: bytes | None, previous: Any, revision: Any, value: Any, at: float) -> bytes:
    """``raw`` with one more entry. A torn last line is dropped first; a malformed timeline is not extended
    (``ValueError``): it would still read as unknown."""
    whole = _complete(raw or b"")
    entries = parse(whole)
    token = _token(revision)
    if entries is None or token is None or not token:
        raise ValueError("timeline is malformed or the revision is not a token")
    return whole + encode([entry(len(entries) + 1, _token(previous), token, value, at)])


class Timeline:
    """The timeline store under ``EMPIRICA_HOME``, one file per run generation, beside its ``run.json``."""

    def __init__(self, home: Path | None = None) -> None:
        self._runs = FilesystemRunRepository(Path(home) if home is not None else empirica_home())

    def path(self, key: RunKey) -> Path:
        return self._runs.generation_dir(key) / TIMELINE

    def append(self, key: RunKey, previous: Any, revision: Any, value: Any, at: float) -> None:
        """Record one write. Raises ``OSError`` or ``ValueError`` when it cannot (the caller decides what that costs)."""
        path = self.path(key)
        with fsio.lock(path):
            fsio.atomic_write(path, appended(fsio.read_bytes(path), previous, revision, value, at))

    def read(self, key: RunKey) -> tuple[dict[str, Any], ...] | None:
        """The generation's entries; ``None`` when unknown: missing, malformed, a refused symlink or an unsafe key."""
        try:
            return parse(fsio.read_bytes(self.path(key)))
        except (OSError, ValueError):
            return None
