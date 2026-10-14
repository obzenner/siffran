#!/usr/bin/env python3
"""validate_licence.py passes on the real tree and fails on each kind of break (negative controls)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate_licence as vl  # noqa: E402


def build(root: Path) -> None:
    (root / "LICENSE").write_text(vl.REQUIRED_NOTICE + "\n\nbody\n", encoding="utf-8")
    for rel in ("package.json", "plugins/methodologist/package.json", "plugins/a/.claude-plugin/plugin.json"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"license": vl.LICENSE_REF}), encoding="utf-8")
    tw = root / vl.TAILWIND
    tw.parent.mkdir(parents=True, exist_ok=True)
    tw.write_text("MIT", encoding="utf-8")
    for name in ("fonts", "lucide"):
        (root / vl.VENDOR / name).mkdir(parents=True)
        (root / vl.VENDOR / name / "LICENSE").write_text("text", encoding="utf-8")


class ValidateLicence(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        build(self.tmp)

    def test_real_tree_is_clean(self) -> None:
        self.assertEqual(vl.check(vl.ROOT), [])

    def test_fixture_is_clean(self) -> None:
        self.assertEqual(vl.check(self.tmp), [])

    def test_missing_license(self) -> None:
        (self.tmp / "LICENSE").unlink()
        self.assertEqual(vl.check(self.tmp), ["LICENSE is missing"])

    def test_wrong_notice(self) -> None:
        (self.tmp / "LICENSE").write_text("# PolyForm\n" + vl.REQUIRED_NOTICE, encoding="utf-8")
        self.assertTrue(any("required notice" in p for p in vl.check(self.tmp)))

    def test_wrong_or_absent_manifest_license(self) -> None:
        (self.tmp / "package.json").write_text(json.dumps({"license": "MIT"}), encoding="utf-8")
        (self.tmp / "plugins/a/.claude-plugin/plugin.json").write_text("{}", encoding="utf-8")
        (self.tmp / "plugins/methodologist/package.json").write_text("not json", encoding="utf-8")
        problems = vl.check(self.tmp)
        self.assertEqual(len(problems), 3)
        self.assertTrue(any("package.json: license is 'MIT'" in p for p in problems))

    def test_extra_package_json_saying_mit(self) -> None:
        extra = self.tmp / "plugins/b/package.json"
        extra.parent.mkdir(parents=True)
        extra.write_text(json.dumps({"license": "MIT"}), encoding="utf-8")
        self.assertEqual(len(vl.check(self.tmp)), 1)
        self.assertIn("plugins/b/package.json", vl.check(self.tmp)[0])

    def test_missing_tailwind_licence(self) -> None:
        (self.tmp / vl.TAILWIND).unlink()
        self.assertEqual(vl.check(self.tmp), [f"{vl.TAILWIND} is missing"])

    def test_vendor_dir_without_license(self) -> None:
        (self.tmp / vl.VENDOR / "fonts" / "LICENSE").unlink()
        self.assertEqual(vl.check(self.tmp), [f"{vl.VENDOR}/fonts has no LICENSE"])


if __name__ == "__main__":
    unittest.main()
