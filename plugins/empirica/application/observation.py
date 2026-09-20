#!/usr/bin/env python3
"""D5 imperative shell factories (empirica 2.0). Shared _capture + _validate_capture."""
from __future__ import annotations

import hashlib

from core.freshness import (
    FileBinding, FileObservation, ObservationState, PathContractError, canonical_digest,
    execution_facts, required_paths, validate_relative_posix_path,
)
from .ports import (
    CapturedFile, ExecutionSnapshot, HarnessResult, ObservationSnapshot, WorkspaceCapture,
)


class ObservationUnavailable(Exception): ...
class HarnessUnavailable(Exception): ...
class HarnessContractError(Exception): ...

_EMPTY_BASIS = canonical_digest(())

def _capture(paths, workspace):
    try:
        return workspace.observe(tuple(paths))
    except Exception as exc:
        raise ObservationUnavailable("workspace observe failed") from exc


def _validate_capture(capture, paths):
    """Shared full capture validation: type, basis, coverage, file type, path match, content."""
    if not isinstance(capture, WorkspaceCapture):
        raise ObservationUnavailable("workspace did not return a WorkspaceCapture")
    if not isinstance(capture.basis_id, str) or not capture.basis_id:
        raise ObservationUnavailable("capture basis_id must be a nonempty string")
    if not isinstance(capture.files, tuple) or len(capture.files) != len(paths):
        raise ObservationUnavailable("capture must have one file per requested path")
    for path, cf in zip(paths, capture.files):
        if not isinstance(cf, CapturedFile):
            raise ObservationUnavailable("capture files must be CapturedFile")
        if not isinstance(cf.observation, FileObservation) or cf.observation.path != path:
            raise ObservationUnavailable("captured observation path mismatch")
        _validate_content(cf)
    return capture.files


def _validate_content(cf):
    obs = cf.observation
    if obs.state is ObservationState.PRESENT:
        if not isinstance(cf.content, bytes):
            raise ObservationUnavailable("present capture requires immutable bytes")
        if "sha256:" + hashlib.sha256(cf.content).hexdigest() != obs.sha256:
            raise ObservationUnavailable("captured content digest mismatch")
    elif cf.content is not None:
        raise ObservationUnavailable("non-present capture requires null content")


def _validate_dependent_paths(dependent_paths) -> tuple[str, ...]:
    if not isinstance(dependent_paths, tuple) or not dependent_paths:
        raise PathContractError("dependent_paths must be a nonempty tuple")
    paths = [validate_relative_posix_path(p) for p in dependent_paths]
    if len(set(paths)) != len(paths):
        raise PathContractError("dependent_paths must be duplicate-free")
    return tuple(paths)


def build_observation_snapshot(active_heads, workspace):
    rpaths = required_paths(active_heads)
    if not rpaths:
        return ObservationSnapshot((), _EMPTY_BASIS, canonical_digest(()))
    capture = _capture(rpaths, workspace)
    files = _validate_capture(capture, rpaths)
    observations = tuple(cf.observation for cf in files)
    digest = canonical_digest([(o.path, o.state.value, o.sha256) for o in observations])
    return ObservationSnapshot(observations, capture.basis_id, digest)


def build_execution_snapshot(dependent_paths, workspace):
    paths = _validate_dependent_paths(dependent_paths)
    capture = _capture(paths, workspace)
    files = _validate_capture(capture, paths)
    bindings = []
    captured = []
    for cf in files:
        obs = cf.observation
        if obs.state is not ObservationState.PRESENT:
            raise ObservationUnavailable("execution capture must be present")
        bindings.append(FileBinding(obs.path, obs.sha256))
        captured.append(cf.content)
    digest = canonical_digest([(b.path, b.sha256) for b in bindings])
    return ExecutionSnapshot(tuple(bindings), tuple(captured), digest)


def execute_spike_bound(command, dependent_paths, workspace, harness):
    """Capture once, execute from immutable bytes, and retain the exact observation basis."""
    if not isinstance(command, str) or not command:
        raise ValueError("command must be a nonempty string")
    paths = _validate_dependent_paths(dependent_paths)
    capture = _capture(paths, workspace)
    files = _validate_capture(capture, paths)
    bindings = tuple(FileBinding(cf.observation.path, cf.observation.sha256) for cf in files)
    execution = ExecutionSnapshot(
        bindings, tuple(cf.content for cf in files),
        canonical_digest([(b.path, b.sha256) for b in bindings]))
    observations = tuple(cf.observation for cf in files)
    observation = ObservationSnapshot(
        observations, capture.basis_id,
        canonical_digest([(o.path, o.state.value, o.sha256) for o in observations]))
    try:
        result = harness.run(command, execution)
    except Exception as exc:
        raise HarnessUnavailable("harness run failed") from exc
    if not isinstance(result, HarnessResult):
        raise HarnessContractError("harness must return a HarnessResult")
    if result.snapshot_digest != execution.snapshot_digest:
        raise HarnessContractError("harness echoed a mismatched snapshot digest")
    return execution_facts(bindings, result), execution, observation


def execute_spike(command, dependent_paths, workspace, harness):
    if not isinstance(command, str) or not command:
        raise ValueError("command must be a nonempty string")
    snapshot = build_execution_snapshot(dependent_paths, workspace)
    try:
        result = harness.run(command, snapshot)
    except Exception as exc:
        raise HarnessUnavailable("harness run failed") from exc
    if not isinstance(result, HarnessResult):
        raise HarnessContractError("harness must return a HarnessResult")
    if result.snapshot_digest != snapshot.snapshot_digest:
        raise HarnessContractError("harness echoed a mismatched snapshot digest")
    return execution_facts(snapshot.file_bindings, result)
