#!/usr/bin/env python3
"""Unit controls for scripts/gen_contract_bounds.py — the generator's ownership is checked, not assumed.

The E2c review seeded two disjoint integer branches with maxima 4 and 9 and the old emitter silently
kept only the 9; a second name collision (two properties whose names normalize alike) overwrote too.
These controls keep the refusal behaviour: a conflict is named, never silently resolved, and the one
allowed duplication — the same definition embedded byte-equal in two documents (state and request both
carry environmentProposal) — emits exactly one constant from either copy.
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
        """The review's seeded conflict: two oneOf branches sharing the node's path with maxima 4 and 9."""
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


if __name__ == "__main__":
    unittest.main()
