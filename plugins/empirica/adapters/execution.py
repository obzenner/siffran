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
