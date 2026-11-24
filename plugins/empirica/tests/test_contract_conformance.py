#!/usr/bin/env python3
"""ADR-0075 contract conformance: schema and core agree, in both directions, for every annotated definition.

A definition that the core validates declares itself in the contract with ``x-core-validator`` (and, for a rule no
schema can express, an ``x-core-only`` entry with the one test that pins it). This harness is the ADR-0075 half of
the shape testing: valid documents are drawn from the definition's keywords by ``scripts/contract_strategies.py``
(the shared T1 engine) and the core's named validator must accept each; invalid documents derived from the same
keywords — a bound one past, a character outside a pattern, a wrong type, a missing or extra key, a null row,
a value outside an enumeration, and, core-side only, an unsized iterable or a mapping where a sequence is
required — must be refused by both Draft 2020-12 and the core. A derived candidate the schema accepts is not an
invalid document and is never asserted here.

Every Hypothesis run is bounded, fixed-seeded and database-free (doc/testing.md, T1). Registration is by
declaration: an annotated definition lands here with no other change, so later nodes add theirs in the contract
alone. A shapes hand-written test would violate the operator rule; behaviour lives in the other modules.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[2]
sys.path[:0] = [str(HERE), str(HERE.parent), str(REPOSITORY / "scripts")]

from hypothesis import given, settings  # noqa: E402

from contract_strategies import Contract  # noqa: E402

_CONTRACT = Contract()
_PROPERTY = settings(max_examples=12, deadline=None, database=None, derandomize=True)
_VALIDATORS = {pointer: _CONTRACT.import_validator(pointer) for pointer in _CONTRACT.annotated}


def _slug(pointer: str) -> str:
    return pointer.removeprefix("#/").replace("$defs/", "defs_").replace("properties/", "member_") \
        .replace("-", "").replace("/", "_")


def _contract_summary(pointer: str) -> str:
    return f"{pointer} declares {len(_CONTRACT.cases(pointer))} invalid-document cases"


class _Base(unittest.TestCase):
    def assertRefused(self, pointer: str, value: object, label: str) -> None:
        try:
            _VALIDATORS[pointer](value)
        except (ValueError, TypeError):
            return
        raise self.failureException(
            f"{pointer}: the named validator accepted a derived invalid document — {label}: {value!r}")

    def assertAccepted(self, pointer: str, value: object) -> None:
        self.assertTrue(_CONTRACT.accepts(pointer, value),
                        f"a drawn {pointer} document the schema itself refuses")
        _VALIDATORS[pointer](value)


class ValidDrawTests(_Base):
    """Every drawn valid document is accepted by both sides: the schema it was drawn from and the core
    validator the definition names — an agreement in the accepting direction."""


class ValidFullDrawTests(_Base):
    """The reachability sweep: every optional member present, every array non-empty, the null alternative
    never taken — one document reaches every declared node."""


class DerivedInvalidTests(_Base):
    """Every derived invalid document is refused by both sides (the core-only kinds by the core alone)."""


class BoundaryAcceptanceTests(_Base):
    """The boundary documents of the definitions' own maxima — an array grown to its maxItems (uniqueness
    held), a string padded to its maxLength (patterns held) — accepted by both sides: sampling never
    promises to reach a bound, so a validator that narrowed one only ever held through nobody visiting."""


for _pointer in sorted(_CONTRACT.annotated):
    def _valid(pointer: str = _pointer):  # noqa: F811 — one declared harness method per annotated definition
        @settings(_PROPERTY)
        @given(document=_CONTRACT.drawn(pointer))
        def method(self, document) -> None:
            self.assertAccepted(pointer, document)
        return method

    def _valid_full(pointer: str = _pointer):
        @settings(_PROPERTY)
        @given(document=_CONTRACT.drawn(pointer, full=True))
        def method(self, document) -> None:
            self.assertAccepted(pointer, document)
        return method

    def _invalid(pointer: str = _pointer):
        @settings(_PROPERTY)
        @given(base=_CONTRACT.drawn(pointer, full=True))
        def method(self, base) -> None:
            refused = 0
            for case in _CONTRACT.cases(pointer):
                for derived in _CONTRACT.derive(base, pointer, case):
                    self.assertRefused(pointer, derived.value, derived.label)
                    refused += 1
            self.assertGreater(refused, 0,
                               f"no derived invalid document fired for this draw; "
                               f"{_contract_summary(pointer)}")
        return method

    def _boundary(pointer: str = _pointer):
        @settings(max_examples=2, deadline=None, database=None, derandomize=True)
        @given(base=_CONTRACT.drawn(pointer, full=True))
        def method(self, base) -> None:
            grown = _CONTRACT.at_bounds(pointer, base)
            self.assertTrue(grown, f"{pointer} declares no maximum to hold a grown document to")
            for derived in grown:
                self.assertAccepted(pointer, derived.value)
                self.assertNotEqual(derived.value, base)
        return method

    for _group, _builder in ((ValidDrawTests, _valid), (ValidFullDrawTests, _valid_full),
                             (DerivedInvalidTests, _invalid), (BoundaryAcceptanceTests, _boundary)):
        setattr(_group, f"test_{_slug(_pointer)}", _builder(_pointer))


if __name__ == "__main__":
    unittest.main()
