"""Run-scoped environment qualification owner: the `/3` record write and its closed failures.

Sandbox adapter only (spec ``empirica-7.2-run-scoped-environment-qualification`` sections
4.3, 4.5 and 5): nothing here changes the core, application, wire, or host surface; E9
composes the owner through the qualification port and performs the run's binding
compare-and-set after the returned record exists.

One entry point, :func:`qualify_run_environment`, takes the run binding
(``project_id``, ``run_id``, ``generation``, ``attempt_id``, ``revision``,
``proposal_digest``), the approved spike-image references, the profile, the Docker CLI,
and one injected qualification budget. It always writes the content-addressed ``/3``
record durably under ``$EMPIRICA_SANDBOX_HOME/docker-spike-qualification-v3/records/``
**before** returning ``bound(record_digest, record)`` (write-first: the caller's CAS,
never this node, installs the binding), or it returns one closed failure from the spec's
reason table plus the kept deadline, egress and cleanup reasons — never collapsed —
carrying the probe name only for ``qualification-probe-failed``. A failed or timed-out
attempt binds nothing and leaves recoverable journals for the next attempt. The ``/2``
record and pointer namespaces are never read, and no pointer is written.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from adapters.state import fsio
from application import generic_spike as application
from core import spike_plan
from core.canonical import canonical_digest
from core.freshness import validate_digest256

from . import (common, generic_admission as admission, generic_authority as authority,
               generic_execution as execution, generic_journal,
               generic_qualification as qualification, planner, probe_image, process)

#: The record schema this owner writes: the closed shape's definition home is
#: ``contracts/empirica-docker-spike/v1/qualification-record.schema.json`` (vendored at
#: ``vendor/contracts/...``), validated here by :func:`validate_run_record` and
#: registered with the contract conformance harness at the restack, through that
#: harness's own discovery and annotation conventions.
QUALIFICATION_RECORD_SCHEMA = "siffran.empirica.docker-spike-qualification-record/3"
NAMESPACE = "docker-spike-qualification-v3"
MAX_RECORD_BYTES = 2 * 1024 * 1024
#: One bound over lock wait, recovery, up to 16 inspections, five probes with their
#: re-observations, final re-observation and publication — set from the operator timing
#: session's measured evidence (spec section 5, "Timing evidence": worst attempt 18.5 s
#: on Lima and Docker Desktop, both profiles); the session may re-measure and move it.
#: Tests and the harness must read this constant, never a literal. A caller may inject a
#: smaller bound, never a larger one.
DEFAULT_QUALIFICATION_SECONDS = 90.0
#: The environment-proposal bound (spec section 4.1): 1..16 local references. The E1
#: model and the E2c harness own the definition once composed; this constant is the
#: sandbox-side ceiling the owner enforces today.
MAX_SPIKE_IMAGE_REFERENCES = 16
#: Provisional closed value: bounded printable identifiers without separators, kept
#: local until the wire contract (E6a) and the conformance harness (E2c) own them.
MAX_IDENTIFIER_CHARS = 128
_IDENTIFIER = re.compile(r"^[^\x00-\x20\x2f\x5c\x7f-\x9f][^\x00-\x20\x2f\x5c\x7f-\x9f]{0,127}$")
_REPO_DIGEST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,253}@sha256:[0-9a-f]{64}$")
_MAJOR_VERSION = re.compile(r"^[0-9]+$")
#: `docker image inspect` of the pinned probe image: admission (spec section 4.3) keys
#: on a ``RepoDigests`` entry carrying the pinned index digest under any repository
#: name (the digest is the image's content identity, the repository name is not),
#: never on the daemon-local ``Id``; the record stores both.
PROBE_IMAGE_INSPECT_FORMAT = (
    '{"Id":{{json .Id}},"Os":{{json .Os}},"Architecture":{{json .Architecture}},'
    '"RepoDigests":{{json .RepoDigests}},"Volumes":{{json (index .Config "Volumes")}}}'
)
_RECORD_BODY_FIELDS = frozenset({
    "schema", "run_binding", "profile_id", "runtime", "semantic_authority", "authority_digest",
    "policy_digest", "implementation_digest", "daemon_id?", "probe_image_id",
    "qualification_image", "spike_images", "probes", "cleanup",
    "qualification_elapsed_ms", "execution_binding_digest",
})
#: The sealed spike-image row shape (spec section 4.5): the requested reference, the
#: full daemon image ID observed at qualification (a diagnostic), the canonical
#: platform, and the volume facts — no capability list, because spike images are
#: inspected, never probed; the shared qualification-image helper keeps its own list
#: for the trusted probe image. (The closed shape itself is the contract conformance
#: harness's at the restack.)
SPIKE_IMAGE_FIELDS = frozenset({
    "reference", "id", "raw_os", "os", "raw_architecture", "architecture",
    "declared_volumes",
})
_CLEANUP_PROOF = {"container_ids_absent": True, "network_ids_absent": True,
                  "staging_empty": True, "journals_empty": True}


def server_version_major(value: object) -> str:
    """The Docker server version's major part: the only piece bound across the binding
    (operator decision, drift "Server version"). A patch or minor daemon update keeps
    a bound qualification; a major change is a stale daemon. The full version string
    stays a diagnostic in the record's runtime facts; within one attempt the exact
    version must still match (the after-probe re-observation), which this derivation
    never relaxes. A version with no numeric major part — a private build, a garbage
    answer — can seal no binding and fails closed."""
    major = value.partition(".")[0] if type(value) is str else None
    if major is None or _MAJOR_VERSION.fullmatch(major) is None:
        raise authority.GenericQualificationError(
            "Docker server major version is unavailable: " + repr(value))
    return major


class RunQualificationReason(str, Enum):
    """The closed failure values this owner can return: spec section 5.1 where named,
    plus the kept deadline, egress, cleanup, image-resolution and capability values.
    Additive only; existing values are never collapsed or rewritten."""
    INVALID_INPUT = "invalid-input"
    PROBE_IMAGE_UNTRUSTED = "probe-image-untrusted"
    DOCKER_UNREACHABLE = "docker-unreachable"
    RESOURCE_LIMITS_UNAVAILABLE = "resource-limits-unavailable"
    CAPABILITY_UNAVAILABLE = "capability-unavailable"
    SANDBOX_UNSAFE = "sandbox-scaffolding-unsafe"
    STALE_DAEMON = "qualification-stale-daemon"
    STALE_IMPLEMENTATION = "qualification-stale-implementation"
    PROBE_FAILED = "qualification-probe-failed"
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
    EGRESS_UNAVAILABLE = "network-egress-unavailable"
    CLEANUP_UNAVAILABLE = "cleanup-unavailable"
    DEADLINE_EXCEEDED = "qualification-deadline-exceeded"

# Kept `/2` values this owner forwards unchanged to its outcome.
_MAPPED_QUALIFICATION_REASONS = {
    qualification.QualificationReason.INVALID_INPUT: RunQualificationReason.INVALID_INPUT,
    qualification.QualificationReason.IMAGE_NOT_LOCAL: RunQualificationReason.IMAGE_NOT_LOCAL,
    qualification.QualificationReason.IMAGE_REFERENCE_AMBIGUOUS:
        RunQualificationReason.IMAGE_REFERENCE_AMBIGUOUS,
    qualification.QualificationReason.IMAGE_MALFORMED: RunQualificationReason.IMAGE_MALFORMED,
    qualification.QualificationReason.IMAGE_INSPECT_FAILED:
        RunQualificationReason.IMAGE_INSPECT_FAILED,
    qualification.QualificationReason.IMAGE_INSPECT_TIMEOUT:
        RunQualificationReason.IMAGE_INSPECT_TIMEOUT,
    qualification.QualificationReason.IMAGE_INSPECT_OUTPUT_LIMIT:
        RunQualificationReason.IMAGE_INSPECT_OUTPUT_LIMIT,
    qualification.QualificationReason.IMAGE_PLATFORM_MISMATCH:
        RunQualificationReason.IMAGE_PLATFORM_MISMATCH,
    qualification.QualificationReason.IMAGE_DECLARES_VOLUMES:
        RunQualificationReason.IMAGE_DECLARES_VOLUMES,
    qualification.QualificationReason.IMAGE_TOOL_MISSING:
        RunQualificationReason.IMAGE_TOOL_MISSING,
    qualification.QualificationReason.IMAGE_UID_UNUSABLE:
        RunQualificationReason.IMAGE_UID_UNUSABLE,
    qualification.QualificationReason.CLEANUP_UNAVAILABLE:
        RunQualificationReason.CLEANUP_UNAVAILABLE,
    qualification.QualificationReason.NETWORK_EGRESS_UNAVAILABLE:
        RunQualificationReason.EGRESS_UNAVAILABLE,
    qualification.QualificationReason.DEADLINE_EXCEEDED:
        RunQualificationReason.DEADLINE_EXCEEDED,
    qualification.QualificationReason.CAPABILITY_UNAVAILABLE:
        RunQualificationReason.CAPABILITY_UNAVAILABLE,
    qualification.QualificationReason.RESOURCE_LIMITS_UNAVAILABLE:
        RunQualificationReason.RESOURCE_LIMITS_UNAVAILABLE,
}


class _RunFailure(RuntimeError):
    """Internal stage-local carrier: the boundary converts it (and the kept `/2`
    failures) into one closed :class:`RunQualificationFailed` outcome. The closed probe
    invariant is enforced here already — a probe-failure reason carries exactly one probe
    name and no other reason ever does — so the boundary can never construct the invalid
    combination."""

    def __init__(self, reason: RunQualificationReason, detail: str,
                 probe: str | None = None) -> None:
        super().__init__(detail)
        if (probe is None) is (reason is RunQualificationReason.PROBE_FAILED) \
                or (probe is not None and probe not in qualification.PROBE_ORDER):
            raise ValueError("run qualification probe identity does not match its reason")
        self.reason = reason
        self.detail = detail
        self.probe = probe


@dataclass(frozen=True, slots=True)
class RunBinding:
    """The run facts a qualification attempt binds its record to (spec section 4.5)."""
    project_id: str
    run_id: str
    generation: int
    attempt_id: str
    revision: int
    proposal_digest: str

    @staticmethod
    def _identifier(value: object, where: str) -> str:
        if (type(value) is not str or not value or len(value.encode("utf-8")) > MAX_IDENTIFIER_CHARS
                or _IDENTIFIER.fullmatch(value) is None):
            raise ValueError("run qualification identifier is not closed: " + where)
        return value

    def __post_init__(self) -> None:
        self._identifier(self.project_id, "project_id")
        self._identifier(self.run_id, "run_id")
        self._identifier(self.attempt_id, "attempt_id")
        for name in ("generation", "revision"):
            value = getattr(self, name)
            if type(value) is not int or isinstance(value, bool) or not 1 <= value < 2 ** 31:
                raise ValueError("run qualification counter is not closed: " + name)
        try:
            validate_digest256(self.proposal_digest)
        except ValueError as exc:
            raise ValueError("run qualification proposal digest is malformed") from exc

    def to_data(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id, "run_id": self.run_id, "generation": self.generation,
            "attempt_id": self.attempt_id, "revision": self.revision,
            "proposal_digest": self.proposal_digest,
        }


RUN_BINDING_FIELDS = frozenset({
    "project_id", "run_id", "generation", "attempt_id", "revision", "proposal_digest",
})


@dataclass(frozen=True, slots=True)
class RunQualificationBound:
    """Success: the `/3` record is already durably published (write-first); the caller
    performs the binding compare-and-set next."""
    record_digest: str
    record: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RunQualificationFailed:
    """One closed failure; ``probe`` carries the failing probe name exactly when the
    reason is ``qualification-probe-failed`` (required then, forbidden otherwise;
    spec 5.1 — the identity is attached only at its owning boundary)."""
    reason: RunQualificationReason
    detail: str
    probe: str | None = None

    def __post_init__(self) -> None:
        if (self.probe is None) is (self.reason is RunQualificationReason.PROBE_FAILED):
            raise ValueError("run qualification probe identity does not match its reason")
        if (self.probe is not None
                and self.probe not in qualification.PROBE_ORDER):
            raise ValueError("run qualification probe name is not permitted")


RunQualificationOutcome = RunQualificationBound | RunQualificationFailed


def _image_row(value: object, runtime: Mapping[str, Any], where: str) -> dict[str, Any]:
    """Validate one sealed probe-image row through the `/2` admission (native platform,
    no declared volumes, closed capabilities: the trusted probe image is probed, so it
    keeps the shared qualification helper's own list)."""
    fields = SPIKE_IMAGE_FIELDS | {"capabilities"}
    if type(value) is not dict or set(value) != fields:
        raise authority.GenericQualificationError(where + " shape is unknown")
    try:
        return qualification._validate_image(value, runtime)
    except qualification.GenericQualificationFailure as exc:
        raise authority.GenericQualificationError(str(exc)) from exc


def _spike_image_row(value: object, runtime: Mapping[str, Any], where: str) -> dict[str, Any]:
    """Validate one sealed spike-image row (spec section 4.5): the requested reference,
    the full image ID observed at qualification (a diagnostic), the canonical
    server-native platform, and no declared volumes. Spike images are inspected, never
    probed, so the row carries no capability list; the shared qualification-image
    helper keeps its own list for the trusted probe image. Per-spike resolution itself
    is E10's, not this node's."""
    if type(value) is not dict or set(value) != SPIKE_IMAGE_FIELDS:
        raise authority.GenericQualificationError(where + " shape is unknown")
    row = dict(value)
    try:
        image = qualification.QualificationImage(
            row["reference"], row["id"], row["raw_os"], row["os"],
            row["raw_architecture"], row["architecture"], tuple(row["declared_volumes"]))
    except (qualification.GenericQualificationFailure, TypeError, ValueError) as exc:
        raise authority.GenericQualificationError(where + " identity is malformed: " + str(exc)) from exc
    if (image.os != "linux" or image.os != runtime["server_os"]
            or image.architecture != runtime["server_architecture"]):
        raise authority.GenericQualificationError(where + " platform is not server-native Linux")
    if image.declared_volumes:
        raise authority.GenericQualificationError(where + " image declares volumes")
    return {key: row[key] for key in SPIKE_IMAGE_FIELDS}


def _repo_digest(value: object, where: str) -> str:
    if type(value) is not str or _REPO_DIGEST.fullmatch(value) is None:
        raise authority.GenericQualificationError(where + " repo digest is malformed")
    return value


def _elapsed_ms(value: object, where: str) -> int:
    if (type(value) is not int or isinstance(value, bool) or value < 0
            or value >= int(DEFAULT_QUALIFICATION_SECONDS * 1000)):
        raise authority.GenericQualificationError(where + " elapsed time is outside its bound")
    return value


def validate_run_record(value: object) -> dict[str, Any]:
    """Validate the exact closed `/3` record and fail closed on every malformed form.

    Loading additionally verifies the run-generation lineage; the verification itself
    (below) is lineage-free so the record stays a pure function of one attempt.
    """
    if type(value) is not dict:
        raise authority.GenericQualificationError("run qualification record shape is unknown")
    # the optional ``daemon_id`` member completes the closed set
    base = _RECORD_BODY_FIELDS - {"daemon_id?"}
    expected = base | ({"daemon_id"} if "daemon_id" in value else frozenset())
    if (set(value) != expected | {"qualification_record_digest"}
            or value.get("schema") != QUALIFICATION_RECORD_SCHEMA):
        raise authority.GenericQualificationError("run qualification record schema or shape is unknown")
    row = json.loads(json.dumps(value))
    try:
        binding = RunBinding(**row["run_binding"]) if type(row.get("run_binding")) is dict else None
    except (TypeError, ValueError) as exc:
        raise authority.GenericQualificationError("run qualification record binding is unknown") from exc
    if binding is None or row["run_binding"] != binding.to_data():
        raise authority.GenericQualificationError("run qualification record binding is malformed")
    if type(row["profile_id"]) is not str or row["profile_id"] not in spike_plan.NETWORK_PROFILES:
        raise authority.GenericQualificationError("run qualification record profile is unknown")
    runtime = authority.validate_runtime(row["runtime"])
    semantic = authority.authority_facts(runtime)
    if row["semantic_authority"] != semantic or row["authority_digest"] != canonical_digest(semantic):
        raise authority.GenericQualificationError("run qualification record semantic authority differs")
    if row["policy_digest"] != spike_plan.FRAMEWORK_SPIKE_POLICY.digest:
        raise authority.GenericQualificationError("run qualification record framework policy differs")
    if not validate_digest256(row["implementation_digest"]):
        raise authority.GenericQualificationError("run qualification record implementation digest differs")
    if ("daemon_id" in row) != ("daemon_id" in runtime) or (
            "daemon_id" in row and row["daemon_id"] != runtime["daemon_id"]):
        raise authority.GenericQualificationError("run qualification record daemon identity differs")
    image_body = row["qualification_image"]
    if type(image_body) is not dict or "repo_digest" not in image_body:
        raise authority.GenericQualificationError("run qualification record probe image digest proof is absent")
    probe_digest_proof = _repo_digest(image_body["repo_digest"], "probe image")
    probe_image_row = _image_row(
        {key: item for key, item in image_body.items() if key != "repo_digest"},
        runtime, "probe image")
    if type(probe_image_row.get("id")) is not str or probe_image_row["id"] != row["probe_image_id"]:
        raise authority.GenericQualificationError("run qualification record probe image identity disagrees")
    if not validate_digest256(row["probe_image_id"]):
        raise authority.GenericQualificationError("run qualification record probe image identity differs")
    probe_image_row["repo_digest"] = probe_digest_proof
    if type(row["spike_images"]) is not list or not 1 <= len(row["spike_images"]) \
            <= MAX_SPIKE_IMAGE_REFERENCES:
        raise authority.GenericQualificationError("run qualification record spike images differ")
    row["spike_images"] = [_spike_image_row(item, runtime, "spike image")
                           for item in row["spike_images"]]
    if type(row["probes"]) is not list or len(row["probes"]) != len(qualification.PROBE_ORDER):
        raise authority.GenericQualificationError("run qualification record must contain five probes")
    try:
        row["probes"] = [qualification.validate_probe(probe, index)
                         for index, probe in enumerate(row["probes"])]
    except qualification.GenericQualificationFailure as exc:
        raise authority.GenericQualificationError(str(exc)) from exc
    expected_running = row["profile_id"] == spike_plan.NETWORKED_PROFILE
    if (row["probes"][-1]["running_network_membership"] is not expected_running
            or any(probe["running_network_membership"] for probe in row["probes"][:-1])):
        raise authority.GenericQualificationError("run qualification record network membership proof differs")
    if row["cleanup"] != _CLEANUP_PROOF:
        raise authority.GenericQualificationError("run qualification record cleanup proof differs")
    row["qualification_elapsed_ms"] = _elapsed_ms(
        row["qualification_elapsed_ms"], "run qualification record")
    binding_body = {
        "schema": row["schema"], "run_binding": row["run_binding"],
        "profile_id": row["profile_id"], "authority_digest": row["authority_digest"],
        "policy_digest": row["policy_digest"], "implementation_digest": row["implementation_digest"],
        # across the binding only the server's major version is bound (operator
        # decision, drift "Server version"); the full version stays a diagnostic in
        # the runtime facts and must still match exactly within one attempt
        "server_major": server_version_major(runtime["server_version"]),
        "daemon_id": row.get("daemon_id"), "probe_image_id": row["probe_image_id"],
        "qualification_image_digest": canonical_digest(probe_image_row),
        "spike_images_digest": canonical_digest(row["spike_images"]),
        "probe_digest": canonical_digest(row["probes"]), "cleanup": _CLEANUP_PROOF,
    }
    if row["execution_binding_digest"] != canonical_digest(binding_body):
        raise authority.GenericQualificationError("run qualification record execution binding differs")
    body = {key: row[key] for key in expected}
    if row["qualification_record_digest"] != canonical_digest(body):
        raise authority.GenericQualificationError("run qualification record digest differs")
    row["runtime"] = runtime
    return row


def build_run_record(
    binding: RunBinding,
    profile_id: str,
    runtime: Mapping[str, Any],
    implementation_digest: str,
    probe_image_row: Mapping[str, Any],
    spike_rows: Sequence[Mapping[str, Any]],
    probes: Sequence[qualification.ProbeObservation],
    elapsed_ms: int,
) -> dict[str, Any]:
    checked = authority.validate_runtime(dict(runtime))
    semantic = authority.authority_facts(checked)
    probes_data = [probe.to_data() for probe in probes]
    spike_data = []
    for item in spike_rows:
        # the builder seals spike rows without a capability list: the sealed rows the
        # shared qualification-image helper produces (which carry its own list for the
        # probe image) are reduced to the spike-row fields here, and a caller's row is
        # admitted only when those fields are present
        try:
            spike_data.append({key: item[key] for key in SPIKE_IMAGE_FIELDS})
        except (TypeError, KeyError) as exc:
            raise authority.GenericQualificationError(
                "run qualification spike image row cannot be sealed") from exc
    body: dict[str, Any] = {
        "schema": QUALIFICATION_RECORD_SCHEMA, "run_binding": binding.to_data(),
        "profile_id": profile_id, "runtime": checked,
        "semantic_authority": semantic, "authority_digest": canonical_digest(semantic),
        "policy_digest": spike_plan.FRAMEWORK_SPIKE_POLICY.digest,
        "implementation_digest": implementation_digest,
        "probe_image_id": probe_image_row["id"], "qualification_image": dict(probe_image_row),
        "spike_images": spike_data,
        "probes": probes_data,
        "cleanup": dict(_CLEANUP_PROOF),
        "qualification_elapsed_ms": elapsed_ms,
    }
    if "daemon_id" in checked:
        body["daemon_id"] = checked["daemon_id"]
    body["execution_binding_digest"] = canonical_digest({
        "schema": body["schema"], "run_binding": body["run_binding"],
        "profile_id": profile_id, "authority_digest": body["authority_digest"],
        "policy_digest": body["policy_digest"], "implementation_digest": implementation_digest,
        "server_major": server_version_major(checked["server_version"]),
        "daemon_id": body.get("daemon_id"),
        "probe_image_id": body["probe_image_id"],
        "qualification_image_digest": canonical_digest(dict(probe_image_row)),
        "spike_images_digest": canonical_digest(spike_data),
        "probe_digest": canonical_digest(probes_data), "cleanup": dict(_CLEANUP_PROOF),
    })
    return validate_run_record({**body,
                                "qualification_record_digest": canonical_digest(body)})


def _naming_levels(store: "RunQualificationStore") -> tuple[Path, Path, Path]:
    """The three directory levels that name a published record, from the top.

    The durable barrier covers this whole required chain (re-review, blocking 2): the
    sandbox directory naming the namespace, the namespace naming ``records``, and
    ``records`` naming the record file. Every level must be proven synced — the scaffold
    may have pre-created any of them well before the publication, so "newly created for
    this publication" is not the criterion; presence with a proven dirent is.
    """
    return (store.sandbox_root, store.root, store.records)


class RunQualificationStore:
    """Read-only loader for content-addressed `/3` records. The `/2` record and
    pointer namespaces are structurally outside it (different tree, no fallback), and
    nothing here ever writes a pointer: installation is the caller's binding CAS."""
    def __init__(self, sandbox_root: Path | None = None, **_: Any) -> None:
        self.sandbox_root = (common.sandbox_home() if sandbox_root is None
                             else common.canonical_authority_path(Path(sandbox_root)))
        self.root = self.sandbox_root / NAMESPACE
        self.records = self.root / "records"

    def load_record(self, record_digest: str, *, project_id: str, run_id: str,
                    generation: int) -> dict[str, Any]:
        """Load one exact record and verify its run-generation lineage."""
        if not validate_digest256(record_digest):
            raise authority.GenericQualificationError("run qualification record digest is malformed")
        common.safe_directory(self.root, create=False)
        # The records directory is validated on load exactly as on write, so a symbolic
        # link planted between the two checks cannot redirect a read into another tree.
        common.safe_directory(self.records, create=False)
        raw = common.safe_file(
            self.records / (record_digest.removeprefix("sha256:") + ".json"),
            cap=MAX_RECORD_BYTES)
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise authority.GenericQualificationError(
                "run qualification record file is malformed") from exc
        record = validate_run_record(document)
        if record["qualification_record_digest"] != record_digest:
            raise authority.GenericQualificationError("run qualification record path differs")
        binding = record["run_binding"]
        if (binding["project_id"] != project_id or binding["run_id"] != run_id
                or binding["generation"] != generation):
            raise authority.GenericQualificationError("run qualification record lineage differs")
        return record

    def write_record(self, record: Mapping[str, Any]) -> Path:
        """Publish one validated record content-addressed and durably (write-first).

        Publication is strictly durable across the whole naming chain: the file is
        fsynced and renamed, and each of the three directories that name it — the sandbox
        directory naming the namespace, the namespace naming ``records``, and ``records``
        naming the file — is strictly fsynced, whatever created it and however long ago
        (re-review, blocking 2). A directory-open or fsync error at any level fails the
        publication, and the identical-bytes retry re-proves the same chain before
        returning. Read-back and mode checks alone would not.
        """
        checked = validate_run_record(dict(record))
        digest = checked["qualification_record_digest"]
        # Create and identity-validate every level first: a pre-planted symlink at any
        # of the three names is refused before a single byte is written.
        for level in _naming_levels(self):
            common.safe_directory(level, create=True)
        path = self.records / (digest.removeprefix("sha256:") + ".json")
        payload = fsio.dump_json(checked)
        if len(payload) > MAX_RECORD_BYTES:
            raise authority.GenericQualificationError(
                "run qualification record exceeds its byte bound")
        readback = None
        try:
            readback = common.safe_file(path, cap=MAX_RECORD_BYTES)
        except common.GenericScaffoldingError as exc:
            if path.exists() or path.is_symlink():
                # an impostor occupies the publication name: unsafe, and the owner
                # boundary reports it as the scaffolding reason (re-review 4,
                # non-blocking 2), never a capability one
                raise common.GenericScaffoldingError(
                    "run qualification record path is unsafe") from exc
        if readback is not None:
            if readback != payload:
                raise authority.GenericQualificationError(
                    "run qualification record path contains different bytes")
            # The retry also proves durability: an interrupted writer can leave the
            # renamed file (or any of its naming levels) behind with no synchronized
            # dirent (E5 review, blocking 5; re-review, blocking 2 for the ancestors).
            for level in reversed(_naming_levels(self)):
                fsio.sync_directory(level)
            return path
        fsio.atomic_write(path, payload, durable=True)
        # The published entry is validated — never mutated. No permission is changed
        # after the rename at all: the closed 0600 mode was fixed on the exclusive
        # temporary descriptor before its fsync and the rename (fsio.atomic_write), so
        # an entry that appears here with any other mode, owner, or link shape — for
        # example a hard link to an unrelated 0644 file swapped onto the fresh
        # publication — is refused without one write through the name (re-review 2,
        # blocking 4: hard links are no-follow-proof, only absence-of-mutation is).
        try:
            readback = common.safe_file(path, cap=MAX_RECORD_BYTES)
            if readback != payload:
                raise authority.GenericQualificationError(
                    "run qualification record publication differs")
        except common.GenericScaffoldingError as exc:
            if path.exists() or path.is_symlink():
                # a swapped-in impostor occupies the publication name: unsafe, refused,
                # and reported at the owner boundary as the scaffolding reason
                # (re-review 4, non-blocking 2), never a capability one
                raise common.GenericScaffoldingError(
                    "run qualification record path is unsafe") from exc
            raise
        for level in reversed(_naming_levels(self)):
            fsio.sync_directory(level)
        return path


def _remain(deadline: float, clock: Callable[[], float], stage: str) -> None:
    """Every stage reads the remaining one-host bound (the E4 fake-clock pattern)."""
    if clock() >= deadline:
        raise _RunFailure(RunQualificationReason.DEADLINE_EXCEEDED,
                          "run qualification deadline expired at: " + stage)


def _classify_authority_error(exc: Exception, *, observation: bool) -> _RunFailure:
    """Map one Docker-authority observation error onto the closed reason set. During the
    initial observation a read failure is `docker-unreachable` (A1: a stopped daemon);
    a capability refusal keeps its own kept value — including a reachable daemon whose
    platform the support contract does not admit (an unsupported engine, never an
    unreachability); the deadline is never collapsed."""
    if isinstance(exc, authority.CapabilityRefused):
        # the endpoint resolved and the daemon answered; its facts do not admit
        observation = False
    message = str(exc)
    if "resource-limits-unavailable:" in message:
        return _RunFailure(RunQualificationReason.RESOURCE_LIMITS_UNAVAILABLE, message)
    if "deadline expired" in message:
        return _RunFailure(RunQualificationReason.DEADLINE_EXCEEDED, message)
    if observation:
        return _RunFailure(RunQualificationReason.DOCKER_UNREACHABLE, message)
    return _RunFailure(RunQualificationReason.CAPABILITY_UNAVAILABLE, message)


def _observe_authority(runtime_inspector: Callable[..., authority.GenericRuntimeInspection],
                       seed: Mapping[str, Any], *, deadline: float,
                       clock: Callable[[], float], stage: str) -> dict[str, Any]:
    """Observe the authority once (context, version, info including ``daemon_id``):
    reads only, no admission.

    The owner's observation is the non-admitting
    :func:`authority.inspect_observed_runtime`: it resolves the endpoint once, pins its
    own remaining reads to it and never admits the facts, because recovery precedes the
    new attempt's admission (E5 review, blocking finding 2): a capability or
    implementation refusal must never skip journal recovery. Admission of these same
    facts happens after recovery.
    """
    _remain(deadline, clock, stage)
    try:
        observed = runtime_inspector(seed, deadline=deadline)
    except authority.GenericQualificationError as exc:
        raise _classify_authority_error(exc, observation=True) from exc
    if type(observed) is not authority.GenericRuntimeInspection:
        raise _RunFailure(RunQualificationReason.DOCKER_UNREACHABLE,
                          "the Docker authority observation is unknown")
    return observed.runtime


def _admit_observed_facts(observed: Mapping[str, Any]) -> dict[str, Any]:
    """Admit the observed facts as the pinned runtime of the new attempt (after recovery):
    a capability or limits refusal keeps its own kept reason, the deadline is never
    collapsed, anything else is a closed capability failure."""
    try:
        return authority.validate_runtime(dict(observed))
    except authority.GenericQualificationError as exc:
        raise _classify_authority_error(exc, observation=False) from exc


def _final_reobservation(runtime: Mapping[str, Any], implementation_digest: str, *,
                         pinned_reobserver: Callable[..., authority.GenericRuntimeInspection],
                         implementation_inspector: Callable[[], Mapping[str, Any]],
                         deadline: float, clock: Callable[[], float]) -> None:
    """Re-observe authority and the implementation after the probes (spec section 5,
    step 5) through the pinned endpoint: semantic equality plus pre/post server-version
    equality; a change is the stale reason of its own kind, never a collapsed probe
    failure. Changed observations (any fact that no longer admits, capability included)
    are stale-daemon; initial capability refusal keeps its own kept reason at the
    initial observation, and a genuine read failure stays unreachable.
    """
    _remain(deadline, clock, "the post-probe authority re-observation")
    try:
        observed = pinned_reobserver(runtime, deadline=deadline)
    except authority.GenericQualificationError as exc:
        raise _classify_authority_error(exc, observation=True) from exc
    if type(observed) is not authority.GenericRuntimeInspection:
        raise _RunFailure(RunQualificationReason.STALE_DAEMON,
                          "the Docker authority could not be re-observed after the probes")
    try:
        current = authority.validate_reobserved_runtime(runtime, observed.runtime)
    except authority.DaemonDrift as exc:
        raise _RunFailure(RunQualificationReason.STALE_DAEMON,
                          "the Docker daemon authority drifted during qualification") from exc
    if not authority.authorities_match(runtime, current):
        raise _RunFailure(RunQualificationReason.STALE_DAEMON,
                          "the Docker daemon authority drifted during qualification")
    if current["server_version"] != runtime["server_version"]:
        raise _RunFailure(RunQualificationReason.STALE_DAEMON,
                          "the Docker server version changed during qualification")
    try:
        manifest = implementation_inspector()
    except Exception as exc:
        raise _RunFailure(RunQualificationReason.STALE_IMPLEMENTATION,
                          "the loaded release could not be re-observed after the probes") from exc
    if (not isinstance(manifest, Mapping)
            or type(manifest.get("implementation_digest")) is not str
            or manifest["implementation_digest"] != implementation_digest):
        raise _RunFailure(RunQualificationReason.STALE_IMPLEMENTATION,
                          "the loaded release differs from the qualified release")


def admit_probe_image(runtime: Mapping[str, Any], *, config: Path, runner: process.Runner,
                      deadline: float, clock: Callable[[], float]) -> tuple[dict[str, Any], str]:
    """Verify the trusted probe image per spec section 4.3, under the pinned endpoint.

    One ``docker image inspect`` of the exact pinned reference must show the pinned
    index digest in some ``RepoDigests`` entry — under any repository name, because
    the digest is the image's content identity and the repository name is not (a
    mirror or proxy pull reports the digest under its own name), never on the
    daemon-local ``Id`` — plus the server-native platform and no declared volumes;
    anything else — including a locally built, loaded or retagged image, which carries
    no ``RepoDigests`` entry with the pinned digest — is ``probe-image-untrusted``.
    """
    _remain(deadline, clock, "the trusted probe image inspection")
    pin = probe_image.pinned_probe_image()
    environment = {"LANG": "C", "LC_ALL": "C", "DOCKER_HOST": runtime["endpoint"],
                   "DOCKER_CONFIG": str(config)}
    argv = (runtime["docker_cli"], "image", "inspect", "--format",
            PROBE_IMAGE_INSPECT_FORMAT, pin.reference)
    detail = ("the pinned probe image is not admitted locally; pull " + pin.reference
              + ", then qualify")
    try:
        result = runner.run(argv, timeout_seconds=min(10.0, deadline - clock()),
                             output_limit_bytes=authority.MAX_POINTER_BYTES, env=environment)
    except Exception as exc:
        raise _RunFailure(RunQualificationReason.PROBE_IMAGE_UNTRUSTED, detail) from exc
    try:
        result = qualification._command_result(result, "the trusted probe image inspection")
    except qualification.GenericQualificationFailure as exc:
        raise _RunFailure(RunQualificationReason.PROBE_IMAGE_UNTRUSTED, detail) from exc
    if result.timed_out or result.output_limited or result.returncode != 0:
        raise _RunFailure(RunQualificationReason.PROBE_IMAGE_UNTRUSTED, detail)
    try:
        document = admission.json_object(result.stdout.data)
        image_id = validate_digest256(document.get("Id"))
        digests = document.get("RepoDigests")
        if (type(digests) is not list or len(digests) > 128
                or any(type(item) is not str or not item for item in digests)):
            raise ValueError("RepoDigests are malformed")
        raw_os, raw_architecture = document.get("Os"), document.get("Architecture")
        if (type(raw_os) is not str or type(raw_architecture) is not str
                or document.get("Volumes") not in (None, {})):
            raise ValueError("platform or volume facts are malformed")
        canonical_os = spike_plan.canonical_os(raw_os)
        canonical_architecture = spike_plan.canonical_architecture(raw_architecture)
        if (canonical_os != "linux" or canonical_os != runtime["server_os"]
                or canonical_architecture != runtime["server_architecture"]):
            raise ValueError("the platform is not server-native")
    except Exception as exc:
        raise _RunFailure(RunQualificationReason.PROBE_IMAGE_UNTRUSTED, detail) from exc
    if not any(_REPO_DIGEST.fullmatch(entry) is not None
               and entry.rsplit("@", 1)[1] == pin.index_digest for entry in digests):
        # content identity, not repository identity: no entry of any repository name
        # carries the pinned index digest, so the local image is not the pinned one
        raise _RunFailure(RunQualificationReason.PROBE_IMAGE_UNTRUSTED, detail)
    try:
        # The sealed reference is the pinned ``repo@index-digest`` form (the canonical
        # name, never a mirror's own repository name, since the pin is the contract's
        # content identity); the full pinned ``name:tag@digest`` reference is a pull
        # hint rendered in the failure detail, and the pinned contract itself is
        # bound through the implementation digest.
        image = qualification.QualificationImage(
            pin.repo_digest, image_id, raw_os, canonical_os, raw_architecture,
            canonical_architecture, ())
    except Exception as exc:
        raise _RunFailure(RunQualificationReason.PROBE_IMAGE_UNTRUSTED, detail) from exc
    row = image.to_data()
    row["repo_digest"] = pin.repo_digest
    return row, image


EffectsStage = Callable[[authority.ResolvedGenericAuthority, "planner.ResolvedDockerOperation | None", str],
                        execution.GenericEffects]


def _concrete_effects(resolved: authority.ResolvedGenericAuthority) \
        -> execution.GenericEffects:
    return execution.concrete_effects(Path(resolved.runtime["docker_cli"]),
                                      dict(resolved.environment), resolved.work_directory)


def qualify_run_environment(
    *,
    project_id: str,
    run_id: str,
    generation: int,
    attempt_id: str,
    revision: int,
    proposal_digest: str,
    spike_images: Sequence[str],
    profile_id: str,
    docker_cli: Path,
    qualification_seconds: float | None = None,
    runtime_inspector: Callable[..., authority.GenericRuntimeInspection] = authority.inspect_observed_runtime,
    pinned_reobserver: Callable[..., authority.GenericRuntimeInspection] = authority.inspect_pinned_runtime,
    implementation_inspector: Callable[[], Mapping[str, Any]] = common.implementation_manifest,
    image_runner: process.Runner | None = None,
    effects_factory: EffectsStage | None = None,
    kernel_factory: Callable[..., qualification.QualificationProbeKernel] = qualification.ProductionProbeKernel,
    sandbox_root: Path | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> RunQualificationOutcome:
    """Run one qualification attempt for the run binding and write the `/3` record.

    Returns ``bound(record_digest, record)`` only after the record is durably published
    write-first — and only after the deadline is re-checked past publication — for the
    caller's binding compare-and-set, or one closed failure. The attempt holds the
    shared runtime lock for its whole duration; a failure or a timeout binds nothing
    and leaves recoverable journals.

    ``runtime_inspector`` performs the one initial discovery (endpoint resolution), and
    it is non-admitting by default — recovery runs on the raw facts before the attempt's
    capability admission; ``pinned_reobserver`` performs every later daemon read — the
    kernel postflights and the final re-observation — at that pinned endpoint, never a
    fresh context resolution. Expiry anywhere reports the deadline reason: when the budget is gone,
    every stage error path is converted to the deadline reason before the outcome is
    built.
    """
    # --- ingress -------------------------------------------------------------
    try:
        binding = RunBinding(project_id, run_id, generation, attempt_id, revision,
                             proposal_digest)
        if (type(profile_id) is not str or profile_id not in spike_plan.NETWORK_PROFILES):
            raise ValueError("run qualification profile is unknown")
        if (type(spike_images) is not list and type(spike_images) is not tuple) or (
                not 1 <= len(spike_images) <= MAX_SPIKE_IMAGE_REFERENCES
                or any(type(item) is not str for item in spike_images)):
            raise ValueError("run qualification spike images are not closed")
        for item in spike_images:
            spike_plan.validate_local_image_reference(item)
        if type(qualification_seconds) is not float and type(qualification_seconds) is not int:
            if qualification_seconds is None:
                qualification_seconds = DEFAULT_QUALIFICATION_SECONDS
            else:
                raise ValueError("run qualification budget is malformed")
        if not 0 < qualification_seconds <= DEFAULT_QUALIFICATION_SECONDS:
            raise ValueError("run qualification budget is outside its bound")
        docker_cli = Path(docker_cli)
    except (ValueError, TypeError, OSError) as exc:
        return RunQualificationFailed(RunQualificationReason.INVALID_INPUT, str(exc))
    budget = float(qualification_seconds)
    began = clock()
    deadline = began + budget
    # The stage label for deadline details: the inner attempt keeps it current.
    stage: list[str] = ["the runtime lock wait"]

    def deadline_outcome() -> RunQualificationFailed:
        return RunQualificationFailed(
            RunQualificationReason.DEADLINE_EXCEEDED,
            "run qualification deadline expired at: " + stage[0])

    try:
        return _qualify_locked(binding, spike_images, profile_id, docker_cli, deadline,
                               runtime_inspector=runtime_inspector,
                               pinned_reobserver=pinned_reobserver,
                               implementation_inspector=implementation_inspector,
                               image_runner=image_runner, effects_factory=effects_factory,
                               kernel_factory=kernel_factory, sandbox_root=sandbox_root,
                               began=began, clock=clock, stage=stage)
    except _RunFailure as exc:
        if clock() >= deadline:
            # Expiry anywhere reports the deadline reason (E5 review, blocking 4), including
            # a stage error that fired while the budget was already gone.
            return deadline_outcome()
        return RunQualificationFailed(exc.reason, exc.detail, exc.probe)
    except qualification.GenericQualificationDrift as exc:
        if clock() >= deadline:
            return deadline_outcome()
        reason = (RunQualificationReason.STALE_IMPLEMENTATION
                  if isinstance(exc.drift, authority.ImplementationDrift)
                  else RunQualificationReason.STALE_DAEMON)
        return RunQualificationFailed(reason, str(exc))
    except qualification.GenericQualificationFailure as exc:
        if clock() >= deadline:
            return deadline_outcome()
        reason = _MAPPED_QUALIFICATION_REASONS.get(
            exc.reason, RunQualificationReason.CAPABILITY_UNAVAILABLE)
        return RunQualificationFailed(reason, str(exc))
    except execution.GenericExecutionUnavailable as exc:
        if clock() >= deadline:
            return deadline_outcome()
        return RunQualificationFailed(RunQualificationReason.CLEANUP_UNAVAILABLE, str(exc))
    except common.GenericScaffoldingError as exc:
        if clock() >= deadline or "deadline expired" in str(exc):
            return deadline_outcome()
        return RunQualificationFailed(RunQualificationReason.SANDBOX_UNSAFE, str(exc))
    except authority.GenericQualificationError as exc:
        if clock() >= deadline:
            return deadline_outcome()
        return RunQualificationFailed(RunQualificationReason.CAPABILITY_UNAVAILABLE, str(exc))
    except Exception as exc:
        if clock() >= deadline:
            return deadline_outcome()
        return RunQualificationFailed(RunQualificationReason.CAPABILITY_UNAVAILABLE,
                                      "run qualification failed: " + str(exc))


def _qualify_locked(
    binding: RunBinding,
    spike_images: Sequence[str],
    profile_id: str,
    docker_cli: Path,
    deadline: float,
    *,
    runtime_inspector: Callable[..., authority.GenericRuntimeInspection],
    pinned_reobserver: Callable[..., authority.GenericRuntimeInspection],
    implementation_inspector: Callable[[], Mapping[str, Any]],
    image_runner: process.Runner | None,
    effects_factory: EffectsStage | None,
    kernel_factory: Callable[..., qualification.QualificationProbeKernel],
    sandbox_root: Path | None,
    began: float,
    clock: Callable[[], float],
    stage: list[str],
) -> RunQualificationBound:
    root = (common.sandbox_home() if sandbox_root is None
            else common.canonical_authority_path(Path(sandbox_root)))
    store = common.ExecutionStore(root)
    store.prepare_execution_scaffolding()
    generic_journal.GenericJournalStore(store).prepare()
    with common.runtime_lock(store, deadline, clock=clock):
        # Initial discovery: one observation (context, version, info including
        # ``daemon_id``) that resolves the endpoint once — reads only, no admission.
        # Recovery precedes the new attempt's admission, so a capability or
        # implementation refusal must never skip journal recovery (E5 review, 2).
        stage[0] = "the authority observation"
        observed = _observe_authority(runtime_inspector, {"docker_cli": str(docker_cli)},
                                     deadline=deadline, clock=clock, stage=stage[0])
        # The minimum pinned daemon observation journal recovery needs: the canonical
        # endpoint and platform only (never capability facts, never the daemon
        # identifier), so a daemon that now refuses capability still gets recovered.
        try:
            identity = authority.pinned_daemon_identity(observed)
        except authority.GenericQualificationError as exc:
            raise _RunFailure(RunQualificationReason.DOCKER_UNREACHABLE,
                              "the Docker authority cannot be identified for recovery") from exc
        # Private per-attempt authority scaffolding under the run-scoped namespace —
        # directories only. The config-emptiness admission runs after recovery.
        namespace = root / NAMESPACE
        config = namespace / "configs" / profile_id
        work = namespace / "work"
        for directory in (namespace, config, work, namespace / "records"):
            common.safe_directory(directory, create=True)
        seed = canonical_digest({"schema": QUALIFICATION_RECORD_SCHEMA,
                                 "attempt": binding.to_data()})
        recovery_cli = Path(observed.get("docker_cli", str(docker_cli)))
        recovery = authority.ResolvedGenericAuthority(
            application.GenericQualifiedBackend("docker", profile_id, seed, seed),
            recovery_cli, tuple(sorted({
                "LANG": "C", "LC_ALL": "C", "DOCKER_HOST": identity["endpoint"],
                "DOCKER_CONFIG": str(config),
            }.items())), work, identity["server_os"], identity["server_architecture"],
            observed.get("ncpu", 0), observed.get("mem_total", 0), dict(observed))
        selected_effects = effects_factory or (lambda current, operation, name:
                                               _concrete_effects(current))
        # Recover every prior journal under the shared runtime lock, on the pinned
        # daemon, before any part of the new attempt's admission.
        stage[0] = "the prior journal recovery"
        effects = selected_effects(recovery, None, "recovery")
        try:
            execution.recover_generic_operations(
                store, effects, authority=identity, deadline=deadline, clock=clock)
        finally:
            effects.close()
        # --- admission of the new attempt, strictly after recovery -------------------
        # Capability admission of the same observed facts: a refusal here keeps its own
        # kept reason and has already recovered its prior journals.
        stage[0] = "the authority admission"
        runtime = _admit_observed_facts(observed)
        # The frozen loaded release (E4): one per-process freeze, compared against disk;
        # an unprovable loaded release can never be bound.
        stage[0] = "the loaded release check"
        try:
            loaded_release = common.freeze_loaded_release(implementation_inspector)
        except common.GenericScaffoldingError as exc:
            raise _RunFailure(RunQualificationReason.STALE_IMPLEMENTATION,
                              "the loaded release could not be established for qualification") from exc
        implementation_probe = implementation_inspector()
        if (not isinstance(implementation_probe, Mapping)
                or type(implementation_probe.get("implementation_digest")) is not str
                or implementation_probe["implementation_digest"] != loaded_release):
            raise _RunFailure(RunQualificationReason.STALE_IMPLEMENTATION,
                              "the loaded release differs from disk before qualification")
        implementation_digest = validate_digest256(loaded_release)
        # The private qualification config must be empty for this attempt — admission.
        stage[0] = "the qualification config admission"
        try:
            if tuple(config.iterdir()):
                raise _RunFailure(
                    RunQualificationReason.CAPABILITY_UNAVAILABLE,
                    "the qualification Docker config contains foreign files; empty the private "
                    "qualification config directory before retrying")
        except OSError as exc:
            raise _RunFailure(RunQualificationReason.CAPABILITY_UNAVAILABLE,
                              "the qualification Docker config is unreadable") from exc
        resolved = authority.ResolvedGenericAuthority(
            application.GenericQualifiedBackend("docker", profile_id, seed, seed),
            Path(runtime["docker_cli"]), tuple(sorted({
                "LANG": "C", "LC_ALL": "C", "DOCKER_HOST": runtime["endpoint"],
                "DOCKER_CONFIG": str(config),
            }.items())), work, runtime["server_os"], runtime["server_architecture"],
            runtime["ncpu"], runtime["mem_total"], runtime)
        runner = process.BoundedRunner() if image_runner is None else image_runner
        # Trusted probe image admission (4.3), then spike-image resolution and sealing.
        stage[0] = "the trusted probe image inspection"
        probe_row, probe_image_ = admit_probe_image(
            runtime, config=config, runner=runner, deadline=deadline, clock=clock)
        stage[0] = "the spike image inspection"
        spike_rows = []
        for reference in spike_images:
            stage[0] = "the spike image inspection " + reference
            _remain(deadline, clock, "the spike image inspection " + reference)
            try:
                sealed = qualification.inspect_qualification_image(
                    reference, runtime, config=config, runner=runner,
                    deadline=deadline, clock=clock)
            except qualification.GenericQualificationFailure as exc:
                raise _RunFailure(
                    _MAPPED_QUALIFICATION_REASONS.get(
                        exc.reason, RunQualificationReason.CAPABILITY_UNAVAILABLE),
                    str(exc)) from exc
            spike_rows.append(sealed.to_data())
        # The five fixed probes through the journalled production lifecycle, each
        # followed by the E4 postflight re-observation inside the kernel.
        kernel = kernel_factory(
            store, runtime, profile_id, probe_image_, implementation_digest,
            effects_factory=lambda operation, name: selected_effects(resolved, operation, name),
            clock=clock, runtime_inspector=runtime_inspector,
            pinned_reobserver=pinned_reobserver,
            implementation_inspector=implementation_inspector)
        probes = []
        for name, expected in zip(qualification.PROBE_ORDER, qualification.PROBE_EXPECTED):
            stage[0] = "the qualification probe " + name
            _remain(deadline, clock, "the qualification probe " + name)
            try:
                probes.append(kernel.run(name, expected, deadline=deadline))
            except qualification.GenericQualificationDrift as exc:
                reason = (RunQualificationReason.STALE_IMPLEMENTATION
                          if isinstance(exc.drift, authority.ImplementationDrift)
                          else RunQualificationReason.STALE_DAEMON)
                raise _RunFailure(reason, str(exc)) from exc
            except qualification.GenericQualificationFailure as exc:
                reason = (RunQualificationReason.PROBE_FAILED
                          if exc.reason is qualification.QualificationReason.PROBE_FAILED
                          else _MAPPED_QUALIFICATION_REASONS.get(
                              exc.reason, RunQualificationReason.CAPABILITY_UNAVAILABLE))
                raise _RunFailure(
                    reason, str(exc),
                    name if reason is RunQualificationReason.PROBE_FAILED else None) from exc
        stage[0] = "the qualification cleanup proof"
        try:
            kernel.cleanup_proof()
        except qualification.GenericQualificationFailure as exc:
            raise _RunFailure(
                _MAPPED_QUALIFICATION_REASONS.get(
                    exc.reason, RunQualificationReason.CAPABILITY_UNAVAILABLE),
                str(exc)) from exc
        _final_reobservation(runtime, implementation_digest,
                              pinned_reobserver=pinned_reobserver,
                              implementation_inspector=implementation_inspector,
                              deadline=deadline, clock=clock)
        _remain(deadline, clock, "the qualification record publication")
        stage[0] = "the qualification record publication"
        record = build_run_record(binding, profile_id, runtime, implementation_digest,
                                   probe_row, spike_rows, tuple(probes),
                                   int((clock() - began) * 1000))
        try:
            RunQualificationStore(root).write_record(record)
        except OSError as exc:
            # Strict durability fails the publication: a directory the record cannot be
            # proven durable in is a sandbox storage failure, never a capability one.
            raise _RunFailure(RunQualificationReason.SANDBOX_UNSAFE,
                              "the qualification record could not be published durably") from exc
        # Recheck the deadline after publication: an attempt whose budget died during
        # the build or the durable write returns failed, never a timed-out binding; the
        # written record stays an inert orphan for its own digest and lineage.
        _remain(deadline, clock, "the qualification record publication")
        return RunQualificationBound(record["qualification_record_digest"], record)
