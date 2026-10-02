"""Strict v2 protocol over the plugin-vendored canonical contracts.

``make vendor-check`` keeps the shipped copy byte-identical to the repository SSOT.
"""
from __future__ import annotations

import copy
import functools
import json
import logging
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import jsonschema
from referencing import Registry, Resource

from core.canonical import canonical_digest
from core.evaluation import ContractView
from .history_records import POLICY_INPUT_KEYS

_LOGGER = logging.getLogger("empirica.protocol")

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
# The static contract documents a caller can validate against, with the registry each one needs.
_SCHEMA_DOCUMENTS = {
    "request": (_REQUEST_SCHEMA, None),
    "response": (_RESPONSE_SCHEMA, _SCHEMA_REGISTRY),
    "state": (_STATE_SCHEMA, None),
}
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
_AUDIT_OBLIGATION = (_bootstrap["audit_obligation"]["obligation_id"],
                     _bootstrap["audit_obligation"]["must"])
_BOOTSTRAP_OPERATIONS = tuple((name, tuple((step["predicate"], step["reason"])
                                           for step in operation["preconditions"]))
                              for name, operation in _bootstrap["operations"].items())
_REASON_METADATA = tuple((code, tuple(spec["next_actions"]), tuple(spec["sections"]))
                         for code, spec in _PUBLIC_CONTRACT["reasons"].items())
_actions = _PUBLIC_CONTRACT["actions"]
if set(_actions["metadata"]) != set(_actions["author"]):
    raise RuntimeError("action metadata must cover every author action exactly")
_decisions = _PUBLIC_CONTRACT["governance_decisions"]
if tuple(_decisions["actions"]) != ("approve", "edit", "reject"):
    raise RuntimeError("unknown or reordered governance decision binding")
_GOVERNANCE_DECISIONS = tuple((name, copy.deepcopy(row))
                              for name, row in _decisions["actions"].items())
_GOVERNANCE_CONTROLS = copy.deepcopy(_decisions)
_PROJECTION_CONTROLS = _GOVERNANCE_CONTROLS["controls"]
_NOTICES = _PUBLIC_CONTRACT["settlement_notices"]
HUMAN_WAIT_NOTICE: str = _NOTICES["human_wait"]
#: Template with the single ``{resource}`` placeholder, formatted with the reason's resource.
BUDGET_EXHAUSTED_NOTICE: str = _NOTICES["budget_exhausted"]
_AUTHOR_VIEW_LABELS = MappingProxyType(dict(_PUBLIC_CONTRACT["author_view"]["labels"]))
_RECOVERY_EXCLUSIONS = {
    mode: tuple(actions)
    for mode, actions in _GOVERNANCE_CONTROLS["recovery_exclusions"].items()
}
APPROVAL_CAPABILITY = MappingProxyType({
    ingress: row["capability"]
    for ingress, row in _PUBLIC_CONTRACT["approval_ingress"].items()
})
_configure_ceilings = _REQUEST_SCHEMA["$defs"]["actionConfigureRun"]["properties"][
    "budgets"
]["properties"]
CEILING_BOUNDS = MappingProxyType({
    name: (schema["minimum"], schema["maximum"])
    for name, schema in _configure_ceilings.items()
})
_LATE_ROUTE_MUST = _bootstrap["late_route_must"]
_DEFAULT_NEXT_ACTION = _bootstrap["default_next_action"]
if _DEFAULT_NEXT_ACTION not in _PUBLIC_CONTRACT["next_actions"]:
    raise RuntimeError("bootstrap default next action is not a contract next action")
_UNTRUSTED_DELIMITERS = copy.deepcopy(_PUBLIC_CONTRACT["untrusted_delimiters"])
_DIGEST = canonical_digest(_PUBLIC_CONTRACT)
CONTRACT_VIEW = ContractView(
    reason_metadata=_REASON_METADATA,
    bootstrap_requirements=_BOOTSTRAP_REQUIREMENTS,
    audit_obligation=_AUDIT_OBLIGATION,
    bootstrap_operations=_BOOTSTRAP_OPERATIONS,
    governance_controls=_PROJECTION_CONTROLS,
    recovery_exclusions=_RECOVERY_EXCLUSIONS,
    late_route_must=_LATE_ROUTE_MUST,
    default_next_action=_DEFAULT_NEXT_ACTION,
    untrusted_delimiters=_UNTRUSTED_DELIMITERS,
)


def protocol_id() -> str:
    """Return the canonical wire protocol identifier."""
    return _PROTOCOL


def public_contract() -> Mapping[str, Any]:
    """Return a read-only view of the canonical public contract."""
    return MappingProxyType(_PUBLIC_CONTRACT)


def request_schema() -> Mapping[str, Any]:
    """Return a read-only view of the canonical request schema."""
    return MappingProxyType(_REQUEST_SCHEMA)


def response_schema() -> Mapping[str, Any]:
    """Return a read-only view of the canonical response schema."""
    return MappingProxyType(_RESPONSE_SCHEMA)


def state_schema() -> Mapping[str, Any]:
    """Return a read-only view of the canonical state schema."""
    return MappingProxyType(_STATE_SCHEMA)


def schema_registry() -> Registry:
    """Return the immutable referencing registry used by schema validators."""
    return _SCHEMA_REGISTRY


def state_schema_id() -> str:
    """Return the canonical persisted-state schema identifier."""
    return _STATE_SCHEMA_ID


def profile_ids() -> tuple[str, ...]:
    return tuple(sorted(_PROFILES))


def host_profile(profile_id: str) -> Mapping[str, Any]:
    return copy.deepcopy(_PROFILES[profile_id])


def governance_decisions() -> tuple[tuple[str, Mapping[str, Any]], ...]:
    """Return the canonical governance decision rows."""
    return tuple((name, MappingProxyType(row)) for name, row in _GOVERNANCE_DECISIONS)


def author_view_labels() -> Mapping[str, str]:
    """Return the contract-owned author-view section headings and line labels."""
    return _AUTHOR_VIEW_LABELS


def untrusted_delimiters() -> Mapping[str, Any]:
    """Return the canonical untrusted-text delimiters."""
    return MappingProxyType(_UNTRUSTED_DELIMITERS)


def contract_digest() -> str:
    return _DIGEST


def projection_controls() -> dict:
    return copy.deepcopy(_PROJECTION_CONTROLS)


def next_action_surfaces() -> dict[str, dict]:
    """Return the public next-action surface table without exposing the contract registry."""
    return {key: copy.deepcopy(row["surface"])
            for key, row in _PUBLIC_CONTRACT["next_actions"].items()}


def response_schema_defs() -> dict[str, dict]:
    """Return a copy of the v2 response schema ``$defs`` (parameter and payload shapes)."""
    return copy.deepcopy(_RESPONSE_SCHEMA["$defs"])



@functools.cache
def schema_validator(document: str, definition: str | None = None):
    """Return the process-wide validator for a contract document, or for one of its ``$defs``.

    The documents are static, so each schema is checked against its metaschema once, here, and the
    validator is reused. (``jsonschema.validate`` re-checks the schema on every call, which cost
    about 60 ms per request, state decode and response.)
    """
    schema, registry = _SCHEMA_DOCUMENTS[document]
    if definition is not None:
        schema = {"$schema": schema.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
                  "$defs": schema.get("$defs", {}), "$ref": f"#/$defs/{definition}"}
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema) if registry is None else cls(schema, registry=registry)


def validate_public_result(result: object) -> bool:
    """Validate one public result with the canonical v2 response validator."""
    envelope = {"protocol": _PROTOCOL, "request_id": "author-view", "result": result}
    try:
        schema_validator("response").validate(envelope)
    except (jsonschema.ValidationError, TypeError):
        return False
    return True


def policy_inputs(profile_id: str) -> dict[str, str]:
    """Return the canonical policy identity persisted with each observation basis."""
    inputs = {
        "contract_id": _PUBLIC_CONTRACT["id"],
        "contract_version": _PUBLIC_CONTRACT["version"],
        "contract_digest": _DIGEST,
        "profile_id": profile_id,
    }
    assert set(inputs) == POLICY_INPUT_KEYS
    return inputs


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
    return None


def validate_private_governance_response(response: object) -> bool:
    """Validate one application-private governance response against the private-response
    definition (public result + required presentation for run-bearing Allow/Block/Inert).

    The public response schema forbids a ``presentation`` key, so private governance responses
    are validated here, never on the public wire. A mismatch lets the caller fail closed with
    the same Fault shape ``dispatch_request`` produces.
    """
    try:
        schema_validator("response", "privateGovernanceResponse").validate(response)
    except (jsonschema.ValidationError, TypeError):
        return False
    return True


def validate_trusted_payload(name: str, payload: object) -> bool:
    """Validate one adapter-private payload against the canonical closed schema."""
    if name not in _REQUEST_SCHEMA.get("$defs", {}):
        return False
    try:
        schema_validator("request", name).validate(payload)
    except jsonschema.ValidationError:
        return False
    return True


def _deepest(error: jsonschema.ValidationError) -> jsonschema.ValidationError:
    """Descend through ``oneOf``/``anyOf``/``allOf`` sub-errors to the most informative failure.

    A ``const`` mismatch usually means "a different envelope alternative" (for example
    ``/result/type``), so it ranks below any other failure; among the rest, deeper wins. This names
    the field that failed, not the enclosing alternative. It is a diagnostic heuristic only.
    """
    while error.context:
        error = max(error.context,
                    key=lambda sub: (sub.validator != "const", len(sub.absolute_path)))
    return error


def _validation_pointer(error: jsonschema.ValidationError) -> str:
    """Return an RFC 6901 pointer without including any instance value."""
    escaped = (str(part).replace("~", "~0").replace("/", "~1")
               for part in error.absolute_path)
    return "/" + "/".join(escaped)


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
        schema_validator("request").validate(raw)
    except jsonschema.ValidationError:
        return _fault("invalid_request", _safe_request_id(raw))
    request_id = raw["request_id"]
    try:
        resp = handler(raw)
    except Exception as exc:
        _LOGGER.warning("handler exception type=%s", type(exc).__name__)
        return _fault("unavailable", request_id)
    try:
        schema_validator("response").validate(resp)
    except jsonschema.ValidationError as exc:
        failed = _deepest(exc)
        _LOGGER.warning("response validation failed pointer=%s validator=%s",
                        _validation_pointer(failed), failed.validator)
        return _fault("unavailable", request_id)
    except TypeError:
        _LOGGER.warning("response validation failed pointer=/ validator=type")
        return _fault("unavailable", request_id)
    if resp.get("request_id") != request_id:
        return _fault("unavailable", request_id)
    return resp
