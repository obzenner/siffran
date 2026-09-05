"""Deterministic, sanitised project and run identifiers for the global-state adapter (ADR-31).

The full run key is ``(project_id, run_id, generation)``. This module derives the two string halves
so they are (a) *deterministic* — the same input always maps to the same id, which is what lets a
resumed session find the run it started — and (b) *safe as a single filesystem segment* — no
``/``, no ``..``, no leading dot, so a hostile or accidental value can never escape the run store's
directory.

Project identity, per ADR-31:

* **Git project** — keyed on the resolved *Git common directory* (``git rev-parse
  --git-common-dir``). This is the one piece of the design that unifies linked worktrees (they all
  share one common dir) while keeping independent clones independent (each has its own). Keying on
  the worktree-specific ``--git-dir`` would re-split identity across worktrees, which is exactly
  what ADR-31 exists to prevent.
* **Non-git project** — keyed on the resolved anchor: the resolved input path itself. A project
  with no Git repository still gets one stable identity per directory.

Deliberately NOT consulted: any host-specific run store. ADR-31 removes the legacy anchor
that let a stray run directory define "this project"; identity now derives only from the repository
boundary or the resolved path, never from plugin debris.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

# A hash suffix disambiguates two distinct raw inputs that sanitise to the same readable prefix, so
# the id stays collision-resistant without the caller having to pre-hash. 16 hex chars of SHA-256
# is ample for a machine-local namespace.
_PROJECT_HASH_LEN = 16
_RUN_HASH_LEN = 12
_READABLE_PREFIX_MAX = 32
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _git_common_dir(path: Path) -> Path | None:
    """The resolved Git common directory for ``path``, or ``None`` if it is not inside a repo.

    ``--git-common-dir`` (not ``--git-dir``) is the whole point: for a linked worktree it names the
    shared ``.git``, so every worktree of one repository resolves to the same identity. The result
    may be relative to ``path`` (git prints it relative to its working directory), so it is joined
    onto ``path`` before resolving; an absolute result overrides the join under pathlib's rules.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=False,
        )
    except (OSError, ValueError):
        return None
    if completed.returncode != 0:
        return None
    raw = completed.stdout.strip()
    if not raw:
        return None
    return (path / raw).resolve()


def identity_anchor(path: Path) -> Path:
    """The resolved directory project identity is keyed on: the Git common dir if any, else the
    resolved path. Pure function of the filesystem — no clock, no environment — so it is stable
    across a resumed session (ADR-31)."""
    resolved = Path(path).resolve()
    common = _git_common_dir(resolved)
    return common if common is not None else resolved


def _sanitise(raw: str, *, hash_len: int, fallback: str) -> str:
    """A single safe path segment derived deterministically from ``raw``: a readable, traversal-free
    prefix plus a SHA-256 suffix. The suffix, not the prefix, carries uniqueness — two inputs that
    collapse to the same prefix still get distinct ids."""
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:hash_len]
    prefix = _UNSAFE.sub("-", raw).strip("-._").lower()[:_READABLE_PREFIX_MAX] or fallback
    return f"{prefix}-{digest}"


def project_id(path: Path | str) -> str:
    """Deterministic, sanitised project id for ``path`` (resolved Git common dir, else resolved
    anchor). Worktree-stable: two worktrees of one repository yield the same id (ADR-31)."""
    anchor = identity_anchor(Path(path))
    return _sanitise(str(anchor), hash_len=_PROJECT_HASH_LEN, fallback="project")


def run_id(raw: str) -> str:
    """Deterministic, sanitised run id for a caller-supplied ``raw`` identifier (e.g. a session id).
    Guarantees a single traversal-free segment regardless of what ``raw`` contains."""
    return _sanitise(raw or "", hash_len=_RUN_HASH_LEN, fallback="run")
