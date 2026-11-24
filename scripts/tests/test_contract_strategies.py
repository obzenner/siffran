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
    sys.modules["conformance_fixtures"] = module
    return module


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
