"""run_suites: a failing suite fails the run, logs are kept and separate, only failures print their log."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "run_suites.py"
PY = sys.executable


def run(*suites: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(SCRIPT), *suites], capture_output=True, text=True, check=False)


def say(text: str, code: int = 0) -> str:
    return f"{PY} -c \"import sys; print('{text}'); print('err-{text}', file=sys.stderr); sys.exit({code})\""


class RunSuites(unittest.TestCase):
    def test_all_passing_suites_exit_zero_with_one_status_line_each(self):
        done = run(f"a={say('one')}", f"b={say('two')}")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(sum(line.strip().startswith("ok") for line in done.stdout.splitlines()), 2)
        self.assertNotIn("one", done.stdout.replace("ok", ""))  # a passing suite's log is not printed

    def test_a_failing_suite_fails_the_run_and_only_its_log_is_printed(self):
        done = run(f"good={say('fine')}", f"bad={say('broken', 3)}")
        self.assertEqual(done.returncode, 1)
        self.assertIn("FAIL  bad", done.stdout)
        self.assertIn("ok    good", done.stdout)
        self.assertIn("broken", done.stdout)
        self.assertIn("err-broken", done.stdout)
        self.assertEqual(done.stdout.count("err-broken"), 1)  # printed once, when it failed
        self.assertNotIn("err-fine", done.stdout)

    def test_logs_are_kept_one_per_suite_and_never_mixed(self):
        done = run(f"a={say('alpha')}", f"b={say('beta')}")
        logdir = Path(done.stdout.split("logs in ")[1].splitlines()[0].split(";")[0])
        self.addCleanup(lambda: [p.unlink() for p in logdir.iterdir()] and logdir.rmdir())
        a, b = (logdir / "a.log").read_text(), (logdir / "b.log").read_text()
        self.assertIn("alpha", a)
        self.assertIn("err-alpha", a)
        self.assertNotIn("beta", a)
        self.assertIn("beta", b)
        self.assertNotIn("alpha", b)

    def test_a_command_that_cannot_start_fails_the_run(self):
        done = run("a=/nonexistent/binary --x", f"b={say('ok')}")
        self.assertEqual(done.returncode, 1)
        self.assertIn("cannot start", done.stdout)

    def test_suites_run_concurrently(self):
        sleeper = f"{PY} -c \"import time; time.sleep(1.5)\""
        import time
        began = time.monotonic()
        done = run(f"a={sleeper}", f"b={sleeper}", f"c={sleeper}")
        self.assertEqual(done.returncode, 0)
        self.assertLess(time.monotonic() - began, 3.5)  # serial would take 4.5 s

    def test_bad_arguments_are_refused(self):
        for bad in (["nocommand"], ["=x"], ["a="], ["a=x", "a=y"], []):
            self.assertEqual(run(*bad).returncode, 1, bad)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie is not alive
        return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()[:1] != "Z"
    except OSError:
        return True


def stuck(pidfile: Path) -> str:
    """A suite that starts a grandchild, records its pid, and then waits forever."""
    return f"sh -c 'sleep 300 & echo $! > {pidfile}; wait'"


class Lifecycle(unittest.TestCase):
    def pid(self, pidfile: Path) -> int:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if pidfile.exists() and pidfile.read_text().strip():
                return int(pidfile.read_text())
            time.sleep(0.05)
        self.fail("the stuck suite never started")

    def start(self, *suites: str, env: dict | None = None) -> subprocess.Popen:
        process = subprocess.Popen([PY, str(SCRIPT), *suites], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   env={**os.environ, **(env or {})})
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def test_a_terminated_runner_leaves_no_suite_alive(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signum.name), tempfile.TemporaryDirectory() as directory:
                pidfile = Path(directory) / "pid"
                process = self.start(f"stuck={stuck(pidfile)}", f"other={stuck(Path(directory) / 'pid2')}")
                child = self.pid(pidfile)
                self.assertTrue(alive(child))
                process.send_signal(signum)
                output, _ = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 128 + signum)
                self.assertIn("interrupted", output)
                time.sleep(0.3)
                self.assertFalse(alive(child), "the stuck suite's grandchild survived the runner")
                self.assertFalse(alive(self.pid(Path(directory) / "pid2")))

    def test_a_failed_suite_log_is_printed_while_another_suite_still_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "pid"
            process = self.start(f"stuck={stuck(pidfile)}", f"bad={say('SENTINEL-LOG', 4)}")
            seen = []
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                line = process.stdout.readline()
                seen.append(line)
                if "err-SENTINEL-LOG" in line:
                    break
            self.assertTrue(any("err-SENTINEL-LOG" in line for line in seen), "".join(seen))
            self.assertIsNone(process.poll(), "the runner was still waiting on the stuck suite")
            child = self.pid(pidfile)
            self.assertTrue(alive(child))
            process.terminate()
            process.communicate(timeout=20)
            time.sleep(0.3)
            self.assertFalse(alive(child))

    def test_a_suite_over_the_timeout_is_killed_and_fails_the_run(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "pid"
            began = time.monotonic()
            done = subprocess.run([PY, str(SCRIPT), f"stuck={stuck(pidfile)}", f"fine={say('ok')}"], capture_output=True,
                                  text=True, env={**os.environ, "RUN_SUITES_TIMEOUT": "1"}, timeout=30)
            self.assertLess(time.monotonic() - began, 15)
            self.assertEqual(done.returncode, 1)
            self.assertIn("timed out after 1 s", done.stdout)
            self.assertIn("ok    fine", done.stdout)
            time.sleep(0.3)
            self.assertFalse(alive(int(pidfile.read_text())))

    def test_a_grandchild_that_outlives_its_leader_is_gone_after_the_runner_exits(self):
        for code in (0, 7):
            with self.subTest(exit=code), tempfile.TemporaryDirectory() as directory:
                pidfile = Path(directory) / "pid"
                leader = (f"{PY} -c \"import subprocess,sys; p=subprocess.Popen(['sleep','300']); "
                          f"open('{pidfile}','w').write(str(p.pid)); sys.exit({code})\"")
                done = subprocess.run([PY, str(SCRIPT), f"early={leader}"], capture_output=True, text=True, timeout=30)
                self.assertEqual(done.returncode, 0 if code == 0 else 1, done.stdout)
                grandchild = self.pid(pidfile)
                self.addCleanup(lambda pid=grandchild: alive(pid) and os.kill(pid, signal.SIGKILL))
                time.sleep(0.3)
                self.assertFalse(alive(grandchild), "the grandchild survived its leader and the runner")

    def test_a_second_signal_during_the_grace_period_does_not_interrupt_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "pid"
            source = (f"import os,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                      f"open('{pidfile}','w').write(str(os.getpid())); time.sleep(300)")
            process = self.start(f"stubborn={PY} -c \"{source}\"")
            child = self.pid(pidfile)
            self.addCleanup(lambda: alive(child) and os.kill(child, signal.SIGKILL))
            process.send_signal(signal.SIGINT)
            time.sleep(0.3)
            process.send_signal(signal.SIGINT)  # during the grace period, while the suite ignores SIGTERM
            output, _ = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 128 + signal.SIGINT)
            time.sleep(0.2)
            self.assertFalse(alive(child), "the suite that ignores SIGTERM was not killed")
            self.assertIn("every suite was stopped", output)

    def test_a_first_signal_during_the_normal_cleanup_still_decides_the_exit_status(self):
        # The leader exits 0 at once and leaves a grandchild that ignores SIGTERM, so cleanup waits out the grace
        # period; a first SIGINT or SIGTERM sent then must give 128 + signal, never 0, and the grandchild still dies.
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum.name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                pidfile = root / "pid"
                (root / "stubborn.py").write_text(
                    "import os, signal, sys, time\n"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
                    "time.sleep(300)\n")
                (root / "leader.py").write_text(
                    "import os, subprocess, sys, time\n"
                    "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n"
                    "deadline = time.monotonic() + 10\n"
                    "while not os.path.exists(sys.argv[2]) and time.monotonic() < deadline:\n"
                    "    time.sleep(0.05)\n")
                process = self.start(f"early={PY} {root / 'leader.py'} {root / 'stubborn.py'} {pidfile}")
                child = self.pid(pidfile)
                self.addCleanup(lambda pid=child: alive(pid) and os.kill(pid, signal.SIGKILL))
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and "ok    early" not in process.stdout.readline():
                    pass
                time.sleep(0.5)  # the run is complete and cleanup is in its grace period
                process.send_signal(signum)
                output, _ = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 128 + signum, output)
                time.sleep(0.2)
                self.assertFalse(alive(child), "the grandchild that ignores SIGTERM was not killed")

    def test_the_stopped_claim_is_printed_only_when_it_is_true(self):
        self.assertIn("every suite was stopped", SCRIPT.read_text())
        self.assertIn("could not be verified", SCRIPT.read_text())

    def test_an_unverified_cleanup_never_claims_every_suite_was_stopped(self):
        import contextlib
        import io
        import unittest.mock as mock
        sys.path.insert(0, str(SCRIPT.parent))
        import run_suites
        buffer = io.StringIO()
        with mock.patch.object(run_suites, "stop", return_value=False), contextlib.redirect_stdout(buffer):
            code = run_suites.main([f"a={say('x')}"])
        self.assertEqual(code, 1)
        self.assertIn("could not be verified", buffer.getvalue())
        self.assertNotIn("every suite was stopped", buffer.getvalue())

    def test_stop_itself_reports_a_group_it_could_not_verify_empty(self):
        import unittest.mock as mock
        sys.path.insert(0, str(SCRIPT.parent))
        import run_suites
        process = subprocess.Popen([PY, "-c", "pass"], start_new_session=True)
        with tempfile.TemporaryFile() as log:
            entry = run_suites.Launched(process, log, time.monotonic())
            with mock.patch.object(run_suites.Launched, "occupied", return_value=True):
                self.assertFalse(run_suites.stop([entry], grace=0.1))
        self.assertTrue(run_suites.stop([], grace=0.1))

    def test_a_non_finite_timeout_is_refused(self):
        for bad in ("nan", "inf", "-inf", "Infinity"):
            done = subprocess.run([PY, str(SCRIPT), f"a={say('x')}"], capture_output=True, text=True,
                                  env={**os.environ, "RUN_SUITES_TIMEOUT": bad})
            self.assertEqual(done.returncode, 1, bad)
            self.assertIn("RUN_SUITES_TIMEOUT", done.stderr + done.stdout, bad)

    def test_the_timeout_is_documented_and_validated(self):
        self.assertIn("RUN_SUITES_TIMEOUT", SCRIPT.read_text())
        for bad in ("abc", "0", "-3"):
            done = subprocess.run([PY, str(SCRIPT), f"a={say('x')}"], capture_output=True, text=True,
                                  env={**os.environ, "RUN_SUITES_TIMEOUT": bad})
            self.assertEqual(done.returncode, 1, bad)


if __name__ == "__main__":
    unittest.main()
