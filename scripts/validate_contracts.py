#!/usr/bin/env python3
"""Validate contract documents and substrate-neutral fixture envelopes."""
import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, RefResolver
except ImportError:  # The structural gate below still validates required wire fields.
    Draft202012Validator = RefResolver = None

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
errors: list[str] = []


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.relative_to(ROOT)}: root must be an object")
        return {}
    return value


schemas: dict[tuple[str, str], dict] = {}
for path in sorted(CONTRACTS.glob("*/*/*.schema.json")):
    data = load(path)
    protocol = "/".join(path.relative_to(CONTRACTS).parts[:2])
    kind = path.name.removesuffix(".schema.json")
    schemas[(protocol, kind)] = data
    if data.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append(f"{path.relative_to(ROOT)}: must use JSON Schema 2020-12")
    if not isinstance(data.get("$id"), str):
        errors.append(f"{path.relative_to(ROOT)}: missing $id")

for path in sorted((CONTRACTS / "fixtures").glob("*.json")):
    fixture = load(path)
    for field, kind in (("request", "request"), ("expected", "response")):
        envelope = fixture.get(field)
        if not isinstance(envelope, dict):
            errors.append(f"{path.relative_to(ROOT)}: {field} must be an object")
            continue
        protocol = envelope.get("protocol")
        if (protocol, kind) not in schemas:
            errors.append(f"{path.relative_to(ROOT)}: no {kind} schema for {protocol!r}")
        if not isinstance(envelope.get("request_id"), str) or not envelope["request_id"]:
            errors.append(f"{path.relative_to(ROOT)}: {field}.request_id is required")
    request = fixture.get("request", {})
    expected = fixture.get("expected", {})
    if request.get("protocol") != expected.get("protocol"):
        errors.append(f"{path.relative_to(ROOT)}: request/expected protocols differ")
    if request.get("request_id") != expected.get("request_id"):
        errors.append(f"{path.relative_to(ROOT)}: request/expected ids differ")
    # Fixture envelopes are contract instances, not illustrative JSON. Validate them when the
    # standard validator is available; refs resolve from the schema's on-disk directory.
    if Draft202012Validator is not None:
        for field, kind in (("request", "request"), ("expected", "response")):
            schema = schemas.get((fixture.get(field, {}).get("protocol"), kind))
            if schema is not None:
                store = {value.get("$id"): value for value in schemas.values()
                         if isinstance(value.get("$id"), str)}
                resolver = RefResolver((CONTRACTS / fixture[field]["protocol"].split("/")[0]
                                        / fixture[field]["protocol"].split("/")[1]).as_uri() + "/", schema,
                                       store=store)
                for error in Draft202012Validator(schema, resolver=resolver).iter_errors(fixture[field]):
                    errors.append(f"{path.relative_to(ROOT)}: {field}: {error.message}")

if errors:
    print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
    raise SystemExit(1)
print(f"ok: {len(schemas)} schemas, "
      f"{len(list((CONTRACTS / 'fixtures').glob('*.json')))} fixtures")
