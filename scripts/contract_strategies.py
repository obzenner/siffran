"""Hypothesis strategies and invalid-document derivations for the Empirica v2 run-state definitions.

The ``x-core-validator`` annotations of ``contracts/empirica/v2/state.schema.json`` (ADR-0075) name the shapes the
core validates; this module draws a valid document for each annotated definition from the schema's keywords alone
and derives invalid documents from the *same* keywords, so a shape is tested by declaring it, never by restating it.
Valid draws extend the shared T1 engine (``ui_view_strategies``); the invalid half is new: no generator before it
had to disagree with a document.

An invalid document is derived by mutating one drawn *full* base document (every optional member present, every
array non-empty, the null alternative never taken) at one path, from one of the keyword kinds: a bound one past
its maximum or one under its minimum, a character outside a pattern (newline, C0, DEL, C1, a lone surrogate, a
blank), a wrong JSON type, a value outside an enumeration, a missing required key, an extra key on a closed
object, a null row in an array, and — core-side only, since no JSON document can carry them — an unsized iterable
or a mapping where a sequence is required. A derived candidate the schema accepts is not an invalid document
(the schema is the shared word on what a document may say) and is dropped, never asserted.

Python's ``$`` trailing-newline reading of an anchored pattern is codec strictness, not the contract's shape:
``from_regex`` never samples it, and a candidate that reaches it is admitted by Draft 2020-12, so the character
kind never fires on it. Dev-only: it imports Hypothesis and jsonschema (``make dev-install``).
"""
from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from jsonschema import Draft202012Validator  # noqa: E402
from referencing import Registry, Resource  # noqa: E402
from referencing.jsonschema import DRAFT202012  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from ui_view_strategies import Strategies  # noqa: E402

STATE_SCHEMA_PATH = ROOT / "contracts" / "empirica" / "v2" / "state.schema.json"
X_VALIDATOR = "x-core-validator"
X_CORE_ONLY = "x-core-only"
#: A character outside a pattern, tried in this order: newline, C0, DEL, C1, a lone surrogate, blanks.
#: A character outside a pattern, tried in this order: newline, C0, DEL, C1, a lone surrogate, then the
#: blank set the contract itself writes for free text (doc/testing.md cross-host parity; U+FEFF included).
OUTSIDE = ["\n", "\x00", "\x1f", "\x7f", "\u0085", "\u009f", "\ud800", "\t", " ", "\r", "\x0b", "\x0c",
           "\u00a0", "\u1680", "\u2000", "\u200a", "\u2028", "\u2029", "\u202f", "\u205f",
           "\u3000", "\ufeff"]
#: Padders that lengthen a string while a pattern may keep holding, tried in this order.
PAD = ["0", "a", ":", "-", "_"]
#: How a padded row is kept unique for a ``uniqueItems`` array: prefixes, never contract content.
UNIQUIFY = "0"
#: The wrong JSON values a node of another kind can be fed instead.
WRONG_TYPES = [7, True, [], {}]
_WHOLE = object()  # _walk's return marker for "the base document itself is the slot"


def state_schema() -> dict:
    """The run-state contract (contracts/empirica/v2/state.schema.json), as the harness reads it."""
    return json.loads(STATE_SCHEMA_PATH.read_text(encoding="utf-8"))


def resolve(pointer: str) -> list[str]:
    """``#/a/b`` as ``["a", "b"]`` (JSON pointer tokens, ``~0`` and ``~1`` unescaped)."""
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer.removeprefix("#/").split("/")]


def _json_type(value: Any) -> str:
    """The Draft 2020-12 type name of a pure-JSON value (a bool is never an integer there)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, float):
        return "number"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, list):
        return "array"
    return "object"


def _fits(value: Any, types: list) -> bool:
    """Whether a JSON-schema ``type`` list admits the value (``number`` covers every integer)."""
    kind = _json_type(value)
    return kind in types or (kind == "integer" and "number" in types)


def _node_types(node: dict) -> list:
    types = node.get("type")
    if types is None:
        return []
    return types if isinstance(types, list) else [types]


def _wrong_types(node: dict) -> list[Any]:
    """The candidates of a JSON kind the node forbids; null only where the node refuses it."""
    types = _node_types(node)
    out = [c for c in WRONG_TYPES if not _fits(c, types)]
    if not types or "null" not in types:
        out.append(None)
    return out


@dataclass
class Case:
    """One invalid-document kind at one target: ``path`` walks the drawn base to the value to mutate."""

    label: str
    path: tuple                  # (("p", member)) steps and (("r", index)) rows; () is the whole base
    kind: str                    # drop | extra | set to | outside | append | whole | short below | ...
    node: dict = field(default=None, repr=False)   # the schema node the kind came from
    holding: Any = None           # the kind's payload: a key, a count, a character, an impostor document


@dataclass
class Derived:
    """A concrete derived document — JSON-invalid, or, ``core_only``, an in-memory impostor."""

    label: str
    value: Any
    core_only: bool = False


def _walk(base: Any, path: tuple) -> tuple[Any, Any] | None:
    """(parent, key) for the path's slot in the base: the container that holds the node and where."""
    node = base
    for index, (step, which) in enumerate(path):
        if isinstance(node, dict):
            if step != "p" or which not in node:
                return None
        elif isinstance(node, (list, tuple)):
            if step != "r" or not 0 <= which < len(node) or index != len(path) - 1:
                return None
            return node, which
        else:
            return None
        if index == len(path) - 1:
            return node, which
        node = node[which]
    return base, _WHOLE


def _absent_key(node: dict) -> str:
    """A key the object cannot hold, deterministic: the first ``unexpected`` my-wise outside its properties."""
    name = "unexpected"
    while name in node.get("properties", {}):
        name = "unexpected" + str(len(node.get("properties", {})))
    return name


class Contract:
    """One read of the run-state contract: its annotated definitions, their draws, and their derivations."""

    def __init__(self, schema: dict | None = None) -> None:
        self.schema = schema or state_schema()
        self.annotated: dict[str, dict] = {}
        self.validators: dict[str, str] = {}
        self.core_only: dict[str, list] = {}
        for holder, entries in (("properties", self.schema.get("properties", {})),
                                ("$defs", self.schema.get("$defs", {}))):
            for name, node in entries.items():
                if isinstance(node, dict) and X_VALIDATOR in node:
                    pointer = f"#/{holder}/{name}"
                    named = node[X_VALIDATOR]
                    if not isinstance(named, str) or "." not in named:
                        raise ValueError(f"{pointer}: x-core-validator must name <module>.<callable>")
                    self.annotated[pointer] = node
                    self.validators[pointer] = named
                    if node.get(X_CORE_ONLY):
                        if not isinstance(node[X_CORE_ONLY], list):
                            raise ValueError(f"{pointer}: x-core-only must be a list of {{rule, test}} entries")
                        for record in node[X_CORE_ONLY]:
                            if not isinstance(record, dict) or not record.get("rule") or not record.get("test"):
                                raise ValueError(f"{pointer}: every x-core-only entry needs a rule and its one test")
                        self.core_only[pointer] = node[X_CORE_ONLY]
        if not self.annotated:
            raise ValueError("state.schema.json declares no x-core-validator annotation")
        resource = Resource.from_contents(self.schema, default_specification=DRAFT202012)
        self.registry = Registry().with_resources([(self.schema["$id"], resource)])
        self._draft: dict[str, Draft202012Validator] = {}
        self._engines: dict[bool, Any] = {}

    def node(self, pointer: str) -> dict:
        """The schema node a pointer names; no references are followed."""
        node: dict = self.schema
        for token in resolve(pointer):
            node = node[token]
        return node

    def accepts(self, pointer: str, document: Any) -> bool:
        """Draft 2020-12: whether ``document`` is valid for the node a pointer names. The draft ignores the
        ``x-`` annotation keywords by specification, so an annotated schema validates exactly its plain self."""
        if pointer not in self._draft:
            self._draft[pointer] = Draft202012Validator(
                {"$ref": self.schema["$id"] + pointer}, registry=self.registry)
        return not bool(list(self._draft[pointer].iter_errors(document)))

    def core_callable(self, pointer: str) -> tuple[str, str]:
        """The annotated validator split as (module, callable), exactly as the contract names it."""
        module, _, name = self.validators[pointer].rpartition(".")
        return module, name

    def import_validator(self, pointer: str) -> Any:
        """The annotated validator itself, imported from the plugin's packages."""
        module, name = self.core_callable(pointer)
        imported = __import__(module, fromlist=[name])
        return getattr(imported, name)

    # ---------------------------------------------------------------- a valid document, drawn

    def drawn(self, pointer: str, *, full: bool = False) -> st.SearchStrategy:
        """A valid document for the node a pointer names, drawn by the shared T1 engine over its keywords.
        (Dev-only: it runs under the .venv interpreter, as every Hypothesis module does — doc/testing.md T1.)"""
        if full not in self._engines:

            class _Engine(Strategies):
                EXTRA = frozenset({X_VALIDATOR, X_CORE_ONLY})

                def __init__(self, contract: Contract) -> None:
                    super().__init__({"state": contract.schema}, full=full)
                    self.known = self.known | self.EXTRA

            self._engines[full] = _Engine(self)
        engine = self._engines[full]
        return engine.node(self.node(pointer), self.schema, pointer)

    # ---------------------------------------------------------------- the targets and cases

    def dereferenced(self, node: dict) -> dict:
        """The node with its local ``$ref`` levels followed (the state schema is one document)."""
        seen = 0
        while "$ref" in node:
            fragment = node["$ref"].partition("#")[2]
            target: dict = self.schema
            for part in (p for p in fragment.lstrip("/").split("/") if p):
                target = target[part]
            node = target
            seen += 1
            if seen > 8:
                raise ValueError(f"a $ref chain that does not end: {fragment}")
        return node

    def _targets(self, pointer: str) -> list[tuple[tuple, dict]]:
        """Every (path, node) a keyword can be derived from, ``$ref`` followed, around the whole definition."""
        found: list[tuple[tuple, dict]] = []

        def walk(node: dict, path: tuple) -> None:
            node = self.dereferenced(node)
            if any(path == seen and node is known for seen, known in found):
                return
            found.append((path, node))
            for word in ("oneOf", "anyOf"):
                for branch in node.get(word, ()):
                    if isinstance(branch, dict) and branch.get("type") != "null":
                        walk(branch, path)
            for name, sub in node.get("properties", {}).items():
                if isinstance(sub, dict):
                    walk(sub, (*path, ("p", name)))
            if isinstance(node.get("items"), dict):
                walk(node["items"], (*path, ("r", 0)))

        walk(self.node(pointer), ())
        return found

    @staticmethod
    def _patterns(node: dict) -> list:
        out = [node["pattern"]] if "pattern" in node else []
        for entry in node.get("allOf", ()):
            if isinstance(entry, dict) and "pattern" in entry:
                out.append(entry["pattern"])
        return out

    def cases(self, pointer: str) -> list[Case]:
        """Every invalid-document kind the definition's keywords derive, in a deterministic order.

        A conditional member — required in one case, forbidden otherwise — is supported in the oneOf form
        ADR-0075 writes it: closed object branches sharing one required guard member, the guard a ``const``
        where the member is required and a closed complementary ``enum`` where the conditional member is
        forbidden by ``"not": {"required": [...]}``. The branches' own required lists already supply the
        missing-member kinds against a base that followed them, and the guard flips below derive the
        breaches that cross the branches: the guard value turned, the conditional member left as drawn.
        Registered by declaration: an annotated definition that uses the form needs no harness change.
        """
        out: list[Case] = []
        for path, node in self._targets(pointer):
            at = "/".join(f"{step}:{which}" for step, which in path) or "<root>"
            types = _node_types(node)
            if isinstance(node.get("properties"), dict):
                for name in node.get("required", ()):
                    out.append(Case(f"{at}: missing the required key {name!r}", path, "drop", node, name))
                if node.get("additionalProperties") is False:
                    out.append(Case(f"{at}: an extra key on the closed object", path, "extra", node,
                                    _absent_key(node)))
            if "enum" in node:
                out.append(Case(f"{at}: a value outside the enumeration", path, "outside", node))
            for candidate in _wrong_types(node):
                out.append(Case(f"{at}: the wrong type {_json_type(candidate)}", path, "set to", node, candidate))
            patterns = self._patterns(node)
            if "string" in types or patterns:
                for character in OUTSIDE:
                    if patterns:
                        out.append(Case(f"{at}: {character!r} appended outside a pattern", path, "append", node,
                                        character))
                        out.append(Case(f"{at}: {character!r} the whole string", path, "whole", node, character))
                if "minLength" in node and node["minLength"] >= 1:
                    out.append(Case(f"{at}: one under minLength", path, "short below", node, node["minLength"] - 1))
                if "maxLength" in node:
                    out.append(Case(f"{at}: one over maxLength", path, "pad beyond", node, node["maxLength"] + 1))
            if "array" in types:
                if "minItems" in node and node["minItems"] >= 1:
                    out.append(Case(f"{at}: one row under minItems", path, "short to", node, node["minItems"] - 1))
                if "maxItems" in node:
                    out.append(Case(f"{at}: one row over maxItems", path, "rows beyond", node, node["maxItems"] + 1))
                item = self.dereferenced(node.get("items", {}))
                if "null" not in _node_types(item) and "enum" not in item:
                    out.append(Case(f"{at}/row0: a null row", path, "null row", node))
                if node.get("uniqueItems"):
                    out.append(Case(f"{at}: one duplicated row", path, "duplicate row", node))
                for impostor in ("iterable", "empty mapping", "keyed mapping"):
                    out.append(Case(f"{at}: {impostor} where a sequence is required — core only", path,
                                    "core only", node, impostor))
            for bound, sign in (("minimum", -1), ("maximum", 1)):
                if bound in node and _json_type(node[bound]) in _node_types(node):
                    out.append(Case(f"{at}: one {bound} bound past", path, "set to", node, node[bound] + sign))
            out.extend(self._couplings(node, path, at))
        return out

    @staticmethod
    def _guarded_values(branch: dict, member: str) -> list | None:
        """The closed guard set a branch holds for a member: a const, or its enum; other shapes stay quiet."""
        sub = branch.get("properties", {}).get(member, {})
        if "const" in sub:
            return [sub["const"]]
        if "enum" in sub:
            return [value for value in sub["enum"]]
        return None

    def _couplings(self, node: dict, path: tuple, at: str) -> list[Case]:
        """The guard flips of a conditional pair: a oneOf/anyOf of closed branches that share one required
        guard member with disjoint closed guard sets. A flip sets the guard to another branch's word while
        the drawn base keeps every other member — the derived document wrongs exactly the pairing."""
        branches = [self.dereferenced(b) for b in (*node.get("oneOf", ()), *node.get("anyOf", ()))
                    if isinstance(b, dict) and isinstance(b.get("properties"), dict)]
        if len(branches) < 2:
            return []
        seen: dict[str, tuple[str, list]] = {}
        out: list[Case] = []
        for member in sorted(set.intersection(*[set(b.get("required", ())) for b in branches])):
            guarded = [self._guarded_values(b, member) for b in branches]
            if not all(guarded):
                continue
            every = [json.dumps(value, sort_keys=True) for values in guarded for value in values]
            if len(every) != len(set(every)):
                continue  # overlapping guards are a union, not a conditional: nothing crosses to pin
            if member in seen:
                continue
            seen[member] = (at, guarded)
            for source, values in enumerate(guarded):
                for value in values:
                    out.append(Case(f"{at}: the guard {member!r} flipped to branch {source}'s word",
                                    (*path, ("p", member)), "set to", node, value))
        return out

    # ---------------------------------------------------------------- applying one case

    def derive(self, base: Any, pointer: str, case: Case) -> list[Derived]:
        """The concrete derived documents one case yields against one drawn full base document."""
        document = copy.deepcopy(base)
        place = _walk(document, case.path)
        if place is None:
            return []
        parent, key = place
        whole = key is _WHOLE
        value = document if whole else parent[key]

        def settled(core_only: bool = False) -> list[Derived]:
            if core_only or not self.accepts(pointer, document):
                return [Derived(case.label, document, core_only)]
            return []

        if case.kind == "drop":
            if isinstance(value, dict) and case.holding in value:
                del value[case.holding]
                return settled()
        elif case.kind == "extra":
            if isinstance(value, dict):
                value[case.holding] = True
                return settled()
        elif case.kind == "set to":
            candidate = case.holding
            if whole:
                document = candidate
            else:
                parent[key] = candidate
            return settled()
        elif case.kind == "outside":
            if isinstance(value, str):
                parent[key] = value + "outside"
                return settled()
        elif case.kind == "append":
            if isinstance(value, str):
                parent[key] = value + case.holding
                return settled()
        elif case.kind == "whole":
            if whole:
                document = case.holding
            else:
                parent[key] = case.holding
            return settled()
        elif case.kind == "short below":
            candidate = "a" * case.holding
            if whole:
                document = candidate
            else:
                parent[key] = candidate
            return settled()
        elif case.kind == "short to":
            if isinstance(value, (list, tuple)) and len(value) > case.holding:
                shortened = list(value)[:case.holding]
                if whole:
                    document = shortened
                else:
                    parent[key] = shortened
                return settled()
        elif case.kind == "pad beyond":
            candidate = self._padded(value, case.node, case.holding)
            if whole:
                document = candidate
            else:
                parent[key] = candidate
            return settled()
        elif case.kind == "rows beyond":
            if isinstance(value, (list, tuple)):
                grown = self._overgrown(list(value), case.node, case.holding)
                if whole:
                    document = grown
                else:
                    parent[key] = grown
                return settled()
        elif case.kind == "null row":
            if isinstance(value, (list, tuple)) and value:
                value[0] = None
                return settled()
        elif case.kind == "duplicate row":
            if isinstance(value, (list, tuple)) and value:
                doubled = [*list(value), copy.deepcopy(value[-1])]
                if whole:
                    document = doubled
                else:
                    parent[key] = doubled
                return settled()
        elif case.kind == "core only":
            rows = list(value) if isinstance(value, (list, tuple)) else []
            if case.holding == "iterable":
                candidate = iter(rows)
            elif case.holding == "empty mapping":
                candidate = {}
            else:
                candidate = {"0": rows[0]} if rows else {}
            if whole:
                document = candidate
            else:
                parent[key] = candidate
            return settled(core_only=True)
        return []

    # ---------------------------------------------------------------- boundary acceptance

    def at_bounds(self, pointer: str, base: Any) -> list[Derived]:
        """The documents of a definition's own boundary keywords, taken exactly at each maximum: arrays
        grown to their ``maxItems`` (uniqueness held, patterns held when they can), strings padded to their
        ``maxLength`` the pattern-preserving way. Sampling never promises to reach a bound (drawn arrays
        stay short by design), so the exact-boundary acceptance is derived here, from the same keywords,
        and asked of the schema and the core alike — a mutant that narrows a bound holds only through the
        bound nobody ever visits, and these visit it."""
        out: list[Derived] = []
        for path, node in self._targets(pointer):
            at_key = "/".join(f"{step}:{which}" for step, which in path) or "<root>"
            place = _walk(base, path) if path else (base, _WHOLE)
            if place is None:
                continue
            parent, key = place
            value = base if key is _WHOLE else parent[key]
            if isinstance(value, (list, tuple)) and "maxItems" in node:
                grown = self._overgrown(list(value), node, node["maxItems"])
                if len(grown) == node["maxItems"] and grown != list(value):
                    out.append(Derived(f"{at_key}: grown to maxItems", self._placed(base, path, grown)))
            if isinstance(value, str) and "maxLength" in node:
                padded = self._padded(value, node, node["maxLength"], must_hold=True)
                if padded is not None and padded != value:
                    out.append(Derived(f"{at_key}: padded to maxLength", self._placed(base, path, padded)))
        return out

    def _placed(self, base: Any, path: tuple, candidate: Any) -> Any:
        """A copy of the base with one slot at its boundary value."""
        if not path:
            return candidate
        document = copy.deepcopy(base)
        place = _walk(document, path)
        if place is None:
            return document
        parent, key = place
        if key is _WHOLE:
            return candidate
        parent[key] = candidate
        return document

    @staticmethod
    def _padded(value: Any, node: dict, target: int, must_hold: bool = False) -> str | None:
        """A string of exactly ``target`` characters. With ``must_hold`` (the boundary acceptance)
        every pattern the node holds must still hold — a sha256-prefixed image reference cannot
        stretch past 64 hexes under its own lexeme, so the boundary turns to a fresh pad string, and
        gives up (None) where nothing pattern-honest reaches the bound. Without it (deriving an
        over-bound invalid) the first pad wins and the fallback pads plain, length being the point."""
        seed = value if isinstance(value, str) else ""
        patterns = Contract._patterns(node)
        for character in PAD:
            if len(seed) > target:
                break
            candidate = seed + character * (target - len(seed))
            if all(re.search(p, candidate) for p in patterns):
                return candidate
        for character in PAD:  # the seed's own form cannot stretch: a fresh string of one paddable character
            candidate = character * target
            if all(re.search(p, candidate) for p in patterns):
                return candidate
        if must_hold:
            return None
        return seed + "0" * max(target - len(seed), 0)

    @staticmethod
    def _overgrown(rows: list, node: dict, target: int) -> list:
        """A row list of exactly ``target`` rows: copies, prefixed where ``uniqueItems`` demands fresh strings."""
        while len(rows) < target:
            extra = copy.deepcopy(rows[0])
            if node.get("uniqueItems") and isinstance(extra, str):
                extra = UNIQUIFY * (target - len(rows)) + extra
            rows = [*rows, extra]
        return rows
