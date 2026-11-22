#!/usr/bin/env python3
"""Negative controls for the Docker spike support-contract, limits-clause and stale-text checks."""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "validate_docker_spike_contract", ROOT / "scripts" / "validate_docker_spike_contract.py")
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)


class LimitsClauseTests(unittest.TestCase):
    def test_current_tree_is_valid(self):
        validator.check_support_contract()
        validator.check_limits()
        validator.check_stale_text()

    def test_clause_renders_every_enforced_limit_and_reason(self):
        text = validator.render_limits()
        for fragment in ("uid/gid 65534/65534", "/work 512 MiB exec tmpfs", "/tmp 64 MiB noexec",
                         "CPU min(2, NCPU)", "2 GiB memory", "256 PIDs", "nofile 1024",
                         "120 s workload", "1 MiB stdout and 1 MiB stderr",
                         "capture 256 files, 64 MiB total, 64 MiB per file", "core 0;",
                         "up to 128 environment overrides", "except 137 is FAIL",
                         "image pull, build, load, or login",
                         "one closed reason whenever the cause is attributable"):
            self.assertIn(fragment, text)
        for reason in validator.generic_spike.SPIKE_FAULT_REASONS:
            self.assertIn(f"reason {reason} | ", text)
            self.assertRegex(reason, r"^[a-z]+(-[a-z]+)+$")

    def test_a_non_hyphenated_reason_fails(self):
        reasons = validator.generic_spike.SPIKE_FAULT_REASONS + ("cleanup_unavailable",)
        hints = {**validator.REASON_HINTS, "cleanup_unavailable": "x"}
        with mock.patch.object(validator.generic_spike, "SPIKE_FAULT_REASONS", reasons), \
                mock.patch.object(validator, "REASON_HINTS", hints):
            with self.assertRaisesRegex(validator.ContractError, "one hyphenated spelling"):
                validator.render_limits()

    def test_a_changed_constant_is_drift(self):
        policy = validator.spike_plan.FRAMEWORK_SPIKE_POLICY
        changed = validator.spike_plan.SpikePlanPolicy.__new__(type(policy))
        for field in type(policy).__dataclass_fields__:
            object.__setattr__(changed, field, getattr(policy, field))
        object.__setattr__(changed, "pids", policy.pids + 1)
        with mock.patch.object(validator.spike_plan, "FRAMEWORK_SPIKE_POLICY", changed):
            with self.assertRaisesRegex(validator.ContractError, "drifted"):
                validator.check_limits()

    def test_a_reason_without_a_hint_fails(self):
        hints = dict(validator.REASON_HINTS)
        hints.pop("image-not-local")
        with mock.patch.object(validator, "REASON_HINTS", hints):
            with self.assertRaisesRegex(validator.ContractError, "cover the closed"):
                validator.render_limits()


class ProbeImageTests(unittest.TestCase):
    """The qualification probe image must be exactly one digest-pinned OCI index reference."""

    REFERENCE = "docker.io/library/alpine:3.22@sha256:" + "4" * 64

    def contract_with_mutation(self, mutate) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name in ("support-contract.json", "support-contract.schema.json"):
            shutil.copy2(validator.CONTRACT_DIR / name, root / name)
        contract = json.loads((root / "support-contract.json").read_text(encoding="utf-8"))
        mutate(contract)
        (root / "support-contract.json").write_text(
            json.dumps(contract, indent=2) + "\n", encoding="utf-8")
        with mock.patch.object(validator, "CONTRACT_DIR", root):
            validator.check_support_contract()

    def apply(self, probe_image):
        return lambda contract: contract["qualification"].__setitem__("probe_image", probe_image)

    def test_a_missing_pin_fails(self):
        with self.assertRaisesRegex(validator.ContractError, "probe_image"):
            self.contract_with_mutation(
                lambda contract: contract["qualification"].pop("probe_image"))

    def test_a_pin_without_a_digest_fails(self):
        with self.assertRaisesRegex(validator.ContractError, "probe_image"):
            self.contract_with_mutation(self.apply({"reference": "docker.io/library/alpine:3.22",
                                                    "repository": "alpine",
                                                    "index_digest": "sha256:" + "4" * 64}))

    def test_a_pin_without_a_tag_fails(self):
        with self.assertRaisesRegex(validator.ContractError, "probe_image"):
            self.contract_with_mutation(self.apply(
                {"reference": "docker.io/library/alpine@sha256:" + "4" * 64,
                 "repository": "alpine", "index_digest": "sha256:" + "4" * 64}))

    def test_a_digest_mismatch_between_reference_and_index_digest_fails(self):
        with self.assertRaisesRegex(validator.ContractError, "probe_image"):
            self.contract_with_mutation(self.apply(
                {"reference": "docker.io/library/alpine:3.22@sha256:" + "4" * 64,
                 "repository": "alpine", "index_digest": "sha256:" + "5" * 64}))

    def test_a_repository_that_is_not_the_short_name_fails(self):
        with self.assertRaisesRegex(validator.ContractError, "probe_image"):
            self.contract_with_mutation(self.apply(
                {"reference": self.REFERENCE, "repository": "library/alpine",
                 "index_digest": "sha256:" + "4" * 64}))

    def test_a_repository_that_is_not_the_reference_tail_fails(self):
        with self.assertRaisesRegex(validator.ContractError, "probe_image"):
            self.contract_with_mutation(self.apply(
                {"reference": "docker.io/library/other:3.22@sha256:" + "4" * 64,
                 "repository": "alpine", "index_digest": "sha256:" + "4" * 64}))

    def test_a_pin_with_extra_fields_fails(self):
        with self.assertRaisesRegex(validator.ContractError, "probe_image"):
            self.contract_with_mutation(self.apply(
                {"reference": self.REFERENCE, "repository": "alpine",
                 "index_digest": "sha256:" + "4" * 64, "id": "sha256:" + "9" * 64}))

    def test_the_current_pinned_pin_parses(self):
        contract = json.loads(
            (validator.CONTRACT_DIR / "support-contract.json").read_text(encoding="utf-8"))
        reference, repository, index_digest = validator._probe_image(
            contract["qualification"]["probe_image"])
        self.assertRegex(f"{reference} {repository} {index_digest}",
                         r"^[^@]+@sha256:[0-9a-f]{64} alpine sha256:[0-9a-f]{64}$")


class StaleTextTests(unittest.TestCase):
    def copy_tree(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for item in validator.STALE_SCAN + ("Makefile",):
            source = ROOT / item
            target = root / item
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("node_modules"))
            else:
                shutil.copy2(source, target)
        return root

    def test_stale_phrase_and_absent_make_target_fail(self):
        root = self.copy_tree()
        validator.check_stale_text(root)
        doc = root / "doc" / "new.md"
        doc.write_text("Run make generic-sandbox-qualification PROBE_IMAGE=x.\n")
        with self.assertRaisesRegex(validator.ContractError, "stale Docker text"):
            validator.check_stale_text(root)
        doc.write_text("zero derives uppercase PASS and nonzero derives uppercase FAIL.\n")
        with self.assertRaisesRegex(validator.ContractError, "stale Docker text"):
            validator.check_stale_text(root)
        doc.unlink()
        exempt = root / "doc" / "design" / "specs" / "empirica-2.0-d5-evidence-workspace-spike.md"
        self.assertIn("600 seconds", exempt.read_text())
        makefile = root / "Makefile"
        makefile.write_text(makefile.read_text().replace(
            "generic-docker-install-candidate:", "generic-docker-install-renamed:"))
        with self.assertRaisesRegex(validator.ContractError, "absent Make target"):
            validator.check_stale_text(root)


if __name__ == "__main__":
    unittest.main()
