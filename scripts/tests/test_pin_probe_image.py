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

    def test_a_decoy_probe_image_elsewhere_is_never_the_rewritten_member(self):
        # A `probe_image`-shaped block earlier in the document than the real member: the
        # candidate that rewrites the decoy fails the parsed-document equality check, and
        # only the qualification member is published.
        carrier = json.loads(self.declared)
        decoy = {"reference": "docker.io/library/decoy:1@sha256:" + "e" * 64,
                 "repository": "decoy", "index_digest": "sha256:" + "e" * 64}
        # A 4-space `probe_image` member inside host_support, textually before qualification.
        carrier["host_support"]["probe_image"] = decoy
        seeded = self.contract.parent / "decoy.json"
        seeded.write_text(json.dumps(carrier, indent=2) + "\n", encoding="utf-8")
        digest = "sha256:" + "7" * 64
        runner = FakeRunner(stdout=index_manifest(digest))
        pin_tool.pin_probe_image("alpine:3.22", contract_path=seeded, runner=runner)
        published = json.loads(seeded.read_text(encoding="utf-8"))
        self.assertEqual(published["qualification"]["probe_image"]["index_digest"], digest)
        self.assertEqual(published["host_support"]["probe_image"], decoy)

    def test_a_wrong_location_only_match_is_refused_and_the_bytes_survive(self):
        # The qualification member is absent and only a decoy exists: refuse before any
        # publication, leaving the original bytes untouched.
        carrier = json.loads(self.declared)
        del carrier["qualification"]["probe_image"]
        carrier["host_support"]["probe_image"] = {
            "reference": "docker.io/library/decoy:1@sha256:" + "e" * 64,
            "repository": "decoy", "index_digest": "sha256:" + "e" * 64}
        seeded = self.contract.parent / "wrong.json"
        seeded.write_text(json.dumps(carrier, indent=2) + "\n", encoding="utf-8")
        before = seeded.read_bytes()
        runner = FakeRunner(stdout=index_manifest("sha256:" + "7" * 64))
        with self.assertRaisesRegex(pin_tool.PinError, "no qualification probe_image"):
            pin_tool.pin_probe_image("alpine:3.22", contract_path=seeded, runner=runner)
        self.assertEqual(seeded.read_bytes(), before)

    def test_an_ambiguous_or_unparsable_contract_is_refused_and_the_bytes_survive(self):
        seeded = self.contract.parent / "broken.json"
        seeded.write_text("{\"qualification\"", encoding="utf-8")
        before = seeded.read_bytes()
        runner = FakeRunner(stdout=index_manifest("sha256:" + "7" * 64))
        with self.assertRaisesRegex(pin_tool.PinError, "not JSON"):
            pin_tool.pin_probe_image("alpine:3.22", contract_path=seeded, runner=runner)
        self.assertEqual(seeded.read_bytes(), before)

    def test_a_valid_contract_with_duplicate_probe_image_keys_is_refused_and_the_bytes_survive(
            self):
        # Valid JSON whose qualification object carries two `probe_image` keys is still
        # ambiguous: stdlib json.loads would silently keep only the last, so exactly one
        # candidate could verify against the collapsed document. Duplicates are rejected
        # before replacements are tried, and the original bytes stay untouched.
        member = pin_tool._PIN_MEMBER.search(self.declared)
        self.assertIsNotNone(member)
        seeded = self.contract.parent / "duplicate.json"
        seeded.write_text(self.declared[:member.start()] + member.group(0) + ",\n"
                          + self.declared[member.start():], encoding="utf-8")
        duplicated = seeded.read_text(encoding="utf-8")
        self.assertIn('"probe_image": {', duplicated)
        self.assertEqual(duplicated.count('"probe_image": {'),
                         self.declared.count('"probe_image": {') + 1)
        json.loads(duplicated)  # still valid JSON: two keys, not a syntax error
        before = seeded.read_bytes()
        runner = FakeRunner(stdout=index_manifest("sha256:" + "7" * 64))
        with self.assertRaisesRegex(pin_tool.PinError, "duplicate"):
            pin_tool.pin_probe_image("alpine:3.22", contract_path=seeded, runner=runner)
        self.assertEqual(seeded.read_bytes(), before)

    def test_a_repository_path_over_255_characters_is_refused_before_any_buildx_call(self):
        runner = FakeRunner()
        with self.assertRaisesRegex(pin_tool.PinError, "255"):
            pin_tool.pin_probe_image("a" * 248 + ":3.22",
                                     contract_path=self.contract, runner=runner)
        self.assertIsNone(runner.arguments)


class MakeEntryTests(unittest.TestCase):
    def test_a_malformed_tag_through_make_reaches_python_as_one_argument(self):
        # A spaced TAG proves the make entry point keeps it one shell argument and it is
        # refused by the complete-input validation before any Docker call; the recipe never
        # splices the value into shell source (verified on macOS' GNU Make 3.81).
        done = subprocess.run(["make", "--no-print-directory", "empirica-probe-image-pin",
                               "TAG=alpine:3.22 not-a-tag"],
                              cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("must be exactly <name:tag>", done.stderr + done.stdout)

    def test_an_apostrophe_in_tag_never_breaks_the_recipe_shell(self):
        # The TAG travels through the environment, never through the recipe text: a literal
        # apostrophe reaches Python intact and is refused by validation, not by make or the shell.
        done = subprocess.run(["make", "--no-print-directory", "empirica-probe-image-pin",
                               "TAG=alpine:3.22'"],
                              cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("must be exactly <name:tag> (got \"alpine:3.22'\")",
                      done.stderr + done.stdout)


if __name__ == "__main__":
    unittest.main()
