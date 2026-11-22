#!/usr/bin/env python3
"""The contract vendor sync must repair byte drift, including nested same-basename extras.

`support-contract.json` alone is the shipped payload of the docker-spike directory; a file
with the same basename nested under it must be deleted by its repository-relative path (not
its bare name), so `make vendor-contracts` can repair exactly what `vendor-check` rejects.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import check_contract_vendor  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "sync_contract_vendor", SCRIPTS / "sync_contract_vendor.py")
sync_contract_vendor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync_contract_vendor
SPEC.loader.exec_module(sync_contract_vendor)


class ContractVendorSyncTests(unittest.TestCase):
    def seeded_copy(self) -> tuple[Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "contracts" / "empirica-docker-spike" / "v1"
        vendor = root / "vendor" / "contracts" / "empirica-docker-spike" / "v1"
        source.mkdir(parents=True)
        vendor.mkdir(parents=True)
        (source / "support-contract.json").write_bytes(b"current contract bytes\n")
        (vendor / "support-contract.json").write_bytes(b"stale contract bytes\n")
        return source, vendor

    def test_sync_repairs_byte_drift_and_the_checker_agrees(self):
        source, vendor = self.seeded_copy()
        copies = [(source, vendor, ("support-contract.json",))]
        problems = check_contract_vendor.problems(copies)
        self.assertIn(str(vendor / "support-contract.json"),
                      "".join(problems), problems)
        self.assertEqual(sync_contract_vendor.sync(copies), 1)
        self.assertEqual((vendor / "support-contract.json").read_bytes(),
                         b"current contract bytes\n")
        self.assertEqual(check_contract_vendor.problems(copies), [])

    def test_a_nested_same_basename_extra_is_deleted_by_relative_path(self):
        source, vendor = self.seeded_copy()
        nested = vendor / "old"
        nested.mkdir()
        (nested / "support-contract.json").write_bytes(b"rogue contract bytes\n")
        copies = [(source, vendor, ("support-contract.json",))]
        self.assertIn(str(nested / "support-contract.json"),
                      "".join(check_contract_vendor.problems(copies)))
        sync_contract_vendor.sync(copies)
        self.assertFalse((nested / "support-contract.json").exists())
        self.assertFalse(nested.exists(), "the emptied directory is removed too")
        self.assertEqual(check_contract_vendor.problems(copies), [])


if __name__ == "__main__":
    unittest.main()
