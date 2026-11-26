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
fraction where the core lands a Python int, a same-family value outside a const or enumeration that
the node's remaining keywords admit with only the challenged one removed (a sibling keyword's refusal
is an explicit redundant/unsupported declaration, never coverage), a missing required key, an
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
import math
import json
import re
import sys
from dataclasses import dataclass, field, replace
from conditional_forms import Pairing as _Pairing, parse as parse_conditional
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
X_REGRESSION_WITNESSES = "x-regression-witnesses"
X_WORK_LIMIT = "x-work-limit"
#: The feasible-work limit for generation (T1's bounded work, not the review's unbounded scaling): a
#: registered definition whose own high bounds — an array's ``maxItems``, a string's ``maxLength`` —
#: ask for more rows or characters than these ceilings is refused at load with a finding naming the
#: node and the bound, instead of the harness silently scaling with an arbitrary schema. A definition
#: whose larger work is honest declares an ``x-work-limit`` override (per bound) and is drawn anyway.
FEASIBLE_ROWS = 128
FEASIBLE_CHARS = 8192
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


def _class_holds(items: list, character: str) -> bool:
    """Whether a non-negated character-class's items admit one code point (literals and ranges only)."""
    code = ord(character)
    for kind, value in items:
        if kind is _sre.LITERAL and value == code:
            return True
        if kind is _sre.RANGE and value[0] <= code <= value[1]:
            return True
    return False


def _case_restricted_letters(patterns: list[str]) -> list[tuple[str, str]]:
    """(the letter a case-restricted class holds, its case flip the class does not) pairs, one per
    restricted class of a node's patterns: a lowercase-only ``[a-z0-9]`` name component yields
    ``('a', 'A')`` — the in-place uppercase challenge of *each case-restricted class* (the re-review's
    seeded spelling defect was an uppercase exactly where the grammar admits only lowercase), derived
    from the grammar, never a hand-listed value. Classes that admit both cases have no case to violate."""
    if not (sys.version_info >= (3, 11)):
        import sre_parse  # noqa: F401 — the fallback parser import above already guarded this
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def scan(clause) -> None:
        if clause is None:
            return
        kind, value = clause
        if kind is _sre.IN:
            items = list(value)
            if any(item[0] is _sre.NEGATE or item[0] is _sre.CATEGORY for item in items):
                scan_down(items)
                return
            for held in "zayb":
                flip = held.upper()
                if not (held.isalpha() and flip != held):
                    continue
                if _class_holds(items, held) and not _class_holds(items, flip):
                    if (held, flip) not in seen:
                        seen.add((held, flip))
                        out.append((held, flip))
                    break  # one pair per class: the first letter it holds whose flip it does not
        elif kind in (_sre.BRANCH,):
            for branch in value[1]:
                for part in branch:
                    scan(part)
        elif kind is _sre.SUBPATTERN:
            for part in value[3]:
                scan(part)
        elif kind in (_sre.MAX_REPEAT, _sre.MIN_REPEAT):
            for part in value[2]:
                scan(part)
        elif kind in (_sre.ASSERT, _sre.ASSERT_NOT):
            for part in value[1]:
                scan(part)

    def scan_down(items: list) -> None:
        for kind, value in items:
            if kind is _sre.CATEGORY:
                continue  # a category's case behaviour is the class engine's word, never assumed here

    for pattern in patterns:
        for clause in _sre_parser.parse(pattern, 0):
            scan(clause)
    return out


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
    gate: tuple | None = None     # the (driver, guarded word) of the conditional that holds this
                                  # member: placed for a challenge whose own base drew the other side




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


#: The most substitute candidates one family contributes to the series the isolating check
#: (``Contract._isolating_substitute``) draws from — a series is bounded, never a search.
FAMILY_SERIES_CAP = 8
#: The families whose fresh-value generation is a *finite* check: only booleans and the single JSON
#: null genuinely exhaust (there is no third boolean and no second null) — everything else generates.
EXHAUSTIBLE = {"boolean", "null"}


def _family_substitutes(node: dict, held: Any | list) -> list[Any]:
    """A bounded series of fresh values of the same JavaScript Object Notation types as the held set,
    generated against the finite set — the pool the *isolating* substitute
    (``Contract._isolating_substitute``) draws from: a string family extends its longest value and
    shortens its members (a four-probe enumeration still yields a fresh string), an integer family
    continues above the peak and below the trough, a number family steps to the representables both
    sides of its extremes (``[1.5]`` yields a fresh decimal, never a crash), a *const array* holds one
    array and gets an array series — every length from the empty array up past the tallest, plus one
    row removed and one row added — never a flattening of its members, and objects generate fresh
    objects. A cross-family value challenges ``type``, and the ledger never credits it — cross-type
    challenges are the plan's separate ``wrong type`` kinds. Only the genuinely exhaustible families
    (booleans, null) may return an empty series, and their ``unsupported`` declaration names a real
    exhaustion; a non-empty series the node's sibling keywords refuse outright is the ``redundant``
    declaration's word, never covered."""
    pool = list(held)
    out: list[Any] = []
    for family in sorted({_json_type(member) for member in pool}):
        members = [member for member in pool if _json_type(member) == family]
        series: list[Any] = []
        if family == "string":
            longest = max((member for member in members if isinstance(member, str)), key=len, default="")
            counter = 1
            while len(series) < 3:
                counter += 1
                candidate = f"{longest}x{counter}"
                if candidate not in pool and candidate not in series:
                    series.append(candidate)
                if counter > len(pool) + 3:
                    break  # a fresh counter extension always exists; a held set cannot exhaust them
            for member in sorted((candidate for candidate in members if isinstance(candidate, str)),
                                key=len, reverse=True):
                if len(series) >= FAMILY_SERIES_CAP:
                    break
                piece = member[:-1]
                while piece and piece in pool:
                    piece = piece[:-1]
                if piece and piece not in pool and piece not in series:
                    series.append(piece)
        elif family == "integer":
            plain = [member for member in members
                     if isinstance(member, int) and not isinstance(member, bool)]
            peak, trough = max(plain), min(plain)
            for candidate in (peak + 1, peak + 2, trough - 1, trough - 2):
                if len(series) >= FAMILY_SERIES_CAP:
                    break
                if candidate not in pool and candidate not in series:
                    series.append(candidate)
        elif family == "number":
            plain = [member for member in members
                     if isinstance(member, float)]
            for candidate in (math.nextafter(max(plain), math.inf),
                              math.nextafter(min(plain), -math.inf)):
                if len(series) >= FAMILY_SERIES_CAP:
                    break
                if candidate not in pool and candidate not in series:
                    series.append(candidate)
        elif family == "array":
            arrays = [member for member in members if isinstance(member, list)]
            tallest = max((len(array) for array in arrays), default=-1)
            trials: list[Any] = [[]]
            trials.extend([None] * length for length in range(1, min(tallest + 2, FAMILY_SERIES_CAP)))
            for array in arrays:
                if array:
                    trials.append(array[:-1])
                    trials.append([*array, None])
                trials.append(array[1:])
            for trial in trials:
                if len(series) >= FAMILY_SERIES_CAP:
                    break
                if trial not in pool and trial not in series:
                    series.append(trial)
        elif family == "object":
            counter = 0
            while len(series) < FAMILY_SERIES_CAP:
                candidate = {} if counter == 0 else ({"fresh": None} if counter == 1 else {"fresh": counter - 1})
                counter += 1
                if candidate not in pool and candidate not in series:
                    series.append(candidate)
        else:  # boolean or null: the only exhaustible families — no third boolean, no second null
            for probe in ((True, False) if family == "boolean" else (None,)):
                if probe not in members:
                    series.append(probe)
                    break
        out.extend(series[:FAMILY_SERIES_CAP])
    return out



def witness_of_family(case: "Case") -> bool:
    """Whether a const/enum challenge's candidate is of the keyword's OWN family — the ledger credits a
    keyword only from such a witness. A wrong-family value (an integer where a string constant is
    challenged) is refused by the ``type`` rules and must never stand in for a const/enum witnessship,
    so the generated coverage asserts this on every family-restricted case before crediting. A const
    array's family is ``array`` (its members are not the alternatives)."""
    if case.kind not in ("const outside", "enum outside"):
        return True
    node = case.node
    if not isinstance(node, dict):
        return False
    if "const" in node:
        pool = [node["const"]]
    elif "enum" in node:
        pool = list(node["enum"])
    else:
        return False
    families = {_json_type(member) for member in pool}
    return _json_type(case.holding) in families


def method_name(key: str, taken: set[str]) -> str:
    """The harness test-method name of one registered key — the *document identity is part of the name*:
    the state definition's ``defs_environmentproposal`` and the request definition's never overwrite one
    another (the re-review found the request copy's seven methods silently dropped when the name kept
    only the pointer). A collision — two registered keys whose names normalize alike — is *refused*
    (raised), never silently merged."""
    document, _, pointer = key.partition("#")
    name = (document + "_" + pointer.strip("/")).replace("$defs/", "defs_").replace("properties/", "member_") \
        .replace("-", "").replace("/", "_").lower()
    if not name or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError(f"the registered key {key} names no valid test method")
    if name in taken:
        raise ValueError(f"a generated test-name collision: {key} normalizes like an earlier registered "
                         "key — give the documents distinct names or identifiers")
    taken.add(name)
    return name


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
        noted: set[str] = set()
        for key in sorted(self.registered):
            self.findings.extend(self._feasibility_errors(key, noted))

    def _feasibility_errors(self, key: str, noted: set[str]) -> list[str]:
        """The generation feasibility limit (the two re-reviews' findings): a registered definition whose
        own allocation-driving bounds — an array's ``maxItems``/``minItems``, a string's
        ``maxLength``/``minLength``, *minima included because a minimum allocates on every draw*
        (a ``minLength`` of a million with no ``maxLength`` is the reviewer's seed) — ask for more rows
        or characters than these ceilings is refused at load with a finding naming the node and the
        bound. A definition whose larger work is honest declares an ``x-work-limit`` override."""
        findings: list[str] = []
        for _, node, node_id in self._targets(key):
            if not isinstance(node, dict) or node_id in noted:
                continue  # the shared proposal is every key's neighbour; one refusal suffices
            noted.add(node_id)
            override = node.get(X_WORK_LIMIT) if isinstance(node.get(X_WORK_LIMIT), dict) else {}
            # every allocation-driving bound, minima included: a minLength of a million allocates a
            # million characters a draw, exactly as a maxLength caps one (numbers are constant-size).
            for bound, ceiling in (("maxItems", FEASIBLE_ROWS), ("minItems", FEASIBLE_ROWS),
                                   ("maxLength", FEASIBLE_CHARS), ("minLength", FEASIBLE_CHARS)):
                if isinstance(node.get(bound), int) and not isinstance(node.get(bound), bool):
                    allowed = override.get(bound, ceiling)
                    if not isinstance(allowed, int):
                        findings.append(f"{node_id}: an x-work-limit {bound} that is not an integer: {allowed!r}")
                        continue
                    if node[bound] > allowed:
                        findings.append(f"{node_id}: the feasible-work limit refuses to draw it: "
                                        f"{bound} {node[bound]} exceeds {allowed} — declare an honest "
                                        f"x-work-limit override or lower the bound")
        return findings

    def regression_challenges(self, key: str, base: Any) -> list[Derived]:
        """The named regression-witness corpus of a definition — the bounded list of *evidenced defects*
        the contract itself carries on a patterned node (``x-regression-witnesses``: ``value`` plus its
        defect reference, at most eight per node, all unique), each placed in a drawn base and fed to
        both judges: both must refuse (the reviewed ``Python:3.12`` seed dies here and nowhere else,
        because a defect that normalizes one input is invisible to the generic probes and lives only as
        a named case)."""
        out: list[Derived] = []
        for path, node, node_id in self._targets(key):
            if not isinstance(node, dict):
                continue
            entries = node.get(X_REGRESSION_WITNESSES) or []
            for entry in entries if isinstance(entries, list) else []:
                if not (isinstance(entry, dict) and isinstance(entry.get("value"), str)):
                    continue  # the annotation guard already refuses the malformed corpus
                placed = self._placed(base, path, entry["value"])
                out.append(Derived(f"the named regression witness {entry['value']!r} "
                                   f"({entry.get('defect', 'an unnamed defect')}) at {node_id}",
                                   placed, node_id, "regression witness", "pattern"))
        return out

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
                # each branch keeps the holder's placement path but owns its own identity: a
                # second branch is never dropped as the first one's duplicate (the views' inline
                # object branches — the installed binding, the last failure — stay challengeable),
                # while the same node along two paths still collapses to one marker.
                for index, branch in enumerate(node.get(word, ())):
                    if isinstance(branch, dict):
                        walk(branch, path, f"{node_id}/{word}/{index}")
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
                held = [node["const"]]
                series = _family_substitutes(node, held)
                substitute = self._isolating_substitute(node, "const", series)
                if substitute is not None:
                    out.append(Case(f"{at}: a same-family value other than the const", path, "const outside",
                                    "const", node, node_id, substitute))
                elif series:
                    out.append(Case(f"{at}: the sibling keywords alone admit only the const itself — "
                                    "the const is redundant here", path, "redundant", "const", node,
                                    node_id, None))
                else:
                    out.append(Case(f"{at}: no fresh same-family value exists for the const", path,
                                    "unsupported", "const", node, node_id, None))
            if "enum" in node:
                series = _family_substitutes(node, node["enum"])
                substitute = self._isolating_substitute(node, "enum", series)
                if substitute is not None:
                    out.append(Case(f"{at}: a same-family value outside the enumeration", path, "enum outside",
                                    "enum", node, node_id, substitute))
                elif series:
                    out.append(Case(f"{at}: the sibling keywords alone admit only the enumerated values — "
                                    "the enum is redundant here", path, "redundant", "enum", node,
                                    node_id, None))
                else:
                    out.append(Case(f"{at}: no fresh same-family value exists for the enumeration", path,
                                    "unsupported", "enum", node, node_id, None))
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
            for held, flipped in _case_restricted_letters(patterns) if patterns else ():
                out.append(Case(f"{at}: a case-restricted class violated in place ({held!r} -> {flipped!r})",
                                path, "case-swap", "pattern", node, node_id, (held, flipped)))
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
            out.extend(self._pairing_flips(node, path, at, node_id, name))
        return self._gates_for(key, out)

    def _isolating_substitute(self, node: dict, keyword: str, series: list) -> Any | None:
        """The first same-family candidate the node itself admits with only the challenged keyword
        removed: Draft 2020-12 over the node's remaining keywords — the ``x-`` annotations are ignored
        by the draft, exactly as the test-side word computes it — must accept it, or the refusal the
        sweep would find belongs to a sibling keyword, never to this one (the re-review 3's control: a
        two-row ``const`` beside ``maxItems: 2`` used to feed an always-longer probe both judges
        refused for length, and a core enforcing only ``maxItems`` was silently credited with covering
        ``const``). ``None`` means no candidate isolates the keyword — an empty series is the genuinely
        exhausted family (``unsupported``), a refused one is the siblings' word (``redundant``) —
        and the ledger never credits either with coverage."""
        if not series:
            return None
        rest = {word: value for word, value in node.items() if word != keyword}
        validator = Draft202012Validator(rest)
        for candidate in series:
            try:
                if not list(validator.iter_errors(candidate)):
                    return candidate
            except Exception:
                continue  # a sibling the draft cannot resolve here is no isolation ground
        return None

    @staticmethod
    def _guarded_values(branch: dict, member: str) -> list | None:
        """The closed guard set a branch holds for a member: a const, or its enum; other shapes stay quiet."""
        sub = branch.get("properties", {}).get(member, {})
        if "const" in sub:
            return [sub["const"]]
        if "enum" in sub:
            return list(sub["enum"])
        return None

    def _pairings_of(self, node: object, name: str) -> list[_Pairing]:
        """Every conditional pairing a node carries, read once by the shared parse
        (conditional_forms) — the same parse the drawing engine consumes, so the supported forms
        cannot drift and anything unmodelled refuses here exactly as it refuses there."""
        _, pairings = parse_conditional(node, name, "")
        return pairings

    def _side_ops(self, side: tuple, target: str, props: dict, name: str) -> list[tuple]:
        """The placements one branch of a pairing demands of its paired member, as derive-time
        operations: a present side sets the member (its side schema, or the member's own property
        node when the side demands only ``required``), a value side sets the branch's schema value,
        an absent side takes the member away."""
        kind, schema = side
        if kind == "absent":
            return [("drop", target, None)]
        drawn_from = schema if schema is not None else props.get(target, {"type": "string"})
        return [("set", target, _representative(self.dereferenced(drawn_from, name)))]

    def _pairing_flips(self, node: dict, path: tuple, at: str, node_id: str, name: str) -> list[Case]:
        """The breaches that cross a conditional's branches, derived from the pairing itself (its
        guarded word read from the schema, never restated): the guard turned to its guarded word
        while the paired member stays on its other side, the pair kept beside an unguarded word, and
        — on the presence pairing whose ``if`` demands the driver while the object leaves it optional —
        the driverless row still carrying the guarded side's member (the reasonless fault with a
        probe). Each flip changes at least one member of the drawn side it is applied to, so it fires
        on every base and both judges must refuse it."""
        props = node.get("properties", {})
        out: list[Case] = []
        for pairing in self._pairings_of(node, name):
            else_ops = self._side_ops(pairing.else_side, pairing.target, props, name)
            then_ops = self._side_ops(pairing.then_side, pairing.target, props, name)
            out.append(Case(f"{at}: the {pairing.driver!r} guard flipped to its guarded word while "
                            f"the paired member {pairing.target!r} stays on its other side",
                            path, "pairing flip", "if/then/else", node, node_id,
                            [("set", pairing.driver, pairing.word), *else_ops]))
            other = self._unguarded_word_value(pairing, props)
            if other is not None:
                out.append(Case(f"{at}: the pair from its guarded side kept beside an unguarded "
                                f"{pairing.driver!r} word", path, "pairing flip", "if/then/else",
                                node, node_id, [("set", pairing.driver, other), *then_ops]))
            if (not pairing.at_allOf and pairing.demanded
                    and pairing.driver in props and pairing.driver not in node.get("required", ())
                    and pairing.then_side[0] == "present"):
                out.append(Case(f"{at}: the {pairing.driver!r} member removed while the guarded "
                                f"side's {pairing.target!r} stays — the driverless breach",
                                path, "pairing flip", "if/then/else", node, node_id,
                                [("drop", pairing.driver, None), *then_ops]))
        return out

    def _unguarded_word_value(self, pairing: _Pairing, props: dict) -> Any | None:
        """A driver value on the other side of the guard, read from the driver node's own keywords —
        an enumerated member other than the guarded word, or a pattern's grammar witness — never a
        restated literal; None when the node's own alternatives are exhausted, and the flip that
        needs one is never derived."""
        if pairing.driver not in props:
            return None
        node = self.dereferenced(props[pairing.driver], "state")
        if "enum" in node:
            for value in node["enum"]:
                if value != pairing.word:
                    return value
            return None
        patterns = self._patterns(node)
        if patterns:
            for witness in pattern_witnesses(patterns[0]):
                if witness != pairing.word:
                    return witness
        return None

    def _gates_for(self, key: str, cases: list[Case]) -> list[Case]:
        """Mark every case whose placement lands on a conditional's paired member with that
        conditional's (driver, guarded word): a challenge must be judged inside the branch that
        allows the member to exist at all, so a base that drew the other side gets the guard placed
        for it too — the candidate then wrongs only its own keyword, never the pairing."""
        cache: dict[tuple, list[_Pairing]] = {}

        def object_at(path: tuple) -> list[_Pairing]:
            if path not in cache:
                name = key.partition("#")[0]
                holder = self._node_at(key, path)
                found_here = list(self._pairings_of(holder, name))
                for word in ("oneOf", "anyOf"):
                    # the paired member's own object may live inside a branch the placement path
                    # cannot see (the last failure's failure row), so the pairing lookup walks into
                    # oneOf/anyOf branches just as the target table does — never into allOf, whose
                    # entries are the conditional attributes the holder's own parse already read.
                    for branch in holder.get(word, ()) if isinstance(holder, dict) else ():
                        if isinstance(branch, dict):
                            found_here += self._pairings_of(self.dereferenced(branch, name), name)
                cache[path] = found_here
            return cache[path]

        gated: list[Case] = []
        for case in cases:
            gate = None
            if (case.path and case.path[-1][0] == "p"
                    and case.kind not in ("unsupported", "redundant", "pairing flip")):
                for pairing in object_at(case.path[:-1]):
                    if pairing.target == case.path[-1][1]:
                        gate = (pairing.driver, pairing.word)
            gated.append(replace(case, gate=gate) if gate is not None else case)
        return gated

    def _node_at(self, key: str, path: tuple) -> dict:
        """The schema node a member path walks to, references followed — the pairing lookup's own
        walk, independent of the target table."""
        name = key.partition("#")[0]
        walked: Any = self.node(key)
        for step, which in path:
            if not isinstance(walked, dict):
                return {}
            walked = self.dereferenced(walked, name)
            try:
                walked = walked["properties"][which] if step == "p" else walked["items"]
            except (KeyError, TypeError):
                return {}
        return walked if isinstance(walked, dict) else {}

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
        if place is None and getattr(case, "gate", None) is not None:
            # the challenge lands on a conditional's paired member and this base drew the other
            # side: place the guard for it, so the candidate wrongs only its own keyword. The
            # emit-only kinds (a fresh value into the slot, never a read of what it holds) may
            # create the member itself — the guarded branch demands it; the reading kinds keep
            # skipping, nothing honest to place for them.
            driver, word = case.gate
            holder = _walk(document, case.path[:-1]) if case.path[:-1] else (document, _WHOLE)
            if holder is not None:
                h_parent, h_slot = holder
                holding = document if h_slot is _WHOLE else h_parent[h_slot]
                if isinstance(holding, dict):
                    holding[driver] = copy.deepcopy(word)
                    place = _walk(document, case.path)
                    if (place is None and case.path and case.path[-1][0] == "p"
                            and case.kind in ("set to", "const outside", "enum outside",
                                              "wrong type")):
                        place = (holding, case.path[-1][1])
        if place is None:
            return []
        parent, slot = place
        whole = slot is _WHOLE
        # a gated emit-only challenge may hold a slot the base never drew (the conditional's other
        # side): reading a missing slot yields None so the value-reading kinds skip instead of
        # crashing the sweep, while an array row's slot stays a real index read.
        if whole or (isinstance(parent, (list, tuple))
                     and isinstance(slot, int) and 0 <= slot < len(parent)):
            value = document if whole else parent[slot]
        elif isinstance(parent, dict):
            value = parent.get(slot)
        else:
            value = None

        def emit(candidate: Any, case_kind: str = case.kind, label: str = case.label,
                 core_only: bool = False) -> list[Derived]:
            if whole:
                return [Derived(label, copy.deepcopy(candidate), case.node_id, case_kind, case.keyword,
                                core_only)]
            parent[slot] = copy.deepcopy(candidate)
            return [Derived(label, document, case.node_id, case_kind, case.keyword, core_only)]

        target = case.holding
        if case.kind == "pairing flip":
            # a conditional breach: its operations land on the object that carries the pairing
            # itself (the case's own slot), and one of them always changes the drawn side, so the
            # flip never degrades into the unchanged base.
            carrier = document if whole else parent[slot]
            if not isinstance(carrier, dict):
                return []
            for op, member, value in target:
                if op == "set":
                    carrier[member] = copy.deepcopy(value)
                else:
                    carrier.pop(member, None)
            return [Derived(case.label, document, case.node_id, case.kind, case.keyword)]
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
        elif case.kind == "case-swap":
            held, flipped = target
            if isinstance(value, str):
                index = value.find(held)
                if index >= 0:
                    return emit(value[:index] + flipped + value[index + 1:])
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
        taken = {row for row in grown if isinstance(row, str)}
        for index in range(len(grown), target):
            extra = copy.deepcopy(rows[0])
            if node.get("uniqueItems") and isinstance(extra, str):
                stamp = index
                fresh = f"{UNIQUIFY}{stamp}{extra}"
                while fresh in taken:  # the digits grow logarithmically: total size stays linear
                    stamp += 1
                    fresh = f"{UNIQUIFY}{stamp}{extra}"
                taken.add(fresh)
                extra = fresh
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
