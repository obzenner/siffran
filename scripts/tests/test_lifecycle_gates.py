#!/usr/bin/env python3
"""Lifecycle gates through make itself: ``make lint`` and ``make adr-check`` run their tool when it is present and fail
when it fails, fail when it is missing unless ``EMPIRICA_ALLOW_SKIP=1`` explicitly acknowledges the omission (the
convention doc/testing.md names for Node/TypeScript tooling), and ``make release-check``'s closing banner claims only
automated checks and names the operator evidence still owed.

The tools are fakes: ``#!/bin/sh`` scripts in a temporary directory that is the whole ``PATH`` of the make under test
(with a link to the real ``printf``, which make execs directly for a recipe line without shell syntax; make runs by its
absolute path, the recipe shell is the Makefile's absolute /bin/bash). Nothing is installed or removed,
no real tool runs, and every make is bounded. The make under test gets a fresh environment, never the caller's, so an
outer ``MAKEFLAGS``, ``JOBS`` or ``EMPIRICA_ALLOW_SKIP`` cannot change the verdict; no assertion prints an environment."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKE = shutil.which("make")
LIMIT_SECONDS = 60
# (target, fake tool, the arguments the recipe passes it)
GATES = (("lint", "ruff", ["check", "plugins", "lib", "scripts"]), ("adr-check", "adrs", ["--ng", "doctor"]))


def release_check_prerequisites() -> list[str]:
    line = next(x for x in (ROOT / "Makefile").read_text().splitlines() if x.startswith("release-check:"))
    return line.split(":", 1)[1].split("##")[0].split()


class Gates(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(MAKE, "make is not on PATH")
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.root / "home").mkdir()
        # make runs a recipe line with no shell syntax (the targets' `printf` banners) by exec, not through the shell, so
        # the one real utility the recipes need besides shell builtins is linked in; no tool under test is.
        printf = shutil.which("printf")
        self.assertIsNotNone(printf, "printf is not on PATH")
        (self.bin / "printf").symlink_to(printf)

    def fake(self, tool: str, code: int) -> Path:
        """A fake ``tool`` that records its arguments, one per line, and exits ``code``."""
        record = self.root / f"{tool}.args"
        script = self.bin / tool
        script.write_text(f"#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done > '{record}'\nexit {code}\n")
        script.chmod(0o755)
        return record

    def make(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        env = {"PATH": str(self.bin), "HOME": str(self.root / "home"), "LANG": "C", **extra}
        return subprocess.run([MAKE, "--no-print-directory", "-C", str(ROOT), *args], capture_output=True, text=True,
                              env=env, timeout=LIMIT_SECONDS, check=False)

    def test_a_present_tool_runs_with_the_recipe_s_arguments_and_its_success_passes(self):
        for target, tool, arguments in GATES:
            with self.subTest(target=target):
                record = self.fake(tool, 0)
                done = self.make(target)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(record.read_text().split("\n")[:-1], arguments)

    def test_a_present_tool_s_failure_fails_the_target(self):
        # make reports a failed recipe with its own nonzero status, not the tool's: the propagation is asserted, not 3
        for target, tool, _ in GATES:
            with self.subTest(target=target):
                record = self.fake(tool, 3)
                done = self.make(target)
                self.assertTrue(record.exists(), f"{tool} was not run")
                self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_missing_tool_fails_and_names_the_explicit_skip(self):
        for target, tool, _ in GATES:
            for flag in ({}, {"EMPIRICA_ALLOW_SKIP": "0"}, {"EMPIRICA_ALLOW_SKIP": ""}):
                with self.subTest(target=target, flag=flag):
                    done = self.make(target, **flag)
                    self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
                    self.assertIn(tool, done.stderr)
                    self.assertIn("EMPIRICA_ALLOW_SKIP=1", done.stderr)

    def test_a_missing_tool_is_skipped_only_when_explicitly_acknowledged_and_says_so(self):
        for target, tool, _ in GATES:
            # in the environment, or on make's command line (which make exports into the recipe's environment)
            for done in (self.make(target, EMPIRICA_ALLOW_SKIP="1"), self.make(target, "EMPIRICA_ALLOW_SKIP=1")):
                with self.subTest(target=target, command=done.args[-1]):
                    self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                    self.assertIn(f"{tool} not installed", done.stdout)
                    self.assertIn("explicitly skipped (EMPIRICA_ALLOW_SKIP=1)", done.stdout)

    def test_release_check_claims_only_automated_checks_and_names_the_operator_evidence_still_owed(self):
        """Release-check's own recipe, from a dry run with each prerequisite marked old (-o), so make neither remakes nor
        dry-runs them; the recipe is printf lines, which -n prints instead of running."""
        old = [flag for name in release_check_prerequisites() for flag in ("-o", name)]
        done = self.make("-n", *old, "release-check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        banner = done.stdout
        self.assertIn("Automated release checks passed", banner)
        self.assertNotIn("Ready to release", banner)
        for owed in ("native qualification", "make native-qualification", "generic-docker-qualify-candidate",
                     "generic-docker-install-candidate", "contracts/empirica-docker-spike/v1/support-contract.json"):
            self.assertIn(owed, banner)


if __name__ == "__main__":
    unittest.main()
