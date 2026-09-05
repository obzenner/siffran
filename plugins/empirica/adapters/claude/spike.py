#!/usr/bin/env python3
"""Deterministic spike executable used exclusively by the Claude knowledge adapter.

It runs argv without a shell, bounds retained output, kills the process group on timeout, and
reports a JSON gate derived only from real subprocess return codes.  It owns no runtime state.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import threading
from typing import TypedDict

DEFAULT_TIMEOUT = 300.0
MAX_REPEAT = 100
MAX_OUTPUT_BYTES = 1_048_576
MAX_LINE = 2000


class SpikeResult(TypedDict):
    cmd: list[str]
    returncode: int | None
    gate: str
    timed_out: bool
    stdout_tail: list[str]
    stderr_tail: list[str]


def _tail(text: str, count: int = 5) -> list[str]:
    return [line if len(line) <= MAX_LINE else line[:MAX_LINE] + "…"
            for line in text.strip().splitlines()[-count:]] if text else []


class _Reader(threading.Thread):
    def __init__(self, pipe) -> None:
        super().__init__(daemon=True)
        self.pipe, self.buf = pipe, b""

    def run(self) -> None:
        try:
            while chunk := self.pipe.read(65536):
                self.buf = (self.buf + chunk)[-MAX_OUTPUT_BYTES:]
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.pipe.close()
            except OSError:
                pass

    def text(self) -> str:
        return self.buf.decode("utf-8", errors="replace")


def _kill(proc: subprocess.Popen) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover
            proc.kill()
    except (OSError, ProcessLookupError):
        proc.kill()


def run_gate(command: list[str], timeout: float = DEFAULT_TIMEOUT) -> SpikeResult:
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                preexec_fn=os.setsid if hasattr(os, "setsid") else None)
    except (OSError, ValueError) as exc:
        return {"cmd": command, "returncode": 127, "gate": "fail", "timed_out": False,
                "stdout_tail": [], "stderr_tail": [f"launch failed: {exc}"]}
    stdout, stderr = _Reader(proc.stdout), _Reader(proc.stderr)
    stdout.start()
    stderr.start()
    try:
        proc.wait(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill(proc)
        proc.wait()
        timed_out = True
    stdout.join(timeout=5)
    stderr.join(timeout=5)
    return {"cmd": command, "returncode": None if timed_out else proc.returncode,
            "gate": "pass" if not timed_out and proc.returncode == 0 else "fail",
            "timed_out": timed_out, "stdout_tail": _tail(stdout.text()),
            "stderr_tail": ([f"timed out after {timeout}s"] if timed_out else _tail(stderr.text()))}


def run_gate_repeated(command: list[str], timeout: float = DEFAULT_TIMEOUT,
                      repeat: int = 1) -> SpikeResult:
    runs, result = [], None
    for _ in range(max(1, min(MAX_REPEAT, repeat))):
        result = run_gate(command, timeout)
        runs.append({"returncode": result["returncode"], "gate": result["gate"],
                     "timed_out": result["timed_out"], "stdout_sha256": hashlib.sha256(
                         "\n".join(result["stdout_tail"]).encode()).hexdigest()})
        if result["gate"] != "pass":
            break
    assert result is not None
    if repeat > 1:
        result["runs"] = runs  # type: ignore[typeddict-unknown-key]
        result["repeat"] = repeat  # type: ignore[typeddict-unknown-key]
    return result


def main(argv: list[str]) -> int:
    timeout, repeat, index = DEFAULT_TIMEOUT, 1, 0
    while index < len(argv):
        if argv[index] == "--report-only":
            index += 1
        elif argv[index] in ("--timeout", "--repeat") and index + 1 < len(argv):
            raw = argv[index + 1]
            if argv[index] == "--timeout":
                try:
                    parsed = float(raw)
                    timeout = parsed if math.isfinite(parsed) and parsed > 0 else DEFAULT_TIMEOUT
                except ValueError:
                    timeout = DEFAULT_TIMEOUT
            else:
                try:
                    repeat = max(1, min(MAX_REPEAT, int(raw)))
                except ValueError:
                    repeat = 1
            index += 2
        else:
            break
    command = argv[index:]
    result = (run_gate_repeated(command, timeout, repeat) if command else
              {"cmd": [], "returncode": 127, "gate": "fail", "timed_out": False,
               "stdout_tail": [], "stderr_tail": ["no command"]})
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
