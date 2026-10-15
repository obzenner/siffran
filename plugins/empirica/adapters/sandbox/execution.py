"""Unreachable Docker execution substrate: bounded lock, staging, and recovery only.

No application transaction imports this module.  The run-bound DockerSpikeHarness is deliberately
reserved for 3C-b; this slice owns only lifecycle mechanics exercised with fake Docker runners.
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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapters.state import fsio
from application.ports import ExecutionSnapshot
from core.freshness import validate_relative_posix_path

from . import docker, record

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


def _remaining(deadline: float, message: str) -> float:
    if (type(deadline) not in {int, float} or isinstance(deadline, bool)
            or not math.isfinite(deadline)):
        raise ExecutionSubstrateError(message + " is malformed")
    value = deadline - time.monotonic()
    if value <= 0:
        raise ExecutionSubstrateError(message + " expired")
    return value


@contextmanager
def runtime_lock(store: record.QualificationStore, deadline: float) -> Iterator[None]:
    """Acquire the operator-created process lock without waiting beyond a monotonic deadline."""
    store.check_execution_scaffolding()
    _remaining(deadline, "runtime lock deadline")
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
                remaining = _remaining(deadline, "runtime lock deadline")
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
    store: record.QualificationStore, client: docker.DockerClient, deadline: float,
) -> tuple[str, ...]:
    """Recover every exact journal while the caller holds the runtime lock."""
    store.check_execution_scaffolding()
    _remaining(deadline, "recovery deadline")
    _clean_incomplete_journal_entries(store)
    _remaining(deadline, "recovery deadline")
    recovered = []
    for row in store.read_journals():
        _remaining(deadline, "recovery deadline")
        _authority_matches_client(store, row, client)
        _remaining(deadline, "recovery deadline")
        operation_id = row["operation_id"]
        staging = store.staging / operation_id
        input_dir = staging
        network = client.policy["isolation"]["network"]
        remaining = _remaining(deadline, "recovery deadline")
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
