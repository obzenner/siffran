#!/usr/bin/env python3
"""Unit controls for scripts/check_core_annotations.py — every finding class, one negative mutation each.

The guard itself runs in check-static; these controls prove a corrupted annotation set fails with the
finding it should name, so a future schema cannot silently walk the registered definitions out of
alignment (the failure modes the E2a review found: an ingress member annotation quietly removed, an
``x-core-only`` rule parked on an unannotated definition, and an annotation inside schema-valued
``additionalProperties`` the walk skipped).

The guard reads every ``contracts/empirica/v2`` document; the controls mutate the real loaded set (or
the synthetic conditional declaration below for the registration-form rules).
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
import contract_strategies as engine  # noqa: E402
from contract_strategies import module_documents  # noqa: E402


def findings_for(mutation, documents_map=None) -> list[str]:
    loaded = copy.deepcopy(documents_map if documents_map is not None else module_documents())
    mutation(loaded)
    return guard.check(loaded)


def _state(documents_map) -> dict:
    return documents_map["state"]


class GuardControlTests(unittest.TestCase):
    def test_the_real_contracts_are_consistent(self):
        self.assertEqual(guard.check(), [])

    def test_an_ingress_member_annotation_cannot_quietly_disappear(self):
        """The review's planted removal: only ``properties.environment.x-core-validator`` went away and
        the old guard stayed green, because history still reached the shared ``$defs``. The declared
        ingress inventory demands its annotation."""
        def drop(documents_map):
            _state(documents_map)["properties"]["environment"].pop("x-core-validator")
        found = findings_for(drop)
        self.assertTrue(any("carries no x-core-validator" in line for line in found), found)

    def test_a_core_only_rule_cannot_park_on_an_unannotated_definition(self):
        def orphan(documents_map):
            _state(documents_map)["$defs"]["digest256"]["x-core-only"] = [
                {"rule": "a rule no one owns", "test": "plugins/empirica/tests/test_run5_binding.py::X.y"}]
        found = findings_for(orphan)
        self.assertTrue(any("orphaned x-core-only" in line for line in found), found)

    def test_a_document_with_annotations_must_declare_its_ingress(self):
        def undeclared(documents_map):
            _state(documents_map).pop("x-core-ingress")
        found = findings_for(undeclared)
        self.assertTrue(any("declares no x-core-ingress" in line for line in found), found)

    def test_an_ingress_entry_must_be_a_local_pointer(self):
        def malformed(documents_map):
            _state(documents_map)["x-core-ingress"][0] = "#/properties/environment"
        found = findings_for(malformed)
        self.assertTrue(any("must be a local pointer" in line for line in found), found)

    def test_an_ingress_member_must_be_required_by_its_object(self):
        def optional(documents_map):
            _state(documents_map)["required"].remove("environment_history")
        found = findings_for(optional)
        self.assertTrue(any("must be required by its object" in line for line in found), found)

    def test_a_reachable_definition_without_its_annotation(self):
        def forget(documents_map):
            _state(documents_map)["$defs"]["environmentMember"].pop("x-core-validator")
        found = findings_for(forget)
        self.assertTrue(any("environmentMember" in line and "no x-core-validator" in line
                            for line in found), found)

    def test_a_forgettable_def_annotation_is_caught_through_the_ingress_inventory(self):
        """The proposal def is reachable from the member; removing its annotation leaves it reachable
        and unregistered, which the closure inventory reports."""
        def forget(documents_map):
            for document in documents_map.values():
                if "$defs" in document and "environmentProposal" in document["$defs"]:
                    document["$defs"]["environmentProposal"].pop("x-core-validator", None)
        found = findings_for(forget)
        self.assertTrue(any("no x-core-validator" in line and "environmentProposal" in line
                            for line in found), found)

    def test_a_validator_that_names_no_real_callable(self):
        def rename(documents_map):
            for document in documents_map.values():
                if "$defs" in document and "environmentProposal" in document["$defs"]:
                    document["$defs"]["environmentProposal"]["x-core-validator"] = "core.environment.no_such_function"
        self.assertTrue(any("names no callable" in line for line in findings_for(rename)),
                        findings_for(rename))

    def test_a_core_only_entry_names_its_one_missing_test(self):
        def unname(documents_map):
            node = _state(documents_map)["$defs"]["environmentMember"]
            node["x-core-only"][0]["test"] = \
                "plugins/empirica/tests/test_run5_binding.py::Run5EnvironmentStateTests.test_never_written"
        self.assertTrue(any("x-core-only test does not exist" in line for line in findings_for(unname)),
                        findings_for(unname))

    def test_a_core_only_case_must_name_a_declared_candidate_kind(self):
        def recase(documents_map):
            node = _state(documents_map)["$defs"]["environmentMember"]["properties"]["revision"]
            node["x-core-only"][0]["case"] = "float-for-string"
        found = findings_for(recase)
        self.assertTrue(any("must name a harness-declared candidate kind" in line for line in found), found)

    def test_a_keyword_the_engines_do_not_derive(self):
        def grow(documents_map):
            _state(documents_map)["$defs"]["environmentProposal"]["properties"]["rationale"] |= {"multipleOf": 2}
        found = findings_for(grow)
        self.assertTrue(any("do not derive" in line for line in found), found)

    def test_a_keyword_inside_schema_valued_additional_properties(self):
        """The review found the old walk skipping schema-valued ``additionalProperties``; a smuggled
        keyword there must be refused now the walk enters it."""
        def smuggle(documents_map):
            node = _state(documents_map)["$defs"]["environmentMember"]["properties"]["revision"]
            node["additionalProperties"] = {"multipleOf": 2, "type": "integer"}
        found = findings_for(smuggle)
        self.assertTrue(any("do not derive" in line and "additionalProperties" in line
                            for line in found), found)

    def test_an_unknown_x_annotation_is_not_spawnable(self):
        def spawn(documents_map):
            _state(documents_map)["properties"]["environment"]["x-some-future"] = True
        found = findings_for(spawn)
        self.assertTrue(any("do not derive" in line for line in findings_for(spawn)), found)

    def test_an_orphaned_x_grammar_is_refused(self):
        def orphan(documents_map):
            _state(documents_map)["$defs"]["claimId"]["x-grammar"] = {"a-piece": "a"}
        found = findings_for(orphan)
        self.assertTrue(any("orphaned x-grammar" in line for line in found), found)

    def test_an_x_grammar_piece_must_compile(self):
        def broken(documents_map):
            item = _state(documents_map)["$defs"]["environmentProposal"]["properties"]["spike_images"]["items"]
            item["x-grammar"]["tag"] = "(?P<unclosed"
        found = findings_for(broken)
        self.assertTrue(any("does not compile" in line for line in found), found)


CONDITIONAL = {  # the oneOf conditional form a later node registers by declaration (ADR-0075)
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://siffran.test/conditional",
    "type": "object",
    "x-core-ingress": ["/$defs/failure"],
    "properties": {"failure": {"$ref": "#/$defs/failure"}},
    "required": ["failure"],
    "$defs": {"failure": {
        "x-core-validator": "core.freshness.validate_digest256",
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


class RegistrationByDeclarationTests(unittest.TestCase):
    """A synthetic document registers through its declaration: ingress, annotation, and the harness's
    conditional-form derivation, with no harness change — and the guard accepts it as it stands."""

    def test_the_synthetic_conditional_declaration_is_consistent(self):
        self.assertEqual(guard.check({"conditional": copy.deepcopy(CONDITIONAL)}), [])
        contract = engine.Contract({"conditional": copy.deepcopy(CONDITIONAL)})
        self.assertEqual(sorted(contract.registered), ["conditional#/$defs/failure"])

    def test_the_response_document_registers_a_fault_by_declaration_only(self):
        """E6b's coming shape: an empty ingress list declares that the response document has no core
        entry point yet; a guard run stays green today and the inventory accepts the coming pointer."""
        documents_map = copy.deepcopy(module_documents())
        documents_map["response"]["x-core-ingress"].append("/$defs/fault")
        documents_map["response"]["$defs"]["fault"]["x-core-validator"] = "core.examples.later"
        found = guard.check(documents_map)
        self.assertTrue(any("does not import" in line and "fault" in line for line in found),
                        "an unimplemented validator is reported, the mechanism already registered it:"
                        + "\n" + "\n".join(found))


if __name__ == "__main__":
    unittest.main()
