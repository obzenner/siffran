"""The observer read model's contract, by generation (T1 B): Hypothesis strategies derived from the v1 schemas
(``ui_view_strategies``), under a fixed seed and a bounded count, database-free.

* every generated summary, row, page and detail validates against its schema (the generator derives only what the
  schema allows);
* every value every enumeration declares is producible (a document pinned to it holds it, by the validator's own walk):
  this replaces the checked-in ``coverage.json``;
* a generated document with one enumeration value the contract does not declare is rejected by the validator.

Dev-only (Hypothesis): ``make check-static`` runs it under ``.venv`` (``make dev-install``).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hypothesis import HealthCheck, Phase, given, settings, strategies as st  # noqa: E402

import ui_view_strategies as S  # noqa: E402
import validate_ui_view as v  # noqa: E402

GEN = S.Strategies()
UI = GEN.ui
NAMES = ("summary", "row", "page", "run")
BOUNDED = settings(max_examples=60, derandomize=True, database=None, deadline=None, phases=[Phase.generate],
                   suppress_health_check=list(HealthCheck))


class GeneratedDocumentsValidateTests(unittest.TestCase):
    def test_every_generated_document_validates_against_its_schema(self):
        for name in NAMES:
            with self.subTest(name):
                @BOUNDED
                @given(GEN.document(name))
                def holds(doc):
                    self.assertEqual(v._schema_errors(UI[name], doc, name, UI), [])
                holds()

    def test_a_generated_document_with_an_undeclared_enumeration_value_is_rejected(self):
        @BOUNDED
        @given(GEN.node(UI["row"]["$defs"]["row"], UI["row"], "/$defs/row"), st.sampled_from(["", "not-a-standing", "PROVEN"]))
        def rejected(row, word):
            self.assertTrue(v._schema_errors(UI["row"], [{**row, "standing": word}], "row", UI))
        rejected()


class EveryDeclaredValueIsProducibleTests(unittest.TestCase):
    """Each declared value is held by a generated document, exactly: for every value of every enumeration, a generator
    with that enumeration pinned to the value (``pin``: every ``oneOf`` on the way takes a branch that leads to it, every
    optional property present) makes a document that validates and holds it, by the validator's own walk. Reachability
    is then per value, independent of a sweep's size, its seed or the schemas' other text. A value no pinned document
    holds is a node the generator cannot get to, or one the validator's walk cannot see."""

    def test_every_enumeration_value_is_producible(self):
        declared = S.declared(UI)
        ids = {f"{name}.schema.json": UI[name]["$id"] for name in NAMES}
        missing = []
        for enum, values in declared.items():
            file, _, pointer = enum.partition("#")
            name = file.removesuffix(".schema.json")
            for value in values:
                pinned = S.Strategies(full=True, pin=(ids[file], pointer, value))
                held = []

                @settings(BOUNDED, max_examples=1)
                @given(pinned.document(name))
                def one(doc):
                    self.assertEqual(v._schema_errors(UI[name], doc, name, UI), [])
                    held.append(repr(value) in {repr(x) for x in S.held(name, doc, UI).get(enum, set())})
                one()
                if not all(held):
                    missing.append((enum, value))
        self.assertEqual(missing, [])
        self.assertGreater(len(declared), 40)

if __name__ == "__main__":
    unittest.main()
