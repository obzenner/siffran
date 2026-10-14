#!/usr/bin/env python3
"""The native-qualification census (.agents/skills/native-qualification/scripts/census.py) never reports a checkout it
could not read: when git fails it exits nonzero with git's stderr and prints no JSON (CP-2), and on a real checkout it
prints the facts."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CENSUS = REPO / ".agents/skills/native-qualification/scripts/census.py"


def run(root: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    """census.py copied to `root`'s skill path, so its ROOT (four parents up) is `root`."""
    target = root / ".agents/skills/native-qualification/scripts/census.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(CENSUS, target)
    return subprocess.run([sys.executable, str(target)], capture_output=True, text=True, timeout=30, env=env)


class CensusTests(unittest.TestCase):
    def test_a_root_that_is_not_a_git_checkout_fails_with_gits_stderr_and_no_json(self):
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(Path(d).resolve().parent)}
            r = run(Path(d), env)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("not a git repository", r.stderr.lower())
            self.assertEqual(r.stdout.strip(), "", "no JSON on failure")

    def test_a_git_that_fails_fails_the_census(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True, timeout=30)
            fake = Path(d) / "bin"
            fake.mkdir()
            (fake / "git").write_text("#!/bin/sh\necho 'fatal: broken git' >&2\nexit 128\n")
            (fake / "git").chmod(0o755)
            r = run(Path(d), {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("fatal: broken git", r.stderr)
            self.assertEqual(r.stdout.strip(), "")

    def test_a_checkout_reports_its_head_and_state(self):
        with tempfile.TemporaryDirectory() as d:
            g = ["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run(["git", "init", "-q", d], check=True, timeout=30)
            subprocess.run([*g, "commit", "-q", "--allow-empty", "-m", "x"], check=True, timeout=30)
            r = run(Path(d))
            self.assertEqual(r.returncode, 0, r.stderr)
            facts = json.loads(r.stdout)
            self.assertEqual(len(facts["head"]), 40)
            self.assertTrue(facts["dirty"], "the copied census is an untracked file")


if __name__ == "__main__":
    unittest.main()
