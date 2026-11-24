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

KNOWN = {"$schema", "$id", "$defs", "$ref", "title", "description", "type", "enum", "const", "oneOf",
         "anyOf", "properties", "required", "additionalProperties", "items", "minItems", "maxItems",
         "uniqueItems", "minimum", "maximum", "minLength", "maxLength", "pattern", "allOf", "not",
         "if", "then", "else",
         "x-core-validator", "x-core-only", "x-grammar", "x-core-ingress", "x-work-limit",
         "x-regression-witnesses"}  # the annotation family: shapes stay the generators' word, annotation words the guard's
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
        #: The schema keywords this generator derives; a subclass extends it with its own annotations.
        self.known = KNOWN
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
        unknown = set(node) - self.known
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
        for combinator in ("oneOf", "anyOf"):
            if combinator in node:
                branches = list(enumerate(node[combinator]))
                if self.full and len(branches) > 1:
                    branches = [(n, b) for n, b in branches if b.get("type") != "null"] or branches
                if self.pin is not None:
                    branches = [(n, b) for n, b in branches
                                if self.reaches(b, doc, f"{pointer}/{combinator}/{n}")] or branches
                return st.one_of([self.node(b, doc, f"{pointer}/{combinator}/{n}") for n, b in branches])
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
        subs += [(b, f"{pointer}/anyOf/{n}") for n, b in enumerate(node.get("anyOf", ()))]
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
            return self._string(node, doc, pointer)
        if kind == "array":
            items = node.get("items")
            if items is None:
                raise ValueError(f"{doc['$id']}#{pointer}: an array with no items")
            low = max(node.get("minItems", 0), 1 if self.full else 0)
            return st.lists(self.node(items, doc, f"{pointer}/items"), min_size=low,
                            max_size=min(node.get("maxItems", MAX_ITEMS), max(low, MAX_ITEMS)),
                            unique=bool(node.get("uniqueItems")))
        if kind == "object":
            return self._object(node, doc, pointer)
        raise ValueError(f"{doc['$id']}#{pointer}: type {kind}")

    @staticmethod
    def _string_patterns(node: dict, doc: dict, pointer: str) -> list[str]:
        """A string node's patterns: its own ``pattern``, then every plain pattern its ``allOf`` adds in
        document order (Draft 2020-12 ``search`` semantics). A string node's ``allOf`` may only add
        patterns; anything else is refused."""
        patterns = [node["pattern"]] if "pattern" in node else []
        for entry in node.get("allOf", ()):
            if set(entry) != {"pattern"}:
                raise ValueError(f"{doc['$id']}#{pointer}: an allOf entry this generator does not derive: {sorted(entry)}")
            patterns.append(entry["pattern"])
        return patterns

    def _admits(self, node: dict, value: Any) -> bool:
        """Whether the driver node's own keywords admit a coupled rule's guard word - only such a
        word joins the driver's pinned arm, so a conditional can never grow the guarded member's own
        declared shape. A free-text driver would never draw its guarded word otherwise, and the
        guarded side of the pairing would be unreachable, not merely rare."""
        if "const" in node:
            return value == node["const"]
        if "enum" in node:
            return value in node["enum"]
        if not isinstance(value, str):
            return False
        types = node.get("type")
        types = types if isinstance(types, list) else [types]
        if types and "string" not in types:
            return False
        doc, pointer = {"$id": "<the guarded member>"}, "<the guarded member>"
        if not all(re.search(pattern, value) for pattern in self._string_patterns(node, doc, pointer)):
            return False
        low, high = node.get("minLength", 0), node.get("maxLength")
        return low <= len(value) and (high is None or len(value) <= high)

    def _string(self, node: dict, doc: dict, pointer: str) -> st.SearchStrategy:
        """A string node: its pattern (and every plain pattern its ``allOf`` adds, Draft-2020-12 ``search`` semantics)
        within its length bounds. A string node's ``allOf`` may only add patterns; anything else is refused."""
        patterns = self._string_patterns(node, doc, pointer)
        low, high = node.get("minLength", 0), node.get("maxLength")
        if not patterns:
            return st.text(st.characters(codec="utf-8", exclude_categories=("Cs",)), min_size=low,
                           max_size=(min(max(12, low), high) if high is not None else max(12, low)))
        drawn = st.from_regex(re.compile(patterns[0]), fullmatch=True)
        for extra in patterns[1:]:
            drawn = drawn.filter(lambda s, extra=extra: re.search(extra, s) is not None)
        if high is not None:
            drawn = drawn.filter(lambda s: len(s) <= high)
        if low:
            drawn = drawn.filter(lambda s: len(s) >= low)
        return drawn

    def _implications(self, node: dict, doc: dict, pointer: str) -> list[tuple[str, str, Any, str, dict]]:
        """The member-to-member couplings this node's ``allOf`` states, as one derivation rule set.
        Each entry must be a conditional form this engine fully models, exactly one of:

        * the one-way form: ``if`` a ``{const}`` or a ``{not: {const}}`` on one required member,
          ``then`` a schema for exactly one other member. The contract uses it for the coupling the
          served document must never break (PR 15, A7): ``status == verified`` carries a parsed
          ``empirica.hitl/1`` body, every other status a null one.
        * the two-way form: one entry whose ``if`` carries a ``{const}`` on one member (its
          ``required`` key may stay absent - the row itself requires the driver, so absence never
          happens), with one paired member's schema for ``then`` and the same member's schema for
          ``else``: the paired value is named exactly on the guard's word, and the ``else`` schema
          holds otherwise (the run/5 attempt row's probe presentation, Empirica 7.2 §5.1). The pair
          is derived as two rules sharing one driver - the const rule for the ``then`` side, its
          complement for the ``else`` side - so the coupled drawing covers both directions.

        Any other ``if``/``then`` shape is refused, never guessed: the guards cover every value the
        guarded member's own keywords can draw, or a drawing would be unsatisfiable.
        """
        rules: list[tuple[str, str, Any, str, dict]] = []
        for entry in node.get("allOf", ()):
            if set(entry) == {"if", "then", "else"} and set(entry["if"]) in (
                    {"properties"}, {"properties", "required"}):
                cond, then, otherwise = entry["if"], entry["then"], entry["else"]
                if (len(cond.get("properties", {})) != 1
                        or set(then) != {"properties"} or len(then["properties"]) != 1
                        or set(otherwise) != {"properties"} or len(otherwise["properties"]) != 1):
                    raise ValueError(f"{doc['$id']}#{pointer}: a two-way conditional names one guarded "
                                     "member and one paired member per side, exactly")
                (driver, guard), = cond["properties"].items()
                (named, when_schema), = then["properties"].items()
                (unquoted, else_schema), = otherwise["properties"].items()
                if set(guard) != {"const"} or named != unquoted or named == driver:
                    raise ValueError(f"{doc['$id']}#{pointer}: a two-way guard is a const on one member "
                                     "naming the same paired member on both of its sides")
                rules.append((driver, "const", guard["const"], named, when_schema))
                rules.append((driver, "not", guard["const"], named, else_schema))
                continue
            if set(entry) != {"if", "then"}:
                raise ValueError(f"{doc['$id']}#{pointer}: an allOf entry that is not this contract's conditional")
            cond, then = entry["if"], entry["then"]
            if set(cond) != {"properties", "required"} or set(then) != {"properties"}:
                raise ValueError(f"{doc['$id']}#{pointer}: an allOf guard must name its own member, then one member")
            (member, guard), = cond["properties"].items()
            if cond["required"] != [member]:
                raise ValueError(f"{doc['$id']}#{pointer}: an allOf guard without its own member required")
            if set(guard) == {"const"}:
                mode, value = "const", guard["const"]
            elif set(guard) == {"not"} and set(guard["not"]) == {"const"}:
                mode, value = "not", guard["not"]["const"]
            else:
                raise ValueError(f"{doc['$id']}#{pointer}: an allOf guard is a const or a not-const only")
            (target, schema), = then["properties"].items()
            rules.append((member, mode, value, target, schema))
        return rules

    def _presence_pairing(self, node: dict, doc: dict, pointer: str) -> dict | None:
        """The node-level ``if``/``then``/``else`` presence pairing, exactly as these contracts write
        it (the run view's last failure and the public fault, Empirica 7.2 §5.1): ``if`` one guarded
        member carries one ``{const}`` word — with ``required: [<driver>]`` naming it, so an absent
        driver fails the guard and falls to ``else`` — then the one paired member is present,
        ``else`` it is forbidden. Every other shape is refused, never guessed."""
        if "if" not in node:
            return None
        cond, then, otherwise = node["if"], node.get("then"), node.get("else")
        if set(cond) not in ({"properties"}, {"properties", "required"}):
            raise ValueError(f"{doc['$id']}#{pointer}: a presence guard must name exactly one member")
        (driver, guard), = cond["properties"].items()
        if set(guard) != {"const"}:
            raise ValueError(f"{doc['$id']}#{pointer}: a presence guard is a const, exactly")
        demanded = "required" in cond
        if demanded and cond["required"] != [driver]:
            raise ValueError(f"{doc['$id']}#{pointer}: a presence guard requires only its own member")

        def _side(branch: object, where: str) -> tuple[str, dict, dict | None]:
            if not isinstance(branch, dict):
                raise ValueError(f"{doc['$id']}#{pointer}: a presence pairing must name both its sides")
            if set(branch) == {"required"} and len(branch["required"]) == 1:
                return "present", {"required": branch["required"]}, None
            if (set(branch) == {"not"} and set(branch["not"]) == {"required"}
                    and len(branch["not"]["required"]) == 1):
                return "absent", {"not": branch["not"]}, None
            if set(branch) == {"properties"} and len(branch["properties"]) == 1:
                (member, schema) = next(iter(branch["properties"].items()))
                return "present", {"required": [member]}, schema
            raise ValueError(f"{doc['$id']}#{pointer}: a presence side ({where}) demands or forbids "
                             "one member, exactly")

        then_side, else_side = _side(then, "then"), _side(otherwise, "else")
        both = []
        for kind, shape, schema in (then_side, else_side):
            member = (shape["required"] if kind == "present" else shape["not"]["required"])[0]
            both.append((member, kind))
        if both[0][0] != both[1][0] or both[0][1] == both[1][1]:
            raise ValueError(f"{doc['$id']}#{pointer}: a presence pairing holds one paired member "
                             "present on exactly one of its two sides")
        return {"driver": driver, "word": guard["const"], "demanded": demanded,
                "target": both[0][0], "then": then_side, "else": else_side}

    def _presence_object(self, node: dict, doc: dict, pointer: str,
                         pairing: dict) -> st.SearchStrategy:
        """Draw one document of a presence-coupled object: the driver is drawn from its own member
        node (its guarded word pinned beside the free draw, so both branches stay reachable; a full
        draw never takes an absent driver an optional member allows), and the paired member follows
        the branch that driver value satisfies — present and drawn from the side's schema (or its
        own property node) on ``then``, absent on ``else``."""
        (driver, word, demanded) = (pairing["driver"], pairing["word"], pairing["demanded"])
        paired_side = pairing["then"] if pairing["then"][0] == "present" else pairing["else"]
        target, side_schema = pairing["target"], paired_side[2]
        props, required = node.get("properties", {}), set(node.get("required", []))
        if driver not in props:
            raise ValueError(f"{doc['$id']}#{pointer}: a presence pairing over a member its own "
                             "object does not hold")
        driver_types = props[driver].get("type")
        if (isinstance(driver_types, list) and "null" in driver_types) or driver_types == "null":
            raise ValueError(f"{doc['$id']}#{pointer}: a presence driver that may itself be null is "
                             "not this form — the pairing cannot tell a value from an absence")
        others = {key: value for key, value in props.items() if key not in (driver, target)}
        fixed = {key: self.node(value, doc, f"{pointer}/properties/{key}")
                 for key, value in others.items() if key in required}
        optional = {key: self.node(value, doc, f"{pointer}/properties/{key}")
                    for key, value in others.items() if key not in required}
        rest = (st.fixed_dictionaries({**fixed, **optional}) if self.full
                else st.fixed_dictionaries(fixed, optional=optional))
        driven = self.node(props[driver], doc, f"{pointer}/properties/{driver}")
        if self._admits(props[driver], word):
            driven = st.one_of(driven, st.just(word))
        if driver not in required and not self.full:
            driven = st.one_of(driven, st.none())  # an absent driver: falls per the guard's demand
        then_side = pairing["then"]
        drawn_from = self.node(side_schema if side_schema is not None else props[target],
                               doc, f"{pointer}/properties/{target}")

        def _chosen(pair: tuple) -> tuple[dict, bool]:
            driver_value, rest_values = pair
            satisfies = (driver_value == word) if driver_value is not None else not demanded
            partial = {**rest_values}
            if driver_value is not None:
                partial[driver] = driver_value
            return partial, (then_side[0] == "present") is satisfies

        def _presence(choice: tuple) -> st.SearchStrategy:
            partial, present = choice
            if not present:
                return st.just(partial)
            return drawn_from.map(lambda value, partial=partial: {**partial, target: value})

        return st.tuples(driven, rest).map(_chosen).flatmap(_presence)

    def _object(self, node: dict, doc: dict, pointer: str) -> st.SearchStrategy:
        forbidden = set(node.get("not", {}).get("required", ()))
        required = set(node.get("required", ()))
        if forbidden & required:
            raise ValueError(f"{doc['$id']}#{pointer}: a member that is both required and forbidden")
        # A ``not: {required: [...]}`` branch (ADR-0075 conditional form): the branch may not carry
        # those members at all, so they are never drawn — a full document excludes a forbidden member.
        props = {key: value for key, value in node.get("properties", {}).items() if key not in forbidden}
        presence = self._presence_pairing(node, doc, pointer)
        if presence is not None:
            return self._presence_object(node, doc, pointer, presence)
        rules = self._implications(node, doc, pointer)
        if rules and len({(rule[0], rule[3]) for rule in rules}) == 1:
            driver, target = rules[0][0], rules[0][3]
            if driver not in required or target not in required or driver not in props:
                raise ValueError(f"{doc['$id']}#{pointer}: a coupling over members its own object does not require")
            others = {k: v for k, v in {**props}.items() if k not in (driver, target)}
            fixed = {k: self.node(v, doc, f"{pointer}/properties/{k}") for k, v in others.items() if k in required}
            optional = {k: self.node(v, doc, f"{pointer}/properties/{k}") for k, v in others.items() if k not in required}
            rest = st.fixed_dictionaries({**fixed, **optional}) if self.full else st.fixed_dictionaries(fixed, optional=optional)
            driven = self.node(props[driver], doc, f"{pointer}/properties/{driver}")
            # every guard word a rule names is pinned into the driver's arm alongside its own draw,
            # so both directions of the pairing stay reachable on a free-text driver too.
            guarded = [rule[2] for rule in rules
                       if rule[1] == "const" and self._admits(props[driver], rule[2])]
            if guarded:
                driven = st.one_of(driven, st.sampled_from(guarded))

            def _coupled(when: tuple[Any, dict[str, Any]]) -> dict:
                a_value, rest_values = when
                for _, mode, value, _, schema in rules:
                    if (a_value == value) if mode == "const" else (a_value != value):
                        return {**rest_values, driver: a_value, **{target: schema}}
                raise ValueError(f"{doc['$id']}#{pointer}: no coupling guard covers {a_value!r}")

            chosen = st.tuples(driven, rest).map(_coupled)
            extra = node.get("additionalProperties", True)
            if extra is not False:
                raise ValueError(f"{doc['$id']}#{pointer}: a coupled object must be closed")
            return chosen.flatmap(lambda drawn: st.builds(
                lambda b, rest, a=drawn[driver]: {**rest, driver: a, target: b},
                self.node(drawn[target], doc, f"{pointer}/properties/{target}"), st.just(drawn)))
        if rules:
            raise ValueError(f"{doc['$id']}#{pointer}: coupling rules over more than one member pair")
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
