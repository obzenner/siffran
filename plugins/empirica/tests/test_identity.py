"""Table regressions for adapter-side opaque model identity classes."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from adapters.identity import POLICY_VERSION, observe
from core.evaluation import identity_pair


class IdentityPolicyTests(unittest.TestCase):
    def test_policy_version_matches_every_contract_schema_const(self):
        root = Path(__file__).resolve().parents[3] / "contracts/empirica/v2"
        found = []
        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "policy_version" and isinstance(child, dict) and "const" in child:
                        found.append(child["const"])
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        for name in ("request.schema.json", "response.schema.json", "state.schema.json"):
            walk(json.loads((root / name).read_text()))
        self.assertTrue(found)
        self.assertEqual(set(found), {POLICY_VERSION})

    def test_equivalent_deployment_spellings_share_classes(self):
        rows = [
            (("anthropic", "claude-opus-4-8"), "anthropic/claude-opus-4-8"),
            (("bedrock", "eu.anthropic.claude-opus-4-8"), "anthropic/claude-opus-4-8"),
            (("amazon-bedrock-eu", "eu.anthropic.claude-opus-4-8"), "anthropic/claude-opus-4-8"),
            (("anthropic", "claude-haiku-4-5-20251001"), "anthropic/claude-haiku-4-5-20251001"),
            (("amazon-bedrock-eu", "eu.anthropic.claude-haiku-4-5-20251001-v1:0"),
             "anthropic/claude-haiku-4-5-20251001"),
            (("amazon-bedrock-us", "us.openai.gpt-6-astra:high"), "openai/gpt-6-astra"),
            (("amazon-bedrock-us", "us.openai.gpt-5.6-sol:off"), "openai/gpt-5.6-sol"),
            (("amazon-bedrock-us", "us.openai.gpt-5.6-sol:minimal"), "openai/gpt-5.6-sol"),
            (("amazon-bedrock-us", "us.openai.gpt-5.6-sol:max"), "openai/gpt-5.6-sol"),
            (("openai", "gpt-6-astra"), "openai/gpt-6-astra"),
            (("anthropic", "Claude-Opus-4-8 [1m]"), "anthropic/claude-opus-4-8"),
        ]
        for (provider, model), expected in rows:
            with self.subTest(provider=provider, model=model):
                value = observe(provider, model, source="test")
                self.assertIsNotNone(value)
                self.assertEqual(value["identity"], expected)
                self.assertEqual(value["provider_id"], provider)
                self.assertEqual(value["model_id"], model)
                self.assertEqual(value["policy_version"], "model-identity/1")

    def test_core_accepts_only_nonempty_host_observed_opaque_classes(self):
        self.assertEqual(identity_pair({"identity": "vendor/model", "observed_by": "host"}),
                         "vendor/model")
        self.assertIsNone(identity_pair({"identity": None, "observed_by": "host"}))
        self.assertIsNone(identity_pair({"identity": "vendor/model", "observed_by": "configuration"}))

    def test_aliases_synthetic_arns_and_malformed_values_are_unknown(self):
        models = ["", "<synthetic>", "opus", "sonnet", "haiku", "fable", "best",
                  "default", "inherit", "opusplan", "claude-opus-latest", "gpt-latest",
                  "arn:aws:bedrock:us-east-1:123:application-inference-profile/example",
                  "not a model", None]
        for model in models:
            with self.subTest(model=model):
                self.assertIsNone(observe("anthropic", model, source="test"))
        self.assertIsNone(observe("amazon-bedrock-eu", "claude-opus-4-8", source="test"))
        self.assertIsNone(observe(None, "claude-opus-4-8", source="test"))


if __name__ == "__main__":
    unittest.main()
