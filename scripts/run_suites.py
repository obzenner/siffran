#!/usr/bin/env python3
"""Run named suites in parallel, each with its own log, and fail if any suite fails.

Usage: run_suites.py NAME='COMMAND ARGS...' [NAME='COMMAND ...' ...]

The Makefile declares the suites and passes them in; this runner only executes them. Every suite runs in its own process
group, and its stdout and stderr go to ``<logdir>/<NAME>.log`` (a fresh temporary directory, kept and printed). One status
line per suite shows its result and elapsed seconds, and a failed suite's log is printed the moment it fails. Exit status is
1 if any suite failed, else 0.

Limits: a suite that runs longer than ``RUN_SUITES_TIMEOUT`` seconds (a finite number, default 600) is killed and fails with
a ``timed out`` line. Every launched process group is tracked until it has been cleaned up, even after its leader exits
(a leader's exit is observed without reaping it, so its group id cannot be reused meanwhile). On completion, failure, timeout
or interruption, every group still holding a process gets SIGTERM, then SIGKILL after a 5 s grace, and its leader is
reaped; the runner then checks that every group is empty. The first SIGINT or SIGTERM is recorded and stops the run; later
ones are deferred until cleanup has finished, which they cannot interrupt. The runner reports "every suite was stopped" only
when that check passed, and exits 130 or 143 after an interruption. One Python process launches the children, so it needs
nothing beyond what GNU Make 3.81 provides."""
from __future__ import annotations

import math
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_TIMEOUT = 600.0
GRACE = 5.0


class Interrupted(Exception):
    def __init__(self, signum: int, stopped: bool = True):
        super().__init__(signum)
        self.signum = signum
        self.stopped = stopped  # whether cleanup verified that every launched group is empty


def parse(arguments: list[str]) -> list[tuple[str, list[str]]]:
    suites = []
    for argument in arguments:
        name, sep, command = argument.partition("=")
        if not sep or not name or not command.strip() or "/" in name:
            raise SystemExit(f"run_suites: expected NAME='COMMAND', got {argument!r}")
        suites.append((name, shlex.split(command)))
    if not suites or len({name for name, _ in suites}) != len(suites):
        raise SystemExit("run_suites: give at least one suite, each with a distinct name")
    return suites


def timeout_seconds() -> float:
    raw = os.environ.get("RUN_SUITES_TIMEOUT", "")
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT
    except ValueError:
        raise SystemExit(f"run_suites: RUN_SUITES_TIMEOUT must be a number of seconds, got {raw!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise SystemExit("run_suites: RUN_SUITES_TIMEOUT must be a finite positive number of seconds")
    return value


class Launched:
    """One suite's process group, tracked until cleaned up. The leader's exit is observed without reaping it (``waitid``
    with ``WNOWAIT``), so its zombie keeps the group id from being reused until cleanup."""

    def __init__(self, process: subprocess.Popen, log, began: float):
        self.process, self.log, self.began = process, log, began
        self.code: int | None = None

    def poll(self) -> int | None:
        """The leader's exit code once it has exited (negative for a signal), else None. Does not reap."""
        if self.code is None:
            info = os.waitid(os.P_PID, self.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if info is not None:
                self.code = info.si_status if info.si_code == os.CLD_EXITED else -info.si_status
        return self.code

    def signal(self, signum: int) -> None:
        try:
            os.killpg(self.process.pid, signum)  # the leader's pid is the group id (start_new_session)
        except (ProcessLookupError, PermissionError):
            pass

    def occupied(self) -> bool:
        """Whether the group still holds a process (the unreaped leader's zombie counts until it is reaped)."""
        try:
            os.killpg(self.process.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def stop(launched: list[Launched], grace: float = GRACE) -> bool:
    """SIGTERM every group, wait up to ``grace`` s, SIGKILL what remains, reap every leader, and return whether every
    group is verifiably empty. Never interrupted by a signal: the handlers only record while this runs."""
    for entry in launched:
        entry.signal(signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(_members(entry) for entry in launched):
        time.sleep(0.05)
    for entry in launched:
        entry.signal(signal.SIGKILL)  # also takes any grandchild the leader left behind
        try:
            entry.process.wait(timeout=grace)  # reaps the leader
        except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL cannot be ignored
            pass
        entry.log.close()
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(entry.occupied() for entry in launched):
        time.sleep(0.02)
    return not any(entry.occupied() for entry in launched)


def _members(entry: Launched) -> int:
    """Live (non-zombie) members of the group, from ``ps``; 0 when ``ps`` is unavailable (the SIGKILL pass follows anyway)."""
    try:
        rows = subprocess.run(["ps", "-A", "-o", "pgid=,stat="], capture_output=True, text=True, check=False).stdout
    except OSError:
        return 0
    fields = (row.split() for row in rows.splitlines())
    return sum(1 for pgid, stat in (f[:2] for f in fields if len(f) >= 2)
               if pgid == str(entry.process.pid) and not stat.startswith("Z"))


def _failed(name: str, code: int | str, seconds: float, logdir: Path, out, note: str = "") -> None:
    print(f"  FAIL  {name:<10} {seconds:6.1f}s{note}", file=out, flush=True)
    print(f"\n==> {name} failed (exit {code}); {logdir / (name + '.log')}", file=out, flush=True)
    out.write((logdir / f"{name}.log").read_text(encoding="utf-8", errors="replace"))
    out.write("\n")
    out.flush()


def run(suites: list[tuple[str, list[str]]], logdir: Path, out=sys.stdout,
        timeout: float = DEFAULT_TIMEOUT) -> dict[str, tuple[int | str, float]]:
    """Start every suite at once; return ``{name: (exit_code, seconds)}``. Prints status lines (and a failed suite's log)
    as each suite finishes. After the suites finish, fail, time out or are interrupted, every launched group is cleaned up
    (see :func:`stop`); a group that cannot be emptied fails the run. An interruption raises :class:`Interrupted` once
    cleanup is done."""
    started = time.monotonic()
    launched: dict[str, Launched] = {}
    running: dict[str, Launched] = {}
    results: dict[str, tuple[int | str, float]] = {}
    state: dict[str, int | None] = {"signal": None}
    cleaning = {"now": False}

    def record(signum, _frame):
        if state["signal"] is None:
            state["signal"] = signum  # the first signal decides the exit status, whenever it arrives
            if not cleaning["now"]:
                raise Interrupted(signum)  # it stops the run; later ones, and any during cleanup, are deferred

    previous = {s: signal.signal(s, record) for s in (signal.SIGINT, signal.SIGTERM)}
    clean = True
    try:
        try:
            for name, command in suites:
                log = open(logdir / f"{name}.log", "wb")  # noqa: SIM115 - closed by stop()
                try:
                    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                               start_new_session=True)
                except OSError as error:
                    log.write(f"cannot start {command[0]}: {error}\n".encode())
                    log.close()
                    results[name] = (127, 0.0)
                    _failed(name, 127, 0.0, logdir, out)
                    continue
                launched[name] = running[name] = Launched(process, log, time.monotonic())
            while running:
                for name, entry in list(running.items()):
                    code = entry.poll()
                    elapsed = time.monotonic() - entry.began
                    if code is None and elapsed > timeout:
                        entry.log.write(f"\nrun_suites: {name} timed out after {timeout:g} s and was killed\n".encode())
                        entry.log.flush()
                        entry.signal(signal.SIGKILL)
                        code = "timeout"
                    if code is None:
                        continue
                    del running[name]
                    results[name] = (code, elapsed)
                    if code == 0:
                        print(f"  ok    {name:<10} {elapsed:6.1f}s", file=out, flush=True)
                    else:
                        entry.log.flush()
                        _failed(name, code, elapsed, logdir, out,
                                f"  timed out after {timeout:g} s" if code == "timeout" else "")
                time.sleep(0.05)
            print(f"  total       {time.monotonic() - started:6.1f}s (wall)", file=out, flush=True)
        except Interrupted:
            pass
        finally:
            cleaning["now"] = True  # from here on a signal is only recorded, never raised
            clean = stop(list(launched.values()))
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if state["signal"] is not None:
        raise Interrupted(int(state["signal"]), clean)
    if not clean:
        raise Interrupted(0, False)
    return results


def main(arguments: list[str]) -> int:
    suites = parse(arguments)
    timeout = timeout_seconds()
    logdir = Path(tempfile.mkdtemp(prefix="suites-"))
    print(f"==> {len(suites)} suites in parallel; logs in {logdir}; per-suite timeout {timeout:g} s", flush=True)
    try:
        results = run(suites, logdir, timeout=timeout)
    except Interrupted as interruption:
        claim = ("every suite was stopped" if interruption.stopped
                 else "some processes could not be verified stopped (their groups may still be running)")
        reason = f"interrupted (signal {interruption.signum})" if interruption.signum else "cleanup failed"
        print(f"\nrun_suites: {reason}; {claim}", flush=True)
        return 128 + interruption.signum if interruption.signum else 1
    failed = [name for name, _ in suites if results[name][0] != 0]
    if failed:
        print(f"\nFAILED: {', '.join(failed)}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
