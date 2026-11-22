#!/usr/bin/env python3
"""Fake-runner-only tests for the probe-image pin developer tool (no Docker daemon, no network)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "empirica"))
from adapters.sandbox import probe_image  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "pin_probe_image", ROOT / "scripts" / "pin_probe_image.py")
pin_tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pin_tool
SPEC.loader.exec_module(pin_tool)

CONTRACT = ROOT / "contracts" / "empirica-docker-spike" / "v1" / "support-contract.json"


def index_manifest(digest: str, architectures=("amd64", "arm64")) -> str:
    return json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "digest": digest,
        "manifests": [
            {"mediaType": "application/vnd.oci.image.manifest.v1+json",
             "digest": f"sha256:{architecture[0] * 63}{index}", "size": 2114,
             "platform": {"architecture": architecture, "os": "linux"}}
            for index, architecture in enumerate(architectures)
        ],
    })


class FakeRunner:
    """Stands in for subprocess.run against `docker buildx`; records the exact argv."""

    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.arguments: list[str] | None = None
        self.result = types.SimpleNamespace(
            stdout=stdout, stderr=stderr, returncode=returncode)

    def __call__(self, argv, **kwargs):
        self.arguments = list(argv)
        return self.result


class PinProbeImageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.contract = Path(temporary.name) / "support-contract.json"
        self.contract.write_bytes(CONTRACT.read_bytes())
        self.declared = CONTRACT.read_text(encoding="utf-8")
        self.old_pin = self.pin_field()

    def pin_field(self) -> tuple[str, str, str]:
        contract = json.loads(self.contract.read_text(encoding="utf-8"))
        pin = probe_image.parse_probe_image(contract["qualification"]["probe_image"])
        return pin.reference, pin.repository, pin.index_digest

    def test_pins_the_index_digest_of_the_canonical_library_reference(self):
        digest = "sha256:" + "a" * 64
        runner = FakeRunner(stdout=index_manifest(digest))
        pin_tool.pin_probe_image("alpine:3.22", contract_path=self.contract, runner=runner)
        self.assertEqual(runner.arguments, [
            "docker", "buildx", "imagetools", "inspect",
            "docker.io/library/alpine:3.22", "--format", "{{json .Manifest}}"])
        reference, repository, index_digest = self.pin_field()
        self.assertEqual((reference, repository, index_digest),
                         (f"docker.io/library/alpine:3.22@{digest}", "alpine", digest))

    def test_a_fully_spelled_out_official_reference_admits_unchanged(self):
        digest = "sha256:" + "b" * 64
        runner = FakeRunner(stdout=index_manifest(digest))
        pin_tool.pin_probe_image("docker.io/library/alpine:3.22",
                                 contract_path=self.contract, runner=runner)
        self.assertEqual(runner.arguments, [
            "docker", "buildx", "imagetools", "inspect",
            "docker.io/library/alpine:3.22", "--format", "{{json .Manifest}}"])
        self.assertEqual(self.pin_field()[2], digest)

    def test_other_registries_and_namespaces_are_refused_before_any_buildx_call(self):
        for tag in ("registry.example.com/team/tool:1.0", "docker.io/team/tool:1.0",
                    "docker.io/library/team/tool:1.0", "ghcr.io/alpine:3.22",
                    "localhost:5000/alpine:3.22", "quay.io/library/alpine:3.22"):
            runner = FakeRunner()
            with self.assertRaisesRegex(pin_tool.PinError, "official Docker Hub library"):
                pin_tool.pin_probe_image(tag, contract_path=self.contract, runner=runner)
            self.assertIsNone(runner.arguments, f"{tag} must be refused before buildx")
            self.assertEqual(self.declared, CONTRACT.read_text(encoding="utf-8"))

    def test_no_arm64_index_entry_is_refused_and_the_old_pin_stays(self):
        digest = "sha256:" + "c" * 64
        runner = FakeRunner(stdout=index_manifest(digest, architectures=("amd64",)))
        with self.assertRaisesRegex(pin_tool.PinError, "linux/arm64"):
            pin_tool.pin_probe_image("alpine:3.22", contract_path=self.contract, runner=runner)
        self.assertNotIn(digest, self.contract.read_text(encoding="utf-8"))
        self.assertEqual(self.pin_field(), self.old_pin)

    def test_no_amd64_index_entry_is_refused(self):
        runner = FakeRunner(stdout=index_manifest("sha256:" + "c" * 64, architectures=("arm64",)))
        with self.assertRaisesRegex(pin_tool.PinError, "linux/amd64"):
            pin_tool.pin_probe_image("alpine:3.22", contract_path=self.contract, runner=runner)

    def test_a_single_platform_media_type_is_refused(self):
        manifest = json.dumps({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                       "digest": "sha256:" + "d" * 64, "size": 1},
            "layers": [],
        })
        runner = FakeRunner(stdout=manifest)
        with self.assertRaisesRegex(pin_tool.PinError, "index"):
            pin_tool.pin_probe_image("alpine:3.22", contract_path=self.contract, runner=runner)

    def test_a_failing_runner_is_refused_and_the_contract_is_untouched(self):
        before = self.contract.read_bytes()
        runner = FakeRunner(returncode=1, stderr="no such tag")
        with self.assertRaisesRegex(pin_tool.PinError, "buildx"):
            pin_tool.pin_probe_image("alpine:3.22", contract_path=self.contract, runner=runner)
        self.assertEqual(self.contract.read_bytes(), before)

    def test_a_manifest_without_an_index_digest_is_refused(self):
        manifest = json.dumps({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {"digest": "sha256:" + "d" * 64, "platform": {"architecture": "amd64", "os": "linux"}},
                {"digest": "sha256:" + "e" * 64, "platform": {"architecture": "arm64", "os": "linux"}}],
        })
        runner = FakeRunner(stdout=manifest)
        with self.assertRaisesRegex(pin_tool.PinError, "digest"):
            pin_tool.pin_probe_image("alpine:3.22", contract_path=self.contract, runner=runner)

    def test_a_manifest_with_a_malformed_index_digest_is_refused(self):
        runner = FakeRunner(stdout=index_manifest("not-a-digest"))
        with self.assertRaisesRegex(pin_tool.PinError, "digest"):
            pin_tool.pin_probe_image("alpine:3.22", contract_path=self.contract, runner=runner)

    def test_a_malformed_tag_is_refused_before_any_runner_call(self):
        runner = FakeRunner()
        with self.assertRaisesRegex(pin_tool.PinError, "name:tag"):
            pin_tool.pin_probe_image("alpine@sha256:" + "a" * 64,
                                     contract_path=self.contract, runner=runner)
        self.assertIsNone(runner.arguments)

    def test_the_rewrite_is_localized_to_the_pin_member_and_leaves_the_rest_intact(self):
        digest = "sha256:" + "f" * 64
        runner = FakeRunner(stdout=index_manifest(digest))
        pin_tool.pin_probe_image("alpine:3.23", contract_path=self.contract, runner=runner)
        before = [line for line in self.declared.splitlines()]
        after = [line for line in self.contract.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(before), len(after))
        self.assertNotEqual(before, after)
        changed = [(old, new) for old, new in zip(before, after) if old != new]
        self.assertLessEqual(len(changed), 3)
        for old, new in changed:
            self.assertRegex(new, '"(reference|repository|index_digest)": ')


class MakeEntryTests(unittest.TestCase):
    def test_a_malformed_tag_through_make_reaches_python_as_one_argument(self):
        # A spaced TAG proves the make entry point keeps it one shell argument (it is
        # passed through an environment variable and quoted) and is refused by the
        # complete-input validation before any Docker call.
        done = subprocess.run(["make", "--no-print-directory", "empirica-probe-image-pin",
                               "TAG=alpine:3.22 not-a-tag"],
                              cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("must be exactly <name:tag>", done.stderr + done.stdout)


if __name__ == "__main__":
    unittest.main()
