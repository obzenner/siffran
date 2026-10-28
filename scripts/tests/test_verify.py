#!/usr/bin/env python3
"""``make verify`` (scripts/verify.py): its steps are the Makefile's targets, run in waves (side by side inside one, the
timed ones alone), each in a home of its own, one summary line each; it stops at the first failing step, stops the rest of
its wave, names it and exits non-zero, and leaves nothing behind; an install whose lockfile is unchanged is skipped."""
from __future__ import annotations

import contextlib
import io
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "scripts" / "verify.py"
sys.path.insert(0, str(ROOT / "scripts"))
import verify  # noqa: E402


def unscheduled():
    """A test's own scheduling: no outer cap. verify.py reads its cap only through run_suites.jobs_cap(), where an empty
    ``JOBS`` means no cap, so a ``make check JOBS=1`` that exports JOBS=1 into this suite never serializes a wave these
    tests start (in process or as a subprocess, whose environment is built from os.environ inside the test)."""
    return mock.patch.dict(os.environ, {"JOBS": ""})


class Unscheduled(unittest.TestCase):
    """Each test runs under :func:`unscheduled`, scoped to the test (patched in setUp, restored by cleanup)."""

    def setUp(self):
        patch = unscheduled()
        patch.start()
        self.addCleanup(patch.stop)


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """``verify.py`` with ``args``, admitting its steps whatever the machine's load (a loaded host would hold a wave's
    second step back and the timing assertions would measure the host, not the script)."""
    return subprocess.run([sys.executable, str(VERIFY), *args], capture_output=True, text=True, cwd=ROOT,
                          env={**os.environ, "RUN_SUITES_MAX_LOAD": "1000", **(env or {})}, check=False)


def release_check_prerequisites() -> list[str]:
    """``release-check``'s prerequisites, in the Makefile's order."""
    line = next(x for x in (ROOT / "Makefile").read_text().splitlines() if x.startswith("release-check:"))
    return line.split(":", 1)[1].split("##")[0].split()


INSTALLS = ["node_modules", "dev-install", "ui-install"]
AFTER = ["ui-contrast", "observer-deep", "ui-browser", "ui-smoke", "observer-smoke"]
TIMED = ["ui-browser", "ui-smoke", "observer-smoke"]


def IDLE() -> float:  # noqa: N802 - an injected load: these cases never read the machine's
    return 0.0


class StepsTests(unittest.TestCase):
    def test_the_steps_are_the_installs_release_check_s_then_the_ui_then_the_smoke_runs(self):
        """release-check's own targets, each a step (so each gets the right home), the dependency installs first."""
        self.assertEqual([s.name for s in verify.steps(ui=True)], [*INSTALLS, *release_check_prerequisites(), *AFTER])

    def test_the_installs_are_one_wave_the_untimed_checks_the_next_and_each_timed_step_alone_after_them(self):
        waves = {s.name: s.wave for s in verify.steps(ui=True)}
        self.assertEqual({waves[n] for n in INSTALLS}, {0})
        self.assertEqual({waves[n] for n in [*release_check_prerequisites(), "ui-contrast", "observer-deep"]}, {1})
        self.assertEqual([waves[n] for n in TIMED], [2, 3, 4], "each timed step alone, on a quiet machine")

    def test_ui_0_skips_the_browser_steps_and_nothing_else(self):
        self.assertEqual([s.name for s in verify.steps(ui=False)][-3:], ["ui-contrast", "observer-deep", "observer-smoke"])

    def test_there_is_no_scale_step_or_switch(self):
        """T1: the 10k runs are gone; the observer's cost is counted in make check, timed once at 1k."""
        self.assertFalse("observer-scale" in (ROOT / "Makefile").read_text(), "the Makefile still has observer-scale")
        self.assertFalse("scale" in VERIFY.read_text().lower(), "verify.py still has a scale step or switch")

    def test_each_step_is_a_make_target(self):
        for s in verify.steps(ui=True):
            self.assertEqual(s.command[0], "make")
            self.assertEqual(s.command[-1], s.name)

    def test_exactly_the_steps_that_install_from_the_registry_get_the_real_home(self):
        """The user's npm is the Socket Firewall shim, which reads its config under the real HOME (never copied)."""
        registry = ("node_modules", "dev-install", "ui-install", "empirica-subagents-matrix", "empirica-pi-api-typecheck")
        self.assertEqual({s.name for s in verify.steps(ui=True) if s.real_home}, set(registry))
        self.assertEqual(verify.REGISTRY, registry)

    def test_the_firewall_is_never_bypassed(self):
        source = VERIFY.read_text()
        for word in ("socketconfig", "socketcert", ".sfw", "sfw-is-disabled", "/bin/npm"):
            self.assertNotIn(word, source)

    def test_no_step_is_already_inside_another(self):
        """A target ``release-check`` runs is never listed again after it (check runs check-static, ...)."""
        dry = subprocess.run(["make", "-n", "--no-print-directory", "release-check"], cwd=ROOT, capture_output=True,
                             text=True, check=False).stdout
        for name in AFTER:
            self.assertNotIn(f"check-{name}", dry)
        self.assertNotIn("scripts/contrast.mjs", dry)
        self.assertNotIn("browser.journeys.mjs", dry)
        self.assertNotIn("test_observer_smoke", dry)


class RunTests(Unscheduled):
    def test_an_outer_jobs_cap_does_not_reach_the_runs_these_tests_start(self):
        """Bounded: under an outer JOBS=1 the fixture leaves verify uncapped, in process and in a subprocess whose second
        step waits at most 5 s for the first."""
        with mock.patch.dict(os.environ, {"JOBS": "1"}), unscheduled():
            self.assertIsNone(verify.jobs_cap())
            with tempfile.TemporaryDirectory() as scratch:
                flag = Path(scratch) / "flag"
                # a starts, then waits for b's mark; b marks only once it has seen a's: serialized, a gives up after 5 s
                hold = f"touch {flag}; for i in $(seq 50); do [ -f {flag}.b ] && exit 0; sleep 0.1; done; exit 1"
                wait = f"for i in $(seq 50); do [ -f {flag} ] && touch {flag}.b && exit 0; sleep 0.1; done; exit 1"
                done = run("--step", f"a=sh -c {shlex.quote(hold)}", "--with", f"b=sh -c {shlex.quote(wait)}")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_it_stops_at_the_first_failing_step_and_names_it(self):
        done = run("--step", "first=true", "--step", "second=false", "--step", "third=true")
        self.assertNotEqual(done.returncode, 0)
        lines = done.stdout.splitlines()
        self.assertRegex(lines[0], r"^verify: first +ok +\d+\.\ds$")
        self.assertRegex(lines[1], r"^verify: second +FAIL +\d+\.\ds$")
        self.assertFalse(any("third" in line for line in lines), lines)
        self.assertIn("second", lines[-1])

    def test_a_failing_step_stops_the_rest_of_its_wave_and_prints_its_log(self):
        # stopped, not waited for: a step left to finish would end ``ok`` (sleep exits 0), so its word is the proof
        done = run("--step", "slow=sleep 30", "--with", "broken=sh -c 'echo the-reason; exit 3'", "--step", "third=true")
        self.assertNotEqual(done.returncode, 0)
        lines = done.stdout.splitlines()
        self.assertRegex(lines[0], r"^verify: broken +FAIL +\d+\.\ds$")
        self.assertRegex(lines[1], r"^verify: slow +stopped +\d+\.\ds$")
        self.assertIn("the-reason", done.stdout, "the failing step's log is printed")
        self.assertFalse(any("third" in line for line in lines), lines)
        self.assertRegex(lines[-1], r"FAIL at broken \(exit 3\)")

    def test_the_steps_of_a_wave_run_side_by_side(self):
        with tempfile.TemporaryDirectory() as scratch:
            flag = Path(scratch) / "flag"
            # b waits for a's file: it passes only if both run at once
            wait = f"for i in $(seq 100); do [ -f {flag} ] && exit 0; sleep 0.1; done; exit 1"
            done = run("--step", f"a=sh -c 'sleep 0.3; touch {flag}; sleep 0.3'", "--with", f"b=sh -c {shlex.quote(wait)}")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_each_step_has_a_home_of_its_own(self):
        with tempfile.TemporaryDirectory() as scratch:

            def probe(n):
                return f"{n}=sh -c {shlex.quote('echo $HOME > ' + str(Path(scratch) / n))}"
            done = run("--step", probe("a"), "--with", probe("b"))
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            homes = {(Path(scratch) / n).read_text().strip() for n in "ab"}
        self.assertEqual(len(homes), 2)


class LockTests(Unscheduled):
    """dev-install and ui-install are skipped while their lockfile is the one they last passed with."""

    def test_the_locked_installs_are_dev_install_and_ui_install_their_record_inside_what_they_install(self):
        self.assertEqual(verify.LOCKS, {"dev-install": ("requirements-dev.txt", ".venv/.verify-lock.sha256"),
                                        "ui-install": ("plugins/empirica/ui/package-lock.json", "plugins/empirica/ui/node_modules/.verify-lock.sha256")})
        for name, (lock, _) in verify.LOCKS.items():
            self.assertTrue((ROOT / lock).is_file(), lock)

    def _plan(self, out: Path):
        return [verify.Step("inst", ["sh", "-c", f"echo ran >> {out}"], False, 0)]

    def test_an_install_runs_records_its_lockfile_then_is_skipped_until_the_lockfile_changes_or_its_folder_goes(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "lock").write_text("v1")
            (root / "installed").mkdir()
            locks = {"inst": (str(root / "lock"), str(root / "installed" / "stamp"))}
            out = root / "ran"

            def ran():
                return out.read_text().count("ran") if out.exists() else 0
            with contextlib.redirect_stdout(io.StringIO()) as said:
                self.assertEqual(verify.run(self._plan(out), locks=locks, load=IDLE, max_load=1.0), 0)
                self.assertEqual(ran(), 1)
                self.assertEqual(verify.run(self._plan(out), locks=locks, load=IDLE, max_load=1.0), 0)
                self.assertEqual(ran(), 1, "skipped: the lockfile is unchanged")
                (root / "lock").write_text("v2")
                self.assertEqual(verify.run(self._plan(out), locks=locks, load=IDLE, max_load=1.0), 0)
                self.assertEqual(ran(), 2, "the lockfile changed: installed again")
                (root / "installed" / "stamp").unlink()
                self.assertEqual(verify.run(self._plan(out), locks=locks, load=IDLE, max_load=1.0), 0)
                self.assertEqual(ran(), 3, "the record went with what was installed")
            self.assertRegex(said.getvalue(), r"(?m)^verify: inst +skip +0\.0s \(lockfile unchanged\)$")

    def test_a_wave_s_steps_wait_their_turn_while_the_injected_load_is_over_the_limit_and_start_together_under_it(self):
        """Admission by the injected load: over the limit a wave runs one step at a time (b finds a ended), under it side
        by side (b finds a started and not ended). The order is read from the steps' own marks, not from a clock."""
        with tempfile.TemporaryDirectory() as scratch:
            began, ended = Path(scratch) / "began", Path(scratch) / "ended"
            a = verify.Step("a", ["sh", "-c", f"touch {began}; sleep 0.3; touch {ended}"], False, 0)
            after = verify.Step("b", ["sh", "-c", f"[ -f {ended} ]"], False, 0)
            beside = verify.Step("b", ["sh", "-c", f"for i in $(seq 50); do [ -f {began} ] && break; sleep 0.02; done; "
                                                   f"[ -f {began} ] && [ ! -f {ended} ]"], False, 0)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(verify.run([a, after], locks={}, load=lambda: 99.0, max_load=1.0), 0, "one at a time")
                began.unlink()
                ended.unlink()
                self.assertEqual(verify.run([a, beside], locks={}, load=IDLE, max_load=1.0), 0, "side by side")

    def test_a_failed_install_records_nothing(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "lock").write_text("v1")
            (root / "installed").mkdir()
            locks = {"inst": (str(root / "lock"), str(root / "installed" / "stamp"))}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(verify.run([verify.Step("inst", ["false"], False, 0)], locks=locks, load=IDLE, max_load=1.0), 1)
            self.assertFalse((root / "installed" / "stamp").exists())

    def test_a_step_over_its_limit_is_killed_and_fails_as_timed_out(self):
        # killed, not waited for: the step sleeps 300 s, so the run ending within the bound with the message is the proof
        done = subprocess.run([sys.executable, str(VERIFY), "--step", "hang@1=sh -c 'sleep 300 & wait'", "--step", "third=true"],
                              capture_output=True, text=True, cwd=ROOT, check=False, timeout=60,
                              env={**os.environ, "RUN_SUITES_MAX_LOAD": "1000"})
        self.assertNotEqual(done.returncode, 0)
        lines = done.stdout.splitlines()
        self.assertRegex(lines[0], r"^verify: hang +FAIL +\d+\.\ds  timed out after 1 s$")
        self.assertFalse(any("third" in line for line in lines), lines)
        self.assertIn("FAIL at hang", lines[-1])

    def test_every_step_has_a_limit_and_the_declared_ones_name_real_steps(self):
        plan = verify.steps(ui=True)
        self.assertTrue(all(0 < s.limit < float("inf") for s in plan))
        self.assertLessEqual(set(verify.LIMITS), {s.name for s in plan})
        self.assertEqual({s.name: s.limit for s in plan if s.name in verify.LIMITS}, verify.LIMITS)
        self.assertEqual(next(s for s in plan if s.name == "observer-smoke").limit, verify.DEFAULT_LIMIT)

    def test_every_step_passing_exits_zero(self):
        done = run("--step", "first=true", "--step", "second=true")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([line.split()[2] for line in done.stdout.splitlines()[:2]], ["ok", "ok"])

    def test_the_home_is_its_own_and_is_removed(self):
        with tempfile.TemporaryDirectory() as scratch:
            out = Path(scratch) / "env"
            probe = ("printf '%s\\n' \"$HOME\" \"$EMPIRICA_HOME\" \"$TMPDIR\" \"$npm_config_cache\" > " + str(out))
            done = run("--step", f"probe=sh -c {shlex.quote(probe)}")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            paths = out.read_text().split()
        self.assertEqual(len(paths), 4)
        self.assertEqual(len(set(paths)), 4)
        for path in paths:
            self.assertNotEqual(path, os.environ.get("HOME"))
            self.assertFalse(Path(path).exists(), f"{path} was left behind")
        self.assertEqual(len({str(Path(p).parent) for p in paths}), 1, "one scratch folder holds all four")
        self.assertTrue(Path(paths[0]).parent.parent.name.startswith("empirica-verify-"), "inside the run's scratch root")

    def _probe(self, real_home: Path, out: Path, *, registry: bool) -> list[str]:
        probe = "printf '%s\\n' \"$HOME\" \"$EMPIRICA_HOME\" \"$TMPDIR\" \"$npm_config_cache\" > " + str(out)
        done = run("--registry-step" if registry else "--step", f"probe=sh -c {shlex.quote(probe)}", env={"HOME": str(real_home)})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return out.read_text().split()

    def test_a_registry_step_has_the_real_home_and_every_other_step_a_scratch_one(self):
        with tempfile.TemporaryDirectory() as scratch:
            real = Path(scratch) / "real"
            real.mkdir()
            reg = self._probe(real, Path(scratch) / "reg", registry=True)
            other = self._probe(real, Path(scratch) / "other", registry=False)
        self.assertEqual(reg[0], str(real), "the install step runs with the real HOME")
        self.assertNotEqual(other[0], str(real), "every other step's HOME is scratch")
        for env in (reg, other):  # EMPIRICA_HOME, TMPDIR and the npm cache stay scratch in both
            self.assertTrue(all(Path(p).name in {"empirica", "tmp", "npm"} and Path(p).parent.parent.name.startswith("empirica-verify-")
                                for p in env[1:]), env)

    def test_a_registry_step_cannot_write_under_the_real_claude_or_pi(self):
        with tempfile.TemporaryDirectory() as scratch:
            real = Path(scratch) / "real"
            for folder in (".claude", ".pi"):
                (real / folder).mkdir(parents=True)
                write = f"echo x > \"$HOME/{folder}/written\""
                done = run("--registry-step", f"install=sh -c {shlex.quote(write)}", env={"HOME": str(real)})
                self.assertNotEqual(done.returncode, 0, f"a write under {folder} passed")
                if verify.guarded(["true"], real) != ["true"]:  # enforced (macOS): the write never lands
                    self.assertFalse((real / folder / "written").exists(), f"{folder} was written")
                (real / folder / "written").unlink(missing_ok=True)
            write = "echo x > \"$HOME/allowed\""
            done = run("--registry-step", f"install=sh -c {shlex.quote(write)}", env={"HOME": str(real)})
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


def _alive(pid: int) -> bool:
    status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout.strip()
    return bool(status) and not status.startswith("Z")


def _kill_group(pgid: int) -> None:
    """Test cleanup: only the exact group this test's own suite created (start_new_session), if anything is left."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class GroupOwnerTests(Unscheduled):
    """AST-4: verify cleans up through the owner run_suites uses; a nested run_suites' groups are its too. Tiny limits."""

    def nested(self, inner_grace: float, outer_grace: float) -> tuple[int, float, int]:
        """A verify step (limit 1.5 s) running run_suites, whose one suite ignores SIGTERM: (exit, seconds, suite pid)."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pidfile = root / "suite.pid"
            (root / "logs").mkdir()
            suite = (f"import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                     f"open({str(pidfile) + '.tmp'!r}, 'w').write(str(os.getpid())); "
                     f"os.replace({str(pidfile) + '.tmp'!r}, {str(pidfile)!r}); time.sleep(60)")
            (root / "runner.py").write_text(
                "import sys\n"
                "from pathlib import Path\n"
                f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
                "import run_suites\n"
                f"run_suites.GRACE = {inner_grace!r}\n"
                f"run_suites.run([('stubborn', [sys.executable, '-c', {suite!r}])], Path({str(root / 'logs')!r}), "
                "timeout=60, load=lambda: 0.0, max_load=99.0)\n")
            plan = [verify.Step("nested", [sys.executable, str(root / "runner.py")], False, 0, 1.5)]
            began = time.monotonic()
            with mock.patch.object(verify, "GRACE", outer_grace), contextlib.redirect_stdout(io.StringIO()) as out:
                code = verify.run(plan, locks={}, load=IDLE, max_load=99.0)
            seconds = time.monotonic() - began
            self.assertTrue(pidfile.exists(), "the suite never started: " + out.getvalue())
            pid = int(pidfile.read_text())
            self.addCleanup(_kill_group, pid)
            self.assertIn("timed out after 1.5 s", out.getvalue())
            return code, seconds, pid

    def test_a_timed_out_step_running_run_suites_kills_its_suite_that_ignores_sigterm(self):
        # the inner runner would wait 60 s before its own SIGKILL: only verify's registry of its groups can end the suite
        code, seconds, pid = self.nested(inner_grace=60.0, outer_grace=0.3)
        self.assertEqual(code, 1)
        self.assertLess(seconds, 10)
        time.sleep(0.2)
        self.assertFalse(_alive(pid), "the nested suite outlived verify's timeout")

    def test_a_timed_out_step_is_asked_first_and_its_nested_runner_cleans_up_within_the_grace(self):
        # cooperative first: SIGTERM lets run_suites stop its suite (0.1 s grace), so verify's 8 s grace is not waited out
        code, seconds, pid = self.nested(inner_grace=0.1, outer_grace=8.0)
        self.assertEqual(code, 1)
        self.assertLess(seconds, 1.5 + 6.0)
        self.assertFalse(_alive(pid))

    def test_a_step_whose_leader_exits_on_sigterm_leaves_no_child_that_ignores_it(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "child.pid"
            leader = ("import os, signal, time\n"
                      "pid = os.fork()\n"
                      "if pid == 0:\n"
                      "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                      f"    open({str(pidfile) + '.tmp'!r}, 'w').write(str(os.getpid()))\n"
                      f"    os.replace({str(pidfile) + '.tmp'!r}, {str(pidfile)!r})\n"
                      "    time.sleep(60)\n"
                      "    os._exit(0)\n"
                      "time.sleep(60)\n")
            # stopped because its wave failed (the other step fails once the child is ready), not killed by a timeout
            fail = (f"import os, sys, time\nwhile not os.path.exists({str(pidfile)!r}): time.sleep(0.02)\n"
                    "sys.exit(3)\n")
            plan = [verify.Step("leader", [sys.executable, "-c", leader], False, 0, 30.0),
                    verify.Step("fail", [sys.executable, "-c", fail], False, 0, 30.0)]
            with mock.patch.object(verify, "GRACE", 0.3), contextlib.redirect_stdout(io.StringIO()) as out:
                code = verify.run(plan, locks={}, load=IDLE, max_load=99.0)
            self.assertTrue(pidfile.exists(), out.getvalue())
            self.assertRegex(out.getvalue(), r"verify: leader +stopped")
            child = int(pidfile.read_text())
            self.addCleanup(lambda: _alive(child) and os.kill(child, signal.SIGKILL))
            self.assertEqual(code, 1)
            time.sleep(0.2)
            self.assertFalse(_alive(child), "the child that ignores SIGTERM outlived its leader")


if __name__ == "__main__":
    unittest.main()
