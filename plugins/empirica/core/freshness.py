#!/usr/bin/env python3
"""D5 pure freshness core (empirica 2.0). No I/O; pure function of supplied observations."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import Enum


class FreshnessContractError(ValueError): ...
class PathContractError(FreshnessContractError): ...
class DigestContractError(FreshnessContractError): ...
class ObservationContractError(FreshnessContractError): ...
class ExecutionContractError(FreshnessContractError): ...

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def validate_relative_posix_path(value) -> str:
    """Validate a D2 strict-normalized path; return unchanged or reject (never rewrite)."""
    if not isinstance(value, str) or not value:
        raise PathContractError("path must be a nonempty string")
    if "\\" in value or "\0" in value or ":" in value:
        raise PathContractError("path must use '/' only; no backslash, NUL, or colon")
    if value.startswith("/"):
        raise PathContractError("path must be relative; no leading '/'")
    for segment in value.split("/"):
        if segment in ("", ".", ".."):
            raise PathContractError("path has an empty/dot/dotdot segment")
    return value


def validate_digest256(value) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise DigestContractError("digest must be exact lowercase sha256:<64 hex>")
    return value

def _canonical_json(value) -> str:
    if isinstance(value, float) and not math.isfinite(value):
        raise DigestContractError("canonical JSON rejects non-finite floats")
    if value is None or isinstance(value, bool) or isinstance(value, (int, float, str)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        keys = list(value.keys())
        if any(not isinstance(k, str) for k in keys):
            raise DigestContractError("canonical JSON mapping keys must be strings")
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _canonical_json(value[k])
                              for k in sorted(keys)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_json(v) for v in value) + "]"
    if isinstance(value, (set, frozenset)):
        raise DigestContractError("canonical JSON rejects unordered containers")
    raise DigestContractError(f"canonical JSON rejects unsupported type {type(value).__name__}")

def canonical_digest(value) -> str:
    """sha256:<64 hex> of the canonical JSON (sorted keys, preserved order, no sets)."""
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()

def command_digest(command) -> str:
    """sha256:<64 hex> of the exact UTF-8 command bytes (no trimming/normalizing)."""
    if not isinstance(command, str):
        raise DigestContractError("command must be a string")
    return "sha256:" + hashlib.sha256(command.encode("utf-8")).hexdigest()


class ObservationState(Enum):
    PRESENT = "present"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    NON_REGULAR = "non_regular"
    OUTSIDE_WORKSPACE = "outside_workspace"

class Gate(Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class FileBinding:
    path: str
    sha256: str
    def __post_init__(self):
        validate_relative_posix_path(self.path)
        validate_digest256(self.sha256)

@dataclass(frozen=True)
class FileObservation:
    path: str
    state: ObservationState
    sha256: str | None
    def __post_init__(self):
        validate_relative_posix_path(self.path)
        if not isinstance(self.state, ObservationState):
            raise FreshnessContractError("state must be an ObservationState")
        if self.state is ObservationState.PRESENT:
            validate_digest256(self.sha256)
        elif self.sha256 is not None:
            raise DigestContractError("non-present observation requires null digest")

@dataclass(frozen=True)
class ActiveSpikeHead:
    artifact_id: str
    claim_id: str
    harness_request_id: str
    file_bindings: tuple[FileBinding, ...]
    def __post_init__(self):
        validate_digest256(self.artifact_id)
        if not isinstance(self.claim_id, str) or not self.claim_id:
            raise FreshnessContractError("claim_id must be a nonempty string")
        if not isinstance(self.harness_request_id, str) or not self.harness_request_id:
            raise FreshnessContractError("harness_request_id must be a nonempty string")
        if not isinstance(self.file_bindings, tuple) or not self.file_bindings:
            raise FreshnessContractError("file_bindings must be a nonempty tuple")
        paths = []
        for b in self.file_bindings:
            if not isinstance(b, FileBinding):
                raise FreshnessContractError("file_bindings must contain FileBinding")
            paths.append(b.path)
        if len(set(paths)) != len(paths):
            raise FreshnessContractError("file_bindings must be unique by path")

@dataclass(frozen=True)
class FreshnessChange:
    path: str
    state: ObservationState
    def __post_init__(self):
        validate_relative_posix_path(self.path)
        if not isinstance(self.state, ObservationState):
            raise FreshnessContractError("state must be an ObservationState")

@dataclass(frozen=True)
class StaleHead:
    artifact_id: str
    changes: tuple[FreshnessChange, ...]
    def __post_init__(self):
        validate_digest256(self.artifact_id)
        if not isinstance(self.changes, tuple) or not self.changes:
            raise FreshnessContractError("changes must be a nonempty tuple")
        for c in self.changes:
            if not isinstance(c, FreshnessChange):
                raise FreshnessContractError("changes must contain FreshnessChange")

@dataclass(frozen=True)
class FreshnessEvaluation:
    stale_heads: tuple[StaleHead, ...]
    def __post_init__(self):
        if not isinstance(self.stale_heads, tuple):
            raise FreshnessContractError("stale_heads must be a tuple")
        for h in self.stale_heads:
            if not isinstance(h, StaleHead):
                raise FreshnessContractError("stale_heads must contain StaleHead")

@dataclass(frozen=True)
class ExecutionFacts:
    file_bindings: tuple[FileBinding, ...]
    exit_code: int
    gate: Gate
    result_digest: str
    def __post_init__(self):
        if not isinstance(self.file_bindings, tuple) or not self.file_bindings:
            raise ExecutionContractError("file_bindings must be a nonempty tuple")
        paths = []
        for b in self.file_bindings:
            if not isinstance(b, FileBinding):
                raise ExecutionContractError("file_bindings must contain FileBinding")
            paths.append(b.path)
        if len(set(paths)) != len(paths):
            raise ExecutionContractError("file_bindings must be unique by path")
        if not isinstance(self.gate, Gate):
            raise ExecutionContractError("gate must be a Gate")
        if self.gate != gate_from_exit_code(self.exit_code):
            raise ExecutionContractError("gate must match gate_from_exit_code(exit_code)")
        validate_digest256(self.result_digest)


def required_paths(active_heads) -> tuple[str, ...]:
    """Lexical (sorted, duplicate-free) union of every supplied head's binding paths."""
    if not isinstance(active_heads, tuple):
        raise FreshnessContractError("active_heads must be a tuple")
    seen: set[str] = set()
    for head in active_heads:
        if not isinstance(head, ActiveSpikeHead):
            raise FreshnessContractError("active_heads must contain ActiveSpikeHead")
        for b in head.file_bindings:
            seen.add(b.path)
    return tuple(sorted(seen))

def validate_observations(requested_paths, observations) -> tuple[FileObservation, ...]:
    """Require exact one-to-one coverage in canonical lexical unique order."""
    if not isinstance(requested_paths, tuple):
        raise ObservationContractError("requested_paths must be a tuple")
    if not isinstance(observations, tuple):
        raise ObservationContractError("observations must be a tuple")
    if len(requested_paths) != len(observations):
        raise ObservationContractError("observation count must equal requested path count")
    for path in requested_paths:
        validate_relative_posix_path(path)
    if list(requested_paths) != sorted(set(requested_paths)):
        raise ObservationContractError("requested_paths must be canonical lexical unique")
    for path, obs in zip(requested_paths, observations):
        if not isinstance(obs, FileObservation):
            raise ObservationContractError("observations must contain FileObservation")
        if obs.path != path:
            raise ObservationContractError("observation path must match requested order")
    return observations

def evaluate_freshness(active_heads, observations) -> FreshnessEvaluation:
    """Compare each binding to its exact observation; return only stale heads in supplied order."""
    rpaths = required_paths(active_heads)
    validate_observations(rpaths, observations)
    by_path = {obs.path: obs for obs in observations}
    stale: list[StaleHead] = []
    for head in active_heads:
        changes: list[FreshnessChange] = []
        for b in head.file_bindings:
            obs = by_path[b.path]
            if obs.state is ObservationState.PRESENT:
                if obs.sha256 != b.sha256:
                    changes.append(FreshnessChange(b.path, ObservationState.PRESENT))
            else:
                changes.append(FreshnessChange(b.path, obs.state))
        if changes:
            stale.append(StaleHead(head.artifact_id, tuple(changes)))
    return FreshnessEvaluation(tuple(stale))

def gate_from_exit_code(code) -> Gate:
    """Non-boolean integer zero -> PASS, nonzero -> FAIL; other values reject."""
    if isinstance(code, bool) or not isinstance(code, int):
        raise ExecutionContractError("exit code must be a non-boolean int")
    return Gate.PASS if code == 0 else Gate.FAIL

_MISSING = object()


def execution_facts(bindings, harness_result) -> ExecutionFacts:
    """Validate exact result types and derive gate only from the exit code (duck-typed result)."""
    if not isinstance(bindings, tuple) or not bindings:
        raise ExecutionContractError("bindings must be a nonempty tuple")
    for b in bindings:
        if not isinstance(b, FileBinding):
            raise ExecutionContractError("bindings must contain FileBinding")
    exit_code = getattr(harness_result, "exit_code", _MISSING)
    result_digest = getattr(harness_result, "result_digest", _MISSING)
    snapshot_digest = getattr(harness_result, "snapshot_digest", _MISSING)
    if exit_code is _MISSING or result_digest is _MISSING or snapshot_digest is _MISSING:
        raise ExecutionContractError("harness result missing exit/result/snapshot fields")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise ExecutionContractError("exit_code must be a non-boolean int")
    try:
        validate_digest256(result_digest)
        validate_digest256(snapshot_digest)
    except DigestContractError as exc:
        raise ExecutionContractError("harness result digests must be digest256") from exc
    return ExecutionFacts(bindings, exit_code, gate_from_exit_code(exit_code), result_digest)
