"""Closed bounded Docker CLI process effects for the one-container `/2` lifecycle."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Mapping

from . import generic_admission as admission
from . import process

CONTROL_BYTES = 256 * 1024
_ALLOWED = frozenset({
    ("container", "create"), ("container", "inspect"), ("container", "start"),
    ("container", "rm"), ("container", "ls"), ("network", "create"), ("network", "inspect"),
    ("network", "rm"), ("network", "ls"),
})
_ENV_KEYS = frozenset({"LANG", "LC_ALL", "DOCKER_HOST", "DOCKER_CONFIG"})


class GenericEffectsError(RuntimeError):
    pass


class GenericEffectsRefused(GenericEffectsError):
    """Raised before any process launch, so no Docker effect can have happened."""
    before_launch = True


def _cli(value: Path) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise GenericEffectsError("Docker CLI path is malformed")
    try:
        canonical = value.resolve(strict=True)
        info = canonical.stat()
    except OSError as exc:
        raise GenericEffectsError("Docker CLI is unavailable") from exc
    if (canonical != value or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) & 0o002 or not os.access(canonical, os.X_OK)):
        raise GenericEffectsError(
            "Docker CLI must be canonical, regular, executable, and not world-writable")
    return canonical


def _environment(value: Mapping[str, str]) -> dict[str, str]:
    if type(value) is not dict or frozenset(value) != _ENV_KEYS \
            or value.get("LANG") != "C" or value.get("LC_ALL") != "C" \
            or any(type(item) is not str or "\x00" in item for item in value.values()):
        raise GenericEffectsError("Docker process environment is not closed")
    host = value["DOCKER_HOST"]
    endpoint = Path(host.removeprefix("unix://")) if host.startswith("unix:///") else Path("")
    config = Path(value["DOCKER_CONFIG"])
    if not endpoint.is_absolute() or not config.is_absolute():
        raise GenericEffectsError("Docker process authority is not local and absolute")
    try:
        before = config.lstat()
        entries = tuple(config.iterdir())
        after = config.stat()
    except OSError as exc:
        raise GenericEffectsError("Docker config authority is unavailable") from exc
    if (stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(after.st_mode)
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or after.st_uid != os.getuid() or stat.S_IMODE(after.st_mode) != 0o700 or entries):
        raise GenericEffectsError(
            "Docker config authority must be an empty owner-only directory; remove foreign "
            "files from the selected private profile config and requalify")
    return dict(value)


class ConcreteGenericEffects:
    """No-shell process owner; each call has independent time/output bounds."""
    def __init__(self, docker_cli: Path, environment: Mapping[str, str], cwd: Path,
                 *, runner: process.Runner | None = None) -> None:
        self.cli = _cli(docker_cli)
        self.environment = _environment(dict(environment))
        try:
            canonical_cwd = cwd.resolve(strict=True)
            info = canonical_cwd.stat()
        except OSError as exc:
            raise GenericEffectsError("Docker process cwd is unavailable") from exc
        if canonical_cwd != cwd or not stat.S_ISDIR(info.st_mode):
            raise GenericEffectsError("Docker process cwd is not canonical")
        self.cwd = cwd
        self.runner = process.BoundedRunner(cwd=cwd, umask=0o077) if runner is None else runner

    def _argv(self, argv: tuple[str, ...]) -> tuple[str, ...]:
        """Admit only the closed `(argv[0], argv[1])` subcommand vocabulary.

        Later tokens are option values or, after the image, the author's guest argv: Docker stops
        flag parsing at the image (`SetInterspersed(false)`) and the planner proves the pre-image
        flags, so a guest word such as `build` or `--pid` is never Docker authority.
        """
        if (type(argv) is not tuple or len(argv) < 2
                or any(type(item) is not str or "\x00" in item for item in argv)
                or argv[:2] not in _ALLOWED):
            raise GenericEffectsRefused("Docker argv is outside the closed lifecycle vocabulary")
        return (str(self.cli), *argv)

    @staticmethod
    def _stream(value: process.StreamCapture) -> admission.RawStreamObservation:
        return admission.RawStreamObservation(value.data, value.bytes_seen, value.sha256)

    def run(self, argv: tuple[str, ...], *, timeout_seconds: float,
            output_limit_bytes: int = CONTROL_BYTES) -> admission.RawProcessObservation:
        command = self._argv(argv)
        result = self.runner.run(command, timeout_seconds=timeout_seconds,
                                 output_limit_bytes=output_limit_bytes,
                                 env=self.environment)
        if type(result) is not process.CommandResult:
            raise GenericEffectsError("Docker runner returned an unknown result")
        return admission.RawProcessObservation(
            argv, True, result.returncode, result.timed_out, result.output_limited, False,
            self._stream(result.stdout), self._stream(result.stderr))

    def attach(self, argv: tuple[str, ...], *, timeout_seconds: float,
               stdout_limit: int, stderr_limit: int) -> admission.RawAttachObservation:
        command = self._argv(argv)
        result = self.runner.run(
            command, timeout_seconds=timeout_seconds,
            stdout_limit_bytes=stdout_limit, stderr_limit_bytes=stderr_limit,
            env=self.environment)
        if type(result) is not process.CommandResult:
            raise GenericEffectsError("Docker runner returned an unknown result")
        raw = admission.RawProcessObservation(
            argv, True, result.returncode, result.timed_out, result.output_limited, False,
            self._stream(result.stdout), self._stream(result.stderr))
        natural = (not raw.timed_out and not raw.output_limited and not raw.process_ambiguous
                   and type(raw.returncode) is int and not isinstance(raw.returncode, bool)
                   and 0 <= raw.returncode <= 255)
        return admission.RawAttachObservation(
            raw, natural, not natural, raw.returncode if natural else None)

    def close(self) -> None:
        return None
