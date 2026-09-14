"""Production filesystem observation and immutable-snapshot subprocess adapters."""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
from pathlib import Path

from application.ports import CapturedFile, ExecutionSnapshot, HarnessResult, WorkspaceCapture
from core.freshness import (FileObservation, ObservationState, canonical_digest,
                            validate_relative_posix_path)


class FilesystemWorkspace:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)

    def observe(self, paths: tuple[str, ...]) -> WorkspaceCapture:
        files = []
        for relative in sorted(set(paths)):
            validate_relative_posix_path(relative)
            target = self.root.joinpath(*relative.split("/"))
            try:
                resolved = target.resolve(strict=False)
                if os.path.commonpath((str(self.root), str(resolved))) != str(self.root):
                    observation = FileObservation(relative, ObservationState.OUTSIDE_WORKSPACE, None)
                    files.append(CapturedFile(observation, None))
                    continue
                mode = target.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    observation = FileObservation(relative, ObservationState.NON_REGULAR, None)
                    files.append(CapturedFile(observation, None))
                    continue
                content = target.read_bytes()
                digest = "sha256:" + hashlib.sha256(content).hexdigest()
                files.append(CapturedFile(
                    FileObservation(relative, ObservationState.PRESENT, digest), content))
            except FileNotFoundError:
                files.append(CapturedFile(
                    FileObservation(relative, ObservationState.MISSING, None), None))
            except OSError:
                files.append(CapturedFile(
                    FileObservation(relative, ObservationState.UNREADABLE, None), None))
        basis = canonical_digest([(f.observation.path, f.observation.state.value,
                                   f.observation.sha256) for f in files])
        return WorkspaceCapture(basis, tuple(files))


class SubprocessSpikeHarness:
    def __init__(self, timeout_seconds: int = 300):
        self.timeout_seconds = timeout_seconds

    def run(self, command: str, snapshot: ExecutionSnapshot) -> HarnessResult:
        with tempfile.TemporaryDirectory(prefix="empirica-spike-") as directory:
            root = Path(directory)
            for binding, content in zip(snapshot.file_bindings, snapshot.captured_bytes):
                target = root.joinpath(*binding.path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            try:
                completed = subprocess.run(
                    ["/bin/sh", "-c", command], cwd=root, capture_output=True,
                    timeout=self.timeout_seconds, check=False)
                exit_code = completed.returncode
                result = canonical_digest({"command": command, "exit_code": exit_code,
                                           "stdout": completed.stdout.hex(),
                                           "stderr": completed.stderr.hex(),
                                           "snapshot_digest": snapshot.snapshot_digest})
            except subprocess.TimeoutExpired as exc:
                exit_code = 124
                result = canonical_digest({"command": command, "exit_code": exit_code,
                                           "stdout": (exc.stdout or b"").hex(),
                                           "stderr": (exc.stderr or b"").hex(),
                                           "snapshot_digest": snapshot.snapshot_digest})
        return HarnessResult(exit_code, result, snapshot.snapshot_digest)
