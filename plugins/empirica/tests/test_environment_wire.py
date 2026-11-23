#!/usr/bin/env python3
"""Empirica 7.2 E6b: cross-source coherence for the environment/qualification wire vocabulary.

Under ADR-0075's test rule, shape and bound checks live with the contract conformance harness
(E2c), so this module owns only behaviour the fast suites can keep honest without repeating a
single contract value: the response Fault reason vocabulary stays the same closed set in every
source that owns it (the code constants, the response schema, the rendered limits clause and its
committed copy), so a value cannot be added on one side alone
<doc/design/specs/empirica-7.2-run-scoped-environment-qualification.md> (§5.1).
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application import generic_spike, protocol

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "scripts"))
from validate_docker_spike_contract import REASON_HINTS, LIMITS_ID, render_limits  # noqa: E402

_PUBLIC_CONTRACT = json.loads(
    (_ROOT / "contracts/empirica/v2/public-contract.json").read_text(encoding="utf-8"))


def _committed_limits_clause() -> dict:
    clauses = _PUBLIC_CONTRACT["sections"]["evidence/spike"]["clauses"]
    rows = [clause for clause in clauses if clause["id"] == LIMITS_ID]
    assert len(rows) == 1, LIMITS_ID
    return rows[0]


def _fault_reason_enum() -> list:
    return protocol.response_schema()["$defs"]["fault"]["properties"]["reason"]["enum"]


class FaultReasonVocabulary(unittest.TestCase):
    """No environment or qualification reason may drift between its three owners."""

    def test_the_schema_fault_enum_is_exactly_the_code_vocabulary_in_its_order(self):
        self.assertEqual(_fault_reason_enum(), list(generic_spike.SPIKE_FAULT_REASONS))

    def test_every_fault_reason_has_one_hint_in_the_renderer(self):
        self.assertEqual(sorted(REASON_HINTS), sorted(generic_spike.SPIKE_FAULT_REASONS))
        for reason, hint in REASON_HINTS.items():
            self.assertGreater(len(hint.strip()), 20, reason)

    def test_the_renderer_prints_exactly_the_hinted_reasons_into_the_limits_clause(self):
        limits = render_limits()
        rows = dict(row.split(" | ", 1) for row in limits.split("\n")
                    if row.startswith("reason "))
        self.assertEqual(sorted(key.removeprefix("reason ") for key in rows),
                         sorted(generic_spike.SPIKE_FAULT_REASONS))
        for key, hint in rows.items():
            self.assertEqual(hint, REASON_HINTS[key.removeprefix("reason ")], key)

    def test_the_committed_limits_clause_is_the_renderer_output_unchanged(self):
        self.assertEqual(_committed_limits_clause()["text"], render_limits())

    def test_the_renderer_tells_the_environment_step_and_the_pinned_probe_image(self):
        limits = render_limits()
        self.assertIn("Environment step", limits)
        self.assertIn("probe image", limits)
        self.assertIn("digest", limits)


class NoProducerYet(unittest.TestCase):
    """The environment/qualification reads are declared optional and produced by nobody yet."""

    def test_the_current_runs_views_stay_valid_without_the_new_projections(self):
        from test_environment_actions import ServiceEnv
        run = ServiceEnv().start()
        result = run.request({"type": "GetRun", "run_id": run.run_id})
        self.assertTrue(protocol.validate_public_result(result), result)
        view = result["run"]
        for projection in ("environment", "qualification"):
            runview = protocol.response_schema()["$defs"]["runView"]
            self.assertIn(projection, runview["properties"], projection)
            self.assertNotIn(projection, runview["required"], projection)
            self.assertNotIn(projection, view, projection)


if __name__ == "__main__":
    unittest.main()
