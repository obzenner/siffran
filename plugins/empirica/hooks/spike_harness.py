#!/usr/bin/env python3
"""M3 SpikeHarness — deterministic gate from a real check (ADR-13).

The gate is the exit code of an actual command in a subprocess (test, type-check,
lint — the deterministic trust boundary), never model judgment. Proven in the spike
at .claude/spike-m3.

gate == "pass" ⇔ the command exited 0; anything else (including timeout) is "fail".
The SpikeResult it prints is transient scratch (ADR-14) — capture it in .claude/
scratch, never commit.

subprocess runs with shell=False (argv list) — no shell metacharacter expansion. But
this is a TRUSTED-COMMAND RUNNER, not a sandbox: the command is model-controlled and runs
with the user's full environment and privileges (inherits PATH, no allowlist, no network
restriction). Treat the command source as trusted; do not point it at untrusted input.

Exit code (review 2.2): by default main() exits with the checked command's own status
(0 ⇔ gate=pass), so `spike_harness … && next` behaves like a real gate and CI/Bash see the
right colour. Use `--report-only` to always exit 0 and read the `gate` field from the JSON.

Usage:  python3 spike_harness.py [--timeout SEC] [--report-only] <cmd> [args...]
"""
import json
import os
import signal
import subprocess
import sys
from typing import TypedDict

DEFAULT_TIMEOUT = 300  # seconds; a spike check that runs longer is a failure signal
MAX_OUTPUT_BYTES = 1_048_576  # 1 MiB cap per stream — a chatty command can't OOM the hook
_LAUNCH_FAIL_RC = 127  # command could not be launched (missing/permission/OS error)


class SpikeResult(TypedDict):
    """The transient gate payload (ADR-14). `gate` is 'pass' iff returncode == 0."""
    cmd: list[str]
    returncode: int | None
    gate: str
    timed_out: bool
    stdout_tail: list[str]
    stderr_tail: list[str]


def _tail(text: str, n: int = 5) -> list[str]:
    if not text:
        return []
    # Cap before splitting so a pathological single line can't blow up memory here.
    if len(text) > MAX_OUTPUT_BYTES:
        text = text[-MAX_OUTPUT_BYTES:]
    return text.strip().splitlines()[-n:]


def run_gate(cmd: list[str], timeout: float = DEFAULT_TIMEOUT) -> SpikeResult:
    """Run a deterministic check; the gate is its real exit code, nothing softer.

    Robust by construction (review 1.5): a timeout, non-UTF-8 output, or a launch failure
    (missing exe / permission / OSError) all resolve to gate=fail with the reason captured
    — no unhandled exception path. On timeout the whole PROCESS GROUP is killed so forked
    descendants don't survive. Output is byte-capped so a chatty command can't exhaust
    memory.
    """
    # New session/process group so a timeout can kill the whole tree (POSIX).
    preexec = os.setsid if hasattr(os, "setsid") else None
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            errors="replace", preexec_fn=preexec,
        )
    except (OSError, ValueError) as exc:
        return SpikeResult(cmd=cmd, returncode=_LAUNCH_FAIL_RC, gate="fail", timed_out=False,
                           stdout_tail=[], stderr_tail=[f"launch failed: {exc}"])
    try:
        out, err = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, err = proc.communicate()
        return SpikeResult(cmd=cmd, returncode=None, gate="fail", timed_out=True,
                           stdout_tail=_tail(out), stderr_tail=[f"timed out after {timeout}s"])
    return SpikeResult(
        cmd=cmd,
        returncode=proc.returncode,
        gate="pass" if proc.returncode == 0 else "fail",
        timed_out=timed_out,
        stdout_tail=_tail(out),
        stderr_tail=_tail(err),
    )


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the child's whole process group on timeout so descendants don't leak."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - Windows
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def parse_args(argv: list[str]) -> tuple[list[str], float, bool]:
    """Split leading `--timeout SEC` / `--report-only` flags from the command to run.

    timeout is clamped to a finite positive value (review 1.5: a bogus timeout must not
    disable the guard).
    """
    timeout = DEFAULT_TIMEOUT
    report_only = False
    while argv:
        if argv[0] == "--report-only":
            report_only = True
            argv = argv[1:]
        elif argv[0] == "--timeout" and len(argv) >= 2:
            try:
                t = float(argv[1])
                if t > 0 and t != float("inf"):
                    timeout = t
            except ValueError:
                pass
            argv = argv[2:]
        else:
            break
    return argv, timeout, report_only


def main() -> int:
    cmd, timeout, report_only = parse_args(sys.argv[1:])
    if not cmd:
        print(json.dumps({"error": "usage: spike_harness.py [--timeout SEC] "
                                    "[--report-only] <cmd> [args...]"}))
        return 2
    result = run_gate(cmd, timeout)
    print(json.dumps(result, indent=2))
    if report_only:
        return 0  # always green; caller reads the `gate` field
    # Default: propagate the checked command's real status so the gate composes with
    # `&&`, CI, and Bash (review 2.2). Timeout/launch-failure normalise to a nonzero code.
    rc = result["returncode"]
    return rc if isinstance(rc, int) else 1


if __name__ == "__main__":
    sys.exit(main())
