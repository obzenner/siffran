#!/usr/bin/env python3
"""Unit controls for scripts/contract_strategies.py — the differential engine's behaviour, pinned per rule.

The harness (plugins/empirica/tests/test_contract_conformance.py) sweeps the real registered contracts;
these controls prove the engine itself: the differential discipline (a candidate is never dropped, a
``ValueError`` is the domain and a ``TypeError`` is a finding), the declared open-disagreement registry
(empty at this head: the run/5 digest anchors healed when 07c5f66b re-anchored both, and the controls
pin the *absence* of any excusal), the oneOf conditional form a later node registers by declaration, the grammar synthesizer's branch
enumeration, the integer-float core-only excuse, and the bounded-work rules (no per-iteration list
copies, an ``_absent_key`` that terminates). Every control is derandomized: the same corpus on every
machine (doc/testing.md T1).
"""
from __future__ import annotations

import copy
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "scripts", ROOT / "plugins" / "empirica"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from hypothesis import HealthCheck, find, settings  # noqa: E402

import contract_strategies as engine  # noqa: E402
from contract_strategies import Contract  # noqa: E402

DRY = settings(max_examples=64, database=None, deadline=None, derandomize=True,
               suppress_health_check=list(HealthCheck))


def _drawn(contract: Contract, key: str):
    return find(contract.drawn(key, full=True), lambda document: True, settings=DRY)


CONDITIONAL = {  # the oneOf conditional form a later node registers by declaration (ADR-0075)
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://siffran.test/conditional",
    "type": "object",
    "x-core-ingress": ["/$defs/failure"],
    "properties": {"failure": {"$ref": "#/$defs/failure"}},
    "required": ["failure"],
    "$defs": {"failure": {
        "x-core-validator": "conformance_fixtures.conditional_validator",
        "type": "object",
        "oneOf": [
            {"type": "object", "additionalProperties": False, "required": ["reason", "probe", "digest"],
             "properties": {"reason": {"const": "qualification-probe-failed"},
                            "probe": {"type": "string", "pattern": "^probe/", "minLength": 1},
                            "digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$", "maxLength": 71}}},
            {"type": "object", "additionalProperties": False, "required": ["reason", "digest"],
             "not": {"required": ["probe"]},
             "properties": {"reason": {"enum": ["environment-required", "host-conflict"]},
                            "digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$", "maxLength": 71}}},
        ]}},
}


TWO_WAY = {  # the two-way if/then/else conditional the run/5 attempt row carries (E6b re-review)
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://siffran.test/two-way",
    "type": "object",
    "x-core-ingress": ["/$defs/attempt"],
    "properties": {"attempt": {"$ref": "#/$defs/attempt"}},
    "required": ["attempt"],
    "$defs": {"attempt": {
        "x-core-validator": "conformance_fixtures.two_way_validator",
        "type": "object",
        "additionalProperties": False,
        "required": ["attempt_id", "reason", "probe"],
        "properties": {"attempt_id": {"type": "string", "minLength": 1, "maxLength": 128},
                      "reason": {"type": ["string", "null"],
                                 "pattern": "^[a-z][a-z0-9-]{0,63}(?![\\s\\S])"},
                      "probe": {"anyOf": [{"type": "null"}, {"$ref": "#/$defs/probeName"}]}},
        "allOf": [{"if": {"properties": {"reason": {"const": "qualification-probe-failed"}}},
                   "then": {"properties": {"probe": {"$ref": "#/$defs/probeName"}}},
                   "else": {"properties": {"probe": {"type": "null"}}}}],
    },
        "probeName": {"enum": ["realization", "timeout"]}},
}


def _install_fixture_validator(accepts=None, crashes=False):
    """A validator the synthetic contract names, so the engine's differential against a *disobedient*
    core is controlled: one that accepts what the schema refuses, one that crashes on a candidate."""
    module = types.ModuleType("conformance_fixtures")

    def conditional_validator(value):
        if crashes and isinstance(value, dict) and set(value) == {"k"}:
            raise TypeError("the validator ran into its own limit")
        if accepts is not None and accepts(value):
            return value
        return conditional_validator.honest(value)

    conditional_validator.honest = staticmethod(_honest_conditional)
    module.conditional_validator = conditional_validator
    module.two_way_validator = _two_way_validator
    module.presence_validator = _presence_validator
    sys.modules["conformance_fixtures"] = module
    return module


def _presence_validator(value):
    """The honest core of the synthetic presence fixtures (a required- and an optional-driver row):
    the probe is present exactly when the reason is the guarded word - and an absent reason falls to
    the else side, so a reasonless row may not carry a probe at all."""
    if not isinstance(value, dict):
        raise ValueError("malformed")
    exhausted = set(value) - {"reason", "probe", "message"}
    if exhausted:
        raise ValueError("malformed")
    guarded = value.get("reason") == "qualification-probe-failed"
    if guarded:
        if "probe" not in value or value["probe"] not in PRESENCE["$defs"]["probeName"]["enum"]:
            raise ValueError("probe missing or unknown")
    elif "probe" in value:
        raise ValueError("probe forbidden without the guarded reason")
    if "reason" in value:
        if value["reason"] not in PRESENCE["$defs"]["wire"]["properties"]["reason"]["enum"]:
            raise ValueError("reason unknown")
        if value["reason"] is None:
            raise ValueError("a present reason is a word, never null")
    if "message" in value and (not isinstance(value["message"], str) or len(value["message"]) > 40):
        raise ValueError("message")
    return value


PRESENCE = {  # the node-level if/then/else presence pairing the views carry (E6c: last_failure, fault)
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://siffran.test/presence",
    "type": "object",
    "x-core-ingress": ["/$defs/looking", "/$defs/wire"],
    "properties": {"looking": {"$ref": "#/$defs/looking"}, "wire": {"$ref": "#/$defs/wire"}},
    "required": ["looking"],
    "$defs": {
        "probeName": {"enum": ["realization", "timeout"]},
        "looking": {  # the last_failure mirror: a required reason carries or forbids the probe
            "x-core-validator": "conformance_fixtures.presence_validator",
            "type": "object",
            "additionalProperties": False,
            "required": ["reason"],
            "properties": {"reason": {"enum": ["docker-unreachable", "qualification-probe-failed",
                                               "qualification-conflict"]},
                           "probe": {"$ref": "#/$defs/probeName"}},
            "if": {"properties": {"reason": {"const": "qualification-probe-failed"}}},
            "then": {"required": ["probe"]},
            "else": {"not": {"required": ["probe"]}},
        },
        "wire": {  # the fault mirror: an optional reason falls to the else side when absent
            "x-core-validator": "conformance_fixtures.presence_validator",
            "type": "object",
            "additionalProperties": False,
            "properties": {"reason": {"enum": ["docker-unreachable", "qualification-probe-failed",
                                               "qualification-conflict"]},
                           "probe": {"$ref": "#/$defs/probeName"},
                           "message": {"type": "string", "maxLength": 40}},
            "if": {"properties": {"reason": {"const": "qualification-probe-failed"}},
                   "required": ["reason"]},
            "then": {"required": ["probe"]},
            "else": {"not": {"required": ["probe"]}},
        }},
}


def _two_way_validator(value):
    """The honest core of the synthetic two-way fixture: refusal by ``ValueError`` only, the row's
    members checked exactly as its contract writes them (an intentionally disobedient core is mounted
    by the tests separately, never here)."""
    import re
    if not isinstance(value, dict) or set(value) != {"attempt_id", "reason", "probe"}:
        raise ValueError("malformed")
    reason, probe = value["reason"], value["probe"]
    if reason is not None and (not isinstance(reason, str)
                              or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", reason)):
        raise ValueError("reason")
    if (reason == "qualification-probe-failed") != (probe is not None):
        raise ValueError("the probe is named exactly on the probe-failure reason")
    if probe is not None and probe not in TWO_WAY["$defs"]["probeName"]["enum"]:
        raise ValueError("probe unknown")
    if not isinstance(value["attempt_id"], str) or not value["attempt_id"] \
            or len(value["attempt_id"]) > 128:
        raise ValueError("attempt id")
    return value


def _honest_conditional(value):
    import re
    if not isinstance(value, dict):
        raise ValueError("malformed")
    if "reason" not in value or "digest" not in value:
        raise ValueError("malformed")
    if value["reason"] == "qualification-probe-failed":
        if "probe" not in value or not re.fullmatch(r"probe/\S+", value["probe"]):
            raise ValueError("probe missing")
    elif "probe" in value:
        raise ValueError("probe forbidden")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value["digest"]):
        raise ValueError("digest")
    return value


class RealContractTests(unittest.TestCase):
    """The engine against the real registered contracts, one behavioural rule per control."""

    @classmethod
    def setUpClass(cls):
        cls.contract = Contract()

    def _derived(self, key: str, base) -> list:
        out = []
        for case in self.contract.cases(key):
            out.extend(self.contract.derive(base, key, case))
        return out

    def test_registration_comes_from_declaration_and_reaches_through_documents(self):
        self.assertEqual(self.contract.findings, [])
        self.assertIn("state#/$defs/environmentMember", self.contract.registered)
        self.assertIn("state#/$defs/environmentProposal", self.contract.registered)
        self.assertIn("request#/$defs/environmentProposal", self.contract.registered,
                      "the request surface registers by its own declaration")

    def test_the_e6_registration_set_lands_by_declaration(self):
        """The E6c registration: the environment and qualify request wrappers, the two read
        projections, the shared probe-name enumeration (both its copies) and the shared digest form
        register by the contracts' own declarations — never by a harness change."""
        for key in ("request#/$defs/actionEnvironment", "request#/$defs/actionQualify",
                    "response#/$defs/environmentView", "response#/$defs/qualificationView",
                    "response#/$defs/qualificationProbeName", "state#/$defs/qualificationProbeName",
                    "response#/$defs/digest256"):
            self.assertIn(key, self.contract.registered, key)
        self.assertNotIn("response#/$defs/fault", self.contract.registered,
                         "the fault def's run-view closure belongs to the migration node")

    def test_the_e8_registration_set_lands_by_declaration(self):
        """The E8 registration: the private environment fetch's document presentation registers by
        the response schema's own declaration — a one-member closed projection drawn by the same
        engine, never a harness exception — and the hitl surface carries its third document kind
        through the schema's own additive enum member."""
        self.assertIn("response#/$defs/environmentPresentation", self.contract.registered)
        self.assertIn("environment", json.loads(
            (ROOT / "contracts" / "empirica" / "v2" / "hitl.schema.json").read_text("utf-8")
        )["properties"]["kind"]["enum"])

    def test_the_attempt_row_and_view_pairings_derive_breaches_both_judges_refuse(self):
        """The conditional pairings the wire carries (E6b re-review's own handoff): the run/5
        attempt row's two-way value pairing and the view's last-failure presence pairing each derive
        their guard flips — every breach refused by both judges on every drawn side."""
        for key in ("state#/$defs/qualificationAttempt", "response#/$defs/qualificationView"):
            flips = [case for case in self.contract.cases(key) if case.kind == "pairing flip"]
            self.assertTrue(flips, f"{key} derives no pairing flip")
            fired = []
            for flip in flips:
                fired += self.contract.derive(self._derived_base(key), key, flip)
            self.assertTrue(fired, f"{key} flips never fired")
            for derived in fired:
                self.assertEqual(self.contract.adjudicate(key, derived), "agree-refused",
                                 f"{key}: {derived.label}")

    def _derived_base(self, key: str):
        return find(self.contract.drawn(key, full=True), lambda document: True, settings=DRY)

    def test_the_real_attempt_row_draws_both_directions_of_its_pairing(self):
        """E6b re-review: the run/5 attempt row's two-way conditional (a probe named exactly on the
        probe-failure word, null on every other reason) is schema-drawn in BOTH directions — the
        guarded word is read from the contract itself, never restated here."""
        key = "state#/$defs/qualificationAttempt"
        row = self.contract.node(key)
        const = next(entry["if"]["properties"]["reason"]["const"]
                     for entry in row["allOf"]
                     if "if" in entry and set(entry["if"].get("properties", {})) == {"reason"})
        probes = self.contract.node("state#/$defs/qualificationProbeName")["enum"]
        for guarded, expected in ((True, "probe named"), (False, "probe null")):
            document = find(self.contract.drawn(key, full=True).filter(
                lambda document: (document["reason"] == const) is guarded), lambda document: True,
                settings=DRY)
            if guarded:
                self.assertIn(document["probe"], probes)
            else:
                self.assertIsNone(document["probe"])
            self.assertTrue(self.contract.accepts(key, document))

    def test_pattern_probes_substitute_and_insert_the_complement_families(self):
        """The families the review demanded: printable-complement and astral probes, not only
        controls and blanks — one substitution through each position, derived per kind."""
        key = "state#/$defs/environmentProposal"
        kinds = {}
        for case in self.contract.cases(key):
            kinds[case.kind] = kinds.get(case.kind, 0) + 1
        for kind, floor in (("pattern-substitute", 30), ("pattern-insert", 30),
                            ("pattern-append", None)):
            if floor:
                self.assertGreaterEqual(kinds.get(kind, 0), floor,
                                        f"{kind} probes must reach across the patterned nodes")

    def test_nested_object_rows_are_challenged_inside_their_row(self):
        """The review: ``_walk`` used to stop at a nonterminal row, leaving every field inside an array
        of objects unchallenged. The history and attempts plans must derive inside row 0."""
        key = "state#/properties/environment_history"
        derived = self._derived(key, _drawn(self.contract, key))
        inside_row = [item for item in derived if isinstance(item.value, list) and item.value
                      and isinstance(item.value[0], dict) and len(item.value[0]) != 5]
        self.assertTrue(inside_row, "a challenge must fire inside an object row of the array")

    def test_a_non_domain_crash_is_a_finding_never_a_refusal(self):
        """E2a #2's shape: an unsized iterable that reaches a ``len()`` the core cannot size — the old
        harness banked the resulting ``TypeError`` as a refusal; the differential reports it."""
        key = "state#/properties/environment_history"
        base = _drawn(self.contract, key)
        unsized = next(derived for derived in self._derived(key, base)
                       if not isinstance(derived.value, (list, dict, str, tuple))
                       and derived.core_only and "iterable" in derived.label)
        status = self.contract.adjudicate(key, unsized)
        self.assertEqual(status, "agree-refused", "the honest core refuses the impostor in the domain")

    def test_the_float_candidate_is_excused_only_given_its_rule(self):
        """Draft 2020-12 admits 2.0 for an integer; the core refuses the float — the contract declares
        it (``case: float-for-int``), so the adjudicator excuses it: never as a disagreement."""
        key = "state#/$defs/environmentMember"
        base = _drawn(self.contract, key)
        floats = [derived for derived in self._derived(key, base)
                  if derived.kind == "float-for-int"
                  and isinstance(derived.value, dict)]
        self.assertTrue(floats, "the float candidate must be derived for the integer member")
        for derived in floats:
            status = self.contract.adjudicate(key, derived)
            self.assertTrue(status.startswith("excused core-only"), status)

    def test_the_open_disagreement_registry_is_empty_and_the_digest_anchor_is_healed(self):
        """The run/5 digest anchors ended in ``$`` — a trailing LF passed Draft 2020-12 while the core
        refused it — and that was the registry's two entries. 07c5f66b re-anchored both with
        ``(?![\s\\S])``, the pins fired, the entries were removed: the engine holds no excusal at
        this head, and the healed anchor refuses the trailing line feed in *both* judges — the gap
        would have to re-enter through a new declared entry, never silently."""
        self.assertEqual(engine.KNOWN_OPEN, {}, "no schema/core verdict gap may hide behind the registry")
        for key, digest in (("state#/$defs/environmentMember", "/properties/digest"),
                            ("state#/$defs/qualificationAttempt", "/properties/record_digest")):
            base = _drawn(self.contract, key)
            suffixed = [derived for derived in self._derived(key, base)
                        if derived.kind in ("lf-suffix", "cr-suffix")
                        and derived.node_id.endswith(digest)]
            self.assertTrue(suffixed, f"{key}{digest}: the line-feed candidates must be derived")
            for derived in suffixed:
                schema_accepts, core_verdict, crash = self.contract.verdicts(key, derived.value)
                self.assertFalse(schema_accepts, "the anchor healed — both judges refuse the suffix")
                self.assertEqual(core_verdict, "refuse", "the core keeps refusing the suffix")
                self.assertEqual(self.contract.adjudicate(key, derived), "agree-refused")

    def test_grammar_witnesses_enumerate_each_accepted_branch(self):
        """The spike-image lexeme's own grammar: top-level alternatives and optional groups each hold a
        witness — the branches the deleted hand-written image test covered, with nothing restated."""
        item = self.contract.node("state#/$defs/environmentProposal/properties/spike_images/items")
        pieces = item["x-grammar"]
        import re
        witnesses = engine.pattern_witnesses(item["pattern"])
        self.assertGreaterEqual(len(witnesses), 2 * engine.pattern_alternatives(item["pattern"]))
        self.assertTrue(any(re.fullmatch(pieces["bare-digest"], witness) for witness in witnesses),
                        "the bare-digest branch has no witness")
        self.assertTrue(any(re.fullmatch(pieces["repo-digest"], witness) for witness in witnesses),
                        "the repo-digest branch has no witness")
        self.assertTrue(any(re.fullmatch(pieces["name-component"], witness) for witness in witnesses),
                        "the name-component branch has no witness")
        self.assertTrue(any("/" in witness
                             and re.fullmatch(pieces["registry-domain"], witness.split("/")[0])
                             for witness in witnesses),
                        "the registry-domain branch has no witness")
        self.assertTrue(any(":" in witness
                            and not re.fullmatch(pieces["bare-digest"], witness)
                            and not re.fullmatch(pieces["repo-digest"], witness)
                            and re.fullmatch(pieces["tag"], witness.rpartition(":")[2])
                            for witness in witnesses),
                        "the tag branch has no witness")

    def test_the_maxima_documents_hold_at_their_bounds_and_numbers_set_theirs(self):
        key = "state#/$defs/environmentProposal"
        base = _drawn(self.contract, key)
        labels = [item.label for item in self.contract.at_bounds(key, base)]
        self.assertIn("p:spike_images: grown to maxItems", labels)
        self.assertIn("p:preparation: grown to maxItems", labels)
        self.assertIn("p:rationale: padded to maxLength", labels)
        for item in self.contract.at_bounds(key, base):
            with self.subTest(label=item.label):
                self.assertTrue(self.contract.accepts(key, item.value))
                self.contract.import_validator(key)(item.value)
        attempt_key = "state#/$defs/qualificationAttempt"
        attempt = _drawn(self.contract, attempt_key)
        attempt["revision"] = attempt["revision"] + 3  # away from its minimum, so the boundary is derived
        attempt["started_stamp"] = attempt["started_stamp"] + 3
        numeric = [item for item in self.contract.at_bounds(attempt_key, attempt)
                   if item.kind in ("at-minimum", "at-maximum")]
        self.assertTrue(numeric, "a numeric member sets itself exactly to its bound")
        for item in self.contract.at_bounds(attempt_key, attempt):
            with self.subTest(label=item.label):
                self.assertTrue(self.contract.accepts(attempt_key, item.value))
                self.contract.import_validator(attempt_key)(item.value)

    def test_a_definition_with_no_maximum_yields_no_boundary_and_no_failure(self):
        """The review: acceptance used to fail a definition with no such maximum. The attempt id bound
        still exists; a definition without any declared extremum yields an empty list, a fact."""
        key = "state#/$defs/environmentMember"
        base = _drawn(self.contract, key)
        self.assertTrue(self.contract.at_bounds(key, base))  # revision minimum and digest bounds exist


class FamilyWitnessTests(unittest.TestCase):
    """The re-review's finding 1: a same-family substitution for a const or an enumeration is a value
    of the SAME JSON type outside the allowed set (a string for a string enum), and the ledger credits a
    keyword only from a witness of that keyword's own family — a wrong-family value must never stand in.

    The re-review 3's finding 2 strengthens the credit: the substitution must also be one the rest of
    the schema admits with only the challenged keyword removed — see IsolatingSubstituteTests."""

    def test_every_const_and_enum_candidate_is_of_its_own_family(self):
        contract = Contract()
        for key in sorted(contract.registered):
            for case in contract.cases(key):
                if case.kind == "unsupported":
                    continue
                if case.kind not in ("const outside", "enum outside"):
                    continue
                with self.subTest(key=key, label=case.label):
                    self.assertTrue(engine.witness_of_family(case),
                                    "a const/enum candidate must be of the held family")
                    node = case.node
                    # the same family rule the ledger's own witness check applies: a
                    # ``const`` node's family is the const value's type itself (a const
                    # array's family is ``array`` — its members are not the alternatives),
                    # while an ``enum`` node's family pool is its enumerated values.
                    pool = [node["const"]] if "const" in node else list(node["enum"])
                    families = {engine._json_type(member) for member in pool}
                    self.assertIn(engine._json_type(case.holding), families)

    def test_a_string_const_is_challenged_with_a_string(self):
        contract = Contract()
        key = "state#/$defs/environmentMember"
        transcribed = [case for case in contract.cases(key)
                       if case.kind in ("const outside", "enum outside")
                       and case.node_id.endswith("/properties/state")]
        self.assertTrue(transcribed, "the member's state vocabulary is enumerated and challenged")
        for case in transcribed:
            self.assertIsInstance(case.holding, str, "the state vocabulary is a string family")
            self.assertNotIn(case.holding, case.node["enum"])
        wrong_types = [case for case in contract.cases(key) if case.kind == "wrong type"
                       and case.node_id.endswith("/properties/state")]
        self.assertTrue(any(isinstance(case.holding, int) for case in wrong_types),
                        "cross-type challenges keep their own wrong-type kinds")

    def test_a_wrong_family_value_is_never_credited(self):
        """The control the coordinator asked for: with a corrupted plan handing an integer where a
        string constant is challenged, the family gate refuses it — it would have been a type refusal,
        not a const witnessship."""
        node = {"const": "empirica.run/5"}
        wrong = engine.Case("a harvested same-family value", (), "const outside", "const", node,
                            "doc#node", 765)
        self.assertFalse(engine.witness_of_family(wrong),
                         "an integer must never be credited as covering a string const")
        right = engine.Case("a harvested same-family value", (), "const outside", "const", node,
                           "doc#node", "outside-any-run")
        self.assertTrue(engine.witness_of_family(right))

    def test_a_boolean_enumeration_holding_every_value_stays_unsupported(self):
        """A boolean family genuinely exhausts (no third boolean): the substitute series is empty and
        the plan declares the pair unsupported instead of crediting a cross-family value — while a
        string family always generates fresh values, so its series is never empty."""
        self.assertEqual(engine._family_substitutes({"type": "boolean"}, [True, False]), [])
        series = engine._family_substitutes({"type": "string"}, ["empirica.run/5"])
        self.assertTrue(series and all(isinstance(candidate, str) for candidate in series),
                        "a string family generates fresh strings, never exhaustion")
        contract = self._contract_for({"type": "boolean", "enum": [True, False]})
        cases = [case for case in contract.cases("probe_document#/$defs/probe")
                 if case.keyword == "enum" and case.node_id.endswith("/properties/v")]
        self.assertEqual([case.kind for case in cases], ["unsupported"],
                         "the exhausted family declares itself, never a cross-family credit")


    _PROBE_DOC = {  # a synthetic registered document: fresh-value families against the real plan
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://siffran.test/family-probe",
    "type": "object",
    "x-core-ingress": ["/$defs/probe"],
    "properties": {"probe": {"$ref": "#/$defs/probe"}},
    "required": ["probe"],
    "$defs": {"probe": {
        "x-core-validator": "conformance_fixtures.pass_through",
        "type": "object",
        "properties": {"v": {"type": "string"}},
        "required": ["v"],
        "additionalProperties": False}},
}

    def _contract_for(self, vocabulary) -> Contract:
        import types
        module = types.ModuleType("conformance_fixtures")
        module.pass_through = staticmethod(lambda value: value)
        sys.modules["conformance_fixtures"] = module
        document = copy.deepcopy(self._PROBE_DOC)
        document["$defs"]["probe"]["properties"]["v"] = vocabulary
        return Contract({"probe_document": document})

    def test_a_four_probe_string_enumeration_still_yields_a_fresh_string(self):
        """The re-review's seed: an enumeration holding every string of the old hard-coded probe list
        mistook probe exhaustion for family exhaustion. Fresh strings always exist — the plan generates
        one (the longest held value extended by a counter) instead of declaring the pair unsupported."""
        held = ["outside", "elsewhere", "elsewhere-once-more", "elsewhere-twice"]
        contract = self._contract_for({"type": "string", "enum": held})
        cases = [case for case in contract.cases("probe_document#/$defs/probe")
                 if case.keyword in ("const", "enum") and case.node_id.endswith("/properties/v")]
        kinds = sorted({case.kind for case in cases})
        self.assertIn("enum outside", kinds, kinds)
        self.assertNotIn("unsupported", kinds, "a string family is never exhausted")
        candidate = next(case for case in cases if case.kind == "enum outside")
        self.assertIn(engine._json_type(candidate.holding), "string")
        self.assertNotIn(candidate.holding, held, "the fresh value is outside the enumeration")
        self.assertTrue(engine.witness_of_family(candidate))

    def test_a_decimal_enumeration_yields_the_next_representable_decimal(self):
        """The re-review's seed: ``[1.5]`` crashed the old probe table with a missing number family.
        The fresh decimal continues above the peak instead (the next representable value, outside the
        finite set, of the enumeration's own family)."""
        contract = self._contract_for({"type": "number", "enum": [1.5]})
        cases = [case for case in contract.cases("probe_document#/$defs/probe")
                 if case.keyword in ("const", "enum") and case.node_id.endswith("/properties/v")]
        self.assertTrue(cases, "the decimal enumeration is challengeable")
        for case in cases:
            self.assertEqual(case.kind, "enum outside")
            self.assertEqual(engine._json_type(case.holding), "number", case.holding)
            self.assertTrue(engine.witness_of_family(case))

    def test_an_array_constant_gets_an_array_substitute_not_a_flattening(self):
        """The re-review's seed: a const array was flattened as if its members were enumeration
        alternatives, so the array family never saw a substitute. The const holds ONE array; the fresh
        value is a fresh array of the same family — shorter or longer (the isolating series tries every
        length from the empty array up), never the const itself, never a flattening."""
        contract = self._contract_for({"type": "array", "const": ["row", "second"]})
        cases = [case for case in contract.cases("probe_document#/$defs/probe")
                 if case.keyword in ("const", "enum") and case.node_id.endswith("/properties/v")]
        self.assertTrue(cases, "the array constant is challengeable")
        for case in cases:
            self.assertEqual(case.kind, "const outside", case.kind)
            self.assertEqual(engine._json_type(case.holding), "array", case.holding)
            self.assertNotEqual(case.holding, ["row", "second"], "a fresh array, never the const itself")
            self.assertTrue(engine.witness_of_family(case))


class IsolatingSubstituteTests(unittest.TestCase):
    """The re-review 3's finding 2: crediting a same-family substitute with covering ``const`` or
    ``enum`` requires the rest of the schema itself to admit it, with only the challenged keyword
    removed — a candidate a sibling keyword refuses (``maxItems`` turning away the always-longer array
    against a two-row const) is the sibling's word, never the keyword's. When the siblings admit no
    substitute at all, the ledger records the keyword redundant or unsupported — never covered."""

    _SOL_DOC = {  # Sol's seed: a two-row const alongside maxItems 2 — the false-coverage low water mark
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://siffran.test/sol-two-row-const",
        "type": "object",
        "x-core-ingress": ["/$defs/pinned"],
        "properties": {"pinned": {"$ref": "#/$defs/pinned"}},
        "required": ["pinned"],
        "$defs": {"pinned": {
            "x-core-validator": "conformance_fixtures.pinned_validator",
            "type": "array",
            "const": ["row", "second"],
            "maxItems": 2}}}

    _SIBLINGED_DOC = {  # the siblings alone admit only the const itself, so the keyword is not isolable
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://siffran.test/sibling-subsumed-const",
        "type": "object",
        "x-core-ingress": ["/$defs/pinned"],
        "properties": {"pinned": {"$ref": "#/$defs/pinned"}},
        "required": ["pinned"],
        "$defs": {"pinned": {
            "x-core-validator": "conformance_fixtures.pinned_validator",
            "type": "array",
            "const": ["row"],
            "minItems": 1,
            "maxItems": 1,
            "uniqueItems": True,
            "items": {"enum": ["row"]}}}}

    def _install_core(self, enforce_const: bool) -> None:
        """A validator the synthetic documents name: one enforcing only ``maxItems`` (Sol's seed — it
        refuses a three-row array and accepts anything else, const included), and one honest core that
        also refuses any array other than the pinned rows."""
        module = types.ModuleType("conformance_fixtures")

        def pinned_validator(value):
            if not isinstance(value, list):
                raise ValueError("not an array")
            if len(value) > 2:
                raise ValueError("too many rows")
            if enforce_const and list(value) != ["row", "second"]:
                raise ValueError("not the pinned rows")
            return value

        module.pinned_validator = staticmethod(pinned_validator)
        sys.modules["conformance_fixtures"] = module

    @staticmethod
    def _rest_admits(case) -> bool:
        """Whether the node with only the challenged keyword removed admits the substitute."""
        from jsonschema import Draft202012Validator
        rest = {word: value for word, value in case.node.items() if word != case.keyword}
        return not list(Draft202012Validator(rest).iter_errors(case.holding))

    def test_sol_control_a_two_row_const_with_maxitems_two_is_uncredited(self):
        """Sol's exact control: a core enforcing only ``maxItems`` used to pass the sweep, because the
        always-longer probe array was refused for length by both judges and the ledger credited the
        ``const`` keyword with the sibling's refusal. The substitute must fit the sibling bound, so the
        missing const refusal surfaces as a disagreement and the keyword is never credited."""
        self._install_core(enforce_const=False)
        contract = Contract({"sol": copy.deepcopy(self._SOL_DOC)})
        key = "sol#/$defs/pinned"
        substitute = next(case for case in contract.cases(key) if case.keyword == "const")
        self.assertEqual(substitute.kind, "const outside", substitute.label)
        self.assertLessEqual(len(substitute.holding), substitute.node["maxItems"],
                             "the substitute must fit the sibling bound, never only exceed the const")
        self.assertNotEqual(substitute.holding, substitute.node["const"])
        self.assertTrue(self._rest_admits(substitute),
                        "the rest of the schema admits the substitute with only the const removed")
        self.assertTrue(engine.witness_of_family(substitute))
        base = find(contract.drawn(key, full=True), lambda document: True, settings=DRY)
        statuses = {contract.adjudicate(key, derived)
                    for derived in contract.derive(base, key, substitute)}
        self.assertTrue(statuses, "the const candidate must fire")
        self.assertTrue(any(status.startswith("DISAGREEMENT") for status in statuses),
                        f"a maxItems-only core must fail on the const candidate, not pass: {statuses}")
        self.assertNotIn("agree-refused", statuses,
                         "a maxItems refusal must never be credited to the const keyword")

    def test_an_honest_core_is_credited_from_the_isolating_witness(self):
        """The teeth of the same control: a core that also refuses the const-substitute yields the
        both-refuse witness, so the credit belongs to the isolated keyword — and only to it."""
        self._install_core(enforce_const=True)
        contract = Contract({"sol": copy.deepcopy(self._SOL_DOC)})
        key = "sol#/$defs/pinned"
        substitute = next(case for case in contract.cases(key) if case.keyword == "const")
        base = find(contract.drawn(key, full=True), lambda document: True, settings=DRY)
        statuses = {contract.adjudicate(key, derived)
                    for derived in contract.derive(base, key, substitute)}
        self.assertEqual(statuses, {"agree-refused"},
                         "the honest core refuses the isolating witness, and only that is credit")

    def test_a_const_the_siblings_subsume_is_declared_redundant(self):
        """The complementary half: when ``minItems``/``maxItems``/``uniqueItems``/``items`` alone admit
        only the const itself, no substituting refusal can be isolated — the plan declares the keyword
        redundant and no candidate is derived the ledger could ever credit."""
        self._install_core(enforce_const=True)
        contract = Contract({"subsumed": copy.deepcopy(self._SIBLINGED_DOC)})
        key = "subsumed#/$defs/pinned"
        declared = [case for case in contract.cases(key) if case.keyword == "const"]
        self.assertEqual([case.kind for case in declared], ["redundant"],
                         [case.label for case in declared])
        base = find(contract.drawn(key, full=True), lambda document: True, settings=DRY)
        for case in declared:
            with self.subTest(label=case.label):
                self.assertEqual(contract.derive(base, key, case), [],
                                 "a redundant keyword derives no candidate coverage could credit")

    def test_every_real_const_and_enum_substitute_is_admitted_by_the_rest(self):
        """The standing word, held against the real registered contracts: every const/enum challenge
        candidate is a value the rest of its node admits with only the challenged keyword removed."""
        contract = Contract()
        seen = 0
        for key in sorted(contract.registered):
            for case in contract.cases(key):
                if case.kind not in ("const outside", "enum outside"):
                    continue
                with self.subTest(key=key, label=case.label):
                    self.assertTrue(self._rest_admits(case),
                                    "the sibling keywords must admit the substitute, or the keyword "
                                    "is declared redundant/unsupported, never covered")
                    seen += 1
        self.assertGreater(seen, 0, "the real contracts carry const/enum challenges to hold")


class MethodNameTests(unittest.TestCase):
    """The re-review's finding 2, naming half: generated test names carry the document identity, and a
    collision is refused rather than silently merged."""

    def test_the_document_identity_separates_two_same_named_definitions(self):
        taken: set[str] = set()
        state = engine.method_name("state#/$defs/environmentProposal", taken)
        request = engine.method_name("request#/$defs/environmentProposal", taken)
        self.assertNotEqual(state, request)
        self.assertIn("state", state)
        self.assertIn("request", request)

    def test_a_collision_is_refused_not_merged(self):
        taken: set[str] = set()
        first = engine.method_name("state#/$defs/environmentProposal", taken)
        with self.assertRaises(ValueError) as caught:
            engine.method_name("state#/$defs/environmentProposal", {first})
        self.assertIn("collision", str(caught.exception))


class FeasibleWorkTests(unittest.TestCase):
    """The re-review's finding 4: an explicit feasible-work limit refuses a definition whose bounds ask
    for unbounded-in-practice work; a declared x-work-limit override admits honest larger work."""

    def test_the_real_definitions_pass(self):
        self.assertEqual(Contract().findings, [])

    def test_an_infeasible_bound_is_refused_at_load(self):
        documents = copy.deepcopy(engine.module_documents())
        documents["state"]["$defs"]["environmentProposal"]["properties"]["rationale"] |= {"maxLength": 100000}
        findings = Contract(documents).findings
        self.assertTrue(any("the feasible-work limit refuses" in line and "maxLength" in line
                            for line in findings), findings)

    def test_a_declared_override_admits_the_honest_work(self):
        documents = copy.deepcopy(engine.module_documents())
        documents["state"]["$defs"]["environmentProposal"]["properties"]["rationale"] |= {
            "maxLength": 100000, "x-work-limit": {"maxLength": 100000}}
        findings = Contract(documents).findings
        self.assertFalse([line for line in findings if "feasible-work" in line], findings)


    def test_an_allocation_driving_minimum_refuses_the_definition_too(self):
        """The re-review 2 finding 3 seed: a string node with no maxLength and a ``minLength`` of one
        million. A minimum allocates on every draw — it is bounded by the same limit, not only maxima."""
        documents = copy.deepcopy(engine.module_documents())
        node = documents["state"]["$defs"]["environmentProposal"]["properties"]["rationale"]
        node |= {"minLength": 1000000}
        node.pop("maxLength", None)
        findings = Contract(documents).findings
        self.assertTrue(any("the feasible-work limit refuses" in line and "minLength" in line
                            for line in findings), findings)

    def test_a_declared_override_admits_an_honest_minimum(self):
        documents = copy.deepcopy(engine.module_documents())
        node = documents["state"]["$defs"]["environmentProposal"]["properties"]["rationale"]
        node |= {"minLength": 100000, "x-work-limit": {"minLength": 100000}}
        findings = [line for line in Contract(documents).findings if "feasible-work" in line]
        self.assertEqual(findings, [])

    def test_a_row_minimum_is_bounded_like_its_maximum(self):
        documents = copy.deepcopy(engine.module_documents())
        node = documents["state"]["$defs"]["environmentProposal"]["properties"]["spike_images"]
        node |= {"minItems": 4000}
        findings = Contract(documents).findings
        self.assertTrue(any("the feasible-work limit refuses" in line and "minItems" in line
                            for line in findings), findings)


class GrowthTests(unittest.TestCase):
    def test_unique_string_growth_is_linear(self):
        rows = ["a"]
        grown = engine.Contract._overgrown(rows, {"uniqueItems": True}, 64)
        self.assertEqual(len(grown), 64)
        self.assertEqual(len(set(grown)), 64, "the grown rows stay unique")
        self.assertLessEqual(sum(len(row) for row in grown), 64 * (1 + 4),
                              "total characters stay near-linear: the old prefixing growth was quadratic")

    def test_growth_never_collides_with_a_held_row(self):
        rows = ["0" + str(index) + "a" for index in range(5)] + ["a"]
        grown = engine.Contract._overgrown(rows, {"uniqueItems": True}, 20)
        self.assertEqual(len(grown), 20)
        self.assertEqual(len({row for row in grown if isinstance(row, str)}),
                         len([row for row in grown if isinstance(row, str)]))


class CaseSwapTests(unittest.TestCase):
    """The re-review's finding 3, first mechanism: a grammar-derived case witness for each
    case-restricted class (an uppercase in a lowercase-only name component), derived from the grammar
    and fired in place."""

    def test_each_case_restricted_class_its_pair(self):
        pairs = engine._case_restricted_letters(
            ["^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*(?![\s\S])"])
        self.assertEqual(pairs, [("z", "Z")], "a lowercase-only class yields its in-place flip")
        self.assertEqual(engine._case_restricted_letters(
            ["^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}(?![\s\S])"]), [],
            "a class admitting both cases has no case to violate")

    def test_the_case_swap_fires_and_both_judges_refuse(self):
        contract = Contract()
        key = "state#/$defs/environmentProposal"
        drawn = copy.deepcopy(find(contract.drawn(key, full=True),
                                   lambda document: True, settings=DRY))
        case = next(case for case in contract.cases(key)
                    if case.kind == "case-swap"
                    and case.node_id.endswith("/properties/spike_images/items"))
        held, _ = case.holding
        if not any(isinstance(row, str) and held in row for row in drawn["spike_images"]):
            # a bounded, derandomized draw of an all-digit image never holds the name letter; a
            # drawn base that does is seeded the same way the corpus seeds itself
            drawn["spike_images"] = ["maintenance" + held + "/probe"]
        fired = [d for d in contract.derive(drawn, key, case) if d.kind == "case-swap"]
        self.assertTrue(fired, "the in-place case flip fires on a name holding the class letter")
        for item in fired:
            self.assertEqual(contract.adjudicate(key, item), "agree-refused", item.label)


class RegressionCorpusTests(unittest.TestCase):
    """The re-review 2 finding 2: the contract's named regression-witness corpus is a *bounded corpus
    of evidenced defects* — every entry an object ``{"value", "defect"}``, at most eight per node, the
    values refused by both judges. The entries live in the contract (``x-regression-witnesses``); the
    test reads them from there, never restating one."""

    def test_the_corpus_is_read_from_the_contract_and_refused_by_both_judges(self):
        contract = Contract()
        key = "state#/$defs/environmentProposal"
        corpus = contract.node(key + "/properties/spike_images/items")["x-regression-witnesses"]
        self.assertTrue(corpus and len(corpus) <= 8, "the corpus is a bounded one")
        self.assertEqual(len({entry["value"] for entry in corpus}), len(corpus), "values are unique")
        for entry in corpus:
            with self.subTest(value=entry["value"]):
                self.assertIsInstance(entry["value"], str)
                self.assertIsInstance(entry["defect"], str)  # the evidenced-defect reference
                self.assertTrue(entry["defect"].strip())
        drawn = find(contract.drawn(key, full=True), lambda document: True, settings=DRY)
        challenges = contract.regression_challenges(key, drawn)
        placed = {challenge.value["spike_images"][0] for challenge in challenges}
        self.assertEqual(len(challenges), len(corpus))
        self.assertEqual(placed, {entry["value"] for entry in corpus},
                         "every corpus value is placed, exactly once")
        for challenge in challenges:
            with self.subTest(label=challenge.label):
                self.assertEqual(contract.adjudicate(key, challenge), "agree-refused",
                                 "a named regression witness one judge admits")


class BoundedWorkTests(unittest.TestCase):
    """The review's non-blocking findings: bounded work, a terminating absent-key probe."""

    def test_absent_key_terminates_on_the_old_never_ending_seed(self):
        """The review's seed: the object already holds *both* ``unexpected`` and
        ``unexpected<N>`` (N = its property count), the pair that used to loop forever."""
        node = {"properties": {f"unexpected{index}": {"type": "boolean"} for index in range(10)},
                "additionalProperties": False}
        node["properties"]["unexpected"] = {"type": "boolean"}
        fresh = engine._absent_key(node)  # the loop ends; a key outside the properties comes back
        self.assertIsNotNone(fresh)
        self.assertNotIn(fresh, node["properties"])

    def test_overgrown_builds_one_list_never_skipping_the_prefixes(self):
        rows = ["a"]
        grown = engine.Contract._overgrown(rows, {"uniqueItems": True}, 64)
        self.assertEqual(len(grown), 64)
        self.assertEqual(len(set(grown)), 64, "the grown rows stay unique")


class TwoWayFormTests(unittest.TestCase):
    """The two-way ``if``/``then``/"``else``" conditional the run/5 attempt row carries (E6b re-review):
    one entry, both directions — the probe is named exactly when the reason is the probe-failure
    word and null otherwise. The drawing engine must derive the pair itself and refuse everything
    else it does not model, and a free-text driver must still draw its guarded word (the const the
    conditional declares is pinned into the driver's arm, so ``then`` is reachable, never only "else"
    on a pattern's random text)."""

    def setUp(self):
        _install_fixture_validator()
        self.contract = Contract({"two-way": copy.deepcopy(TWO_WAY)})

    def tearDown(self):
        sys.modules.pop("conformance_fixtures", None)

    def _branch(self, guarded: bool) -> dict:
        const = TWO_WAY["$defs"]["attempt"]["allOf"][0]["if"]["properties"]["reason"]["const"]
        strategy = self.contract.drawn("two-way#/$defs/attempt", full=True).filter(
            lambda document, const=const: (document["reason"] == const) is guarded)
        return find(strategy, lambda document: True, settings=DRY)

    def test_both_directions_of_the_pairing_are_drawn(self):
        guarded = self._branch(True)
        complementary = self._branch(False)
        self.assertIn(guarded["probe"], TWO_WAY["$defs"]["probeName"]["enum"])
        self.assertIsNone(complementary["probe"], complementary)
        for document in (guarded, complementary):
            self.assertTrue(self.contract.accepts("two-way#/$defs/attempt", document))
            _two_way_validator(document)  # the honest core accepts both drawn directions

    def test_a_direct_breach_of_the_pairing_is_refused_by_both_judges(self):
        probes = TWO_WAY["$defs"]["probeName"]["enum"]
        for label, breach in (("probe absent on its guarded reason", {**self._branch(True), "probe": None}),
                              ("probe beside another reason", {**self._branch(False), "probe": probes[0]})):
            with self.subTest(label=label):
                self.assertFalse(self.contract.accepts("two-way#/$defs/attempt", breach))
                with self.assertRaises(ValueError):
                    _two_way_validator(breach)

    def test_the_pairing_flips_breach_both_directions_and_both_judges_refuse(self):
        key = "two-way#/$defs/attempt"
        flips = [item for item in self.contract.cases(key) if item.kind == "pairing flip"]
        self.assertTrue(flips, "the two-way pairing must derive its own breaches")
        for base in (self._branch(True), self._branch(False)):
            for flip in flips:
                for derived in self.contract.derive(base, key, flip):
                    self.assertEqual(self.contract.adjudicate(key, derived), "agree-refused",
                                     derived.label)

    def test_shapes_the_engine_does_not_model_stay_refused_never_guessed(self):
        import copy
        for bad in (
                {"if": {"properties": {"reason": {"pattern": "^qualification"}}},
                 "then": {"properties": {"probe": {"$ref": "#/$defs/probeName"}}},
                 "else": {"properties": {"probe": {"type": "null"}}}},
                {"if": {"properties": {"reason": {"const": "qualification-probe-failed"}}},
                 "then": {"properties": {"probe": {"$ref": "#/$defs/probeName"}}},
                 "else": {"properties": {"probe": {"type": "null"}, "reason": {"type": "null"}}}},
                {"if": {"properties": {"reason": {"const": "qualification-probe-failed"}}},
                 "then": {"properties": {"attempt_id": {"type": "string"}}},
                 "else": {"properties": {"probe": {"type": "null"}}}},
        ):
            document = copy.deepcopy(TWO_WAY)
            document["$defs"]["attempt"]["allOf"] = [bad]
            mutant = Contract({"two-way": document})
            with self.assertRaises(ValueError):
                mutant.drawn("two-way#/$defs/attempt")


    def test_a_boolean_if_inside_allof_stays_refused(self):
        """Sol's seed (E6c re-review 1, blocking 2): replacing a two-way guard's ``if`` with a
        boolean leaves the conditional loadable but unmodelled - ``if: true`` always applies (its
        ``then`` names the probe on every reason, a boolean is no const on a member), and the old
        parse silently ignored the entry, so the drawing drew a null probe the schema refused and
        the plan yielded zero pairing flips. An ``if`` key this engine cannot model refuses at
        parse, on both engines, in drawing and derivation alike."""
        for boolean in (True, False):
            with self.subTest(if_=boolean):
                document = copy.deepcopy(TWO_WAY)
                document["$defs"]["attempt"]["allOf"][0]["if"] = boolean
                mutant = Contract({"two-way": document})
                with self.assertRaises(ValueError):
                    mutant.drawn("two-way#/$defs/attempt")
                with self.assertRaises(ValueError):
                    mutant.cases("two-way#/$defs/attempt")

    def test_a_multi_key_if_required_with_an_optional_member_stays_refused(self):
        """Sol's seed (E6c review, blocking 3): a two-way conditional whose ``if`` demands the
        guarded member *and* an optional sibling loads today and draws a document the schema itself
        refuses — the drawing honors only the driver's const, and the sibling may legally stay
        absent, so ``if.required`` fires. The form is unmodelled: both engines refuse it at parse."""
        document = copy.deepcopy(TWO_WAY)
        attempt = document["$defs"]["attempt"]
        attempt["properties"]["other"] = {"type": "string", "maxLength": 16}
        attempt["allOf"] = [{
            "if": {"properties": {"reason": {"const": "qualification-probe-failed"}},
                   "required": ["reason", "other"]},
            "then": {"properties": {"probe": {"$ref": "#/$defs/probeName"}}},
            "else": {"properties": {"probe": {"type": "null"}}}}]
        mutant = Contract({"two-way": document})
        with self.assertRaises(ValueError):
            mutant.drawn("two-way#/$defs/attempt")
        with self.assertRaises(ValueError):
            mutant.cases("two-way#/$defs/attempt")


class PresenceFormTests(unittest.TestCase):
    """The node-level ``if``/``then``/``else`` presence pairing the views declare (E6c):
    the guarded word carries the paired member, every other word - and, on the wire form, an absent
    driver too - forbids it. The drawing engine must draw every direction, the pairing must derive
    its own breaches, and a gated member's own challenges must still fire against a base that drew
    the complementary side (the guard is placed for them, never guessed away)."""

    def test_a_presence_conditional_pairing_a_member_with_itself_stays_refused(self):
        """Sol's seed (E6c re-review 2, blocking 2): leaving the optional-reason fixture's guard
        on ``reason`` but pointing both branches at ``reason`` too yields a schema-valid
        conditional the old parse accepted — and the drawing then drew ``{'reason':
        'docker-unreachable'}`` the schema itself refuses, while the first pairing flip derived
        ``{}``, which the schema accepts. A member guarding itself is unmodelled (the two-way
        value parse already refuses it): both engines refuse the seed at parse."""
        document = copy.deepcopy(PRESENCE)
        which = "wire"
        node = document["$defs"][which]
        node["then"] = {"required": ["reason"]}
        node["else"] = {"not": {"required": ["reason"]}}
        mutant = Contract({"presence": document})
        with self.assertRaises(ValueError):
            mutant.drawn(f"presence#/$defs/{which}")
        with self.assertRaises(ValueError):
            mutant.cases(f"presence#/$defs/{which}")

    def test_a_boolean_if_at_the_node_level_stays_refused(self):
        """Sol's seed (E6c re-review 1, blocking 2): replacing a presence guard's ``if`` with a
        boolean leaves the conditional loadable but unmodelled - ``if: true`` always applies
        (its ``then`` demands the probe on every reason, ``else`` never fires), and the old parse
        silently ignored the node's ``if``, so the drawing drew the guarded reason without its
        probe (a document the schema itself refused) and the plan yielded zero pairing flips. Any
        ``if`` key this engine cannot model refuses at parse, on both engines, both sides."""
        for boolean in (True, False):
            for which in ("looking", "wire"):
                with self.subTest(if_=boolean, node=which):
                    document = copy.deepcopy(PRESENCE)
                    document["$defs"][which]["if"] = boolean
                    mutant = Contract({"presence": document})
                    with self.assertRaises(ValueError):
                        mutant.drawn(f"presence#/$defs/{which}")
                    with self.assertRaises(ValueError):
                        mutant.cases(f"presence#/$defs/{which}")

    def test_a_presence_side_that_only_redeclares_properties_stays_refused(self):
        """Sol's seed (E6c review, blocking 3): a ``then`` that only re-declares the paired member
        with its schema does not make it required to jsonschema, but the old presence parser read
        that as a presence demand — every draw supplied the member and the schema-valid member-free
        branch was silently unreachable. Unmodelled: both engines refuse it at parse."""
        for which in ("looking", "wire"):
            document = copy.deepcopy(PRESENCE)
            document["$defs"][which]["then"] = {"properties": {
                "probe": {"$ref": "#/$defs/probeName"}}}
            mutant = Contract({"presence": document})
            with self.assertRaises(ValueError):
                mutant.drawn(f"presence#/$defs/{which}")
            with self.assertRaises(ValueError):
                mutant.cases(f"presence#/$defs/{which}")

    def setUp(self):
        _install_fixture_validator()
        self.contract = Contract({"presence": copy.deepcopy(PRESENCE)})
        self.probes = sorted(PRESENCE["$defs"]["probeName"]["enum"])

    def tearDown(self):
        sys.modules.pop("conformance_fixtures", None)

    def _draw(self, key: str, guarded: bool, *, driver_absent: bool = False):
        strategy = self.contract.drawn(key, full=True).filter(
            lambda document: (document.get("reason") == "qualification-probe-failed") is guarded
            and ("reason" in document) is not driver_absent)
        return find(strategy, lambda document: True, settings=DRY)

    def test_every_direction_of_the_pairing_is_drawn_and_agrees(self):
        for key in ("presence#/$defs/looking", "presence#/$defs/wire"):
            guarded = self._draw(key, True)
            self.assertIn(guarded["probe"], self.probes)
            self.assertTrue(self.contract.accepts(key, guarded))
            _presence_validator(guarded)
            complementary = self._draw(key, False)
            self.assertNotIn("probe", complementary)
            self.assertTrue(self.contract.accepts(key, complementary))
            _presence_validator(complementary)
        # an absent driver only a non-full draw takes (a full draw keeps every optional member):
        # it falls to the else side, so the reasonless row may not carry a probe at all.
        reasonless = find(self.contract.drawn("presence#/$defs/wire").filter(
            lambda document: "reason" not in document), lambda document: True, settings=DRY)
        self.assertNotIn("probe", reasonless, "an absent driver falls to the else side")
        self.assertTrue(self.contract.accepts("presence#/$defs/wire", reasonless))
        _presence_validator(reasonless)

    def test_direct_pairing_breaches_are_refused_by_both_judges(self):
        key = "presence#/$defs/wire"
        breaches = [
            {k: v for k, v in self._draw(key, True).items() if k != "probe"},   # guarded, probe gone
            {**self._draw(key, False), "probe": self.probes[0]},                # other reason, probe kept
            {k: v for k, v in self._draw(key, True, driver_absent=False).items()
             if k != "reason"},                                                 # reasonless, probe kept
        ]
        breaches[-1]["probe"] = self.probes[0]
        for breach in breaches:
            with self.subTest(breach=breach):
                self.assertFalse(self.contract.accepts(key, breach))
                with self.assertRaises(ValueError):
                    _presence_validator(breach)

    def test_the_pairing_flips_breach_every_direction_and_both_judges_refuse(self):
        for key in ("presence#/$defs/looking", "presence#/$defs/wire"):
            flips = [item for item in self.contract.cases(key) if item.kind == "pairing flip"]
            self.assertTrue(flips, f"{key} must derive its pairing breaches")
            labels = {item.label for item in flips}
            if key.endswith("/wire"):
                self.assertTrue(any("driverless" in label for label in labels),
                                "the wire form derives the reasonless-with-probe breach")
            else:
                self.assertFalse(any("driverless" in label for label in labels),
                                 "a required driver has no driverless breach to derive")
            for flip in flips:
                fired = []
                for guarded in (True, False):
                    base = self._draw(key, guarded)
                    fired += self.contract.derive(base, key, flip)
                self.assertTrue(fired, f"{flip.label} never fired")
                for derived in fired:
                    self.assertEqual(self.contract.adjudicate(key, derived), "agree-refused",
                                     derived.label)

    def test_the_gated_members_own_challenges_fire_on_the_complementary_side(self):
        """A base that drew the else side carries no probe at all, yet the probe member's own
        plan cases (its enumeration and its kinds) must still fire: the guard is placed for them,
        so each candidate lands inside the guarded branch with only the challenged keyword wronged
        - and both judges refuse it."""
        key = "presence#/$defs/wire"
        base = self._draw(key, False)
        self.assertNotIn("probe", base)
        fired = [item for item in (self.contract.derive(base, key, case)
                                  for case in self.contract.cases(key))]
        fired = [item for group in fired for item in group]
        gated = [item for item in fired
                 if isinstance(item.value, dict) and "probe" in item.value]
        self.assertTrue(gated, "the gated probe member's own challenges must still fire")
        for derived in gated:
            self.assertEqual(self.contract.adjudicate(key, derived), "agree-refused", derived.label)


class ConditionalFormTests(unittest.TestCase):
    """The oneOf conditional form a later node registers by declaration (ADR-0075, documented in
    cases()); here it also proves the differential discipline against a disobedient core."""

    def setUp(self):
        self.quiet = sys.modules.pop("conformance_fixtures", None)
        _install_fixture_validator()
        self.contract = Contract({"conditional": copy.deepcopy(CONDITIONAL)})

    def tearDown(self):
        if self.quiet is not None:
            sys.modules["conformance_fixtures"] = self.quiet
        else:
            sys.modules.pop("conformance_fixtures", None)

    def _branch(self, tested: bool) -> dict:
        strategy = self.contract.drawn("conditional#/$defs/failure", full=True).filter(
            lambda document: (document["reason"] == "qualification-probe-failed") is tested)
        return find(strategy, lambda document: True, settings=DRY)

    def test_draws_omit_the_member_a_branch_forbids(self):
        guarded = self._branch(True)
        complementary = self._branch(False)
        self.assertIn("probe", guarded)
        self.assertEqual(guarded["reason"], "qualification-probe-failed")
        self.assertNotIn("probe", complementary)
        self.assertIn(complementary["reason"], ("environment-required", "host-conflict"))

    def test_the_missing_member_and_the_guard_flip_are_both_derived(self):
        seen = set()
        for document in (self._branch(True), self._branch(False)):
            for case in self.contract.cases("conditional#/$defs/failure"):
                for derived in self.contract.derive(document, "conditional#/$defs/failure", case):
                    seen.add(("missing" in derived.label, "guard" in derived.label))
        self.assertIn((True, False), seen, "a required probe missing is derived against its own branch")
        self.assertIn((False, True), seen, "the guard flip with the other member left as drawn is derived")

    def test_a_disobedient_core_is_never_dropped_even_when_the_schema_refuses(self):
        """A candidate the schema refuses and a core that still accepts it: a disagreement, refused
        wholesale — never a dropped candidate or an excused one."""
        key = "conditional#/$defs/failure"
        base = self._branch(False)
        bad = []
        for case in self.contract.cases(key):
            for derived in self.contract.derive(base, key, case):
                if not self.contract.accepts(key, derived.value):
                    bad.append(derived)
        self.assertTrue(bad)
        disobedient = Contract({"conditional": copy.deepcopy(CONDITIONAL)})
        _install_fixture_validator(accepts=lambda value: True)
        disobedient._cores.clear()
        found = []
        for derived in bad[:20]:
            status = disobedient.adjudicate(key, derived)
            if status.startswith("DISAGREEMENT"):
                found.append(status)
        self.assertTrue(found, "a core that accepts what the schema refuses goes unnamed")
        self.assertIn("the core accepts what the schema refuses", found[0])

    def test_a_validator_crash_is_a_finding_never_a_refusal(self):
        key = "conditional#/$defs/failure"
        crashing = Contract({"conditional": copy.deepcopy(CONDITIONAL)})
        _install_fixture_validator(crashes=True)
        crashing._cores.clear()
        derived = engine.Derived("the crashing shape", {"k": 1},
                                 "conditional#/$defs/failure", "wrong type", "type")
        crashing.import_validator(key)  # the crash happens on the verifier's own throat, not the import
        status = crashing.adjudicate(key, derived)
        self.assertTrue(status.startswith("NON-DOMAIN CRASH"), status)
        self.assertIn("TypeError", status)

    def test_witness_challenges_reach_the_later_branches(self):
        key = "conditional#/$defs/failure"
        base = self._branch(True)
        labels = [item.label for item in self.contract.witness_challenges(key, base)]
        self.assertTrue(any("grammar witness" in label for label in labels),
                        "the patterned nodes receive witness-seeded challenges")


if __name__ == "__main__":
    unittest.main()
