"""run_suites: a failing suite fails the run, logs are kept and separate, only failures print their log."""
import atexit
import io
import itertools
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "run_suites.py"
PY = sys.executable
# Every runner these tests start writes its durations file here, never to the user's real one.
_SCRATCH = tempfile.mkdtemp(prefix="run-suites-test-")
atexit.register(shutil.rmtree, _SCRATCH, ignore_errors=True)
os.environ["RUN_SUITES_DURATIONS"] = str(Path(_SCRATCH) / "durations.json")
_RUNS = itertools.count()


def runner_env(**extra: str) -> dict:
    """The environment of a runner under test: independent of the machine. A test that needs concurrency must not depend on
    the host's load average (under ``make check`` it exceeds the CPU count and the runner admits one suite at a time), nor on
    a durations file another test wrote (it fixes the start order). A test that needs a load or a history passes its own."""
    return {**os.environ, "RUN_SUITES_MAX_LOAD": "1000000",
            "RUN_SUITES_DURATIONS": str(Path(_SCRATCH) / f"durations-{next(_RUNS)}.json"), **extra}


def run(*suites: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(SCRIPT), *suites], capture_output=True, text=True, check=False, env=runner_env())


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
        """A rendezvous, not a stopwatch: each suite marks itself started and passes only once all three are, so the run
        passes only if they run side by side (run one at a time, the first would wait for the others and fail)."""
        with tempfile.TemporaryDirectory() as directory:
            def meet(name: str) -> str:
                flags = [str(Path(directory) / n) for n in "abc"]
                wait = " && ".join(f"[ -f {f} ]" for f in flags)
                return f"sh -c 'touch {Path(directory) / name}; for i in $(seq 600); do {wait} && exit 0; sleep 0.1; done; exit 1'"
            done = run(f"a={meet('a')}", f"b={meet('b')}", f"c={meet('c')}")
        self.assertEqual(done.returncode, 0, done.stdout)

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
                                   env=runner_env(**(env or {})))
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
            # killed, not waited for: the stuck suite sleeps 300 s, so the run ending at all (a liveness bound) with the
            # runner's own timeout message is the proof
            done = subprocess.run([PY, str(SCRIPT), f"stuck={stuck(pidfile)}", f"fine={say('ok')}"], capture_output=True,
                                  text=True, env=runner_env(RUN_SUITES_TIMEOUT="1"), timeout=120)
            self.assertEqual(done.returncode, 1)
            self.assertIn("timed out after 1 s", done.stdout)
            self.assertIn("ok    fine", done.stdout)
            time.sleep(0.3)
            self.assertFalse(alive(int(pidfile.read_text())))

    def test_a_suite_s_declared_limit_replaces_the_default_for_it_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "pid"
            # the default stays 600 s: only the suite that declares @1 is bounded at 1 s, and its group is killed
            done = subprocess.run([PY, str(SCRIPT), f"stuck@1={stuck(pidfile)}", f"fine={say('ok')}"], capture_output=True,
                                  text=True, env=runner_env(), timeout=120)
            self.assertEqual(done.returncode, 1)
            self.assertIn("FAIL  stuck", done.stdout)
            self.assertIn("timed out after 1 s", done.stdout)
            self.assertIn("ok    fine", done.stdout)
            time.sleep(0.3)
            self.assertFalse(alive(int(pidfile.read_text())))

    def test_a_declared_limit_must_be_a_finite_positive_number(self):
        for bad in ("a@=true", "a@0=true", "a@-1=true", "a@x=true", "a@inf=true"):
            done = subprocess.run([PY, str(SCRIPT), bad], capture_output=True, text=True, env=runner_env(), timeout=60)
            self.assertEqual(done.returncode, 1, bad)
            self.assertIn("run_suites:", done.stderr + done.stdout, bad)

    def test_a_grandchild_that_outlives_its_leader_is_gone_after_the_runner_exits(self):
        for code in (0, 7):
            with self.subTest(exit=code), tempfile.TemporaryDirectory() as directory:
                pidfile = Path(directory) / "pid"
                leader = (f"{PY} -c \"import subprocess,sys; p=subprocess.Popen(['sleep','300']); "
                          f"open('{pidfile}','w').write(str(p.pid)); sys.exit({code})\"")
                done = subprocess.run([PY, str(SCRIPT), f"early={leader}"], capture_output=True, text=True, timeout=30,
                                      env=runner_env())
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
                                  env=runner_env(RUN_SUITES_TIMEOUT=bad))
            self.assertEqual(done.returncode, 1, bad)
            self.assertIn("RUN_SUITES_TIMEOUT", done.stderr + done.stdout, bad)

    def test_the_timeout_is_documented_and_validated(self):
        self.assertIn("RUN_SUITES_TIMEOUT", SCRIPT.read_text())
        for bad in ("abc", "0", "-3"):
            done = subprocess.run([PY, str(SCRIPT), f"a={say('x')}"], capture_output=True, text=True,
                                  env=runner_env(RUN_SUITES_TIMEOUT=bad))
            self.assertEqual(done.returncode, 1, bad)


class LoadAware(unittest.TestCase):
    """Load-aware admission, the progress guarantee, the JOBS cap and longest-first order (the load, the clock and the
    sleep are injected, so no real machine load is needed)."""

    def setUp(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import run_suites
        self.rs = run_suites
        self.dir = Path(tempfile.mkdtemp(prefix="load-aware-"))
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self.events = self.dir / "events"

    def worker(self, name: str, seconds: float = 0.4, code: int = 0) -> str:
        """A suite that records ``start <name>`` and ``end <name>`` (one O_APPEND write each) around a sleep."""
        source = (f"import os,time\n"
                  f"def note(t):\n"
                  f"    fd=os.open({str(self.events)!r}, os.O_WRONLY|os.O_APPEND|os.O_CREAT); os.write(fd, t.encode()); os.close(fd)\n"
                  f"note('start {name}\\n'); time.sleep({seconds}); note('end {name}\\n')\n"
                  f"raise SystemExit({code})\n")
        script = self.dir / f"{name}.py"
        script.write_text(source)
        return f"{PY} {script}"

    def go(self, names, load, max_load, jobs=None, seconds=0.4, stall=600):
        import io
        suites = [(n, self.rs.shlex.split(self.worker(n, seconds))) for n in names]
        out = io.StringIO()
        polls = {"n": 0}

        def sleep(delay):
            polls["n"] += 1
            if polls["n"] > stall:
                raise AssertionError("the runner stalled: nothing was running and nothing was started")
            time.sleep(0.01)

        logdir = self.dir / "logs"
        logdir.mkdir(exist_ok=True)
        results = self.rs.run(suites, logdir, out=out, timeout=30, load=load, max_load=max_load, jobs=jobs, sleep=sleep)
        text = self.events.read_text().split() if self.events.exists() else []
        events = [" ".join(text[i:i + 2]) for i in range(0, len(text), 2)]
        return events, out.getvalue(), results

    def concurrent_peak(self, events):
        running = peak = 0
        for event in events:
            running += 1 if event.startswith("start") else -1
            peak = max(peak, running)
        return peak

    def test_high_load_runs_one_suite_at_a_time(self):
        events, out, results = self.go(["a", "b", "c"], load=lambda: 99.0, max_load=4.0)
        self.assertEqual(events, ["start a", "end a", "start b", "end b", "start c", "end c"])
        self.assertTrue(all(code == 0 for code, _ in results.values()))

    def test_low_load_starts_every_suite_at_once(self):
        events, _, _ = self.go(["a", "b", "c"], load=lambda: 0.5, max_load=4.0)
        self.assertEqual(sorted(events[:3]), ["start a", "start b", "start c"])
        self.assertEqual(self.concurrent_peak(events), 3)

    def test_queued_suites_start_when_the_load_drops_mid_run(self):
        def started() -> bool:
            return self.events.exists() and "start a" in self.events.read_text()

        def load() -> float:
            return 0.5 if started() else 99.0  # busy until suite a runs, then idle

        events, _, _ = self.go(["a", "b", "c"], load=load, max_load=4.0, seconds=1.0)
        self.assertEqual(events[0], "start a")
        self.assertLess(events.index("start b"), events.index("end a"), events)
        self.assertLess(events.index("start c"), events.index("end a"), events)

    def test_something_always_runs_while_work_is_queued_whatever_the_load(self):
        # The load never drops below the limit; the runner must still finish, one suite at a time (a stall raises).
        events, _, results = self.go(["a", "b"], load=lambda: 1e9, max_load=0.001)
        self.assertEqual(events, ["start a", "end a", "start b", "end b"])
        self.assertEqual(len(results), 2)

    def test_jobs_is_a_hard_cap_even_when_the_load_is_low(self):
        events, _, _ = self.go(["a", "b", "c", "d"], load=lambda: 0.1, max_load=8.0, jobs=2)
        self.assertEqual(self.concurrent_peak(events), 2, events)
        self.assertEqual(sorted(e for e in events if e.startswith("end")), ["end a", "end b", "end c", "end d"])

    def test_the_admission_decision_is_in_the_log_header_and_the_status_output(self):
        _, out, _ = self.go(["a", "b"], load=lambda: 9.6, max_load=14.0)
        self.assertIn("started at load 9.6/14", out)
        header = (self.dir / "logs" / "a.log").read_text()
        self.assertTrue(header.startswith("run_suites: started at load 9.6/14"), header)

    def test_a_forced_start_says_so(self):
        _, out, _ = self.go(["a"], load=lambda: 30.0, max_load=14.0)
        self.assertIn("started at load 30.0/14 (nothing was running)", out)

    def test_a_load_probe_that_fails_admits_like_an_idle_machine(self):
        import unittest.mock as mock
        with mock.patch.object(self.rs.os, "getloadavg", side_effect=OSError("no load average")):
            self.assertEqual(self.rs.system_load(), 0.0)

    def test_longest_first_order_comes_from_the_durations_file(self):
        path = self.dir / "durations.json"
        path.write_text('{"a": 1.0, "b": 80.0, "c": 5.0}')
        suites = [("a", ["x"]), ("b", ["x"]), ("c", ["x"])]
        self.assertEqual([n for n, _ in self.rs.order_suites(suites, path)], ["b", "c", "a"])

    def test_a_suite_with_no_recorded_time_starts_before_the_known_ones(self):
        path = self.dir / "durations.json"
        path.write_text('{"a": 1.0, "c": 5.0}')
        suites = [("a", ["x"]), ("b", ["x"]), ("c", ["x"])]
        self.assertEqual([n for n, _ in self.rs.order_suites(suites, path)], ["b", "c", "a"])

    def test_a_missing_or_corrupt_durations_file_keeps_the_declared_order(self):
        suites = [("a", ["x"]), ("b", ["x"]), ("c", ["x"])]
        declared = ["a", "b", "c"]
        path = self.dir / "durations.json"
        self.assertEqual([n for n, _ in self.rs.order_suites(suites, path)], declared)  # missing
        for bad in ("not json", "[1, 2]", "null", '{"a": "slow", "b": -3, "c": NaN}', ""):
            path.write_text(bad)
            self.assertEqual([n for n, _ in self.rs.order_suites(suites, path)], declared, bad)
        self.assertEqual([n for n, _ in self.rs.order_suites(suites, self.dir)], declared)  # a directory: unreadable

    def test_durations_are_recorded_for_passing_suites_only_and_merged(self):
        path = self.dir / "durations.json"
        path.write_text('{"old": 7.0, "a": 99.0}')
        self.rs.record_durations(path, {"a": (0, 3.5), "b": (1, 9.0), "c": ("timeout", 600.0)})
        import json
        self.assertEqual(json.loads(path.read_text()), {"old": 7.0, "a": 3.5})

    def test_a_corrupt_durations_file_is_replaced_not_fatal(self):
        path = self.dir / "durations.json"
        path.write_text("garbage")
        self.rs.record_durations(path, {"a": (0, 2.0)})
        self.assertIn('"a"', path.read_text())

    def test_the_end_to_end_runner_starts_the_longest_suite_first_and_records_times(self):
        path = self.dir / "durations.json"
        path.write_text('{"quick": 1.0, "slow": 60.0}')
        done = subprocess.run([PY, str(SCRIPT), f"quick={say('q')}", f"slow={say('s')}"], capture_output=True, text=True,
                              env=runner_env(RUN_SUITES_DURATIONS=str(path)))
        self.assertEqual(done.returncode, 0, done.stdout)
        starts = [line.split()[1] for line in done.stdout.splitlines() if line.strip().startswith("start")]
        self.assertEqual(starts, ["slow", "quick"], done.stdout)
        import json
        self.assertEqual(sorted(json.loads(path.read_text())), ["quick", "slow"])
        self.assertLess(json.loads(path.read_text())["slow"], 30)  # the recorded time replaced the old one

    def test_jobs_and_the_load_limit_are_validated(self):
        for name, bad in (("JOBS", "0"), ("JOBS", "-2"), ("JOBS", "two"), ("JOBS", "1.5"),
                          ("RUN_SUITES_MAX_LOAD", "nan"), ("RUN_SUITES_MAX_LOAD", "0"), ("RUN_SUITES_MAX_LOAD", "-1"),
                          ("RUN_SUITES_MAX_LOAD", "high"), ("RUN_SUITES_MAX_LOAD", "inf")):
            done = subprocess.run([PY, str(SCRIPT), f"a={say('x')}"], capture_output=True, text=True,
                                  env=runner_env(**{name: bad}))
            self.assertEqual(done.returncode, 1, (name, bad))
            self.assertIn(name, done.stdout + done.stderr, (name, bad))

    def test_an_empty_jobs_means_no_cap(self):
        done = subprocess.run([PY, str(SCRIPT), f"a={say('x')}", f"b={say('y')}"], capture_output=True, text=True,
                              env=runner_env(JOBS=""))
        self.assertEqual(done.returncode, 0, done.stdout)

    def test_the_scheduling_documentation_names_what_the_runner_reads(self):
        """doc/testing.md owns the scheduling rule: it must name every environment variable the runner reads and the
        default durations file, and the runner and the Makefile must point at it (so the rule has one home)."""
        import re
        source = SCRIPT.read_text()
        root = SCRIPT.parents[1]
        testing = (root / "doc" / "testing.md").read_text()
        read = set(re.findall(r'os\.environ\.get\("([A-Z_]+)"', source))
        self.assertTrue({"JOBS", "RUN_SUITES_MAX_LOAD", "RUN_SUITES_DURATIONS", "RUN_SUITES_TIMEOUT"} <= read, read)
        for name in sorted(read):
            self.assertIn(f"`{name}`", testing, f"doc/testing.md does not name {name}")
        default = re.search(r'tempfile\.gettempdir\(\)\) / "([^"]+)"', source).group(1)
        self.assertIn(default, testing)
        self.assertIn("doc/testing.md", source)
        self.assertIn("doc/testing.md", (root / "Makefile").read_text())


#: A leader that starts a child ignoring SIGTERM, prints the child's pid once it is ready, and then waits; the leader itself
#: dies on SIGTERM, so a cleanup that stops at the leader's exit leaves the child behind.
LEADER_EXITS = """import os, signal, sys
r, w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(r); signal.signal(signal.SIGTERM, signal.SIG_IGN); os.write(w, b'R'); os.close(w)
    while True:
        signal.pause()
os.close(w); os.read(r, 1); os.close(r); print(pid, flush=True)
while True:
    signal.pause()
"""


class SharedOwner(unittest.TestCase):
    """AST-4: the one process-group owner (``Groups``/``stop``) that run_suites and verify share. Tiny graces only."""

    def setUp(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import run_suites
        self.rs = run_suites

    def kill_group(self, pgid: int) -> None:
        """Test cleanup: the exact group this test created, if anything of it is left."""
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def test_cleanup_kills_a_child_that_ignores_sigterm_even_when_its_leader_exits_on_sigterm(self):
        owner = self.rs.Groups()
        entry = owner.start([PY, "-u", "-c", LEADER_EXITS], None, time.monotonic(), stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.kill_group, entry.pgid)
        child = int(entry.process.stdout.readline())
        self.assertTrue(alive(child))
        began = time.monotonic()
        self.assertTrue(self.rs.stop([entry], grace=0.3))
        self.assertLess(time.monotonic() - began, 5)
        entry.process.stdout.close()
        self.assertEqual(entry.process.returncode, -signal.SIGTERM, "the leader exited on SIGTERM")
        self.assertFalse(alive(child), "the child that ignores SIGTERM outlived its leader's exit")
        owner.close()
        self.assertFalse(owner.root.exists(), "an emptied owner leaves no registry behind")

    def test_a_registration_lasts_until_its_group_is_empty_and_never_names_the_caller(self):
        owner = self.rs.Groups()
        entry = owner.start([PY, "-c", "import time; time.sleep(30)"], None, time.monotonic())
        self.addCleanup(self.kill_group, entry.pgid)
        self.assertEqual((entry.slot / "pgid").read_text(), str(entry.pgid))
        nested = entry.slot / "groups-x" / "g1"
        nested.mkdir(parents=True)
        (nested / "pgid").write_text(str(os.getpgrp()))  # a registry naming this process's own group is ignored
        self.assertEqual(entry.descendants(), set())
        self.assertIsNone(entry.poll())
        self.assertTrue(self.rs.stop([entry], grace=0.2))
        self.assertTrue(entry.done)
        self.assertFalse(entry.slot.exists())
        self.assertTrue(self.rs.stop([entry], grace=0.2), "a cleaned-up group is not signalled again")

    def test_a_suite_over_its_limit_that_runs_run_suites_takes_the_inner_suites_with_it(self):
        """A nested runner: run_suites' timeout on a suite that is itself a run_suites whose suite ignores SIGTERM. The
        inner runner's own grace is far longer than the test, so only the outer owner's registry can end that suite."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pidfile = root / "inner.pid"
            (root / "inner_logs").mkdir()
            (root / "outer_logs").mkdir()
            suite = (f"import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                     f"open({str(pidfile) + '.tmp'!r}, 'w').write(str(os.getpid())); "
                     f"os.replace({str(pidfile) + '.tmp'!r}, {str(pidfile)!r}); time.sleep(60)")
            (root / "runner.py").write_text(
                "import sys\n"
                "from pathlib import Path\n"
                f"sys.path.insert(0, {str(SCRIPT.parent)!r})\n"
                "import run_suites\n"
                "run_suites.GRACE = 60.0\n"
                f"run_suites.run([('stubborn', [sys.executable, '-c', {suite!r}])], Path({str(root / 'inner_logs')!r}), "
                "timeout=60, load=lambda: 0.0, max_load=99.0)\n")
            self.rs.GRACE, saved = 0.3, self.rs.GRACE
            self.addCleanup(setattr, self.rs, "GRACE", saved)
            began = time.monotonic()
            results = self.rs.run([("nested", [PY, str(root / "runner.py")])], root / "outer_logs", out=io.StringIO(),
                                  timeout=1.5, load=lambda: 0.0, max_load=99.0)
            self.assertEqual(results["nested"][0], "timeout")
            self.assertLess(time.monotonic() - began, 10)
            self.assertTrue(pidfile.exists(), "the inner suite never started")
            inner = int(pidfile.read_text())
            self.addCleanup(self.kill_group, inner)  # its own group (start_new_session), should it be left
            time.sleep(0.2)
            self.assertFalse(alive(inner), "the inner runner's suite outlived the outer timeout")


if __name__ == "__main__":
    unittest.main()
