"""Operator-owned one-container Docker qualification and explicit candidate installation.

Qualification writes only a caller-selected candidate file. Installation is a separate explicit
operation; ordinary runtime imports neither operation and can only read exact `/2` records.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from adapters.state import fsio
from application import generic_spike as application
from core import spike_plan
from core.canonical import canonical_digest
from core.freshness import FileBinding, validate_digest256

from . import (common, generic_admission as admission, generic_authority as authority,
               generic_execution as execution, generic_journal, planner, process)

TOTAL_QUALIFICATION_SECONDS = 60.0
QUALIFICATION_TIMEOUT_SECONDS = 5.0
CANDIDATE_SCHEMA = "siffran.empirica.docker-spike-qualification-candidate/2"
RECORD_SCHEMA = "siffran.empirica.docker-spike-qualification-record/2"
POINTER_SCHEMA = "siffran.empirica.docker-spike-qualification-pointer/2"
NAMESPACE = "docker-spike-qualification-v2"
MAX_CANDIDATE_BYTES = 2 * 1024 * 1024
PROBE_ORDER = ("realization", "natural-125", "output-overflow", "timeout", "network-profile")
PROBE_EXPECTED = ("PASS", "FAIL", "UNAVAILABLE_OUTPUT_LIMIT", "UNAVAILABLE_TIMEOUT", "PASS")
IMAGE_CAPABILITIES = ("/bin/sh", "cat", "chmod", "command", "head", "id", "printf",
                      "sleep", "test", "touch", "wget", "uid65534-readable-executable")
_FULL_DIGEST = "sha256:"


class QualificationReason(str, Enum):
    INVALID_INPUT = "invalid-input"
    CAPABILITY_UNAVAILABLE = "capability-unavailable"
    RESOURCE_LIMITS_UNAVAILABLE = "resource-limits-unavailable"
    NETWORK_EGRESS_UNAVAILABLE = "network-egress-unavailable"
    IMAGE_NOT_LOCAL = "image-not-local"
    IMAGE_REFERENCE_AMBIGUOUS = "image-reference-ambiguous"
    IMAGE_MALFORMED = "image-malformed"
    IMAGE_INSPECT_FAILED = "image-inspect-failed"
    IMAGE_INSPECT_TIMEOUT = "image-inspect-timeout"
    IMAGE_INSPECT_OUTPUT_LIMIT = "image-inspect-output-limit"
    IMAGE_PLATFORM_MISMATCH = "image-platform-mismatch"
    IMAGE_DECLARES_VOLUMES = "image-declares-volumes"
    IMAGE_TOOL_MISSING = "image-tool-missing"
    IMAGE_UID_UNUSABLE = "image-uid65534-unusable"
    PROBE_FAILED = "probe-failed"
    CLEANUP_UNAVAILABLE = "cleanup-unavailable"
    DEADLINE_EXCEEDED = "qualification-deadline-exceeded"
    CANDIDATE_UNSAFE = "candidate-output-unsafe"
    INSTALL_VALIDATION_FAILED = "install-validation-failed"


class GenericQualificationFailure(RuntimeError):
    def __init__(self, reason: QualificationReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class QualificationImage:
    reference: str
    image_id: str
    raw_os: str
    os: str
    raw_architecture: str
    architecture: str
    declared_volumes: tuple[str, ...]

    def __post_init__(self) -> None:
        try:
            spike_plan.validate_local_image_reference(self.reference)
            validate_digest256(self.image_id)
        except ValueError as exc:
            raise GenericQualificationFailure(
                QualificationReason.IMAGE_MALFORMED, "qualification image identity is malformed") from exc
        if (self.os != spike_plan.canonical_os(self.raw_os)
                or self.architecture != spike_plan.canonical_architecture(self.raw_architecture)
                or type(self.declared_volumes) is not tuple):
            raise GenericQualificationFailure(
                QualificationReason.IMAGE_MALFORMED, "qualification image platform is malformed")

    def to_data(self) -> dict[str, Any]:
        return {
            "reference": self.reference, "id": self.image_id,
            "raw_os": self.raw_os, "os": self.os,
            "raw_architecture": self.raw_architecture, "architecture": self.architecture,
            "declared_volumes": list(self.declared_volumes),
            "capabilities": list(IMAGE_CAPABILITIES),
        }


@dataclass(frozen=True, slots=True)
class ProbeObservation:
    name: str
    expected: str
    observed: str
    operation_digest: str
    cleanup_proven: bool
    duration_ms: int
    running_network_membership: bool
    realization_digest: str | None = None

    def to_data(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in (
            "name", "expected", "observed", "operation_digest", "cleanup_proven",
            "duration_ms", "running_network_membership", "realization_digest")}


class QualificationProbeKernel(Protocol):
    def run(self, name: str, expected: str, *, deadline: float) -> ProbeObservation: ...
    def cleanup_proof(self) -> dict[str, bool]: ...


def _strict_digest(value: object, where: str) -> str:
    try:
        return validate_digest256(value)
    except ValueError as exc:
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, where + " is not a lowercase digest") from exc


def _positive_ms(value: object, where: str, *, allow_zero: bool = True) -> int:
    if (type(value) is not int or isinstance(value, bool)
            or value < (0 if allow_zero else 1) or value >= 60_000):
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, where + " is outside its bound")
    return value


def validate_probe(value: object, index: int) -> dict[str, Any]:
    fields = {"name", "expected", "observed", "operation_digest", "cleanup_proven",
              "duration_ms", "running_network_membership", "realization_digest"}
    if type(value) is not dict or set(value) != fields or index >= len(PROBE_ORDER):
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, "qualification probe shape is unknown")
    row = dict(value)
    if (row["name"] != PROBE_ORDER[index] or row["expected"] != PROBE_EXPECTED[index]
            or row["observed"] != row["expected"] or row["cleanup_proven"] is not True
            or type(row["running_network_membership"]) is not bool):
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, "qualification probe outcome differs")
    _strict_digest(row["operation_digest"], "probe operation digest")
    _positive_ms(row["duration_ms"], "probe duration")
    # A verdict probe records the container's observed top-level `Image` realization digest; an
    # unavailable probe admits no result, so it records none.
    if row["expected"] in {"PASS", "FAIL"}:
        _strict_digest(row["realization_digest"], "probe realization digest")
    elif row["realization_digest"] is not None:
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED,
            "unavailable probe cannot claim a realization digest")
    return row


def _validate_image(value: object, runtime: Mapping[str, Any]) -> dict[str, Any]:
    fields = {"reference", "id", "raw_os", "os", "raw_architecture", "architecture",
              "declared_volumes", "capabilities"}
    if type(value) is not dict or set(value) != fields:
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, "qualification image evidence is unknown")
    row = dict(value)
    image = QualificationImage(
        row["reference"], row["id"], row["raw_os"], row["os"],
        row["raw_architecture"], row["architecture"], tuple(row["declared_volumes"]))
    if (image.os != "linux" or image.os != runtime["server_os"]
            or image.architecture != runtime["server_architecture"]):
        raise GenericQualificationFailure(
            QualificationReason.IMAGE_PLATFORM_MISMATCH, "qualification image is not native Linux")
    if image.declared_volumes:
        raise GenericQualificationFailure(
            QualificationReason.IMAGE_DECLARES_VOLUMES, "qualification image declares volumes")
    if row["capabilities"] != list(IMAGE_CAPABILITIES):
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, "qualification image capabilities differ")
    return image.to_data()


def _validate_candidate_record(value: object) -> dict[str, Any]:
    fields = {"schema", "profile_id", "runtime", "semantic_authority", "authority_digest",
              "policy_digest", "implementation_digest", "qualification_image", "probes",
              "cleanup", "qualification_elapsed_ms", "execution_binding_digest",
              "qualification_record_digest"}
    if type(value) is not dict or set(value) != fields or value.get("schema") != RECORD_SCHEMA:
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, "qualification record schema or shape is unknown")
    row = json.loads(json.dumps(value))
    if not isinstance(row["profile_id"], str) or row["profile_id"] not in spike_plan.NETWORK_PROFILES:
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, "qualification profile is unknown")
    runtime = authority.validate_runtime(row["runtime"])
    semantic = authority.authority_facts(runtime)
    if row["semantic_authority"] != semantic or row["authority_digest"] != canonical_digest(semantic):
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, "qualification semantic authority differs")
    if row["policy_digest"] != spike_plan.FRAMEWORK_SPIKE_POLICY.digest:
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, "qualification framework policy differs")
    _strict_digest(row["implementation_digest"], "implementation digest")
    row["qualification_image"] = _validate_image(row["qualification_image"], runtime)
    if type(row["probes"]) is not list or len(row["probes"]) != len(PROBE_ORDER):
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, "qualification must contain five probes")
    row["probes"] = [validate_probe(probe, index) for index, probe in enumerate(row["probes"])]
    expected_running = row["profile_id"] == spike_plan.NETWORKED_PROFILE
    if row["probes"][-1]["running_network_membership"] is not expected_running \
            or any(probe["running_network_membership"] for probe in row["probes"][:-1]):
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, "network membership timing proof differs")
    cleanup = {"container_ids_absent": True, "network_ids_absent": True,
               "staging_empty": True, "journals_empty": True}
    if row["cleanup"] != cleanup:
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, "qualification cleanup proof differs")
    elapsed = _positive_ms(row["qualification_elapsed_ms"], "qualification elapsed time")
    if elapsed >= int(TOTAL_QUALIFICATION_SECONDS * 1000):
        raise GenericQualificationFailure(
            QualificationReason.DEADLINE_EXCEEDED, "qualification exceeded sixty seconds")
    binding_body = {
        "schema": row["schema"], "profile_id": row["profile_id"],
        "authority_digest": row["authority_digest"], "policy_digest": row["policy_digest"],
        "implementation_digest": row["implementation_digest"],
        "qualification_image_digest": canonical_digest(row["qualification_image"]),
        "probe_digest": canonical_digest(row["probes"]), "cleanup": cleanup,
    }
    if row["execution_binding_digest"] != canonical_digest(binding_body):
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, "qualification execution binding differs")
    body = {key: row[key] for key in fields - {"qualification_record_digest"}}
    if row["qualification_record_digest"] != canonical_digest(body):
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED, "qualification record digest differs")
    row["runtime"] = runtime
    return row


def validate_candidate_record(value: object) -> dict[str, Any]:
    """Validate the exact closed record and collapse every malformed form fail-closed."""
    try:
        return _validate_candidate_record(value)
    except GenericQualificationFailure:
        raise
    except Exception as exc:
        raise GenericQualificationFailure(
            QualificationReason.INSTALL_VALIDATION_FAILED,
            "qualification record contains malformed values") from exc


def build_candidate_record(
    profile_id: str,
    runtime: Mapping[str, Any],
    implementation_digest: str,
    image: QualificationImage,
    probes: tuple[ProbeObservation, ...],
    cleanup: Mapping[str, bool],
    elapsed_ms: int,
) -> dict[str, Any]:
    checked = authority.validate_runtime(dict(runtime))
    semantic = authority.authority_facts(checked)
    body: dict[str, Any] = {
        "schema": RECORD_SCHEMA, "profile_id": profile_id, "runtime": checked,
        "semantic_authority": semantic, "authority_digest": canonical_digest(semantic),
        "policy_digest": spike_plan.FRAMEWORK_SPIKE_POLICY.digest,
        "implementation_digest": implementation_digest,
        "qualification_image": image.to_data(),
        "probes": [probe.to_data() for probe in probes], "cleanup": dict(cleanup),
        "qualification_elapsed_ms": elapsed_ms,
    }
    body["execution_binding_digest"] = canonical_digest({
        "schema": body["schema"], "profile_id": profile_id,
        "authority_digest": body["authority_digest"], "policy_digest": body["policy_digest"],
        "implementation_digest": implementation_digest,
        "qualification_image_digest": canonical_digest(body["qualification_image"]),
        "probe_digest": canonical_digest(body["probes"]), "cleanup": body["cleanup"],
    })
    return validate_candidate_record({
        **body, "qualification_record_digest": canonical_digest(body)})


def _command_result(value: object, where: str) -> process.CommandResult:
    try:
        return authority._admit_command_result(value, authority.MAX_POINTER_BYTES, where)
    except Exception as exc:
        raise GenericQualificationFailure(
            QualificationReason.IMAGE_MALFORMED, where + " returned malformed output") from exc


def inspect_qualification_image(
    reference: str,
    runtime: Mapping[str, Any],
    *,
    config: Path,
    runner: process.Runner,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> QualificationImage:
    try:
        spike_plan.validate_local_image_reference(reference)
    except ValueError as exc:
        raise GenericQualificationFailure(
            QualificationReason.INVALID_INPUT, "qualification image reference is malformed") from exc
    if deadline <= clock():
        raise GenericQualificationFailure(
            QualificationReason.DEADLINE_EXCEEDED, "qualification image deadline expired")
    checked = authority.validate_runtime(dict(runtime))
    environment = {"LANG": "C", "LC_ALL": "C", "DOCKER_HOST": checked["endpoint"],
                   "DOCKER_CONFIG": str(config)}
    argv = (checked["docker_cli"], "image", "inspect", "--format",
            planner.IMAGE_INSPECT_FORMAT, reference)
    try:
        raw_result = runner.run(
            argv, timeout_seconds=min(10.0, deadline - clock()),
            output_limit_bytes=authority.MAX_POINTER_BYTES, env=environment)
    except Exception as exc:
        raise GenericQualificationFailure(
            QualificationReason.IMAGE_INSPECT_FAILED,
            "qualification image inspect could not be executed") from exc
    result = _command_result(raw_result, "qualification image inspect")
    if result.timed_out:
        raise GenericQualificationFailure(
            QualificationReason.IMAGE_INSPECT_TIMEOUT,
            "qualification image inspect timed out")
    if result.output_limited:
        raise GenericQualificationFailure(
            QualificationReason.IMAGE_INSPECT_OUTPUT_LIMIT,
            "qualification image inspect exceeded its output bound")
    if result.returncode != 0:
        text = result.stderr.data.decode("utf-8", errors="replace").lower()
        reason = (QualificationReason.IMAGE_REFERENCE_AMBIGUOUS
                  if "ambiguous" in text or "multiple" in text
                  else QualificationReason.IMAGE_NOT_LOCAL
                  if "no such image" in text or "not found" in text
                  else QualificationReason.IMAGE_INSPECT_FAILED)
        raise GenericQualificationFailure(reason, "qualification image is unavailable")
    try:
        image_id, raw_os, raw_arch, volumes = admission.decode_image_document(
            admission.json_object(result.stdout.data))
        image = QualificationImage(
            reference, image_id, raw_os, spike_plan.canonical_os(raw_os), raw_arch,
            spike_plan.canonical_architecture(raw_arch), volumes)
    except GenericQualificationFailure:
        raise
    except Exception as exc:
        raise GenericQualificationFailure(
            QualificationReason.IMAGE_MALFORMED, "qualification image projection is malformed") from exc
    if image.declared_volumes:
        raise GenericQualificationFailure(
            QualificationReason.IMAGE_DECLARES_VOLUMES, "qualification image declares volumes")
    if image.os != "linux" or image.os != checked["server_os"] \
            or image.architecture != checked["server_architecture"]:
        raise GenericQualificationFailure(
            QualificationReason.IMAGE_PLATFORM_MISMATCH, "qualification image is not native Linux")
    return image


def _snapshot(content: bytes | None) -> application.GenericExecutionSnapshot:
    if content is None:
        files: tuple[FileBinding, ...] = ()
        contents: tuple[bytes, ...] = ()
    else:
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        files = (FileBinding("probe.txt", digest),)
        contents = (content,)
    basis = canonical_digest([(item.path, item.sha256) for item in files])
    snapshot_digest = canonical_digest({
        "basis_id": basis,
        "files": [(item.path, item.sha256, len(data)) for item, data in zip(files, contents)],
    })
    return application.GenericExecutionSnapshot(basis, files, contents, snapshot_digest)


def _sealed_plan(
    image: QualificationImage, runtime: Mapping[str, Any], profile: str,
    name: str, argv: tuple[str, ...], snapshot: application.GenericExecutionSnapshot,
) -> spike_plan.SealedSpikePlan:
    captured = tuple(spike_plan.CapturedFile(binding.path, binding.sha256, len(content))
                     for binding, content in zip(snapshot.file_bindings, snapshot.captured_bytes))
    return spike_plan.SealedSpikePlan(
        canonical_digest({"qualification_probe": name, "argv": list(argv)}),
        spike_plan.ResolvedImage(
            image.reference, image.image_id, image.raw_os, image.os,
            image.raw_architecture, image.architecture, ()),
        argv, ("probe.txt",) if captured else (), captured,
        canonical_digest([item.to_data() for item in captured]), (), runtime["ncpu"],
        min(2, runtime["ncpu"]), profile, spike_plan.FRAMEWORK_SPIKE_POLICY.digest)


class _ObservedEffects:
    def __init__(self, delegate: execution.GenericEffects) -> None:
        self.delegate = delegate
        self.last_attach: admission.RawAttachObservation | None = None

    def run(self, *args, **kwargs):
        return self.delegate.run(*args, **kwargs)

    def attach(self, *args, **kwargs):
        self.last_attach = self.delegate.attach(*args, **kwargs)
        return self.last_attach

    def close(self) -> None:
        self.delegate.close()


class ProductionProbeKernel:
    """Five probes through the production compiler, staging, journal, effects and admission path."""
    def __init__(
        self,
        store: common.ExecutionStore,
        runtime: Mapping[str, Any],
        profile: str,
        image: QualificationImage,
        implementation_digest: str,
        *,
        effects_factory: Callable[[planner.ResolvedDockerOperation, str], execution.GenericEffects],
        clock: Callable[[], float] = time.monotonic,
        operation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.runtime = authority.validate_runtime(dict(runtime))
        self.profile = profile
        self.image = image
        self.clock = clock
        self.effects_factory = effects_factory
        self.operation_id_factory = operation_id_factory or (
            lambda: "o-" + secrets.token_hex(16))
        seed = canonical_digest({
            "schema": CANDIDATE_SCHEMA, "profile": profile,
            "authority": authority.authority_facts(self.runtime),
            "policy": spike_plan.FRAMEWORK_SPIKE_POLICY.digest,
            "implementation": implementation_digest, "image": image.to_data(),
        })
        self.backend = application.GenericQualifiedBackend("docker", profile, seed, seed)
        self._container_ids_absent = True
        self._network_ids_absent = True

    @staticmethod
    def _argv(name: str, profile: str) -> tuple[str, ...]:
        if name == "realization":
            script = (
                "set -eu; if test \"$(id -u)\" != 65534; then echo uid65534-unusable; exit 45; fi; "
                "for x in /bin/sh id head sleep cat wget touch chmod printf command test; "
                "do command -v \"$x\" >/dev/null || "
                "{ echo missing:$x; exit 42; }; done; "
                "if ! test -r /inputs/probe.txt || ! test -x /bin/sh; "
                "then echo uid65534-unusable; exit 45; fi; "
                "test \"$(cat /inputs/probe.txt)\" = empirica-qualification-v2; "
                "if touch /usr/empirica-write-test 2>/dev/null; then exit 43; fi; "
                "printf '#!/bin/sh\\nexit 0\\n' >/work/x; chmod +x /work/x; /work/x; "
                ": >/tmp/x; echo qualified"
            )
        elif name == "natural-125":
            script = "exit 125"
        elif name == "output-overflow":
            script = "head -c 1048577 /dev/zero"
        elif name == "timeout":
            script = "sleep 30"
        elif profile == spike_plan.ISOLATED_PROFILE:
            script = ("if wget -q -T 3 -O /dev/null http://example.com/; "
                      "then exit 44; else exit 0; fi")
        else:
            script = "wget -q -T 5 -O /dev/null http://example.com/ || exit 46; sleep 1"
        return ("/bin/sh", "-c", script)

    def _running_network_probe(self, operation: planner.ResolvedDockerOperation,
                               observed: dict[str, bool]):
        def probe(effects: execution.GenericEffects, row: generic_journal.GenericJournal,
                  staging: Path, deadline: float, attach_complete: Callable[[], bool]) -> None:
            if operation.network is None or row.container_id is None or row.network_id is None:
                raise execution.GenericExecutionUnavailable("qualification network identity absent")
            while self.clock() < deadline and not attach_complete():
                argv = ("container", "inspect", "--format", planner.CONTAINER_INSPECT_FORMAT,
                        row.container_id)
                _, document = execution._json_control(effects, argv, deadline, self.clock)
                admission.admit_static_container(
                    operation, document, container_full_id=row.container_id,
                    network_full_id=row.network_id, staging_path=staging)
                state = document.get("State")
                if type(state) is dict and state.get("Running") is True \
                        and state.get("Status") == "running":
                    network_argv = ("network", "inspect", "--format",
                                    planner.NETWORK_INSPECT_FORMAT, row.network_id)
                    _, network = execution._json_control(
                        effects, network_argv, deadline, self.clock)
                    admission.admit_network_membership(
                        operation, network, network_full_id=row.network_id,
                        container_full_id=row.container_id)
                    observed["running"] = True
                    return
                time.sleep(min(0.02, max(0.0, deadline - self.clock())))
            raise execution.GenericExecutionUnavailable(
                "qualification attach ended or deadline expired before running membership was observed")
        return probe

    def run(self, name: str, expected: str, *, deadline: float) -> ProbeObservation:
        if name not in PROBE_ORDER or expected != PROBE_EXPECTED[PROBE_ORDER.index(name)]:
            raise GenericQualificationFailure(
                QualificationReason.INVALID_INPUT, "qualification probe selection is unknown")
        began = self.clock()
        content = b"empirica-qualification-v2" if name == "realization" else None
        snapshot = _snapshot(content)
        argv = self._argv(name, self.profile)
        plan = _sealed_plan(self.image, self.runtime, self.profile, name, argv, snapshot)
        request = spike_plan.SealedSpikeRequest(plan, spike_plan.FRAMEWORK_SPIKE_POLICY)
        operation_id = self.operation_id_factory()
        operation = planner.compile_docker_operation(
            request, self.backend, "qualification-" + name.replace("-", ""), operation_id,
            "stg-" + canonical_digest({"operation": operation_id})[7:39])
        delegate = self.effects_factory(operation, name)
        effects = _ObservedEffects(delegate)
        running = {"running": False}
        timeout = (QUALIFICATION_TIMEOUT_SECONDS if name == "timeout"
                   else spike_plan.WORKLOAD_TIMEOUT_SECONDS)
        callback = (self._running_network_probe(operation, running)
                    if name == "network-profile" and operation.network is not None else None)
        controls = execution.QualificationExecutionControls(timeout, 10.0, callback)
        result: execution.GenericExecutionResult | None = None
        caught: execution.GenericExecutionUnavailable | None = None
        try:
            result = execution.execute_generic_operation(
                self.store, operation, snapshot, effects, backend_deadline=deadline,
                postflight=lambda limit: self.backend.execution_binding_digest,
                authority=authority.authority_facts(self.runtime),
                clock=self.clock, qualification_controls=controls)
        except execution.GenericExecutionUnavailable as exc:
            caught = exc
        finally:
            effects.close()
        attach = effects.last_attach
        observed = None
        if name in {"realization", "network-profile"}:
            if result is not None and result.verdict is application.GenericVerdict.PASS:
                observed = "PASS"
        elif name == "natural-125":
            if (result is not None and result.verdict is application.GenericVerdict.FAIL
                    and result.exit_code == 125 and attach is not None
                    and attach.natural_exit is True and attach.process.returncode == 125):
                observed = "FAIL"
        elif name == "output-overflow":
            if (caught is not None and attach is not None
                    and attach.process.output_limited is True
                    and attach.process.stdout.bytes_seen > spike_plan.STREAM_BYTES):
                observed = "UNAVAILABLE_OUTPUT_LIMIT"
        elif name == "timeout":
            if caught is not None and attach is not None and attach.process.timed_out is True:
                observed = "UNAVAILABLE_TIMEOUT"
        if caught is not None:
            try:
                residue = (bool(generic_journal.GenericJournalStore(self.store).scan())
                           or bool(tuple(self.store.staging.iterdir())))
            except Exception as exc:
                raise GenericQualificationFailure(
                    QualificationReason.CLEANUP_UNAVAILABLE,
                    "qualification cleanup state is unreadable") from exc
            if residue:
                raise GenericQualificationFailure(
                    QualificationReason.CLEANUP_UNAVAILABLE,
                    "qualification probe cleanup is incomplete after: " + str(caught))
        if observed != expected:
            # A fast egress failure can exit before running membership is observed, leaving no
            # result; the natural attach exit still identifies it.
            if (result is not None and result.exit_code == 46) or (
                    name == "network-profile" and attach is not None
                    and attach.natural_exit is True and attach.process.returncode == 46):
                raise GenericQualificationFailure(
                    QualificationReason.NETWORK_EGRESS_UNAVAILABLE,
                    "networked Docker profile could not prove outbound HTTP egress")
            if attach is not None:
                text = attach.process.stdout.data.decode("utf-8", errors="replace")
                error_text = attach.process.stderr.data.decode("utf-8", errors="replace").lower()
                if (name == "realization"
                        and ("executable file not found" in error_text
                             or "no such file or directory" in error_text)):
                    raise GenericQualificationFailure(
                        QualificationReason.IMAGE_TOOL_MISSING,
                        "qualification image is missing required tool /bin/sh")
                if "uid65534-unusable" in text:
                    raise GenericQualificationFailure(
                        QualificationReason.IMAGE_UID_UNUSABLE,
                        "qualification image is not readable and executable as uid 65534")
                if "missing:" in text:
                    tool = text.split("missing:", 1)[1].split()[0][:64]
                    raise GenericQualificationFailure(
                        QualificationReason.IMAGE_TOOL_MISSING,
                        "qualification image is missing required tool " + tool)
            raise GenericQualificationFailure(
                QualificationReason.PROBE_FAILED, "qualification probe did not prove " + expected)
        self._assert_empty()
        duration = int((self.clock() - began) * 1000)
        return ProbeObservation(
            name, expected, observed, operation.digest, True, duration,
            running["running"], None if result is None else result.realization_digest)

    def _assert_empty(self) -> None:
        try:
            journals = generic_journal.GenericJournalStore(self.store).scan()
            staging = tuple(self.store.staging.iterdir())
        except Exception as exc:
            raise GenericQualificationFailure(
                QualificationReason.CLEANUP_UNAVAILABLE,
                "qualification cleanup state is unreadable") from exc
        if journals:
            raise GenericQualificationFailure(
                QualificationReason.CLEANUP_UNAVAILABLE, "qualification journals are not empty")
        if staging:
            raise GenericQualificationFailure(
                QualificationReason.CLEANUP_UNAVAILABLE, "qualification staging is not empty")

    def cleanup_proof(self) -> dict[str, bool]:
        self._assert_empty()
        return {"container_ids_absent": self._container_ids_absent,
                "network_ids_absent": self._network_ids_absent,
                "staging_empty": True, "journals_empty": True}


def _safe_output(path: Path) -> Path:
    if not path.is_absolute() or path.resolve(strict=False) != path or ".." in path.parts:
        raise GenericQualificationFailure(
            QualificationReason.CANDIDATE_UNSAFE, "candidate output must be canonical and absolute")
    common.safe_directory(path.parent, create=False)
    try:
        path.lstat()
    except FileNotFoundError:
        return path
    raise GenericQualificationFailure(
        QualificationReason.CANDIDATE_UNSAFE, "candidate output already exists")


def _write_candidate(path: Path, record: Mapping[str, Any]) -> None:
    selected = _safe_output(path)
    payload = fsio.dump_json(dict(record))
    if len(payload) > MAX_CANDIDATE_BYTES:
        raise GenericQualificationFailure(
            QualificationReason.CANDIDATE_UNSAFE, "candidate exceeds its byte bound")
    fsio.atomic_write(selected, payload)
    os.chmod(selected, 0o600)
    if common.safe_file(selected, cap=MAX_CANDIDATE_BYTES) != payload:
        raise GenericQualificationFailure(
            QualificationReason.CANDIDATE_UNSAFE, "candidate publication differs")


def qualify_to_candidate(
    *,
    profile_id: str,
    image_reference: str,
    docker_cli: Path,
    output: Path,
    work_root: Path,
    runtime_inspector: Callable[..., authority.GenericRuntimeInspection] = authority.inspect_current_runtime,
    image_runner: process.Runner | None = None,
    effects_factory: Callable[[authority.ResolvedGenericAuthority], execution.GenericEffects] | None = None,
    implementation_inspector: Callable[[], Mapping[str, Any]] = common.implementation_manifest,
    kernel_factory: Callable[..., QualificationProbeKernel] = ProductionProbeKernel,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if not isinstance(profile_id, str) or profile_id not in spike_plan.NETWORK_PROFILES:
        raise GenericQualificationFailure(
            QualificationReason.INVALID_INPUT, "qualification profile is unknown")
    began = clock()
    deadline = began + TOTAL_QUALIFICATION_SECONDS
    root = common.canonical_authority_path(Path(work_root))
    store = common.ExecutionStore(root)
    store.prepare_execution_scaffolding()
    generic_journal.GenericJournalStore(store).prepare()
    config = root / "qualification-docker-config"
    work = root / "qualification-work"
    common.safe_directory(config, create=True)
    common.safe_directory(work, create=True)
    try:
        if tuple(config.iterdir()):
            raise GenericQualificationFailure(
                QualificationReason.CAPABILITY_UNAVAILABLE,
                "qualification Docker config contains foreign files; empty the private "
                "qualification config directory before retrying")
    except OSError as exc:
        raise GenericQualificationFailure(
            QualificationReason.CAPABILITY_UNAVAILABLE,
            "qualification Docker config is unreadable") from exc
    try:
        observed = runtime_inspector(
            {"docker_cli": str(Path(docker_cli))}, deadline=deadline)
        if type(observed) is not authority.GenericRuntimeInspection:
            raise GenericQualificationFailure(
                QualificationReason.CAPABILITY_UNAVAILABLE, "runtime inspector returned unknown facts")
        runtime = authority.validate_runtime(observed.runtime)
    except GenericQualificationFailure:
        raise
    except authority.GenericQualificationError as exc:
        detail = str(exc)
        reason = (QualificationReason.RESOURCE_LIMITS_UNAVAILABLE
                  if detail.startswith("resource-limits-unavailable:")
                  else QualificationReason.CAPABILITY_UNAVAILABLE)
        raise GenericQualificationFailure(reason, detail) from exc
    except Exception as exc:
        raise GenericQualificationFailure(
            QualificationReason.CAPABILITY_UNAVAILABLE,
            "Docker capability qualification failed: " + str(exc)) from exc
    runner = process.BoundedRunner() if image_runner is None else image_runner
    image = inspect_qualification_image(
        image_reference, runtime, config=config, runner=runner, deadline=deadline, clock=clock)
    manifest = implementation_inspector()
    if (not isinstance(manifest, Mapping)
            or type(manifest.get("implementation_digest")) is not str):
        raise GenericQualificationFailure(
            QualificationReason.CAPABILITY_UNAVAILABLE, "implementation manifest is unavailable")
    implementation_digest = _strict_digest(
        manifest["implementation_digest"], "implementation digest")
    seed_backend = application.GenericQualifiedBackend(
        "docker", profile_id, canonical_digest({"candidate": profile_id, "image": image.image_id}),
        canonical_digest({"execution": profile_id, "image": image.image_id}))
    resolved = authority.ResolvedGenericAuthority(
        seed_backend, Path(runtime["docker_cli"]), tuple(sorted({
            "LANG": "C", "LC_ALL": "C", "DOCKER_HOST": runtime["endpoint"],
            "DOCKER_CONFIG": str(config),
        }.items())), work, runtime["server_os"], runtime["server_architecture"],
        runtime["ncpu"], runtime["mem_total"], runtime)
    selected_effects = (effects_factory if effects_factory is not None
                        else lambda current: execution.concrete_effects(
                            current.docker_cli, dict(current.environment), current.work_directory))
    kernel = kernel_factory(
        store, runtime, profile_id, image, implementation_digest,
        effects_factory=lambda operation, name: selected_effects(resolved), clock=clock)
    probes = []
    for name, expected in zip(PROBE_ORDER, PROBE_EXPECTED):
        if clock() >= deadline:
            raise GenericQualificationFailure(
                QualificationReason.DEADLINE_EXCEEDED, "qualification exceeded sixty seconds")
        probes.append(kernel.run(name, expected, deadline=deadline))
    cleanup = kernel.cleanup_proof()
    elapsed_ms = int((clock() - began) * 1000)
    record = build_candidate_record(
        profile_id, runtime, implementation_digest, image, tuple(probes), cleanup, elapsed_ms)
    _write_candidate(Path(output), record)
    return record


class GenericQualificationInstaller:
    """Explicit candidate validator and atomic `/2` record/pointer publisher."""
    def __init__(
        self,
        sandbox_root: Path,
        *,
        runtime_inspector: Callable[..., authority.GenericRuntimeInspection] = authority.inspect_current_runtime,
        implementation_inspector: Callable[[], Mapping[str, Any]] = common.implementation_manifest,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = common.canonical_authority_path(Path(sandbox_root))
        self.runtime_inspector = runtime_inspector
        self.implementation_inspector = implementation_inspector
        self.clock = clock

    def install(self, candidate: Path, *, profile_id: str) -> dict[str, Any]:
        try:
            selected_candidate = common.canonical_authority_path(Path(candidate))
            raw = common.safe_file(selected_candidate, cap=MAX_CANDIDATE_BYTES)
            record = validate_candidate_record(json.loads(raw.decode("utf-8")))
        except GenericQualificationFailure:
            raise
        except Exception as exc:
            raise GenericQualificationFailure(
                QualificationReason.INSTALL_VALIDATION_FAILED, "candidate cannot be read safely") from exc
        if record["profile_id"] != profile_id:
            raise GenericQualificationFailure(
                QualificationReason.INSTALL_VALIDATION_FAILED, "candidate profile differs")
        deadline = self.clock() + 15.0
        try:
            current = self.runtime_inspector(record["runtime"], deadline=deadline)
            if (type(current) is not authority.GenericRuntimeInspection
                    or authority.authority_facts(current.runtime) != record["semantic_authority"]):
                raise ValueError("semantic capability drift")
            manifest = self.implementation_inspector()
            if manifest.get("implementation_digest") != record["implementation_digest"]:
                raise ValueError("implementation drift")
        except Exception as exc:
            raise GenericQualificationFailure(
                QualificationReason.INSTALL_VALIDATION_FAILED,
                "candidate no longer matches current implementation or capability") from exc
        namespace = self.root / NAMESPACE
        records = namespace / "records"
        pointers = namespace / "pointers"
        configs = self.root / "docker-spike-config-v2"
        config = configs / profile_id
        work = self.root / "docker-spike-work-v2"
        for directory in (self.root, namespace, records, pointers, configs, config, work):
            common.safe_directory(directory, create=True)
        try:
            if tuple(config.iterdir()):
                raise GenericQualificationFailure(
                    QualificationReason.INSTALL_VALIDATION_FAILED,
                    "installed Docker config contains foreign files; empty the selected private "
                    "profile config directory and reinstall the reviewed candidate")
        except OSError as exc:
            raise GenericQualificationFailure(
                QualificationReason.INSTALL_VALIDATION_FAILED,
                "installed Docker config authority is unreadable") from exc
        execution_store = common.ExecutionStore(self.root)
        execution_store.prepare_execution_scaffolding()
        generic_journal.GenericJournalStore(execution_store).prepare()
        digest = record["qualification_record_digest"].removeprefix(_FULL_DIGEST)
        record_path = records / (digest + ".json")
        payload = fsio.dump_json(record)
        try:
            record_path.lstat()
        except FileNotFoundError:
            existing = None
        else:
            try:
                existing = common.safe_file(record_path, cap=MAX_CANDIDATE_BYTES)
            except common.GenericScaffoldingError as exc:
                raise GenericQualificationFailure(
                    QualificationReason.INSTALL_VALIDATION_FAILED,
                    "existing record path is unsafe") from exc
        if existing is None:
            fsio.atomic_write(record_path, payload)
            os.chmod(record_path, 0o600)
        elif existing != payload:
            raise GenericQualificationFailure(
                QualificationReason.INSTALL_VALIDATION_FAILED, "record path contains different bytes")
        pointer = {
            "schema": POINTER_SCHEMA, "profile_id": profile_id,
            "qualification_record_digest": record["qualification_record_digest"],
            "execution_binding_digest": record["execution_binding_digest"],
        }
        pointer_path = pointers / (profile_id + ".json")
        fsio.atomic_write(pointer_path, fsio.dump_json(pointer))
        os.chmod(pointer_path, 0o600)
        loaded = authority.GenericQualificationStore(self.root).load_current(profile_id)
        if loaded != record:
            raise GenericQualificationFailure(
                QualificationReason.INSTALL_VALIDATION_FAILED, "installed qualification differs")
        return record
