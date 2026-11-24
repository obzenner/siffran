#!/usr/bin/env python3
"""The ADR-0075 static guard for the v2 contracts' core-validation annotations (check-static).

The contract is the single source of a shape, so the annotations themselves are guarded on every fast
gate — on every ``contracts/empirica/v2`` schema document, not only the state document, so a later node's
response ``Fault`` registers by declaration:

* every document that carries an annotation declares its core ingress roots (``x-core-ingress``, a list
  of local pointers), each entry a well-formed pointer to a node carrying ``x-core-validator`` — removing
  only a member's annotation is caught here, because the declared ingress inventory demands it;
* every annotation found anywhere in a document — walked in full: through ``properties``, ``items``
  rows, schema-valued ``additionalProperties``, ``oneOf``/``anyOf``/``allOf`` branches, ``not``, and the
  ``$defs`` — must be attached to a node inside a declared ingress's reachable closure: an annotation
  nothing reaches is orphaned and refused;
* every ``$defs`` definition inside a declared closure that the core validates carries its own
  annotation — a definition can never slip out of the harness by riding in unannotated;
* an ingress that names a property holds a member its own object requires;
* every ``x-core-validator`` names a module and a callable of the plugin's packages that exist;
* every ``x-core-only`` entry declares its rule, the one ``path::Class.test`` that pins it — and that
  test exists — and its ``case``, when it excuses a differential candidate kind, names one of the
  harness's declared kinds (``contract_strategies.CASE_KINDS``);
* every ``x-grammar`` piece compiles, and a grammar rides a node that carries the pattern it refines;
* every schema keyword the closures use is one the strategy engines derive (``ui_view_strategies.KNOWN``
  plus the annotation keywords) — a definition that grows a keyword the engines cannot draw fails here.

The harness (plugins/empirica/tests/test_contract_conformance.py) exercises what these annotations
declare; this script checks what they are. It reads the repository's SSOT contracts, never the vendor
copies.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "scripts", ROOT / "plugins" / "empirica"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from contract_strategies import (CASE_KINDS, X_CORE_ONLY, X_GRAMMAR, X_INGRESS, X_VALIDATOR,  # noqa: E402
                                 Contract)
import validate_contracts  # noqa: E402
from ui_view_strategies import KNOWN  # noqa: E402

ANNOTATIONS = (X_VALIDATOR, X_CORE_ONLY, X_GRAMMAR)
KNOWN_KEYWORDS = KNOWN | set(ANNOTATIONS) | {X_INGRESS}


def _discover(document: object, found: list[tuple[str, str]] | None = None,
              seen: set[int] | None = None) -> list[tuple[str, str]]:
    """Every ``(pointer, keyword)`` an annotation keyword appears at, walking the *whole* document —
    schema-valued ``additionalProperties`` included, array rows included, so no annotation placement
    hides from the guard."""
    found = found if found is not None else []
    seen = seen if seen is not None else set()

    def walk(node: object, pointer: str) -> None:
        if not isinstance(node, (dict, list)) or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, dict):
            for word in ANNOTATIONS:
                if word in node:
                    found.append((pointer or "/", word))
            for key, value in node.items():
                if key == "$ref":
                    continue  # a reference is followed by the closure walk, not by discovery
                walk(value, f"{pointer}/{key}".replace("//", "/"))
        else:
            for index, entry in enumerate(node):
                walk(entry, f"{pointer}/{index}")

    walk(document, "")
    return found


def _closure_of(contract: Contract) -> set[str]:
    """Every schema node identity (doc#/pointer) inside the registered closures, all documents."""
    closure: set[str] = set()
    for key in sorted(contract.registered):
        for _, _, node_id in contract._targets(key):
            closure.add(node_id)
        closure.add(key)
    return closure


def _reachable_defs(contract: Contract) -> set[str]:
    """Every ``#/$defs`` key name inside some registered closure, per document name."""
    reached: dict[str, set[str]] = {}
    for key in contract.registered:
        name, _, _ = key.partition("#")
        for other in contract._reachable_defs(key):
            if other.startswith(f"{name}#/$defs/"):
                reached.setdefault(name, set()).add(other)
    return reached


def check(documents_map: dict[str, dict] | None = None, schema: dict | None = None) -> list[str]:
    """The guard's findings, as human-readable lines; an empty list is the pass."""
    try:
        contract = Contract(documents_map, schema)
    except (ValueError, KeyError) as exc:
        return [f"the contracts do not parse as annotations: {exc}"]
    errors: list[str] = list(contract.findings)
    if contract.registered or contract.documents:
        if not contract.registered:
            errors.append("no document registers a core-validated definition: declare an x-core-ingress root "
                          "with its x-core-validator annotation")
    closure = _closure_of(contract)
    closed_defs = _reachable_defs(contract)

    for name in sorted(contract.documents):
        document = contract.documents[name]
        found = _discover(document)
        for pointer, word in found:
            where = f"{name}#{pointer}"
            under = any(where == entry or where.startswith(entry + "/") for entry in closure)
            if under:
                continue
            hint = ("a declared x-core-ingress root, or a definition one reaches" if word == X_VALIDATOR
                    else "the core-validated definition that owns its node" if word == X_CORE_ONLY
                    else "the patterned node it refines inside a registered closure")
            errors.append(f"{where}: an orphaned {word} — it is attached to {hint}")
        if not isinstance(document.get(X_INGRESS), list):
            declared = sorted({word for _, word in found})
            if declared:
                errors.append(f"{name}: the document carries {declared} but declares no x-core-ingress "
                              "core entry point")
            continue
        for entry in document[X_INGRESS]:
            if isinstance(entry, str) and entry.startswith("/properties/"):
                member = entry.rsplit("/", 1)[-1]
                if member not in document.get("required", ()):
                    errors.append(f"{name}#{entry}: a declared ingress member must be required by its object")

    for name, definitions in sorted(closed_defs.items()):
        for definition in sorted(definitions):
            if definition not in contract.registered:
                errors.append(f"{definition}: reachable from a declared core ingress but carries no "
                              "x-core-validator annotation")

    for key, named in sorted(contract.validators.items()):
        module, _, name = named.rpartition(".")
        try:
            imported = __import__(module, fromlist=[name])
        except ImportError as exc:
            errors.append(f"{key}: x-core-validator module {module!r} does not import: {exc}")
            continue
        if not callable(getattr(imported, name, None)):
            errors.append(f"{key}: x-core-validator names no callable of {module}: {name!r}")

    for key in sorted(contract.registered):
        for path, node, node_id in contract._targets(key):
            where = f"{node_id} ({'/'.join(f'{step}:{which}' for step, which in path) or '<root>'})"
            for entry in (node.get(X_CORE_ONLY) or []) if isinstance(node, dict) else []:
                if not isinstance(entry, dict) or not entry.get("rule") or not entry.get("test"):
                    errors.append(f"{node_id}: every x-core-only entry needs a rule and its one test")
                    continue
                declared_case = entry.get("case")
                if declared_case is not None and declared_case not in CASE_KINDS:
                    errors.append(f"{node_id}: an x-core-only case must name a harness-declared candidate "
                                  f"kind ({sorted(CASE_KINDS)}), not {declared_case!r}")
                module_path, _, names = entry["test"].rpartition("::")
                if not module_path or not names or "." not in names:
                    errors.append(f"{node_id}: an x-core-only test must read path::Class.test: "
                                  f"{entry['test']!r}")
                    continue
                target = ROOT / module_path
                if not target.exists():
                    errors.append(f"{node_id}: the named x-core-only test file does not exist: "
                                  f"{module_path!r}")
                    continue
                missing = validate_contracts._test_declared(target, names.split("."))
                if missing:
                    errors.append(f"{node_id}: the named x-core-only test does not exist: "
                                  f"{missing!r} in {module_path!r}")
            grammar = node.get(X_GRAMMAR) if isinstance(node, dict) else None
            if grammar is not None:
                if not isinstance(grammar, dict) or not grammar:
                    errors.append(f"{node_id}: an x-grammar must be a non-empty object of named pieces")
                elif not contract._patterns(node):
                    errors.append(f"{node_id}: an x-grammar rides a node with no pattern to refine")
                for piece, expressed in (grammar or {}).items():
                    if not isinstance(expressed, str):
                        errors.append(f"{node_id}: the x-grammar piece {piece!r} is not a pattern string")
                        continue
                    try:
                        re.compile(expressed)
                    except re.error as exc:
                        errors.append(f"{node_id}: the x-grammar piece {piece!r} does not compile: {exc}")

    for key in sorted(contract.registered):
        errors.extend(_keyword_errors(contract, key))
    return [line for index, line in enumerate(errors) if line not in errors[:index]]


def _keyword_errors(contract: Contract, key: str) -> list[str]:
    """Every keyword the pointer's registered closure uses, checked against what the engines derive."""
    errors: list[str] = []
    seen: set[int] = set()
    name = key.partition("#")[0]

    def walk(node: object, at: str) -> None:
        if not isinstance(node, dict) or id(node) in seen:
            return
        seen.add(id(node))
        for word in sorted(node):
            if word not in KNOWN_KEYWORDS:
                errors.append(f"{at}: a keyword the strategy engines do not derive: {word!r}")
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            walk(contract.node(f"{name}{ref}"), f"{key}{ref}")
        for combinator in ("oneOf", "anyOf", "allOf"):
            for branch in node.get(combinator, ()):
                walk(branch, f"{at}/{combinator}")
        walk(node.get("not"), f"{at}/not")
        for member, sub in node.get("properties", {}).items():
            walk(sub, f"{at}/properties/{member}")
        if isinstance(node.get("items"), dict):
            walk(node["items"], f"{at}/items")
        if isinstance(node.get("additionalProperties"), dict):
            walk(node["additionalProperties"], f"{at}/additionalProperties")
    walk(contract.node(key), key)
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
