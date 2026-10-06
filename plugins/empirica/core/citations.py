"""How a research record is verified, and whether a workspace citation is verbatim.

This module is the single owner of the verification class (ADR-0067). The class is derived from the
record's immutable ``source_kind`` and ``source_ref`` and never stored:

* ``verbatim`` — ``source_ref`` is a repository-relative workspace path (any source kind). The
  service checks the citation against the file when the record is submitted.
* ``retrievable`` — ``source_ref`` is an ``http(s)`` URL or an absolute / ``~`` path. Someone other
  than the service (the audit, or a human) retrieves it: the record's *retrieval kind* is ``web``
  for a URL and ``local`` for an absolute path.
* ``observed`` — a ``runtime`` record whose ``source_ref`` is neither. Only a human can confirm it,
  and it must say why no deterministic spike could establish it (``spike_unavailable``).

A ``code``, ``docs`` or ``web`` record whose ``source_ref`` is none of the three forms is refused, and so
is an absolute path that names a file inside the workspace: it must cite the repository-relative path.

A workspace ``source_ref`` names one file, optionally with line ranges: ``path``, ``path:12``,
``path:12-40``, ``path:12-40,55-60``. The citation may join several excerpts with an ellipsis
(``…`` or ``...``); each must appear, in order, within the cited lines. Whitespace runs compare as
one space. Relevance and correctness stay with the independent audit.
"""
from __future__ import annotations

import posixpath
import re
from typing import Any

from .freshness import FreshnessContractError, ObservationState, validate_relative_posix_path

VERBATIM, RETRIEVABLE, OBSERVED = "verbatim", "retrievable", "observed"
WORKSPACE, LOCAL, WEB = "workspace", "local", "web"
#: The retrieval kinds a host's auditor may be observed to cover (``invocation.audit_retrieval``).
RETRIEVAL_KINDS = (LOCAL, WEB, WORKSPACE)
#: Why a record is refused: the ``problem`` parameter of ``research.citation_unverified``.
PROBLEMS = ("source_ref_form", "file_unavailable", "lines_out_of_range", "text_not_found",
            "spike_reason_missing")

_REF = re.compile(r"\A(?P<path>[^\s:;,]+)(?::(?P<lines>\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*))?\Z")
_URL = re.compile(r"\Ahttps?://\S+\Z", re.IGNORECASE)
_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.)\s*")


def _workspace_path(source_ref: str) -> str | None:
    """The repository-relative file ``source_ref`` names, or ``None`` when it is not one."""
    match = _REF.match(source_ref.strip())
    if match is None:
        return None
    try:
        validate_relative_posix_path(match["path"])
    except FreshnessContractError:
        return None
    return match["path"]


def retrieval_kind(source_ref: str) -> str | None:
    """Where ``source_ref`` is retrieved from (``workspace``, ``local`` or ``web``), or ``None``."""
    ref = source_ref.strip()
    if _URL.match(ref):
        return WEB
    if ref.startswith(("/", "~")):
        return LOCAL
    return WORKSPACE if _workspace_path(ref) is not None else None


def verification_class(source_kind: str, source_ref: str) -> str | None:
    """The record's class, or ``None`` for a ``code``/``docs``/``web`` ref of no accepted form."""
    kind = retrieval_kind(source_ref)
    if kind == WORKSPACE:
        return VERBATIM
    if kind is not None:
        return RETRIEVABLE
    return OBSERVED if source_kind == "runtime" else None


def cited_file(source_kind: str, source_ref: str) -> str | None:
    """The workspace file the service must read to verify a ``verbatim`` record, else ``None``."""
    if verification_class(source_kind, source_ref) != VERBATIM:
        return None
    return _workspace_path(source_ref)


_SELECTOR = re.compile(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*")
_SUFFIX = re.compile(r":([0-9,\-]*)\Z")


def split_selector(ref: str) -> tuple[str, str | None] | None:
    """``(path, selector)`` for a ref, or ``None`` when its suffix is malformed or ambiguous.

    Only a trailing ``:N``, ``:N-M`` or comma-separated list of those is a line selector; everything before it is
    the path, colons included. A trailing ``:`` followed by nothing, or by digits, commas and dashes that do not form
    the grammar (``:12-``, ``:1,,2``), is refused rather than cut at an arbitrary colon."""
    ref = ref.strip()
    suffix = _SUFFIX.search(ref)
    if suffix is None:
        return ref, None
    if not _SELECTOR.fullmatch(suffix.group(1)):
        return None
    return ref[:suffix.start()], suffix.group(1)


def spelled_canonically(path: str) -> bool:
    """Whether a path is spelled in a way that cannot hide where it points: no ``.`` or ``..`` segments, no doubled
    slash, no leading ``//``, no trailing slash. Pure spelling; no filesystem is read (a symlink is the audit's job)."""
    return not path.startswith("//") and posixpath.normpath(path) == path


def noncanonical(source_ref: str) -> bool:
    """Whether an absolute or ``~`` ref is refused for its spelling: a malformed selector suffix, or a path (the
    whole of it, colons included) that is not spelled canonically."""
    ref = source_ref.strip()
    if _URL.match(ref) or not ref.startswith(("/", "~")):
        return False
    parts = split_selector(ref)
    return parts is None or not spelled_canonically(parts[0])


def _inside(source_ref: str, workspace_root: str | None) -> bool:
    """Whether an absolute ``source_ref`` names a file under the workspace root (its spelling, not its
    place, would otherwise skip the verbatim check)."""
    if workspace_root is None or retrieval_kind(source_ref) != LOCAL or not source_ref.strip().startswith("/"):
        return False
    parts = split_selector(source_ref)
    if parts is None:
        return False
    path = parts[0]
    root = workspace_root.rstrip("/")
    return path == root or path.startswith(root + "/")


def submission_problem(source_kind: str, payload: Any, content: Any, state: ObservationState,
                       workspace_root: str | None = None) -> str | None:
    """Why a research payload is refused at submission (one of ``PROBLEMS``), or ``None``.

    ``content``/``state`` are the workspace's observation of the cited file; they matter only to a
    ``verbatim`` record. ``workspace_root`` is where the run's workspace lives, when known: an absolute
    ``source_ref`` inside it is refused as ``source_ref_form`` (cite the repository-relative path)."""
    source_ref = payload["source_ref"]
    record_class = verification_class(source_kind, source_ref)
    if record_class is None or noncanonical(source_ref) or _inside(source_ref, workspace_root):
        return "source_ref_form"
    if record_class == OBSERVED:
        return None if payload.get("spike_unavailable", "").strip() else "spike_reason_missing"
    if record_class == VERBATIM:
        return citation_problem(source_ref, payload["citation"], content, state)
    return None


def _ranges(lines: str | None) -> list[tuple[int, int]]:
    if not lines:
        return []
    out = []
    for part in lines.split(","):
        first, _, last = part.partition("-")
        out.append((int(first), int(last or first)))
    return out


def _squash(text: str) -> str:
    return " ".join(text.split())


def citation_problem(source_ref: str, citation: str, content: Any, state: ObservationState) -> str | None:
    """Why ``citation`` is not verbatim from the workspace file ``source_ref`` names (one of
    ``PROBLEMS``), or ``None`` when it is. ``content``/``state`` are the workspace's observation."""
    match = _REF.match(source_ref.strip())
    if state is not ObservationState.PRESENT or content is None:
        return "file_unavailable"
    lines = content.decode("utf-8", errors="replace").splitlines()
    ranges = _ranges(match["lines"])
    if any(first < 1 or last < first or last > len(lines) for first, last in ranges):
        return "lines_out_of_range"
    chosen = [lines[first - 1:last] for first, last in ranges] or [lines]
    haystack = _squash("\n".join(line for block in chosen for line in block))
    fragments = [f for f in (_squash(f) for f in _ELLIPSIS.split(citation)) if f]
    if not fragments:
        return "text_not_found"
    at = 0
    for fragment in fragments:
        found = haystack.find(fragment, at)
        if found < 0:
            return "text_not_found"
        at = found + len(fragment)
    return None
