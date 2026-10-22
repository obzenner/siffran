"""Production filesystem observation and bounded immutable-snapshot execution adapters."""
from __future__ import annotations

import errno
import hashlib
import os
import stat
import time
from pathlib import Path

from application.ports import CapturedFile, WorkspaceCapture
from core import spike_plan
from core.freshness import (FileObservation, ObservationState, canonical_digest,
                            validate_relative_posix_path)


class FilesystemWorkspace:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        root_stat = self.root.stat()
        self._identity = (root_stat.st_dev, root_stat.st_ino)

    @staticmethod
    def _read_at(root_fd: int, relative: str,
                 maximum: int = spike_plan.MAX_FILE_SIZE_BYTES) -> CapturedFile:
        directory_fd = os.dup(root_fd)
        file_fd = -1
        try:
            parts = relative.split("/")
            for part in parts[:-1]:
                next_fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            # O_NONBLOCK: a leaf swapped for a FIFO opens without blocking and is then refused as
            # non-regular; it has no effect on reading a regular file.
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                              dir_fd=directory_fd)
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
                return CapturedFile(
                    FileObservation(relative, ObservationState.NON_REGULAR, None), None)
            with os.fdopen(os.dup(file_fd), "rb") as stream:
                content = stream.read(maximum + 1)
            after = os.fstat(file_fd)
            if (len(content) > maximum
                    or (before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode), before.st_size,
                        before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode), after.st_size,
                        after.st_mtime_ns, after.st_ctime_ns)):
                return CapturedFile(
                    FileObservation(relative, ObservationState.UNREADABLE, None), None)
            digest = "sha256:" + hashlib.sha256(content).hexdigest()
            return CapturedFile(
                FileObservation(relative, ObservationState.PRESENT, digest), content)
        except FileNotFoundError:
            return CapturedFile(FileObservation(relative, ObservationState.MISSING, None), None)
        except OSError as exc:
            state = (ObservationState.NON_REGULAR if exc.errno in {errno.ELOOP, errno.ENOTDIR}
                     else ObservationState.UNREADABLE)
            return CapturedFile(FileObservation(relative, state, None), None)
        finally:
            if file_fd >= 0:
                os.close(file_fd)
            os.close(directory_fd)

    # --- change detection for a reader (the observer's drift index); none of it is read by a run decision ----------

    def drift_id(self) -> str:
        """Which workspace this is, for a reader that indexes its files across polls: its root."""
        return str(self.root)

    @staticmethod
    def now_ns() -> int:
        """The reference a reader compares a file's change time with to tell a racily clean file (as Git does): this
        machine's wall clock, floored to the second, so a filesystem whose times are coarser than a nanosecond (up to
        one second) never makes a file written after a hash look older than it. A file changed in the same second is
        hashed again the next time; nothing is ordered or decided by it."""
        return time.time_ns() // 1_000_000_000 * 1_000_000_000

    def signatures(self, paths) -> dict[str, tuple | None]:
        """Each path's change signature, from ``lstat`` alone (nothing is opened or read): the root's identity, each
        directory on the way down by type and inode (what ``observe`` refuses to follow is told apart), and the file's
        type, inode, size, modification and change times. Users cannot set a change time, so every write changes the
        last field. A component that is not there ends the signature with ``None``."""
        try:
            root = os.lstat(self.root)
            head = (root.st_dev, root.st_ino)
        except OSError:
            head = None
        seen: dict[str, tuple | None] = {}
        out = {}
        base = str(self.root)
        for relative in paths:
            parts = relative.split("/")
            sig: list = [head]
            prefix = base
            for n, part in enumerate(parts):
                prefix = prefix + "/" + part
                leaf = n == len(parts) - 1
                if prefix not in seen or leaf:
                    try:
                        st = os.lstat(prefix)
                    except OSError as exc:
                        seen[prefix] = ("absent", exc.errno)
                    else:
                        seen[prefix] = ((stat.S_IFMT(st.st_mode), st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                                        if leaf else (stat.S_IFMT(st.st_mode), st.st_ino))
                sig.append(seen[prefix])
                if seen[prefix][0] == "absent" or (not leaf and seen[prefix][0] != stat.S_IFDIR):
                    break
            out[relative] = tuple(sig)
        return out

    @staticmethod
    def changed_at(signature: tuple | None) -> int | None:
        """The change time a signature holds for its file, or ``None`` (a file that is not there)."""
        leaf = signature[-1] if signature else None
        return leaf[4] if isinstance(leaf, tuple) and len(leaf) == 5 else None

    @staticmethod
    def basis_of(observations) -> str:
        """The ``basis_id`` ``observe`` gives a capture of exactly these observations."""
        return canonical_digest([(o.path, o.state.value, o.sha256) for o in observations])

    def capture_inputs(self, declarations: tuple[str, ...]) -> WorkspaceCapture:
        """Expand and capture one canonical tree without following symlinks or special files."""
        for declaration in declarations:
            candidate = declaration[:-1] if declaration.endswith("/") else declaration
            validate_relative_posix_path(candidate)
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            root_stat = os.fstat(root_fd)
            if (root_stat.st_dev, root_stat.st_ino) != self._identity:
                raise OSError("workspace root identity changed")
            paths: set[str] = set()
            total_bytes = 0

            def add_file(relative: str, item: os.stat_result) -> None:
                nonlocal total_bytes
                if relative in paths:
                    return
                if item.st_size < 0 or item.st_size > spike_plan.MAX_FILE_SIZE_BYTES:
                    raise OSError("captured input file exceeds the per-file byte ceiling")
                if len(paths) >= spike_plan.MAX_STAGING_FILES:
                    raise OSError("captured input file count exceeds the ceiling")
                if total_bytes + item.st_size > spike_plan.MAX_STAGING_BYTES:
                    raise OSError("captured input bytes exceed the aggregate ceiling")
                paths.add(relative)
                total_bytes += item.st_size

            def walk(directory_fd: int, prefix: str) -> None:
                for name in sorted(os.listdir(directory_fd)):
                    if name == ".git":
                        continue
                    relative = f"{prefix}/{name}" if prefix else name
                    item = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    kind = stat.S_IFMT(item.st_mode)
                    if kind == stat.S_IFLNK:
                        continue
                    if kind == stat.S_IFREG:
                        add_file(relative, item)
                    elif kind == stat.S_IFDIR:
                        child = os.open(
                            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory_fd)
                        try:
                            walk(child, relative)
                        finally:
                            os.close(child)
                    else:
                        continue

            for declaration in declarations:
                relative = declaration[:-1] if declaration.endswith("/") else declaration
                if ".git" in relative.split("/"):
                    raise OSError("an explicitly declared .git path is forbidden")
                parent = os.dup(root_fd)
                try:
                    parts = relative.split("/")
                    for component in parts[:-1]:
                        next_fd = os.open(
                            component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent)
                        os.close(parent)
                        parent = next_fd
                    leaf = parts[-1]
                    item = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                    kind = stat.S_IFMT(item.st_mode)
                    if kind == stat.S_IFLNK:
                        raise OSError("an explicitly declared symlink is forbidden")
                    if kind == stat.S_IFREG:
                        add_file(relative, item)
                    elif kind == stat.S_IFDIR:
                        child = os.open(
                            leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent)
                        try:
                            walk(child, relative)
                        finally:
                            os.close(child)
                    else:
                        raise OSError("an explicitly declared special file is forbidden")
                finally:
                    os.close(parent)
            ordered = tuple(sorted(paths))
            files = tuple(self._read_at(root_fd, relative, spike_plan.MAX_FILE_SIZE_BYTES)
                          for relative in ordered)
        finally:
            os.close(root_fd)
        basis = canonical_digest([(f.observation.path, f.observation.state.value,
                                   f.observation.sha256) for f in files])
        return WorkspaceCapture(basis, files)

    def observe(self, paths: tuple[str, ...]) -> WorkspaceCapture:
        for relative in paths:
            validate_relative_posix_path(relative)
        root_fd = -1
        try:
            root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            root_stat = os.fstat(root_fd)
            if (root_stat.st_dev, root_stat.st_ino) != self._identity:
                raise OSError("workspace root identity changed")
        except OSError:
            if root_fd >= 0:
                os.close(root_fd)
            files = tuple(CapturedFile(
                FileObservation(relative, ObservationState.UNREADABLE, None), None)
                for relative in paths)
        else:
            try:
                files = tuple(self._read_at(root_fd, relative) for relative in paths)
            finally:
                os.close(root_fd)
        basis = canonical_digest([(f.observation.path, f.observation.state.value,
                                   f.observation.sha256) for f in files])
        return WorkspaceCapture(basis, files)
