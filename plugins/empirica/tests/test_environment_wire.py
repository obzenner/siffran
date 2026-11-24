#!/usr/bin/env python3
"""Empirica 7.2 E6b: the environment/qualification wire vocabulary and its read projections.

Under ADR-0075's test rule, shape and bound coverage of the declared wire members belongs to the
contract conformance harness (E2c), and the renderer, hint and registry mirrors belong with the
validation scripts that own them (validate_docker_spike_contract renders and compares the
``evidence/spike/limits`` clause over the full closed reason set every gate), so this module
keeps only what a fast suite can keep honest without restating a single contract value: one
generic consistency gate — the response Fault reason vocabulary stays the same closed set in the
code that owns it and the schema that renders it — and one behaviour fact — no current producer
emits the new run-view projections
<plugins/../doc/design/specs/empirica-7.2-run-scoped-environment-qualification.md> (§5.1).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.sandbox import generic_qualification
from application import generic_spike, protocol


class FaultReasonVocabulary(unittest.TestCase):
    """One generic code/schema consistency gate for the reason enums, nothing else."""

    def test_the_schema_fault_enum_is_exactly_the_code_vocabulary_in_its_order(self):
        enum = protocol.response_schema()["$defs"]["fault"]["properties"]["reason"]["enum"]
        self.assertEqual(enum, list(generic_spike.SPIKE_FAULT_REASONS))
        # The kept, uncollapsed /2 attempt failures (deadline, egress, cleanup) reach the wire
        # spelled exactly as their generic_qualification owner spells them, never a new one (R12).
        for reason in (generic_qualification.QualificationReason.DEADLINE_EXCEEDED,
                       generic_qualification.QualificationReason.NETWORK_EGRESS_UNAVAILABLE,
                       generic_qualification.QualificationReason.CLEANUP_UNAVAILABLE):
            self.assertIn(reason.value, enum, reason)


class NoProducerYet(unittest.TestCase):
    """The environment/qualification reads are declared optional and produced by nobody yet."""

    def test_the_current_runs_views_stay_valid_without_the_new_projections(self):
        from test_environment_actions import ServiceEnv
        run = ServiceEnv().start()
        result = run.request({"type": "GetRun", "run_id": run.run_id})
        self.assertTrue(protocol.validate_public_result(result), result)
        for projection in ("environment", "qualification"):
            self.assertNotIn(projection, result["run"], projection)


if __name__ == "__main__":
    unittest.main()
