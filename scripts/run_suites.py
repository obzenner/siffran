#!/usr/bin/env python3
"""Run named suites in parallel, each with its own log, and fail if any suite fails.

Usage: run_suites.py NAME[@SECONDS]='COMMAND ARGS...' [NAME[@SECONDS]='COMMAND ...' ...]

Scheduling: every suite runs; when each starts (load admission, longest first, the ``JOBS`` cap) is stated in ``doc/testing.md``.

The Makefile declares the suites and passes them in; this runner only executes them. Every suite runs in its own process
group, and its stdout and stderr go to ``<logdir>/<NAME>.log`` (a fresh temporary directory, kept and printed). One status
line per suite shows its result and elapsed seconds, and a failed suite's log is printed the moment it fails. Exit status is
1 if any suite failed, else 0.

Limits: a suite that runs longer than its limit is stopped (:func:`stop`: its group and every group a runner inside it
registered, SIGTERM then SIGKILL) and fails with a ``timed out after N s`` line. The limit is declared per suite
(``NAME@SECONDS``), else ``RUN_SUITES_TIMEOUT`` seconds (a finite number, default 600). The signal and cleanup rules (process groups, SIGTERM then SIGKILL, exit 130 or 143) are those of
:func:`stop` and :func:`run`."""
from __future__ import annotations

import json
import math
import os
import shlex
import shutil
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
#: The registry folder of the group a process runs in (see :class:`Groups`); a nested runner registers below it.
GROUPS_ENV = "RUN_SUITES_GROUPS"


class Interrupted(Exception):
    def __init__(self, signum: int, stopped: bool = True):
        super().__init__(signum)
        self.signum = signum
        self.stopped = stopped  # whether cleanup verified that every launched group is empty


def _seconds(raw: str, what: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit(f"run_suites: {what} must be a number of seconds, got {raw!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise SystemExit(f"run_suites: {what} must be a finite positive number of seconds")
    return value


def _split(argument: str) -> tuple[str, float | None, str]:
    """``NAME[@SECONDS]=COMMAND`` -> (name, its declared limit or None, command)."""
    head, sep, command = argument.partition("=")
    name, at, limit = head.partition("@")
    if not sep or not name or not command.strip() or "/" in name or (at and not limit):
        raise SystemExit(f"run_suites: expected NAME[@SECONDS]='COMMAND', got {argument!r}")
    return name, _seconds(limit, f"the limit of {name}") if at else None, command


def parse(arguments: list[str]) -> list[tuple[str, list[str]]]:
    suites = []
    for argument in arguments:
        name, _limit, command = _split(argument)
        suites.append((name, shlex.split(command)))
    if not suites or len({name for name, _ in suites}) != len(suites):
        raise SystemExit("run_suites: give at least one suite, each with a distinct name")
    return suites


def limits(arguments: list[str]) -> dict[str, float]:
    """The limits the suites declare (``NAME@SECONDS``); a suite not here has ``RUN_SUITES_TIMEOUT``'s."""
    return {name: limit for name, limit, _ in map(_split, arguments) if limit is not None}


def timeout_seconds() -> float:
    raw = os.environ.get("RUN_SUITES_TIMEOUT", "")
    return _seconds(raw, "RUN_SUITES_TIMEOUT") if raw else DEFAULT_TIMEOUT


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
    """One process group, tracked until cleaned up. The leader's exit is observed without reaping it (``waitid`` with
    ``WNOWAIT``), so its zombie keeps the group id from being reused until cleanup. ``slot`` is the group's folder in
    its owner's registry (:class:`Groups`); a runner started inside the group registers its own groups below it."""

    def __init__(self, process: subprocess.Popen, log, began: float, slot: Path | None = None):
        self.process, self.log, self.began, self.slot = process, log, began, slot
        self.code: int | None = None
        self.done = False  # cleaned up: every member and registered descendant gone, the leader reaped

    @property
    def pgid(self) -> int:
        return self.process.pid  # the leader's pid is the group id (start_new_session)

    def poll(self) -> int | None:
        """The leader's exit code once it has exited (negative for a signal), else None. Does not reap."""
        if self.code is None:
            info = os.waitid(os.P_PID, self.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if info is not None:
                self.code = info.si_status if info.si_code == os.CLD_EXITED else -info.si_status
        return self.code

    def signal(self, signum: int) -> None:
        _signal_group(self.pgid, signum)

    def occupied(self) -> bool:
        """Whether the group still holds a process (the unreaped leader's zombie counts until it is reaped)."""
        try:
            os.killpg(self.pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def descendants(self) -> set[int]:
        """The groups registered below this one (by runners it started, at any depth), never the caller's own."""
        if self.slot is None:
            return set()
        own = self.slot / "pgid"
        try:
            files = [path for path in self.slot.rglob("pgid") if path != own]
        except OSError:
            return set()
        found = set()
        for path in files:
            try:
                pgid = int(path.read_text().strip())
            except (OSError, ValueError):
                continue
            if pgid > 1 and pgid != os.getpgrp():
                found.add(pgid)
        return found

    def unregister(self) -> None:
        if self.slot is not None:
            shutil.rmtree(self.slot, ignore_errors=True)


def _signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _census() -> dict[int, int]:
    """Live (non-zombie) processes by group id, from ``ps``; empty when ``ps`` is unavailable (the SIGKILL pass follows
    anyway)."""
    try:
        rows = subprocess.run(["ps", "-A", "-o", "pgid=,stat="], capture_output=True, text=True, check=False,
                              timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    counts: dict[int, int] = {}
    for row in rows.splitlines():
        fields = row.split()
        if len(fields) >= 2 and fields[0].isdigit() and not fields[1].startswith("Z"):
            counts[int(fields[0])] = counts.get(int(fields[0]), 0) + 1
    return counts


class Groups:
    """The one owner of the process groups a runner starts (``run_suites`` and ``verify`` both use it), and of every group
    a runner inside them starts in turn.

    Each group gets a folder in this owner's registry holding its id; the group's processes inherit ``GROUPS_ENV``
    pointing at that folder, so a nested runner's owner registers its groups below it. A nested group leaves its parent's
    process group (``start_new_session``), so killing the parent's group alone would orphan it: :func:`stop` therefore
    kills every group registered below an entry as well. A registration is removed only once its group is empty, before
    its leader is reaped, so a registered id is pinned (by a live member or the unreaped leader) while it is listed."""

    def __init__(self) -> None:
        inherited = os.environ.get(GROUPS_ENV, "")
        base = inherited if inherited and os.path.isdir(inherited) else None
        try:
            self.root = Path(tempfile.mkdtemp(prefix="groups-", dir=base))
        except OSError:
            self.root = Path(tempfile.mkdtemp(prefix="groups-"))
        self.entries: list[Launched] = []
        self._count = 0

    def start(self, command: list[str], log, began: float, *, env: dict | None = None, **popen) -> Launched:
        """``command`` in a new session (its own group), registered before anything else can happen to it."""
        self._count += 1
        slot = self.root / f"g{self._count}"
        slot.mkdir()
        child_env = {**(os.environ if env is None else env), GROUPS_ENV: str(slot)}
        try:
            process = subprocess.Popen(command, env=child_env, start_new_session=True, **popen)
        except OSError:
            shutil.rmtree(slot, ignore_errors=True)
            raise
        entry = Launched(process, log, began, slot)
        self.entries.append(entry)
        scratch = slot / "pgid.tmp"
        scratch.write_text(str(process.pid))
        os.replace(scratch, slot / "pgid")
        return entry

    def stop(self, entries: list[Launched] | None = None, grace: float | None = None) -> bool:
        return stop(self.entries if entries is None else entries, grace)

    def close(self) -> None:
        """Remove the registry if every group was cleaned up; a group left behind stays registered for an outer owner."""
        if all(entry.done for entry in self.entries):
            shutil.rmtree(self.root, ignore_errors=True)


def stop(launched: list[Launched], grace: float | None = None) -> bool:
    """Clean up the groups: first cooperatively, SIGTERM to each group and every group registered below it, waiting up to
    ``grace`` s (``GRACE``) for them to empty (a nested runner cleans up its own groups then); then SIGKILL every
    registered descendant group, every group itself, and the descendants once more, always, even where a leader already
    exited. Each emptied group is unregistered, then its leader reaped; returns whether every group is verifiably empty.
    Never interrupted by a signal in ``run``: the handlers only record while this runs."""
    grace = GRACE if grace is None else grace
    entries = [entry for entry in launched if not entry.done]
    if not entries:
        return True

    def registered() -> set[int]:
        return {pgid for entry in entries for pgid in entry.descendants()}

    def live() -> bool:
        census = _census()
        return any(census.get(pgid) for pgid in {entry.pgid for entry in entries} | registered())

    for entry in entries:
        entry.signal(signal.SIGTERM)
    for pgid in registered():
        _signal_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and live():
        time.sleep(0.05)
    for pgid in registered():  # while this owner's leaders are unreaped, so the runner holding these ids still lives
        _signal_group(pgid, signal.SIGKILL)
    for entry in entries:
        entry.signal(signal.SIGKILL)  # also takes any grandchild the leader left behind
    for pgid in registered():  # anything registered while its runner was dying
        _signal_group(pgid, signal.SIGKILL)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and live():
        time.sleep(0.02)
    census = _census()
    emptied = {}
    for entry in entries:
        emptied[entry] = not census.get(entry.pgid) and not any(census.get(g) for g in entry.descendants())
        if emptied[entry]:
            entry.unregister()  # before the reap: until then the leader's zombie pins the id
        try:
            entry.process.wait(timeout=grace)  # reaps the leader
        except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL cannot be ignored
            pass
        if entry.log is not None and not entry.log.closed:
            entry.log.close()
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(entry.occupied() for entry in entries):
        time.sleep(0.02)
    for entry in entries:
        entry.done = emptied[entry] and not entry.occupied()
    return all(entry.done for entry in entries)


def _failed(name: str, code: int | str, seconds: float, logdir: Path, out, note: str = "") -> None:
    print(f"  FAIL  {name:<10} {seconds:6.1f}s{note}", file=out, flush=True)
    print(f"\n==> {name} failed (exit {code}); {logdir / (name + '.log')}", file=out, flush=True)
    out.write((logdir / f"{name}.log").read_text(encoding="utf-8", errors="replace"))
    out.write("\n")
    out.flush()


def run(suites: list[tuple[str, list[str]]], logdir: Path, out=sys.stdout,
        timeout: float = DEFAULT_TIMEOUT, *, limits: dict[str, float] | None = None,
        load: Callable[[], float] = system_load,
        max_load: float | None = None, jobs: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic) -> dict[str, tuple[int | str, float]]:
    """Start the suites in the given order as the admission rule in ``doc/testing.md`` allows; return
    ``{name: (exit_code, seconds)}``. A suite runs at most ``limits[name]`` seconds, else ``timeout``.

    Prints a start line (also at the top of the suite's log), a status line as each suite finishes, and a failed
    suite's log. When the suites finish, fail, time out or are interrupted, every launched group is cleaned up (see
    :func:`stop`); a group that cannot be emptied fails the run. An interruption raises :class:`Interrupted` once
    cleanup is done."""
    started = clock()
    limit = max_load if max_load is not None else max_load_limit()
    owner = Groups()  # every suite's group, and every group a runner inside a suite registers below it
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
                    entry = owner.start(command, log, clock(), stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL)
                except OSError as error:
                    log.write(f"cannot start {command[0]}: {error}\n".encode())
                    log.close()
                    results[name] = (127, 0.0)
                    _failed(name, 127, 0.0, logdir, out)
                    return
                running[name] = entry

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
                    limit_s = (limits or {}).get(name, timeout)
                    if code is None and elapsed > limit_s:
                        entry.log.write(f"\nrun_suites: {name} timed out after {limit_s:g} s and was killed\n".encode())
                        entry.log.flush()
                        stop([entry])  # cooperative first, then SIGKILL its group and every group registered below it
                        code = "timeout"
                    if code is None:
                        continue
                    del running[name]
                    results[name] = (code, elapsed)
                    if code == 0:
                        print(f"  ok    {name:<10} {elapsed:6.1f}s", file=out, flush=True)
                    else:
                        if not entry.log.closed:
                            entry.log.flush()
                        _failed(name, code, elapsed, logdir, out,
                                f"  timed out after {limit_s:g} s" if code == "timeout" else "")
                admit()
                sleep(POLL)
            print(f"  total       {clock() - started:6.1f}s (wall)", file=out, flush=True)
        except Interrupted:
            pass
        finally:
            cleaning["now"] = True  # from here on a signal is only recorded, never raised
            clean = stop(owner.entries)
            if clean:
                owner.close()
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
        results = run(suites, logdir, timeout=timeout, limits=limits(arguments), max_load=limit, jobs=jobs)
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
