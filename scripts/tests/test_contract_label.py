#!/usr/bin/env python3
"""The public contract's label (6.0.0) is the same everywhere it is checked or stated. One test names every place,
so a label that moves in one place and not another fails here."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_contracts as v  # noqa: E402

LABEL = "6.0.0"
CONTRACTS = ROOT / "contracts" / "empirica" / "v2"
PLUGIN = ROOT / "plugins" / "empirica"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ContractLabelTests(unittest.TestCase):
    def test_the_contract_and_everything_that_checks_it_agree(self):
        self.assertEqual(json.loads(text(CONTRACTS / "public-contract.json"))["version"], LABEL)
        self.assertEqual(v.REGISTRY_VERSION, LABEL)
        self.assertEqual(json.loads(text(PLUGIN / "architecture.json"))["required_contract_references"]["version"], LABEL)
        # The Pi guard derives the label from response.schema.json (checked below); it must not restate it.
        self.assertNotIn(f'"{LABEL}"', text(PLUGIN / "adapters" / "pi" / "src" / "guard.ts"))
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


class TurnEndNoticeTests(unittest.TestCase):
    """The ``unattended_residual`` settlement notice (the honest end of an auto turn) is one name in the contract, its
    schema, the vendored copy, the tools file Pi reads, and the turn-end table's validator."""

    NOTICE = "unattended_residual"

    def registry_with_a_cell_naming_it(self) -> dict:
        doc = json.loads(text(CONTRACTS / "host-boundaries.json"))
        doc["turn_end"]["states"][0]["outcomes"]["auto-delegated"]["notices"] = [self.NOTICE]
        return doc

    def test_the_validator_accepts_the_notice_only_when_the_contract_defines_it(self):
        accepted: list[str] = []
        v.check_turn_end(self.registry_with_a_cell_naming_it(), accepted, "doc")
        self.assertEqual(accepted, [])
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            contract = json.loads(text(CONTRACTS / "public-contract.json"))
            del contract["settlement_notices"][self.NOTICE]
            target = Path(tmp) / "contracts" / "empirica" / "v2"
            target.mkdir(parents=True)
            (target / "public-contract.json").write_text(json.dumps(contract), encoding="utf-8")
            refused: list[str] = []
            v.check_turn_end(self.registry_with_a_cell_naming_it(), refused, "doc", Path(tmp))
        self.assertTrue(refused)  # every cell of the registry that names it, and the one added above
        for message in refused:
            self.assertIn(f"notice '{self.NOTICE}' is not a settlement notice", message)

    def test_the_schema_the_vendored_copy_and_the_tools_file_name_it(self):
        schema = json.loads(text(CONTRACTS / "public-contract.schema.json"))["properties"]["settlement_notices"]
        self.assertIn(self.NOTICE, schema["required"])
        self.assertEqual(schema["properties"][self.NOTICE], schema["properties"]["unattended_continue"])  # one {reason}
        contract = json.loads(text(CONTRACTS / "public-contract.json"))["settlement_notices"]
        self.assertEqual(contract[self.NOTICE].count("{reason}"), 1)
        for name in ("public-tools.json",):
            tools = json.loads(text(CONTRACTS / name))["settlement_notices"]
            self.assertEqual(tools, contract, name)
            self.assertEqual(text(CONTRACTS / name), text(PLUGIN / "vendor" / "contracts" / "empirica" / "v2" / name), name)

    def test_the_notice_names_the_reason_and_offers_the_honest_end(self):
        notice = json.loads(text(CONTRACTS / "public-contract.json"))["settlement_notices"][self.NOTICE]
        self.assertIn("{reason}", notice)
        self.assertIn("report_convergence intent=stop", notice)


if __name__ == "__main__":
    unittest.main()
