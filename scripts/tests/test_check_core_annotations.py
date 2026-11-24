#!/usr/bin/env python3
"""Unit controls for scripts/check_core_annotations.py — every finding class, one negative mutation each.

The guard itself runs in check-static; these controls prove a corrupted annotation set fails with the
finding it should name, so a future schema cannot silently walk the annotated definitions out of alignment
(the failure mode the E2a review found: a core-validated shape that drifts from its schema).
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

import check_core_annotations as guard  # noqa: E402
from contract_strategies import state_schema  # noqa: E402


def findings_for(mutation) -> list[str]:
    schema = copy.deepcopy(state_schema())
    mutation(schema)
    return guard.check(schema)


class GuardControlTests(unittest.TestCase):
    def test_the_real_contract_is_consistent(self):
        self.assertEqual(guard.check(), [])

    def test_a_reachable_definition_without_its_annotation(self):
        def forget(schema: dict) -> None:
            schema["$defs"]["environmentMember"].pop("x-core-validator")

        found = findings_for(forget)
        self.assertTrue(any("environmentMember" in line and "no annotation" in line for line in found), found)

    def test_a_validator_that_names_no_real_callable(self):
        def rename(schema: dict) -> None:
            schema["$defs"]["qualificationAttempt"]["x-core-validator"] = "core.run.no_such_function"

        self.assertTrue(any("no callable" in line for line in findings_for(rename)),
                        findings_for(rename))

    def test_an_annotation_no_state_member_reaches_is_orphaned(self):
        def orphan(schema: dict) -> None:
            schema["$defs"]["claimId"]["x-core-validator"] = "core.run.validate_environment"

        self.assertTrue(any("orphaned" in line for line in findings_for(orphan)),
                        findings_for(orphan))

    def test_a_state_member_annotation_must_name_a_required_member(self):
        def optional(schema: dict) -> None:
            schema["required"].remove("environment_history")
            schema["properties"]["environment_history"]["x-core-validator"] = "core.run.validate_environment_history"

        self.assertTrue(any("must be required" in line for line in findings_for(optional)),
                        findings_for(optional))

    def test_a_core_only_entry_names_its_one_missing_test(self):
        def unname(schema: dict) -> None:
            schema["$defs"]["environmentMember"]["x-core-only"][0]["test"] = \
                "plugins/empirica/tests/test_run5_binding.py::Run5EnvironmentStateTests.test_never_written"

        self.assertTrue(any("x-core-only test does not exist" in line for line in findings_for(unname)),
                        findings_for(unname))

    def test_a_core_only_entry_without_a_test_is_refused_wholesale(self):
        def mute(schema: dict) -> None:
            schema["$defs"]["environmentMember"]["x-core-only"][0]["test"] = ""

        found = findings_for(mute)
        self.assertTrue(any("a rule and its one test" in line or "path::Class.test" in line for line in found), found)

    def test_a_keyword_the_engines_do_not_derive(self):
        def grow(schema: dict) -> None:
            schema["$defs"]["environmentProposal"]["properties"]["rationale"] |= {"multipleOf": 2}

        self.assertTrue(any("do not derive" in line for line in findings_for(grow)),
                        findings_for(grow))

    def test_an_unknown_x_annotation_is_not_spawnable(self):
        def spawn(schema: dict) -> None:
            schema["properties"]["environment"]["x-some-future"] = True

        self.assertTrue(any("do not derive" in line for line in findings_for(spawn)),
                        findings_for(spawn))


if __name__ == "__main__":
    unittest.main()
