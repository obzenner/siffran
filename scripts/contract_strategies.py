"""Differential strategies, derivations and adjudication for the Empirica v2 core-validated definitions.

The ``x-core-validator`` annotations of the ``contracts/empirica/v2`` documents (ADR-0075) name the shapes the
core validates — every ingress root the document declares under ``x-core-ingress``, and every annotated
``$defs`` definition an ingress reaches. This module is the engine behind the harness: it draws a valid
document for each registered definition from the schema's keywords alone (the shared T1 engine,
``ui_view_strategies``), and derives *challenge candidates* from the same keywords by a plan computed from the
keywords alone — never from a verdict. For every candidate both judges then speak, and the harness holds them
to agreement:

* the Draft 2020-12 verdict of the definition's schema, and
* the verdict of the core validator the annotation names — a ``ValueError`` is a refusal; any other exception
  (a ``TypeError`` from tripping an implementation limit, say) is a *failure of the validator*, never a
  refusal, so an accidentally-crashing core is caught, not banked as coverage;

a candidate the schema accepts and the core refuses is allowed only where an ``x-core-only`` entry on exactly
that node declares exactly that candidate kind (a rule no schema can express), or where the declared
open-disagreement registry below names the pair — it is empty at this head: the run/5 digest anchors it
once carried were re-anchored absolutely (07c5f66b), the KnownOpen controls' live pins fired, and the
entries were removed, so no verdict gap hides behind a registry entry. A candidate the schema refuses
and the core accepts is never allowed. Nothing is dropped: every derived candidate is adjudicated.

The kinds come from the keywords: a bound one past and one under, a character outside a pattern substituted
and inserted through it (control, blank, printable-complement, astral and lone-surrogate probes, plus the
``\\n``/``\\r`` suffixes that reach a ``$`` anchor's blind spot), a wrong JSON type, a float with a zeroed
fraction where the core lands a Python int, a value outside a const or enumeration, a missing required key, an
extra key on a closed object, a breach of a ``not.required`` forbidden member, a guard flip across a
conditional pair, a duplicated row, one row under and beyond the item bounds — and, since no JSON document can
carry them but the core must still refuse them, an unsized iterable or a mapping where a sequence is required.
Every plan entry is accounted per (node, keyword): a witness both sides refuse, or an explicit
unsupported/redundant declaration — one firing mutation is never confused with coverage.

Valid directions are derived too: the boundary documents of the keywords' own extrema (array sizes, string
lengths, numeric values), and the branch witnesses a pattern's own grammar enumerates — the engine reads the
pattern's AST and builds one witness per top-level alternative and per present/absent optional group, so every
accepted branch of a grammar (the spike-image lexeme included) is held to the core's word with no hand-written
form, and no witness restates a contract value.  Dev-only: imports Hypothesis, jsonschema and referencing
(``make dev-install``).
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

CONTRACTS = ROOT / "contracts" / "empirica" / "v2"
STATE_SCHEMA_PATH = CONTRACTS / "state.schema.json"
X_VALIDATOR = "x-core-validator"
X_CORE_ONLY = "x-core-only"
X_INGRESS = "x-core-ingress"
X_GRAMMAR = "x-grammar"
#: The candidate kinds an ``x-core-only`` entry may declare (its ``"case"``), the harness's own words.
CASE_KINDS = frozenset({"float-for-int"})
#: A document with no ``case`` keys and no core-only entries covers nothing excusable; keep the registry domain
#: explicit for the annotation guard, which refuses a ``case`` that is not one of these.
#: The declared open disagreements of this head: none. The registry stays the only way a schema/Core
#: verdict gap may be excused instead of failing — the run/5 digest anchors were its two entries while
#: both patterns ended in ``$`` (a trailing line feed passed Draft 2020-12 and the core refused it);
#: 07c5f66b re-anchored both with ``(?![\s\S])``, the mismatch healed, the pins fired, and the
#: entries were removed. A future disagreement re-enters only by declaring its node, kind, and pin
#: here; the differential sweep then holds everything else, and the parity check fails the re-anchored
#: excusal the moment an entry stops matching reality. Keeping the registry empty is the intent
#: of this head: no verdict gap may hide behind it.
KNOWN_OPEN: dict[str, frozenset[str]] = {}
#: Probe characters for pattern challenges, one per family: the newline anchors, C0, DEL, C1, a lone
#: surrogate, the tab, the ASCII blank, the blank set the free-text contract itself writes (doc/testing.md
#: cross-host parity; U+FEFF included), printable-complement probes that reach inside machine classes,
#: a non-ASCII letter, and an astral plane character.
OUTSIDE = ["\n", "\r", "\x00", "\x7f", "\ud800", "\t", " ", "\ufeff", "G", "!",
           "\U0001F600", "\u00a0", "\u1680", "\u2000", "\x1f", "\u0085", "\u009f", "~", "\u00b5",
           "\u4e2d", "\u3000"]
#: Padders that lengthen a string while a pattern may keep holding, tried in this order.
PAD = ["0", "a", ":", "-", "_"]
#: The wrong JSON values a node of another kind can be fed instead, one representative per JSON kind.
WRONG_TYPES = [None, True, 7, 0.5, "text", [], {}, ["row"], {"k": 1}]
#: How a padded row is kept unique for a ``uniqueItems`` array: prefixes, never contract content.
UNIQUIFY = "0"
#: The probe positions a patterned string is challenged at, and the cap so one plan stays bounded.
PROBE_SHAPES = ("substitute-start", "substitute-middle", "substitute-end",
                "insert-start", "insert-end", "suffix-end")
CANDIDATE_CAP = 96
#: The most grammar witnesses one pattern may enumerate.
WITNESS_CAP = 64
#: The most grammar witnesses one pattern seeds challenges from, strided across the enumeration so
#: every end of the witness list (the later branches) is as reachable as the first.
WITNESS_SEEDS = 6
_WHOLE = object()  # _walk's return marker for "the base document itself is the slot"


def documents() -> dict[str, dict]:
    """Every ``contracts/empirica/v2`` schema document by name, sorted — the whole surface the annotations
    may attach to, so a later node's response document registers by declaration, not by harness change."""
    loaded = {}
    for path in sorted(CONTRACTS.glob("*.schema.json")):
        loaded[path.name.removesuffix(".schema.json")] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


#: A module-scope alias: the constructor's ``documents`` parameter shadows the loader's name.
module_documents = documents


def state_schema() -> dict:
    """The run-state contract (contracts/empirica/v2/state.schema.json), as the harness reads it."""
    return json.loads(STATE_SCHEMA_PATH.read_text(encoding="utf-8"))


def resolve(pointer: str) -> list[str]:
    """``#/a/b`` as ``["a", "b"]`` (JSON pointer tokens, ``~0`` and ``~1`` unescaped)."""
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer.removeprefix("#").split("/")]


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


def _walk(base: Any, path: tuple) -> tuple[Any, Any] | None:
    """(parent, key) for the path's slot in the base: the container that holds the node and where. Steps
    descend through array rows too (a field inside an object row is reachable: the walker named it,
    the document walk must follow it) — a nonterminal row step enters the row instead of refusing."""
    node = base
    for index, (step, which) in enumerate(path):
        last = index == len(path) - 1
        if isinstance(node, dict):
            if step != "p" or which not in node:
                return None
            if last:
                return node, which
            node = node[which]
        elif isinstance(node, (list, tuple)):
            if step != "r" or not 0 <= which < len(node):
                return None
            if last:
                return node, which
            node = node[which]
        else:
            return None
    return base, _WHOLE


def _absent_key(node: dict) -> str | None:
    """A key the object cannot hold, deterministic: the first ``unexpected`` its properties do not name —
    bounded: an object that already exhausts the probes yields ``None`` (an explicitly declared unsupported
    extra-key challenge), never a loop."""
    for index in range(len(node.get("properties", {})) + 2):
        name = "unexpected" if index == 0 else f"unexpected{index}"
        if name not in node.get("properties", {}):
            return name
    return None


# --------------------------------------------------------------------------- the pattern synthesizer

try:  # Python 3.11+: the parser the re module itself uses; sre_parse before that.
    from re import _parser as _sre_parser, _constants as _sre
except ImportError:  # pragma: no cover — the dev venv is newer than both
    import sre_parse as _sre_parser  # type: ignore[no-redef]
    import sre_constants as _sre  # type: ignore[no-redef]

_SRE_CATEGORY_REPRESENTATIVES = {
    "CATEGORY_DIGIT": "0", "CATEGORY_WORD": "a", "CATEGORY_SPACE": " ",
    "CATEGORY_NOT_DIGIT": "G", "CATEGORY_NOT_WORD": " ", "CATEGORY_NOT_SPACE": "a",
}


def _char_of(item) -> str | None:
    """The character an IN-item contributes when it names one; ranges contribute their low end."""
    kind, value = item
    if kind is _sre.LITERAL:
        return chr(value)
    if kind is _sre.RANGE:
        return chr(value[0])
    if kind is _sre.CATEGORY:
        for name, representative in _SRE_CATEGORY_REPRESENTATIVES.items():
            if value is getattr(_sre, name, None):
                return representative
    return None


def _in_probes(clause) -> list[str]:
    """Representative members of a character class: the first character of up to two items (or, negated,
    probes from outside the class)."""
    _, value = clause
    negated = any(item[0] is _sre.NEGATE for item in value)
    if negated:
        members = [_char_of(item) for item in value if item[0] is not _sre.NEGATE]
        out = []
        for probe in ("a", "0", "G", "\u4e2d", "\U0001F600"):
            if probe not in members and probe not in out:
                out.append(probe)
        return out[:2]
    firsts: list[str] = []
    for item in value:
        char = _char_of(item)
        if char is not None and char not in firsts:
            firsts.append(char)
    return firsts[:2] or ["a"]


def _witnesses(clause) -> list[str]:
    """Witness strings for one pattern AST clause, splitting at every decision point: each top-level
    alternative, and each optional (zero-minimum) group both present and absent."""
    if clause is None:
        return []
    kind, value = clause
    if kind is _sre.LITERAL:
        return [chr(value)]
    if kind is _sre.NOT_LITERAL:
        return ["a" if chr(value) != "a" else "b"]
    if kind is _sre.ANY:
        return ["a"]
    if kind is _sre.IN:
        return _in_probes(clause)
    if kind is _sre.BRANCH:
        return [witness for branch in value[1] for witness in _sequence_witnesses(branch)]
    if kind is _sre.SUBPATTERN:
        return _sequence_witnesses(value[3])
    if kind in (_sre.MAX_REPEAT, _sre.MIN_REPEAT):
        low, high, sub = value
        counted = [low]
        if high is None or high > low:
            counted.append(low + 1)
        out = []
        for count in counted:
            if count == 0:
                out.append("")
                continue
            pieces = _sequence_witnesses(sub)
            for piece in pieces[:1]:
                out.append(piece * count)
            for piece in pieces[1:2]:
                out.append(piece * max(low, 1))
        return out
    if kind in (_sre.AT, _sre.ASSERT, _sre.ASSERT_NOT):
        return [""]  # zero-width: anchored closures are judged by the fullmatch filter
    return []


def _sequence_witnesses(clause) -> list[str]:
    """The join of one sequence's parts: each part contributes its own branch witnesses (its first joined
    onto every accumulation so far, then each of its further branch witnesses onto the first), bounded."""
    accumulated = [""]
    for part in clause:
        kind = part[0]
        witnesses = _witnesses(part)
        if kind in (_sre.AT, _sre.ASSERT, _sre.ASSERT_NOT):
            continue  # zero-width constructs impose no characters
        if not witnesses:
            return []
        firsts = [joined + witnesses[0] for joined in accumulated]
        extras = [accumulated[0] + witness for witness in witnesses[1:]]
        accumulated = (firsts + extras)[:WITNESS_CAP]
    return accumulated[:WITNESS_CAP]


def pattern_witnesses(pattern: str) -> list[str]:
    """Every witness the pattern's own grammar enumerates — fullmatch-true, lacking surrogates (a lone
    surrogate is a transport-level refusal, not a branch any host accepts), deduplicated, bounded."""
    if not pattern:
        return []
    compiled = re.compile(pattern)
    witnesses: list[str] = []
    for witness in _sequence_witnesses(_sre_parser.parse(pattern, 0)):
        if not witness or any(0xD800 <= ord(char) <= 0xDFFF for char in witness):
            continue
        if compiled.fullmatch(witness) is not None and witness not in witnesses:
            witnesses.append(witness)
        if len(witnesses) >= WITNESS_CAP:
            break
    return witnesses


def pattern_alternatives(pattern: str) -> int:
    """The number of top-level alternatives the pattern splits into — an acceptance-reachability floor."""
    parsed = _sre_parser.parse(pattern, 0)
    for kind, value in parsed:
        if kind is _sre.BRANCH:
            return len(value[1])
    return 1


# --------------------------------------------------------------------------- cases and derived documents


@dataclass
class Case:
    """One challenge kind at one target: ``path`` walks the drawn base to the value to challenge, and
    ``node_id`` names the schema node (its canonical ``$defs`` identity) the annotation rules attach to."""

    label: str
    path: tuple                  # (("p", member)) steps and (("r", 0)) rows; () is the whole base
    kind: str                    # the harness's kind vocabulary (coverage keys quote it)
    keyword: str                 # the schema keyword this entry is the coverage witness of
    node: dict = field(default=None, repr=False)
    node_id: str = ""
    holding: Any = None           # the kind's payload: a key, a count, a character, an impostor document


@dataclass
class Derived:
    """A concrete derived document — any JSON shape, or an in-memory impostor the core must still refuse."""

    label: str
    value: Any
    node_id: str
    kind: str
    keyword: str
    core_only: bool = False


def _at_key(path: tuple) -> str:
    return "/".join(f"{step}:{which}" for step, which in path) or "<root>"


def _representative(node: dict) -> Any:
    """A JSON value of a node's own kind: its const or an enum member, its pattern's first witness, or a
    plain representative of its declared type — never a contract value restated by hand."""
    node = node if isinstance(node, dict) else {}
    if "const" in node:
        return node["const"]
    if "enum" in node and node["enum"]:
        return node["enum"][0]
    patterns = Contract._patterns(node)
    if patterns:
        witnesses = pattern_witnesses(patterns[0])
        if witnesses:
            return witnesses[0]
    types = _node_types(node)
    for kind, candidate in (("string", "text"), ("integer", 1), ("number", 1.5), ("boolean", True),
                            ("array", []), ("object", {})):
        if kind in types or not types:
            return candidate
    return True


def _wrong_types(node: dict) -> list[Any]:
    """One candidate per JSON kind the node forbids, so the coverage is per forbidden kind."""
    types = _node_types(node)
    seen: set[str] = set()
    out = []
    for candidate in WRONG_TYPES:
        kind = _json_type(candidate)
        if kind in seen or _fits(candidate, types):
            continue
        seen.add(kind)
        out.append(candidate)
    return out


def _outside_values(held: Any | list) -> list[Any]:
    """Same-family value probes a const or an enumeration cannot hold — fresh content (never a contract
    word restated), tried until one is distinct from every held value of the same family."""
    pool = list(held) if isinstance(held, (list, tuple)) else [held]
    probes: dict[str, list] = {
        "string": ["outside", "elsewhere", "elsewhere-once-more", "elsewhere-twice"],
        "integer": [sum(map(ord, "outside")), -sum(map(ord, "outside"))],
        "boolean": [True, False],
        "array": [["outside"], []],
        "object": [{"outside": True}],
        "null": [None],
    }
    out = []
    for family, family_probes in probes.items():
        for probe in family_probes:
            if probe not in pool and all(_json_type(member) != family for member in pool):
                out.append(probe)
                break
    return out


def _pattern_probes() -> list[tuple[str, str]]:
    """(operation shape, character) pairs for one patterned string: a substitution through the
    start/middle/end and an insertion at start/end for every probe character, plus the trailing
    suffixes — LF and CR included — that reach an anchored pattern's end-quirk blind spot."""
    probes: list[tuple[str, str]] = []
    for character in OUTSIDE:
        for shape in PROBE_SHAPES:
            probes.append((shape, character))
    return probes


class Contract:
    """One read of the v2 contracts: their registered definitions, their draws, their derivations and the
    adjudication of every derived candidate."""

    def __init__(self, documents: dict[str, dict] | None = None, schema: dict | None = None) -> None:
        self.documents = dict(documents or {}) if documents is not None else module_documents()
        if schema is not None:
            self.documents["state"] = schema
        self.schema = self.documents.get("state", {})
        self.registry = Registry()
        for document in self.documents.values():
            resource = Resource.from_contents(document, default_specification=DRAFT202012)
            self.registry = self.registry.with_resources([(document["$id"], resource)])
        #: registered[key] = node — each ingress-declared root with its annotated validator, and every
        #: annotated ``$defs`` definition an ingress reaches: the definitions the harness tests.
        self.registered: dict[str, dict] = {}
        self.validators: dict[str, str] = {}
        #: declaration-level findings the annotation guard reports and the harness refuses to run through.
        self.findings: list[str] = []
        self._collect()
        self._draft: dict[str, Draft202012Validator] = {}
        self._engines: dict[bool, Any] = {}
        self._cores: dict[str, Any] = {}
        self._case_rules: dict[str, list[dict]] | None = None

    # ---------------------------------------------------------------- declaration collection

    def _collect(self) -> None:
        """Register every ingress root and the annotated definitions the ingresses reach; collect every
        malformed declaration as a finding the guard reports."""
        for name in sorted(self.documents):
            document = self.documents[name]
            ingress = document.get(X_INGRESS)
            if ingress is None:
                continue  # a document without a declaration has no core entry point; the guard judges that
            if not isinstance(ingress, list):
                self.findings.append(f"{name}: x-core-ingress must declare a list of local pointers")
                continue
            for entry in ingress:
                key = f"{name}#{entry}" if isinstance(entry, str) and entry.startswith("/") else None
                if key is None:
                    self.findings.append(f"{name}: an x-core-ingress entry must be a local pointer: {entry!r}")
                    continue
                try:
                    node = self.node(key)
                except (KeyError, TypeError, IndexError):
                    self.findings.append(f"{key}: the declared ingress pointer names no node")
                    continue
                named = node.get(X_VALIDATOR) if isinstance(node, dict) else None
                if not isinstance(named, str) or "." not in named:
                    self.findings.append(f"{key}: the ingress node carries no x-core-validator "
                                         "naming <module>.<callable>")
                    continue
                self.registered[key] = node
                self.validators[key] = named
        for name in sorted(self.documents):
            document = self.documents[name]
            for definition in sorted(document.get("$defs", {})):
                node = document["$defs"][definition]
                if not isinstance(node, dict) or X_VALIDATOR not in node:
                    continue
                key = f"{name}#/$defs/{definition}"
                if key in self.registered:
                    continue
                if any(key in self._reachable_defs(root) for root in self.registered):
                    self.registered[key] = node
                    self.validators[key] = node[X_VALIDATOR]

    def _reachable_defs(self, key: str) -> set[str]:
        """Every ``#/$defs/<name>`` definition inside the key's closure, its references followed
        transitively; the key itself is in its own closure."""
        name, _, local = key.partition("#")
        closure: set[str] = set()
        queue = [key]
        followed: set[str] = set()
        while queue:
            current = queue.pop()
            if current in followed:
                continue
            followed.add(current)
            closure.add(current)
            for ref in _structural_refs(self.node(current)):
                pointed = f"{name}{ref}"  # the ref arrives as "#/$defs/…", so it carries its own '#'
                if pointed not in followed and pointed.startswith(f"{name}#/$defs/"):
                    queue.append(pointed)
        return closure

    # ---------------------------------------------------------------- node addressing

    def node(self, key: str) -> dict:
        """The schema node a full key names (``doc#/local/pointer``); no references are followed."""
        name, _, local = key.partition("#")
        node: Any = self.documents[name]
        for token in (token.replace("~1", "/").replace("~0", "~") for token in local.lstrip("/").split("/") if token):
            node = node[token]
        return node

    def dereferenced(self, node: dict, name: str = "state") -> dict:
        """The node with its local ``$ref`` levels followed inside its own document."""
        seen = 0
        while isinstance(node, dict) and "$ref" in node:
            fragment = node["$ref"].partition("#")[2]
            node = self.node(f"{name}#{fragment}")
            seen += 1
            if seen > 8:
                raise ValueError(f"a $ref chain that does not end: {fragment}")
        return node

    def full_pointer(self, key: str) -> str:
        """The key's canonical node identity, as written (``doc#/local/pointer``)."""
        return key

    # ---------------------------------------------------------------- the judges

    def accepts(self, key: str, document: Any) -> bool:
        """Draft 2020-12: whether ``document`` is valid for the node a key names. The draft ignores the
        ``x-`` annotation keywords by specification, so an annotated schema validates exactly its plain
        self."""
        if key not in self._draft:
            name, _, local = key.partition("#")
            self._draft[key] = Draft202012Validator(
                {"$ref": f"{self.documents[name]['$id']}#{local}"}, registry=self.registry)
        return not bool(list(self._draft[key].iter_errors(document)))

    def core_callable(self, key: str) -> tuple[str, str]:
        """The annotated validator split as (module, callable), exactly as the contract names it."""
        module, _, called = self.validators[key].rpartition(".")
        return module, called

    def import_validator(self, key: str) -> Any:
        """The annotated validator itself, imported once from the plugin's packages."""
        if key not in self._cores:
            module, name = self.core_callable(key)
            imported = __import__(module, fromlist=[name])
            self._cores[key] = getattr(imported, name)
        return self._cores[key]

    def verdicts(self, key: str, document: Any) -> tuple[bool, str, BaseException | None]:
        """Both judges' decisions: ``(schema accepts, core verdict, the exception that spoke beyond the
        domain)``. The core verdict is ``accept``/``refuse``/``non-domain``: only a ``ValueError`` family
        exception is a refusal — anything else a validator raises is an implementation failure the
        harness reports, never a refusal it can bank coverage on."""
        schema_accepts = self.accepts(key, document)
        try:
            self.import_validator(key)(document)
        except ValueError:
            return schema_accepts, "refuse", None
        except BaseException as exc:  # a non-domain crash is a finding, not a refusal
            return schema_accepts, "non-domain", exc
        return schema_accepts, "accept", None

    def core_only_cases(self, node_id: str, kind: str) -> list[dict]:
        """Every ``x-core-only`` entry inside the registered closures whose ``case`` names exactly this
        candidate kind at this node."""
        if self._case_rules is None:
            rules: dict[str, list[dict]] = {}
            for key in sorted(self.registered):
                for _, node, target_id in self._targets(key):
                    for entry in (node.get(X_CORE_ONLY) or []) if isinstance(node, dict) else []:
                        if isinstance(entry, dict) and isinstance(entry.get("case"), str):
                            rules.setdefault(target_id, []).append(entry)
            self._case_rules = rules
        return [entry for entry in self._case_rules.get(node_id, ())
                if entry.get("case") == kind]

    # ---------------------------------------------------------------- a valid document, drawn

    def drawn(self, key: str, *, full: bool = False) -> st.SearchStrategy:
        """A valid document for the node a key names, drawn by the shared T1 engine over its keywords.
        (Dev-only: it runs under the .venv interpreter, as every Hypothesis module does — doc/testing.md T1.)"""
        name, _, local = key.partition("#")
        if full not in self._engines:

            class _Engine(Strategies):
                EXTRA = frozenset({X_VALIDATOR, X_CORE_ONLY, X_GRAMMAR})

                def __init__(self, contract: "Contract") -> None:
                    super().__init__(dict(contract.documents), full=full)
                    self.known |= self.EXTRA

            self._engines[full] = _Engine(self)
        engine = self._engines[full]
        return engine.node(self.node(key), self.documents[name], f"#{local}")

    # ---------------------------------------------------------------- the targets

    def _targets(self, key: str) -> list[tuple[tuple, dict, str]]:
        """Every (path, node, node_id) a keyword's challenge or boundary attaches to, ``$ref`` followed,
        one entry per node identity and path: properties, ``items`` rows (nested rows included, so a field
        inside an array row is reachable), schema-valued ``additionalProperties``, ``oneOf``/``anyOf``/``allOf``
        branches and a ``not`` body. A walk into a ``$ref`` target keeps the target's own identity, so the
        annotations inside a shared definition attach where they are written."""
        name, _, local = key.partition("#")
        found: list[tuple[tuple, dict, str]] = []
        visited: set[tuple[str, tuple]] = set()

        def walk(node: dict, path: tuple, node_id: str) -> None:
            if isinstance(node, dict) and "$ref" in node:
                fragment = node["$ref"].partition("#")[2]
                walk(self.node(f"{name}#{fragment}"), path, f"{name}#{fragment}")
                return
            node = node if isinstance(node, dict) else {}
            marker = (node_id, path)
            if marker in visited:
                return
            visited.add(marker)
            found.append((path, node, node_id))
            for word in ("oneOf", "anyOf"):
                for branch in node.get(word, ()):
                    if isinstance(branch, dict):
                        walk(branch, path, f"{node_id}/{word}")
            for member, sub in node.get("properties", {}).items():
                if isinstance(sub, dict):
                    walk(sub, (*path, ("p", member)), f"{node_id}/properties/{member}")
            if isinstance(node.get("not"), dict):
                walk(node["not"], path, f"{node_id}/not")
            if isinstance(node.get("items"), dict):
                walk(node["items"], (*path, ("r", 0)), f"{node_id}/items")
            if isinstance(node.get("additionalProperties"), dict):
                walk(node["additionalProperties"], path, f"{node_id}/additionalProperties")

        walk(self.node(key), (), self.full_pointer(key))
        return found

    @staticmethod
    def _patterns(node: dict) -> list:
        """The node's own pattern and every plain pattern its ``allOf`` adds."""
        out = [node["pattern"]] if "pattern" in node else []
        for entry in node.get("allOf", ()):
            if isinstance(entry, dict) and "pattern" in entry:
                out.append(entry["pattern"])
        return out

    # ---------------------------------------------------------------- the challenge plan

    def cases(self, key: str) -> list[Case]:
        """Every challenge kind the definition's keywords derive, in a deterministic order, each entry
        carrying the keyword it is the coverage witness of. A conditional member — required in one case,
        forbidden otherwise — is supported in the oneOf form ADR-0075 writes it: closed object branches
        sharing one required guard member, the guard a ``const`` where the member is required and a closed
        complementary ``enum`` where the conditional member is forbidden by ``"not": {"required": [...]}``;
        the branches' own required lists supply the missing-member kinds against a base that followed them,
        and the guard flips below derive the breaches that cross the branches. Registered by declaration: an
        annotated definition that uses the form needs no harness change."""
        out: list[Case] = []
        name = key.partition("#")[0]
        for path, node, node_id in self._targets(key):
            at = _at_key(path)
            types = _node_types(node)
            if isinstance(node.get("properties"), dict):
                for member in node.get("required", ()):
                    out.append(Case(f"{at}: missing the required key {member!r}", path, "drop", "required",
                                    node, node_id, member))
                absent = _absent_key(node)
                if node.get("additionalProperties") is False and absent is not None:
                    out.append(Case(f"{at}: an extra key on the closed object", path, "extra",
                                    "additionalProperties", node, node_id, absent))
                elif node.get("additionalProperties") is False:
                    out.append(Case(f"{at}: no fresh key exists to add", path, "unsupported",
                                    "additionalProperties", node, node_id, None))
                for member in node.get("not", {}).get("required", ()):
                    schema_for_member = node.get("properties", {}).get(member, {"type": "boolean"})
                    out.append(Case(f"{at}: the forbidden member {member!r} present", path, "member present",
                                    "not-required", node, node_id,
                                    (member, json.dumps(schema_for_member))))
            if "const" in node:
                for candidate in _outside_values(node["const"]):
                    out.append(Case(f"{at}: a same-family value other than the const", path, "const outside",
                                    "const", node, node_id, candidate))
                    break
            if "enum" in node:
                for candidate in _outside_values(node["enum"]):
                    out.append(Case(f"{at}: a same-family value outside the enumeration", path, "enum outside",
                                    "enum", node, node_id, candidate))
                    break
            for candidate in _wrong_types(node):
                out.append(Case(f"{at}: the wrong type {_json_type(candidate)}", path, "wrong type", "type",
                                node, node_id, candidate))
            if "integer" in types and "number" not in types:
                out.append(Case(f"{at}: a JSON float with a zeroed fraction", path, "float-for-int", "type",
                                node, node_id, None))
            patterns = self._patterns(node)
            if patterns and ("string" in types or not types):
                emitted = 0
                shape = ""
                for lifted_shape, lifted_character in _pattern_probes():
                    if emitted >= CANDIDATE_CAP:
                        break
                    shape, character = lifted_shape, lifted_character
                    if character in ("\n", "\r") and shape in ("insert-end", "suffix-end"):
                        # Insert-at-end *is* the suffix; together these reach an anchored pattern's
                        # end-quirk blind spot, so both carry the registered anchor kinds.
                        kind = "lf-suffix" if character == "\n" else "cr-suffix"
                        out.append(Case(f"{at}: {character!r} suffixed onto the patterned string", path,
                                        kind, "pattern", node, node_id, character))
                    elif shape.startswith(("substitute", "insert")):
                        kind = f"pattern-{shape.split('-')[0]}"
                        position = shape.split("-", 1)[1]
                        operation = {"substitute": "substituted", "insert": "inserted"}[shape.split("-")[0]]
                        label = f"{at}: {character!r} {operation} at the {position}"
                        out.append(Case(label, path, kind, "pattern", node, node_id,
                                       (character, position)))
                    else:
                        out.append(Case(f"{at}: {character!r} appended onto the patterned string", path,
                                        "pattern-append", "pattern", node, node_id, character))
                    emitted += 1
            if "string" in types or patterns:
                if "minLength" in node and node["minLength"] >= 1:
                    out.append(Case(f"{at}: one under minLength", path, "short below", "minLength",
                                    node, node_id, node["minLength"] - 1))
                if "maxLength" in node:
                    out.append(Case(f"{at}: one over maxLength", path, "pad beyond", "maxLength",
                                    node, node_id, node["maxLength"] + 1))
            if "array" in types:
                if "minItems" in node and node["minItems"] >= 1:
                    out.append(Case(f"{at}: one row under minItems", path, "short to", "minItems",
                                    node, node_id, node["minItems"] - 1))
                if "maxItems" in node:
                    out.append(Case(f"{at}: one row over maxItems", path, "rows beyond", "maxItems",
                                    node, node_id, node["maxItems"] + 1))
                if node.get("uniqueItems"):
                    out.append(Case(f"{at}: one duplicated row", path, "duplicate row", "uniqueItems",
                                    node, node_id, None))
                for impostor in ("iterable", "empty mapping", "keyed mapping"):
                    out.append(Case(f"{at}: {impostor} where a sequence is required — core only", path,
                                    "core only", "type", node, node_id, impostor))
            for bound, sign in (("minimum", -1), ("maximum", 1)):
                if bound in node and _json_type(node[bound]) in _node_types(node):
                    out.append(Case(f"{at}: one {bound} bound past", path, "set to", bound,
                                    node, node_id, node[bound] + sign))
            out.extend(self._couplings(node, path, at, node_id, name))
        return out

    @staticmethod
    def _guarded_values(branch: dict, member: str) -> list | None:
        """The closed guard set a branch holds for a member: a const, or its enum; other shapes stay quiet."""
        sub = branch.get("properties", {}).get(member, {})
        if "const" in sub:
            return [sub["const"]]
        if "enum" in sub:
            return list(sub["enum"])
        return None

    def _couplings(self, node: dict, path: tuple, at: str, node_id: str, name: str) -> list[Case]:
        """The guard flips of a conditional pair: a oneOf/anyOf of closed branches that share one required
        guard member with disjoint closed guard sets. A flip sets the guard to another branch's word while
        the drawn base keeps every other member — the derived document wrongs exactly the pairing."""
        branches = [self.dereferenced(branch, name)
                    for branch in (*node.get("oneOf", ()), *node.get("anyOf", ()))
                    if isinstance(branch, dict) and isinstance(branch.get("properties"), dict)]
        if len(branches) < 2:
            return []
        out: list[Case] = []
        for member in sorted(set.intersection(*[set(branch.get("required", ())) for branch in branches])):
            guarded = [self._guarded_values(branch, member) for branch in branches]
            if not all(guarded):
                continue
            every = [json.dumps(value, sort_keys=True) for values in guarded for value in values]
            if len(every) != len(set(every)):
                continue  # overlapping guards are a union, not a conditional: nothing crosses to pin
            for source, values in enumerate(guarded):
                for value in values:
                    out.append(Case(f"{at}: the guard {member!r} flipped to branch {source}'s word",
                                    (*path, ("p", member)), "set to", "oneOf", node,
                                    f"{node_id}/{'oneOf' if 'oneOf' in node else 'anyOf'}", value))
        return out

    # ---------------------------------------------------------------- applying one case

    def derive(self, base: Any, key: str, case: Case) -> list[Derived]:
        """The concrete derived documents one case yields against one drawn full base document."""
        document = copy.deepcopy(base)
        place = _walk(document, case.path)
        if place is None:
            return []
        parent, slot = place
        whole = slot is _WHOLE
        value = document if whole else parent[slot]

        def emit(candidate: Any, case_kind: str = case.kind, label: str = case.label,
                 core_only: bool = False) -> list[Derived]:
            if whole:
                return [Derived(label, copy.deepcopy(candidate), case.node_id, case_kind, case.keyword,
                                core_only)]
            parent[slot] = copy.deepcopy(candidate)
            return [Derived(label, document, case.node_id, case_kind, case.keyword, core_only)]

        target = case.holding
        if case.kind == "drop":
            if isinstance(value, dict) and target in value:
                del value[target]
                return [Derived(case.label, document, case.node_id, case.kind, case.keyword)]
        elif case.kind == "extra":
            if isinstance(value, dict):
                value[target] = True
                return [Derived(case.label, document, case.node_id, case.kind, case.keyword)]
        elif case.kind == "member present":
            member, schema_for_member = target[0], json.loads(target[1])
            name = key.partition("#")[0]
            if isinstance(value, dict) and member not in value:
                value[member] = _representative(self.dereferenced(schema_for_member, name))
                return [Derived(case.label, document, case.node_id, case.kind, case.keyword)]
        elif case.kind == "set to":
            return emit(target)
        elif case.kind in ("const outside", "enum outside", "wrong type"):
            return emit(target)
        elif case.kind == "float-for-int":
            if isinstance(value, int) and not isinstance(value, bool):
                return emit(float(value))
        elif case.kind in ("lf-suffix", "cr-suffix"):
            if isinstance(value, str):
                return emit(value + target)
        elif case.kind == "pattern-substitute":
            character, position = target
            if isinstance(value, str) and value:
                index = {"start": 0, "middle": len(value) // 2, "end": len(value) - 1}[position]
                return emit(value[:index] + character + value[index + 1:])
        elif case.kind == "pattern-insert":
            character, position = target
            if isinstance(value, str):
                index = {"start": 0, "middle": len(value) // 2, "end": len(value)}[position]
                return emit(value[:index] + character + value[index:])
        elif case.kind == "pattern-append":
            if isinstance(value, str):
                return emit(value + target)
        elif case.kind == "short below":
            keeper = value if isinstance(value, str) else "a"
            return emit(keeper[:max(target, 0)])
        elif case.kind == "pad beyond":
            return emit(self._padded(value, case.node, target))
        elif case.kind == "short to":
            if isinstance(value, (list, tuple)) and len(value) > target:
                return emit(list(value)[:max(target, 0)])
        elif case.kind == "rows beyond":
            if isinstance(value, (list, tuple)):
                return emit(self._overgrown(list(value), case.node, target))
        elif case.kind == "duplicate row":
            if isinstance(value, (list, tuple)) and value:
                return emit([*list(value), copy.deepcopy(value[-1])])
        elif case.kind == "core only":
            rows = list(value) if isinstance(value, (list, tuple)) else []
            if target == "iterable":
                return emit(iter(rows), core_only=True)
            if target == "empty mapping":
                return emit({}, core_only=True)
            return emit({"0": rows[0]} if rows else {}, core_only=True)
        return []

    @staticmethod
    def _placed(base: Any, path: tuple, candidate: Any) -> Any:
        """A copy of the base with one slot at the given value."""
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

    # ---------------------------------------------------------------- acceptance boundaries

    def at_bounds(self, key: str, base: Any) -> list[Derived]:
        """The documents of a definition's own boundary keywords, taken exactly at each extremum: arrays
        grown to their ``maxItems`` (uniqueness held, patterns held when they can) and cut to their
        ``minItems``, strings padded to their ``maxLength`` and trimmed to their ``minLength`` the
        pattern-preserving way, and numbers set exactly to their ``minimum``/``maximum``. Sampling never
        promises to reach a bound (drawn arrays stay short by design), so the exact-boundary acceptance is
        derived here, from the same keywords, and asked of both judges — a mutant that narrows a bound
        holds only through the bound nobody ever visits, and these visit it. A definition that declares no
        such extremum yields no boundary document, which is a fact, not a failure."""
        out: list[Derived] = []
        for path, node, node_id in self._targets(key):
            at_key = _at_key(path)
            place = _walk(base, path) if path else (base, _WHOLE)
            if place is None:
                continue
            parent, slot = place
            value = base if slot is _WHOLE else parent[slot]
            if isinstance(value, (list, tuple)) and "maxItems" in node:
                grown = self._overgrown(list(value), node, node["maxItems"])
                if len(grown) == node["maxItems"] and grown != list(value):
                    out.append(Derived(f"{at_key}: grown to maxItems", self._placed(base, path, grown),
                                       node_id, "at-maxItems", "maxItems"))
            if isinstance(value, (list, tuple)) and "minItems" in node and len(value) > node["minItems"]:
                shortened = list(value)[:node["minItems"]]
                out.append(Derived(f"{at_key}: cut to minItems", self._placed(base, path, shortened),
                                   node_id, "at-minItems", "minItems"))
            if isinstance(value, str) and "maxLength" in node:
                padded = self._padded(value, node, node["maxLength"], must_hold=True)
                if padded is not None and padded != value:
                    out.append(Derived(f"{at_key}: padded to maxLength", self._placed(base, path, padded),
                                       node_id, "at-maxLength", "maxLength"))
            if isinstance(value, str) and "minLength" in node and len(value) > node["minLength"]:
                trimmed = _trim_to_hold(value, node, node["minLength"])
                if trimmed is not None:
                    out.append(Derived(f"{at_key}: trimmed to minLength", self._placed(base, path, trimmed),
                                       node_id, "at-minLength", "minLength"))
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                for bound, kind in (("minimum", "at-minimum"), ("maximum", "at-maximum")):
                    if bound in node and value != node[bound]:
                        out.append(Derived(f"{at_key}: set to {bound}",
                                           self._placed(base, path, node[bound]), node_id, kind, bound))
        return out

    # ---------------------------------------------------------------- adjudication

    def adjudicate(self, key: str, derived: Derived) -> str:
        """The status of one derived candidate: ``agree-refused``/``agree-accepted``; ``excused core-only``
        where an ``x-core-only`` rule on the challenged node names exactly this candidate kind; ``open
        disagreement`` for an entry of the declared registry; and the two harness failures —
        ``DISAGREEMENT`` (so italic a disagreement without an excuse) and ``NON-DOMAIN`` (the core crashed
        on a candidate no domain refusal answered). Every candidate is judged; none is dropped."""
        schema_accepts, core_verdict, crash = self.verdicts(key, derived.value)
        if core_verdict == "non-domain":
            return (f"NON-DOMAIN CRASH on {derived.label}: {type(crash).__name__}: "
                    f"{crash!s} — the validator ran into its own limit, and that is a failure, not a refusal")
        if not schema_accepts and core_verdict == "refuse":
            return "agree-refused"
        if schema_accepts and core_verdict == "accept":
            return "agree-accepted"
        if not schema_accepts and core_verdict == "accept":
            return f"DISAGREEMENT (the core accepts what the schema refuses) on {derived.label}"
        if derived.node_id in KNOWN_OPEN and derived.kind in KNOWN_OPEN[derived.node_id]:
            return "open disagreement (a declared registry entry, red until its anchor heals)"
        rules = self.core_only_cases(derived.node_id, derived.kind)
        if rules:
            return "excused core-only (" + "; ".join(entry.get("rule", "")[:80] for entry in rules) + ")"
        return (f"DISAGREEMENT on {derived.label}: the schema accepts and the core refuses, "
                f"and no x-core-only rule names this candidate")

    # ---------------------------------------------------------------- witness-based challenges

    def witness_challenges(self, key: str, base: Any) -> list[Derived]:
        """Challenges seeded from a pattern's own grammar witnesses — strided across the branch
        enumeration, so the later branches (a tag, a bare name) are as reachable as the first: a
        substitution or insertion into a witness form is a challenge against grammar *anatomy* (a broadened
        tag class, a loosened name component), which a hex-only drawn value never reaches. The kinds match
        the plan's vocabulary — the same ``lf-suffix``/``cr-suffix``/``pattern-insert`` — so the declared
        registries and excuses apply to these pairs too."""
        out: list[Derived] = []
        for path, node, node_id in self._targets(key):
            patterns = self._patterns(node)
            if not patterns:
                continue
            place = _walk(base, path) if path else (base, _WHOLE)
            if place is None:
                continue
            witnesses = pattern_witnesses(patterns[0])
            if not witnesses:
                continue
            stride = max(1, len(witnesses) // WITNESS_SEEDS)
            for witness in witnesses[::stride][:WITNESS_SEEDS]:
                for character, kind in (("G", "pattern-insert"), ("!", "pattern-insert"),
                                        ("\n", "lf-suffix"), ("\r", "cr-suffix")):
                    if kind == "pattern-insert":
                        index = 0 if character == "G" else len(witness)  # G through the start, ! at the end
                        seeded = witness[:index] + character + witness[index:]
                        label = (f"{_at_key(path)}: {character!r} inserted into the grammar "
                                 f"witness {witness!r}")
                    else:
                        seeded = witness + character
                        label = f"{_at_key(path)}: {character!r} suffixed onto the grammar witness {witness!r}"
                    out.append(Derived(label, self._placed(base, path, seeded), node_id, kind, "pattern"))
        return out

    # ---------------------------------------------------------------- padding helpers

    @staticmethod
    def _padded(value: Any, node: dict, target: int, must_hold: bool = False) -> str | None:
        """A string of exactly ``target`` characters. With ``must_hold`` (the boundary acceptance) every
        pattern the node holds must still hold — a sha256-prefixed image reference cannot stretch past 64
        hexes under its own lexeme, so the boundary turns to a fresh pad string, and gives up (None) where
        nothing pattern-honest reaches the bound. Without it (deriving an over-bound challenge) the first
        pad wins and the fallback pads plain, length being the point."""
        seed = value if isinstance(value, str) else ""
        patterns = Contract._patterns(node)
        for character in PAD:
            if len(seed) > target:
                break
            candidate = seed + character * (target - len(seed))
            if all(re.search(pattern, candidate) for pattern in patterns):
                return candidate
        for character in PAD:  # the seed's own form cannot stretch: a fresh string of one paddable character
            candidate = character * target
            if all(re.search(pattern, candidate) for pattern in patterns):
                return candidate
        if must_hold:
            return None
        return seed + "0" * max(target - len(seed), 0)

    @staticmethod
    def _overgrown(rows: list, node: dict, target: int) -> list:
        """A row list of exactly ``target`` rows, built in one pass (never a list copy per iteration — the
        growth is bounded by the schema's own bound): copies, prefixed where ``uniqueItems`` demands fresh
        strings."""
        if len(rows) >= target:
            return rows
        grown = list(rows)
        for index in range(len(grown), target):
            extra = copy.deepcopy(rows[0])
            if node.get("uniqueItems") and isinstance(extra, str):
                extra = UNIQUIFY * (target - index) + extra
            grown.append(extra)
        return grown


def _trim_to_hold(value: str, node: dict, target: int) -> str | None:
    """A window of ``value`` of exactly ``target`` characters that still holds the node's patterns."""
    patterns = Contract._patterns(node)
    for start in range(len(value) - target, -1, -1):
        candidate = value[start:start + target]
        if len(candidate) == target and all(re.search(pattern, candidate) for pattern in patterns):
            return candidate
    return None


def _structural_refs(node: object) -> list[str]:
    """Every local ``#/$def`` pointer a node references, transitively scanned as structure (its words,
    properties and rows included) — the reachable-closure walk for registration and guarding."""
    found: list[str] = []
    seen: set[int] = set()

    def walk(current: object) -> None:
        if not isinstance(current, dict) or id(current) in seen:
            return
        seen.add(id(current))
        ref = current.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            found.append(ref)
            return  # the target is walked when it is reached itself
        for word in ("oneOf", "anyOf", "allOf"):
            for branch in current.get(word, ()):
                walk(branch)
        for sub in current.get("properties", {}).values():
            walk(sub)
        walk(current.get("not"))
        walk(current.get("items"))
        walk(current.get("additionalProperties"))

    walk(node)
    return found
