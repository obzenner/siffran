"""validate_skills: the spec rules, each with a negative control, and the repository itself."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate_skills as v  # noqa: E402

GOOD = "---\nname: demo\ndescription: Does a thing.\n---\n# Demo\n"


class Tree:
    def __init__(self, test: unittest.TestCase):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        canonical = self.root / ".agents/skills/native-qualification"
        canonical.mkdir(parents=True)
        (canonical / "SKILL.md").write_text(GOOD.replace("demo", "native-qualification"))
        (self.root / ".claude/skills").mkdir(parents=True)
        os.symlink("../../.agents/skills/native-qualification", self.root / ".claude/skills/native-qualification")

    def skill(self, text: str, directory: str = "demo", extra: dict[str, str] | None = None) -> None:
        base = self.root / "plugins/p/skills" / directory
        base.mkdir(parents=True, exist_ok=True)
        (base / "SKILL.md").write_text(text)
        for name, body in (extra or {}).items():
            (base / name).write_text(body)


class SkillRules(unittest.TestCase):
    def problems(self, text: str, directory: str = "demo", extra=None) -> list[str]:
        tree = Tree(self)
        tree.skill(text, directory, extra)
        return v.validate(tree.root)

    def test_a_good_tree_has_no_problems(self):
        self.assertEqual(self.problems(GOOD), [])

    def test_name_format_and_length(self):
        for bad in ("Demo", "-demo", "demo-", "de--mo", "de_mo", "x" * 65, ""):
            text = GOOD.replace("name: demo", f"name: {bad}")
            self.assertTrue(any("name" in p for p in self.problems(text, directory=bad or "demo")), bad)

    def test_name_must_equal_the_directory(self):
        self.assertTrue(any("must equal the directory" in p for p in self.problems(GOOD, directory="other")))

    def test_description_length(self):
        for bad in ("", "d" * 1025):
            text = GOOD.replace("description: Does a thing.", f"description: {bad}")
            self.assertTrue(any("description" in p for p in self.problems(text)), len(bad))

    def test_compatibility_length(self):
        text = GOOD.replace("---\n#", f"compatibility: {'c' * 501}\n---\n#")
        self.assertTrue(any("compatibility" in p for p in self.problems(text)))

    def test_unknown_frontmatter_keys(self):
        text = GOOD.replace("---\n#", "disable-model-invocation: true\n---\n#")
        self.assertTrue(any("unknown frontmatter key" in p for p in self.problems(text)))

    def test_a_missing_or_unclosed_frontmatter(self):
        self.assertTrue(self.problems("# no frontmatter\n"))
        self.assertTrue(self.problems("---\nname: demo\ndescription: x\n"))

    def test_a_broken_relative_link(self):
        self.assertTrue(any("does not resolve" in p for p in self.problems(GOOD + "[x](references/gone.md)\n")))
        self.assertEqual(self.problems(GOOD + "[x](a.md#h) [u](https://e.test/x) [h](#top)\n", extra={"a.md": "#h\n"}), [])

    def test_a_broken_link_in_a_reference(self):
        tree = Tree(self)
        (tree.root / ".agents/skills/native-qualification/ref.md").write_text("[x](nowhere.md)\n")
        self.assertTrue(any("ref.md" in p for p in v.validate(tree.root)))

    def test_the_claude_entry_must_be_a_relative_symlink_to_the_canonical_directory(self):
        tree = Tree(self)
        entry = tree.root / ".claude/skills/native-qualification"
        entry.unlink()
        self.assertTrue(any("must be a symlink" in p for p in v.validate(tree.root)))
        os.symlink(str(tree.root / ".agents/skills/native-qualification"), entry)  # absolute
        self.assertTrue(any("relative symlink" in p for p in v.validate(tree.root)))
        entry.unlink()
        (tree.root / ".agents/skills/other").mkdir()
        os.symlink("../../.agents/skills/other", entry)  # elsewhere
        self.assertTrue(any("relative symlink" in p for p in v.validate(tree.root)))

    def test_the_repository_itself_is_valid(self):
        self.assertEqual(v.validate(), [])


if __name__ == "__main__":
    unittest.main()
