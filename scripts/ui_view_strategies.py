"""Hypothesis strategies read from the observer read model's v1 JSON Schemas (``contracts/empirica-ui-view/v1``).

One strategy per schema node, derived from its keywords and nothing else, so a value the contract declares is a value
these strategies produce (T1 B): ``document("summary" | "row" | "page" | "run")``. The keywords are the ones the
contract uses (``type`` with ``null``, ``enum``, ``const``, ``oneOf``, ``$ref``, ``properties``/``required``,
``additionalProperties``, ``items``/``minItems``/``maxItems``, ``minimum``/``maximum``, ``minLength``, ``pattern``);
any other is refused, so a schema that grows a keyword fails here instead of being generated loosely. Dev-only: it
imports Hypothesis (``make dev-install``).
"""
from __future__ import annotations

import re
from typing import Any

from hypothesis import strategies as st

from validate_ui_view import view_schemas

KNOWN = {"$schema", "$id", "$defs", "$ref", "title", "description", "type", "enum", "const", "oneOf", "properties",
         "required", "additionalProperties", "items", "minItems", "maxItems", "minimum", "maximum", "minLength", "pattern"}
#: Generated arrays and maps are short: what is checked is the shape, never the volume.
MAX_ITEMS = 3
_TEXT = st.text(st.characters(codec="utf-8", exclude_categories=("Cs",)), max_size=12)


class Strategies:
    """``full``: every optional property present, every array non-empty and a null alternative never taken where
    another exists, so one document reaches every enumeration node (the reachability sweep); otherwise any valid
    document, the empty and absent cases included."""

    def __init__(self, ui: dict[str, dict] | None = None, *, full: bool = False,
                 pin: tuple[str, str, Any] | None = None) -> None:
        """``pin`` (``(schema $id, pointer, value)``): the enumeration at that pointer always takes ``value``, and every
        ``oneOf`` takes only the branches that lead to it, so one document holds that exact value (per-value
        reachability, independent of a sweep's size or seed)."""
        self.full = full
        self.pin = pin
        self.ui = ui or view_schemas()
        self.by_id = {s["$id"]: s for s in self.ui.values()}
        self._memo: dict[tuple[str, str], st.SearchStrategy] = {}

    def document(self, name: str) -> st.SearchStrategy:
        schema = self.ui[name]
        return self.node(schema, schema, "")

    def _resolve(self, ref: str, doc: dict) -> tuple[dict, dict, str]:
        url, _, fragment = ref.partition("#")
        doc = self.by_id[url] if url else doc
        node = doc
        for part in (p for p in fragment.split("/") if p):
            node = node[part]
        return node, doc, fragment

    def node(self, node: dict, doc: dict, pointer: str) -> st.SearchStrategy:
        unknown = set(node) - KNOWN
        if unknown:
            raise ValueError(f"{doc['$id']}#{pointer}: keywords this generator does not derive: {sorted(unknown)}")
        if "$ref" in node:
            target, tdoc, fragment = self._resolve(node["$ref"], doc)
            key = (tdoc["$id"], fragment)
            if key not in self._memo:
                self._memo[key] = st.deferred(lambda: self.node(target, tdoc, fragment))
            return self._memo[key]
        if "const" in node:
            return st.just(node["const"])
        if "enum" in node:
            if self.pin is not None and self.pin[:2] == (doc["$id"], pointer):
                return st.just(self.pin[2])
            return st.sampled_from(node["enum"])
        if "oneOf" in node:
            branches = list(enumerate(node["oneOf"]))
            if self.full and len(branches) > 1:
                branches = [(n, b) for n, b in branches if b.get("type") != "null"] or branches
            if self.pin is not None:
                branches = [(n, b) for n, b in branches if self.reaches(b, doc, f"{pointer}/oneOf/{n}")] or branches
            return st.one_of([self.node(b, doc, f"{pointer}/oneOf/{n}") for n, b in branches])
        types = node.get("type")
        if types is None:
            raise ValueError(f"{doc['$id']}#{pointer}: a node with no type")
        types = types if isinstance(types, list) else [types]
        if self.full and len(types) > 1:
            types = [t for t in types if t != "null"]
        return st.one_of([self._typed(t, node, doc, pointer) for t in types])

    def reaches(self, node: dict, doc: dict, pointer: str, seen: set | None = None) -> bool:
        """Whether the pinned enumeration is at or under ``node`` (references followed once each)."""
        seen = set() if seen is None else seen
        if (doc["$id"], pointer) == self.pin[:2]:
            return True
        if "$ref" in node:
            target, tdoc, fragment = self._resolve(node["$ref"], doc)
            if (tdoc["$id"], fragment) in seen:
                return False
            seen.add((tdoc["$id"], fragment))
            return self.reaches(target, tdoc, fragment, seen)
        subs = [(b, f"{pointer}/oneOf/{n}") for n, b in enumerate(node.get("oneOf", ()))]
        subs += [(v, f"{pointer}/properties/{k}") for k, v in node.get("properties", {}).items()]
        if isinstance(node.get("additionalProperties"), dict):
            subs.append((node["additionalProperties"], f"{pointer}/additionalProperties"))
        if isinstance(node.get("items"), dict):
            subs.append((node["items"], f"{pointer}/items"))
        return any(self.reaches(sub, doc, at, seen) for sub, at in subs)

    def _typed(self, kind: str, node: dict, doc: dict, pointer: str) -> st.SearchStrategy:
        if kind == "null":
            return st.none()
        if kind == "boolean":
            return st.booleans()
        if kind in ("integer", "number"):
            low, high = node.get("minimum"), node.get("maximum")
            ints = st.integers(min_value=None if low is None else int(low), max_value=None if high is None else int(high))
            return ints if kind == "integer" else st.one_of(ints, st.floats(
                min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
        if kind == "string":
            if "pattern" in node:
                return st.from_regex(re.compile(node["pattern"]), fullmatch=True)
            return st.text(st.characters(codec="utf-8", exclude_categories=("Cs",)),
                           min_size=node.get("minLength", 0), max_size=max(12, node.get("minLength", 0)))
        if kind == "array":
            items = node.get("items")
            if items is None:
                raise ValueError(f"{doc['$id']}#{pointer}: an array with no items")
            low = max(node.get("minItems", 0), 1 if self.full else 0)
            return st.lists(self.node(items, doc, f"{pointer}/items"), min_size=low,
                            max_size=min(node.get("maxItems", MAX_ITEMS), max(low, MAX_ITEMS)))
        if kind == "object":
            return self._object(node, doc, pointer)
        raise ValueError(f"{doc['$id']}#{pointer}: type {kind}")

    def _object(self, node: dict, doc: dict, pointer: str) -> st.SearchStrategy:
        props = node.get("properties", {})
        required = set(node.get("required", ()))
        fixed = {k: self.node(v, doc, f"{pointer}/properties/{k}") for k, v in props.items() if k in required}
        optional = {k: self.node(v, doc, f"{pointer}/properties/{k}") for k, v in props.items() if k not in required}
        whole = st.fixed_dictionaries({**fixed, **optional}) if self.full else st.fixed_dictionaries(fixed, optional=optional)
        extra = node.get("additionalProperties", True)
        if isinstance(extra, dict):
            more = st.dictionaries(_TEXT.filter(lambda k: k not in props),
                                   self.node(extra, doc, f"{pointer}/additionalProperties"), max_size=MAX_ITEMS)
            return st.tuples(whole, more).map(lambda pair: {**pair[1], **pair[0]})
        return whole


def declared(ui: dict[str, dict] | None = None) -> dict[str, list]:
    """Every enumeration by ``schema#pointer`` and its values (the validator's own list)."""
    from validate_ui_view import declared_values
    return declared_values(ui)


def held(name: str, value: Any, ui: dict[str, dict] | None = None) -> dict[str, set]:
    """The enumeration values a document holds, by ``schema#pointer`` (the validator's own walk)."""
    import json

    from validate_ui_view import _walk
    ui = ui or view_schemas()
    seen: dict[str, set] = {}
    _walk(ui[name], ui[name], value, "", ui, seen)
    return {k: {json.loads(x) for x in v} for k, v in seen.items()}
