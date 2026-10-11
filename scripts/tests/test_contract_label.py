#!/usr/bin/env python3
"""The public contract's label (4.0.0) is the same everywhere it is checked or stated. One test names every place,
so a label that moves in one place and not another fails here."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_contracts as v  # noqa: E402

LABEL = "4.0.0"
CONTRACTS = ROOT / "contracts" / "empirica" / "v2"
PLUGIN = ROOT / "plugins" / "empirica"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ContractLabelTests(unittest.TestCase):
    def test_the_contract_and_everything_that_checks_it_agree(self):
        self.assertEqual(json.loads(text(CONTRACTS / "public-contract.json"))["version"], LABEL)
        self.assertEqual(v.REGISTRY_VERSION, LABEL)
        self.assertEqual(json.loads(text(PLUGIN / "architecture.json"))["required_contract_references"]["version"], LABEL)
        self.assertIn(f'index.version === "{LABEL}"', text(PLUGIN / "adapters" / "pi" / "src" / "author-view.ts"))
        for schema in ("public-contract.schema.json", "response.schema.json"):
            consts = re.findall(r'"const": "(\d+\.\d+\.\d+)"', text(CONTRACTS / schema))
            self.assertIn(LABEL, consts, schema)
            self.assertEqual(text(CONTRACTS / schema), text(PLUGIN / "vendor" / "contracts" / "empirica" / "v2" / schema), schema)
        governance = text(PLUGIN / "skills" / "empirica" / "references" / "governance.md")
        self.assertIn(f"(public contract {LABEL})", governance.splitlines()[0])

    def test_every_stored_run_view_names_the_label(self):
        wrong = [str(p.relative_to(ROOT)) for folder in (CONTRACTS / "fixtures",
                                                         PLUGIN / "adapters" / "pi" / "test" / "author-view-golden")
                 for p in sorted(folder.glob("*.json"))
                 if set(re.findall(r'"contract": \{[^{}]*?"version": "([^"]+)"', text(p))) - {LABEL}]
        self.assertEqual(wrong, [])


if __name__ == "__main__":
    unittest.main()
