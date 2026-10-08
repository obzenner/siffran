#!/usr/bin/env python3
"""Run named suites in parallel, each with its own log, and fail if any suite fails.

Usage: run_suites.py NAME='COMMAND ARGS...' [NAME='COMMAND ...' ...]

Scheduling: every suite runs; when each starts is the rule owned by ``doc/testing.md`` (load admission, longest first,
the ``JOBS`` cap), not restated here.

The Makefile declares the suites and passes them in; this runner only executes them. Every suite runs in its own process
group, and its stdout and stderr go to ``<logdir>/<NAME>.log`` (a fresh temporary directory, kept and printed). One status
line per suite shows its result and elapsed seconds, and a failed suite's log is printed the moment it fails. Exit status is
1 if any suite failed, else 0.

Limits: a suite that runs longer than ``RUN_SUITES_TIMEOUT`` seconds (a finite number, default 600) is killed and fails
with a ``timed out`` line. The signal and cleanup rules (process groups, SIGTERM then SIGKILL, exit 130 or 143) are those of
:func:`stop` and :func:`run`. One Python process launches the children, so it needs nothing beyond what GNU Make 3.81 provides."""
from __future__ import annotations

import json
import math
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

DEFAULT_TIMEOUT = 600.0
GRACE = 5.0
POLL = 0.05


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


def jobs_cap() -> int | None:
    """``JOBS``: an optional hard cap on suites running at once; unset or empty means none."""
    raw = os.environ.get("JOBS", "").strip()
    if not raw:
        return None
    if not raw.isascii() or not raw.isdigit() or int(raw) < 1:
        raise SystemExit(f"run_suites: JOBS must be a whole number of at least 1, got {raw!r}")
    return int(raw)


def max_load_limit() -> float:
    """``RUN_SUITES_MAX_LOAD``: the 1-minute load average below which a queued suite may start (default: CPU count)."""
    raw = os.environ.get("RUN_SUITES_MAX_LOAD", "").strip()
    try:
        value = float(raw) if raw else float(os.cpu_count() or 1)
    except ValueError:
        raise SystemExit(f"run_suites: RUN_SUITES_MAX_LOAD must be a number, got {raw!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise SystemExit("run_suites: RUN_SUITES_MAX_LOAD must be a finite positive number")
    return value


def system_load() -> float:
    """The 1-minute load average; 0.0 where it cannot be read (the runner then admits like an idle machine)."""
    try:
        return float(os.getloadavg()[0])
    except (OSError, AttributeError):
        return 0.0


def durations_path() -> Path:
    raw = os.environ.get("RUN_SUITES_DURATIONS", "").strip()
    return Path(raw) if raw else Path(tempfile.gettempdir()) / "run-suites-durations.json"


def read_durations(path: Path) -> dict[str, float]:
    """Previous wall times by suite name. Anything unreadable, or not a finite non-negative number, is left out."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {name: float(value) for name, value in data.items()
            if isinstance(name, str) and isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0}


def order_suites(suites: list[tuple[str, list[str]]], path: Path) -> list[tuple[str, list[str]]]:
    """Longest first (LPT): suites with no recorded time first, in declared order, then the rest by descending time."""
    known = read_durations(path)
    unknown = [suite for suite in suites if suite[0] not in known]
    timed = sorted((suite for suite in suites if suite[0] in known), key=lambda suite: -known[suite[0]])
    return unknown + timed


def record_durations(path: Path, results: dict[str, tuple[int | str, float]]) -> None:
    """Merge the wall times of the suites that passed into the file (atomically); a failed suite's time is not kept, and
    a file that cannot be written is ignored (it only orders the next run)."""
    merged = read_durations(path)
    merged.update({name: round(seconds, 1) for name, (code, seconds) in results.items() if code == 0})
    try:
        scratch = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        scratch.write_text(json.dumps(merged, sort_keys=True), encoding="utf-8")
        os.replace(scratch, path)
    except OSError:
        pass


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
        timeout: float = DEFAULT_TIMEOUT, *, load: Callable[[], float] = system_load,
        max_load: float | None = None, jobs: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic) -> dict[str, tuple[int | str, float]]:
    """Start the suites in the given order as the admission rule in ``doc/testing.md`` allows; return
    ``{name: (exit_code, seconds)}``.

    Prints a start line (also at the top of the suite's log), a status line as each suite finishes, and a failed
    suite's log. When the suites finish, fail, time out or are interrupted, every launched group is cleaned up (see
    :func:`stop`); a group that cannot be emptied fails the run. An interruption raises :class:`Interrupted` once
    cleanup is done."""
    started = clock()
    limit = max_load if max_load is not None else max_load_limit()
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
            queue = list(suites)
            queued_since = clock()

            def start_next(now_load: float) -> None:
                nonlocal queued_since
                name, command = queue.pop(0)
                waited = clock() - queued_since
                note = f"started at load {now_load:.1f}/{limit:g}"
                if now_load >= limit:
                    note += " (nothing was running)"
                if waited >= 1.0:
                    note += f" after waiting {waited:.0f}s"
                queued_since = clock()
                log = open(logdir / f"{name}.log", "wb")  # noqa: SIM115 - closed by stop()
                log.write(f"run_suites: {note}\n".encode())
                log.flush()
                print(f"  start {name:<10} {note}", file=out, flush=True)
                try:
                    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                               start_new_session=True)
                except OSError as error:
                    log.write(f"cannot start {command[0]}: {error}\n".encode())
                    log.close()
                    results[name] = (127, 0.0)
                    _failed(name, 127, 0.0, logdir, out)
                    return
                launched[name] = running[name] = Launched(process, log, clock())

            def admit() -> None:
                while queue and (jobs is None or len(running) < jobs):
                    now_load = load()
                    if running and not now_load < limit:
                        break  # busy: wait; with nothing running one suite always starts (progress guarantee)
                    start_next(now_load)

            admit()
            while running or queue:
                for name, entry in list(running.items()):
                    code = entry.poll()
                    elapsed = clock() - entry.began
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
                admit()
                sleep(POLL)
            print(f"  total       {clock() - started:6.1f}s (wall)", file=out, flush=True)
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
    jobs, limit, history = jobs_cap(), max_load_limit(), durations_path()
    suites = order_suites(suites, history)
    logdir = Path(tempfile.mkdtemp(prefix="suites-"))
    print(f"==> {len(suites)} suites, load-aware (limit {limit:g}, jobs {jobs if jobs else 'uncapped'}, longest first); "
          f"logs in {logdir}; per-suite timeout {timeout:g} s", flush=True)
    try:
        results = run(suites, logdir, timeout=timeout, max_load=limit, jobs=jobs)
    except Interrupted as interruption:
        claim = ("every suite was stopped" if interruption.stopped
                 else "some processes could not be verified stopped (their groups may still be running)")
        reason = f"interrupted (signal {interruption.signum})" if interruption.signum else "cleanup failed"
        print(f"\nrun_suites: {reason}; {claim}", flush=True)
        return 128 + interruption.signum if interruption.signum else 1
    record_durations(history, results)
    failed = [name for name, _ in suites if results[name][0] != 0]
    if failed:
        print(f"\nFAILED: {', '.join(failed)}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
