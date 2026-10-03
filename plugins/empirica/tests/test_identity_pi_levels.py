"""The identity policy strips exactly the contract's thinking levels (Empirica 4.1, D8).

The level set is the contract's ``thinking_levels`` (host-profiles.json); the cases are a shared policy
fixture that the Pi adapter test (``preflight-seam.test.ts``) consumes too. Nothing here reads a
dependency's source: the fixture states the policy, the contract states the levels.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from adapters.identity import observe

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/fixtures/thinking-level-cases.json").read_text(encoding="utf-8"))["cases"]
CONTRACT = json.loads(
    (ROOT / "vendor/contracts/empirica/v2/host-profiles.json").read_text(encoding="utf-8"))


class ThinkingLevelPolicyTests(unittest.TestCase):
    def test_every_case_is_normalized_as_the_fixture_states(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                observed = observe(case["provider"], case["model_id"], source="test")
                self.assertEqual(observed["identity"] if observed else None, case["python_identity"])

    def test_the_cases_cover_exactly_the_contract_levels(self):
        self.assertEqual({case["strips_level"] for case in CASES if case["strips_level"]},
                         set(CONTRACT["thinking_levels"]))

    def test_each_case_is_self_consistent(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                level = case["strips_level"]
                expected = case["model_id"][:-len(level) - 1] if level else case["model_id"]
                if level:
                    self.assertTrue(case["model_id"].endswith(f":{level}"))
                self.assertEqual(case["without_thinking_level"], expected)

    def test_a_contract_level_outside_the_fixture_would_not_be_silently_covered(self):
        # Mutation control: the strip pattern is built from the contract, so a level absent from the
        # contract is a plain (non-concrete) suffix, not a thinking level.
        self.assertIsNone(observe("openai", "gpt-5.6-sol:ultra", source="test"))

    def test_the_policy_fixture_names_no_dependency_path(self):
        text = (ROOT / "tests/fixtures/thinking-level-cases.json").read_text(encoding="utf-8")
        self.assertNotIn("node_modules", text)
        self.assertNotIn("pi-subagents/src", text)


if __name__ == "__main__":
    unittest.main()
