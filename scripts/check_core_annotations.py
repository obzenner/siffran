#!/usr/bin/env python3
"""The ADR-0075 static guard for the run-state contract's core-validation annotations (check-static).

The contract is the single source of a shape, so the annotations themselves are guarded on every fast gate:

* every ``$defs`` entry reachable from a core-validated (annotated) node carries its own annotation — a member
  definition the core validates can never slip out of the harness by riding in unannotated;
* every ``x-core-validator`` names a module and a callable of the plugin's packages that exist;
* every ``x-core-only`` entry declares its rule and the one test that pins it, in the ``path::Class.test``
  form, and that test exists;
* no annotation is orphaned: an annotated state member is a required member of the run state, and an annotated
  ``$defs`` definition is reachable from an annotated state member;
* every schema keyword the reachable closure uses is one the strategy engines derive
  (``ui_view_strategies.KNOWN`` plus the two annotation keywords) — a definition that grows a keyword the
  engines cannot draw fails here first.

The harness (plugins/empirica/tests/test_contract_conformance.py) exercises what these annotations declare;
this script checks what they are. It reads the repository's SSOT contracts, never the vendor copies.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "scripts", ROOT / "plugins" / "empirica"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from contract_strategies import Contract, X_CORE_ONLY, X_VALIDATOR  # noqa: E402
import validate_contracts  # noqa: E402
from ui_view_strategies import KNOWN  # noqa: E402


def _reachable_defs(contract: Contract, pointer: str) -> set[str]:
    """The ``#/$defs/<name>`` entries an annotated node reaches through its references, transitively."""
    seen: set[str] = set()

    def follow(node: object) -> None:
        if not isinstance(node, dict):
            return
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.rsplit("/", 1)[-1]
            if f"#/$defs/{name}" not in seen and name in contract.schema.get("$defs", {}):
                seen.add(f"#/$defs/{name}")
                follow(contract.schema["$defs"][name])
        for word in ("oneOf", "anyOf", "allOf"):
            for branch in node.get(word, ()):
                follow(branch)
        for sub in node.get("properties", {}).values():
            follow(sub)
        follow(node.get("items"))
        follow(node.get("not"))

    follow(contract.node(pointer))
    return seen


def _keyword_errors(contract: Contract, pointer: str) -> list[str]:
    """Every keyword the pointer's reachable closure uses, checked against what the engines derive."""
    errors: list[str] = []
    known = KNOWN | {X_VALIDATOR, X_CORE_ONLY}
    seen: set[int] = set()

    def walk(node: object, at: str) -> None:
        if not isinstance(node, dict) or id(node) in seen:
            return
        seen.add(id(node))
        for word in sorted(node):
            if word not in known:
                errors.append(f"{at}: a keyword the strategy engines do not derive: {word!r}")
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            follow = contract.schema
            for part in ref.removeprefix("#/").split("/"):
                follow = follow[part]
            walk(follow, at)
        for key in ("oneOf", "anyOf", "allOf"):
            for index, branch in enumerate(node.get(key, ())):
                walk(branch, f"{at}/{key}/{index}")
        walk(node.get("not"), f"{at}/not")
        for member, sub in node.get("properties", {}).items():
            walk(sub, f"{at}/properties/{member}")
        if isinstance(node.get("items"), dict):
            walk(node["items"], f"{at}/items")

    walk(contract.node(pointer), pointer)
    return errors


def check(schema: dict | None = None) -> list[str]:
    """The guard's findings, as human-readable lines; an empty list is the pass."""
    try:
        contract = Contract(schema)
    except ValueError as exc:
        return [f"state.schema.json: {exc}"]
    errors: list[str] = []
    required = set(contract.schema.get("required", ()))
    members = [pointer for pointer in contract.annotated if pointer.startswith("#/properties/")]
    reaches = {pointer: _reachable_defs(contract, pointer) for pointer in contract.annotated}

    for pointer in sorted(contract.annotated):
        if pointer.startswith("#/properties/"):
            if pointer.rsplit("/", 1)[-1] not in required:
                errors.append(f"{pointer}: an annotated state member must be required by the run state")
        elif not any(pointer in reaches[member] for member in members):
            errors.append(f"{pointer}: an annotated definition no annotated state member reaches is orphaned")
    for definition in sorted(set().union(*reaches.values()) if reaches else set()):
        if definition.startswith("#/$defs/") and definition not in contract.annotated:
            errors.append(f"{definition}: reachable from a core-validated member but carries no annotation")

    for pointer, named in sorted(contract.validators.items()):
        module, _, name = named.rpartition(".")
        try:
            imported = __import__(module, fromlist=[name])
        except ImportError as exc:
            errors.append(f"{pointer}: x-core-validator module {module!r} does not import: {exc}")
            continue
        if not callable(getattr(imported, name, None)):
            errors.append(f"{pointer}: x-core-validator names no callable of {module}: {name!r}")

    for pointer, entries in sorted(contract.core_only.items()):
        for entry in entries:
            module_path, _, names = entry["test"].rpartition("::")
            if not module_path or not names or "." not in names:
                errors.append(f"{pointer}: an x-core-only test must read path::Class.test: {entry['test']!r}")
                continue
            target = ROOT / module_path
            if not target.exists():
                errors.append(f"{pointer}: the named x-core-only test file does not exist: {module_path!r}")
                continue
            missing = validate_contracts._test_declared(target, names.split("."))
            if missing:
                errors.append(f"{pointer}: the named x-core-only test does not exist: {missing!r} in {module_path!r}")

    for pointer in sorted(contract.annotated):
        errors.extend(_keyword_errors(contract, pointer))
    return errors


def main() -> int:
    findings = check()
    for line in findings:
        print(line)
    if findings:
        print(f"{len(findings)} annotation finding(s)", file=sys.stderr)
        return 1
    print("core-validation annotations are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
