#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import unittest
from unittest.mock import patch

from application import generic_spike as subject
from application.ports import CapturedFile, WorkspaceCapture
from core.canonical import canonical_digest
from core.freshness import FileObservation, ObservationState
from core.spike_plan_draft import canonicalize_spike_plan_draft

D0 = "sha256:" + "0" * 64
D1 = "sha256:" + "1" * 64


def backend(profile="docker-isolated-v1"):
    return subject.GenericQualifiedBackend("docker", profile, D0, D1)


def draft(*, inputs=None):
    return canonicalize_spike_plan_draft({
        "schema": "empirica.spike-plan-draft/2", "image": "python:3.12",
        "argv": ["python", "/inputs/src/a.py"],
        "inputs": ["src/"] if inputs is None else inputs,
        "environment": {"PYTHONPATH": "/inputs"},
    })


class Resolver:
    def __init__(self, value=None):
        self.calls = []
        self.value = value

    def resolve(self, reference, selected):
        self.calls.append((reference, selected))
        return self.value or subject.LocalImageResolution(
            reference, D1, "linux", "aarch64", "linux", "arm64", (), 14, selected)


class Workspace:
    def __init__(self, files=None):
        self.files = {"src/a.py": b"a"} if files is None else dict(files)
        self.capture_calls = []
        self.observe_calls = []

    def _capture(self, paths):
        rows = []
        for path in paths:
            content = self.files[path]
            digest = "sha256:" + hashlib.sha256(content).hexdigest()
            rows.append(CapturedFile(
                FileObservation(path, ObservationState.PRESENT, digest), content))
        basis = canonical_digest([(row.observation.path, row.observation.state.value,
                                   row.observation.sha256) for row in rows])
        return WorkspaceCapture(basis, tuple(rows))

    def capture_inputs(self, declarations):
        self.capture_calls.append(declarations)
        return self._capture(tuple(sorted(self.files)))

    def observe(self, paths):
        self.observe_calls.append(paths)
        return self._capture(paths)


class GenericSpikeApplicationTests(unittest.TestCase):
    def test_prepare_resolves_then_captures_and_seals_single_snapshot(self):
        selected, source, images = backend(), Workspace(), Resolver()
        sealed = subject.prepare_generic_spike(draft(), source, images, selected)
        self.assertEqual(images.calls, [("python:3.12", selected)])
        self.assertEqual(source.capture_calls, [("src/",)])
        self.assertEqual(sealed.plan.image.image_id, D1)
        self.assertEqual(sealed.plan.image.raw_architecture, "aarch64")
        self.assertEqual(sealed.plan.image.architecture, "arm64")
        self.assertEqual(sealed.plan.engine_ncpu, 14)
        self.assertEqual(sealed.plan.cpu_count, 2)
        self.assertEqual([item.path for item in sealed.plan.captured_files], ["src/a.py"])
        self.assertEqual(sealed.snapshot.captured_bytes, (b"a",))
        self.assertNotIn("containers", sealed.plan.to_data())
        self.assertNotIn("networks", sealed.plan.to_data())

    def test_empty_inputs_seal_an_empty_immutable_tree(self):
        source = Workspace({})
        sealed = subject.prepare_generic_spike(
            draft(inputs=[]), source, Resolver(), backend())
        self.assertEqual(sealed.snapshot.file_bindings, ())
        self.assertEqual(sealed.snapshot.captured_bytes, ())
        self.assertEqual(sealed.plan.captured_files, ())

    def test_resolution_refusal_reasons_are_exact_and_capture_never_runs(self):
        selected = backend()
        for reason in (
            subject.GenericPreparationReason.IMAGE_NOT_LOCAL,
            subject.GenericPreparationReason.IMAGE_REFERENCE_AMBIGUOUS,
            subject.GenericPreparationReason.MALFORMED_REQUIRED_RESULT,
            subject.GenericPreparationReason.AUTHORITY_DRIFT,
        ):
            source = Workspace()
            resolver = Resolver(subject.LocalImageResolutionUnavailable(reason))
            with self.subTest(reason=reason), self.assertRaises(
                    subject.GenericPreparationUnavailable) as raised:
                subject.prepare_generic_spike(draft(), source, resolver, selected)
            self.assertIs(raised.exception.reason, reason)
            self.assertEqual(source.capture_calls, [])

    def test_platform_volume_malformed_and_authority_drift_reasons(self):
        selected = backend()
        cases = (
            (subject.LocalImageResolution(
                "python:3.12", D1, "linux", "amd64", "linux", "arm64", (), 2, selected),
             subject.GenericPreparationReason.IMAGE_PLATFORM_MISMATCH),
            (subject.LocalImageResolution(
                "python:3.12", D1, "linux", "arm64", "linux", "arm64", ("/data",), 2, selected),
             subject.GenericPreparationReason.IMAGE_DECLARES_VOLUMES),
            (subject.LocalImageResolution(
                "python:3.12", "bad", "linux", "arm64", "linux", "arm64", (), 2, selected),
             subject.GenericPreparationReason.MALFORMED_REQUIRED_RESULT),
            (subject.LocalImageResolution(
                "python:3.12", D1, "linux", "arm64", "linux", "arm64", (), 2,
                backend("docker-networked-v1")),
             subject.GenericPreparationReason.AUTHORITY_DRIFT),
        )
        for value, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(
                    subject.GenericPreparationUnavailable) as raised:
                subject.prepare_generic_spike(draft(), Workspace(), Resolver(value), selected)
            self.assertIs(raised.exception.reason, reason)

    def test_capture_size_digest_state_and_order_fail_closed(self):
        selected = backend()
        valid = Workspace()._capture(("src/a.py",))
        ordered = Workspace({"src/a.py": b"a", "src/b.py": b"b"})._capture(
            ("src/a.py", "src/b.py"))
        bad_rows = [
            object(),
            WorkspaceCapture(ordered.basis_id, tuple(reversed(ordered.files))),
            WorkspaceCapture(D0, valid.files),
            WorkspaceCapture(valid.basis_id, (CapturedFile(
                FileObservation("src/a.py", ObservationState.MISSING, None), None),)),
        ]
        class BadWorkspace(Workspace):
            def __init__(self, value): self.value = value
            def capture_inputs(self, declarations): return self.value
        for value in bad_rows:
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(subject.GenericPreparationUnavailable) as raised:
                    subject.prepare_generic_spike(
                        draft(), BadWorkspace(value), Resolver(), selected)
                self.assertIs(
                    raised.exception.reason, subject.GenericPreparationReason.CAPTURE_INVALID)

    def test_capture_count_per_file_and_aggregate_limits_fail_closed(self):
        cases = (
            ("MAX_STAGING_FILES", 0),
            ("MAX_FILE_SIZE_BYTES", 0),
            ("MAX_STAGING_BYTES", 0),
        )
        capture = Workspace()._capture(("src/a.py",))
        for constant, value in cases:
            with self.subTest(constant=constant):
                with patch.object(subject.spike_plan, constant, value), \
                        self.assertRaises(subject.GenericPreparationUnavailable) as raised:
                    subject._capture_basis(capture)
                self.assertIs(
                    raised.exception.reason, subject.GenericPreparationReason.CAPTURE_INVALID)

    def test_reobservation_and_authority_are_exact_postflight_checks(self):
        selected, source, resolver = backend(), Workspace(), Resolver()
        sealed = subject.prepare_generic_spike(draft(), source, resolver, selected)
        self.assertTrue(subject.reobserve_generic_spike(sealed, source))
        self.assertIsNone(subject.reobserve_authority(sealed, draft(), selected, resolver))
        source.files["src/a.py"] = b"changed"
        self.assertFalse(subject.reobserve_generic_spike(sealed, source))
        moved = Resolver(subject.LocalImageResolution(
            "python:3.12", D0, "linux", "arm64", "linux", "arm64", (), 14, selected))
        self.assertIs(subject.reobserve_authority(sealed, draft(), selected, moved),
                      subject.GenericPreparationReason.AUTHORITY_DRIFT)
        gone = Resolver(subject.LocalImageResolutionUnavailable(
            subject.GenericPreparationReason.IMAGE_NOT_LOCAL))
        self.assertIs(subject.reobserve_authority(sealed, draft(), selected, gone),
                      subject.GenericPreparationReason.IMAGE_NOT_LOCAL)

    def test_sealed_request_contains_no_bytes_or_old_partitions(self):
        selected = backend()
        sealed = subject.prepare_generic_spike(draft(), Workspace(), Resolver(), selected)
        facts = subject.sealed_request_facts(sealed, selected)
        self.assertEqual(set(facts), {
            "draft_digest", "plan", "plan_digest", "policy", "policy_digest",
            "operation_timeout_seconds", "snapshot_basis_id", "snapshot_digest", "backend",
        })
        self.assertNotIn("inputs", facts)
        self.assertNotIn("captured_bytes", repr(facts))

    def test_natural_137_cannot_construct_result_evidence(self):
        stream = subject.GenericStreamFact(0, D0)
        facts = subject.GenericOperationFacts(D0, D0, D1, D1, D0, D0, D0, D0)
        with self.assertRaises(ValueError):
            subject.GenericHarnessResult(
                "r-12345678901234567890123456789012", backend(), D0, D0, D0,
                facts, stream, stream, True, False, 137,
                subject.GenericVerdict.FAIL, True, D1)


if __name__ == "__main__":
    unittest.main()
