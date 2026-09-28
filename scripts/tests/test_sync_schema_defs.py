from __future__ import annotations

from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sync_schema_defs.py"
SPEC = importlib.util.spec_from_file_location("sync_schema_defs", SCRIPT)
assert SPEC and SPEC.loader
sync_schema_defs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_schema_defs)


class SyncSchemaDefsTests(unittest.TestCase):
    def _contracts(self, tmp: str) -> Path:
        contracts = Path(tmp)
        for name in (*sync_schema_defs.TARGETS, "shared-defs.json",
                     "public-contract.schema.json", "public-contract.json"):
            (contracts / name).write_bytes((sync_schema_defs.CONTRACTS / name).read_bytes())
        return contracts

    def _main(self, contracts: Path, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with patch.object(sync_schema_defs, "CONTRACTS", contracts), redirect_stdout(output):
            result = sync_schema_defs.main(list(args))
        return result, output.getvalue()

    def test_check_detects_and_regeneration_repairs_embedded_definition_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contracts = self._contracts(tmp)
            response_path = contracts / "response.schema.json"
            response = json.loads(response_path.read_text())
            response["$defs"]["block"]["allOf"][0]["then"]["properties"]["reasons"] \
                ["items"]["allOf"][1]["properties"]["code"]["enum"] = ["drift"]
            response_path.write_text(json.dumps(response), encoding="utf-8")
            self.assertEqual(self._main(contracts, "--check")[0], 1)
            self.assertEqual(self._main(contracts)[0], 0)
            self.assertEqual(self._main(contracts, "--check")[0], 0)

    def test_near_match_fails_with_json_path_and_annotations_survive(self) -> None:
        shared = json.loads((sync_schema_defs.CONTRACTS / "shared-defs.json").read_text())["$defs"]
        near = copy.deepcopy(shared["identityObservation"])
        near["properties"]["policy_version"]["const"] = "different"
        with self.assertRaisesRegex(sync_schema_defs.SchemaGenerationError,
                                    r"#/\$defs/lookalike"):
            sync_schema_defs._replace_shared(near, shared, ("$defs", "lookalike"))
        exact = copy.deepcopy(shared["invocationProvenance"])
        exact["$comment"] = "retain me"
        self.assertEqual(sync_schema_defs._replace_shared(exact, shared)["$comment"], "retain me")

    def test_missing_source_fails_instead_of_generating_self_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contracts = self._contracts(tmp)
            shared_path = contracts / "shared-defs.json"
            shared = json.loads(shared_path.read_text())
            del shared["$defs"]["identityObservation"]
            shared_path.write_text(json.dumps(shared), encoding="utf-8")
            result, output = self._main(contracts)
            self.assertEqual(result, 2)
            self.assertIn("missing shared definition", output)

    def test_dead_public_contract_embedding_is_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contracts = self._contracts(tmp)
            response_path = contracts / "response.schema.json"
            response = json.loads(response_path.read_text())
            # Simulate a stale embedding from before QUAL-1 removed GetContract target: full.
            response["$defs"]["publicContract"] = {"type": "object"}
            response["$defs"]["publicContract__bootstrapAction"] = {"type": "object"}
            response[sync_schema_defs._MANAGED_KEY] = [
                "publicContract", "publicContract__bootstrapAction"]
            response_path.write_text(json.dumps(response), encoding="utf-8")
            # Regeneration strips both the dead definitions and the management marker.
            self.assertEqual(self._main(contracts)[0], 0)
            generated = json.loads(response_path.read_text())
            self.assertNotIn("publicContract", generated["$defs"])
            self.assertNotIn("publicContract__bootstrapAction", generated["$defs"])
            self.assertNotIn(sync_schema_defs._MANAGED_KEY, generated)
            # And a --check on the stripped output reports no drift.
            self.assertEqual(self._main(contracts, "--check")[0], 0)


if __name__ == "__main__":
    unittest.main()
