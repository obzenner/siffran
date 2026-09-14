#!/usr/bin/env python3
"""The host-neutral empirica decision core (ADR-30).

A pure adjudication layer extracted from the empirica hooks: given host-neutral facts and injected
pure verdicts, it decides whether a run may stop and whether it converged, returning one typed
`Decision` — Allow, Block, Inert, or Fault. It names no hook event, emits no exit code, reads no
filesystem path, runs no git command, and prints no UI. A host adapter (NOT part of this package)
supplies the injections, maps the returned `Decision` onto the platform (a Stop-hook exit code, a
stderr message, a manifest write), and applies the persisted pass-budget cap.

Public API — the smallest surface a caller needs:

    from empirica_core import adjudicate, RunState
    from empirica_core import Allow, Block, Inert, Fault, ClaimReason, Decision
    from empirica_core import claims           # pure claim-graph state derivation

`adjudicate` is the entry point. `claims` is the extracted pure decision helper an adapter
composes to build `adjudicate`'s injected verdicts. The package is importable both as a package
(`from . import ...`) and by direct path-load; see `tests/test_core.py` for the reference wiring.
"""
from . import claims
from .convergence import CORRUPT_STATUS, RunState, adjudicate
from .decisions import Allow, Block, ClaimReason, Decision, Fault, Inert
from .ports import ArtifactRepository, MigrationPort, RunRepository
from .records import (
    ABSENT,
    Absent,
    Artifact,
    Conflict,
    Corrupt,
    MigrationReport,
    Present,
    Read,
    Revision,
    RunKey,
)

__all__ = [
    "adjudicate",
    "RunState",
    "CORRUPT_STATUS",
    "Decision",
    "Allow",
    "Block",
    "Inert",
    "Fault",
    "ClaimReason",
    "claims",
    "ABSENT",
    "Absent",
    "Artifact",
    "ArtifactRepository",
    "Conflict",
    "Corrupt",
    "MigrationPort",
    "MigrationReport",
    "Present",
    "Read",
    "Revision",
    "RunKey",
    "RunRepository",
]
