#!/usr/bin/env python3
"""The Make entry point must select real tests and bound asynchronous waits."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate_pi_adapter as validator


class PiValidatorTests(unittest.TestCase):
    def test_conflicting_modes_and_retired_live_flag_fail_instead_of_skipping(self):
        import validate_codex_adapter as codex
        with contextlib.redirect_stderr(io.StringIO()):
            for args in (["--package-only", "--test-file", "one.test.ts"],
                         ["--typecheck-only", "--test-file", "one.test.ts"],
                         ["--package-only", "--typecheck-only"]):
                with self.assertRaises(SystemExit) as failed:
                    validator.main(args)
                self.assertEqual(failed.exception.code, 2)
            with patch.object(sys, "argv", ["validate_codex_adapter.py", "--live"]), \
                    patch.object(codex, "static_validate") as validate:
                with self.assertRaises(SystemExit) as failed:
                    codex.main()
                self.assertEqual(failed.exception.code, 2)
                validate.assert_not_called()

    def test_package_only_stops_before_dynamic_adapter_layers(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            package = root / "package"
            package.mkdir()
            with patch.object(validator, "ROOT", root), \
                    patch.object(validator, "check_manifest", return_value={}), \
                    patch.object(validator, "runtime_dir", return_value=package), \
                    patch.object(validator, "check_layout"), \
                    patch.object(validator, "check_no_runtime_writes"), \
                    patch.object(validator, "check_bridge_smoke") as bridge, \
                    patch.object(validator, "run_typecheck") as typecheck, \
                    patch.object(validator, "run_tests") as tests:
                self.assertEqual(validator.main(["package", "--package-only"]), 0)
                bridge.assert_not_called()
                typecheck.assert_not_called()
                tests.assert_not_called()

    def test_full_and_focused_invocations_are_bounded(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / "test").mkdir()
            for name in ("one.test.ts", "two.test.ts"):
                (root / "test" / name).touch()
            with patch.object(validator, "ROOT", root), patch.object(validator.shutil, "which", return_value="node"), \
                    patch.object(validator.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
                for selected, count in ((None, 2), ("one.test.ts", 1)):
                    self.assertEqual(validator.run_tests(root, selected), 0)
                    argv = run.call_args.args[0]
                    self.assertEqual(argv[:3], ["node", "--test", "--test-timeout=30000"])
                    self.assertEqual(len(argv[3:]), count)
                run.reset_mock()
                self.assertEqual(validator.run_tests(root, "missing.test.ts"), 1)
                run.assert_not_called()


VALID_ROOT = {
    "peerDependencies": {"pi-subagents": "*"},
    "peerDependenciesMeta": {"pi-subagents": {"optional": True}},
    "devDependencies": {"pi-subagents": "0.74.0"},
    "pi": {"extensions": ["./plugins/empirica/adapters/pi/src/index.ts"]},
}
VALID_SETTINGS = {"packages": ["..", {
    "source": validator.SIFFRAN_REPO, "autoload": False,
    "extensions": ["-plugins/empirica/adapters/pi/src/index.ts",
                   "-plugins/methodologist/adapters/pi/src/index.ts"],
    "skills": ["-plugins/empirica/skills/empirica", "-plugins/methodologist/skills/think"]}]}


def altered(base: dict, **changes):
    merged = json.loads(json.dumps(base))
    merged.update(json.loads(json.dumps(changes)))
    return merged


class ExternalRuntimeBoundaryTests(unittest.TestCase):
    def test_the_committed_manifest_and_settings_satisfy_the_boundary(self):
        manifest = json.loads((validator.ROOT / "package.json").read_text(encoding="utf-8"))
        settings = json.loads((validator.ROOT / ".pi" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(validator.external_runtime_problems(manifest), [])
        self.assertEqual(validator.dogfood_settings_problems(settings), [])

    def test_devdependency_only_with_peer_declaration_is_allowed(self):
        self.assertEqual(validator.external_runtime_problems(VALID_ROOT), [])

    def test_every_bundled_or_runtime_form_is_rejected(self):
        cases = {
            "runtime dependency": altered(VALID_ROOT, dependencies={"pi-subagents": "0.50.0"}),
            "optional dependency": altered(VALID_ROOT, optionalDependencies={"pi-subagents": "0.74.0"}),
            "bundledDependencies": altered(VALID_ROOT, bundledDependencies=["pi-subagents"]),
            "bundleDependencies": altered(VALID_ROOT, bundleDependencies=["pi-subagents"]),
            "extension": altered(VALID_ROOT, pi={"extensions": ["./node_modules/pi-subagents/index.ts"]}),
        }
        for name, manifest in cases.items():
            with self.subTest(name):
                self.assertTrue(validator.external_runtime_problems(manifest), name)

    def test_peer_declaration_and_exact_dev_pin_are_required(self):
        no_peer = {k: v for k, v in VALID_ROOT.items() if k != "peerDependencies"}
        self.assertTrue(validator.external_runtime_problems(no_peer))
        self.assertTrue(validator.external_runtime_problems(altered(VALID_ROOT, peerDependencies={"pi-subagents": ">=0.74"})))
        self.assertTrue(validator.external_runtime_problems(altered(VALID_ROOT, peerDependenciesMeta={})))
        self.assertTrue(validator.external_runtime_problems(
            altered(VALID_ROOT, devDependencies={"pi-subagents": "^0.74.0"})))

    def test_stale_settings_are_rejected(self):
        self.assertEqual(validator.dogfood_settings_problems(VALID_SETTINGS), [])
        suppressing = altered(VALID_SETTINGS, packages=[*VALID_SETTINGS["packages"],
            {"source": "npm:pi-subagents@0.50.0", "autoload": False, "extensions": ["-index.ts"]}])
        self.assertTrue(validator.dogfood_settings_problems(suppressing))
        bundled_filter = json.loads(json.dumps(VALID_SETTINGS))
        bundled_filter["packages"][1]["extensions"].append("-node_modules/pi-subagents/index.ts")
        self.assertTrue(validator.dogfood_settings_problems(bundled_filter))
        missing_delta = altered(VALID_SETTINGS, packages=[".."])
        self.assertTrue(validator.dogfood_settings_problems(missing_delta))


if __name__ == "__main__":
    unittest.main()
