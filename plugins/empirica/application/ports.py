#!/usr/bin/env python3
"""D5 application transport facts and ports (empirica 2.0)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.freshness import FileObservation, observations_digest


@dataclass(frozen=True, slots=True)
class QualificationAttemptRequest:
    """One charged run-scoped qualification attempt the host runs (Empirica 7.2 §5, E9): the run
    binding the attempt's ``/3`` record closes over, the approved revision's proposal digest,
    spike-image references and profile. Built by the application transaction from committed,
    validated run state — never author data — after the durable reservation, so the run binding
    the record carries is exactly the run generation and revision the attempt was admitted
    for."""

    project_id: str
    run_id: str
    generation: int
    attempt_id: str
    revision: int
    proposal_digest: str
    spike_images: tuple[str, ...]
    profile_id: str

    def __post_init__(self) -> None:
        for member in (self.project_id, self.run_id, self.attempt_id, self.proposal_digest):
            if type(member) is not str or not member:
                raise ValueError("qualification attempt request members are malformed")
        for member in (self.generation, self.revision):
            if type(member) is not int or member < 1:
                raise ValueError("qualification attempt request counters are malformed")
        if (type(self.spike_images) is not tuple or not self.spike_images
                or any(type(item) is not str for item in self.spike_images)):
            raise ValueError("qualification attempt request spike images are malformed")
        if type(self.profile_id) is not str or not self.profile_id:
            raise ValueError("qualification attempt request profile is malformed")


@dataclass(frozen=True, slots=True)
class QualificationAttemptBound:
    """The attempt's success: the ``/3`` record is durably published write-first (its content
    digest), and the binding the caller installs by compare-and-set carries the record's
    execution binding digest."""

    record_digest: str
    execution_binding_digest: str


@dataclass(frozen=True, slots=True)
class QualificationAttemptFailed:
    """The attempt's closed failure: one registered attempt-failure reason (never an admission
    reason, never ``invalid-input``), plus the failing probe name exactly when the reason is the
    probe failure. The caller records the outcome and changes no binding."""

    reason: str
    probe: str | None = None


class QualificationAttemptPort(Protocol):
    """One host qualification attempt under the run's sandbox authority (the bridge binds this
    to E5's sandbox owner; no Docker in the host-neutral layer). Runs only after the
    transaction's durable reservation; a killed or raising attempt binds nothing."""

    def run_qualification_attempt(
        self, request: QualificationAttemptRequest,
    ) -> QualificationAttemptBound | QualificationAttemptFailed: ...


@dataclass(frozen=True)
class SpikesUnavailable:
    """A closed selection result. ``diagnostic`` is never persisted as run authority."""

    diagnostic: str | None = None

    def persisted(self) -> dict[str, str]:
        return {"kind": "spikes_unavailable"}


class NullSpikeAuthoritySelector:
    def select(self, requested_profile: str | None = None) -> SpikesUnavailable:
        return SpikesUnavailable()


@dataclass(frozen=True)
class CapturedFile:
    observation: FileObservation
    content: bytes | None

@dataclass(frozen=True)
class WorkspaceCapture:
    basis_id: str
    files: tuple[CapturedFile, ...]


@dataclass(frozen=True)
class ObservationSnapshot:
    observations: tuple[FileObservation, ...]
    basis_id: str
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.observations, tuple):
            raise ValueError("observations must be a tuple")
        if any(not isinstance(item, FileObservation) for item in self.observations):
            raise ValueError("observations must contain FileObservation")
        if not isinstance(self.basis_id, str) or not self.basis_id:
            raise ValueError("basis_id must be nonempty text")
        paths = [item.path for item in self.observations]
        if paths != sorted(set(paths)):
            raise ValueError("observation paths must be canonical lexical unique")
        if self.digest != observations_digest(self.observations):
            raise ValueError("digest must equal canonical observations digest")

class Workspace(Protocol):
    def capture_inputs(self, declarations: tuple[str, ...]) -> WorkspaceCapture: ...
    def observe(self, paths: tuple[str, ...]) -> WorkspaceCapture: ...
