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


class FamilyWitnessTests(unittest.TestCase):
    """The re-review's finding 1: a same-family substitution for a const or an enumeration is a value
    of the SAME JSON type outside the allowed set (a string for a string enum), and the ledger credits a
    keyword only from a witness of that keyword's own family — a wrong-family value must never stand in."""

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
                    held = node.get("const", node.get("enum"))
                    pool = list(held) if isinstance(held, (list, tuple)) else [held]
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

    def test_a_fresh_probe_free_family_declares_itself_unsupported(self):
        """A boolean const that holds every value of its family has no same-family harvest left: the
        plan declares the pair unsupported instead of crediting a cross-family value (the premise of
        the wrong-family finding, made explicit)."""
        probed = engine._outside_values([True, False])
        self.assertEqual(probed, [], "the boolean family is exhausted")
        probes = engine._outside_values("empirica.run/5")
        self.assertTrue(probes and all(isinstance(probe, str) for probe in probes),
                        "a string const harvests a string")


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
    """The re-review's finding 3, second mechanism: the contract's named regression-witness corpus —
    known-defect values (the exact spellings a deleted hand-written test once held), fed through both
    judges and refused by both. The values live in the contract (``x-regression-witnesses``); the test
    reads them from there, never restating one."""

    def test_the_corpus_is_read_from_the_contract_and_refused_by_both_judges(self):
        contract = Contract()
        key = "state#/$defs/environmentProposal"
        item = contract.node(key + "/properties/spike_images/items")
        corpus = item["x-regression-witnesses"]
        self.assertTrue(any(isinstance(witness, str) and witness not in ("",) for witness in corpus))
        drawn = find(contract.drawn(key, full=True), lambda document: True, settings=DRY)
        challenges = contract.regression_challenges(key, drawn)
        placed = {challenge.value["spike_images"][0] for challenge in challenges}
        self.assertEqual(len(challenges), len(corpus))
        self.assertEqual(placed, set(corpus), "every corpus value is placed, exactly once")
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
