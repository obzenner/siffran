#!/usr/bin/env python3
"""M3 SpikeHarness — deterministic gate from a real check (ADR-13).

The gate is the exit code of an actual command in a subprocess (test, type-check,
lint — the deterministic trust boundary), never model judgment. Proven in the spike
at .claude/spike-m3.

gate == "pass" ⇔ the command exited 0; anything else (including timeout) is "fail".
The SpikeResult it prints is transient scratch (ADR-14) — capture it in .claude/
scratch, never commit.

subprocess is run with shell=False (argv list) — no shell interpolation, no injection
surface. Running the given argv IS the intended function (an agent-driven harness).

Usage:  python3 spike_harness.py [--timeout SEC] <cmd> [args...]
"""
import json
import subprocess
import sys
from typing import TypedDict

DEFAULT_TIMEOUT = 300  # seconds; a spike check that runs longer is a failure signal


class SpikeResult(TypedDict):
    """The transient gate payload (ADR-14). `gate` is 'pass' iff returncode == 0."""
    cmd: list[str]
    returncode: int | None
    gate: str
    timed_out: bool
    stdout_tail: list[str]
    stderr_tail: list[str]


def _tail(text: str, n: int = 5) -> list[str]:
    return text.strip().splitlines()[-n:] if text else []


def run_gate(cmd: list[str], timeout: float = DEFAULT_TIMEOUT) -> SpikeResult:
    """Run a deterministic check; the gate is its real exit code, nothing softer.

    A timeout or non-UTF-8 output never crashes the harness — both resolve to gate=fail
    with the reason captured, preserving the 'harness exits 0, gate lives in payload'
    contract (adversarial review: no unhandled decode/hang path).
    """
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        return SpikeResult(
            cmd=cmd, returncode=None, gate="fail", timed_out=True,
            stdout_tail=_tail(exc.stdout or "" if isinstance(exc.stdout, str) else ""),
            stderr_tail=[f"timed out after {timeout}s"],
        )
    return SpikeResult(
        cmd=cmd,
        returncode=proc.returncode,
        gate="pass" if proc.returncode == 0 else "fail",
        timed_out=False,
        stdout_tail=_tail(proc.stdout),
        stderr_tail=_tail(proc.stderr),
    )


def parse_args(argv: list[str]) -> tuple[list[str], float]:
    """Split an optional leading `--timeout SEC` from the command to run."""
    timeout = DEFAULT_TIMEOUT
    if len(argv) >= 2 and argv[0] == "--timeout":
        try:
            timeout = float(argv[1])
        except ValueError:
            pass
        argv = argv[2:]
    return argv, timeout


def main() -> int:
    cmd, timeout = parse_args(sys.argv[1:])
    if not cmd:
        print(json.dumps({"error": "usage: spike_harness.py [--timeout SEC] <cmd> [args...]"}))
        return 2
    print(json.dumps(run_gate(cmd, timeout), indent=2))
    return 0  # gate lives in the payload, not this process's exit code


if __name__ == "__main__":
    sys.exit(main())
