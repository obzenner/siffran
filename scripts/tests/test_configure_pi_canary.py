from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "configure_pi_canary.py"
SPEC = importlib.util.spec_from_file_location("configure_pi_canary", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ConfigurePiCanaryTests(unittest.TestCase):
    def test_detects_string_and_object_global_pi_subagents(self) -> None:
        self.assertTrue(MODULE.has_global_pi_subagents({"packages": ["npm:pi-subagents@0.50.0"]}))
        self.assertTrue(
            MODULE.has_global_pi_subagents(
                {"packages": [{"source": "npm:pi-subagents", "extensions": []}]}
            )
        )
        self.assertFalse(MODULE.has_global_pi_subagents({"packages": ["npm:other"]}))

    def test_converts_installed_string_and_preserves_other_packages(self) -> None:
        source = "git:github.com/obzenner/siffran@feature"
        settings = {"packages": ["npm:other", source]}
        self.assertTrue(MODULE.exclude_bundled_subagents(settings, source))
        self.assertEqual(
            settings,
            {
                "packages": [
                    "npm:other",
                    {
                        "source": source,
                        "extensions": [MODULE.BUNDLED_SUBAGENTS],
                    },
                ]
            },
        )
        self.assertFalse(MODULE.exclude_bundled_subagents(settings, source))

    def test_main_only_rewrites_when_global_subagents_is_installed(self) -> None:
        source = "git:github.com/obzenner/siffran@feature"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent_dir = root / "agent"
            project_dir = root / "project"
            agent_dir.mkdir()
            (project_dir / ".pi").mkdir(parents=True)
            global_path = agent_dir / "settings.json"
            project_path = project_dir / ".pi" / "settings.json"
            project_path.write_text(json.dumps({"packages": [source]}), encoding="utf-8")
            global_path.write_text(json.dumps({"packages": ["npm:other"]}), encoding="utf-8")
            env = {**os.environ, "PI_CODING_AGENT_DIR": str(agent_dir)}

            subprocess.run(
                [sys.executable, str(SCRIPT), str(project_dir), source],
                check=True,
                env=env,
            )
            self.assertEqual(json.loads(project_path.read_text()), {"packages": [source]})

            global_path.write_text(
                json.dumps({"packages": ["npm:pi-subagents@0.50.0"]}), encoding="utf-8"
            )
            subprocess.run(
                [sys.executable, str(SCRIPT), str(project_dir), source],
                check=True,
                env=env,
            )
            entry = json.loads(project_path.read_text())["packages"][0]
            self.assertEqual(entry["source"], source)
            self.assertEqual(entry["extensions"], [MODULE.BUNDLED_SUBAGENTS])


if __name__ == "__main__":
    unittest.main()
