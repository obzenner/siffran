#!/usr/bin/env python3
"""Checkout/Make delegation checks for the shipped sandbox operator CLI."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "empirica"))

from adapters.sandbox import cli  # noqa: E402


class CheckoutDelegationTests(unittest.TestCase):
    def test_packaged_cli_exposes_only_explicit_operator_operations(self):
        self.assertEqual(cli.parse_args(("preflight",)).operation, "preflight")
        self.assertEqual(cli.parse_args(("qualify", "--probe", "all")).operation, "qualify")
        self.assertEqual(cli.parse_args(("install",)).operation, "install")
        with self.assertRaises(SystemExit):
            cli.parse_args(("install", "--probe", "preflight"))

    def test_root_scripts_are_thin_delegates_to_the_shipped_package(self):
        qualification = (ROOT / "scripts" / "sandbox_qualification.py").read_text(encoding="utf-8")
        compatibility = (ROOT / "scripts" / "docker_spike_qualification.py").read_text(encoding="utf-8")
        self.assertIn("from adapters.sandbox.cli import main", qualification)
        self.assertIn("from adapters.sandbox.docker import *", compatibility)
        self.assertNotIn("subprocess", qualification)
        self.assertNotIn("class DockerClient", compatibility)

    def test_default_reports_stay_under_the_established_tmp_directory(self):
        self.assertEqual(cli.DEFAULT_OUTPUT_DIR.name, "siffran-sandbox-qualification")
        self.assertEqual(cli.DEFAULT_OUTPUT_DIR.parent, Path(__import__("tempfile").gettempdir()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
