#!/usr/bin/env python3
"""``make verify``: the one hermetic gate. It runs the dependency installs, ``release-check``'s targets, ``ui-contrast``
and ``observer-deep`` side by side, then ``ui-browser``, ``ui-smoke`` and ``observer-smoke`` one at a time, each a
Makefile target in a scratch home of its own, and removes them on exit.

    python3 scripts/verify.py [--no-ui] [--step NAME=COMMAND ...] [--with NAME=COMMAND ...] [--registry-step NAME=COMMAND ...]

* **Steps:** the pinned dependencies first (``node_modules``, ``dev-install`` for the Python dev tools the property tests
  need, ``ui-install`` for the UI's), then ``release-check``'s own prerequisites, each a step (``check``, and so
  ``check-static``: ADRs, docs, contracts, manifests; then the integration suites and matrices), so none is listed twice
  and each gets its own home. ``ui-contrast`` is the one check ``ui-build`` runs that neither ``release-check`` nor
  ``ui-browser`` does; it reads the tokens and rebuilds nothing tracked (``ui-vendor`` and ``ui-css`` rewrite committed
  files, so they are not steps). The two smoke runs time the realistic size once (1,000 runs: the UI's first render, DOM
  count and zoom frames; the observer's budgets on the filesystem store); what a pass costs is counted in ``check``.
  ``--no-ui`` (``UI=0``) skips ``ui-browser`` and ``ui-smoke``.
* **Hermetic:** ``HOME``, ``EMPIRICA_HOME``, ``TMPDIR`` and ``npm_config_cache`` point into a scratch folder of the
  step's own, inside one scratch directory made for the run and removed when it ends, however it ends.
* **The registry steps** (``REGISTRY``: ``node_modules``, ``dev-install``, ``ui-install``, ``empirica-subagents-matrix``,
  ``empirica-pi-api-typecheck``, the ones that run ``npm ci``, ``npm install``, ``npm pack`` or ``uv pip sync``) keep
  the **real** ``HOME``: the user's ``npm`` and ``uv`` may be firewall shims that read their configuration (and a CA key)
  under the real home, and copying that is out (P3c). Their
  ``EMPIRICA_HOME``, ``TMPDIR`` and npm cache stay scratch, and they may write nothing under the real ``~/.claude`` or
  ``~/.pi``: on macOS ``sandbox-exec`` denies it (the step fails), elsewhere the two trees are compared before and after.
  The firewall is never bypassed: npm and uv are whatever ``PATH`` finds. Chrome is started with a mock keychain
  (``ui/tests/cdp.mjs``), so no keychain dialog appears.
* **In parallel:** the steps run in waves (``steps``). The installs are one wave, run side by side; then every check
  that times nothing, side by side; then the timed ones (``TIMED``: frame times, first render, the observer's budgets),
  one at a time on a quiet machine. Inside a wave a step starts when nothing runs, or while the 1-minute load is under
  ``RUN_SUITES_MAX_LOAD`` (default: the CPU count) and fewer than ``JOBS`` run (unset: no cap), as ``run_suites.py``
  admits suites. Each step has its own scratch ``HOME``, ``EMPIRICA_HOME``, ``TMPDIR`` and npm cache, so two never
  share one, and its own log (kept, its folder printed).
* **Installs skipped when nothing changed:** ``dev-install`` and ``ui-install`` (``LOCKS``) record their lockfile's
  SHA-256 inside what they install once they pass; while the lockfile still has that digest the step is skipped
  (``skip``). Removing what was installed removes the record, so the next run installs.
* **Time limits:** each step has a wall limit (``LIMITS``, declared per step; ``DEFAULT_LIMIT``, 600 s, for the rest).
  A step over its limit is stopped and fails as ``timed out after N s``, like any failing step. A hang is a bug to fix,
  not to wait out.
* **Process groups:** every step runs in its own group, owned by the ``Groups`` that ``run_suites.py`` uses too, so a
  ``run_suites`` inside a step (``check``) registers its suites' groups below the step's. Stopping a step (a timeout, a
  failure in its wave, the end of the run) asks first: SIGTERM to its group and every registered one, up to ``GRACE``
  s; then SIGKILL to all of them, even when the leader already exited, before the leader is reaped.
* **Output:** one line per step as it ends (``verify: NAME ok|FAIL|skip|stopped SECONDS``). At the first failing step
  it stops the steps still running in its wave (``stopped``), runs no later wave, prints the failing step's log and
  exits non-zero, naming it.
* ``--step NAME[@SECONDS]=COMMAND`` replaces the steps (for this script's own tests), each a wave of its own; ``--with
  NAME[@SECONDS]=COMMAND`` runs one in the wave of the step before it. ``@SECONDS`` is the step's limit.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_suites import Groups, Launched, jobs_cap, max_load_limit, stop, system_load  # noqa: E402

#: Every environment variable a step reads its home, its temporary files or a cache from, and the folder each gets.
HERMETIC = {"HOME": "home", "EMPIRICA_HOME": "empirica", "TMPDIR": "tmp", "npm_config_cache": "npm"}
#: The steps that install from the npm registry, run with the real HOME.
REGISTRY = ("node_modules", "dev-install", "ui-install", "empirica-subagents-matrix", "empirica-pi-api-typecheck")
#: The installs, first.
INSTALLS = ("node_modules", "dev-install", "ui-install")
#: The steps that time something (frames, first render, the observer's budgets): last, one at a time.
TIMED = ("ui-browser", "ui-smoke", "observer-smoke")
#: An install skipped while its lockfile is unchanged: its lockfile, and where the digest it last passed with is kept
#: (inside what it installs, so removing that removes the record). Repository-relative.
LOCKS = {"dev-install": ("requirements-dev.txt", ".venv/.verify-lock.sha256"),
         "ui-install": ("plugins/empirica/ui/package-lock.json", "plugins/empirica/ui/node_modules/.verify-lock.sha256")}
#: The real home's folders a registry step may not write.
GUARDED = (".claude", ".pi")
#: Each step's wall limit in seconds where it is not ``DEFAULT_LIMIT``: about 2-3 times its time on a loaded machine
#: (core-integration about 240 s, ui-browser about 270 s, the installs with a cold cache).
DEFAULT_LIMIT = 600.0
LIMITS = {"check": 900.0, "empirica-core-integration": 1200.0, "empirica-host-integration": 900.0,
          "empirica-governance-check": 900.0, "ui-browser": 900.0, "node_modules": 900.0, "dev-install": 900.0,
          "ui-install": 900.0}
POLL = 0.05
GRACE = 5.0
TAIL = 80  # lines of a failing step's log printed


@dataclass(frozen=True)
class Step:
    name: str
    command: list[str]
    real_home: bool  # a registry step: the real HOME
    wave: int  # the steps of one wave run side by side; a wave starts when the one before has passed
    limit: float = DEFAULT_LIMIT  # wall seconds before its process group is killed and it fails as timed out


def release_targets() -> list[str]:
    """``release-check``'s prerequisites, in the Makefile's order (the Makefile owns the list)."""
    line = next(x for x in (ROOT / "Makefile").read_text().splitlines() if x.startswith("release-check:"))
    return line.split(":", 1)[1].split("##")[0].split()


def steps(*, ui: bool) -> list[Step]:
    """The gate's steps in order, each a Makefile target: the installs (wave 0), the untimed checks (wave 1), then each
    timed step in a wave of its own."""
    untimed = [*release_targets(), "ui-contrast", "observer-deep"]
    timed = [t for t in TIMED if ui or not t.startswith("ui-")]
    waves = [(name, 0) for name in INSTALLS] + [(name, 1) for name in untimed] + [(name, 2 + k) for k, name in enumerate(timed)]
    return [Step(name, ["make", "--no-print-directory", name], name in REGISTRY, wave, LIMITS.get(name, DEFAULT_LIMIT))
            for name, wave in waves]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unchanged(name: str, locks: dict[str, tuple[str, str]], root: Path = ROOT) -> bool:
    """Whether install ``name`` last passed with its lockfile as it is now (its record is there and matches)."""
    if name not in locks:
        return False
    lock, stamp = (root / p for p in locks[name])
    try:
        return stamp.read_text().strip() == digest(lock)
    except OSError:
        return False


def record(name: str, locks: dict[str, tuple[str, str]], root: Path = ROOT) -> None:
    """After install ``name`` passed: keep its lockfile's digest where it installed."""
    if name in locks:
        lock, stamp = (root / p for p in locks[name])
        if stamp.parent.is_dir():
            stamp.write_text(digest(lock) + "\n")


def _tree(home: Path) -> dict[str, tuple[int, int]]:
    """Every file under the guarded folders of ``home``, with its mtime and size."""
    seen = {}
    for folder in GUARDED:
        for dirpath, _dirs, files in os.walk(home / folder):
            for f in files:
                try:
                    st = os.lstat(os.path.join(dirpath, f))
                except OSError:
                    continue
                seen[os.path.join(dirpath, f)] = (st.st_mtime_ns, st.st_size)
    return seen


def guarded(command: list[str], home: Path) -> list[str]:
    """``command`` denied any write under ``home``'s guarded folders, where the platform can enforce it."""
    sandbox = shutil.which("sandbox-exec") if sys.platform == "darwin" else None
    if not sandbox:
        return command
    real = os.path.realpath(home)
    deny = " ".join(f'(subpath "{os.path.join(real, folder)}")' for folder in GUARDED)
    return [sandbox, "-p", f"(version 1)(allow default)(deny file-write* {deny})", *command]


def hermetic(root: Path) -> dict[str, str]:
    """A step's environment: the caller's, with each of ``HERMETIC`` in a folder of ``root`` (made here)."""
    env = dict(os.environ)
    for name in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL"):  # each step is a make of its own, not a sub-make of verify's
        env.pop(name, None)
    root.mkdir(parents=True, exist_ok=True)
    for name, folder in HERMETIC.items():
        path = root / folder
        path.mkdir()
        env[name] = str(path)
    return env


@dataclass
class Running:
    step: Step
    group: Launched  # the step's process group, owned by the run's :class:`run_suites.Groups`
    before: dict | None  # the guarded tree before a registry step, where it is compared rather than enforced
    timed_out: bool = False

    @property
    def began(self) -> float:
        return self.group.began


def _launch(owner: Groups, step: Step, scratch: Path, logdir: Path, real_home: Path) -> Running:
    env = hermetic(scratch / step.name)
    command, before = step.command, None
    if step.real_home:
        env["HOME"] = str(real_home)
        command = guarded(step.command, real_home)
        before = None if command is not step.command else _tree(real_home)
    log = open(logdir / f"{step.name}.log", "w", encoding="utf-8")
    try:
        group = owner.start(command, log, time.monotonic(), cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    except OSError:
        log.close()
        raise
    return Running(step, group, before)


def _stop(running: list[Running]) -> bool:
    """Clean up the steps' groups with the owner ``run_suites`` uses (:func:`run_suites.stop`): SIGTERM each group and
    every group a runner inside it registered (a nested ``run_suites`` then stops its suites), up to ``GRACE`` s, then
    SIGKILL all of them, even where a leader already exited, and reap. Returns whether every group is verifiably empty."""
    return stop([r.group for r in running], GRACE)


def _line(name: str, word: str, seconds: float, note: str = "") -> None:
    print(f"verify: {name:<16} {word:<4} {seconds:.1f}s{note}", flush=True)


def run(plan: list[Step], *, locks: dict[str, tuple[str, str]] = LOCKS, load=system_load,
        max_load: float | None = None, jobs: int | None = None) -> int:
    real_home = Path(os.environ["HOME"])
    limit = max_load if max_load is not None else max_load_limit()
    cap = jobs if jobs is not None else jobs_cap()
    scratch = Path(tempfile.mkdtemp(prefix="empirica-verify-"))
    logdir = Path(tempfile.mkdtemp(prefix="empirica-verify-logs-"))
    began, running = time.monotonic(), []
    owner = Groups()
    try:
        for wave in sorted({s.wave for s in plan}):
            queue = [s for s in plan if s.wave == wave]
            while queue or running:
                while queue and (not running or (load() < limit and (cap is None or len(running) < cap))):
                    step = queue.pop(0)
                    if unchanged(step.name, locks):
                        _line(step.name, "skip", 0.0, " (lockfile unchanged)")
                        continue
                    running.append(_launch(owner, step, scratch, logdir, real_home))
                time.sleep(POLL)
                for r in [r for r in running if r.group.poll() is None and time.monotonic() - r.began > r.step.limit]:
                    _stop([r])  # the step's whole group, make and all it started, and every group registered below it
                    r.timed_out = True
                for r in [r for r in running if r.timed_out or r.group.poll() is not None]:
                    running.remove(r)
                    code = r.group.code
                    if not r.timed_out and not _stop([r]):  # what the leader left behind, then the reap
                        print(f"verify: {r.step.name} left processes that could not be stopped", flush=True)
                        code = code or 1
                    if r.timed_out:
                        code = "timeout"
                    if r.before is not None and _tree(real_home) != r.before:
                        print(f"verify: {r.step.name} wrote under {', '.join(GUARDED)} of the real home", flush=True)
                        code = code or 1
                    _line(r.step.name, "ok" if code == 0 else "FAIL", time.monotonic() - r.began,
                          f"  timed out after {r.step.limit:g} s" if r.timed_out else "")
                    if code == 0:
                        record(r.step.name, locks)
                        continue
                    others, running = running, []
                    _stop(others)
                    for o in others:
                        _line(o.step.name, "stopped", time.monotonic() - o.began)
                    text = (logdir / f"{r.step.name}.log").read_text(encoding="utf-8", errors="replace").splitlines()
                    print("\n".join(text[-TAIL:]), flush=True)
                    print(f"verify: FAIL at {r.step.name} (exit {code}); later steps not run; logs in {logdir}", flush=True)
                    return 1
        print(f"verify: ok, {len(plan)} steps in {time.monotonic() - began:.1f}s; logs in {logdir}", flush=True)
        return 0
    finally:
        if stop(owner.entries, GRACE):
            owner.close()
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-ui", action="store_true", help="skip ui-browser and ui-smoke (UI=0)")
    parser.add_argument("--step", dest="steps", action="append", default=[], type=lambda s: (s, False, False),
                        metavar="NAME=COMMAND", help="replace the steps: one, in a wave of its own")
    parser.add_argument("--with", dest="steps", action="append", type=lambda s: (s, False, True),
                        metavar="NAME=COMMAND", help="replace the steps: one, beside the step before it")
    parser.add_argument("--registry-step", dest="steps", action="append", type=lambda s: (s, True, False),
                        metavar="NAME=COMMAND", help="replace the steps: one run as a registry step (the real HOME)")
    args = parser.parse_args(argv)
    plan, wave = [], -1
    for spec, real, beside in args.steps:
        wave += 0 if beside and plan else 1
        head, command = spec.split("=", 1)
        name, _, limit = head.partition("@")
        plan.append(Step(name, shlex.split(command), real, wave, float(limit) if limit else DEFAULT_LIMIT))
    try:
        return run(plan or steps(ui=not args.no_ui))
    except KeyboardInterrupt:
        print("verify: interrupted", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
