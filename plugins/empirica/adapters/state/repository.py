"""Filesystem ``RunRepository`` over a machine-local home namespace (ADR-31).

This is the concrete global-state adapter: it satisfies :class:`core.ports.RunRepository` by shape,
storing each run's single operational-state document as one JSON file, guarded by optimistic
concurrency. It owns none of the schema — the value is any JSON-serialisable object the caller hands
it — and it never touches the knowledge plane (claims/evidence live behind ``ArtifactRepository``).

Layout under the home root (``EMPIRICA_HOME`` or ``~/.empirica-plugin``):

    <home>/projects/<project_id>/runs/<run_id>/gen-<generation>/run.json

The revision is the SHA-256 of the file's exact bytes: an opaque content-hash token (ADR-31
explicitly allows a content hash), so a compare-and-set is a genuine cross-process CAS — the write
lands only if the file on disk still hashes to the revision the caller read. Absence and corruption
are kept distinct as ADR-31 requires: a missing file is ``ABSENT`` (safe to create), an
undecodable one is ``Corrupt`` (must fail closed, never silently overwritten).

No host-specific runtime directory is read and there is no legacy fallback (ADR-31).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path

from core.records import ABSENT, Conflict, Corrupt, Present, Read, Revision, RunKey

from . import fsio

_DEFAULT_HOME = ".empirica-plugin"
_HOME_ENV = "EMPIRICA_HOME"
_DOC_NAME = "run.json"
_GEN_PREFIX = "gen-"
# A RunKey's string halves are used as single path segments. They should already be sanitised (see
# ``identity``), but the port accepts arbitrary strings, so the adapter validates rather than trust:
# a segment with a separator or ``..`` is a traversal attempt and is refused, not normalised away.
_SAFE_SEGMENT = re.compile(r"\A[A-Za-z0-9_.-]+\Z")


def empirica_home() -> Path:
    """The machine-local root: ``$EMPIRICA_HOME`` if set (and non-empty), else
    ``~/.empirica-plugin`` (ADR-31). Read fresh each call so a test can redirect it per case."""
    override = os.environ.get(_HOME_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / _DEFAULT_HOME


def _reject_non_finite(_constant: str) -> object:
    raise ValueError("non-finite JSON constant")


def _safe_segment(value: str, field: str) -> str:
    if value in ("", ".", "..") or not _SAFE_SEGMENT.match(value):
        raise ValueError(f"unsafe {field} path segment: {value!r}")
    return value


def _revision_of(raw: bytes) -> Revision:
    return Revision(hashlib.sha256(raw).hexdigest())


class FilesystemRunRepository:
    """A :class:`core.ports.RunRepository` backed by hardened local files.

    Satisfies the port structurally (no inheritance): ``read``/``create``/``compare_and_set`` with
    the port's contract. The value type is any JSON-serialisable object; operational documents are
    JSON only (ADR-31).
    """

    def __init__(self, home: Path | None = None) -> None:
        self._home = Path(home) if home is not None else empirica_home()

    # --- layout ---------------------------------------------------------------

    def run_dir(self, project_id: str, run_id: str) -> Path:
        """The directory holding every generation of one run."""
        return (self._home / "projects" / _safe_segment(project_id, "project_id")
                / "runs" / _safe_segment(run_id, "run_id"))

    def _doc_path(self, key: RunKey) -> Path:
        if not isinstance(key.generation, int) or key.generation < 0:
            raise ValueError(f"generation must be a non-negative int: {key.generation!r}")
        return (self.run_dir(key.project_id, key.run_id)
                / f"{_GEN_PREFIX}{key.generation}" / _DOC_NAME)

    def generations(self, project_id: str, run_id: str) -> list[int]:
        """Existing generation numbers for a run, ascending. Empty if the run has no storage yet."""
        base = self.run_dir(project_id, run_id)
        if not base.is_dir():
            return []
        found: list[int] = []
        for child in base.iterdir():
            if child.is_dir() and child.name.startswith(_GEN_PREFIX):
                suffix = child.name[len(_GEN_PREFIX):]
                if suffix.isdigit():
                    found.append(int(suffix))
        return sorted(found)

    # --- RunRepository port ---------------------------------------------------

    def read(self, key: RunKey) -> Read[object]:
        """``Present(value, revision)`` | ``ABSENT`` | ``Corrupt(reason)``. Never raises for absence
        or corruption — both are values, so a caller fails closed on corruption without a try/except.
        (A symlinked target is neither and is still refused loudly by the IO layer.)"""
        raw = fsio.read_bytes(self._doc_path(key))
        if raw is None:
            return ABSENT
        decoded = self._decode(raw)
        if isinstance(decoded, Corrupt):
            return decoded
        return Present(decoded, _revision_of(raw))

    def create(self, key: RunKey, value: object) -> Revision:
        """Create a previously-absent document; return its first revision. First-writer-wins: if
        anything already occupies the path — a valid doc, a corrupt one, even a symlink — raise
        :class:`Conflict` rather than overwrite (ADR-31)."""
        path = self._doc_path(key)
        with fsio.lock(path):
            if os.path.lexists(path):  # lexists, so a planted symlink also blocks creation
                raise Conflict(key, None, "document already exists")
            raw = fsio.dump_json(value)
            fsio.atomic_write(path, raw)
            return _revision_of(raw)

    def compare_and_set(self, key: RunKey, value: object, expected: Revision) -> Revision:
        """Replace the value iff the stored revision equals ``expected``; return the new revision.
        Raise :class:`Conflict` and leave storage untouched if the key is absent, corrupt, or its
        revision differs — the caller must re-read and retry, never clobber a concurrent write."""
        path = self._doc_path(key)
        with fsio.lock(path):
            raw = fsio.read_bytes(path)
            if raw is None:
                raise Conflict(key, expected, "absent")
            if isinstance(self._decode(raw), Corrupt):
                raise Conflict(key, expected, "corrupt")
            current = _revision_of(raw)
            if current != expected:
                raise Conflict(key, expected, "revision mismatch")
            new_raw = fsio.dump_json(value)
            fsio.atomic_write(path, new_raw)
            return _revision_of(new_raw)

    # --- decoding -------------------------------------------------------------

    def _decode(self, raw: bytes) -> object | Corrupt:
        """Decode stored bytes into the operational document, or ``Corrupt`` with a reason.

        Any decode failure collapses to ``Corrupt`` rather than raising: corruption of a run is a
        value the caller fails closed on, distinct from absence (ADR-31). A non-object top level is
        corrupt too — the adapter's operational document is always a JSON object."""
        try:
            value = json.loads(raw.decode("utf-8"), parse_constant=_reject_non_finite)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return Corrupt(f"undecodable operational document: {exc}")
        if not isinstance(value, dict):
            return Corrupt("operational document must be a JSON object")
        return value


def _default_is_active(value: object) -> bool:
    """Empirica's active-run predicate: a document whose ``status`` is ``"active"``. Terminal
    statuses (``converged``/``stopped_*``) read as not active, so the generation allocator opens a
    fresh generation for them (ADR-19 lifecycle statuses, ADR-31 generations)."""
    return isinstance(value, dict) and value.get("status") == "active"


class GenerationAllocator:
    """Chooses the generation a session should use for ``(project_id, run_id)`` (ADR-31).

    The rule follows ADR-31's generation semantics: an *active* run is reopened at its own
    generation (resume in place); a *terminal or corrupt* run starts the *next* generation without
    touching the old one, so stale budgets/verdicts never become current and rollback stays possible
    by pointing back at the older generation.

    "Active" is injected (default: :func:`_default_is_active`) so the allocator carries no schema —
    corruption it detects itself via the repository read; the predicate only classifies a decoded
    value as active or not.
    """

    def __init__(self, repo: FilesystemRunRepository,
                 is_active: Callable[[object], bool] = _default_is_active) -> None:
        self._repo = repo
        self._is_active = is_active

    def resolve(self, project_id: str, run_id: str) -> RunKey | None:
        """Return the latest existing generation without creating or advancing one.

        Lifecycle hooks use this read-only lookup after StartRun.  In particular, a Stop or
        SessionStart event for a terminal run must not allocate a fresh generation.
        """
        gens = self._repo.generations(project_id, run_id)
        return RunKey(project_id, run_id, gens[-1]) if gens else None

    def allocate(self, project_id: str, run_id: str) -> RunKey:
        gens = self._repo.generations(project_id, run_id)
        if not gens:
            return RunKey(project_id, run_id, 1)
        top = gens[-1]
        key = RunKey(project_id, run_id, top)
        read = self._repo.read(key)
        if read is ABSENT:
            return key  # an empty generation slot is reusable
        if isinstance(read, Present) and self._is_active(read.value):
            return key  # active run resumes in place
        return RunKey(project_id, run_id, top + 1)  # terminal or corrupt → next, no overwrite
