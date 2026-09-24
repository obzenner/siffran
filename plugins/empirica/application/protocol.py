"""Strict v2 protocol over the plugin-vendored canonical contracts.

``make vendor-check`` keeps the shipped copy byte-identical to the repository SSOT.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

# Sole internal loader and canonical PublicContract digest.

_V2 = Path(__file__).resolve().parents[1] / "vendor/contracts/empirica/v2"

_PUBLIC_CONTRACT = json.loads((_V2 / "public-contract.json").read_text(encoding="utf-8"))
_REQUEST_SCHEMA = json.loads((_V2 / "request.schema.json").read_text(encoding="utf-8"))
_RESPONSE_SCHEMA = json.loads((_V2 / "response.schema.json").read_text(encoding="utf-8"))
_PUBLIC_CONTRACT_SCHEMA = json.loads(
    (_V2 / "public-contract.schema.json").read_text(encoding="utf-8"))
_SCHEMA_REGISTRY = Registry().with_resource(
    _PUBLIC_CONTRACT_SCHEMA["$id"], Resource.from_contents(_PUBLIC_CONTRACT_SCHEMA))
_STATE_SCHEMA = json.loads((_V2 / "state.schema.json").read_text(encoding="utf-8"))
_HOST_PROFILES = json.loads((_V2 / "host-profiles.json").read_text(encoding="utf-8"))
_PROTOCOL = _PUBLIC_CONTRACT["protocol"]
_STATE_SCHEMA_ID = _STATE_SCHEMA["properties"]["state_schema"]["const"]
_PROFILES = {p["profile_id"]: p for p in _HOST_PROFILES["profiles"]}
_BOOTSTRAP_PREDICATES = ("route.recorded", "graph.selected", "governance.approved",
                         "investigation.recorded")
_BOOTSTRAP_OPERATION_IDS = ("route", "graph", "configure_run", "governance.present",
                             "investigate", "report_convergence")
_bootstrap = _PUBLIC_CONTRACT["bootstrap"]
if (tuple(row["predicate"] for row in _bootstrap["requirements"]) != _BOOTSTRAP_PREDICATES
        or tuple(_bootstrap["operations"]) != _BOOTSTRAP_OPERATION_IDS
        or any(step["predicate"] not in _BOOTSTRAP_PREDICATES
               or step["reason"] not in _PUBLIC_CONTRACT["reasons"]
               for operation in _bootstrap["operations"].values()
               for step in operation["preconditions"])):
    raise RuntimeError("unknown or reordered bootstrap contract binding")
_BOOTSTRAP_REQUIREMENTS = tuple((row["predicate"], row["obligation_id"], row["must"])
                                for row in _bootstrap["requirements"])
_BOOTSTRAP_OPERATIONS = tuple((name, tuple((step["predicate"], step["reason"])
                                           for step in operation["preconditions"]))
                              for name, operation in _bootstrap["operations"].items())
_decisions = _PUBLIC_CONTRACT["governance_decisions"]
if (tuple(_decisions["actions"]) != ("approve", "edit", "request_changes", "reject")
        or any(row["feedback"] not in {"forbidden", "required"}
               for row in _decisions["actions"].values())):
    raise RuntimeError("unknown or reordered governance decision binding")
_GOVERNANCE_DECISIONS = tuple((name, copy.deepcopy(row))
                              for name, row in _decisions["actions"].items())
_GOVERNANCE_CONTROLS = copy.deepcopy(_decisions)
_DIGEST = "sha256:" + hashlib.sha256(
    json.dumps(_PUBLIC_CONTRACT, sort_keys=True, separators=(",", ":")).encode(),
).hexdigest()

def _safe_request_id(raw) -> str:
    """Use the supplied valid nonempty request ID when safe; otherwise ``invalid-request``."""
    if isinstance(raw, dict):
        rid = raw.get("request_id")
        if isinstance(rid, str) and rid:
            return rid
    return "invalid-request"


def _fault(code: str, request_id: str) -> dict:
    """Build a schema-valid v2 Fault envelope that always speaks v2."""
    return {
        "protocol": _PROTOCOL,
        "request_id": request_id,
        "result": {"type": "Fault", "code": code, "fail_direction": "closed"},
    }


def contract_result(target: str, section_id: str | None = None) -> dict | None:
    """Materialize exactly one projection from the canonical registry."""
    if target == "index":
        return {"target": target, "digest": _DIGEST, "index": {
            "id": _PUBLIC_CONTRACT["id"], "version": _PUBLIC_CONTRACT["version"],
            "sections": [{"id": key, "title": row["title"]}
                         for key, row in _PUBLIC_CONTRACT["sections"].items()],
            "reasons": [{"code": key, "sections": list(row["sections"])}
                        for key, row in _PUBLIC_CONTRACT["reasons"].items()],
            "next_actions": [{"id": key, "description": row["description"]}
                             for key, row in _PUBLIC_CONTRACT["next_actions"].items()]}}
    if target == "section":
        row = _PUBLIC_CONTRACT["sections"].get(section_id)
        if row is None:
            return None
        return {"target": target, "digest": _DIGEST, "section_id": section_id,
                "section": {"id": section_id, "title": row["title"],
                            "summary": row["summary"], "clauses": copy.deepcopy(row["clauses"])}}
    if target == "full":
        return {"target": target, "digest": _DIGEST, "full": copy.deepcopy(_PUBLIC_CONTRACT)}
    return None


def validate_trusted_payload(name: str, payload: object) -> bool:
    """Validate one adapter-private payload against the canonical closed schema."""
    schema = {
        "$schema": _REQUEST_SCHEMA.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
        "$defs": _REQUEST_SCHEMA.get("$defs", {}),
        "$ref": f"#/$defs/{name}",
    }
    if name not in schema["$defs"]:
        return False
    try:
        jsonschema.validate(instance=payload, schema=schema)
    except jsonschema.ValidationError:
        return False
    return True


def dispatch_request(raw, handler):
    """Validate ``raw``, call ``handler`` once with the valid envelope, validate the
    response, require response request_id == request request_id, and apply the fallback.

    * Invalid wire (null/empty/v1/future protocol, unknown/extra fields, unknown command/action)
      → exact Fault ``invalid_request``/closed with a safe request ID.
    * A valid request calls ``handler`` exactly once.
    * A malformed, exceptional, or request-id-mismatched handler response → exact schema-valid
      ``unavailable``/closed Fault (the accepted D2 code), without recursive validation loops.
    """
    try:
        jsonschema.validate(instance=raw, schema=_REQUEST_SCHEMA)
    except jsonschema.ValidationError:
        return _fault("invalid_request", _safe_request_id(raw))
    request_id = raw["request_id"]
    try:
        resp = handler(raw)
    except Exception:
        return _fault("unavailable", request_id)
    try:
        jsonschema.validators.validator_for(_RESPONSE_SCHEMA)(
            _RESPONSE_SCHEMA, registry=_SCHEMA_REGISTRY).validate(resp)
    except (jsonschema.ValidationError, TypeError):
        return _fault("unavailable", request_id)
    if resp.get("request_id") != request_id:
        return _fault("unavailable", request_id)
    return resp
