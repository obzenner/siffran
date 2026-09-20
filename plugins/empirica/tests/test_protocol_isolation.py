#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from application import protocol  # noqa: E402
from core.context_selector import select_sections  # noqa: E402


class ProtocolIsolationTests(unittest.TestCase):
    def test_contract_results_are_detached_from_canonical_registry(self):
        full = protocol.contract_result("full")
        section = protocol.contract_result("section", "core")
        index = protocol.contract_result("index")
        full["full"]["sections"]["core"]["title"] = "mutated"
        section["section"]["clauses"][0]["text"] = "mutated"
        index["index"]["reasons"][0]["sections"].append("mutated")
        self.assertNotEqual(
            protocol.contract_result("full")["full"]["sections"]["core"]["title"], "mutated")
        self.assertNotEqual(
            protocol.contract_result("section", "core")["section"]["clauses"][0]["text"],
            "mutated")
        self.assertNotIn(
            "mutated", protocol.contract_result("index")["index"]["reasons"][0]["sections"])

    def test_selector_executes_the_supplied_registry_projection(self):
        registry = copy.deepcopy(protocol._PUBLIC_CONTRACT)
        registry["presentation_selector"]["context_sections"]["bootstrap"] = ["terminal"]
        self.assertEqual(select_sections(registry, "bootstrap", [], None), ["terminal"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
