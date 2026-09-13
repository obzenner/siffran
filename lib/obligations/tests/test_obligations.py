#!/usr/bin/env python3
"""Executable conformance suite shared conceptually with the TypeScript mirror."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "lib"))
from obligations import (  # noqa: E402
    Contract,
    Obligation,
    Observation,
    Preservation,
    Retirement,
    Verdict,
    Witness,
    canonical,
    from_json,
    parse,
    preserved,
    project,
    render_text,
    revise,
    same_contract,
    to_json,
    verify,
)

FIXTURES = ROOT / "contracts/obligations/v1/fixtures"


class ContractValues(unittest.TestCase):
    def test_ref_grammar_is_enforced_at_construction(self):
        with self.assertRaises(ValueError):
            Witness("test", "Test/no spaces", "pass", "description")
        with self.assertRaises(ValueError):
            Observation("test", "test/no spaces", "pass", "runner", "now")

    def test_description_and_source_are_required(self):
        with self.assertRaises(ValueError):
            Witness("test", "test/case", "pass", " ")
        with self.assertRaises(ValueError):
            Observation("test", "test/case", "pass", "", "now")

    def test_anonymous_judgments_are_rejected_case_insensitively(self):
        for source in ("anonymous", "UNKNOWN", "Model"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                Observation("judgment", "audit/run", "pass", source, "now")

    def test_hold_and_reason_are_atomic(self):
        with self.assertRaises(ValueError):
            Obligation("O1", "require", "effect", (), hold="blocked")
        with self.assertRaises(ValueError):
            Obligation("O1", "require", "effect", (), hold_reason="reason")

    def test_every_public_value_round_trips_json(self):
        witness = Witness("test", "test/case", "pass", "test passes")
        obligation = Obligation("O1", "require", "effect", (witness,), ("G1",))
        observation = Observation("test", "test/case", "pass", "runner", "now")
        retirement = Retirement(obligation, "obsolete", "human", 2)
        values = (
            witness,
            obligation,
            observation,
            Contract("c", 1, (obligation,), ("p",)),
            retirement,
            Verdict(satisfied=("O1",)),
            Preservation(True),
        )
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.assertEqual(value, from_json(type(value), to_json(value)))
                self.assertEqual(value, type(value).from_json(value.to_json()))

    def test_revision_requires_authority_and_records_retirement(self):
        obligation = Obligation("O1", "require", "effect", ())
        contract = Contract("c", 1, (obligation,), ())
        with self.assertRaises(ValueError):
            revise(contract, retire=("O1",), reason="obsolete", authority="")
        with self.assertRaises(ValueError):  # A7: reason is required, not only authority (audit m11)
            revise(contract, retire=("O1",), reason="", authority="human")
        with self.assertRaises(ValueError):
            revise(contract, retire=("O1",), reason="   ", authority="human")
        # Add-only revisions create no Retirement, so ONLY revise()'s own guard can reject a blank
        # reason/authority here (audit m11/m23: verify the guard itself, not defence in depth).
        addition = Obligation("O2", "require", "another effect", ())
        for reason, authority in (("", "human"), ("   ", "human"), ("growth", ""), ("growth", " ")):
            with self.assertRaises(ValueError, msg=f"reason={reason!r} authority={authority!r}"):
                revise(contract, add=(addition,), reason=reason, authority=authority)
        self.assertEqual(2, revise(contract, add=(addition,), reason="growth", authority="human").revision)
        revised = revise(contract, retire=("O1",), reason="obsolete", authority="human")
        self.assertEqual((), revised.obligations)
        self.assertEqual("O1", revised.retired[0].obligation.id)
        self.assertEqual(("c@1",), revised.supersedes)
        self.assertEqual(1, revised.parent_revision)
        self.assertEqual(1, contract.revision)


class Fixtures(unittest.TestCase):
    def test_every_fixture_executes_without_case_specific_test_code(self):
        paths = sorted(FIXTURES.glob("*.json"))
        self.assertGreaterEqual(len(paths), 19)
        for path in paths:
            with self.subTest(fixture=path.name):
                self._execute(json.loads(path.read_text(encoding="utf-8")))

    def _execute(self, fixture):
        if "before" in fixture:
            compare = same_contract if fixture.get("comparison") == "same_contract" else preserved
            actual = compare(fixture["before"], fixture["after"])
            self.assertEqual(fixture["expect"], actual.to_json())
            return
        if "operation" in fixture:
            base = Contract.from_json(fixture["base"])
            operation = fixture["operation"]
            contract = revise(
                base,
                add=tuple(Obligation.from_json(item) for item in operation["add"]),
                retire=tuple(operation["retire"]),
                reason=operation["reason"],
                authority=operation["authority"],
            )
            self.assertEqual(fixture["expect"]["contract"], canonical(contract))
        else:
            contract = Contract.from_json(fixture["contract"])
        observations = tuple(Observation.from_json(item) for item in fixture.get("observations", ()))
        trusted_sources = frozenset(fixture.get("trusted_sources", ()))

        def trusted(item):
            return item.source in trusted_sources

        verdict = verify(contract, observations, trusted)
        expected = fixture["expect"]
        self.assertEqual(expected["verdict"], verdict.to_json())
        accepted = tuple(item for item in observations if trusted(item))
        view = project(contract, verdict, accepted)
        self.assertEqual(expected["view"], view)
        self.assertEqual(expected["text"], render_text(view))
        self.assertEqual(canonical(contract), canonical(parse(view)))
        if "round_trip" in expected:
            self.assertEqual(expected["round_trip"], canonical(parse(view)))
        if "comparison_observations" in fixture:
            comparison = tuple(
                Observation.from_json(item) for item in fixture["comparison_observations"]
            )
            self.assertEqual(verdict, verify(contract, comparison, trusted))


if __name__ == "__main__":
    unittest.main()
