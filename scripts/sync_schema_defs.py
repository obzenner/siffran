#!/usr/bin/env python3
"""Generate local Empirica schema embeddings from explicit canonical sources.

``shared-defs.json#/$defs`` is the sole source for invocationProvenance,
identityObservation and claimId (the one claim-identifier pattern, embedded in every target). The dead ``publicContract`` embedding (only reachable through the removed
``GetContract target: full`` response) is stripped here, not re-embedded. No source file is
rewritten.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts" / "empirica" / "v2"
DOCKER_SUPPORT = ROOT / "contracts" / "empirica-docker-spike" / "v1" / "support-contract.json"
TARGETS = ("request.schema.json", "response.schema.json", "state.schema.json")
IDENTITY_KEYS = ("identity", "provider_id", "model_id", "policy_version", "source", "observed_by")
INVOCATION_KEYS = ("host", "interactive", "signal", "delegation", "audit_retrieval", "human_surface")
# Optional members of the shared invocation definition: an inline copy that lists them is still the
# shared shape (`required` stays INVOCATION_KEYS).
INVOCATION_OPTIONAL_KEYS = ("host_runtime", "session")
# Extra closed shared definitions copied verbatim into the targets that reference them.
# The graph payload is the SSOT for the model-facing graph action shape (defence in depth for
# core.evaluation.valid_graph); governancePresentation is the private-only dialog body.
_GRAPH_DEFS = ("graphClaimItem", "graphEdgeItem", "graphPayload")
# Every target carries the claim-identifier definition; each claim-id position refers to it.
_UNIVERSAL_SHARED_DEFS = ("claimId",)
_EXTRA_SHARED_DEFS = {
    "request.schema.json": (*_GRAPH_DEFS, "spikePlanDraft", "environmentProposal"),
    "response.schema.json": (*_GRAPH_DEFS, "governancePresentation", "spikePlanDraft",
                               "qualificationProbeName"),
    "state.schema.json": ("environmentProposal", "qualificationProbeName"),
}
_MANAGED_KEY = "x-generated-public-contract-defs"
_PUBLIC_PREFIX = "publicContract__"
_STRUCTURAL = frozenset({"type", "additionalProperties", "required", "properties"})


class SchemaGenerationError(ValueError):
    """A shared-shaped schema differs from its canonical definition."""


def _load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def _qualification_probe_names() -> list[str]:
    """The qualification probe names from the Docker spike support contract (their only copy).

    The support contract's ``qualification.probes`` is the owner (validated against the code
    order by empirica-docker-support-contract-check); the generated ``qualificationProbeName``
    definition reuses that vocabulary for run/5 rows and the qualification failure and refusal
    payload, so no target schema restates the names (ADR-0075).
    """
    probes = json.loads(DOCKER_SUPPORT.read_text(encoding="utf-8"))["qualification"]["probes"]
    if not isinstance(probes, list) or not probes or len(probes) != len(set(probes)) \
            or any(not isinstance(name, str) or not name for name in probes):
        raise SchemaGenerationError("the Docker spike support contract's qualification probes "
                                    "are not a nonempty closed set of names")
    return probes


def _shared_definitions() -> dict[str, dict]:
    shared = copy.deepcopy(_load("shared-defs.json")["$defs"])
    # The ADR-0075 registration rides the definition itself: the annotation names the one core
    # callable that validates a probe word (both target copies register through it), while the
    # enumeration stays the support contract's own generated word, never re-owned here.
    shared["qualificationProbeName"] = {
        "x-core-validator": "core.run.validate_qualification_probe_name",
        "enum": _qualification_probe_names(),
        "description": ("The qualification probe names, owned by the Docker spike support "
                        "contract (contracts/empirica-docker-spike/v1, its only copy): the "
                        "run's qualification rows, its last failure and a refusal that names a "
                        "probe reuse this enumeration, never a second spelling."),
    }
    return shared


def _pointer(path: tuple[object, ...]) -> str:
    return "#" + "".join("/" + str(part).replace("~", "~0").replace("/", "~1")
                          for part in path)


def _shape(value: dict, keys: tuple[str, ...], optional: tuple[str, ...] = ()) -> bool:
    return (set(value.get("required", ())) == set(keys)
            and isinstance(value.get("properties"), dict)
            and set(keys) <= set(value["properties"]) <= set(keys) | set(optional))


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
    if _shape(value, INVOCATION_KEYS, INVOCATION_OPTIONAL_KEYS):
        if not _same_properties(value, invocation):
            raise SchemaGenerationError(
                f"invocation-shaped schema differs from invocationProvenance at {_pointer(path)}")
        return _carry(value, {"$ref": "#/$defs/invocationProvenance"}, shared, path)
    return {key: _replace_shared(item, shared, (*path, key)) for key, item in value.items()}


def _drop_managed_public_defs(response: dict) -> None:
    """Strip the dead embedded publicContract definitions and their management marker.

    QUAL-1 removed ``GetContract target: full`` — the only response path that reached the embedded
    publicContract — so these definitions are unreachable (a reference-closure check from the
    response root and privateGovernanceResponse finds no path). Regeneration is idempotent.
    """
    defs = response.get("$defs", {})
    for name in response.pop(_MANAGED_KEY, []):
        defs.pop(name, None)
    for name in [n for n in defs if n == "publicContract" or n.startswith(_PUBLIC_PREFIX)]:
        defs.pop(name, None)


def _replace_spike_draft(value: object, draft: dict[str, Any]) -> object:
    """Replace every embedded public draft schema with the shared unreleased /2 shape."""
    if isinstance(value, list):
        return [_replace_spike_draft(item, draft) for item in value]
    if not isinstance(value, dict):
        return value
    schema = value.get("properties", {}).get("schema")
    if isinstance(schema, dict) and schema.get("const") == "empirica.spike-plan-draft/2":
        return copy.deepcopy(draft)
    return {key: _replace_spike_draft(item, draft) for key, item in value.items()}


def _replace_projected_spike_draft(value: object, draft: dict[str, Any]) -> object:
    if isinstance(value, list):
        return [_replace_projected_spike_draft(item, draft) for item in value]
    if not isinstance(value, dict):
        return value
    properties = value.get("properties")
    if (isinstance(properties, dict)
            and properties.get("kind", {}).get("const") == "spike_request"
            and "artifact_id" in properties and "draft" in properties):
        result = copy.deepcopy(value)
        result["properties"]["draft"] = copy.deepcopy(draft)
        result["properties"]["inputs"] = {
            "type": "array", "maxItems": 256,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["path", "sha256", "bytes"],
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 1024,
                             "pattern": "^(?!/)(?!.*\\\\)(?!.*//).+$"},
                    "sha256": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                    "bytes": {"type": "integer", "minimum": 0, "maximum": 67108864},
                },
            },
        }
        return {key: _replace_projected_spike_draft(item, draft)
                for key, item in result.items()}
    return {key: _replace_projected_spike_draft(item, draft) for key, item in value.items()}


def _single_result_facts_schema() -> dict[str, Any]:
    digest = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
    stream = {"type": "object", "additionalProperties": False,
              "required": ["bytes_seen", "sha256"],
              "properties": {"bytes_seen": {"type": "integer", "minimum": 0},
                             "sha256": copy.deepcopy(digest)}}
    operation_names = ("operation_digest", "qualification_record_digest",
                       "execution_binding_digest", "image_digest", "input_digest",
                       "process_digest", "topology_digest", "timing_digest")
    backend = {
        "type": "object", "additionalProperties": False,
        "required": ["kind", "backend", "profile_id", "qualification_record_digest",
                     "execution_binding_digest"],
        "properties": {
            "kind": {"const": "qualified"}, "backend": {"const": "docker"},
            "profile_id": {"enum": ["docker-isolated-v1", "docker-networked-v1"]},
            "qualification_record_digest": copy.deepcopy(digest),
            "execution_binding_digest": copy.deepcopy(digest),
        },
    }
    fields = ("run_id", "backend", "plan_digest", "policy_digest", "snapshot_digest",
              "operation_facts", "stdout", "stderr", "natural_exit",
              "forced_termination", "exit_code", "verdict", "cleanup_proven",
              "postflight_identity_digest", "harness_result_digest")
    return {
        "type": "object", "additionalProperties": False, "required": list(fields),
        "properties": {
            "run_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"},
            "backend": backend,
            "plan_digest": copy.deepcopy(digest), "policy_digest": copy.deepcopy(digest),
            "snapshot_digest": copy.deepcopy(digest),
            "operation_facts": {
                "type": "object", "additionalProperties": False,
                "required": list(operation_names),
                "properties": {name: copy.deepcopy(digest) for name in operation_names},
            },
            "stdout": copy.deepcopy(stream), "stderr": copy.deepcopy(stream),
            "natural_exit": {"const": True}, "forced_termination": {"const": False},
            "exit_code": {"type": "integer", "minimum": 0, "maximum": 255,
                          "not": {"const": 137}},
            "verdict": {"enum": ["PASS", "FAIL"]},
            "cleanup_proven": {"const": True},
            "postflight_identity_digest": copy.deepcopy(digest),
            "harness_result_digest": copy.deepcopy(digest),
        },
    }


def _replace_result_facts(value: object) -> object:
    if isinstance(value, list):
        return [_replace_result_facts(item) for item in value]
    if not isinstance(value, dict):
        return value
    properties = value.get("properties")
    if (isinstance(properties, dict) and "verdict_container_id" in properties
            and "containers" in properties and "harness_result_digest" in properties):
        return _single_result_facts_schema()
    return {key: _replace_result_facts(item) for key, item in value.items()}


def _bound_qualification_view(response: dict, state: dict) -> None:
    """Bind the run view's qualification accounting to the one attempt ceiling the run/5
    state owns (``qualification_attempts.maxItems``), never a second literal (ADR-0075):

    ``attempts_used`` and ``active.seq`` are generated an upper bound at that ceiling, and
    ``attempts_max`` is generated the ceiling's exact value (``const``), because the field
    reports the fixed run ceiling, not a sometimes-smaller limit. ``qualification_attempts``
    stays the single owner of how many qualification attempts one run may ever start (Empirica
    7.2 §5.1).
    """
    attempts = state.get("properties", {}).get("qualification_attempts", {})
    ceiling = attempts.get("maxItems")
    if type(ceiling) is not int or ceiling < 1:
        raise SchemaGenerationError("state.schema.json qualification_attempts.maxItems is not "
                                    "the attempt ceiling owner")
    view = response.get("$defs", {}).get("qualificationView")
    members = (view or {}).get("properties", {})
    if not isinstance(view, dict) or not {"attempts_used", "attempts_max", "active"} <= set(members):
        raise SchemaGenerationError("response.schema.json qualificationView is not the run's "
                                    "qualification accounting projection")
    members["attempts_used"]["maximum"] = ceiling
    fixed = members["attempts_max"]
    # The ceiling stays a fixed numeric const — and an explicit integer type rides it (E6c
    # review, blocking 1): a value like 8.0 is equal to draft 2020-12 (integer, const compares
    # equal) while the application's Python validator demands an int object, so the conformance
    # engine derives the member's float-for-int candidate from the type and the entry declares it.
    fixed["type"] = "integer"
    fixed.pop("minimum", None)
    fixed.pop("maximum", None)
    fixed["const"] = ceiling
    bound = next((branch for branch in members["active"].get("oneOf", [])
                  if isinstance(branch, dict) and "seq" in branch.get("properties", {})), None)
    if bound is None:
        raise SchemaGenerationError("response.schema.json qualification.active has no installed "
                                    "binding branch with a sequence")
    bound["properties"]["seq"]["maximum"] = ceiling


def generated_documents() -> dict[str, dict]:
    docs = {name: _load(name) for name in TARGETS}
    shared = _shared_definitions()
    for required in ("identityObservation", "invocationProvenance"):
        if required not in shared:
            raise SchemaGenerationError(f"missing shared definition #/$defs/{required}")
    for name, document in tuple(docs.items()):
        document = _replace_spike_draft(document, shared["spikePlanDraft"])
        document = _replace_projected_spike_draft(
            document, shared["projectedSpikePlanDraft"])
        document = _replace_result_facts(document)
        # The embedded copies of the shared definitions are generated output: refresh the previous
        # ones in place (they may predate a shared-definition change) instead of comparing against
        # them, so the document keeps its definition order.
        for embedded in ("invocationProvenance", "identityObservation"):
            if embedded in document.get("$defs", {}):
                document["$defs"][embedded] = copy.deepcopy(shared[embedded])
        document = _replace_shared(document, shared)
        defs = document.setdefault("$defs", {})
        defs.pop("publicContractDefs", None)
        defs["invocationProvenance"] = copy.deepcopy(shared["invocationProvenance"])
        defs["identityObservation"] = copy.deepcopy(shared["identityObservation"])
        for extra in (*_UNIVERSAL_SHARED_DEFS, *_EXTRA_SHARED_DEFS.get(name, ())):
            if extra not in shared:
                raise SchemaGenerationError(f"missing shared definition #/$defs/{extra}")
            defs[extra] = copy.deepcopy(shared[extra])
        docs[name] = document

    _bound_qualification_view(docs["response.schema.json"], docs["state.schema.json"])

    public_contract = _load("public-contract.json")
    public_contract = _replace_spike_draft(public_contract, shared["spikePlanDraft"])
    public_contract = _replace_projected_spike_draft(
        public_contract, shared["projectedSpikePlanDraft"])
    public_contract = _replace_result_facts(public_contract)
    docs["public-contract.json"] = public_contract
    _drop_managed_public_defs(docs["response.schema.json"])
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
