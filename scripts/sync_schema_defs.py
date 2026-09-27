#!/usr/bin/env python3
"""Generate local Empirica schema embeddings from explicit canonical sources.

``shared-defs.json#/$defs`` is the sole source for invocationProvenance and
identityObservation. ``public-contract.schema.json`` is the sole source for the embedded
publicContract and its prefixed definitions. Neither source file is rewritten here.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts" / "empirica" / "v2"
TARGETS = ("request.schema.json", "response.schema.json", "state.schema.json")
IDENTITY_KEYS = ("identity", "provider_id", "model_id", "policy_version", "source", "observed_by")
INVOCATION_KEYS = ("host", "interactive", "signal", "delegation")
_MANAGED_KEY = "x-generated-public-contract-defs"
_PUBLIC_PREFIX = "publicContract__"
_STRUCTURAL = frozenset({"type", "additionalProperties", "required", "properties"})


class SchemaGenerationError(ValueError):
    """A shared-shaped schema differs from its canonical definition."""


def _load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def _pointer(path: tuple[object, ...]) -> str:
    return "#" + "".join("/" + str(part).replace("~", "~0").replace("/", "~1")
                          for part in path)


def _shape(value: dict, keys: tuple[str, ...]) -> bool:
    return (set(value.get("required", ())) == set(keys)
            and isinstance(value.get("properties"), dict)
            and set(value["properties"]) == set(keys))


def _same_properties(value: dict, shared: dict) -> bool:
    return (value.get("type") == shared.get("type")
            and value.get("required") == shared.get("required")
            and value.get("properties") == shared.get("properties"))


def _carry(value: dict, result: dict[str, Any], shared: dict[str, dict],
           path: tuple[object, ...]) -> dict[str, Any]:
    for key, item in value.items():
        if key not in _STRUCTURAL:
            result[key] = _replace_shared(item, shared, (*path, key))
    return result


def _replace_shared(value: object, shared: dict[str, dict],
                    path: tuple[object, ...] = ()) -> object:
    if isinstance(value, list):
        return [_replace_shared(item, shared, (*path, index))
                for index, item in enumerate(value)]
    if not isinstance(value, dict):
        return value
    properties = value.get("properties")
    required = tuple(value.get("required", ()))
    identity, invocation = shared["identityObservation"], shared["invocationProvenance"]
    if _shape(value, IDENTITY_KEYS):
        if not _same_properties(value, identity):
            raise SchemaGenerationError(
                f"identity-shaped schema differs from identityObservation at {_pointer(path)}")
        result: dict[str, Any] = {"$ref": "#/$defs/identityObservation",
                                  "unevaluatedProperties": False}
        return _carry(value, result, shared, path)
    if (set(IDENTITY_KEYS) < set(required) and isinstance(properties, dict)
            and set(properties) == set(required)):
        if any(properties[key] != identity["properties"][key] for key in IDENTITY_KEYS):
            raise SchemaGenerationError(
                f"identity extension differs from identityObservation at {_pointer(path)}")
        extras = [key for key in required if key not in IDENTITY_KEYS]
        extension = {"type": "object", "required": extras,
                     "properties": {key: _replace_shared(properties[key], shared,
                                                           (*path, "properties", key))
                                    for key in extras}}
        result = {"allOf": [{"$ref": "#/$defs/identityObservation"}, extension],
                  "unevaluatedProperties": False}
        return _carry(value, result, shared, path)
    if _shape(value, INVOCATION_KEYS):
        if not _same_properties(value, invocation):
            raise SchemaGenerationError(
                f"invocation-shaped schema differs from invocationProvenance at {_pointer(path)}")
        return _carry(value, {"$ref": "#/$defs/invocationProvenance"}, shared, path)
    return {key: _replace_shared(item, shared, (*path, key)) for key, item in value.items()}


def _rewrite_public_refs(value: object) -> object:
    if isinstance(value, list):
        return [_rewrite_public_refs(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _rewrite_public_refs(item) for key, item in value.items()}
    ref = result.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        result["$ref"] = f"#/$defs/{_PUBLIC_PREFIX}{ref.split('/')[-1]}"
    return result


def _embed_public(response: dict, public_schema: dict) -> None:
    defs = response["$defs"]
    for name in response.pop(_MANAGED_KEY, []):
        defs.pop(name, None)
    public_definition = {key: copy.deepcopy(value) for key, value in public_schema.items()
                         if key not in {"$schema", "$id", "title", "$defs"}}
    names = ["publicContract", *(_PUBLIC_PREFIX + name for name in public_schema.get("$defs", {}))]
    collisions = [name for name in names if name in defs]
    if collisions:
        raise SchemaGenerationError("public-contract definition collision at #/$defs/" + collisions[0])
    defs["publicContract"] = _rewrite_public_refs(public_definition)
    for name, value in public_schema.get("$defs", {}).items():
        defs[_PUBLIC_PREFIX + name] = _rewrite_public_refs(copy.deepcopy(value))
    response[_MANAGED_KEY] = names


def generated_documents() -> dict[str, dict]:
    docs = {name: _load(name) for name in TARGETS}
    shared = copy.deepcopy(_load("shared-defs.json")["$defs"])
    for required in ("identityObservation", "invocationProvenance"):
        if required not in shared:
            raise SchemaGenerationError(f"missing shared definition #/$defs/{required}")
    for name, document in tuple(docs.items()):
        document = _replace_shared(document, shared)
        defs = document.setdefault("$defs", {})
        defs.pop("publicContractDefs", None)
        defs["invocationProvenance"] = copy.deepcopy(shared["invocationProvenance"])
        defs["identityObservation"] = copy.deepcopy(shared["identityObservation"])
        docs[name] = document

    public_schema = _load("public-contract.schema.json")
    public_contract = _load("public-contract.json")
    _embed_public(docs["response.schema.json"], public_schema)
    refusal_codes = [code for code, row in public_contract["reasons"].items()
                     if row.get("disposition") == "start_refused"]
    block = docs["response.schema.json"]["$defs"]["block"]
    block["allOf"] = [{
        "if": {"not": {"required": ["run"]}},
        "then": {"properties": {"reasons": {"items": {"allOf": [
            {"$ref": "#/$defs/reasonEntry"},
            {"properties": {"code": {"enum": refusal_codes}}},
        ]}}}},
    }]
    return docs


def render(document: dict) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        generated = generated_documents()
    except (KeyError, SchemaGenerationError) as exc:
        print(f"schema generation failed: {exc}")
        return 2
    drift: list[str] = []
    for name, document in generated.items():
        path = CONTRACTS / name
        expected = render(document)
        if path.read_text(encoding="utf-8") != expected:
            drift.append(name)
            if not args.check:
                path.write_text(expected, encoding="utf-8")
    if args.check and drift:
        print("schema definition drift: " + ", ".join(drift))
        return 1
    print("schema definitions are current" if args.check else "schema definitions regenerated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
