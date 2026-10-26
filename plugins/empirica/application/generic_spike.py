"""Single-container /2 spike preparation, typed resolver/harness ports, and sealed facts."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from core.canonical import canonical_digest
from core.freshness import FileBinding, FileObservation, ObservationState, validate_digest256, valid_claim_id
from core import spike_plan
from core.spike_plan_draft import SpikePlanDraft, canonicalize_spike_plan_draft, seal_spike_plan
from .ports import CapturedFile, Workspace, WorkspaceCapture


class GenericPreparationReason(str, Enum):
    IMAGE_NOT_LOCAL = "image-not-local"
    IMAGE_REFERENCE_AMBIGUOUS = "image-reference-ambiguous"
    IMAGE_PLATFORM_MISMATCH = "image-platform-mismatch"
    IMAGE_DECLARES_VOLUMES = "image-declares-volumes"
    MALFORMED_REQUIRED_RESULT = "malformed-required-result"
    AUTHORITY_DRIFT = "authority-drift"
    CAPTURE_INVALID = "capture-invalid"
    # The run is bound to `spikes_unavailable`; raised by the transaction, never by preparation.
    SPIKES_UNAVAILABLE = "spikes-unavailable"


class GenericPreparationUnavailable(ValueError):
    def __init__(self, reason: GenericPreparationReason):
        if type(reason) is not GenericPreparationReason:
            raise ValueError("generic preparation reason must be closed")
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class GenericQualifiedBackend:
    backend: str
    profile_id: str
    qualification_record_digest: str
    execution_binding_digest: str

    def __post_init__(self) -> None:
        if spike_plan.spike_backend_kind(self.to_data()) != "qualified":
            raise ValueError("generic backend profile is not closed")

    def to_data(self) -> dict[str, str]:
        return {
            "kind": "qualified", "backend": self.backend, "profile_id": self.profile_id,
            "qualification_record_digest": self.qualification_record_digest,
            "execution_binding_digest": self.execution_binding_digest,
        }


@dataclass(frozen=True, slots=True)
class LocalImageResolution:
    requested_reference: str
    image_id: str
    raw_os: str
    raw_architecture: str
    server_raw_os: str
    server_raw_architecture: str
    declared_volumes: tuple[str, ...]
    engine_ncpu: int
    backend: GenericQualifiedBackend


@dataclass(frozen=True, slots=True)
class LocalImageResolutionUnavailable:
    reason: GenericPreparationReason

    def __post_init__(self) -> None:
        if self.reason not in {
            GenericPreparationReason.IMAGE_NOT_LOCAL,
            GenericPreparationReason.IMAGE_REFERENCE_AMBIGUOUS,
            GenericPreparationReason.MALFORMED_REQUIRED_RESULT,
            GenericPreparationReason.AUTHORITY_DRIFT,
        }:
            raise ValueError("resolver unavailable reason is not closed")


class LocalImageResolver(Protocol):
    """Bounded read-only resolution under the exact run-bound backend."""
    def resolve(
        self, requested_reference: str, backend: GenericQualifiedBackend,
    ) -> LocalImageResolution | LocalImageResolutionUnavailable: ...


class NullLocalImageResolver:
    def resolve(self, requested_reference: str, backend: GenericQualifiedBackend):
        return LocalImageResolutionUnavailable(GenericPreparationReason.MALFORMED_REQUIRED_RESULT)


@dataclass(frozen=True, slots=True)
class GenericExecutionSnapshot:
    basis_id: str
    file_bindings: tuple[FileBinding, ...]
    captured_bytes: tuple[bytes, ...]
    snapshot_digest: str

    def __post_init__(self) -> None:
        validate_digest256(self.basis_id)
        if (type(self.file_bindings) is not tuple
                or any(type(item) is not FileBinding for item in self.file_bindings)
                or tuple(sorted(self.file_bindings, key=lambda item: item.path)) != self.file_bindings
                or len({item.path for item in self.file_bindings}) != len(self.file_bindings)
                or type(self.captured_bytes) is not tuple
                or any(type(item) is not bytes for item in self.captured_bytes)
                or len(self.file_bindings) != len(self.captured_bytes)):
            raise ValueError("generic snapshot files are not canonical immutable values")
        for binding, content in zip(self.file_bindings, self.captured_bytes):
            if "sha256:" + hashlib.sha256(content).hexdigest() != binding.sha256:
                raise ValueError("generic snapshot content differs from its binding")
        expected = canonical_digest({
            "basis_id": self.basis_id,
            "files": [(item.path, item.sha256, len(content))
                      for item, content in zip(self.file_bindings, self.captured_bytes)],
        })
        if self.snapshot_digest != expected:
            raise ValueError("generic snapshot digest differs")


@dataclass(frozen=True, slots=True)
class GenericSealedCapture:
    draft_digest: str
    plan: spike_plan.SealedSpikePlan
    snapshot: GenericExecutionSnapshot

    def __post_init__(self) -> None:
        validate_digest256(self.draft_digest)
        if type(self.plan) is not spike_plan.SealedSpikePlan \
                or type(self.snapshot) is not GenericExecutionSnapshot:
            raise ValueError("generic sealed capture values must be exact")
        if self.plan.draft_digest != self.draft_digest:
            raise ValueError("sealed plan differs from draft")
        plan_files = tuple((item.path, item.sha256, item.bytes) for item in self.plan.captured_files)
        snapshot_files = tuple((item.path, item.sha256, len(content))
                               for item, content in zip(
                                   self.snapshot.file_bindings, self.snapshot.captured_bytes))
        if plan_files != snapshot_files:
            raise ValueError("sealed plan files differ from immutable snapshot")


def _capture_basis(capture: object) -> GenericExecutionSnapshot:
    if type(capture) is not WorkspaceCapture or type(capture.basis_id) is not str \
            or type(capture.files) is not tuple:
        raise GenericPreparationUnavailable(GenericPreparationReason.CAPTURE_INVALID)
    if len(capture.files) > spike_plan.MAX_STAGING_FILES:
        raise GenericPreparationUnavailable(GenericPreparationReason.CAPTURE_INVALID)
    bindings: list[FileBinding] = []
    contents: list[bytes] = []
    previous = None
    total = 0
    for captured in capture.files:
        if type(captured) is not CapturedFile or type(captured.observation) is not FileObservation:
            raise GenericPreparationUnavailable(GenericPreparationReason.CAPTURE_INVALID)
        observation = captured.observation
        if (observation.state is not ObservationState.PRESENT
                or type(captured.content) is not bytes
                or previous is not None and observation.path <= previous):
            raise GenericPreparationUnavailable(GenericPreparationReason.CAPTURE_INVALID)
        previous = observation.path
        digest = "sha256:" + hashlib.sha256(captured.content).hexdigest()
        if observation.sha256 != digest or len(captured.content) > spike_plan.MAX_FILE_SIZE_BYTES:
            raise GenericPreparationUnavailable(GenericPreparationReason.CAPTURE_INVALID)
        total += len(captured.content)
        if total > spike_plan.MAX_STAGING_BYTES:
            raise GenericPreparationUnavailable(GenericPreparationReason.CAPTURE_INVALID)
        bindings.append(FileBinding(observation.path, digest))
        contents.append(captured.content)
    expected_basis = canonical_digest([
        (item.path, item.state.value, item.sha256)
        for item in (captured.observation for captured in capture.files)
    ])
    if capture.basis_id != expected_basis:
        raise GenericPreparationUnavailable(GenericPreparationReason.CAPTURE_INVALID)
    snapshot_digest = canonical_digest({
        "basis_id": capture.basis_id,
        "files": [(item.path, item.sha256, len(content))
                  for item, content in zip(bindings, contents)],
    })
    return GenericExecutionSnapshot(
        capture.basis_id, tuple(bindings), tuple(contents), snapshot_digest)


def _resolved_image(
    draft: SpikePlanDraft,
    backend: GenericQualifiedBackend,
    value: object,
) -> tuple[spike_plan.ResolvedImage, int]:
    if type(value) is LocalImageResolutionUnavailable:
        raise GenericPreparationUnavailable(value.reason)
    if type(value) is not LocalImageResolution:
        raise GenericPreparationUnavailable(GenericPreparationReason.MALFORMED_REQUIRED_RESULT)
    if value.backend != backend:
        raise GenericPreparationUnavailable(GenericPreparationReason.AUTHORITY_DRIFT)
    if (value.requested_reference != draft.image
            or type(value.declared_volumes) is not tuple
            or any(type(item) is not str or not item for item in value.declared_volumes)
            or type(value.engine_ncpu) is not int or isinstance(value.engine_ncpu, bool)
            or value.engine_ncpu <= 0):
        raise GenericPreparationUnavailable(GenericPreparationReason.MALFORMED_REQUIRED_RESULT)
    if value.declared_volumes:
        raise GenericPreparationUnavailable(GenericPreparationReason.IMAGE_DECLARES_VOLUMES)
    try:
        image_os = spike_plan.canonical_os(value.raw_os)
        image_arch = spike_plan.canonical_architecture(value.raw_architecture)
        server_os = spike_plan.canonical_os(value.server_raw_os)
        server_arch = spike_plan.canonical_architecture(value.server_raw_architecture)
        image = spike_plan.ResolvedImage(
            value.requested_reference, value.image_id, value.raw_os, image_os,
            value.raw_architecture, image_arch, ())
    except (TypeError, ValueError, spike_plan.SpikePlanError) as exc:
        raise GenericPreparationUnavailable(
            GenericPreparationReason.MALFORMED_REQUIRED_RESULT) from exc
    if image_os != server_os or image_arch != server_arch or server_os != "linux":
        raise GenericPreparationUnavailable(GenericPreparationReason.IMAGE_PLATFORM_MISMATCH)
    return image, value.engine_ncpu


def prepare_generic_spike(
    draft: SpikePlanDraft,
    workspace: Workspace,
    resolver: LocalImageResolver,
    backend: GenericQualifiedBackend,
) -> GenericSealedCapture:
    """Resolve and capture read-only facts before request CAS; perform no staging or effects."""
    if type(draft) is not SpikePlanDraft or type(backend) is not GenericQualifiedBackend:
        raise GenericPreparationUnavailable(GenericPreparationReason.MALFORMED_REQUIRED_RESULT)
    if canonicalize_spike_plan_draft(draft.to_data()) != draft:
        raise GenericPreparationUnavailable(GenericPreparationReason.MALFORMED_REQUIRED_RESULT)
    try:
        resolved_value = resolver.resolve(draft.image, backend)
    except Exception as exc:
        raise GenericPreparationUnavailable(
            GenericPreparationReason.MALFORMED_REQUIRED_RESULT) from exc
    image, engine_ncpu = _resolved_image(draft, backend, resolved_value)
    try:
        capture = workspace.capture_inputs(draft.inputs)
    except Exception as exc:
        raise GenericPreparationUnavailable(GenericPreparationReason.CAPTURE_INVALID) from exc
    snapshot = _capture_basis(capture)
    captured_files = tuple(
        spike_plan.CapturedFile(binding.path, binding.sha256, len(content))
        for binding, content in zip(snapshot.file_bindings, snapshot.captured_bytes))
    try:
        plan = seal_spike_plan(
            draft, image, captured_files, engine_ncpu=engine_ncpu,
            network_profile=backend.profile_id)
    except Exception as exc:
        raise GenericPreparationUnavailable(GenericPreparationReason.CAPTURE_INVALID) from exc
    return GenericSealedCapture(draft.digest, plan, snapshot)


def reobserve_authority(
    sealed: GenericSealedCapture,
    draft: SpikePlanDraft,
    backend: GenericQualifiedBackend,
    resolver: LocalImageResolver,
) -> GenericPreparationReason | None:
    """None when the image and engine still equal the sealed ones; otherwise the closed reason
    (the resolver's own, such as `image-not-local`, or `authority-drift` for a changed match)."""
    try:
        value = resolver.resolve(draft.image, backend)
        image, engine_ncpu = _resolved_image(draft, backend, value)
    except GenericPreparationUnavailable as exc:
        return exc.reason
    except Exception:
        return GenericPreparationReason.MALFORMED_REQUIRED_RESULT
    if image == sealed.plan.image and engine_ncpu == sealed.plan.engine_ncpu:
        return None
    return GenericPreparationReason.AUTHORITY_DRIFT


def reobserve_generic_spike(sealed: GenericSealedCapture, workspace: Workspace) -> bool:
    """Re-observe exact expanded files without re-reading bytes for execution."""
    paths = tuple(item.path for item in sealed.snapshot.file_bindings)
    try:
        capture = workspace.observe(paths)
    except Exception:
        return False
    if type(capture) is not WorkspaceCapture or len(capture.files) != len(paths):
        return False
    observations = tuple(item.observation for item in capture.files
                         if type(item) is CapturedFile)
    return (len(observations) == len(paths)
            and tuple((item.path, item.sha256) for item in observations)
            == tuple((item.path, item.sha256) for item in sealed.snapshot.file_bindings))


@dataclass(frozen=True, slots=True)
class GenericHarnessRequest:
    run_id: str
    backend: GenericQualifiedBackend
    plan: spike_plan.SealedSpikePlan
    plan_digest: str
    policy_digest: str
    snapshot: GenericExecutionSnapshot

    def __post_init__(self) -> None:
        if (not valid_claim_id(self.run_id) or type(self.backend) is not GenericQualifiedBackend
                or type(self.plan) is not spike_plan.SealedSpikePlan
                or self.plan_digest != self.plan.digest
                or self.policy_digest != spike_plan.FRAMEWORK_SPIKE_POLICY.digest
                or type(self.snapshot) is not GenericExecutionSnapshot):
            raise ValueError("generic harness request is malformed")
        if self.plan.network_profile != self.backend.profile_id:
            raise ValueError("generic harness request authority differs")


@dataclass(frozen=True, slots=True)
class GenericStreamFact:
    bytes_seen: int
    sha256: str

    def __post_init__(self) -> None:
        if type(self.bytes_seen) is not int or isinstance(self.bytes_seen, bool) or self.bytes_seen < 0:
            raise ValueError("generic stream byte count is invalid")
        validate_digest256(self.sha256)


@dataclass(frozen=True, slots=True)
class GenericOperationFacts:
    operation_digest: str
    qualification_record_digest: str
    execution_binding_digest: str
    image_digest: str
    input_digest: str
    process_digest: str
    topology_digest: str
    timing_digest: str

    def __post_init__(self) -> None:
        for value in (
            self.operation_digest, self.qualification_record_digest,
            self.execution_binding_digest, self.image_digest, self.input_digest,
            self.process_digest, self.topology_digest, self.timing_digest,
        ):
            validate_digest256(value)


class GenericVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class GenericHarnessResult:
    run_id: str
    backend: GenericQualifiedBackend
    plan_digest: str
    policy_digest: str
    snapshot_digest: str
    facts: GenericOperationFacts
    stdout: GenericStreamFact
    stderr: GenericStreamFact
    natural_exit: bool
    forced_termination: bool
    exit_code: int | None
    verdict: GenericVerdict
    cleanup_proven: bool
    postflight_identity_digest: str

    def __post_init__(self) -> None:
        if (not valid_claim_id(self.run_id) or type(self.backend) is not GenericQualifiedBackend
                or type(self.facts) is not GenericOperationFacts
                or type(self.stdout) is not GenericStreamFact
                or type(self.stderr) is not GenericStreamFact
                or type(self.natural_exit) is not bool
                or type(self.forced_termination) is not bool
                or self.natural_exit == self.forced_termination):
            raise ValueError("generic harness result is malformed")
        for value in (self.plan_digest, self.policy_digest, self.snapshot_digest,
                      self.postflight_identity_digest):
            validate_digest256(value)
        if (self.natural_exit is not True or type(self.exit_code) is not int
                or isinstance(self.exit_code, bool) or not 0 <= self.exit_code <= 255
                or self.exit_code == 137):
            raise ValueError("generic result requires one admissible natural exit")
        expected = GenericVerdict.PASS if self.exit_code == 0 else GenericVerdict.FAIL
        if self.verdict is not expected or self.cleanup_proven is not True \
                or self.postflight_identity_digest != self.backend.execution_binding_digest:
            raise ValueError("generic verdict, cleanup, or postflight differs")


class GenericUnavailableReason(str, Enum):
    AUTHORITY_INVALID = "authority-invalid"
    LIFECYCLE_UNAVAILABLE = "lifecycle-unavailable"
    CLEANUP_UNAVAILABLE = "cleanup-unavailable"
    POSTFLIGHT_CHANGED = "postflight-changed"


# The closed agent-visible spike Fault `reason` vocabulary (public-contract `evidence/spike/limits`).
SPIKE_FAULT_REASONS = tuple(item.value for item in GenericPreparationReason) + tuple(
    item.value for item in GenericUnavailableReason)


@dataclass(frozen=True, slots=True)
class GenericHarnessUnavailable:
    reason: GenericUnavailableReason
    private_detail: str | None = None

    def __post_init__(self) -> None:
        if type(self.reason) is not GenericUnavailableReason:
            raise ValueError("generic unavailable reason is malformed")
        if (self.private_detail is not None
                and (type(self.private_detail) is not str
                     or not self.private_detail
                     or len(self.private_detail.encode("utf-8")) > 4096)):
            raise ValueError("generic private unavailable detail is malformed")


class GenericSpikeHarness(Protocol):
    def run(self, request: GenericHarnessRequest) -> GenericHarnessResult | GenericHarnessUnavailable: ...


def sealed_request_facts(sealed: GenericSealedCapture, backend: GenericQualifiedBackend) -> dict[str, object]:
    if type(sealed) is not GenericSealedCapture or type(backend) is not GenericQualifiedBackend:
        raise ValueError("generic sealed request facts require exact values")
    return {
        "draft_digest": sealed.draft_digest,
        "plan": sealed.plan.to_data(),
        "plan_digest": sealed.plan.digest,
        "policy": spike_plan.FRAMEWORK_SPIKE_POLICY.to_data(),
        "policy_digest": spike_plan.FRAMEWORK_SPIKE_POLICY.digest,
        "operation_timeout_seconds": spike_plan.BACKEND_OPERATION_TIMEOUT_SECONDS,
        "snapshot_basis_id": sealed.snapshot.basis_id,
        "snapshot_digest": sealed.snapshot.snapshot_digest,
        "backend": backend.to_data(),
    }


def sealed_result_facts(result: GenericHarnessResult) -> dict[str, object]:
    if type(result) is not GenericHarnessResult:
        raise ValueError("generic sealed result facts require an exact result")
    body: dict[str, object] = {
        "run_id": result.run_id,
        "backend": result.backend.to_data(),
        "plan_digest": result.plan_digest,
        "policy_digest": result.policy_digest,
        "snapshot_digest": result.snapshot_digest,
        "operation_facts": {name: getattr(result.facts, name) for name in (
            "operation_digest", "qualification_record_digest", "execution_binding_digest",
            "image_digest", "input_digest", "process_digest", "topology_digest", "timing_digest")},
        "stdout": {"bytes_seen": result.stdout.bytes_seen, "sha256": result.stdout.sha256},
        "stderr": {"bytes_seen": result.stderr.bytes_seen, "sha256": result.stderr.sha256},
        "natural_exit": result.natural_exit,
        "forced_termination": result.forced_termination,
        "exit_code": result.exit_code,
        "verdict": result.verdict.value,
        "cleanup_proven": result.cleanup_proven,
        "postflight_identity_digest": result.postflight_identity_digest,
    }
    body["harness_result_digest"] = canonical_digest(body)
    return body


def validate_generic_harness_result(request: GenericHarnessRequest, result: object):
    if type(request) is not GenericHarnessRequest:
        raise ValueError("generic harness validation requires exact request")
    if type(result) is GenericHarnessUnavailable:
        return result
    if type(result) is not GenericHarnessResult:
        raise ValueError("generic harness returned an unknown value")
    if (result.run_id != request.run_id or result.backend != request.backend
            or result.plan_digest != request.plan_digest
            or result.policy_digest != request.policy_digest
            or result.snapshot_digest != request.snapshot.snapshot_digest):
        raise ValueError("generic harness result differs from exact request")
    if (result.stdout.bytes_seen > spike_plan.STREAM_BYTES
            or result.stderr.bytes_seen > spike_plan.STREAM_BYTES
            or result.facts.qualification_record_digest != request.backend.qualification_record_digest
            or result.facts.execution_binding_digest != request.backend.execution_binding_digest
            or result.facts.input_digest != request.snapshot.snapshot_digest):
        raise ValueError("generic result authority, input, or stream facts differ")
    return result
