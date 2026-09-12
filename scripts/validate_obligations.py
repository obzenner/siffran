#!/usr/bin/env python3
"""Validate obligation schemas and every executable fixture, with a stdlib fallback."""
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "contracts/obligations/v1"
sys.path.insert(0, str(ROOT / "lib"))
from obligations import Contract, Observation, Obligation, parse  # noqa: E402
from obligations.model import REF_PATTERN  # noqa: E402

errors: list[str] = []
REF_RE = re.compile(REF_PATTERN)
KINDS = {"test", "exit_code", "event", "artifact", "predicate", "judgment"}
PARTITIONS = ("satisfied", "holds", "violated", "residual", "unwitnessed", "held")


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.relative_to(ROOT)}: root must be an object")
        return {}
    return value


def require(value: dict, keys: tuple[str, ...], where: str) -> None:
    missing = [key for key in keys if key not in value]
    if missing:
        errors.append(f"{where}: missing required keys {missing}")


def structural_witness(value: Any, where: str, observed: bool = False) -> None:
    if not isinstance(value, dict):
        errors.append(f"{where}: witness must be an object")
        return
    require(value, ("kind", "ref", "expect", "description"), where)
    if value.get("kind") not in KINDS:
        errors.append(f"{where}: invalid kind")
    if not isinstance(value.get("ref"), str) or not REF_RE.fullmatch(value["ref"]):
        errors.append(f"{where}: invalid ref")
    if value.get("expect") not in {"pass", "fail"}:
        errors.append(f"{where}: invalid expect")
    if not isinstance(value.get("description"), str) or not value["description"].strip():
        errors.append(f"{where}: empty description")
    if observed and value.get("observed") not in {None, "pass", "fail"}:
        errors.append(f"{where}: invalid observed outcome")


def structural_obligation(value: Any, where: str, view: bool = False) -> None:
    if not isinstance(value, dict):
        errors.append(f"{where}: obligation must be an object")
        return
    require(value, ("id", "mode", "must", "witnesses", "because"), where)
    if value.get("mode") not in {"require", "forbid"}:
        errors.append(f"{where}: invalid mode")
    if value.get("hold") not in {None, "blocked", "deferred"}:
        errors.append(f"{where}: invalid hold")
    if ("hold" in value) != ("hold_reason" in value):
        errors.append(f"{where}: hold and hold_reason must occur together")
    for index, witness in enumerate(value.get("witnesses", ())):
        structural_witness(witness, f"{where}.witnesses[{index}]", view)
    if view and value.get("status") not in {"satisfied", "holds", "violated", "residual"}:
        errors.append(f"{where}: invalid or missing status")


def structural_contract(value: Any, where: str, view: bool = False) -> None:
    if not isinstance(value, dict):
        errors.append(f"{where}: contract must be an object")
        return
    require(value, ("contract_id", "revision", "obligations", "provenance", "supersedes", "retired"), where)
    for index, obligation in enumerate(value.get("obligations", ())):
        structural_obligation(obligation, f"{where}.obligations[{index}]", view)
    for index, retirement in enumerate(value.get("retired", ())):
        if not isinstance(retirement, dict):
            errors.append(f"{where}.retired[{index}]: must be an object")
            continue
        require(retirement, ("obligation", "reason", "authority", "at_revision"), f"{where}.retired[{index}]")
        structural_obligation(retirement.get("obligation"), f"{where}.retired[{index}].obligation")
    if view:
        structural_verdict(value.get("verdict"), f"{where}.verdict")


def structural_observation(value: Any, where: str) -> None:
    if not isinstance(value, dict):
        errors.append(f"{where}: observation must be an object")
        return
    require(value, ("kind", "ref", "outcome", "source", "at"), where)
    if value.get("kind") not in KINDS:
        errors.append(f"{where}: invalid kind")
    if not isinstance(value.get("ref"), str) or not REF_RE.fullmatch(value["ref"]):
        errors.append(f"{where}: invalid ref")
    if value.get("outcome") not in {"pass", "fail"}:
        errors.append(f"{where}: invalid outcome")
    if not isinstance(value.get("source"), str) or not value["source"].strip():
        errors.append(f"{where}: source is required")
    if value.get("kind") == "judgment" and str(value.get("source", "")).strip().lower() in {"anonymous", "unknown", "model"}:
        errors.append(f"{where}: anonymous judgment source")


def structural_verdict(value: Any, where: str) -> None:
    if not isinstance(value, dict):
        errors.append(f"{where}: verdict must be an object")
        return
    require(value, PARTITIONS, where)
    for name in PARTITIONS:
        if not isinstance(value.get(name), list) or not all(isinstance(item, str) for item in value.get(name, ())):
            errors.append(f"{where}.{name}: must be an id array")


schemas = {path.name: load(path) for path in sorted(BASE.glob("*.schema.json"))}
for name in ("contract.schema.json", "observation.schema.json", "verdict.schema.json"):
    schema = schemas.get(name, {})
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append(f"{name}: must use JSON Schema 2020-12")
try:
    import jsonschema
except ImportError:
    jsonschema = None
else:
    for name, schema in schemas.items():
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            errors.append(f"{name}: invalid schema: {exc.message}")

contract_schema = schemas.get("contract.schema.json", {})
def_schema = contract_schema.get("$defs", {})
for path in sorted((BASE / "fixtures").glob("*.json")):
    fixture = load(path)
    where = str(path.relative_to(ROOT))
    require(fixture, ("name", "expect"), where)
    try:
        if "before" in fixture:
            structural_contract(fixture.get("before"), f"{where}.before", "verdict" in fixture.get("before", {}))
            structural_contract(fixture.get("after"), f"{where}.after", "verdict" in fixture.get("after", {}))
            require(fixture.get("expect", {}), ("ok", "reasons"), f"{where}.expect")
            parse(fixture["before"])
            parse(fixture["after"])
        else:
            contract_value = fixture.get("base") if "operation" in fixture else fixture.get("contract")
            structural_contract(contract_value, f"{where}.contract")
            Contract.from_json(contract_value)
            for field in ("observations", "comparison_observations"):
                for index, value in enumerate(fixture.get(field, ())):
                    structural_observation(value, f"{where}.{field}[{index}]")
                    Observation.from_json(value)
            expected = fixture.get("expect", {})
            require(expected, ("verdict", "view", "text"), f"{where}.expect")
            structural_verdict(expected.get("verdict"), f"{where}.expect.verdict")
            structural_contract(expected.get("view"), f"{where}.expect.view", True)
            parse(expected["view"])
            if "operation" in fixture:
                structural_contract(expected.get("contract"), f"{where}.expect.contract")
                Contract.from_json(expected["contract"])
                for value in fixture["operation"].get("add", ()):
                    Obligation.from_json(value)
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"{where}: {exc}")
    if jsonschema:
        if "before" in fixture:
            instance_values = (
                (fixture.get("before"), "view" if "verdict" in fixture.get("before", {}) else "contract", "before"),
                (fixture.get("after"), "view" if "verdict" in fixture.get("after", {}) else "contract", "after"),
            )
        else:
            instance_values = (
                ((fixture.get("base") if "operation" in fixture else fixture.get("contract")), "contract", "contract"),
                (fixture.get("expect", {}).get("verdict"), "verdict", "verdict"),
                (fixture.get("expect", {}).get("view"), "view", "view"),
            )
            if "operation" in fixture:
                instance_values += ((fixture.get("expect", {}).get("contract"), "contract", "expect.contract"),)
        for value, definition, label in instance_values:
            try:
                jsonschema.validate(value, {"$ref": f"#/$defs/{definition}", "$defs": def_schema})
            except jsonschema.ValidationError as exc:
                errors.append(f"{where}.{label}: {exc.message}")
        for field in ("observations", "comparison_observations"):
            for index, value in enumerate(fixture.get(field, ())):
                try:
                    jsonschema.validate(value, schemas["observation.schema.json"])
                except jsonschema.ValidationError as exc:
                    errors.append(f"{where}.{field}[{index}]: {exc.message}")

if errors:
    print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
    raise SystemExit(1)
mode = "jsonschema + stdlib" if jsonschema else "stdlib structural"
print(f"ok: {len(schemas)} schemas, {len(list((BASE / 'fixtures').glob('*.json')))} fixtures ({mode})")
