#!/usr/bin/env python3
"""G10: fixtures and goldens store ``<contract-digest>``, never the contract's hash; a loader puts the live digest in.

A change to ``public-contract.json`` changes its digest, and the digest used to be copied into every contract fixture and
Pi golden. The copies are gone; a response that carries a wrong digest must still fail."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "plugins" / "empirica"))
import validate_contracts as v  # noqa: E402
from application.protocol import contract_digest, stored, to_stored  # noqa: E402
from core.canonical import CONTRACT_DIGEST_PLACEHOLDER  # noqa: E402

FIXTURES = ROOT / "contracts" / "empirica" / "v2" / "fixtures"
GOLDENS = ROOT / "plugins" / "empirica" / "adapters" / "pi" / "test" / "author-view-golden"
REGISTRY = json.loads((ROOT / "contracts" / "empirica" / "v2" / "public-contract.json").read_text(encoding="utf-8"))


class StoredContractDigestTests(unittest.TestCase):
    def test_no_fixture_or_golden_holds_a_copy_of_the_digest(self):
        live = contract_digest()
        holders = [str(p.relative_to(ROOT)) for folder in (FIXTURES, GOLDENS) for p in sorted(folder.glob("*.json"))
                   if live in p.read_text(encoding="utf-8")]
        self.assertEqual(holders, [])

    def test_the_placeholder_is_stored_and_loading_puts_the_live_digest_in(self):
        raw = json.loads((FIXTURES / "start-bootstrap-allow.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["expected"]["result"]["run"]["contract"]["digest"], CONTRACT_DIGEST_PLACEHOLDER)
        loaded = stored(raw)
        self.assertEqual(loaded["expected"]["result"]["run"]["contract"]["digest"], contract_digest())
        self.assertEqual(to_stored(loaded), raw)  # the generators write the placeholder back
        self.assertNotIn(CONTRACT_DIGEST_PLACEHOLDER, json.dumps(loaded))

    def test_the_validator_loads_the_same_digest_from_the_registry_file(self):
        self.assertEqual(v.registry_digest(REGISTRY), contract_digest())

    def test_loading_never_heals_a_digest_that_is_not_the_placeholder(self):
        """The loader fills the placeholder only: a file that stores a wrong digest keeps it, and the comparison fails."""
        raw = json.loads((FIXTURES / "block-open-claim.json").read_text(encoding="utf-8"))
        live = contract_digest()
        raw["expected"]["result"]["run"]["contract"]["digest"] = live[:-1] + ("0" if live[-1] != "0" else "1")
        kept = stored(raw)["expected"]["result"]["run"]["contract"]["digest"]
        self.assertNotEqual(kept, live)
        errors: list[str] = []
        v.check_response_run_view(stored(raw)["expected"]["result"], REGISTRY, {}, v.registry_digest(REGISTRY), errors, "f")
        self.assertTrue([e for e in errors if "contract.digest" in e], errors)

    def test_a_response_with_a_wrong_contract_digest_still_fails(self):
        result = stored(json.loads((FIXTURES / "block-open-claim.json").read_text(encoding="utf-8")))["expected"]["result"]
        errors: list[str] = []
        v.check_response_run_view(result, REGISTRY, {}, v.registry_digest(REGISTRY), errors, "fixture")
        self.assertEqual([e for e in errors if "contract.digest" in e], [])
        wrong = copy.deepcopy(result)
        digest = wrong["run"]["contract"]["digest"]
        wrong["run"]["contract"]["digest"] = digest[:-1] + ("0" if digest[-1] != "0" else "1")
        self.assertNotEqual(wrong, result)
        errors = []
        v.check_response_run_view(wrong, REGISTRY, {}, v.registry_digest(REGISTRY), errors, "fixture")
        self.assertTrue([e for e in errors if "contract.digest" in e], errors)


if __name__ == "__main__":
    unittest.main()
