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


CLAIM_ID = {"type": "string", "pattern": r"^[A-Za-z0-9._-]{1,64}(?![\s\S])"}
_CLAIM_NAMES = frozenset({"from", "to", "root"})
_SCHEMA_KEYWORDS = frozenset({"properties", "$defs"})
# Every claim-id position, per document. A new claim-bearing field that is not listed here (or
# does not resolve to the one pattern) fails the traversal below.
_GRAPH_POSITIONS = (
    "/$defs/graphClaimItem/properties/id",
    "/$defs/graphEdgeItem/properties/from",
    "/$defs/graphEdgeItem/properties/to",
    "/$defs/graphPayload/properties/root",
)
_TOOL_GRAPH = ("/properties/action/oneOf/0/properties/payload/properties/root",
               "/properties/action/oneOf/0/properties/payload/properties/claims/items/properties/id",
               "/properties/action/oneOf/0/properties/payload/properties/edges/items/properties/from",
               "/properties/action/oneOf/0/properties/payload/properties/edges/items/properties/to",
               "/properties/action/oneOf/1/properties/claim_id",
               "/properties/action/oneOf/2/properties/claim_id")
EXPECTED_CLAIM_ID_POSITIONS = {
    "shared-defs.json": _GRAPH_POSITIONS,
    "request.schema.json": (
        "/$defs/reviewedClaimItemRequest/properties/claim_id",
        "/$defs/actionResearch/properties/claim_id",
        "/$defs/actionSpikeRequest/properties/claim_id",
        *_GRAPH_POSITIONS,
    ),
    "response.schema.json": (
        "/$defs/auditStaleParams/properties/claim_id",
        "/$defs/freezeDeferredParams/properties/claim_ids",
        "/$defs/residualItem/allOf/1/properties/claim_id",
        "/$defs/residualItem/allOf/1/properties/target_claim_id",
        "/$defs/obligationSummary/properties/missing/oneOf/1/properties/target_claim_id",
        "/$defs/argumentView/properties/root_claim_id",
        "/$defs/claimItem/properties/claim_id",
        "/$defs/edgeItem/properties/from",
        "/$defs/edgeItem/properties/to",
        "/$defs/researchArtifact/properties/claim_id",
        "/$defs/spikeRequestArtifact/properties/claim_id",
        "/$defs/spikeArtifact/properties/claim_id",
        "/$defs/reviewedClaimItem/properties/claim_id",
        *_GRAPH_POSITIONS,
    ),
    "state.schema.json": ("/properties/frozen_claim_ids",),
    "public-contract.json": (
        "/next_actions/graph.record/params/properties/root",
        "/next_actions/research.record/params/properties/claim_id",
        "/next_actions/spike.run/params/properties/claim_id",
        "/next_actions/spike.regate/params/properties/claim_id",
        "/next_actions/human.request_decision/params/properties/claim_id",
        "/reasons/audit.stale/params/properties/claim_id",
        "/reasons/freeze.deferred/params/properties/claim_ids",
    ),
    "public-tools.json": tuple(f"/schemas/{surface}/empirica_observe{path}"
                               for surface in ("model", "host_handle") for path in _TOOL_GRAPH),
}


def _claim_positions(node: object, path: str = "") -> set[str]:
    """Discover every schema property that names a claim identifier."""
    found: set[str] = set()
    if isinstance(node, list):
        for index, item in enumerate(node):
            found |= _claim_positions(item, f"{path}/{index}")
    elif isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            for name, schema in properties.items():
                graph_vertex = name == "id" and {"text", "gating"} <= set(properties)
                if (name in _CLAIM_NAMES or graph_vertex or name.endswith("claim_id")
                        or name.endswith("claim_ids")):
                    found.add(f"{path}/properties/{name}")
        for key, item in node.items():
            found |= _claim_positions(item, f"{path}/{key}")
    return found


def _at(document: dict, pointer: str) -> object:
    node: object = document
    for part in pointer.split("/")[1:]:
        node = node[int(part)] if isinstance(node, list) else node[part]  # type: ignore[index]
    return node


def _is_claim_id(schema: object, document: dict) -> bool:
    """Resolve refs, nullable ``anyOf`` and arrays down to the one claim-id fragment."""
    if not isinstance(schema, dict):
        return False
    if set(schema) == {"$ref"}:
        return _is_claim_id(_at(document, schema["$ref"].removeprefix("#")), document)
    if set(schema) == {"anyOf"}:
        branches = schema["anyOf"]
        return (len(branches) == 2 and {"type": "null"} in branches
                and any(_is_claim_id(branch, document) for branch in branches
                        if branch != {"type": "null"}))
    if "items" in schema and schema.get("type") in ("array", ["null", "array"]):
        return _is_claim_id(schema["items"], document)
    return schema == CLAIM_ID


class ClaimIdPositionTests(unittest.TestCase):
    def test_every_claim_id_position_resolves_to_the_one_pattern(self) -> None:
        shared = json.loads((sync_schema_defs.CONTRACTS / "shared-defs.json").read_text())
        self.assertEqual(shared["$defs"]["claimId"], CLAIM_ID)
        for name, expected in EXPECTED_CLAIM_ID_POSITIONS.items():
            with self.subTest(document=name):
                document = json.loads((sync_schema_defs.CONTRACTS / name).read_text())
                self.assertEqual(_claim_positions(document), set(expected))
                for pointer in expected:
                    self.assertTrue(_is_claim_id(_at(document, pointer), document), pointer)
                if name in sync_schema_defs.TARGETS:
                    self.assertEqual(document["$defs"]["claimId"], CLAIM_ID)

    def test_unlisted_or_loose_claim_field_fails_the_traversal(self) -> None:
        document = json.loads((sync_schema_defs.CONTRACTS / "response.schema.json").read_text())
        document["$defs"]["claimItem"]["properties"]["parent_claim_id"] = {"type": "string"}
        self.assertIn("/$defs/claimItem/properties/parent_claim_id", _claim_positions(document))
        self.assertFalse(_is_claim_id({"type": "string", "minLength": 1}, document))
        self.assertFalse(_is_claim_id({"anyOf": [{"type": "string"}, {"type": "null"}]}, document))
        self.assertTrue(_is_claim_id(
            {"anyOf": [{"$ref": "#/$defs/claimId"}, {"type": "null"}]}, document))

    def test_claim_id_boundaries_hold_in_every_generated_schema(self) -> None:
        import jsonschema
        accepted = ("A", "a.b-C_9", "x" * 64)
        rejected = ("", "x" * 65, "a b", "claim:C0", "caf\u00e9", "a\nb",
                    "<<<EMPIRICA_UNTRUSTED_DATA>>>")
        for name in sync_schema_defs.TARGETS:
            document = json.loads((sync_schema_defs.CONTRACTS / name).read_text())
            schema = {"$schema": document.get("$schema"), "$defs": document["$defs"],
                      "$ref": "#/$defs/claimId"}
            validator = jsonschema.Draft202012Validator(schema)
            for value in accepted:
                self.assertTrue(validator.is_valid(value), (name, value))
            for value in rejected:
                self.assertFalse(validator.is_valid(value), (name, value))


if __name__ == "__main__":
    unittest.main()
