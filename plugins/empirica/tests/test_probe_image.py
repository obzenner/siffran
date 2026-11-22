#!/usr/bin/env python3
"""Digest-pinned probe-image reader tests (fake vendored contracts only)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from adapters.sandbox import probe_image

REFERENCE = "docker.io/library/alpine:3.22@sha256:" + "4" * 64
PIN = {"reference": REFERENCE, "repository": "alpine", "index_digest": "sha256:" + "4" * 64}


def vendored_contract(qualification) -> str:
    return json.dumps({"schema": "siffran.empirica.docker-spike-support/1",
                       "contract_id": "empirica-docker-spike-support-v1", "qualification": qualification})


class PinnedProbeImageTests(unittest.TestCase):
    def test_reads_the_current_digest_pinned_probe_image(self):
        pin = probe_image.pinned_probe_image()
        self.assertEqual(pin.repository, "alpine")
        self.assertEqual(pin.reference, "docker.io/library/alpine:3.22@" + pin.index_digest)
        self.assertRegex(pin.repo_digest, r"^alpine@sha256:[0-9a-f]{64}$")

    def test_the_repository_digest_form_is_the_admission_key(self):
        pin = probe_image.parse_probe_image(PIN)
        self.assertEqual(pin.repo_digest, "alpine@sha256:" + "4" * 64)

    def test_an_absent_vendored_contract_fails_closed(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with mock.patch.object(probe_image, "PROBE_IMAGE_CONTRACT",
                               Path(temporary.name) / "absent.json"):
            with self.assertRaisesRegex(probe_image.ProbeImageError, "unreadable"):
                probe_image.pinned_probe_image()

    def test_a_malformed_vendored_contract_fails_closed(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "support-contract.json"
        path.write_text("{\"qualification\": \"nope\"", encoding="utf-8")
        with mock.patch.object(probe_image, "PROBE_IMAGE_CONTRACT", path):
            with self.assertRaisesRegex(probe_image.ProbeImageError, "malformed"):
                probe_image.pinned_probe_image()

    def test_a_vendored_contract_without_a_pin_fails_closed(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "support-contract.json"
        path.write_text(vendored_contract({"probes": []}), encoding="utf-8")
        with mock.patch.object(probe_image, "PROBE_IMAGE_CONTRACT", path):
            with self.assertRaisesRegex(probe_image.ProbeImageError, "probe_image"):
                probe_image.pinned_probe_image()

    def test_a_tampered_vendored_pin_fails_closed(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "support-contract.json"
        path.write_text(vendored_contract({"probe_image": {"reference": "alpine:3.22",
                                                           "repository": "alpine",
                                                           "index_digest": "sha256:" + "4" * 64}}),
                       encoding="utf-8")
        with mock.patch.object(probe_image, "PROBE_IMAGE_CONTRACT", path):
            with self.assertRaisesRegex(probe_image.ProbeImageError, "probe_image"):
                probe_image.pinned_probe_image()


class ParseProbeImageTests(unittest.TestCase):
    def test_exactly_the_three_fields_admit(self):
        pin = probe_image.parse_probe_image(PIN)
        self.assertEqual((pin.reference, pin.repository, pin.index_digest),
                         (REFERENCE, "alpine", "sha256:" + "4" * 64))

    def test_extra_and_missing_fields_fail(self):
        for probe in ({}, {**PIN, "id": "sha256:" + "9" * 64},
                      {**PIN, "index_digest": "sha256:" + "9" * 64},
                      {k: v for k, v in PIN.items() if k != "repository"}):
            with self.assertRaisesRegex(probe_image.ProbeImageError, "probe_image"):
                probe_image.parse_probe_image(probe)

    def test_the_reference_must_be_a_name_tag_and_digest(self):
        for reference in ("docker.io/library/alpine:3.22",
                         "docker.io/library/alpine@sha256:" + "4" * 64,
                         "docker.io/library/alpine:3.22@sha256:not-hex",
                         "docker.io/library/alpine:3.22@sha256:" + "4" * 63,
                         "docker.io/library/alpine:3:22@sha256:" + "4" * 64):
            with self.assertRaisesRegex(probe_image.ProbeImageError, "probe_image"):
                probe_image.parse_probe_image({**PIN, "reference": reference})

    def test_the_repository_must_be_the_short_reference_tail(self):
        for repository in ("library/alpine", "other",
                           "Alpine", "alpine!", "alpine..x", ""):
            with self.assertRaisesRegex(probe_image.ProbeImageError, "probe_image"):
                probe_image.parse_probe_image({**PIN, "repository": repository})

    def test_the_index_digest_must_equal_the_reference_digest(self):
        with self.assertRaisesRegex(probe_image.ProbeImageError, "probe_image"):
            probe_image.parse_probe_image(
                {**PIN, "index_digest": "sha256:" + "5" * 64})

    def test_a_short_name_reference_without_a_registry_prefix_admits(self):
        pin = probe_image.parse_probe_image({"reference": "alpine:3.22@sha256:" + "4" * 64,
                                             "repository": "alpine",
                                             "index_digest": "sha256:" + "4" * 64})
        self.assertEqual(pin.repo_digest, "alpine@sha256:" + "4" * 64)


if __name__ == "__main__":
    unittest.main()
