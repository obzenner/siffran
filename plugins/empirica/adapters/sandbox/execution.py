"""Docker execution substrate and exact-record run-bound harness.

Application code depends only on the run-bound port and never imports this module. The unchanged
bridge still supplies the legacy two-argument harness, so default production composition cannot
enter ``DockerSpikeHarness`` until a separately reviewed composition checkpoint.
"""
from __future__ import annotations

import fcntl
import hashlib
import math
import os
import re
import stat
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapters.state import fsio
from application.ports import (
    ExecutionSnapshot, HarnessResult, QualifiedSpikeBackend, SpikeExecutionUnavailable,
)
from core.freshness import canonical_digest, validate_relative_posix_path

from . import docker, environment, record, selection

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_ATOMIC_RESIDUE = re.compile(r"^\.tmp\.[A-Za-z0-9_-]{6,64}\.json$")


class ExecutionSubstrateError(RuntimeError):
    """Staging, lock, or recovery could not be completed without ambiguity."""


def _operation_id(value: str) -> str:
    if not isinstance(value, str) or record.OPERATION_ID.fullmatch(value) is None:
        raise ExecutionSubstrateError("operation identity is malformed")
    return value


def new_operation_id() -> str:
    return "o-" + uuid.uuid4().hex


def _remaining(
    deadline: float, message: str, *, clock: Callable[[], float] = time.monotonic,
) -> float:
    if (type(deadline) not in {int, float} or isinstance(deadline, bool)
            or not math.isfinite(deadline)):
        raise ExecutionSubstrateError(message + " is malformed")
    value = deadline - clock()
    if value <= 0:
        raise ExecutionSubstrateError(message + " expired")
    return value


@contextmanager
def runtime_lock(
    store: record.QualificationStore, deadline: float, *,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[None]:
    """Acquire the operator-created process lock without waiting beyond a monotonic deadline."""
    store.check_execution_scaffolding()
    _remaining(deadline, "runtime lock deadline", clock=clock)
    flags = os.O_RDWR | _NOFOLLOW | _CLOEXEC
    try:
        fd = os.open(store.runtime_lock, flags)
    except OSError as exc:
        raise ExecutionSubstrateError("runtime lock cannot be opened safely") from exc
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 0
                or info.st_nlink != 1):
            raise ExecutionSubstrateError("runtime lock identity is unsafe")
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = _remaining(
                    deadline, "runtime lock deadline", clock=clock)
                time.sleep(min(0.01, remaining))
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@dataclass(frozen=True)
class _StagingPlan:
    files: tuple[tuple[tuple[str, ...], Any, bytes], ...]
    max_files: int
    max_bytes: int
    max_entries: int
    max_depth: int


def _staging_limits(policy: Mapping[str, Any]) -> tuple[int, int, int, int]:
    checked = docker.validate_policy(policy)
    bounds = checked["bounds"]
    max_files = bounds["staging_max_files"]
    return max_files, bounds["staging_max_bytes"], max_files * 4, min(max_files, 64)


def _plan_staging(snapshot: ExecutionSnapshot, policy: Mapping[str, Any]) -> _StagingPlan:
    if not isinstance(snapshot, ExecutionSnapshot):
        raise ExecutionSubstrateError("staging requires an ExecutionSnapshot")
    max_files, max_bytes, max_entries, max_depth = _staging_limits(policy)
    if len(snapshot.file_bindings) > max_files or sum(map(len, snapshot.captured_bytes)) > max_bytes:
        raise ExecutionSubstrateError("execution snapshot exceeds bound staging limits")
    directories: set[tuple[str, ...]] = set()
    files = []
    for binding, content in zip(snapshot.file_bindings, snapshot.captured_bytes):
        parts = tuple(validate_relative_posix_path(binding.path).split("/"))
        if len(parts) > max_depth:
            raise ExecutionSubstrateError("execution snapshot exceeds staging depth bound")
        directories.update(parts[:index] for index in range(1, len(parts)))
        files.append((parts, binding, content))
    if len(files) + len(directories) > max_entries:
        raise ExecutionSubstrateError("execution snapshot exceeds staging entry bound")
    return _StagingPlan(tuple(files), max_files, max_bytes, max_entries, max_depth)


def _open_directory(name: str, parent_fd: int) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=parent_fd)


def materialize_snapshot(
    store: record.QualificationStore, operation_id: str,
    snapshot: ExecutionSnapshot, policy: Mapping[str, Any],
) -> Path:
    """Create one sealed read-only tree containing only the immutable snapshot bytes."""
    store.check_execution_scaffolding()
    operation_id = _operation_id(operation_id)
    plan = _plan_staging(snapshot, policy)
    root = store.staging / operation_id
    try:
        root.mkdir(mode=0o700)
    except OSError as exc:
        raise ExecutionSubstrateError("staging operation cannot be allocated exclusively") from exc
    root_fd = -1
    opened: dict[str, int] = {}
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC)
        opened[""] = root_fd
        for parts, binding, content in plan.files:
            prefix = ""
            parent_fd = root_fd
            for part in parts[:-1]:
                next_prefix = part if not prefix else prefix + "/" + part
                if next_prefix not in opened:
                    try:
                        os.mkdir(part, 0o700, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                    opened[next_prefix] = _open_directory(part, parent_fd)
                    info = os.fstat(opened[next_prefix])
                    if (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
                        raise ExecutionSubstrateError("staging directory identity is unsafe")
                parent_fd = opened[next_prefix]
                prefix = next_prefix
            leaf = parts[-1]
            try:
                os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ExecutionSubstrateError("staging leaf already exists")
            temporary = ".tmp-" + uuid.uuid4().hex
            fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
                0o600, dir_fd=parent_fd)
            try:
                view = memoryview(content)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise ExecutionSubstrateError("staging write made no progress")
                    view = view[written:]
                os.fsync(fd)
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_nlink != 1
                        or hashlib.sha256(content).hexdigest() != binding.sha256.removeprefix("sha256:")):
                    raise ExecutionSubstrateError("staged file identity or digest is unsafe")
                os.fchmod(fd, 0o444)
            finally:
                os.close(fd)
            try:
                os.rename(temporary, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            except BaseException:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except OSError:
                    pass
                raise
            os.fsync(parent_fd)
        for prefix in sorted((key for key in opened if key), key=lambda x: x.count("/"), reverse=True):
            os.fchmod(opened[prefix], 0o555)
        os.fchmod(root_fd, 0o555)
        os.fsync(root_fd)
    except BaseException:
        for fd in set(opened.values()):
            if fd != root_fd:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if root_fd >= 0:
            os.close(root_fd)
        try:
            cleanup_staging(store, operation_id, policy)
        except BaseException:
            pass
        raise
    else:
        for fd in set(opened.values()):
            if fd != root_fd:
                os.close(fd)
        os.close(root_fd)
    return root.resolve(strict=True)


def cleanup_staging(
    store: record.QualificationStore, operation_id: str, policy: Mapping[str, Any],
) -> None:
    """Inspect and delete only one exact bounded no-follow staging operation tree."""
    store.check_execution_scaffolding()
    operation_id = _operation_id(operation_id)
    _max_files, max_bytes, max_entries, max_depth = _staging_limits(policy)
    root = store.staging / operation_id
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return
    if (not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode)
            or root_info.st_uid != os.getuid()
            or stat.S_IMODE(root_info.st_mode) not in {0o555, 0o700}):
        raise ExecutionSubstrateError("staging operation root is unsafe")
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC)
    count = total = 0

    def remove_contents(directory_fd: int, depth: int) -> None:
        nonlocal count, total
        if depth > max_depth:
            raise ExecutionSubstrateError("staging cleanup exceeds depth bound")
        for name in sorted(os.listdir(directory_fd)):
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            count += 1
            if count > max_entries:
                raise ExecutionSubstrateError("staging entry count exceeds cleanup bound")
            mode = stat.S_IMODE(info.st_mode)
            if info.st_uid != os.getuid():
                raise ExecutionSubstrateError("staging entry owner is unsafe")
            if stat.S_ISDIR(info.st_mode):
                if mode not in {0o555, 0o700}:
                    raise ExecutionSubstrateError("staging directory mode is unsafe")
                child_fd = _open_directory(name, directory_fd)
                try:
                    os.fchmod(child_fd, 0o700)
                    remove_contents(child_fd, depth + 1)
                finally:
                    os.close(child_fd)
                os.rmdir(name, dir_fd=directory_fd)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1 or mode not in {0o444, 0o600}:
                    raise ExecutionSubstrateError("staging file identity is unsafe")
                total += info.st_size
                if total > max_bytes:
                    raise ExecutionSubstrateError("staging bytes exceed cleanup bound")
                os.unlink(name, dir_fd=directory_fd)
            else:
                raise ExecutionSubstrateError("staging contains a non-regular entry")

    try:
        os.fchmod(root_fd, 0o700)
        remove_contents(root_fd, 0)
    finally:
        os.close(root_fd)
    root.rmdir()
    fsio._fsync_dir(store.staging)


def _authority_matches_client(
    store: record.QualificationStore, row: Mapping[str, Any], client: docker.DockerClient,
) -> None:
    authority = store.load_record(row["qualification_record_digest"])
    policy = docker.validate_policy({
        **authority["profile"]["policy"], "runtime_identity": authority["runtime"]})
    if (authority["execution_binding_digest"] != row["execution_binding_digest"]
            or docker.policy_digest(policy) != row["policy_digest"]
            or policy != client.policy):
        raise ExecutionSubstrateError("journal authority is not the current verified Docker client")


def _plan_recovery(
    row: Mapping[str, Any], *, container_present: bool,
    staging_present: bool, staging_safe: bool,
) -> str:
    """Purely admit one effect from validated journal plus Docker/filesystem observations."""
    if row["lifecycle_state"] not in record.JOURNAL_STATES:
        raise ExecutionSubstrateError("recovery observation has unknown journal state")
    if container_present:
        if not staging_present or not staging_safe:
            return "refuse"
        return "remove"
    if staging_present:
        return "cleanup"
    return "finish"


def _clean_incomplete_journal_entries(store: record.QualificationStore) -> None:
    """Remove only closed fsio residue and exact empty pre-intent/finished directories."""
    store.check_execution_scaffolding()
    roots = sorted(store.runtime.iterdir())
    if len(roots) > record.MAX_JOURNALS:
        raise ExecutionSubstrateError("journal entry count exceeds recovery bound")
    planned: list[tuple[Path, tuple[str, ...], bool]] = []
    total_bytes = 0
    for root in roots:
        if record.OPERATION_ID.fullmatch(root.name) is None:
            raise ExecutionSubstrateError("runtime contains a malformed operation directory")
        info = root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise ExecutionSubstrateError("incomplete operation directory is unsafe")
        names = tuple(sorted(os.listdir(root)))
        if len(names) > 3:
            raise ExecutionSubstrateError("incomplete operation entry count exceeds bound")
        residue = tuple(name for name in names if _ATOMIC_RESIDUE.fullmatch(name))
        unknown = set(names) - set(residue) - {"intent.json", "container.cid"}
        if unknown:
            raise ExecutionSubstrateError("incomplete operation contains an unknown entry")
        if "intent.json" not in names and set(names) - set(residue):
            raise ExecutionSubstrateError("pre-intent operation contains unexpected state")
        for name in names:
            item = (root / name).lstat()
            total_bytes += item.st_size
            if total_bytes > record.MAX_JOURNAL_TOTAL_BYTES:
                raise ExecutionSubstrateError("journal bytes exceed recovery bound")
            if name in residue:
                if (not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid()
                        or stat.S_IMODE(item.st_mode) != 0o600 or item.st_nlink != 1
                        or item.st_size > record.MAX_JOURNAL_BYTES):
                    raise ExecutionSubstrateError("atomic journal residue is unsafe")
            elif name == "intent.json":
                if (not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid()
                        or stat.S_IMODE(item.st_mode) != 0o600 or item.st_nlink != 1
                        or item.st_size > record.MAX_JOURNAL_BYTES):
                    raise ExecutionSubstrateError("journal intent control file is unsafe")
            elif (not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid()
                  or stat.S_IMODE(item.st_mode) != 0o600 or item.st_nlink != 1
                  or item.st_size > record.MAX_CIDFILE_BYTES):
                raise ExecutionSubstrateError("journal cid control file is unsafe")
        planned.append((root, residue, "intent.json" in names))
    for root, residue, has_intent in planned:
        for name in residue:
            (root / name).unlink()
            fsio._fsync_dir(root)
        if not has_intent:
            root.rmdir()
            fsio._fsync_dir(store.runtime)


def recover_operations(
    store: record.QualificationStore, client: docker.DockerClient, deadline: float, *,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, ...]:
    """Recover every exact journal while the caller holds the runtime lock."""
    store.check_execution_scaffolding()
    _remaining(deadline, "recovery deadline", clock=clock)
    _clean_incomplete_journal_entries(store)
    _remaining(deadline, "recovery deadline", clock=clock)
    recovered = []
    for row in store.read_journals():
        _remaining(deadline, "recovery deadline", clock=clock)
        _authority_matches_client(store, row, client)
        _remaining(deadline, "recovery deadline", clock=clock)
        operation_id = row["operation_id"]
        staging = store.staging / operation_id
        input_dir = staging
        network = client.policy["isolation"]["network"]
        remaining = _remaining(deadline, "recovery deadline", clock=clock)
        cid = row["cidfile_full_id"]
        if cid is None:
            candidates = client.exact_candidates(
                name=row["name"], labels=row["labels"], input_dir=input_dir,
                network=network, timeout_seconds=remaining)
        else:
            known = client.known_container(
                container_id=cid, name=row["name"], labels=row["labels"],
                input_dir=input_dir, network=network, timeout_seconds=remaining)
            candidates = () if known is None else ((cid, known),)
        try:
            staging_info = staging.lstat()
        except FileNotFoundError:
            staging_present = staging_safe = False
        except OSError as exc:
            raise ExecutionSubstrateError("staging identity is unavailable") from exc
        else:
            staging_present = True
            staging_safe = (
                stat.S_ISDIR(staging_info.st_mode) and not stat.S_ISLNK(staging_info.st_mode)
                and staging_info.st_uid == os.getuid()
                and stat.S_IMODE(staging_info.st_mode) in {0o555, 0o700})
        action = _plan_recovery(
            row, container_present=bool(candidates),
            staging_present=staging_present, staging_safe=staging_safe)
        if action == "refuse":
            raise ExecutionSubstrateError("live container staging identity is unsafe")
        if action == "cleanup":
            cleanup_staging(store, operation_id, client.policy)
            action = _plan_recovery(
                row, container_present=False, staging_present=False, staging_safe=False)
        if action == "finish":
            if row["lifecycle_state"] == "intent" and cid is None:
                store.finish_operation(operation_id, None)
            else:
                full_id = row["full_id"] or cid
                if row["lifecycle_state"] == "intent":
                    store.mark_created(operation_id, full_id)
                    store.mark_removing(operation_id)
                elif row["lifecycle_state"] == "created":
                    store.mark_removing(operation_id)
                store.finish_operation(operation_id, full_id)
            recovered.append(operation_id)
            continue
        container_id, _doc = candidates[0]
        if cid is not None and cid != container_id:
            raise ExecutionSubstrateError("journal cidfile differs from exact inspected candidate")
        if row["lifecycle_state"] == "intent":
            if cid is None:
                store.write_cid(operation_id, container_id)
            store.mark_created(operation_id, container_id)
        store.mark_removing(operation_id)
        client.remove_exact(
            container_id=container_id, name=row["name"], labels=row["labels"],
            input_dir=input_dir, network=network, deadline=deadline)
        action = _plan_recovery(
            row, container_present=False, staging_present=True, staging_safe=True)
        if action != "cleanup":
            raise ExecutionSubstrateError("post-remove recovery plan is inconsistent")
        cleanup_staging(store, operation_id, client.policy)
        action = _plan_recovery(
            row, container_present=False, staging_present=False, staging_safe=False)
        if action != "finish":
            raise ExecutionSubstrateError("post-cleanup recovery plan is inconsistent")
        store.finish_operation(operation_id, container_id)
        recovered.append(operation_id)
    return tuple(recovered)


def _validated_capture(
    value: object, *, output_limited: bool, output_limit_bytes: int,
) -> docker.StreamCapture:
    if (type(value) is not docker.StreamCapture or type(value.data) is not bytes
            or isinstance(value.bytes_seen, bool) or type(value.bytes_seen) is not int
            or value.bytes_seen < len(value.data) or value.bytes_seen < 0
            or type(value.sha256) is not str or len(value.sha256) != 71
            or not value.sha256.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in value.sha256[7:])
            or type(value.tail) is not str):
        raise ExecutionSubstrateError("runner stream capture is malformed")
    if output_limited is False:
        expected_tail = value.data[-docker.DIAGNOSTIC_TAIL_BYTES:].decode(
            "utf-8", errors="replace").strip()
        if (value.bytes_seen != len(value.data) or value.bytes_seen > output_limit_bytes
                or value.sha256 != "sha256:" + hashlib.sha256(value.data).hexdigest()
                or value.tail != expected_tail):
            raise ExecutionSubstrateError("runner stream capture is inconsistent")
    return value


def _validated_command_result(
    value: object, *, output_limit_bytes: int,
) -> docker.CommandResult:
    if (type(value) is not docker.CommandResult
            or isinstance(value.returncode, bool) or type(value.returncode) is not int
            or type(value.timed_out) is not bool or type(value.output_limited) is not bool
            or isinstance(value.elapsed_ms, bool) or type(value.elapsed_ms) is not int
            or value.elapsed_ms < 0):
        raise ExecutionSubstrateError("runner command result is malformed")
    _validated_capture(
        value.stdout, output_limited=value.output_limited,
        output_limit_bytes=output_limit_bytes)
    _validated_capture(
        value.stderr, output_limited=value.output_limited,
        output_limit_bytes=output_limit_bytes)
    return value


class _DeadlineRunner:
    """Narrow every delegated subprocess bound to the operation's monotonic deadline."""

    def __init__(
        self, runner: docker.Runner, deadline: float, clock: Callable[[], float],
    ) -> None:
        self._runner, self._deadline, self._clock = runner, deadline, clock

    def run(self, argv, *, timeout_seconds, output_limit_bytes, env=None):
        remaining = _remaining(
            self._deadline, "Docker command deadline", clock=self._clock)
        result = self._runner.run(
            argv, timeout_seconds=min(timeout_seconds, remaining),
            output_limit_bytes=output_limit_bytes, env=env)
        return _validated_command_result(result, output_limit_bytes=output_limit_bytes)


@dataclass(frozen=True)
class _ResolvedAuthority:
    record: dict[str, Any]
    environment: dict[str, Any]
    runtime: dict[str, Any]
    policy: dict[str, Any]
    client: docker.DockerClient


@dataclass(frozen=True)
class _ExecutionPlan:
    run_id: str
    binding: QualifiedSpikeBackend
    operation_id: str
    name: str
    labels: dict[str, str]
    intent: dict[str, Any]
    command: str
    command_digest: str
    snapshot: ExecutionSnapshot
    output_limit_bytes: int


def _check_binding(
    authority: Mapping[str, Any], binding: QualifiedSpikeBackend,
) -> None:
    """One pure rule for exact immutable-record/run binding equality."""
    if (authority["qualification_record_digest"] != binding.qualification_record_digest
            or authority["execution_binding_digest"] != binding.execution_binding_digest
            or authority["profile"]["id"] != record.PROFILE_ID):
        raise ExecutionSubstrateError("immutable record does not match run binding")


def _validate_request(run_id: object, command: object, snapshot: object) -> None:
    if type(run_id) is not str or not run_id or len(run_id) > 4096:
        raise ValueError("run_id must be bounded nonempty text")
    if type(command) is not str or not command:
        raise ValueError("command must be nonempty text")
    if not isinstance(snapshot, ExecutionSnapshot):
        raise ValueError("snapshot must be an ExecutionSnapshot")
    try:
        run_id.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("run_id must be valid UTF-8 text") from exc


def _execution_plan(
    *, run_id: str, binding: QualifiedSpikeBackend, command: str,
    snapshot: ExecutionSnapshot, operation_id: str,
    authority: Mapping[str, Any], policy: Mapping[str, Any],
) -> _ExecutionPlan:
    """Purely derive all persisted/container identity from bound run and authority facts."""
    _validate_request(run_id, command, snapshot)
    if type(binding) is not QualifiedSpikeBackend:
        raise ValueError("binding must be an exact QualifiedSpikeBackend")
    operation_id = _operation_id(operation_id)
    _check_binding(authority, binding)
    policy_digest = docker.policy_digest(policy)
    correlation = "r-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:40]
    role = "spike"
    name = docker.NAME_PREFIX + operation_id
    labels = docker.qualification_labels(correlation, role, policy_digest)
    intent = {
        "schema": record.JOURNAL_SCHEMA,
        "operation_id": operation_id,
        "lifecycle_state": "intent",
        "execution_binding_digest": binding.execution_binding_digest,
        "qualification_record_digest": binding.qualification_record_digest,
        "policy_digest": policy_digest,
        "run_id": correlation,
        "role": role,
        "name": name,
        "labels": labels,
        "expected_image": {
            "repo_digest": authority["runtime"]["image_repo_digest"],
            "config_id": authority["runtime"]["image_config_id"],
        },
        "full_id": None,
        "cidfile_name": "container.cid",
    }
    checked_intent = record.validate_journal(intent, directory_name=operation_id)
    return _ExecutionPlan(
        run_id, binding, operation_id, name, labels, checked_intent, command,
        canonical_digest(command),
        snapshot, policy["bounds"]["workload_output_limit_bytes"])


def _admit_result(
    plan: _ExecutionPlan, resolved: _ResolvedAuthority, *, exit_code: int,
    stdout: docker.StreamCapture, stderr: docker.StreamCapture,
    staging_absent: bool, journal_absent: bool,
) -> HarnessResult:
    """Purely admit a result only from complete cleanup and unchanged exact authority."""
    _check_binding(resolved.record, plan.binding)
    if (isinstance(exit_code, bool) or not isinstance(exit_code, int)
            or staging_absent is not True or journal_absent is not True
            or docker.policy_digest(resolved.policy) != plan.intent["policy_digest"]):
        raise ExecutionSubstrateError("result admission facts are incomplete or changed")
    digest = canonical_digest({
        "command_digest": plan.command_digest,
        "exit_code": exit_code,
        "stdout_sha256": stdout.sha256,
        "stdout_bytes": stdout.bytes_seen,
        "stderr_sha256": stderr.sha256,
        "stderr_bytes": stderr.bytes_seen,
        "output_limit_bytes": plan.output_limit_bytes,
        "snapshot_digest": plan.snapshot.snapshot_digest,
        "qualification_record_digest": plan.intent["qualification_record_digest"],
        "execution_binding_digest": plan.intent["execution_binding_digest"],
        "policy_digest": plan.intent["policy_digest"],
    })
    return HarnessResult(exit_code, digest, plan.snapshot.snapshot_digest)


class _UnavailableSignal(Exception):
    def __init__(self, reason: str, detail: str):
        self.reason, self.detail = reason, detail
        super().__init__(detail)


class DockerSpikeHarness:
    """Production exact-record Docker harness for run-bound qualified transactions."""

    def __init__(
        self, store: record.QualificationStore | None = None, *,
        environment_inspector: Callable[[], environment.EnvironmentIdentity]
        = environment.inspect_environment,
        runner: docker.Runner | None = None,
        clock: Callable[[], float] = time.monotonic,
        operation_id_source: Callable[[], str] = new_operation_id,
    ) -> None:
        self._runner = runner if runner is not None else docker.BoundedRunner()
        self._clock = clock
        self._operation_id_source = operation_id_source
        try:
            if store is not None and type(store) is not record.QualificationStore:
                raise record.QualificationRecordError(
                    "execution store must be an exact QualificationStore")
            self._store = (store if store is not None else record.QualificationStore(
                environment_inspector=environment_inspector))
            self._environment_inspector = self._store.environment_inspector
        except Exception as exc:
            raise SpikeExecutionUnavailable(
                "scaffolding_unsafe", f"{type(exc).__name__}: {exc}") from exc

    def _resolve(
        self, binding: QualifiedSpikeBackend, deadline: float,
    ) -> _ResolvedAuthority:
        _remaining(deadline, "authority resolution", clock=self._clock)
        observed_environment = self._environment_inspector()
        checked_environment = environment.validate_environment_identity(
            observed_environment, require_available=True)
        _remaining(deadline, "environment inspection", clock=self._clock)
        authority = self._store.load_record(
            binding.qualification_record_digest,
            expected_environment=checked_environment)
        _check_binding(authority, binding)
        policy = docker.validate_policy({
            **authority["profile"]["policy"], "runtime_identity": authority["runtime"]})
        bounded_runner = _DeadlineRunner(self._runner, deadline, self._clock)
        discovered = selection.inspect_runtime(
            observed_environment, runner=bounded_runner,
            cli_path=authority["runtime"]["docker_cli"])
        checked_runtime = docker.validate_runtime_identity(discovered)
        _remaining(deadline, "Docker discovery", clock=self._clock)
        reloaded = self._store.load_record(
            binding.qualification_record_digest,
            expected_environment=checked_environment, expected_runtime=checked_runtime)
        _check_binding(reloaded, binding)
        if reloaded != authority:
            raise ExecutionSubstrateError("immutable qualification record changed during resolution")
        client = docker.DockerClient(discovered, policy, bounded_runner)
        if client.policy != policy:
            raise ExecutionSubstrateError("Docker client changed the exact execution policy")
        return _ResolvedAuthority(reloaded, checked_environment, checked_runtime, policy, client)

    def _bounded_client(
        self, resolved: _ResolvedAuthority, deadline: float,
    ) -> docker.DockerClient:
        _remaining(deadline, "Docker phase deadline", clock=self._clock)
        return docker.DockerClient(
            resolved.client.identity, resolved.policy,
            _DeadlineRunner(self._runner, deadline, self._clock))

    def _cleanup_deadline(self, hard_deadline: float) -> float:
        now = self._clock()
        deadline = min(
            hard_deadline - docker.POSTFLIGHT_TIMEOUT_SECONDS,
            now + docker.CLEANUP_TIMEOUT_SECONDS)
        _remaining(deadline, "cleanup deadline", clock=self._clock)
        return deadline

    @staticmethod
    def _detail(exc: BaseException) -> str:
        return f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _reason_for_phase(phase: str) -> str:
        return {
            "staging": "staging_failed",
            "workload": "workload_state_invalid",
            "cleanup": "cleanup_failed",
            "postflight": "postflight_changed",
        }.get(phase, "lifecycle_failed")

    def run(
        self, *, run_id: str, binding: QualifiedSpikeBackend,
        command: str, snapshot: ExecutionSnapshot,
    ) -> HarnessResult:
        if type(binding) is not QualifiedSpikeBackend:
            raise SpikeExecutionUnavailable("authority_invalid", "binding type is not qualified")
        _validate_request(run_id, command, snapshot)
        try:
            began = self._clock()
        except Exception as exc:
            raise SpikeExecutionUnavailable(
                "authority_invalid", self._detail(exc)) from exc
        if (type(began) not in {int, float} or isinstance(began, bool)
                or not math.isfinite(began)):
            raise SpikeExecutionUnavailable("authority_invalid", "monotonic clock is malformed")
        hard_deadline = began + docker.OPERATION_TIMEOUT_SECONDS
        effect_deadline = (
            hard_deadline - docker.CLEANUP_TIMEOUT_SECONDS
            - docker.POSTFLIGHT_TIMEOUT_SECONDS)
        intent_started = False
        phase = "authority"
        try:
            try:
                try:
                    lock = runtime_lock(self._store, effect_deadline, clock=self._clock)
                    lock.__enter__()
                except ExecutionSubstrateError as exc:
                    reason = ("runtime_busy" if "expired" in str(exc)
                              else "scaffolding_unsafe")
                    raise SpikeExecutionUnavailable(reason, self._detail(exc)) from exc
                try:
                    try:
                        resolved = self._resolve(binding, effect_deadline)
                    except Exception as exc:
                        raise _UnavailableSignal("authority_invalid", self._detail(exc)) from exc
                    try:
                        recover_operations(
                            self._store, resolved.client, effect_deadline, clock=self._clock)
                    except Exception as exc:
                        raise _UnavailableSignal("recovery_failed", self._detail(exc)) from exc
                    phase = "lifecycle"
                    plan = _execution_plan(
                        run_id=run_id, binding=binding, command=command, snapshot=snapshot,
                        operation_id=self._operation_id_source(), authority=resolved.record,
                        policy=resolved.policy)
                    intent_started = True
                    try:
                        self._store.begin_operation(plan.intent)
                        phase = "staging"
                        staging = materialize_snapshot(
                            self._store, plan.operation_id, plan.snapshot, resolved.policy)
                        phase = "lifecycle"
                        container_id, _ = resolved.client.create_container_with_identity(
                            name=plan.name, labels=plan.labels, input_dir=staging,
                            command=plan.command, network=resolved.policy["isolation"]["network"])
                        self._store.write_cid(plan.operation_id, container_id)
                        self._store.mark_created(plan.operation_id, container_id)
                        phase = "workload"
                        started = resolved.client.call(
                            ("container", "start", "--attach", container_id),
                            timeout_seconds=min(
                                resolved.policy["bounds"]["workload_timeout_seconds"],
                                _remaining(
                                    effect_deadline, "workload deadline", clock=self._clock)),
                            output_limit_bytes=plan.output_limit_bytes)
                        if started.timed_out:
                            raise _UnavailableSignal(
                                "workload_timeout", "Docker start exceeded workload deadline")
                        if started.output_limited:
                            raise _UnavailableSignal(
                                "workload_output_limit", "Docker start exceeded output ceiling")
                        inspected = resolved.client.json_call_with_timeout(
                            ("container", "inspect", container_id, "--format", "{{json .}}"),
                            "docker post-workload inspect",
                            min(resolved.policy["bounds"]["control_timeout_seconds"],
                                _remaining(
                                    effect_deadline, "workload inspect deadline",
                                    clock=self._clock)))
                        if not isinstance(inspected, dict):
                            raise _UnavailableSignal(
                                "workload_state_invalid", "post-workload inspect is malformed")
                        resolved.client.assert_policy_inspect(
                            inspected, container_id=container_id, input_dir=staging,
                            name=plan.name, labels=plan.labels,
                            network=resolved.policy["isolation"]["network"])
                        state = inspected.get("State")
                        if (not isinstance(state, dict) or state.get("Status") != "exited"
                                or state.get("Running") is not False
                                or isinstance(state.get("ExitCode"), bool)
                                or not isinstance(state.get("ExitCode"), int)
                                or not 0 <= state.get("ExitCode") <= 255
                                or not isinstance(state.get("Error"), str)
                                or type(state.get("OOMKilled")) is not bool
                                or state.get("Error")):
                            raise _UnavailableSignal(
                                "workload_state_invalid", "container did not inspect as exited")
                        if started.returncode != state["ExitCode"]:
                            raise _UnavailableSignal(
                                "workload_state_invalid",
                                "attached exit differs from inspected exit")
                        if state.get("OOMKilled") is True:
                            raise _UnavailableSignal("workload_oom", "container was OOM-killed")
                        exit_code = state["ExitCode"]
                        phase = "cleanup"
                        cleanup_deadline = self._cleanup_deadline(hard_deadline)
                        cleanup_client = self._bounded_client(resolved, cleanup_deadline)
                        self._store.mark_removing(plan.operation_id)
                        cleanup_client.remove_exact(
                            container_id=container_id, name=plan.name, labels=plan.labels,
                            input_dir=staging, network=resolved.policy["isolation"]["network"],
                            deadline=cleanup_deadline)
                        _remaining(
                            cleanup_deadline, "staging cleanup deadline", clock=self._clock)
                        cleanup_staging(self._store, plan.operation_id, resolved.policy)
                        _remaining(
                            cleanup_deadline, "journal finish deadline", clock=self._clock)
                        self._store.finish_operation(plan.operation_id, container_id)
                        _remaining(
                            cleanup_deadline, "cleanup completion deadline", clock=self._clock)
                        intent_started = False
                        phase = "postflight"
                        postflight_deadline = min(
                            hard_deadline,
                            self._clock() + resolved.policy["bounds"]["postflight_timeout_seconds"])
                        postflight = self._resolve(binding, postflight_deadline)
                        try:
                            staging.lstat()
                        except FileNotFoundError:
                            staging_absent = True
                        else:
                            staging_absent = False
                        journal_absent = not any(
                            row["operation_id"] == plan.operation_id
                            for row in self._store.read_journals())
                        return _admit_result(
                            plan, postflight, exit_code=exit_code,
                            stdout=started.stdout, stderr=started.stderr,
                            staging_absent=staging_absent, journal_absent=journal_absent)
                    except BaseException as failure:
                        cleanup_failure: BaseException | None = None
                        if intent_started:
                            try:
                                cleanup_deadline = self._cleanup_deadline(hard_deadline)
                                cleanup_client = self._bounded_client(
                                    resolved, cleanup_deadline)
                                recover_operations(
                                    self._store, cleanup_client, cleanup_deadline,
                                    clock=self._clock)
                            except BaseException as exc:
                                cleanup_failure = exc
                        if not isinstance(failure, Exception):
                            if cleanup_failure is not None:
                                raise failure from cleanup_failure
                            raise
                        if cleanup_failure is not None and not isinstance(cleanup_failure, Exception):
                            raise cleanup_failure
                        if cleanup_failure is not None:
                            raise SpikeExecutionUnavailable(
                                "cleanup_failed", self._detail(cleanup_failure)) from cleanup_failure
                        if isinstance(failure, _UnavailableSignal):
                            raise SpikeExecutionUnavailable(
                                failure.reason, failure.detail) from failure
                        raise SpikeExecutionUnavailable(
                            self._reason_for_phase(phase), self._detail(failure)) from failure
                finally:
                    lock.__exit__(None, None, None)
            except SpikeExecutionUnavailable:
                raise
            except _UnavailableSignal as failure:
                raise SpikeExecutionUnavailable(
                    failure.reason, failure.detail) from failure
            except Exception as exc:
                reason = ("scaffolding_unsafe" if phase == "authority"
                          else self._reason_for_phase(phase))
                raise SpikeExecutionUnavailable(reason, self._detail(exc)) from exc
        except SpikeExecutionUnavailable:
            raise
