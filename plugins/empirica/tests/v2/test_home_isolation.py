"""No test writes the developer's home.

Operational state lives under ``EMPIRICA_HOME`` (default ``~/.empirica-plugin``, ADR-31). Every ``ConformanceCase``
therefore gets its own ``EMPIRICA_HOME``. This is
the structural guard, and it stays fast enough for ``check-core``: every test case in the suites that reach the host
mediator must be a ``ConformanceCase``, and a probe case run in a subprocess with ``HOME`` a fresh directory and
``EMPIRICA_HOME`` unset must leave that directory empty. Its negative control removes the redirect and sees the write."""
from __future__ import annotations

import importlib
import inspect
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
SUITES = ["test_evidence_confirmation", "test_evidence_hosts", "test_evidence_scenarios", "test_a1_boundaries"]

PROBE = '''
import sys, unittest
import assertions
{patch}
from adapters.state.repository import empirica_home

class Probe(assertions.ConformanceCase):
    def test_a_state_write_goes_to_the_cases_own_home(self):
        home = empirica_home()
        home.mkdir(parents=True, exist_ok=True)
        (home / "probe-state").write_text("written")

unittest.main(argv=["probe"], verbosity=0)
'''


def tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) + ("/" if p.is_dir() else f":{p.read_bytes().hex()}") for p in root.rglob("*"))


def run(args: list[str], home: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "EMPIRICA_HOME"}
    env.update(HOME=str(home), PYTHONPATH=os.pathsep.join([str(PLUGIN), str(HERE.parent), str(HERE)]))
    return subprocess.run([sys.executable, *args], cwd=HERE, env=env, capture_output=True, text=True)


class HomeIsolationTests(unittest.TestCase):
    def test_every_case_in_the_suites_that_reach_the_mediator_gets_the_redirect(self):
        import assertions
        for name in SUITES:
            module = importlib.import_module(name)
            cases = [c for c in vars(module).values() if inspect.isclass(c) and issubclass(c, unittest.TestCase)
                     and c.__module__ == name]
            self.assertTrue(cases, name)
            self.assertEqual([c.__name__ for c in cases if not issubclass(c, assertions.ConformanceCase)], [], name)

    def test_a_case_with_the_redirect_leaves_the_home_clean(self):
        with tempfile.TemporaryDirectory() as home:
            done = run(["-c", PROBE.format(patch="")], Path(home))
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            self.assertEqual(tree(Path(home)), [])

    def test_negative_control_without_the_redirect_the_write_is_seen(self):
        with tempfile.TemporaryDirectory() as home:
            patch = "assertions.ConformanceCase.setUp = lambda self: None"
            done = run(["-c", PROBE.format(patch=patch)], Path(home))
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            written = tree(Path(home))
            self.assertTrue(any("probe-state" in row for row in written), written)


if __name__ == "__main__":
    unittest.main()
