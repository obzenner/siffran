#!/usr/bin/env python3
"""The implementation manifest equals the closure of its declared roots, and the check refuses every difference."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_implementation_manifest", ROOT / "scripts" / "check_implementation_manifest.py")
manifest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manifest
SPEC.loader.exec_module(manifest)

EXEMPT = "load: (file) => import(pathToFileURL(file).href),"


class SeededTreeTests(unittest.TestCase):
    def check(self, files: dict[str, str], roots: tuple[str, ...], listed: tuple[str, ...]) -> list[str]:
        with tempfile.TemporaryDirectory() as temporary:
            plugin = Path(temporary)
            for name, text in files.items():
                (plugin / name).parent.mkdir(parents=True, exist_ok=True)
                (plugin / name).write_text(text, encoding="utf-8")
            return manifest.check(plugin, roots, listed)

    PACKAGE = {"pkg/__init__.py": "", "pkg/listed.py": "", "pkg/unlisted.py": "", "pkg/extra.py": ""}

    def test_a_module_a_root_imports_but_the_list_omits_is_refused_by_name(self):
        refusals = self.check({**self.PACKAGE, "root.py": "import pkg.listed\n\ndef late():\n    from pkg import unlisted\n"},
                              ("root.py",), ("pkg/__init__.py", "pkg/listed.py", "root.py"))
        self.assertEqual(refusals, ["unlisted: pkg/unlisted.py (reached from root.py:4)"])

    def test_a_typescript_import_is_followed_whatever_its_binding_list_holds(self):
        for text, line in (
            ('import {\n  // don\'t remove\n  x,\n} from "./extra.ts";\n', 4),
            ('import {\n  x, // a; b\n} from "./extra.ts";\n', 3),
            ('import"./extra.ts";\n', 1),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.check({"root.ts": text, "extra.ts": ""}, ("root.ts",), ("root.ts",)),
                                 [f"unlisted: extra.ts (reached from root.ts:{line})"])

    def test_a_listed_module_no_root_reaches_is_refused(self):
        refusals = self.check({**self.PACKAGE, "root.py": "import pkg.listed\n"},
                              ("root.py",), ("pkg/__init__.py", "pkg/extra.py", "pkg/listed.py", "root.py"))
        self.assertEqual(refusals, ["unreachable: pkg/extra.py is listed but no root reaches it"])

    def test_python_dynamic_loading_is_refused_outright(self):
        refused = "dynamic import: root.py:{} uses {}, which is refused outright"
        for text, line, name in (
            ("import importlib\n", 1, "importlib"),
            ("import importlib\nNAME = 'pkg.listed'\nimportlib.import_module(NAME)\n", 1, "importlib"),
            ("from importlib import import_module as load\nload('pkg.listed')\n", 1, "importlib"),
            ("from runpy import run_path\n", 1, "runpy"),
            ("import importlib.util as u\n", 1, "importlib.util"),
            ("from importlib import *\n", 1, "importlib"),
            ("NAME = 'pkg.listed'\n__import__(NAME)\n", 2, "__import__"),
            ("x = 1\n__import__('pkg.listed')\n", 2, "__import__"),
            ("__import__('runpy').run_path('x.py')\n", 1, "__import__"),
            ("import builtins\nbuiltins.__import__('pkg.listed')\n", 2, "__import__"),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.check({**self.PACKAGE, "root.py": text}, ("root.py",), ("root.py",)),
                                 [refused.format(line, name)])

    def test_a_dynamic_import_the_check_cannot_follow_is_refused(self):
        for name, text, refusal in (
            ("root.py", "from pkg import *\n", "star import: root.py:1 cannot be followed"),
            ("root.ts", "const spec = './listed.ts';\nexport const loaded = await import(spec);\n",
             "dynamic import: root.ts:2 has a non-literal target"),
            ("root.ts", "import { createRequire } from 'node:module';\n", "dynamic import: root.ts:1 uses `createRequire`"),
            ("root.ts", "export const loaded = await import /*c*/ (spec);\n",
             "dynamic import: root.ts:1 has a non-literal target"),
            ("root.ts", "// a require in prose is no import\nconst r = require;\nr('./listed.ts');\n",
             "dynamic import: root.ts:2 uses `require` outside a literal call"),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.check({**self.PACKAGE, name: text}, (name,), (name,)), [refusal])

    def test_an_import_that_resolves_to_no_file_is_refused(self):
        for name, text, prefix in (
            ("root.py", "import nowhere_in_this_tree\n", "unresolved import: root.py:1 imports nowhere_in_this_tree,"),
            ("root.py", "import pkg.nowhere\n", "unresolved import: root.py:1 imports pkg.nowhere,"),
            ("root.py", "from pkg import nowhere\n", "unresolved import: root.py:1 imports pkg.nowhere,"),
            ("root.ts", "import { x } from './nowhere.ts';\n", "unresolved import: root.ts:1 imports ./nowhere.ts"),
        ):
            with self.subTest(text=text):
                refusals = self.check({**self.PACKAGE, name: text}, (name,), (name, "pkg/__init__.py"))
                self.assertEqual(len(refusals), 1, refusals)
                self.assertTrue(refusals[0].startswith(prefix), refusals)

    def test_a_name_a_package_binds_is_not_a_submodule(self):
        files = {"pkg/__init__.py": "from .listed import thing\n", "pkg/listed.py": "thing = 1\n",
                 "root.py": "from pkg import thing\n"}
        self.assertEqual(self.check(files, ("root.py",), ("pkg/__init__.py", "pkg/listed.py", "root.py")), [])

    def test_a_root_that_names_no_file_is_refused(self):
        self.assertEqual(self.check({"root.py": ""}, ("root.py", "absent.py"), ("root.py",)),
                         ["missing root: absent.py"])

    def test_an_exemption_whose_line_has_gone_is_refused_as_stale(self):
        with mock.patch.dict(manifest.TS_DYNAMIC_EXEMPT, {("root.ts", EXEMPT): "seeded"}, clear=True):
            self.assertEqual(self.check({"root.ts": "export const x = 1;\n"}, ("root.ts",), ("root.ts",)),
                             [f"stale exemption: root.ts: {EXEMPT}"])

    def test_an_exempt_line_must_occur_exactly_once(self):
        with mock.patch.dict(manifest.TS_DYNAMIC_EXEMPT, {("root.ts", EXEMPT): "seeded"}, clear=True):
            self.assertEqual(self.check({"root.ts": f"  {EXEMPT}\n{EXEMPT}\n"}, ("root.ts",), ("root.ts",)),
                             [f"duplicated exemption: root.ts: {EXEMPT} occurs 2 times"])

    def rule(self, listed: str) -> str:
        return f"`b/c.py` is outside.\n{manifest.ROOTS_START}\n{listed}\n{manifest.ROOTS_END}\n`d.py` too.\n"

    def test_a_declared_root_missing_from_the_written_roots_list_is_refused(self):
        self.assertEqual(manifest.roots_in_rule(("a.py", "b/c.py"), self.rule("- `a.py`")),
                         ["undocumented root: b/c.py is not in the roots list of doc/testing.md"])

    def test_a_path_in_the_written_roots_list_that_is_not_declared_is_refused(self):
        self.assertEqual(manifest.roots_in_rule(("a.py",), self.rule("- `a.py` and `d.py`")),
                         ["undeclared root: d.py is in the roots list of doc/testing.md but not in IMPLEMENTATION_ROOTS"])


class RealTreeTests(unittest.TestCase):
    def test_the_manifest_equals_the_closure_of_its_roots_and_the_rule_names_every_root(self):
        sys.path.insert(0, str(manifest.PLUGIN))
        from adapters.sandbox import common
        self.assertEqual(
            manifest.check(manifest.PLUGIN, common.IMPLEMENTATION_ROOTS, common.IMPLEMENTATION_SOURCE_PATHS), [])
        self.assertEqual(manifest.roots_in_rule(common.IMPLEMENTATION_ROOTS, manifest.RULE.read_text(encoding="utf-8")),
                         [])


if __name__ == "__main__":
    unittest.main()
