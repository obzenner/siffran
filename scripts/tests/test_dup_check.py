"""``make dup-check`` (``scripts/dup_check.py``): which clones are new on the changed lines, and what fails or warns.

The clone-pair logic is tested on hand-made clones; each scenario runs the pinned jscpd on a small synthetic git
repository (committed BASE and HEAD) and is skipped, saying so, only where ``make dup-install`` has not run."""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dup_check  # noqa: E402

# main() reads BASE= and TOKENS= from the environment, as make passes them: these tests give their own
for _name in ("BASE", "TOKENS"):
    os.environ.pop(_name, None)

MAKEFILE = ".PHONY: regen\nregen: ## regenerate\n\t@true\n"
GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
needs_jscpd = unittest.skipUnless(dup_check.JSCPD.is_file(), "the pinned clone detector is not installed: run make dup-install")


def body(tag: str = "v", lines: int = 14) -> str:
    """A function body of more than the least clone size, the same wherever the same tag is used."""
    return "".join(f"    {tag}_{i} = compute(argument_{i}, {i}) + offset_{tag}\n" for i in range(lines))


def module(name: str, text: str, other: str = "") -> str:
    return f"def {name}(offset):\n{text}    return offset\n{other}"


class Repository:
    """A scratch git repository: ``commit`` records the working files and returns the commit."""

    def __init__(self, test: unittest.TestCase):
        folder = tempfile.TemporaryDirectory()
        test.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.git("init", "-q", "-b", "main")
        self.write("Makefile", MAKEFILE)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, env=GIT_ENV, capture_output=True, text=True, check=True).stdout

    def write(self, path: str, text: str) -> None:
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_text(text, encoding="utf-8")

    def commit(self) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        return self.git("rev-parse", "HEAD").strip()

    def verdict(self, base: str, tokens: int = dup_check.DEFAULT_TOKENS):
        failures, warnings = dup_check.check(self.root, base, tokens)
        return [str(c) for c in failures], [str(c) for c in warnings]


def clone(first: tuple, second: tuple, digest: str = "d") -> dup_check.Clone:
    return dup_check.Clone(first, second, 10, digest)


class ClonePairTests(unittest.TestCase):
    CHANGED = {"a.py": [(5, 6)]}

    def test_a_clone_is_new_when_a_fragment_overlaps_a_changed_line_and_its_pair_is_not_at_base(self):
        head = clone(("a.py", 1, 8), ("b.py", 1, 8))
        self.assertEqual(dup_check.new_clones([head], [], self.CHANGED, {}), [head])
        self.assertEqual(dup_check.new_clones([clone(("b.py", 1, 8), ("a.py", 1, 8))], [], self.CHANGED, {}),
                         [clone(("b.py", 1, 8), ("a.py", 1, 8))])

    def test_a_clone_outside_every_changed_line_is_not_new(self):
        self.assertEqual(dup_check.new_clones([clone(("a.py", 1, 4), ("b.py", 1, 4))], [], self.CHANGED, {}), [])
        self.assertEqual(dup_check.new_clones([clone(("a.py", 7, 9), ("b.py", 1, 4))], [], self.CHANGED, {}), [])

    def test_the_same_pair_at_base_is_not_new_wherever_its_lines_moved_but_another_fragment_is(self):
        old = clone(("a.py", 20, 30), ("b.py", 20, 30))
        self.assertEqual(dup_check.new_clones([clone(("a.py", 1, 8), ("b.py", 1, 8))], [old], self.CHANGED, {}), [])
        grown = clone(("a.py", 1, 8), ("b.py", 1, 8), digest="longer")
        self.assertEqual(dup_check.new_clones([grown], [old], self.CHANGED, {}), [grown])

    def test_a_renamed_file_is_compared_under_its_name_at_base(self):
        old = clone(("a.py", 20, 30), ("b.py", 20, 30))
        self.assertEqual(dup_check.new_clones([clone(("a.py", 1, 8), ("c.py", 1, 8))], [old], self.CHANGED, {"c.py": "b.py"}), [])

    def test_a_clone_involving_test_code_warns_and_only_production_pairs_fail(self):
        production, mixed, tests = (clone(("lib/a.py", 1, 5), ("lib/b.py", 1, 5)), clone(("lib/a.py", 1, 5), ("tests/test_a.py", 1, 5)),
                                    clone(("tests/test_a.py", 1, 5), ("tests/test_b.py", 1, 5)))
        self.assertEqual(dup_check.verdict([production, mixed, tests]), ([production], [mixed, tests]))

    def test_a_file_is_generated_only_by_a_header_naming_a_target_the_makefile_declares(self):
        targets = dup_check.make_targets(MAKEFILE + "check-static: lint docs\n\tx\nVAR := 1\n")
        self.assertEqual(targets, {".PHONY", "regen", "check-static"})
        self.assertTrue(dup_check.generated('"""Generated by ``make regen`` from X — do not edit."""\n', targets))
        self.assertFalse(dup_check.generated('"""Generated by ``make nothing`` from X."""\n', targets))
        self.assertFalse(dup_check.generated("\n" * 30 + "# Generated by `make regen`\n", targets))


@needs_jscpd
class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(self)

    def clone_at_head(self, base_files: dict, head_files: dict, tokens: int = dup_check.DEFAULT_TOKENS):
        for path, text in base_files.items():
            self.repo.write(path, text)
        base = self.repo.commit()
        for path, text in head_files.items():
            self.repo.write(path, text)
        self.repo.commit()
        return self.repo.verdict(base, tokens)

    def test_a_new_production_clone_fails_and_is_printed_with_its_files_lines_and_size(self):
        failures, warnings = self.clone_at_head({"plugins/p/a.py": module("one", body())}, {"plugins/p/b.py": module("two", body())})
        self.assertEqual((len(failures), warnings), (1, []))
        self.assertRegex(failures[0], r"^plugins/p/[ab]\.py:\d+-\d+ == plugins/p/[ab]\.py:\d+-\d+ \(\d+ lines\)$")

    def test_a_new_clone_in_test_code_warns_and_does_not_fail(self):
        failures, warnings = self.clone_at_head({"plugins/p/tests/test_a.py": module("one", body())},
                                                {"plugins/p/tests/test_b.py": module("two", body())})
        self.assertEqual((failures, len(warnings)), ([], 1))

    def test_a_clone_between_production_and_test_code_warns(self):
        failures, warnings = self.clone_at_head({"plugins/p/a.py": module("one", body())}, {"plugins/p/tests/test_a.py": module("two", body())})
        self.assertEqual((failures, len(warnings)), ([], 1))

    def test_a_clone_already_at_base_is_ignored_when_its_files_change_elsewhere_or_only_in_whitespace(self):
        files = {"plugins/p/a.py": module("one", body()), "plugins/p/b.py": module("two", body())}
        elsewhere = {"plugins/p/a.py": module("one", body(), "\nEXTRA = 1\n")}
        self.assertEqual(self.clone_at_head(files, elsewhere), ([], []))
        self.repo = Repository(self)
        reindented = {"plugins/p/a.py": module("one", body().replace(" = ", "  =  "))}
        self.assertEqual(self.clone_at_head(files, reindented), ([], []))

    def test_a_pre_existing_clone_follows_a_renamed_file(self):
        files = {"plugins/p/a.py": module("one", body()), "plugins/p/b.py": module("two", body())}
        for path, text in files.items():
            self.repo.write(path, text)
        base = self.repo.commit()
        self.repo.git("mv", "plugins/p/b.py", "plugins/p/c.py")
        self.repo.write("plugins/p/c.py", module("two", body().replace(" = ", "  =  ", 2)))
        self.repo.commit()
        self.assertEqual(self.repo.verdict(base), ([], []))

    def test_a_clone_that_appears_without_touching_a_changed_line_is_ignored(self):
        for path, text in {"plugins/p/a.py": module("one", body()), "plugins/p/fixtures/b.py": module("two", body())}.items():
            self.repo.write(path, text)
        base = self.repo.commit()
        self.repo.git("mv", "plugins/p/fixtures/b.py", "plugins/p/b.py")
        self.repo.commit()
        self.assertEqual(self.repo.verdict(base), ([], []))

    def test_a_clone_into_a_file_that_is_not_hand_written_is_ignored(self):
        for path, header in {"plugins/p/vendor/b.py": "", "plugins/p/fixtures/b.py": "", "plugins/p/author-view-golden/b.py": "",
                             "plugins/p/node_modules/b.py": "", "plugins/p/dist/b.py": "", "plugins/p/b.py": '"""Generated by ``make regen``."""\n'}.items():
            with self.subTest(path=path):
                self.repo = Repository(self)
                self.assertEqual(self.clone_at_head({"plugins/p/a.py": module("one", body())}, {path: header + module("two", body())}),
                                 ([], []))

    def test_a_header_naming_no_makefile_target_does_not_exempt_a_file(self):
        failures, _ = self.clone_at_head({"plugins/p/a.py": module("one", body())},
                                         {"plugins/p/b.py": '"""Generated by ``make absent``."""\n' + module("two", body())})
        self.assertEqual(len(failures), 1)

    def test_the_least_clone_size_is_the_tokens_given(self):
        base, head = {"plugins/p/a.py": module("one", body(lines=4))}, {"plugins/p/b.py": module("two", body(lines=4))}
        self.assertEqual(len(self.clone_at_head(base, head, tokens=10)[0]), 1)
        self.repo = Repository(self)
        self.assertEqual(self.clone_at_head(base, head, tokens=500), ([], []))

    def test_main_prints_each_failure_and_exits_one_and_exits_zero_with_none(self):
        self.repo.write("plugins/p/a.py", module("one", body()))
        base = self.repo.commit()
        self.repo.write("plugins/p/b.py", module("two", body()))
        self.repo.commit()
        for ref, code, said in ((base, 1, "FAIL new production clones:\n  plugins/p/"), ("HEAD", 0, "no new clone")):
            with self.subTest(base=ref), mock.patch.object(dup_check, "ROOT", self.repo.root), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(dup_check.main(["--base", ref]), code)
                self.assertIn(said, out.getvalue())


if __name__ == "__main__":
    unittest.main()
