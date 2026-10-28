#!/usr/bin/env python3
"""D5 pure freshness core (empirica 2.0). No I/O; pure function of supplied observations."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .canonical import CanonicalJSONError, canonical_digest


FreshnessContractError = CanonicalJSONError
class PathContractError(FreshnessContractError): ...
DigestContractError = CanonicalJSONError
class ObservationContractError(FreshnessContractError): ...

# The one digest form; the literal is the vendored schemas' $defs/digest256 pattern (a test pins the
# equality). Always applied with ``fullmatch``, so a trailing newline never matches.
DIGEST256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_DIGEST_RE = re.compile(DIGEST256_PATTERN)
# The one claim-identifier pattern; the literal mirrors shared-defs.json#/$defs/claimId (a test
# pins the equality). The final lookahead means absolute end under both Python ``re.search`` and
# ECMA-262 (``$`` would also match before a trailing newline in Python).
CLAIM_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}(?![\s\S])"
_CLAIM_ID_RE = re.compile(CLAIM_ID_PATTERN)


def valid_claim_id(value) -> bool:
    """Return whether ``value`` is a claim identifier (safe by construction to render raw)."""
    return isinstance(value, str) and _CLAIM_ID_RE.fullmatch(value) is not None


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


def is_digest256(value) -> bool:
    """Whether ``value`` is a plain ``str`` in the exact lowercase ``sha256:<64 hex>`` form."""
    return type(value) is str and _DIGEST_RE.fullmatch(value) is not None


def validate_digest256(value) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise DigestContractError("digest must be exact lowercase sha256:<64 hex>")
    return value

class ObservationState(Enum):
    PRESENT = "present"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    NON_REGULAR = "non_regular"
    OUTSIDE_WORKSPACE = "outside_workspace"

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


def observations_digest(observations) -> str:
    """Digest canonical file observations using the core's sole JSON canonicalizer."""
    return canonical_digest([(item.path, item.state.value, item.sha256)
                             for item in observations])


@dataclass(frozen=True)
class ActiveSpikeHead:
    artifact_id: str
    claim_id: str
    harness_request_id: str
    file_bindings: tuple[FileBinding, ...]
    def __post_init__(self):
        validate_digest256(self.artifact_id)
        if not valid_claim_id(self.claim_id):
            raise FreshnessContractError("claim_id must match " + CLAIM_ID_PATTERN)
        if not isinstance(self.harness_request_id, str) or not self.harness_request_id:
            raise FreshnessContractError("harness_request_id must be a nonempty string")
        if not isinstance(self.file_bindings, tuple):
            raise FreshnessContractError("file_bindings must be a tuple")
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
