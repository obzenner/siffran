"""The identity policy strips every thinking level the pinned pi-subagents can append (P1-D1).

Reads the vendored pi-subagents source, so it needs ``make node_modules``; it runs in the Pi suite,
not the Node-free core suite, and fails closed when the dependency is absent.
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

from adapters.identity import observe


class PinnedPiThinkingLevelTests(unittest.TestCase):
    def test_every_pinned_pi_thinking_level_is_normalized(self):
        source = (Path(__file__).resolve().parents[3] / "node_modules" / "pi-subagents" / "src" / "shared"
                  / "model-info.ts")
        self.assertTrue(source.exists(), f"{source} is missing: run `make node_modules` first")
        levels = re.search(r"THINKING_LEVELS = \[([^\]]*)\]", source.read_text()).group(1)
        for level in re.findall(r'"([a-z]+)"', levels):
            with self.subTest(level=level):
                self.assertEqual(observe("amazon-bedrock-us", f"us.openai.gpt-5.6-sol:{level}",
                                         source="test")["identity"], "openai/gpt-5.6-sol")


if __name__ == "__main__":
    unittest.main()
