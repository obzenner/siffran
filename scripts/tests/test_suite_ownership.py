"""Every test file is run by at most one fast suite, and by one fast suite or a named integration target.

The suites are read from ``make -n`` (the Makefile is their only declaration; ``FAST_SUITES`` names the fast ones). Each
dry-run command is resolved the way the shell would run it: ``cd`` changes the working directory, a module or path
argument is resolved against it, and a discovery pattern is globbed there. A file is owned by a suite when its
repository-relative canonical path is among what that suite's commands select; a name alone never owns a path."""
from __future__ import annotations

import glob
import os
import re
import shlex
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTEGRATION = ("empirica-core-integration", "empirica-host-integration", "empirica-subagents-matrix",
               "empirica-pi-api-typecheck", "empirica-governance-check", "observer-smoke", "observer-deep", "ui-smoke")
# Test files deliberately outside every suite, with the reason. None today.
EXCEPTIONS: dict[str, str] = {}
NOT_TESTS = re.compile(r"(^|/)(node_modules|\.git|__pycache__|vendor|\.stryker-tmp|\.venv)/")
TEST_FILE = re.compile(r"(^|/)test_[^/]*\.py$|\.test\.(mjs|ts)$")


def fast_suites() -> tuple[str, ...]:
    """The fast suite names, as the Makefile declares them."""
    declared = re.search(r"^FAST_SUITES\s*:=\s*(.+)$", (ROOT / "Makefile").read_text(encoding="utf-8"), re.M)
    assert declared, "Makefile declares no FAST_SUITES"
    return tuple(declared.group(1).split())


def dry(target: str) -> str:
    done = subprocess.run(["make", "-n", "--no-print-directory", target], cwd=ROOT, capture_output=True, text=True,
                          check=False)
    return done.stdout + done.stderr


def _add(owned: set[str], cwd: str, pattern: str) -> None:
    for hit in glob.glob(os.path.normpath(os.path.join(ROOT, cwd, pattern))):
        relative = os.path.relpath(hit, ROOT)
        if TEST_FILE.search(relative):
            owned.add(relative)


def owned_paths(text: str) -> set[str]:
    """The repository-relative test files a dry run selects, with every path resolved against its own ``cd``."""
    owned: set[str] = set()
    for command in re.sub(r"\\\n", " ", text).splitlines():
        cwd = "."
        for part in re.split(r"\s*&&\s*|\s*;\s*", command):
            try:
                tokens = shlex.split(part)
            except ValueError:
                continue
            if tokens[:1] == ["cd"] and len(tokens) == 2:
                cwd = os.path.normpath(os.path.join(cwd, tokens[1]))
                continue
            for token in tokens:
                if re.search(r"\.(py|mjs|ts)$", token) or "*" in token:
                    _add(owned, cwd, token)
            if "unittest" in tokens:
                after = tokens[tokens.index("unittest") + 1:]
                if "discover" in after:
                    pattern = after[after.index("-p") + 1] if "-p" in after else "test*.py"
                    _add(owned, cwd, pattern)
                else:
                    for token in after:
                        if not token.startswith("-"):
                            _add(owned, cwd, token.split(".")[0] + ".py")
            if any(token.endswith("/.bin/vitest") for token in tokens) and "run" in tokens:
                _add(owned, cwd, "tests/*.test.mjs")  # `vitest run`: its config's include (ui/tests/imports.test.mjs)
            if any(token.endswith("validate_pi_adapter.py") for token in tokens) and "--package-only" not in tokens:
                for token in tokens[tokens.index(next(t for t in tokens if t.endswith("validate_pi_adapter.py"))) + 1:]:
                    if not token.startswith("-"):
                        _add(owned, cwd, f"{token}/test/*.test.ts")  # an adapter directory
                        _add(owned, cwd, f"{token}/adapters/pi/test/*.test.ts")  # a plugin directory
    return owned


def test_files() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.split("\0")
    return sorted(p for p in listed if p and TEST_FILE.search(p) and not NOT_TESTS.search(p) and (ROOT / p).exists())


def problem(path: str, fast: dict[str, set[str]], named: dict[str, set[str]], exceptions: dict[str, str]) -> str | None:
    """What is wrong with where a test file runs, or None. ``path`` is repository-relative."""
    in_fast = [name for name, owned in fast.items() if path in owned]
    in_named = [name for name, owned in named.items() if path in owned]
    if path in exceptions:
        return f"{path}: listed as an exception but a suite runs it ({in_fast + in_named})" if in_fast or in_named else None
    if len(in_fast) > 1:
        return f"{path}: run by several fast suites {in_fast}"
    if not in_fast and not in_named:
        return f"{path}: run by no fast suite and no integration target"
    return None


class SuiteOwnership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = fast_suites()
        cls.fast = {name: owned_paths(dry(f"check-{name}")) for name in cls.names}
        cls.integration = {name: owned_paths(dry(name)) for name in INTEGRATION}

    def test_every_test_file_has_exactly_one_home(self):
        found = [problem(p, self.fast, self.integration, EXCEPTIONS) for p in test_files()]
        self.assertEqual([p for p in found if p], [])

    def test_the_suites_are_read_from_the_makefile(self):
        self.assertIn("evidence", self.names)
        for name in self.names:
            self.assertTrue(self.fast[name], f"check-{name} selects no test file")
        self.assertIn("plugins/empirica/tests/v2/test_a1_boundaries.py", self.fast["evidence"])
        self.assertNotIn("plugins/empirica/tests/v2/test_a1_boundaries.py", self.fast["core"])
        self.assertIn(
            "plugins/empirica/tests/v2/test_budget_freeze_terminal.py", self.fast["core"])
        self.assertIn(
            "plugins/empirica/tests/v2/test_evidence_freshness.py", self.fast["evidence"])
        self.assertIn(
            "plugins/empirica/tests/v2/test_public_host_path.py", self.fast["evidence"])
        self.assertIn(
            "plugins/empirica/tests/test_execution_adapter.py", self.fast["core"])

    def test_check_core_selects_the_three_real_escape_transaction_cases_and_integration_runs_the_whole_module(self):
        """A named selection of an integration module's cases is that module's one fast home (as test_governance's are):
        the fast gate runs exactly these three D7 cases, and the integration target still runs the whole module."""
        module = "plugins/empirica/tests/test_d7_transactions.py"
        self.assertIn(module, self.fast["core"])
        self.assertIn(module, self.integration["empirica-core-integration"])
        core = re.sub(r"\\\n", " ", dry("check-core"))
        selected = set(re.findall(r"\btest_d7_transactions\.D7TransactionTests\.(\w+)", core))
        self.assertEqual(selected, {
            "test_real_generic_docker_harness_result_is_admitted_by_plan_spike_result",
            "test_any_valid_input_order_binds_one_canonical_draft",
            "test_overlapping_or_duplicate_root_inputs_are_invalid_before_any_docker_read",
        })
        self.assertNotRegex(core, r"unittest[^\n]*\btest_d7_transactions(\s|$)", "the whole module is integration-only")
        self.assertNotIn(module, set().union(*(owned for name, owned in self.fast.items() if name != "core")))

    def test_the_same_basename_in_another_directory_is_unowned(self):
        # core runs plugins/methodologist/tests/test_core.py; a new test_core.py elsewhere must not ride on its name
        self.assertIn("plugins/methodologist/tests/test_core.py", self.fast["core"])
        stranger = "plugins/empirica/tests/unowned/test_core.py"
        self.assertIn("run by no fast suite", problem(stranger, self.fast, self.integration, {}))

    def test_paths_are_resolved_against_the_commands_working_directory(self):
        text = "cd plugins/empirica/tests/v2 && PYTHONPATH=..:../.. python3 -m unittest -q test_audit test_async_children"
        self.assertEqual(owned_paths(text), {"plugins/empirica/tests/v2/test_audit.py",
                                             "plugins/empirica/tests/v2/test_async_children.py"})
        self.assertEqual(owned_paths("cd plugins/empirica/tests && python3 -m unittest -q test_audit_retrieval.Case.test_x"),
                         {"plugins/empirica/tests/test_audit_retrieval.py"})
        # the same name resolved in another directory is another file
        self.assertEqual(owned_paths("cd plugins/methodologist/tests && python3 -m unittest -q test_core"),
                         {"plugins/methodologist/tests/test_core.py"})

    def test_negative_controls(self):
        path = "plugins/empirica/tests/test_x.py"
        fast_a, fast_b, none = {path}, {path}, {"other.py"}
        self.assertIsNone(problem(path, {"a": fast_a, "b": none}, {}, {}))
        self.assertIsNone(problem(path, {"a": none}, {"i": fast_a}, {}))
        self.assertIn("several fast suites", problem(path, {"a": fast_a, "b": fast_b}, {}, {}))
        self.assertIn("no fast suite", problem(path, {"a": none}, {"i": none}, {}))
        self.assertIn("listed as an exception", problem(path, {"a": fast_a}, {}, {path: "why"}))
        self.assertIsNone(problem(path, {"a": none}, {}, {path: "why"}))

    def test_exceptions_exist(self):
        for path in EXCEPTIONS:
            self.assertTrue((ROOT / path).exists(), path)


if __name__ == "__main__":
    unittest.main()
