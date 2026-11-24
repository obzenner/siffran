#!/usr/bin/env python3
"""The ADR-0075 static guard for the v2 contracts' core-validation annotations (check-static).

The contract is the single source of a shape, so the annotations themselves are guarded on every fast
gate — on every ``contracts/empirica/v2`` schema document, not only the state document, so a later node's
response ``Fault`` registers by declaration:

* the registration is two independent declarations and they must agree: the contract's
  ``x-core-ingress`` inventory (each entry a well-formed pointer to a node carrying
  ``x-core-validator``, every reachable annotated ``$defs`` registered) and the core's own word in
  ``core.contract_registry`` — deleting a validator and its ingress entry *together* still fails on
  the core's declaration, and undeclaring on the core side fails on the contract's;
* every annotation found anywhere in a document — walked in full: through ``properties``, ``items``
  rows, schema-valued ``additionalProperties``, ``oneOf``/``anyOf``/``allOf`` branches, ``not``, and the
  ``$defs`` — must be attached to a node inside a declared ingress's reachable closure: an annotation
  nothing reaches is orphaned and refused;
* an ``x-core-validator`` placement is a registration, never a decoration: it sits on a declared
  ingress root or a reachable annotated ``$defs`` definition *exactly* — an interior placement (the
  re-review seeded one on a member's revision) is reported whatever name it carries, real or not;
* every ``$defs`` definition inside a declared closure that the core validates carries its own
  annotation — a definition can never slip out of the harness by riding in unannotated;
* an ingress that names a property holds a member its own object requires;
* every ``x-core-validator`` names a module and a callable of the plugin's packages that exist;
* every ``x-core-only`` entry declares its rule, the one ``path::Class.test`` that pins it — and that
  test exists — and its ``case``, when it excuses a differential candidate kind, names one of the
  harness's declared kinds (``contract_strategies.CASE_KINDS``);
* every ``x-grammar`` piece compiles, and a grammar rides a node that carries the pattern it refines;
* every ``x-regression-witnesses`` corpus is a non-empty list of strings each *refused statically by
  the node's own patterns* — a known-defect corpus value the schema still admits is a contract bug —
  and the harness runs each value through both judges;
* every ``x-work-limit`` override names bounds the engine reads (``maxItems``, ``maxLength``), as
  integers at least the node's own bound — it is the feasible-work limit's declared exception;
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

from contract_strategies import (CASE_KINDS, X_CORE_ONLY, X_GRAMMAR, X_INGRESS,  # noqa: E402
                                 X_REGRESSION_WITNESSES, X_VALIDATOR, X_WORK_LIMIT, Contract)
from core import contract_registry  # noqa: E402
import validate_contracts  # noqa: E402
from ui_view_strategies import KNOWN  # noqa: E402

ANNOTATIONS = (X_VALIDATOR, X_CORE_ONLY, X_GRAMMAR, X_REGRESSION_WITNESSES, X_WORK_LIMIT)
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


def _registry_disagreements(contract: Contract) -> list[str]:
    """The ADR-0075 registration is two independent declarations and they must agree: the contract's
    ``x-core-ingress`` inventory (its roots and the annotated ``$defs`` their closures reach) and the
    core's own hand-written word in ``core.contract_registry`` (``plugins/empirica/core/
    contract_registry.py``). Deleting a validator *and* its ingress entry together no longer hides
    the registration — the core's declaration still names the key; undeclaring it here no longer
    hides it either — the contract's inventory still does."""
    try:
        declared = contract_registry.REGISTERED_DEFINITIONS
    except AttributeError as exc:
        return [f"core.contract_registry is malformed (no REGISTERED_DEFINITIONS): {exc}"]
    core_side = {f"{document}#{pointer}" for document, pointer in declared}
    if len(core_side) != len(declared):
        return ["core.contract_registry names one registered key twice"]
    contract_side = set(contract.registered)
    out = []
    for key in sorted(contract_side - core_side):
        out.append(f"{key}: registered by the contract's x-core-ingress inventory but never declared "
                   "by core.contract_registry — the two declarations disagree")
    for key in sorted(core_side - contract_side):
        out.append(f"{key}: declared by core.contract_registry but the contract registers nothing — "
                   "the two declarations disagree (a validator and its ingress entry deleted together "
                   "still fail here)")
    return out


def document_value_at(document: dict, pointer: str, word: str) -> object:
    """The annotation value at a discovered pointer, for reporting ('?' on any traversal trouble)."""
    node: object = document
    for token in (token.replace("~1", "/").replace("~0", "~") for token in pointer.strip("/").split("/") if token):
        if not isinstance(node, dict) or token not in node:
            return "?"
        node = node[token]
    return node.get(word) if isinstance(node, dict) else "?"


def _regression_errors(node: dict, node_id: str) -> list[str]:
    """A named regression-witness corpus (``x-regression-witnesses``): a list of strings, each a
    known-defect value the node's own patterns must refuse statically *here* — a witness the schema
    still accepts is a contract bug, not a test — and the core's verdict is the harness's job
    (the exact ``Python:3.12`` image spelling is the review's finding)."""
    witnesses = node.get(X_REGRESSION_WITNESSES)
    if not isinstance(witnesses, list) or not witnesses:
        return [f"{node_id}: an x-regression-witnesses corpus must be a non-empty list of strings"]
    out = []
    patterns = Contract._patterns(node)
    if not patterns:
        return [f"{node_id}: an x-regression-witnesses corpus rides a node with no pattern to violate"]
    for witness in witnesses:
        if not isinstance(witness, str):
            out.append(f"{node_id}: a regression witness that is not a string: {witness!r}")
            continue
        import re
        if any(re.fullmatch(pattern, witness) for pattern in patterns if isinstance(pattern, str)):
            out.append(f"{node_id}: a named regression witness the pattern still accepts: {witness!r} "
                       "— a known-defect value the contract itself admits is a contract bug")
    return out


def _work_limit_errors(node: dict, node_id: str) -> list[str]:
    """An ``x-work-limit`` override (the feasible-work limit's declared exception): an object of integer
    bounds (``maxItems``, ``maxLength``), riding the very node whose bound it overrides."""
    override = node.get(X_WORK_LIMIT)
    if not isinstance(override, dict) or not override:
        return [f"{node_id}: an x-work-limit override must be a non-empty object of named bounds"]
    out = []
    for bound, value in sorted(override.items()):
        if bound not in ("maxItems", "maxLength"):
            out.append(f"{node_id}: an x-work-limit bound the engine does not read: {bound!r}")
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < node.get(bound, 0):
            out.append(f"{node_id}: an x-work-limit {bound} must be an integer at least the node's own "
                       f"bound, not {value!r}")
    return out


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
    errors.extend(_registry_disagreements(contract))

    for name in sorted(contract.documents):
        document = contract.documents[name]
        found = _discover(document)
        for pointer, word in found:
            where = f"{name}#{pointer}"
            under = any(where == entry or where.startswith(entry + "/") for entry in closure)
            if word == X_VALIDATOR:
                # a validator placement is a *registration*, never a decoration: it sits on an
                # x-core-ingress root or a reachable annotated $defs definition exactly, or it is
                # reported — an interior placement (the review seeded one on a member's revision)
                # is a finding whatever name it carries, and whatever it names must also resolve.
                if where in contract.registered:
                    continue
                errors.append(f"{where}: an x-core-validator placement the inventory never "
                              "registered — it sits neither on a declared x-core-ingress root nor on "
                              "a reachable annotated $defs definition; interior placements are "
                              "reported, whatever they name")
                module, _, callable_name = str(document_value_at(document, pointer, word)).rpartition(".")
                if module:
                    imported = None
                    try:
                        imported = __import__(module, fromlist=[callable_name])
                    except ImportError:
                        pass
                    if imported is not None and not callable(getattr(imported, callable_name, None)):
                        errors.append(f"{where}: the unregistered x-core-validator also names no "
                                      f"callable of {module}: {callable_name!r}")
                continue
            if under:
                continue
            hint = ("the core-validated definition that owns its node" if word == X_CORE_ONLY
                    else "the patterned node it refines inside a registered closure" if word == X_GRAMMAR
                    else "the patterned node it witnesses inside a registered closure" if
                    word == X_REGRESSION_WITNESSES
                    else "the bounded node it overrides inside a registered closure")
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
            if isinstance(node, dict) and X_REGRESSION_WITNESSES in node:
                errors.extend(_regression_errors(node, node_id))
            if isinstance(node, dict) and X_WORK_LIMIT in node:
                errors.extend(_work_limit_errors(node, node_id))
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
