"""Strict v2 request protocol seam (D6 spec §6).

Sole internal loader/root discovery: loads the PublicContract, request/response/state schemas, and
host profiles once from the repo and computes the canonical PublicContract SHA256 over strict
canonical JSON; exposes the private constants sibling modules consume.
``dispatch_request(raw, handler)`` is the service's only public-request gateway: it validates the
raw value against the closed request schema, calls ``handler`` exactly once with the valid
discriminated envelope, validates the handler response against the response schema, requires the
response request_id to equal the request request_id, and applies the response fallback. Invalid
wire returns exact Fault ``invalid_request``/closed with a safe request-id fallback. A malformed,
exceptional, or request-id-mismatched handler response becomes an exact schema-valid
``unavailable``/closed Fault without recursive validation loops. Trusted payload shape never grants
capability admission (handled by the service, not here).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

# Sole internal loader: PublicContract, request/response/state schemas, host profiles (SSOT).
# Canonical PublicContract SHA256 over strict canonical JSON.

_ROOT = Path(__file__).resolve()
for _ in range(8):
    if (_ROOT / "contracts" / "empirica" / "v2" / "public-contract.json").exists():
        break
    _ROOT = _ROOT.parent
_V2 = _ROOT / "contracts" / "empirica" / "v2"

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
            "reasons": [{"code": key, "sections": row["sections"]}
                        for key, row in _PUBLIC_CONTRACT["reasons"].items()],
            "next_actions": [{"id": key, "description": row["description"]}
                             for key, row in _PUBLIC_CONTRACT["next_actions"].items()]}}
    if target == "section":
        row = _PUBLIC_CONTRACT["sections"].get(section_id)
        if row is None:
            return None
        return {"target": target, "digest": _DIGEST, "section_id": section_id,
                "section": {"id": section_id, "title": row["title"],
                            "summary": row["summary"], "clauses": row["clauses"]}}
    if target == "full":
        return {"target": target, "digest": _DIGEST, "full": _PUBLIC_CONTRACT}
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
