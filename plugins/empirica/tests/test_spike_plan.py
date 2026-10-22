#!/usr/bin/env python3
from __future__ import annotations

import unittest
from dataclasses import replace

from core import spike_plan as subject

D0 = "sha256:" + "0" * 64
D1 = "sha256:" + "1" * 64


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


if __name__ == "__main__":
    unittest.main()
