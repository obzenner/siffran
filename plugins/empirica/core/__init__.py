"""Strict Empirica v2 functional core.

Public behavior is implemented by ``evaluation`` and ``projection``. This package exports only
the host-neutral records and ports used at composition boundaries; the removed v1 adjudicator
and decision hierarchy are intentionally not compatibility surfaces.
"""
from . import claims
from .ports import ArtifactRepository, MigrationPort, RunRepository
from .records import (
    ABSENT, Absent, Artifact, Conflict, Corrupt, MigrationReport, Present, Read, Revision,
    RunKey,
)

__all__ = [
    "claims", "ABSENT", "Absent", "Artifact", "ArtifactRepository", "Conflict", "Corrupt",
    "MigrationPort", "MigrationReport", "Present", "Read", "Revision", "RunKey",
    "RunRepository",
]
