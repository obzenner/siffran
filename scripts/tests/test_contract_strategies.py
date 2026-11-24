#!/usr/bin/env python3
"""Unit controls for scripts/contract_strategies.py — the derivation engine's behaviour, pinned per kind.

The harness (plugins/empirica/tests/test_contract_conformance.py) sweeps the real annotated contract; these
controls prove the engine itself derives and drops what it claims, against the real contract and against a
synthetic conditional definition of the form ADR-0075 documents: one closed-object branch per case, a shared
required guard member with disjoint closed guard sets, the conditional member required in one branch and
forbidden by ``"not": {"required": [...]}`` in the other — the form a later node registers by declaration,
with no harness change.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "scripts", ROOT / "plugins" / "empirica"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from hypothesis import HealthCheck, find, settings  # noqa: E402

from contract_strategies import OUTSIDE, Contract  # noqa: E402
from core import environment as environment_model  # noqa: E402

DRY = settings(max_examples=64, database=None, deadline=None,
               suppress_health_check=[HealthCheck.filter_too_much])


def _drawn(contract: Contract, pointer: str):
    return find(contract.drawn(pointer, full=True), lambda document: True, settings=DRY)


class RealContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = Contract()

    def _derived(self, pointer: str, base: object) -> list:
        out = []
        for case in self.contract.cases(pointer):
            out.extend(self.contract.derive(base, pointer, case))
        return out

    def test_the_free_text_blank_set_is_the_contract_itself_not_a_host_language(self):
        """The schema's own explicit cross-host class decides blankness — U+FEFF alone is blank, where
        JavaScript's \\S agrees and Python's strip() does not — so a core leaning on either language's
        semantics is caught by the sweep, never by a hand-written case."""
        base = _drawn(self.contract, "#/$defs/environmentProposal")
        for character in ("\ufeff", "\u00a0", "\u1680", "\u2000", "\u3000", " ", "\r", *OUTSIDE):
            with self.subTest(character=character):
                whole = copy.deepcopy(base)
                whole["rationale"] = character
                self.assertFalse(self.contract.accepts("#/$defs/environmentProposal", whole),
                                  f"{character!r} alone must be blank per the contract's own class")
                with self.assertRaises(environment_model.EnvironmentProposalError):
                    environment_model.validate_proposal(whole)

    def test_the_whole_string_blank_kinds_fire_for_every_blank_family(self):
        base = _drawn(self.contract, "#/$defs/environmentProposal")
        fired = {derived.value["rationale"] for derived in self._derived("#/$defs/environmentProposal", base)
                 if isinstance(derived.value, dict) and derived.value.get("rationale")}
        for character in (" ", "\ufeff", "\u00a0", "\u3000", "\u1680", "\u2028"):
            self.assertIn(character, fired, "every blank family fails alone")

    def test_a_digest_candidate_the_schema_admits_is_never_asserted(self):
        """Python's ``$`` trailing-newline reading: the schema admits such a digest string, so the character
        kind drops it — the codec's stricter word is never claimed as the contract's."""
        base = _drawn(self.contract, "#/$defs/qualificationAttempt")
        trailing = copy.deepcopy(base)
        trailing["record_digest"] = str(base["record_digest"]) + "\n"
        self.assertTrue(self.contract.accepts("#/$defs/qualificationAttempt", trailing))
        derived = [item.value for item in self._derived("#/$defs/qualificationAttempt", base)
                   if isinstance(item.value, dict)]
        self.assertNotIn(trailing, derived, "an admitted candidate never rides the invalid sweep")

    def test_over_bound_arrays_pad_with_pattern_and_uniqueness_intact(self):
        derived = self._derived("#/$defs/environmentProposal", _drawn(self.contract,
                                                                       "#/$defs/environmentProposal"))
        over = [item for item in derived if isinstance(item.value, dict)
                and isinstance(item.value.get("spike_images"), list)
                and len(item.value["spike_images"]) > 16]
        self.assertTrue(over, "the uniqueItems array grows past its bound in the sweep")
        images = over[0].value["spike_images"]
        self.assertEqual(len(images), 17)
        self.assertEqual(len(set(images)), len(images), "a uniqueItems array pads without copying")


    def test_the_maxima_documents_hold_at_their_bounds(self):
        """Samples stay short by design, so the exact maximum is derived and held: a list grown to its
        maxItems (uniqueness intact) and a string padded to its maxLength (pattern intact) stay valid
        for the schema and for the core alike — a narrowed bound has no visitors to hide behind."""
        pointer = "#/$defs/environmentProposal"
        base = _drawn(self.contract, pointer)
        grown = self.contract.at_bounds(pointer, base)
        labels = [item.label for item in grown]
        self.assertIn("p:spike_images: grown to maxItems", labels)
        self.assertIn("p:preparation: grown to maxItems", labels)
        self.assertIn("p:rationale: padded to maxLength", labels)
        self.assertIn("p:preparation/r:0: padded to maxLength", labels)
        from core import environment as environment_model
        for item in grown:
            with self.subTest(label=item.label):
                self.assertTrue(self.contract.accepts(pointer, item.value))
                environment_model.validate_proposal(item.value)

    def test_the_core_only_impostors_replace_sequences(self):
        pointer = "#/properties/qualification_attempts"
        labels = {item.label for item in self._derived(pointer, _drawn(self.contract, pointer))
                  if item.core_only}
        self.assertIn("<root>: iterable where a sequence is required — core only", labels)
        self.assertIn("<root>: empty mapping where a sequence is required — core only", labels)
        self.assertIn("<root>: keyed mapping where a sequence is required — core only", labels)


CONDITIONAL = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://siffran.test/conditional",
    "$defs": {"failure": {
        "x-core-validator": "core.freshness.validate_digest256 replaced below",
        "type": "object",
        "oneOf": [
            {"type": "object", "additionalProperties": False, "required": ["reason", "probe", "digest"],
             "properties": {"reason": {"const": "qualification-probe-failed"},
                            "probe": {"type": "string", "pattern": "^probe/", "minLength": 1},
                            "digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}}},
            {"type": "object", "additionalProperties": False, "required": ["reason", "digest"],
             "not": {"required": ["probe"]},
             "properties": {"reason": {"enum": ["environment-required", "host-conflict"]},
                            "probe": {"type": "string", "pattern": "^probe/", "minLength": 1},
                            "digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}}},
        ]}},
}


class ConditionalFormTests(unittest.TestCase):
    """The oneOf conditional form a later node registers by declaration (ADR-0075, documented in cases())."""

    @classmethod
    def setUpClass(cls):
        schema = copy.deepcopy(CONDITIONAL)
        schema["$defs"]["failure"]["x-core-validator"] = "core.freshness.validate_digest256"
        cls.contract = Contract(schema)

    @classmethod
    def tearDownClass(cls):
        pass

    def _branch(self, tested: bool) -> dict:
        """A drawn document of the guarded branch when ``tested`` is true, else the complementary one —
        ``find`` minimizes into the first branch, so the branches are selected by their guard's own word."""
        strategy = self.contract.drawn("#/$defs/failure", full=True).filter(
            lambda document: (document["reason"] == "qualification-probe-failed") is tested)
        return find(strategy, lambda document: True, settings=DRY)

    def test_draws_omit_the_member_a_branch_forbids(self):
        guarded = self._branch(True)
        complementary = self._branch(False)
        self.assertIn("probe", guarded)
        self.assertEqual(guarded["reason"], "qualification-probe-failed")
        self.assertNotIn("probe", complementary)
        self.assertIn(complementary["reason"], ("environment-required", "host-conflict"))

    def test_full_draws_reach_both_branches(self):
        self.assertEqual(self._branch(True)["reason"], "qualification-probe-failed")
        self.assertIn(self._branch(False)["reason"], ("environment-required", "host-conflict"))

    def test_the_missing_member_and_the_guard_flip_are_both_derived(self):
        seen = set()
        for document in (self._branch(True), self._branch(False)):
            for case in self.contract.cases("#/$defs/failure"):
                for derived in self.contract.derive(document, "#/$defs/failure", case):
                    seen.add(("missing" in derived.label, "guard" in derived.label))
        self.assertIn((True, False), seen, "a required probe missing is derived against its own branch")
        self.assertIn((False, True), seen, "the guard flip with the other member left as drawn is derived")


if __name__ == "__main__":
    unittest.main()
