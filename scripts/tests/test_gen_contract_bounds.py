#!/usr/bin/env python3
"""Unit controls for scripts/gen_contract_bounds.py — the generator's ownership is checked, not assumed.

Two disjoint branches under one path with different bounds, or two properties whose names normalize
alike, are a named conflict, never silently resolved by keeping the last bound; the one allowed
duplication — the same definition embedded byte-equal in two documents (state and request both carry
environmentProposal) — emits exactly one constant from either copy. The host-profile registry's
``x-supported`` declaration generates only as a subset of its own enumeration.
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

from contract_strategies import Contract, X_INGRESS, module_documents, state_schema  # noqa: E402
import gen_contract_bounds as generator  # noqa: E402


def _seeded(mutation) -> dict[str, dict]:
    documents = {"state": copy.deepcopy(state_schema())}
    documents["state"][X_INGRESS] = ["/properties/environment"]
    mutation(documents["state"])
    return documents


class GeneratorControlTests(unittest.TestCase):
    def test_the_real_contracts_generate_the_committed_module(self):
        fresh = generator.render(generator.collect(Contract()))
        self.assertEqual(fresh, (ROOT / generator.TARGET).read_text(encoding="utf-8"),
                         "the committed contract_bounds.py is not what the contracts generate — "
                         "rerun `make contract-bounds`")

    def test_two_branches_with_different_bounds_under_one_path_are_refused(self):
        """Two oneOf branches sharing the node's path with maxima 4 and 9 are a named conflict."""
        def seed(schema):
            def as_branch(maximum):
                return {"type": "integer", "maximum": maximum}
            schema["$defs"]["environmentMember"] = copy.deepcopy(schema["$defs"]["environmentMember"])
            schema["$defs"]["environmentMember"]["x-core-validator"] = "core.run.validate_environment_member"

            schema["properties"]["environment"] = {
                "x-core-validator": "core.run.validate_environment_member",
                "oneOf": [as_branch(4), as_branch(self_max)],
            }
        self_max = 9
        with self.assertRaises(ValueError) as caught:
            generator.collect(Contract(_seeded(seed)))
        self.assertIn("conflicting generated name", str(caught.exception))

    def test_two_normalized_names_colliding_are_refused(self):
        """``someName`` and ``some-Name`` normalize to one constant name: two nodes, one name — refused."""
        def seed(schema):
            schema["$defs"]["environmentMember"]["properties"] = {
                "someName": {"type": "integer", "minimum": 1},
                "some-Name": {"type": "integer", "minimum": 1},
            }
        with self.assertRaises(ValueError) as caught:
            generator.collect(Contract(_seeded(seed)))
        self.assertIn("normalize alike", str(caught.exception))

    def test_one_node_claimed_along_two_paths_is_refused(self):
        def seed(schema):
            shared = {"type": "string", "maxLength": 40}
            member = schema["$defs"]["environmentMember"]
            member["properties"]["revision"] = shared
            member["properties"]["receipt_id"] = shared  # the same dict object under two owners' paths
        with self.assertRaises(ValueError) as caught:
            generator.collect(Contract(_seeded(seed)))
        self.assertIn("two owners", str(caught.exception))

    def test_an_identical_embedding_generates_one_constant(self):
        """state and request carry byte-equal copies of environmentProposal: the same name with the same
        value is one constant, silently — the duplication the sync guarantees is not a conflict."""
        documents = module_documents()
        self.assertIn("request#/$defs/environmentProposal", Contract(documents).registered)
        collected = generator.collect(Contract())
        self.assertEqual(collected["ENVIRONMENT_PROPOSAL_SPIKE_IMAGES_MAX_LENGTH"], 255)

    def test_the_grammar_pieces_generate_as_constants(self):
        collected = generator.collect(Contract())
        for piece in ("BARE_DIGEST", "REPO_DIGEST", "TAG", "NAME_COMPONENT", "REGISTRY_DOMAIN"):
            self.assertIn(f"ENVIRONMENT_PROPOSAL_SPIKE_IMAGES_GRAMMAR_{piece}", collected)
        tag = collected["ENVIRONMENT_PROPOSAL_SPIKE_IMAGES_GRAMMAR_TAG"]
        import re
        self.assertIsNotNone(re.compile(tag).fullmatch("3.12-slim"), "the tag piece still tags")

    def _registry(self, mutation):
        registry = copy.deepcopy(generator._load_host_profiles_schema())
        mutation(registry["properties"]["profiles"]["items"]["properties"])
        return registry

    def test_every_target_is_what_the_contracts_generate(self):
        for target, fresh in generator.generate(Contract()).items():
            with self.subTest(target=target.name):
                self.assertEqual(fresh, target.read_text(encoding="utf-8"),
                                 "rerun `make contract-bounds`")
        self.assertEqual(set(generator.generate(Contract())), set(generator.TARGETS))

    def test_the_supported_declaration_generates_from_the_registry_schema(self):
        members = generator._load_host_profiles_schema()["properties"]["profiles"]["items"]["properties"]
        collected = generator.collect_host_profiles()
        self.assertEqual(collected["HOST_PROFILE_PROMOTION_STATUS_SUPPORTED"],
                         tuple(members["promotion_status"][generator.X_SUPPORTED]))
        for value in members["approval_ingress"]["enum"]:
            self.assertIn(value, collected.values())

    def test_a_supported_declaration_outside_its_enumeration_is_refused(self):
        def outside(members):
            members["promotion_status"][generator.X_SUPPORTED] = ["not-a-status"]

        def repeated(members):
            members["promotion_status"][generator.X_SUPPORTED] *= 2

        def no_enumeration(members):
            members["host_id"][generator.X_SUPPORTED] = []
        claim = "host-profiles.schema.json#"
        for mutation, message in (
                (outside, f"{claim}promotion_status: {generator.X_SUPPORTED} must list distinct values of the enumeration"),
                (repeated, f"{claim}promotion_status: {generator.X_SUPPORTED} must list distinct values of the enumeration"),
                (no_enumeration, f"{claim}host_id: {generator.X_SUPPORTED} on a member with no enumeration")):
            with self.subTest(mutation=mutation.__name__), self.assertRaises(ValueError) as caught:
                generator.collect_host_profiles(self._registry(mutation))
            self.assertEqual(str(caught.exception), message)

    def test_a_supported_declaration_may_name_the_whole_enumeration(self):
        def everything(members):
            members["promotion_status"][generator.X_SUPPORTED] = list(members["promotion_status"]["enum"])
        registry = self._registry(everything)
        collected = generator.collect_host_profiles(registry)
        self.assertEqual(collected["HOST_PROFILE_PROMOTION_STATUS_SUPPORTED"], tuple(
            registry["properties"]["profiles"]["items"]["properties"]["promotion_status"]["enum"]))

    def test_two_registry_values_normalizing_alike_are_refused(self):
        def alike(members):
            members["approval_ingress"]["enum"] += ["pi-ui"]
        with self.assertRaises(ValueError) as caught:
            generator.collect_host_profiles(self._registry(alike))
        self.assertEqual(str(caught.exception), "host-profiles.schema.json#approval_ingress: two "
                         "registry values generate one name HOST_PROFILE_APPROVAL_INGRESS_PI_UI")


if __name__ == "__main__":
    unittest.main()
