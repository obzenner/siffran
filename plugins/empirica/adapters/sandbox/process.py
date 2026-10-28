"""Generic bounded subprocess capture used only by Docker discovery and qualification.

This module owns no image, profile, policy, Docker lifecycle, or compatibility behavior.
"""
from __future__ import annotations

import hashlib
import math
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

DIAGNOSTIC_TAIL_BYTES = 4096


@dataclass(frozen=True)
class StreamCapture:
    data: bytes
    bytes_seen: int
    sha256: str
    tail: str

    def report(self) -> dict[str, Any]:
        return {"bytes": self.bytes_seen, "sha256": self.sha256, "tail": self.tail}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    timed_out: bool
    output_limited: bool
    stdout: StreamCapture
    stderr: StreamCapture
    elapsed_ms: int

    @classmethod
    def fake(
        cls, returncode: int = 0, stdout: str | bytes = b"", stderr: str | bytes = b"", *,
        timed_out: bool = False, output_limited: bool = False,
    ) -> "CommandResult":
        return cls(
            returncode, timed_out, output_limited,
            _capture_bytes(stdout.encode() if isinstance(stdout, str) else stdout),
            _capture_bytes(stderr.encode() if isinstance(stderr, str) else stderr), 0,
        )

    def report(self) -> dict[str, Any]:
        return {
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "output_limited": self.output_limited,
            "stdout": self.stdout.report(),
            "stderr": self.stderr.report(),
            "elapsed_ms": self.elapsed_ms,
        }


class Runner(Protocol):
    def run(
        self, argv: Sequence[str], *, timeout_seconds: float,
        output_limit_bytes: int | None = None,
        stdout_limit_bytes: int | None = None,
        stderr_limit_bytes: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult: ...


def _capture_bytes(value: bytes) -> StreamCapture:
    return StreamCapture(
        value, len(value), "sha256:" + hashlib.sha256(value).hexdigest(),
        value[-DIAGNOSTIC_TAIL_BYTES:].decode("utf-8", errors="replace").strip(),
    )


class BoundedRunner:
    """Drain both pipes incrementally, hash/count them, and kill the owned process group at a bound."""

    def __init__(self, cwd: str | os.PathLike[str] | None = None, *, umask: int | None = None) -> None:
        self.cwd = None if cwd is None else os.fspath(cwd)
        self.umask = umask

    def run(
        self, argv: Sequence[str], *, timeout_seconds: float,
        output_limit_bytes: int | None = None,
        stdout_limit_bytes: int | None = None,
        stderr_limit_bytes: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        if output_limit_bytes is not None:
            if stdout_limit_bytes is not None or stderr_limit_bytes is not None:
                raise ValueError("runner accepts either one shared or two independent byte limits")
            stdout_limit_bytes = stderr_limit_bytes = output_limit_bytes
        limits = {"stdout": stdout_limit_bytes, "stderr": stderr_limit_bytes}
        if (not argv or isinstance(timeout_seconds, bool)
                or type(timeout_seconds) not in {int, float}
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
                or any(isinstance(limit, bool) or type(limit) is not int or limit <= 0
                       for limit in limits.values())):
            raise ValueError("runner requires argv, a finite positive timeout, and positive byte limits")
        began = time.monotonic_ns()
        child = subprocess.Popen(
            tuple(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=None if env is None else dict(env), start_new_session=True,
            cwd=self.cwd, umask=-1 if self.umask is None else self.umask,
        )
        assert child.stdout is not None and child.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ, "stdout")
        selector.register(child.stderr, selectors.EVENT_READ, "stderr")
        retained = {"stdout": bytearray(), "stderr": bytearray()}
        tails = {"stdout": bytearray(), "stderr": bytearray()}
        totals = {"stdout": 0, "stderr": 0}
        digests = {"stdout": hashlib.sha256(), "stderr": hashlib.sha256()}
        deadline = time.monotonic() + timeout_seconds
        timed_out = output_limited = killed = False

        def kill_group() -> None:
            nonlocal killed
            if killed:
                return
            killed = True
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                if child.poll() is None:
                    child.kill()

        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    kill_group()
                    remaining = 0.05
                events = selector.select(max(0.0, min(0.05, remaining if remaining > 0 else 0.05)))
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 65_536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    stream = key.data
                    totals[stream] += len(chunk)
                    digests[stream].update(chunk)
                    room = max(0, limits[stream] - len(retained[stream]))
                    retained[stream].extend(chunk[:room])
                    tails[stream].extend(chunk)
                    if len(tails[stream]) > DIAGNOSTIC_TAIL_BYTES:
                        del tails[stream][:-DIAGNOSTIC_TAIL_BYTES]
                    if totals[stream] > limits[stream]:
                        output_limited = True
                        kill_group()
                if output_limited:
                    deadline = min(deadline, time.monotonic() + 0.5)
            if child.poll() is None:
                remaining = max(0.01, deadline - time.monotonic())
                try:
                    child.wait(timeout=min(2.0, remaining))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    kill_group()
                    child.wait(timeout=2)
            else:
                child.wait()
        except BaseException:
            kill_group()
            try:
                child.wait(timeout=2)
            except BaseException:
                try:
                    child.kill()
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=2)
                except BaseException:
                    pass
            raise
        finally:
            selector.close()
            child.stdout.close()
            child.stderr.close()
        captures = {
            stream: StreamCapture(
                bytes(retained[stream]), totals[stream],
                "sha256:" + digests[stream].hexdigest(),
                tails[stream].decode("utf-8", errors="replace").strip(),
            )
            for stream in ("stdout", "stderr")
        }
        return CommandResult(
            child.returncode, timed_out, output_limited,
            captures["stdout"], captures["stderr"],
            (time.monotonic_ns() - began) // 1_000_000,
        )
