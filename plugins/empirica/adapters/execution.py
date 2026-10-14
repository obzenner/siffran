"""Production filesystem observation and bounded immutable-snapshot execution adapters."""
from __future__ import annotations

import errno
import hashlib
import os
import resource
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from application.ports import CapturedFile, ExecutionSnapshot, HarnessResult, WorkspaceCapture
from core.freshness import (FileObservation, ObservationState, canonical_digest,
                            validate_relative_posix_path)

DEFAULT_SPIKE_TIMEOUT_SECONDS = 300
DEFAULT_OUTPUT_LIMIT_BYTES = 1 << 20
OUTPUT_LIMIT_EXIT_CODE = 125


class FilesystemWorkspace:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        root_stat = self.root.stat()
        self._identity = (root_stat.st_dev, root_stat.st_ino)

    @staticmethod
    def _read_at(root_fd: int, relative: str) -> CapturedFile:
        directory_fd = os.dup(root_fd)
        file_fd = -1
        try:
            parts = relative.split("/")
            for part in parts[:-1]:
                next_fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                return CapturedFile(
                    FileObservation(relative, ObservationState.NON_REGULAR, None), None)
            with os.fdopen(os.dup(file_fd), "rb") as stream:
                content = stream.read()
            after = os.fstat(file_fd)
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
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


class SubprocessSpikeHarness:
    def __init__(self, timeout_seconds: int = DEFAULT_SPIKE_TIMEOUT_SECONDS,
                 output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES):
        self.timeout_seconds, self.output_limit_bytes = timeout_seconds, output_limit_bytes

    @staticmethod
    def _stream_fact(stream) -> dict:
        size = stream.tell()
        stream.seek(0)
        return {"sha256": "sha256:" + hashlib.sha256(stream.read()).hexdigest(),
                "bytes": size}

    def run(self, command: str, snapshot: ExecutionSnapshot) -> HarnessResult:
        with tempfile.TemporaryDirectory(prefix="empirica-spike-") as directory:
            root = Path(directory)
            for binding, content in zip(snapshot.file_bindings, snapshot.captured_bytes):
                target = root.joinpath(*binding.path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                target.chmod(0o444)
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                def limit_output() -> None:
                    resource.setrlimit(resource.RLIMIT_FSIZE,
                                       (self.output_limit_bytes, self.output_limit_bytes))

                process = subprocess.Popen(
                    ["/bin/sh", "-c", command], cwd=root, stdout=stdout, stderr=stderr,
                    start_new_session=True, preexec_fn=limit_output)
                timed_out = False
                try:
                    process.wait(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                output_limited = max(stdout.tell(), stderr.tell()) >= self.output_limit_bytes
                exit_code = (OUTPUT_LIMIT_EXIT_CODE if output_limited else
                             124 if timed_out else process.returncode)
                result = canonical_digest({
                    "command": command, "exit_code": exit_code,
                    "stdout": self._stream_fact(stdout), "stderr": self._stream_fact(stderr),
                    "output_limit_bytes": self.output_limit_bytes,
                    "snapshot_digest": snapshot.snapshot_digest,
                })
        return HarnessResult(exit_code, result, snapshot.snapshot_digest)
