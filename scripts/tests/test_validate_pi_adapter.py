#!/usr/bin/env python3
"""The Make entry point must select real tests and bound asynchronous waits."""
import contextlib
import io
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


if __name__ == "__main__":
    unittest.main()
