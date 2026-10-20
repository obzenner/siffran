"""Descriptor-relative immutable staging for one captured `/2` execution snapshot."""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from application.generic_spike import GenericExecutionSnapshot
from core import spike_plan
from core.freshness import validate_digest256, validate_relative_posix_path

from . import common

MAX_DEPTH = 64
MAX_ENTRIES = spike_plan.MAX_STAGING_FILES * 4
_CHUNK = 64 * 1024
_OPERATION = re.compile(r"^o-[0-9a-f]{32}$")
_TOKEN = re.compile(r"^stg-[0-9a-f]{32}$")


class GenericStagingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StagedFile:
    path: str
    sha256: str
    size: int
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class StagedDirectory:
    path: str
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class GenericStagingManifest:
    operation_id: str
    staging_token: str
    plan_digest: str
    snapshot_digest: str
    root: Path
    root_device: int
    root_inode: int
    directories: tuple[StagedDirectory, ...]
    files: tuple[StagedFile, ...]

    def __post_init__(self) -> None:
        validate_digest256(self.plan_digest)
        validate_digest256(self.snapshot_digest)
        if (_OPERATION.fullmatch(self.operation_id) is None
                or _TOKEN.fullmatch(self.staging_token) is None
                or not self.root.is_absolute()
                or tuple(sorted(self.directories, key=lambda item: item.path)) != self.directories
                or tuple(sorted(self.files, key=lambda item: item.path)) != self.files):
            raise ValueError("staging manifest is malformed")


def _open_dir(name: str | Path, *, dir_fd: int | None = None, mode: int) -> tuple[int, os.stat_result]:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                     | getattr(os, "O_CLOEXEC", 0), dir_fd=dir_fd)
        info = os.fstat(fd)
    except OSError as exc:
        raise GenericStagingError("staging directory cannot be opened without following links") from exc
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != mode):
        os.close(fd)
        raise GenericStagingError("staging directory identity or mode differs")
    return fd, info


def _names(fd: int) -> tuple[str, ...]:
    try:
        names = tuple(sorted(entry.name for entry in os.scandir(fd)))
    except OSError as exc:
        raise GenericStagingError("staging directory cannot be enumerated") from exc
    if len(names) > MAX_ENTRIES:
        raise GenericStagingError("staging entry bound exceeded")
    return names


def _write(fd: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        try:
            count = os.write(fd, value[offset:offset + _CHUNK])
        except OSError as exc:
            raise GenericStagingError("staging write failed") from exc
        if count <= 0:
            raise GenericStagingError("staging write made no progress")
        offset += count


def _hash(fd: int, maximum: int) -> tuple[str, int, os.stat_result]:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, _CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise GenericStagingError("staging file exceeds its bound")
            digest.update(chunk)
        return "sha256:" + digest.hexdigest(), total, os.fstat(fd)
    except OSError as exc:
        raise GenericStagingError("staging file cannot be verified") from exc


def _snapshot_rows(plan: spike_plan.SealedSpikePlan,
                   snapshot: GenericExecutionSnapshot) -> tuple[tuple[str, str, bytes], ...]:
    if type(plan) is not spike_plan.SealedSpikePlan or type(snapshot) is not GenericExecutionSnapshot:
        raise GenericStagingError("staging requires exact sealed plan and snapshot")
    expected = tuple((item.path, item.sha256, item.bytes) for item in plan.captured_files)
    actual = tuple((item.path, item.sha256, len(content))
                   for item, content in zip(snapshot.file_bindings, snapshot.captured_bytes))
    if expected != actual or len(actual) > spike_plan.MAX_STAGING_FILES \
            or sum(size for _, _, size in actual) > spike_plan.MAX_STAGING_BYTES:
        raise GenericStagingError("sealed plan and immutable capture differ")
    rows = []
    for binding, content in zip(snapshot.file_bindings, snapshot.captured_bytes):
        validate_relative_posix_path(binding.path)
        if ".git" in binding.path.split("/") or len(binding.path.split("/")) > MAX_DEPTH:
            raise GenericStagingError("captured staging path is forbidden")
        if "sha256:" + hashlib.sha256(content).hexdigest() != binding.sha256:
            raise GenericStagingError("captured staging bytes differ")
        rows.append((binding.path, binding.sha256, content))
    return tuple(rows)


def materialize_generic_staging(
    store: common.ExecutionStore,
    operation_id: str,
    plan: spike_plan.SealedSpikePlan,
    snapshot: GenericExecutionSnapshot,
    staging_token: str,
) -> GenericStagingManifest:
    """Publish one immutable operation tree using only pre-CAS captured bytes."""
    if (type(store) is not common.ExecutionStore or _OPERATION.fullmatch(operation_id or "") is None
            or _TOKEN.fullmatch(staging_token or "") is None):
        raise GenericStagingError("staging owner or identity is malformed")
    rows = _snapshot_rows(plan, snapshot)
    parent_fd = build_fd = -1
    temporary = "." + operation_id + ".tmp"
    try:
        parent_fd, _ = _open_dir(store.staging, mode=0o700)
        if temporary in _names(parent_fd) or operation_id in _names(parent_fd):
            raise GenericStagingError("staging operation already exists or has residue")
        try:
            os.mkdir(temporary, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except OSError as exc:
            raise GenericStagingError("staging private root cannot be created") from exc
        build_fd, _ = _open_dir(temporary, dir_fd=parent_fd, mode=0o700)
        opened: dict[str, int] = {"": build_fd}
        files: list[StagedFile] = []
        directories: list[StagedDirectory] = []
        try:
            for relative, expected_digest, content in rows:
                parts = relative.split("/")
                current = build_fd
                prefix = ""
                for part in parts[:-1]:
                    prefix = part if not prefix else prefix + "/" + part
                    if prefix not in opened:
                        try:
                            os.mkdir(part, 0o700, dir_fd=current)
                            os.fsync(current)
                        except OSError as exc:
                            raise GenericStagingError("staging directory creation failed") from exc
                        child, _ = _open_dir(part, dir_fd=current, mode=0o700)
                        opened[prefix] = child
                    current = opened[prefix]
                try:
                    fd = os.open(parts[-1], os.O_RDWR | os.O_CREAT | os.O_EXCL
                                 | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                                 0o600, dir_fd=current)
                except OSError as exc:
                    raise GenericStagingError("staging file cannot be created exclusively") from exc
                try:
                    before = os.fstat(fd)
                    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                            or before.st_nlink != 1 or before.st_size != 0
                            or stat.S_IMODE(before.st_mode) != 0o600):
                        raise GenericStagingError("new staging file identity is unsafe")
                    _write(fd, content)
                    os.fsync(fd)
                    observed, size, after = _hash(fd, spike_plan.MAX_FILE_SIZE_BYTES)
                    if (observed != expected_digest or size != len(content)
                            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                            or after.st_nlink != 1):
                        raise GenericStagingError("staging file changed while written")
                    os.fchmod(fd, 0o444)
                    os.fsync(fd)
                    observed, size, sealed = _hash(fd, spike_plan.MAX_FILE_SIZE_BYTES)
                    if (observed != expected_digest or stat.S_IMODE(sealed.st_mode) != 0o444
                            or sealed.st_uid != os.getuid() or sealed.st_nlink != 1):
                        raise GenericStagingError("staging file changed while sealed")
                    files.append(StagedFile(relative, observed, size, sealed.st_dev, sealed.st_ino))
                finally:
                    os.close(fd)
                os.fsync(current)
            for prefix in sorted((item for item in opened if item),
                                 key=lambda item: item.count("/"), reverse=True):
                fd = opened[prefix]
                os.fchmod(fd, 0o555)
                os.fsync(fd)
                info = os.fstat(fd)
                directories.append(StagedDirectory(prefix, info.st_dev, info.st_ino))
            os.fchmod(build_fd, 0o555)
            os.fsync(build_fd)
            root_info = os.fstat(build_fd)
        finally:
            for prefix, fd in tuple(opened.items()):
                if prefix:
                    os.close(fd)
        os.rename(temporary, operation_id, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
        root = store.staging / operation_id
        manifest = GenericStagingManifest(
            operation_id, staging_token, plan.digest, snapshot.snapshot_digest, root,
            root_info.st_dev, root_info.st_ino,
            tuple(sorted(directories, key=lambda item: item.path)),
            tuple(sorted(files, key=lambda item: item.path)))
        revalidate_generic_staging(store, plan, snapshot, manifest)
        return manifest
    finally:
        if build_fd >= 0:
            os.close(build_fd)
        if parent_fd >= 0:
            os.close(parent_fd)


def revalidate_generic_staging(
    store: common.ExecutionStore,
    plan: spike_plan.SealedSpikePlan,
    snapshot: GenericExecutionSnapshot,
    manifest: GenericStagingManifest,
) -> Path:
    """Verify exact membership, inode identity, modes, bytes, and capture binding."""
    rows = _snapshot_rows(plan, snapshot)
    if (type(manifest) is not GenericStagingManifest or manifest.plan_digest != plan.digest
            or manifest.snapshot_digest != snapshot.snapshot_digest
            or manifest.root != store.staging / manifest.operation_id):
        raise GenericStagingError("staging manifest does not bind this capture")
    expected_files = {path: (digest, content) for path, digest, content in rows}
    expected_dirs = {item.path: item for item in manifest.directories}
    parent_fd = root_fd = -1
    visited: set[str] = set()
    try:
        parent_fd, _ = _open_dir(store.staging, mode=0o700)
        root_fd, root_info = _open_dir(manifest.operation_id, dir_fd=parent_fd, mode=0o555)
        if (root_info.st_dev, root_info.st_ino) != (manifest.root_device, manifest.root_inode):
            raise GenericStagingError("staging root was replaced")
        file_facts = {item.path: item for item in manifest.files}
        if set(file_facts) != set(expected_files):
            raise GenericStagingError("staging file manifest differs")

        def walk(fd: int, prefix: str, depth: int) -> None:
            if depth > MAX_DEPTH:
                raise GenericStagingError("staging depth bound exceeded")
            for name in _names(fd):
                relative = name if not prefix else prefix + "/" + name
                visited.add(relative)
                try:
                    before = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except OSError as exc:
                    raise GenericStagingError("staging entry cannot be inspected") from exc
                if relative in expected_dirs:
                    child_fd, opened = _open_dir(name, dir_fd=fd, mode=0o555)
                    try:
                        expected = expected_dirs[relative]
                        if (opened.st_dev, opened.st_ino) != (expected.device, expected.inode):
                            raise GenericStagingError("staging directory identity drifted")
                        walk(child_fd, relative, depth + 1)
                    finally:
                        os.close(child_fd)
                elif relative in expected_files:
                    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
                        raise GenericStagingError("staging file type drifted")
                    try:
                        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW
                                          | getattr(os, "O_CLOEXEC", 0), dir_fd=fd)
                    except OSError as exc:
                        raise GenericStagingError("staging file cannot be opened") from exc
                    try:
                        digest_value, size, after = _hash(file_fd, spike_plan.MAX_FILE_SIZE_BYTES)
                    finally:
                        os.close(file_fd)
                    fact = file_facts[relative]
                    expected_digest, content = expected_files[relative]
                    if ((before.st_dev, before.st_ino) != (fact.device, fact.inode)
                            or (after.st_dev, after.st_ino) != (fact.device, fact.inode)
                            or before.st_uid != os.getuid() or before.st_nlink != 1
                            or stat.S_IMODE(before.st_mode) != 0o444
                            or digest_value != expected_digest or size != len(content)):
                        raise GenericStagingError("staging file identity or bytes drifted")
                else:
                    raise GenericStagingError("staging contains an extra entry")

        walk(root_fd, "", 0)
        if visited != set(expected_dirs) | set(expected_files):
            raise GenericStagingError("staging membership differs")
        return manifest.root
    finally:
        if root_fd >= 0:
            os.close(root_fd)
        if parent_fd >= 0:
            os.close(parent_fd)


def cleanup_generic_staging(store: common.ExecutionStore, operation_id: str) -> None:
    """Remove one exact operation tree without following links, then prove path absence."""
    if type(store) is not common.ExecutionStore or _OPERATION.fullmatch(operation_id or "") is None:
        raise GenericStagingError("staging cleanup identity is malformed")
    parent_fd = root_fd = -1
    entries = total = 0

    def remove(fd: int, depth: int) -> None:
        nonlocal entries, total
        if depth > MAX_DEPTH:
            raise GenericStagingError("staging cleanup depth exceeded")
        for name in _names(fd):
            entries += 1
            if entries > MAX_ENTRIES:
                raise GenericStagingError("staging cleanup entry bound exceeded")
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if before.st_uid != os.getuid() or stat.S_ISLNK(before.st_mode):
                raise GenericStagingError("staging cleanup encountered foreign identity")
            if stat.S_ISDIR(before.st_mode):
                child, opened = _open_dir(name, dir_fd=fd, mode=stat.S_IMODE(before.st_mode))
                try:
                    if stat.S_IMODE(opened.st_mode) not in {0o555, 0o700}:
                        raise GenericStagingError("staging cleanup directory mode differs")
                    os.fchmod(child, 0o700)
                    remove(child, depth + 1)
                    os.fsync(child)
                finally:
                    os.close(child)
                after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                    raise GenericStagingError("staging cleanup directory was replaced")
                os.rmdir(name, dir_fd=fd)
            elif stat.S_ISREG(before.st_mode):
                total += before.st_size
                if (before.st_nlink != 1 or total > spike_plan.MAX_STAGING_BYTES
                        or stat.S_IMODE(before.st_mode) not in {0o444, 0o600}):
                    raise GenericStagingError("staging cleanup file identity differs")
                opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    info = os.fstat(opened)
                finally:
                    os.close(opened)
                if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
                    raise GenericStagingError("staging cleanup file was replaced")
                os.unlink(name, dir_fd=fd)
            else:
                raise GenericStagingError("staging cleanup encountered a special file")
            os.fsync(fd)

    try:
        parent_fd, _ = _open_dir(store.staging, mode=0o700)
        for target in (operation_id, "." + operation_id + ".tmp"):
            try:
                before = os.stat(target, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
                raise GenericStagingError("staging cleanup root type differs")
            root_fd, opened = _open_dir(target, dir_fd=parent_fd,
                                        mode=stat.S_IMODE(before.st_mode))
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise GenericStagingError("staging cleanup root was replaced")
            os.fchmod(root_fd, 0o700)
            remove(root_fd, 0)
            os.fsync(root_fd)
            os.close(root_fd)
            root_fd = -1
            after = os.stat(target, dir_fd=parent_fd, follow_symlinks=False)
            if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                raise GenericStagingError("staging cleanup root changed")
            os.rmdir(target, dir_fd=parent_fd)
            os.fsync(parent_fd)
        for target in (operation_id, "." + operation_id + ".tmp"):
            try:
                os.stat(target, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise GenericStagingError("staging cleanup did not prove absence")
    finally:
        if root_fd >= 0:
            os.close(root_fd)
        if parent_fd >= 0:
            os.close(parent_fd)
