"""Git shadow-ref adapters for the empirica knowledge plane (ADR-31).

The :class:`~empirica.adapters.git.artifact_repo.GitArtifactRepository` stores a run's claims and
evidence as immutable, content-addressed Git objects under ``refs/empirica/*``. It uses only Git
plumbing (``hash-object``, ``mktree``, ``commit-tree``, ``update-ref``), operates through the
resolved common directory so linked worktrees share one namespace, and never touches the user's
HEAD, index, or worktree.
"""
from .artifact_repo import ArtifactCollision, GitArtifactRepository

__all__ = ["ArtifactCollision", "GitArtifactRepository"]
