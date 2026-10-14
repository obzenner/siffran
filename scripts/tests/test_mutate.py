"""``make mutate`` (``scripts/mutate.py``): the changed lines it mutates, with which tool, and how a survivor reads (T3)."""
from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mutate  # noqa: E402

# main() reads FILES=, LINES= and RUNNER= from the environment, as make passes them: these tests give their own
for _name in ("FILES", "LINES", "RUNNER"):
    os.environ.pop(_name, None)

DIFF = """diff --git a/plugins/empirica/ui/app/geometry.js b/plugins/empirica/ui/app/geometry.js
--- a/plugins/empirica/ui/app/geometry.js
+++ b/plugins/empirica/ui/app/geometry.js
@@ -40,0 +41,3 @@ export function markRadius(level, s, g) {
+  one
+  two
+  three
@@ -117 +120 @@ export const overlaps = (a, b) =>
-  old
+  new
@@ -130,2 +133,0 @@
-  gone
-  gone
diff --git a/scripts/gone.py b/scripts/gone.py
deleted file mode 100644
--- a/scripts/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x = 1
-y = 2
"""
EVERY = ("plugins/empirica/core/observer.py", "plugins/empirica/ui/elements/views/rows.js", "plugins/empirica/ui/app/app.js",
         "plugins/empirica/tests/v2/test_observer_stateful.py", "plugins/empirica/ui/tests/generated.test.mjs",
         "scripts/validate_ui_view.py", "scripts/tests/test_mutate.py", "doc/testing.md", "Makefile",
         "plugins/empirica/ui/vendor/basecoat/basecoat.js", "plugins/empirica/ui/scripts/audit.mjs")


class ScopeTests(unittest.TestCase):
    def test_the_changed_lines_are_each_hunk_s_new_side_and_a_deletion_changes_no_line(self):
        self.assertEqual(mutate.hunks(DIFF), {"plugins/empirica/ui/app/geometry.js": [(41, 43), (120, 120)]})

    def test_the_default_scope_is_the_diff_s_lines_and_every_line_of_an_untracked_file(self):
        calls = []

        def git(*args):
            calls.append(args)
            return DIFF if args[0] == "diff" else "plugins/empirica/ui/app/new.js\n"
        self.assertEqual(mutate.changed(git), {"plugins/empirica/ui/app/geometry.js": [(41, 43), (120, 120)],
                                               "plugins/empirica/ui/app/new.js": None})
        self.assertEqual(calls, [("diff", "-U0", "--no-color", "--no-ext-diff", "HEAD"), ("ls-files", "--others", "--exclude-standard")])

    def test_sources_are_split_by_tool_and_tests_docs_vendored_code_and_build_scripts_are_left_alone(self):
        work = mutate.plan({f: None for f in EVERY}, exists=lambda p: True)
        self.assertEqual(work.python, {"plugins/empirica/core/observer.py": None, "scripts/validate_ui_view.py": None})
        self.assertEqual(work.ui, {"elements/views/rows.js": None, "app/app.js": None})

    def test_a_ui_path_may_be_given_from_the_ui_s_folder_and_a_missing_file_is_not_mutated(self):
        work = mutate.plan({"app/geometry.js": [(3, 4)], "scripts/gone.py": None}, exists=lambda p: p == "plugins/empirica/ui/app/geometry.js")
        self.assertEqual((work.ui, work.python), ({"app/geometry.js": [(3, 4)]}, {}))

    def test_files_and_lines_override_the_changed_lines(self):
        self.assertEqual(mutate.named("a.py,b.js:10-20"), {"a.py": None, "b.js": [(10, 20)]})
        self.assertEqual(mutate.named("a.py,b.js:7", "1-2,5"), {"a.py": [(1, 2), (5, 5)], "b.js": [(7, 7)]})
        self.assertEqual(mutate.named("a.py:1-2,a.py:9"), {"a.py": [(1, 2), (9, 9)]})
        self.assertEqual(mutate.named("a.py:1-2,a.py"), {"a.py": None}, "the whole file wins")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            mutate.main(["--lines", "1-2"])

    def test_nothing_changed_runs_nothing(self):
        old = (mutate.changed, mutate._stryker, mutate._mutmut)
        try:
            ran = []
            mutate.changed, mutate._stryker, mutate._mutmut = (lambda: {"plugins/empirica/gone.py": None}), ran.append, ran.append
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(mutate.main([]), 0)
            self.assertEqual(ran, [])
            self.assertIn("nothing to mutate", out.getvalue())
        finally:
            mutate.changed, mutate._stryker, mutate._mutmut = old


class StrykerTests(unittest.TestCase):
    """One Stryker run over every UI file's lines; the survivors read from its JSON report, in scope only."""

    def test_mutate_names_each_range_and_a_whole_file_once(self):
        self.assertEqual(mutate.mutate_arg({"app/a.js": [(3, 5), (9, 9)], "app/b.js": None}), "app/a.js:3-5,app/a.js:9-9,app/b.js")

    def test_the_config_is_the_vitest_runner_per_test_incremental_and_related(self):
        import json
        config = json.loads((mutate.ROOT / mutate.UI / "stryker.config.json").read_text(encoding="utf-8"))
        self.assertEqual((config["testRunner"], config["coverageAnalysis"], config["incremental"], config["vitest"]["related"]),
                         ("vitest", "perTest", True, True))
        self.assertEqual(config["incrementalFile"], "reports/stryker-incremental.json")
        self.assertEqual(config["jsonReporter"]["fileName"], mutate.STRYKER_REPORT)
        self.assertNotIn("commandRunner", config)
        self.assertFalse(config["symlinkNodeModules"], "the sandbox sits in the UI's folder: node_modules is found, never linked")
        ignored = (mutate.ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("plugins/empirica/ui/reports/", ignored)

    def test_the_workers_are_capped_at_min_4_and_half_the_cores_and_jobs_chooses(self):
        self.assertEqual([mutate.jobs(None, c) for c in (1, 2, 3, 6, 8, 16, 64)], [1, 1, 1, 3, 4, 4, 4])
        self.assertEqual(mutate.jobs("7", 2), 7, "JOBS= wins")
        with self.assertRaises(ValueError):
            mutate.jobs("0", 8)
        self.assertGreaterEqual(mutate.jobs(), 1)

    def test_the_stryker_command_carries_the_cap_and_mutmut_runs_serially(self):
        cmd = mutate.stryker_command("stryker", {"app/a.js": [(1, 2)]}, 3)
        self.assertEqual(cmd[:3], ["stryker", "run", "stryker.config.json"])
        self.assertEqual(cmd[cmd.index("--concurrency") + 1], "3")
        self.assertEqual(cmd[cmd.index("--mutate") + 1], "app/a.js:1-2")
        import json
        config = json.loads((mutate.ROOT / mutate.UI / "stryker.config.json").read_text(encoding="utf-8"))
        self.assertNotIn("concurrency", config, "the cap is the command line's, never a fixed config value")
        self.assertEqual(config["testRunnerNodeArgs"], ["--max-old-space-size=768"], "each worker's heap is capped")
        self.assertLessEqual(config["timeoutMS"], 5000)
        self.assertLessEqual(config["timeoutFactor"], 1.5)
        mm = mutate.mutmut_command(["py", "-m", "mutmut"], "a.py", "true", "/p")
        self.assertFalse({"--parallel", "-j", "--max-children"} & set(mm), "one mutant at a time")
        self.assertIn("Memory", mutate.__doc__)

    def test_each_survivor_and_uncovered_mutant_in_scope_is_a_finding_and_the_rest_is_counted(self):
        source = "const a = 1;\nexport const f = (x) => x < 10 && ok(x);\nexport const g = 2;\n"
        at = lambda line, c0, c1: {"start": {"line": line, "column": c0}, "end": {"line": line, "column": c1}}  # noqa: E731
        report = {"files": {
            "app/a.js": {"source": source, "mutants": [
                {"status": "Survived", "replacement": "x <= 10", "mutatorName": "EqualityOperator", "testsCompleted": 3, "location": at(2, 25, 31)},
                {"status": "NoCoverage", "replacement": "false", "mutatorName": "ConditionalExpression", "location": at(2, 35, 40)},
                {"status": "Killed", "replacement": "x >= 10", "mutatorName": "EqualityOperator", "testsCompleted": 1, "location": at(2, 25, 31)},
                {"status": "Survived", "replacement": "3", "mutatorName": "NumberLiteral", "location": at(3, 18, 19)},  # out of scope
            ]},
            "app/other.js": {"source": "x", "mutants": [{"status": "Survived", "replacement": "y", "mutatorName": "M", "location": at(1, 1, 2)}]},
        }}
        found, counts = mutate.stryker_findings(report, {"app/a.js": [(2, 2)]})
        self.assertEqual([str(f) for f in found], [
            "plugins/empirica/ui/app/a.js:2  x < 10 → x <= 10  survived (tests ran: 3)",
            "plugins/empirica/ui/app/a.js:2  ok(x) → false  no test covers it"])
        self.assertEqual(counts, {"Survived": 1, "NoCoverage": 1, "Killed": 1})
        self.assertEqual(mutate.score(counts), "33.3% (1 killed, 1 nocoverage, 1 survived)")
        self.assertEqual(mutate.score({"Killed": 2, "Timeout": 2}), "100.0% (2 killed, 2 timeout)")
        self.assertEqual(mutate.score({}), "no mutant in scope")
        many = {"source": "a\nbb + c\nd", "mutants": [{"status": "Survived", "replacement": "x", "mutatorName": "M",
                                                         "location": {"start": {"line": 1, "column": 1}, "end": {"line": 3, "column": 2}}}]}
        self.assertEqual(mutate.stryker_findings({"files": {"app/m.js": many}}, {"app/m.js": None})[0][0].original, "a bb + c d")

    def test_a_long_mutant_is_shortened_to_sixty_characters_on_one_line(self):
        self.assertEqual(mutate._short("x" * 60), "x" * 60)
        self.assertEqual(mutate._short("x" * 61), "x" * 59 + "…")
        self.assertEqual(mutate._short("a\n   b"), "a b")

    def test_a_survivor_makes_the_run_fail_and_is_printed_before_the_score(self):
        old = (mutate.changed, mutate._stryker)
        finding = mutate.Finding("plugins/empirica/ui/app/a.js", 2, "<", "<=", "survived", 4)
        try:
            mutate.changed = lambda: {"plugins/empirica/ui/app/geometry.js": [(1, 2)]}
            mutate._stryker = lambda ui, workers: ([finding], "50.0% (1 killed, 1 survived)", 0)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(mutate.main([]), 1)
            lines = out.getvalue().splitlines()
            self.assertEqual(lines[-2:], ["plugins/empirica/ui/app/a.js:2  < → <=  survived (tests ran: 4)", "Stryker: 50.0% (1 killed, 1 survived)"])
            mutate._stryker = lambda ui, workers: ([], "100.0% (2 killed)", 0)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mutate.main([]), 0)
        finally:
            mutate.changed, mutate._stryker = old


class MutmutTests(unittest.TestCase):
    """mutmut keeps its runner; it mutates only the scope's lines (a patch) in a scratch copy, its survivors in the same form."""

    def test_the_patch_adds_exactly_the_scope_s_lines(self):
        text = mutate.patch({"scripts/a.py": [(2, 3), (9, 12)], "scripts/b.py": None},
                            read={"scripts/a.py": "one\ntwo\nthree\nfour\n", "scripts/b.py": "x\ny"}.__getitem__)
        self.assertEqual(text, "diff -u scripts/a.py scripts/a.py\n--- scripts/a.py\n+++ scripts/a.py\n@@ -0,0 +2,2 @@\n+two\n+three\n"  # 9-12: past its end
                               "diff -u scripts/b.py scripts/b.py\n--- scripts/b.py\n+++ scripts/b.py\n@@ -0,0 +1,2 @@\n+x\n+y\n")
        try:
            import whatthepatch
        except ImportError:
            self.skipTest("make dev-install")
        parsed = {d.header.new_path: sorted(c.new for c in d.changes if c.old is None) for d in whatthepatch.parse_patch(text)}
        self.assertEqual(parsed, {"scripts/a.py": [2, 3], "scripts/b.py": [1, 2]})

    def test_a_shown_mutant_reads_as_its_line_and_the_part_it_changed(self):
        diff = "--- scripts/a.py\n+++ scripts/a.py\n@@ -12 +12 @@\n-    if count < limit:\n+    if count <= limit:\n"
        self.assertEqual(str(mutate.shown(diff, 2)), "scripts/a.py:12  < → <=  survived (tests ran: 2)")
        diff = "--- scripts/a.py\n+++ scripts/a.py\n@@ -3 +3 @@\n-    return value\n+    return None\n"
        self.assertEqual(str(mutate.shown(diff, 1)), "scripts/a.py:3  value → None  survived (tests ran: 1)")
        diff = ("--- scripts/a.py\n+++ scripts/a.py\n@@ -140,7 +140,7 @@\n \n def f(text):\n     text = 1\n"
                "-    return text if n <= limit else 0\n+    return text if n < limit else 0\n \n \n")  # mutmut shows 3 lines of context
        self.assertEqual(str(mutate.shown(diff, 1)), "scripts/a.py:143  <= → <  survived (tests ran: 1)")
        self.assertIsNone(mutate.shown("", 1))

    def test_python_mutants_run_against_the_tests_that_import_the_module(self):
        tests = mutate.tests_for("plugins/empirica/core/observer.py")
        self.assertIn("plugins/empirica/tests/v2/test_observer_agreement.py", tests)
        self.assertTrue(all(Path(t).name.startswith("test_") for t in tests))

    def test_the_runner_runs_each_test_the_way_its_suite_does(self):
        runner = mutate.runner(["plugins/empirica/tests/v2/test_observer_agreement.py", "scripts/tests/test_verify.py"],
                               python="/venv/bin/python")
        self.assertIn("cd plugins/empirica/tests/v2 && PYTHONPATH=..:../..:../../../../scripts /venv/bin/python -m unittest -q "
                      "test_observer_agreement", runner)
        self.assertIn("/venv/bin/python -m unittest -q scripts/tests/test_verify.py", runner)

    def test_the_runner_mutmut_is_given_executes_as_mutmut_splits_it(self):
        """mutmut 2.5.1 runs ``shlex.split(runner)`` without a shell: the generated runner (a ``cd``, ``&&``, a v2 module,
        a scripts test) must run there, and fail when a test fails."""
        import shlex
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for folder in ("plugins/empirica/tests/v2", "scripts/tests"):
                (Path(tmp) / folder).mkdir(parents=True)
            fake = "import unittest\n\nclass T(unittest.TestCase):\n    def test_it(self):\n        self.assertTrue({ok})\n"
            (Path(tmp) / "plugins/empirica/tests/v2/test_fake_v2.py").write_text(fake.format(ok=True))
            (Path(tmp) / "scripts/tests/test_fake.py").write_text(fake.format(ok=True))
            run = mutate.runner(["plugins/empirica/tests/v2/test_fake_v2.py", "scripts/tests/test_fake.py"], python=sys.executable)
            command = mutate.mutmut_command(["py", "-m", "mutmut"], "a.py", run, "/p")
            given = command[command.index("--runner") + 1]
            done = subprocess.run(shlex.split(given, posix=True), cwd=tmp, capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("Ran 1 test", done.stderr)
            (Path(tmp) / "scripts/tests/test_fake.py").write_text(fake.format(ok=False))
            failed = subprocess.run(shlex.split(given, posix=True), cwd=tmp, capture_output=True, text=True, timeout=60)
            self.assertNotEqual(failed.returncode, 0, "a failing test fails the runner")

    def test_every_test_a_runner_names_exists(self):
        for source in ("plugins/empirica/application/observer_index.py", "plugins/empirica/core/observer.py"):
            for test in mutate.tests_for(source):
                self.assertTrue((mutate.ROOT / test).is_file(), test)

    def test_python_mutates_only_in_a_scratch_copy_and_never_links_a_dependency_into_it(self):
        command = mutate.scratch_copy("/repo", "/tmp/scratch")
        self.assertEqual(command[:2], ["rsync", "-a"])
        excluded = {command[i + 1] for i, word in enumerate(command) if word == "--exclude"}
        self.assertLessEqual({".git", "node_modules", ".venv"}, excluded)
        self.assertEqual(command[-2:], ["/repo/", "/tmp/scratch/"])

    def test_the_help_line_says_how_to_scope_it(self):
        line = next(x for x in (mutate.ROOT / "Makefile").read_text().splitlines() if x.startswith("mutate:"))
        for word in ("changed lines", "FILES=", "LINES=", "RUNNER="):
            self.assertIn(word, line)


if __name__ == "__main__":
    unittest.main()
