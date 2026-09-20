#!/usr/bin/env python3
"""D5 application transport facts and ports (empirica 2.0)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from core.freshness import FileBinding, FileObservation, canonical_digest, validate_digest256


@dataclass(frozen=True)
class CapturedFile:
    observation: FileObservation
    content: bytes | None

@dataclass(frozen=True)
class WorkspaceCapture:
    basis_id: str
    files: tuple[CapturedFile, ...]

@dataclass(frozen=True)
class HarnessResult:
    exit_code: int
    result_digest: str
    snapshot_digest: str
    def __post_init__(self):
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ValueError("exit_code must be a non-boolean int")
        validate_digest256(self.result_digest)
        validate_digest256(self.snapshot_digest)


@dataclass(frozen=True)
class ObservationSnapshot:
    observations: tuple[FileObservation, ...]
    basis_id: str
    digest: str
    def __post_init__(self):
        if not isinstance(self.observations, tuple):
            raise ValueError("observations must be a tuple")
        for o in self.observations:
            if not isinstance(o, FileObservation):
                raise ValueError("observations must contain FileObservation")
        if not isinstance(self.basis_id, str) or not self.basis_id:
            raise ValueError("basis_id must be a nonempty string")
        paths = [o.path for o in self.observations]
        if paths != sorted(set(paths)):
            raise ValueError("observation paths must be canonical lexical unique")
        if self.digest != canonical_digest(
                [(o.path, o.state.value, o.sha256) for o in self.observations]):
            raise ValueError("digest must equal canonical digest of observations")


@dataclass(frozen=True)
class ExecutionSnapshot:
    file_bindings: tuple[FileBinding, ...]
    captured_bytes: tuple[bytes, ...]
    snapshot_digest: str
    def __post_init__(self):
        if not isinstance(self.file_bindings, tuple) or not self.file_bindings:
            raise ValueError("file_bindings must be a nonempty tuple")
        if not isinstance(self.captured_bytes, tuple) or len(self.captured_bytes) != len(self.file_bindings):
            raise ValueError("captured_bytes must match file_bindings length")
        for b, content in zip(self.file_bindings, self.captured_bytes):
            if not isinstance(b, FileBinding) or not isinstance(content, bytes):
                raise ValueError("binding/bytes type mismatch")
            if "sha256:" + hashlib.sha256(content).hexdigest() != b.sha256:
                raise ValueError("captured content digest mismatch")
        paths = [b.path for b in self.file_bindings]
        if len(set(paths)) != len(paths):
            raise ValueError("file_bindings must be unique by path")
        if self.snapshot_digest != canonical_digest(
                [(b.path, b.sha256) for b in self.file_bindings]):
            raise ValueError("snapshot_digest must equal canonical digest of bindings")


class Workspace(Protocol):
    def observe(self, paths: tuple[str, ...]) -> WorkspaceCapture: ...


class SpikeHarness(Protocol):
    def run(self, command: str, snapshot: ExecutionSnapshot) -> HarnessResult: ...
