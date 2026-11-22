#!/usr/bin/env python3
"""Fake-runner-only tests for the probe-image pin developer tool (no Docker daemon, no network)."""
from __future__ import annotations

import importlib.util
import json
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
        carrier = json.loads(
            (ROOT / "contracts" / "empirica-docker-spike" / "v1" / "support-contract.json"
             ).read_text(encoding="utf-8"))
        # A refusal writes nothing: the pinned field starts absent in every fixture.
        del carrier["qualification"]["probe_image"]
        self.contract.write_text(json.dumps(carrier, indent=2) + "\n", encoding="utf-8")

    def pin_field(self) -> object:
        return json.loads(self.contract.read_text(encoding="utf-8"))["qualification"]["probe_image"]

    def test_pins_the_index_digest_into_a_full_digest_reference_and_short_repository(self):
        digest = "sha256:" + "a" * 64
        runner = FakeRunner(stdout=index_manifest(digest))
        pin_tool.pin_probe_image("alpine:3.22", contract_path=self.contract, runner=runner)
        self.assertEqual(runner.arguments, [
            "docker", "buildx", "imagetools", "inspect",
            "docker.io/library/alpine:3.22", "--format", "{{json .Manifest}}"])
        pin = probe_image.parse_probe_image(self.pin_field())
        self.assertEqual((pin.reference, pin.repository, pin.index_digest),
                         (f"docker.io/library/alpine:3.22@{digest}", "alpine", digest))

    def test_a_registry_scoped_tag_stays_registry_scoped(self):
        digest = "sha256:" + "b" * 64
        runner = FakeRunner(stdout=index_manifest(digest))
        pin_tool.pin_probe_image("registry.example.com/team/tool:1.0",
                                 contract_path=self.contract, runner=runner)
        pin = probe_image.parse_probe_image(self.pin_field())
        self.assertEqual(pin.reference, f"registry.example.com/team/tool:1.0@{digest}")
        self.assertEqual(pin.repository, "tool")

    def test_no_arm64_index_entry_is_refused(self):
        digest = "sha256:" + "c" * 64
        runner = FakeRunner(stdout=index_manifest(digest, architectures=("amd64",)))
        with self.assertRaisesRegex(pin_tool.PinError, "linux/arm64"):
            pin_tool.pin_probe_image("alpine:3.22", contract_path=self.contract, runner=runner)
        self.assertNotIn("probe_image",
                         json.loads(self.contract.read_text(encoding="utf-8"))["qualification"])

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
        manifest = index_manifest("not-a-digest")
        runner = FakeRunner(stdout=manifest)
        with self.assertRaisesRegex(pin_tool.PinError, "digest"):
            pin_tool.pin_probe_image("alpine:3.22", contract_path=self.contract, runner=runner)

    def test_a_tag_without_a_name_component_separator_is_refused_before_any_runner_call(self):
        runner = FakeRunner()
        with self.assertRaisesRegex(pin_tool.PinError, "name:tag"):
            pin_tool.pin_probe_image("alpine@sha256:" + "a" * 64,
                                     contract_path=self.contract, runner=runner)
        self.assertIsNone(runner.arguments)


if __name__ == "__main__":
    unittest.main()
