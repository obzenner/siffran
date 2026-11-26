#!/usr/bin/env python3
"""Unit controls for scripts/check_core_annotations.py — every finding class, one negative mutation each.

The guard itself runs in check-static; these controls prove a corrupted annotation set fails with the
finding it should name, so a future schema cannot silently walk the registered definitions out of
alignment (the failure modes: an ingress member annotation quietly removed, an
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


def _patterned_spike_items(documents_map) -> list[dict]:
    """Every embedded spike-images items node across the documents (the shared-defs embedding)."""
    out = []

    def walk(node):
        if isinstance(node, dict):
            if "properties" in node and isinstance(node["properties"], dict) and \
                    "spike_images" in node["properties"] and \
                    isinstance(node["properties"]["spike_images"], dict):
                items = node["properties"]["spike_images"].get("items")
                if isinstance(items, dict) and "pattern" in items:
                    out.append(items)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for document in documents_map.values():
        walk(document)
    return out


_ADMISSIBLE = "test_a_reference_to_an_existing_test_is_admissible"
#: A defect reference naming a committed test (the positive control below).
GUARDED = f"test:{Path(__file__).resolve().relative_to(ROOT).as_posix()}::GuardControlTests.{_ADMISSIBLE}"


class GeneratedMethods(unittest.TestCase):
    """A test class whose method is attached at import, as the conformance harness generates its own."""


GeneratedMethods.test_generated = lambda self: None


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
        """A planted removal: only ``properties.environment.x-core-validator`` goes away, and a guard
        that only walked reachability would stay green, because history still reaches the shared ``$defs``. The declared
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
        """The walk enters schema-valued ``additionalProperties``: a keyword smuggled there is refused."""
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

    def test_a_nested_validator_placement_is_reported_whatever_it_names(self):
        """An x-core-validator under a registered closure, on an interior node,
        naming a callable that never resolved. Discovered anywhere, a placement is a registration —
        an interior one is reported even when the name itself is real."""
        def nested(documents_map):
            _state(documents_map)["$defs"]["environmentMember"]["properties"]["revision"] |= {
                "x-core-validator": "core.run.does_not_exist"}
        found = findings_for(nested)

        def real_name(documents_map):
            _state(documents_map)["$defs"]["environmentMember"]["properties"]["revision"] |= {
                "x-core-validator": "core.run.validate_environment"}
        also = findings_for(real_name)
        for word in (found, also):
            self.assertTrue(any("the inventory never registered" in line for line in word), word)
        self.assertTrue(any("names no callable" in line for line in found), found)
        self.assertFalse(any("names no callable" in line for line in also), also)

    def test_deleting_a_validator_and_its_ingress_entry_together_still_fails(self):
        """Two independent declarations: the inventory runs both ways, so deleting
        both never hides an unregistered definition — the core's own declaration
        (core.contract_registry) still names it."""
        def delete_both(documents_map):
            _state(documents_map).pop("x-core-ingress")
            _state(documents_map)["properties"].pop("environment")
            _state(documents_map)["required"].remove("environment")
        found = findings_for(delete_both)
        self.assertTrue(any("declared by core.contract_registry but the contract registers nothing"
                            in line for line in found), found)

    def test_a_core_registry_drift_is_reported_both_ways(self):
        registry, declared = guard.contract_registry, guard.contract_registry.REGISTERED_DEFINITIONS
        dropped = tuple(entry for entry in declared if entry != ("state", "/properties/environment"))
        extra = (*declared, ("response", "/$defs/fault"))
        try:
            registry.REGISTERED_DEFINITIONS = dropped
            self.assertTrue(any("never declared by core.contract_registry" in line
                                for line in guard.check()), "a missing core-side declaration")
            registry.REGISTERED_DEFINITIONS = extra
            self.assertTrue(any("declared by core.contract_registry but the contract registers nothing"
                                in line for line in guard.check()), "an undeclared core-side key")
        finally:
            registry.REGISTERED_DEFINITIONS = declared

    def test_a_regression_witness_the_draft_word_still_accepts_is_a_contract_bug(self):
        def accepted(documents_map):
            for node in _patterned_spike_items(documents_map):
                node["x-regression-witnesses"] = [
                    {"value": "python:3.12-slim", "defect": GUARDED}]
        found = findings_for(accepted)
        self.assertTrue(any("the node's Draft 2020-12 word still accepts" in line for line in found),
                        found)

    def test_the_draft_word_uses_search_semantics_not_fullmatch(self):
        """An *unanchored* pattern ``a`` accepts ``ba`` under the
        draft (the pattern keyword searches) — an ``any(re.fullmatch(...))`` guard would refuse ``ba``
        as a negative witness it cannot refuse and accept ``a`` when one conjunct refuses it;
        the guard's word is the draft verdict."""
        def unanchored(documents_map):
            for node in _patterned_spike_items(documents_map):
                node["x-regression-witnesses"] = [{"value": "ba", "defect": GUARDED}]
        found = findings_for(unanchored)
        self.assertTrue(any("the node's Draft 2020-12 word still accepts: 'ba'" in line for line in found),
                        found)

    def test_a_malformed_regression_corpus_is_refused(self):
        def malformed(documents_map):
            for node in _patterned_spike_items(documents_map):
                node["x-regression-witnesses"] = {"a": 1}
        found = findings_for(malformed)
        self.assertTrue(any("corpus must be a non-empty list" in line for line in found), found)

    def test_a_flooded_corpus_of_ten_thousand_is_refused(self):
        """Ten thousand duplicated bare entries would each become a candidate. The cap, the duplicates, and the shape all fire."""
        def flooded(documents_map):
            for node in _patterned_spike_items(documents_map):
                node["x-regression-witnesses"] = ["duplicate"] * 10000
        found = findings_for(flooded)
        self.assertTrue(any("exceeds the cap of 8" in line for line in found), found)
        self.assertTrue(any("must be an object of exactly" in line for line in found), found)

    def test_a_corpus_of_nine_barely_overflows_and_is_refused(self):
        def nine(documents_map):
            for node in _patterned_spike_items(documents_map):
                node["x-regression-witnesses"] = [
                    {"value": f"x{index}", "defect": GUARDED}
                    for index in range(9)]
        found = findings_for(nine)
        self.assertTrue(any("exceeds the cap of 8" in line for line in found), found)

    def test_duplicate_corpus_values_are_refused(self):
        def duplicated(documents_map):
            for node in _patterned_spike_items(documents_map):
                node["x-regression-witnesses"] = [
                    {"value": "same", "defect": GUARDED},
                    {"value": "same", "defect": GUARDED}]
        found = findings_for(duplicated)
        self.assertTrue(any("duplicated regression entry value" in line for line in found), found)

    def test_an_entry_without_its_evidenced_defect_reference_is_refused(self):
        def bare(documents_map):
            for node in _patterned_spike_items(documents_map):
                node["x-regression-witnesses"] = [{"value": "somevalue"}]
        found = findings_for(bare)
        self.assertTrue(any("an object of exactly \"value\" and \"defect\"" in line for line in found),
                        found)

        def blank(documents_map):
            for node in _patterned_spike_items(documents_map):
                node["x-regression-witnesses"] = [{"value": "gv", "defect": "   "}]
        also = findings_for(blank)
        self.assertTrue(any("without its evidenced defect reference" in line for line in also), also)

    def test_a_witness_value_over_the_cap_alone_is_refused(self):
        """The value carries its own length bound, so no hand-written case of any size is banked. With the node's own maxLength gone the 256-character cap is the only ceiling:
        257 characters is refused, and 256 at the cap stays admissible."""
        def over(documents_map):
            for node in _patterned_spike_items(documents_map):
                node.pop("maxLength", None)
                node["x-regression-witnesses"] = [
                    {"value": "x" * 257, "defect": GUARDED}]
        found = findings_for(over)
        self.assertTrue(any("outside the node's length bounds" in line and "257" in line
                            for line in found), found)

        def at_cap(documents_map):
            for node in _patterned_spike_items(documents_map):
                node.pop("maxLength", None)
                node["x-regression-witnesses"] = [
                    {"value": "X" * 256, "defect": GUARDED}]
        also = findings_for(at_cap)
        self.assertEqual(also, [], "a value inside the cap and refused by the draft word banked cleanly")

    def test_a_witness_value_outside_the_nodes_own_bounds_is_refused(self):
        """The node's own minLength/maxLength bound the value where they are smaller than the cap: a
        length refusal is a shape defect the generated length challenges already cover, never a
        pattern regression."""
        def over(documents_map):
            for node in _patterned_spike_items(documents_map):
                node["x-regression-witnesses"] = [
                    {"value": "x" * 300, "defect": GUARDED}]
        found = findings_for(over)
        self.assertTrue(any("outside the node's length bounds" in line and "(1 to 255" in line
                            for line in found), found)

        def under(documents_map):
            for node in _patterned_spike_items(documents_map):
                node["x-regression-witnesses"] = [
                    {"value": "", "defect": GUARDED}]
        also = findings_for(under)
        self.assertTrue(any("outside the node's length bounds" in line for line in also), also)

    def test_a_defect_reference_over_the_cap_is_refused(self):
        """The reference is bounded at 120 characters even when its shape is the structured form, so
        prose of any length is never smuggled in through a long path."""
        shape = "test:{}/test_x.py::A.test_b"
        long_reference = shape.format("p" * (guard.REGRESSION_DEFECT_CAP + 3 - len(shape)))
        self.assertEqual(len(long_reference), guard.REGRESSION_DEFECT_CAP + 1)

        def prose(documents_map):
            for node in _patterned_spike_items(documents_map):
                node["x-regression-witnesses"] = [{"value": "x", "defect": long_reference}]
        found = findings_for(prose)
        self.assertTrue(any(f"exceeds the cap of {guard.REGRESSION_DEFECT_CAP}" in line for line in found), found)

    def test_an_unstructured_or_unresolvable_defect_reference_is_refused(self):
        """The reference must be structured evidence — 'test:<repository path>::<Class>.<test_name>' —
        never a bare word, and it must name a committed test: a missing file, class or test, or a
        path leaving the repository, is refused."""
        test_file = Path(__file__).resolve().relative_to(ROOT).as_posix()
        for defect, finding in (
                ("a reference", "not structured evidence"),
                ("defects.md#5", "not structured evidence"),
                (f"test:{test_file}::GuardControlTests", "not structured evidence"),
                ("test:scripts/tests/test_absent.py::GuardControlTests.test_x", "naming no committed test"),
                (f"test:{test_file}::AbsentTests.{_ADMISSIBLE}", "naming no committed test"),
                (f"test:{test_file}::GuardControlTests.test_absent", "naming no committed test"),
                ("test:../tests/test_x.py::GuardControlTests.test_x", "not structured evidence")):
            def corpus(documents_map, defect=defect):
                for node in _patterned_spike_items(documents_map):
                    node["x-regression-witnesses"] = [{"value": "x", "defect": defect}]
            with self.subTest(defect=defect):
                found = findings_for(corpus)
                self.assertTrue(any(finding in line for line in found), found)

    def test_a_named_test_must_resolve_inside_the_repository(self):
        """A link inside the repository to a test file outside it names no committed test."""
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            outside, root = base / "outside", base / "repository"
            outside.mkdir()
            (root / "tests").mkdir(parents=True)
            (outside / "test_far.py").write_text(
                "import unittest\nclass Far(unittest.TestCase):\n    def test_x(self):\n        pass\n")
            (root / "tests" / "test_far.py").symlink_to(outside / "test_far.py")
            (root / "tests" / "test_near.py").write_text((outside / "test_far.py").read_text())
            original = guard.ROOT
            guard.ROOT = root
            try:
                self.assertFalse(guard._named_test_exists("tests/test_far.py", "Far", "test_x"))
                self.assertTrue(guard._named_test_exists("tests/test_near.py", "Far", "test_x"))
                self.assertFalse(guard._named_test_exists("tests/test_near.py", "Far", "test_y"))
                (root / "tests" / "test_broken.py").write_text("raise ImportError('unimportable')\n")
                self.assertFalse(guard._named_test_exists("tests/test_broken.py", "Far", "test_x"))
                self.assertIsNone(guard._TEST_MODULES[(root / "tests" / "test_broken.py")])
                # the file's own directory resolves its sibling imports, and is removed again after
                (root / "tests" / "sibling_helper.py").write_text("NAME = 'Far'\n")
                (root / "tests" / "test_sibling.py").write_text(
                    "import unittest\nfrom sibling_helper import NAME\n"
                    "globals()[NAME] = type(NAME, (unittest.TestCase,), {'test_x': lambda self: None})\n")
                # a same-named module earlier on the path never shadows the file's own sibling
                (outside / "sibling_helper.py").write_text("NAME = 'Shadow'\n")
                sys.path.insert(0, str(outside))
                try:
                    path_before = list(sys.path)
                    self.assertTrue(guard._named_test_exists("tests/test_sibling.py", "Far", "test_x"))
                    self.assertEqual(sys.path, path_before)
                finally:
                    sys.path.remove(str(outside))
                    sys.modules.pop("sibling_helper", None)
                self.assertEqual(guard._TEST_MODULES[root / "tests" / "test_sibling.py"].__name__,
                                 "_regression_witness_test_sibling")
            finally:
                guard.ROOT = original

    def test_a_reference_to_an_existing_test_is_admissible(self):
        """The positive control: a reference naming a committed test banks cleanly, a generated test
        method exactly like a hand-written one."""
        def named(documents_map):
            for node in _patterned_spike_items(documents_map):
                node["x-regression-witnesses"] = [
                    {"value": "Python:94x", "defect": GUARDED},
                    {"value": "Python:3.13", "defect": f"test:{Path(__file__).resolve().relative_to(ROOT).as_posix()}"
                                                      f"::GeneratedMethods.test_generated"}]
        found = findings_for(named)
        self.assertEqual(found, [], "a reference to an existing test banks cleanly")

    def test_a_work_limit_override_must_name_bounds_the_engine_reads(self):
        def override(documents_map):
            for node in _patterned_spike_items(documents_map):
                node |= {"maxItems": 4096, "x-work-limit": {"maxWords": 8}}
        found = findings_for(override)
        self.assertTrue(any("an x-work-limit bound the engine does not read" in line for line in found),
                        found)
        self.assertTrue(any("the feasible-work limit refuses" in line for line in found), found)

    def test_a_declared_work_limit_override_makes_the_bound_feasible(self):
        def override(documents_map):
            for node in _patterned_spike_items(documents_map):
                node |= {"maxItems": 4096, "x-work-limit": {"maxItems": 4096}}
        found = findings_for(override)
        self.assertFalse(any("the feasible-work limit refuses" in line for line in found), found)

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
        """The mechanism registers the document by its declaration — and the two-declaration
        discipline reports exactly what a brand-new document must still do: declare its keys
        on the core side too (core.contract_registry), which the synthetic does not."""
        registry = guard.contract_registry
        declared = registry.REGISTERED_DEFINITIONS
        registry.REGISTERED_DEFINITIONS = ()  # a synthetic document declares nothing on the core side
        try:
            found = guard.check({"conditional": copy.deepcopy(CONDITIONAL)})
        finally:
            registry.REGISTERED_DEFINITIONS = declared
        self.assertEqual([line for line in found if "disagree" in line],
                         ["conditional#/$defs/failure: registered by the contract's x-core-ingress "
                          "inventory but never declared by core.contract_registry — the two "
                          "declarations disagree"], found)
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

    def test_the_docker_spike_record_document_registers_by_declaration(self):
        """E5's /3 qualification record: the harness discovers the docker-spike contract directory
        with the v2 surface, so the record registers through its own declarations — its root and every
        $defs definition its ingress reaches — with no harness change beyond that discovery, and the
        guard reports no disagreement for it."""
        documents = module_documents()
        self.assertIn("qualification-record", documents,
                      "the docker-spike contract directory is part of the discovery surface")
        contract = engine.Contract()
        self.assertIn("qualification-record#/", contract.registered)
        for definition in ("digest", "elapsedMs", "identifier", "counter", "runBinding",
                           "spikeImageRow", "probeImage", "repoDigest", "spikeImages", "probe"):
            self.assertIn(f"qualification-record#/$defs/{definition}", contract.registered)
        self.assertFalse([line for line in guard.check() if "qualification-record" in line],
                        "the record's two declarations agree: "
                        + "\n".join(guard.check()))


if __name__ == "__main__":
    unittest.main()
