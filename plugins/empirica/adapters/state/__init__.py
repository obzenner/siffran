"""Machine-local ``RunRepository`` adapter for empirica operational state (ADR-31).

Binds :class:`core.ports.RunRepository` to a hardened home-namespace filesystem store. Public API:

    from adapters.state import (
        FilesystemRunRepository, GenerationAllocator, empirica_home,
        project_id, run_id, SymlinkRefused,
    )

* :class:`FilesystemRunRepository` — CAS-guarded JSON operational-state store, keyed on the full
  ``RunKey``, under ``EMPIRICA_HOME`` (default ``~/.empirica-plugin``).
* :class:`GenerationAllocator` — resolves which generation a session opens: active resumes in place,
  terminal/corrupt starts the next without overwriting.
* :func:`project_id` / :func:`run_id` — deterministic, sanitised identity (Git common dir unifies
  worktrees; non-git uses the resolved anchor).
"""
from .fsio import SymlinkRefused
from .identity import identity_anchor, project_id, run_id
from .repository import FilesystemRunRepository, GenerationAllocator, empirica_home

__all__ = [
    "FilesystemRunRepository",
    "GenerationAllocator",
    "SymlinkRefused",
    "empirica_home",
    "identity_anchor",
    "project_id",
    "run_id",
]
