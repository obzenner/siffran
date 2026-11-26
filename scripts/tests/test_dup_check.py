"""``make dup-check`` (``scripts/dup_check.py``): which clones are new on the changed lines, and what fails or warns.

The git handling, the pairing of occurrences and the verdicts run on synthetic git repositories with a recorded detector
report (the shape jscpd prints: a pair of occurrences per line of the report, the first file the representative of every
other copy); one integration test runs the real detector on the scenarios that matter most and is skipped, saying so,
only where ``make dup-install`` has not run."""
from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dup_check  # noqa: E402
import mutate  # noqa: E402

# main() reads BASE= and TOKENS= from the environment, as make passes them: these tests give their own
for _name in ("BASE", "TOKENS"):
    os.environ.pop(_name, None)

MAKEFILE = ".PHONY: regen check-static\nregen: ## regenerate\n\t@true\n"
GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
needs_jscpd = unittest.skipUnless(dup_check.JSCPD.is_file(), "the pinned clone detector is not installed: run make dup-install")
GENERATED = frozenset({"plugins/p/generated.py"})


def body(tag: str = "v", lines: int = 14) -> str:
    """A function body of more than the least clone size, the same wherever the same tag is used."""
    return "".join(f"    {tag}_{i} = compute(argument_{i}, {i}) + offset_{tag}\n" for i in range(lines))


def module(name: str, text: str, other: str = "") -> str:
    return f"def {name}(offset):\n{text}    return offset\n{other}"


def squeezed(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines()]


def recorded_report(scanned: Path, tokens: int, block: str = body()) -> dict:
    """The report jscpd gives of copies of ``block`` (whitespace aside): the first file by name holds the representative
    occurrence and is paired with each other copy, so two copies beside it are not paired with each other."""
    want, found = squeezed(block), []
    for path in sorted(p for p in scanned.rglob("*") if p.is_file()):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i in range(len(lines) - len(want) + 1):
            if squeezed("\n".join(lines[i:i + len(want)])) == want:
                found.append((path, i + 1, i + len(want), "\n".join(lines[i:i + len(want)])))
                break
    side = lambda f: {"name": str(f[0]), "start": f[1], "end": f[2]}  # noqa: E731
    return {"duplicates": [{"firstFile": side(found[0]), "secondFile": side(other), "fragment": found[0][3], "lines": len(want),
                            "tokens": 100, "kind": "exact", "format": "python"} for other in found[1:]]}


def shaped(found: list[str]) -> list[str]:
    """Clones as printed, less the line numbers (the real detector's range takes the function around the shared block)."""
    return [re.sub(r"\(\d+ lines\)", "(N lines)", re.sub(r":\d+-\d+", ":S-E", line)) for line in found]


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

    def write(self, path: str, text: str | None) -> None:
        if text is None:
            (self.root / path).unlink()
            return
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_text(text, encoding="utf-8")

    def commit(self) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        return self.git("rev-parse", "HEAD").strip()


def clone(first: tuple, second: tuple, digest: str = "d", other: str | None = None) -> dup_check.Clone:
    return dup_check.Clone(dup_check.Occurrence(*first, digest), dup_check.Occurrence(*second, other or digest))


class ClonePairTests(unittest.TestCase):
    CHANGED = {"a.py": [(5, 6)]}

    def test_a_clone_is_new_when_an_occurrence_overlaps_a_changed_line_and_its_pair_is_not_at_base(self):
        for head in (clone(("a.py", 1, 8), ("b.py", 1, 8)), clone(("b.py", 1, 8), ("a.py", 1, 8))):
            self.assertEqual(dup_check.new_clones([head], [], self.CHANGED, {}), [head])

    def test_a_clone_outside_every_changed_line_is_not_new(self):
        self.assertEqual(dup_check.new_clones([clone(("a.py", 1, 4), ("b.py", 1, 4))], [], self.CHANGED, {}), [])
        self.assertEqual(dup_check.new_clones([clone(("a.py", 7, 9), ("b.py", 1, 4))], [], self.CHANGED, {}), [])

    def test_the_occurrences_of_one_pair_are_filtered_by_the_changed_lines_in_whatever_order_they_come(self):
        on_change, elsewhere = clone(("a.py", 3, 8), ("b.py", 1, 6)), clone(("a.py", 20, 25), ("b.py", 20, 25))
        for head in ([on_change, elsewhere], [elsewhere, on_change]):
            self.assertEqual(dup_check.new_clones(head, [], self.CHANGED, {}), [on_change])

    def test_the_same_pair_at_base_is_not_new_wherever_its_lines_moved_but_another_fragment_is(self):
        old = clone(("a.py", 20, 30), ("b.py", 20, 30))
        self.assertEqual(dup_check.new_clones([clone(("a.py", 1, 8), ("b.py", 1, 8))], [old], self.CHANGED, {}), [])
        for grown in (clone(("a.py", 1, 8), ("b.py", 1, 8), digest="longer"), clone(("a.py", 1, 8), ("b.py", 1, 8), other="changed")):
            self.assertEqual(dup_check.new_clones([grown], [old], self.CHANGED, {}), [grown])

    def test_a_renamed_file_is_compared_under_its_name_at_base(self):
        old = clone(("a.py", 20, 30), ("b.py", 20, 30))
        self.assertEqual(dup_check.new_clones([clone(("a.py", 1, 8), ("c.py", 1, 8))], [old], self.CHANGED, {"c.py": "b.py"}), [])

    def test_a_clone_involving_test_code_warns_and_only_production_pairs_fail(self):
        production, mixed, tests = (clone(("lib/a.py", 1, 5), ("lib/b.py", 1, 5)), clone(("lib/a.py", 1, 5), ("tests/test_a.py", 1, 5)),
                                    clone(("tests/test_a.py", 1, 5), ("tests/test_b.py", 1, 5)))
        self.assertEqual(dup_check.verdict([production, mixed, tests]), ([production], [mixed, tests]))

    def test_a_test_is_named_by_the_one_rule_make_mutate_uses_wherever_it_lives(self):
        for path in ("plugins/x/tests/a.py", "scripts/test_a.py", "plugins/x/src/audit.test.ts", "plugins/x/ui/a.test.mjs"):
            with self.subTest(path=path):
                self.assertTrue(dup_check.is_test(path))
                self.assertTrue(mutate.TEST_PATH.search(path))
        self.assertFalse(dup_check.is_test("plugins/x/src/audit.ts"))

    def test_only_the_suffixes_the_detector_reads_are_sources_and_a_generator_s_output_is_not_authored(self):
        for path, expected in {"a.py": True, "a.ts": True, "a.js": True, "a.mjs": True, "a.cjs": True, "a.tsx": False, "a.jsx": False,
                               "a.md": False, "x/vendor/a.py": False, "x/dist/a.js": False, "x/fixtures/a.py": False,
                               "x/author-view-golden/a.py": False, "x/node_modules/a.js": False, "plugins/p/generated.py": False}.items():
            with self.subTest(path=path):
                self.assertEqual(dup_check.authored(path, GENERATED), expected)

    def test_the_detector_s_pairs_are_grouped_so_every_two_copies_are_a_clone_by_the_text_each_has(self):
        def side(path):
            return {"name": f"/scan/{path}", "start": 1, "end": 2}
        texts = {"a.py": "def a():\n  x  y\n", "b.py": "def b():\n x y\n", "c.py": "def a():\n  x y\n"}
        report = {"duplicates": [{"firstFile": side("a.py"), "secondFile": side(f), "fragment": "x", "lines": 2} for f in ("b.py", "c.py")]}
        found = dup_check.clones(report, Path("/scan"), read=lambda path: texts[path.name])
        self.assertEqual(sorted(tuple(p for p, _ in c.pair()) for c in found), [("a.py", "b.py"), ("a.py", "c.py"), ("b.py", "c.py")])
        digests = {o.path: o.digest for c in found for o in (c.first, c.second)}
        self.assertEqual((digests["a.py"], digests["b.py"] == digests["a.py"]), (digests["c.py"], False))

    def test_git_s_name_records_are_split_by_nul_so_a_name_may_hold_any_other_character(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Repository(self)
            names = ["plugins/p/a b.py", "plugins/p/new\nline.py", 'plugins/p/q"t.py', "plugins/p/caf\u00e9.py"]
            for name in names:
                repo.write(name, "x = 1\n")
            base = repo.commit()
            repo.git("mv", names[1], "plugins/p/moved\nline.py")
            repo.git("mv", names[0], "plugins/p/c d.py")
            repo.commit()
            self.assertEqual(dup_check.renames(repo.root, base), {"plugins/p/moved\nline.py": names[1], "plugins/p/c d.py": names[0]})
            snap = Path(folder)
            dup_check.snapshot(repo.root, base, snap, frozenset())
            self.assertEqual(sorted(p.relative_to(snap).as_posix() for p in snap.rglob("*.py")), sorted(names))

    def test_a_base_that_is_not_an_ancestor_of_head_is_refused(self):
        repo = Repository(self)
        repo.write("plugins/p/a.py", "x = 1\n")
        first = repo.commit()
        repo.git("checkout", "-q", "-b", "other")
        repo.write("plugins/p/b.py", "x = 2\n")
        other = repo.commit()
        repo.git("checkout", "-q", "main")
        repo.write("plugins/p/c.py", "x = 3\n")
        repo.commit()
        dup_check.require_ancestor(repo.root, first)
        with self.assertRaises(SystemExit) as refused:
            dup_check.check(repo.root, other, 60, frozenset(), lambda scanned, tokens: {"duplicates": []})
        self.assertIn("is not an ancestor of HEAD", str(refused.exception))


class GeneratedOutputTests(unittest.TestCase):
    def test_the_generated_sources_are_those_a_generator_declares_and_every_header_claiming_one_is_declared(self):
        declared = dup_check.generated_outputs()
        self.assertTrue(declared and all((dup_check.ROOT / path).is_file() for path in declared))
        tracked = subprocess.run(["git", "ls-files", "-z", *dup_check.SCAN], cwd=dup_check.ROOT, capture_output=True, text=True, check=True)
        claiming = {path for path in tracked.stdout.split("\0")[:-1] if dup_check.authored(path, frozenset())
                    and re.search(r"Generated by", "\n".join((dup_check.ROOT / path).read_text(encoding="utf-8").splitlines()[:20]))}
        self.assertEqual(claiming - declared, set(), "a source says it is generated: its generator must declare it")


class ScenarioTests(unittest.TestCase):
    """Synthetic repositories committed at BASE and at HEAD, the detector report recorded."""

    report_of = staticmethod(recorded_report)

    def setUp(self):
        self.repo = Repository(self)

    def verdict(self, base_files: dict, head_files: dict, tokens: int = dup_check.DEFAULT_TOKENS, repo=None):
        repo = repo or self.repo
        for path, text in base_files.items():
            repo.write(path, text)
        base = repo.commit()
        for path, text in head_files.items():
            repo.write(path, text)
        repo.commit()
        failures, warnings = dup_check.check(repo.root, base, tokens, GENERATED, self.report_of)
        return [str(c) for c in failures], [str(c) for c in warnings]

    def test_a_new_production_clone_fails_and_is_printed_with_its_files_lines_and_size(self):
        failures, warnings = self.verdict({"plugins/p/a.py": module("one", body())}, {"plugins/p/b.py": module("two", body())})
        self.assertEqual((shaped(failures), warnings), (["plugins/p/a.py:S-E == plugins/p/b.py:S-E (N lines)"], []))
        self.assertRegex(failures[0], r"^plugins/p/a\.py:\d+-\d+ == plugins/p/b\.py:\d+-\d+ \(\d+ lines\)$")

    def test_a_new_clone_in_test_code_or_between_test_and_production_code_warns_and_does_not_fail(self):
        failures, warnings = self.verdict({"plugins/p/tests/test_a.py": module("one", body())}, {"plugins/p/tests/test_b.py": module("two", body())})
        self.assertEqual((failures, len(warnings)), ([], 1))
        failures, warnings = self.verdict({"plugins/p/a.py": module("one", body())}, {"plugins/p/tests/test_a.py": module("two", body())},
                                          repo=Repository(self))
        self.assertEqual((failures, len(warnings)), ([], 1))

    def test_a_clone_already_at_base_is_ignored_when_its_files_change_elsewhere_or_only_in_whitespace(self):
        files = {"plugins/p/a.py": module("one", body()), "plugins/p/b.py": module("two", body())}
        self.assertEqual(self.verdict(files, {"plugins/p/a.py": module("one", body(), "\nEXTRA = 1\n")}), ([], []))
        self.assertEqual(self.verdict(files, {"plugins/p/a.py": module("one", body().replace(" = ", "  =  "))}, repo=Repository(self)), ([], []))

    def test_a_pre_existing_clone_follows_a_renamed_file_whatever_its_name(self):
        for name in ("c.py", "c d.py", "caf\u00e9.py", 'q"t.py'):
            with self.subTest(name=name):
                repo = Repository(self)
                for path, text in {"plugins/p/a.py": module("one", body()), "plugins/p/b.py": module("two", body())}.items():
                    repo.write(path, text)
                base = repo.commit()
                repo.git("mv", "plugins/p/b.py", f"plugins/p/{name}")
                repo.write(f"plugins/p/{name}", module("two", body().replace(" = ", "  =  ", 2)))
                repo.commit()
                self.assertEqual(dup_check.check(repo.root, base, 60, GENERATED, self.report_of), ([], []))

    def test_a_new_clone_is_reported_under_file_names_with_spaces_quotes_and_non_ascii_characters(self):
        failures, _ = self.verdict({"plugins/p/a b.py": module("one", body())}, {"plugins/p/caf\u00e9 \"q\".py": module("two", body())})
        self.assertEqual(shaped(failures), ['plugins/p/a b.py:S-E == plugins/p/caf\u00e9 "q".py:S-E (N lines)'])

    def test_a_clone_that_appears_without_touching_a_changed_line_is_ignored(self):
        self.repo.write("plugins/p/a.py", module("one", body()))
        self.repo.write("plugins/p/fixtures/b.py", module("two", body()))
        base = self.repo.commit()
        self.repo.git("mv", "plugins/p/fixtures/b.py", "plugins/p/b.py")
        self.repo.commit()
        self.assertEqual(dup_check.check(self.repo.root, base, 60, GENERATED, self.report_of), ([], []))

    def test_a_clone_into_a_file_that_is_not_hand_written_is_ignored(self):
        for path in ("plugins/p/vendor/b.py", "plugins/p/fixtures/b.py", "plugins/p/author-view-golden/b.py", "plugins/p/node_modules/b.py",
                     "plugins/p/dist/b.py", "plugins/p/generated.py"):
            with self.subTest(path=path):
                self.assertEqual(self.verdict({"plugins/p/a.py": module("one", body())}, {path: module("two", body())}, repo=Repository(self)),
                                 ([], []))

    def test_a_header_saying_a_file_is_generated_does_not_exempt_it(self):
        failures, _ = self.verdict({"plugins/p/a.py": module("one", body())},
                                   {"plugins/p/b.py": '"""Generated by ``make check-static``."""\n' + module("two", body())})
        self.assertEqual(len(failures), 1)

    def test_with_three_copies_deleting_the_representative_leaves_the_pair_of_the_others_old(self):
        files = {f"plugins/p/{n}.py": module(n, body()) for n in "abc"}
        self.assertEqual(self.verdict(files, {"plugins/p/a.py": None, "plugins/p/b.py": module("b", body().replace(" = ", "  =  "))}), ([], []))

    def test_a_third_copy_pairs_with_each_of_the_others_whichever_the_detector_took_as_its_representative(self):
        # the test file sorts first, so the detector pairs it with both production copies and reports no pair between them
        failures, warnings = self.verdict({"plugins/p/tests/test_a.py": module("a", body()), "plugins/p/zz_b.py": module("b", body())},
                                          {"plugins/p/zz_c.py": module("c", body())})
        self.assertEqual(shaped(failures), ["plugins/p/zz_b.py:S-E == plugins/p/zz_c.py:S-E (N lines)"])
        self.assertEqual(shaped(warnings), ["plugins/p/tests/test_a.py:S-E == plugins/p/zz_c.py:S-E (N lines)"])

    def test_the_least_clone_size_reaches_the_detector(self):
        sizes = []
        self.report_of = lambda scanned, tokens: sizes.append(tokens) or recorded_report(scanned, tokens)
        self.verdict({"plugins/p/a.py": module("one", body())}, {"plugins/p/b.py": module("two", body())}, tokens=17)
        self.assertEqual(sizes, [17, 17])

    def test_main_prints_each_failure_and_exits_one_and_exits_zero_with_none(self):
        self.repo.write("plugins/p/a.py", module("one", body()))
        base = self.repo.commit()
        self.repo.write("plugins/p/b.py", module("two", body()))
        self.repo.commit()
        for ref, code, said in ((base, 1, "FAIL new production clones:\n  plugins/p/"), ("HEAD", 0, "no new clone")):
            with self.subTest(base=ref), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(dup_check.main(["--base", ref], root=self.repo.root, report_of=self.report_of, generated=GENERATED), code)
                self.assertIn(said, out.getvalue())


@needs_jscpd
class DetectorIntegrationTests(ScenarioTests):
    """The scenarios that depend on what the real detector reports."""

    report_of = staticmethod(lambda scanned, tokens: dup_check.jscpd_report(scanned, tokens, dup_check.JSCPD))

    def test_the_real_detector_s_least_clone_size_decides_what_is_a_clone(self):
        base, head = {"plugins/p/a.py": module("one", body(lines=4))}, {"plugins/p/b.py": module("two", body(lines=4))}
        self.assertEqual(len(self.verdict(base, head, tokens=10)[0]), 1)
        self.assertEqual(self.verdict(base, head, tokens=500, repo=Repository(self)), ([], []))


KEPT = {"test_a_new_production_clone_fails_and_is_printed_with_its_files_lines_and_size",
        "test_with_three_copies_deleting_the_representative_leaves_the_pair_of_the_others_old",
        "test_a_third_copy_pairs_with_each_of_the_others_whichever_the_detector_took_as_its_representative",
        "test_a_pre_existing_clone_follows_a_renamed_file_whatever_its_name",
        "test_the_real_detector_s_least_clone_size_decides_what_is_a_clone"}
for _name in [n for n in dir(DetectorIntegrationTests) if n.startswith("test_") and n not in KEPT]:
    setattr(DetectorIntegrationTests, _name, None)


if __name__ == "__main__":
    unittest.main()
