#!/usr/bin/env python3
"""Dogfooding never provisions, enables, or suppresses a pi-subagents runtime.

siffran does not bundle pi-subagents (Empirica 4.1): the audit runtime is whichever external
extension registered the ``subagent`` tool, resolved by the adapter at ``session_start``. The
committed ``.pi/settings.json`` and the ``pi-dev``/``pi-canary`` recipes must therefore carry no
pi-subagents package entry, suppression, or bundled-extension filter, and ``pi-canary`` must run
nothing but ``pi install -l`` for siffran itself.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "git:github.com/obzenner/siffran@feature"


def dry_run(*args: str) -> str:
    """The commands ``make -n`` would run; printed messages are recipe text, not behaviour."""
    completed = subprocess.run(["make", "-n", *args], cwd=ROOT, check=True, capture_output=True, text=True)
    return completed.stdout


class PiDogfoodRuntimeBoundaryTests(unittest.TestCase):
    def test_project_settings_carry_no_pi_subagents_entry_or_filter(self) -> None:
        text = (ROOT / ".pi" / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("pi-subagents", text)
        settings = json.loads(text)
        delta = next(entry for entry in settings["packages"] if isinstance(entry, dict)
                     and entry.get("source") == "https://github.com/obzenner/siffran")
        self.assertEqual(delta["extensions"], ["-plugins/empirica/adapters/pi/src/index.ts",
                                               "-plugins/methodologist/adapters/pi/src/index.ts"])
        self.assertEqual([entry for entry in settings["packages"] if isinstance(entry, str)], [".."])
        self.assertEqual(len(settings["packages"]), 2)

    def test_canary_only_installs_siffran_and_never_touches_the_runtime(self) -> None:
        commands = dry_run("pi-canary", "REF=feature", "DIR=/tmp/project", "PI=pi")
        self.assertIn(f'pi install -l "{SOURCE.replace("@feature", "")}@feature"', commands)
        self.assertNotIn("pi-subagents", commands)
        self.assertNotIn("configure_pi_canary", commands)
        self.assertEqual(sum(1 for line in commands.splitlines() if " install " in line), 1)

    def test_canary_recipe_and_scripts_have_no_runtime_toggle(self) -> None:
        self.assertFalse((ROOT / "scripts" / "configure_pi_canary.py").exists())
        self.assertNotIn("configure_pi_canary", (ROOT / "Makefile").read_text(encoding="utf-8"))

    def test_pi_dev_launches_pi_without_provisioning_a_runtime(self) -> None:
        commands = dry_run("pi-dev", "PI=pi")
        self.assertNotIn("pi-subagents", commands)
        self.assertNotIn("pi install", commands)


if __name__ == "__main__":
    unittest.main()
