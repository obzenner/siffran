"""Strict Empirica v2 functional core.

Public behavior is implemented by ``evaluation`` and ``projection``. This package exports only
the host-neutral records and ports used at composition boundaries; the removed v1 adjudicator
and decision hierarchy are intentionally not compatibility surfaces.
"""
from .ports import ArtifactRepository, RunRepository
from .records import ABSENT, Absent, Artifact, Conflict, Corrupt, Present, Read, Revision, RunKey

__all__ = [
    "ABSENT", "Absent", "Artifact", "ArtifactRepository", "Conflict", "Corrupt",
    "Present", "Read", "Revision", "RunKey", "RunRepository",
]
