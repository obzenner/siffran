#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import jsonschema

from core import evaluation, spike_plan
from core.spike_plan_draft import (
    DRAFT_SCHEMA, SpikePlanDraftError, canonicalize_spike_plan_draft, seal_spike_plan,
)

D0 = "sha256:" + "0" * 64
D1 = "sha256:" + "1" * 64


def copy_dict(value):
    return copy.deepcopy(value)


def raw_draft():
    return {
        "schema": DRAFT_SCHEMA,
        "image": "python:3.12",
        "argv": ["python", "/inputs/src/example.py", ""],
        "inputs": ["tests/", "src/example.py"],
        "environment": {"PYTHONDONTWRITEBYTECODE": "1", "CACHE": "/work/cache"},
    }


class SpikePlanDraftTests(unittest.TestCase):
    def test_minimal_draft_is_exact_closed_and_canonical(self):
        draft = canonicalize_spike_plan_draft(raw_draft())
        self.assertEqual(draft.to_data(), {
            "schema": "empirica.spike-plan-draft/2",
            "image": "python:3.12",
            "argv": ["python", "/inputs/src/example.py", ""],
            "inputs": ["src/example.py", "tests/"],
            "environment": {"CACHE": "/work/cache", "PYTHONDONTWRITEBYTECODE": "1"},
        })
        self.assertRegex(draft.digest, r"^sha256:[0-9a-f]{64}$")

    def test_removed_v1_and_multi_container_source_are_absent_from_production_core(self):
        root = Path(__file__).resolve().parents[1] / "core"
        source = "\n".join((root / name).read_text(encoding="utf-8")
                           for name in ("spike_plan.py", "spike_plan_draft.py"))
        for removed in (
            "empirica.spike-plan-draft/1", "ContainerPlan", "ContainerKind",
            "PrivateNetwork", "NetworkAttachment", "startup_order",
            '"containers"', '"networks"', '"services"', '"aliases"',
        ):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, source)

    def test_old_and_multi_container_shapes_are_rejected_not_translated(self):
        old = {
            "schema": "empirica.spike-plan-draft/1", "containers": [], "inputs": [],
            "networks": [], "startup_order": [],
        }
        with self.assertRaises(SpikePlanDraftError):
            canonicalize_spike_plan_draft(old)
        for field, value in {
            "containers": [], "networks": [], "startup_order": [], "services": [],
            "aliases": [], "resources": {}, "timeout": 1, "profile": "docker-isolated-v1",
            "user": "root", "workdir": "/", "authority": {},
        }.items():
            malformed = raw_draft()
            malformed[field] = value
            with self.subTest(field=field), self.assertRaises(SpikePlanDraftError):
                canonicalize_spike_plan_draft(malformed)

    def test_canonical_request_schema_rejects_v1_multi_fields_and_reserved_environment(self):
        root = Path(__file__).resolve().parents[3]
        document = json.loads((root / "contracts/empirica/v2/request.schema.json")
                              .read_text(encoding="utf-8"))
        schema = document["$defs"]["spikePlanDraft"]
        jsonschema.validators.validator_for(schema).check_schema(schema)
        validator = jsonschema.validators.validator_for(schema)(schema)
        valid = raw_draft()
        self.assertFalse(list(validator.iter_errors(valid)))
        malformed = []
        old = copy_dict(valid)
        old["schema"] = "empirica.spike-plan-draft/1"
        malformed.append(old)
        extra = copy_dict(valid)
        extra["containers"] = []
        malformed.append(extra)
        reserved = copy_dict(valid)
        reserved["environment"]["HOME"] = "/tmp"
        malformed.append(reserved)
        missing = copy_dict(valid)
        del missing["argv"]
        malformed.append(missing)
        for row in malformed:
            self.assertTrue(list(validator.iter_errors(row)))

    def test_exact_root_requires_every_field_and_rejects_unknowns(self):
        for field in tuple(raw_draft()):
            malformed = raw_draft()
            malformed.pop(field)
            with self.subTest(missing=field), self.assertRaises(SpikePlanDraftError):
                canonicalize_spike_plan_draft(malformed)
        malformed = raw_draft()
        malformed["unknown"] = None
        with self.assertRaises(SpikePlanDraftError):
            canonicalize_spike_plan_draft(malformed)

    def test_argv_is_nonempty_exact_and_bounded(self):
        for argv in ([], [""], "python", ["python", None], ["x" * 4097]):
            malformed = raw_draft()
            malformed["argv"] = argv
            with self.subTest(argv=repr(argv)), self.assertRaises(SpikePlanDraftError):
                canonicalize_spike_plan_draft(malformed)
        draft = raw_draft()
        draft["argv"] = ["python", "", "two words", "$(not-a-shell)"]
        self.assertEqual(canonicalize_spike_plan_draft(draft).argv, tuple(draft["argv"]))

    def test_inputs_allow_files_and_directories_but_require_canonical_closure(self):
        draft = raw_draft()
        draft["inputs"] = []
        self.assertEqual(canonicalize_spike_plan_draft(draft).inputs, ())
        malformed_values = (
            ["/etc/passwd"], ["../secret"], ["a\\b"], ["a//b"], [".git/config"],
            ["src/", "src/a.py"], ["src", "src/a.py"], ["src", "src/"],
            ["same", "same"],
        )
        for inputs in malformed_values:
            malformed = raw_draft()
            malformed["inputs"] = inputs
            with self.subTest(inputs=inputs), self.assertRaises(SpikePlanDraftError):
                canonicalize_spike_plan_draft(malformed)

    def test_environment_is_a_bounded_map_and_reserved_names_are_refused(self):
        for environment in (
            [], {"HOME": "/tmp"}, {"TMPDIR": "/work"}, {"BAD-NAME": "x"},
            {"OK": None}, {f"V{i}": "x" for i in range(129)},
        ):
            malformed = raw_draft()
            malformed["environment"] = environment
            with self.subTest(environment=repr(environment)[:80]), self.assertRaises(SpikePlanDraftError):
                canonicalize_spike_plan_draft(malformed)

    def test_host_seal_binds_resolved_image_expanded_files_policy_and_profile(self):
        draft = canonicalize_spike_plan_draft(raw_draft())
        image = spike_plan.ResolvedImage(
            draft.image, D1, "linux", "linux", "aarch64", "arm64", ())
        files = (
            spike_plan.CapturedFile("src/example.py", D0, 10),
            spike_plan.CapturedFile("tests/a.py", D1, 20),
        )
        plan = seal_spike_plan(
            draft, image, files, engine_ncpu=14,
            network_profile="docker-isolated-v1")
        self.assertEqual(plan.cpu_count, 2)
        self.assertEqual(plan.image.image_id, D1)
        self.assertEqual(plan.captured_files, files)
        self.assertEqual(plan.to_data()["uid"], 65534)
        self.assertEqual(plan.to_data()["gid"], 65534)
        self.assertEqual(plan.to_data()["workdir"], "/work")
        self.assertEqual(plan.to_data()["inputs_mount"], "/inputs")
        self.assertEqual(plan.to_data()["tmpdir"], "/tmp")
        self.assertNotIn("containers", plan.to_data())
        self.assertNotIn("networks", plan.to_data())
        request = spike_plan.SealedSpikeRequest(plan, spike_plan.FRAMEWORK_SPIKE_POLICY)
        self.assertEqual(request.to_data()["schema"], "empirica.spike-request/2")
        self.assertEqual(request.to_data()["operation_timeout_seconds"], 210)

    def test_pure_evaluation_request_validation_rejects_old_and_unknown_shapes(self):
        draft = canonicalize_spike_plan_draft(raw_draft())
        image = spike_plan.ResolvedImage(
            draft.image, D1, "linux", "linux", "aarch64", "arm64", ())
        plan = seal_spike_plan(
            draft, image, (spike_plan.CapturedFile("src/example.py", D0, 1),),
            engine_ncpu=1, network_profile="docker-isolated-v1")
        action = {"kind": "spike_request", "claim_id": "G0", "plan": draft.to_data()}
        sealed = {
            "draft_digest": draft.digest,
            "plan": plan.to_data(),
            "plan_digest": plan.digest,
            "policy": spike_plan.FRAMEWORK_SPIKE_POLICY.to_data(),
            "policy_digest": spike_plan.FRAMEWORK_SPIKE_POLICY.digest,
            "operation_timeout_seconds": 210,
            "snapshot_basis_id": D0,
            "snapshot_digest": D1,
            "backend": {
                "kind": "qualified", "backend": "docker",
                "profile_id": "docker-isolated-v1",
                "qualification_record_digest": D0,
                "execution_binding_digest": D1,
            },
        }
        validated_draft, validated_plan = evaluation.validate_sealed_spike_request(action, sealed)
        self.assertEqual(validated_draft, draft)
        self.assertEqual(validated_plan, plan)
        mutations = []
        old = copy_dict(action)
        old["plan"]["schema"] = "empirica.spike-plan-draft/1"
        mutations.append((old, sealed))
        extra = copy_dict(action)
        extra["plan"]["containers"] = []
        mutations.append((extra, sealed))
        alternate = copy_dict(sealed)
        alternate["operation_timeout_seconds"] = 209
        mutations.append((action, alternate))
        unknown = copy_dict(sealed)
        unknown["unknown"] = True
        mutations.append((action, unknown))
        drift = copy_dict(sealed)
        drift["backend"]["profile_id"] = "docker-networked-v1"
        mutations.append((action, drift))
        for candidate_action, candidate_sealed in mutations:
            with self.assertRaises(ValueError):
                evaluation.validate_sealed_spike_request(candidate_action, candidate_sealed)

    def test_sealing_rejects_reference_capture_cpu_and_profile_drift(self):
        draft = canonicalize_spike_plan_draft(raw_draft())
        image = spike_plan.ResolvedImage(
            draft.image, D1, "linux", "linux", "x86_64", "amd64", ())
        valid = (spike_plan.CapturedFile("src/example.py", D0, 1),)
        cases = (
            (spike_plan.ResolvedImage(
                "other:tag", D1, "linux", "linux", "amd64", "amd64", ()),
             valid, 2, "docker-isolated-v1"),
            (image, (spike_plan.CapturedFile("outside.py", D0, 1),), 2, "docker-isolated-v1"),
            (image, valid, 0, "docker-isolated-v1"),
            (image, valid, 2, "default"),
        )
        for resolved, files, ncpu, profile in cases:
            with self.subTest(profile=profile, ncpu=ncpu), self.assertRaises(SpikePlanDraftError):
                seal_spike_plan(
                    draft, resolved, files, engine_ncpu=ncpu,
                    network_profile=profile)


if __name__ == "__main__":
    unittest.main()
