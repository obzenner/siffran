#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

import jsonschema

from core import spike_plan as subject

D0 = "sha256:" + "0" * 64
D1 = "sha256:" + "1" * 64
# Read independently of any production reader: the runtime-loaded vendored copy.
VENDORED = Path(__file__).resolve().parents[1] / "vendor" / "contracts" / "empirica" / "v2"


def _vendored(name: str) -> dict:
    return json.loads((VENDORED / f"{name}.schema.json").read_text(encoding="utf-8"))


def _qualified(**changes):
    row = {"kind": "qualified", "backend": "docker", "profile_id": "docker-isolated-v1",
           "qualification_record_digest": D0, "execution_binding_digest": D1}
    row.update(changes)
    return row


def _without(key):
    row = _qualified()
    del row[key]
    return row


# JSON-valued malformed bindings; each must be refused by the code owner and by the schema.
MALFORMED_BACKENDS = {
    "empty": {},
    "unavailable extra key": {"kind": "spikes_unavailable", "reason": "absent"},
    "qualified extra key": _qualified(extra=True),
    **{f"missing {key}": _without(key) for key in _qualified()},
    "wrong backend": _qualified(backend="host"),
    "wrong kind": _qualified(kind="unqualified"),
    "unknown profile": _qualified(profile_id="docker-rust-v1"),
    "list profile": _qualified(profile_id=["docker-isolated-v1"]),
    **{f"{label} {key}": _qualified(**{key: value})
       for key in ("qualification_record_digest", "execution_binding_digest")
       for label, value in (("uppercase", "sha256:" + "A" * 64), ("short", "sha256:" + "0" * 63),
                            ("unprefixed", "0" * 64), ("numeric", 7))},
    "array": [],
    "null": None,
}


class SpikePlanTests(unittest.TestCase):
    def test_framework_policy_is_exact_mvp_default_and_content_addressed(self):
        policy = subject.FRAMEWORK_SPIKE_POLICY
        self.assertEqual(policy.to_data(), {
            "schema": "empirica.spike-plan-policy/2",
            "max_cpu_count": 2,
            "memory_bytes": 2 * 1024 * 1024 * 1024,
            "memory_swap_bytes": 2 * 1024 * 1024 * 1024,
            "pids": 256,
            "open_files": 1024,
            "max_file_size_bytes": 64 * 1024 * 1024,
            "core_bytes": 0,
            "workload_timeout_seconds": 120,
            "stdout_bytes": 1024 * 1024,
            "stderr_bytes": 1024 * 1024,
            "staging_files": 256,
            "staging_bytes": 64 * 1024 * 1024,
            "staging_file_bytes": 64 * 1024 * 1024,
            "work_tmpfs_bytes": 512 * 1024 * 1024,
            "tmp_tmpfs_bytes": 64 * 1024 * 1024,
            "shm_bytes": 64 * 1024 * 1024,
        })
        self.assertRegex(policy.digest, r"^sha256:[0-9a-f]{64}$")
        for field in ("memory_bytes", "pids", "stdout_bytes", "work_tmpfs_bytes"):
            with self.subTest(field=field), self.assertRaises(subject.SpikePlanError):
                replace(policy, **{field: getattr(policy, field) - 1})

    def test_local_image_reference_accepts_familiar_local_forms(self):
        valid = (
            "python", "python:3.12", "library/python:3.12-slim",
            "localhost:5000/team/image:Tag_1", "registry.example/team/image",
            "example/probe@" + D1, D1, "1" * 64, "abcdef123456",
            "sha256:" + "a" * 12,
        )
        for value in valid:
            with self.subTest(value=value):
                self.assertEqual(subject.validate_local_image_reference(value), value)

    def test_local_image_reference_rejects_nonlocal_or_malformed_syntax(self):
        invalid = (
            "", " Python:3", "UPPER/name:tag", "python:", "python@sha256:12",
            "repo//image", "repo/image@" + D1 + "x",
            "repo image:tag", "registry.example:abc/image",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(subject.SpikePlanError):
                subject.validate_local_image_reference(value)

    def test_architecture_aliases_are_canonical_without_erasing_raw_value(self):
        self.assertEqual(subject.canonical_architecture("aarch64"), "arm64")
        self.assertEqual(subject.canonical_architecture("x86_64"), "amd64")
        self.assertEqual(subject.canonical_architecture("riscv64"), "riscv64")
        image = subject.ResolvedImage(
            "python:3.12", D1, "linux", "linux", "aarch64", "arm64", ())
        self.assertEqual(image.to_data()["raw_architecture"], "aarch64")
        self.assertEqual(image.to_data()["architecture"], "arm64")
        with self.assertRaises(subject.SpikePlanError):
            replace(image, architecture="aarch64")

    def test_resolved_image_and_captured_file_are_closed_host_facts(self):
        with self.assertRaises(subject.SpikePlanError):
            subject.ResolvedImage(
                "python:3.12", "1" * 64, "linux", "linux", "arm64", "arm64", ())
        image = subject.ResolvedImage(
            "python:3.12", D1, "linux", "linux", "arm64", "arm64", ())
        with self.assertRaises(subject.SpikePlanError):
            replace(image, declared_volumes=("/data",))
        captured = subject.CapturedFile("src/a.py", D0, 12)
        self.assertEqual(captured.to_data(), {"path": "src/a.py", "sha256": D0, "bytes": 12})
        for bad in (-1, 64 * 1024 * 1024 + 1, True):
            with self.subTest(bytes=bad), self.assertRaises(subject.SpikePlanError):
                subject.CapturedFile("src/a.py", D0, bad)

    def test_natural_137_is_reserved_unavailable(self):
        self.assertIs(subject.natural_verdict(0), subject.NaturalVerdict.PASS)
        for code in (1, 124, 125, 255):
            self.assertIs(subject.natural_verdict(code), subject.NaturalVerdict.FAIL)
        self.assertIsNone(subject.natural_verdict(137))
        for malformed in (-1, 256, True, "0"):
            with self.assertRaises(subject.SpikePlanError):
                subject.natural_verdict(malformed)


class SpikeProfileAndBackendOwnerTests(unittest.TestCase):
    def test_network_profiles_are_exactly_every_vendored_schema_profile_enum(self):
        request, response, state = _vendored("request"), _vendored("response"), _vendored("state")
        artifact = response["$defs"]["spikeArtifact"]["properties"]
        locations = {
            "request startRun.spike_profile":
                request["$defs"]["startRun"]["properties"]["spike_profile"],
            "response spikeBackend":
                response["$defs"]["spikeBackend"]["oneOf"][0]["properties"]["profile_id"],
            "response spikeRequestArtifact.backend":
                response["$defs"]["spikeRequestArtifact"]["properties"]["backend"]["properties"]["profile_id"],
            "response spikeArtifact.backend": artifact["backend"]["properties"]["profile_id"],
            "response spikeArtifact.result_facts.backend":
                artifact["result_facts"]["properties"]["backend"]["properties"]["profile_id"],
            "state spike_backend":
                state["properties"]["spike_backend"]["oneOf"][0]["properties"]["profile_id"],
        }
        self.assertEqual(subject.NETWORK_PROFILES,
                         {subject.ISOLATED_PROFILE, subject.NETWORKED_PROFILE})
        for where, schema in locations.items():
            with self.subTest(where=where):
                self.assertEqual(subject.NETWORK_PROFILES, frozenset(schema["enum"]))
                self.assertEqual(len(schema["enum"]), len(subject.NETWORK_PROFILES))

    def test_spike_backend_kind_accepts_exactly_the_two_closed_bindings(self):
        for profile in sorted(subject.NETWORK_PROFILES):
            with self.subTest(profile=profile):
                self.assertEqual(subject.spike_backend_kind(_qualified(profile_id=profile)), "qualified")
        self.assertEqual(subject.spike_backend_kind({"kind": "spikes_unavailable"}),
                         "spikes_unavailable")

    def test_spike_backend_kind_refuses_every_malformed_binding(self):
        class Text(str):
            pass

        refused = {
            **MALFORMED_BACKENDS,
            "trailing newline digest": _qualified(qualification_record_digest=D0 + "\n"),
            "str subclass digest": _qualified(execution_binding_digest=Text(D1)),
            "str subclass profile": _qualified(profile_id=Text("docker-isolated-v1")),
            "text": "qualified",
        }
        for label, binding in refused.items():
            with self.subTest(binding=label):
                self.assertIsNone(subject.spike_backend_kind(binding))

    def test_spike_backend_kind_agrees_with_both_vendored_backend_schemas(self):
        response, state = _vendored("response"), _vendored("state")
        schemas = {
            "state": {"$defs": state["$defs"], **state["properties"]["spike_backend"]},
            "response": {"$defs": response["$defs"], **response["$defs"]["spikeBackend"]},
        }
        cases = {"unavailable": {"kind": "spikes_unavailable"},
                 **{profile: _qualified(profile_id=profile) for profile in subject.NETWORK_PROFILES},
                 **MALFORMED_BACKENDS}
        for where, schema in schemas.items():
            qualified_branch = schema["oneOf"][0]
            self.assertEqual(set(qualified_branch["required"]), set(_qualified()))
            self.assertEqual(set(qualified_branch["properties"]), set(_qualified()))
            validator = jsonschema.Draft202012Validator(schema)
            for label, binding in cases.items():
                with self.subTest(schema=where, binding=label):
                    self.assertEqual(subject.spike_backend_kind(copy.deepcopy(binding)) is not None,
                                     validator.is_valid(binding))


if __name__ == "__main__":
    unittest.main()
