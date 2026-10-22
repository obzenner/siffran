#!/usr/bin/env python3
"""D5 application transport facts and ports (empirica 2.0)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.freshness import FileObservation, observations_digest


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
