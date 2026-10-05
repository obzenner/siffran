"""Whether a research record's citation is verbatim from the repository file it names.

The skill requires every research record to carry a verbatim ``citation`` from the source its
``source_ref`` identifies (SKILL.md §3.1). For a ``code`` or ``docs`` source inside the workspace this
is checkable without judgement, so the core checks it when the record is submitted and the author
learns at once that a paraphrase is not a citation. Relevance and correctness stay with the
independent audit. A source outside the workspace (a URL or an absolute path) is not checked here.

``source_ref`` names one file, optionally with line ranges: ``path``, ``path:12``, ``path:12-40``,
``path:12-40,55-60``. The citation may join several excerpts with an ellipsis (``…`` or ``...``);
each must appear, in order, within the cited lines. Whitespace runs compare as one space.
"""
from __future__ import annotations

import re
from typing import Any

from .freshness import FreshnessContractError, ObservationState, validate_relative_posix_path

CHECKED_KINDS = frozenset({"code", "docs"})
#: Why a citation is refused: the ``problem`` parameter of ``research.citation_unverified``.
PROBLEMS = ("source_ref_form", "file_unavailable", "lines_out_of_range", "text_not_found")

_REF = re.compile(r"\A(?P<path>[^\s:;,]+)(?::(?P<lines>\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*))?\Z")
_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.)\s*")


def cited_file(source_kind: str, source_ref: str) -> str | None:
    """The workspace file a research record cites, or ``None`` when it is not checked here
    (another source kind, a URL, an absolute or home path). A ref that is checked but not one
    well-formed repository path is returned as-is, for ``citation_problem`` to refuse."""
    if source_kind not in CHECKED_KINDS:
        return None
    ref = source_ref.strip()
    if "://" in ref or ref.startswith(("/", "~")):
        return None
    match = _REF.match(ref)
    return match["path"] if match else ref


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
    """Why ``citation`` is not verbatim from the file ``source_ref`` names (one of ``PROBLEMS``), or
    ``None`` when it is. ``content``/``state`` are the workspace's observation of that file."""
    match = _REF.match(source_ref.strip())
    if match is None:
        return "source_ref_form"
    try:
        validate_relative_posix_path(match["path"])
    except FreshnessContractError:
        return "source_ref_form"
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
