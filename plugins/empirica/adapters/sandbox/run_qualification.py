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
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

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
PROBE_IMAGE_LIST_FORMAT = "{{json .}}"
#: The bounded `docker image list --digests` fallback (drift review, non-blocking 6): the
#: answer's bytes and rows are each capped, and an oversized or unparsable list refuses
#: the admission rather than risk a partial read.
MAX_IMAGE_LIST_BYTES = 256 * 1024
MAX_IMAGE_LIST_ROWS = 512

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
    answer — can seal no binding and fails closed.

    The major is the normalized integer, never the literal text (drift review,
    non-blocking 2): ``029.1`` and ``29.1`` bind the same ``29``, so the invariant is
    numeric rather than textual.
    """
    major = value.partition(".")[0] if type(value) is str else None
    if major is None or _MAJOR_VERSION.fullmatch(major) is None:
        raise authority.GenericQualificationError(
            "Docker server major version is unavailable: " + repr(value))
    return str(int(major))


class RunQualificationReason(str, Enum):
    """The closed failure values this owner can return: spec section 5.1 where named,
    plus the kept deadline, egress, cleanup, image-resolution and capability values.
    Additive only; existing values are never collapsed or rewritten.

    The attempt-failure values are bound to the public reason registry — the
    ``GenericUnavailableReason`` and ``GenericPreparationReason`` members whose closed set
    the response schema mirrors as ``qualification.last_failure.reason``
    (``QUALIFICATION_FAILURE_REASONS``) — by member, never by a second literal: renaming or
    removing one in the registry fails this module at import instead of drifting. The owner
    never emits the CAS/conflict value: the binding CAS the caller performs owns it. The one
    closed, documented exception is ``invalid-input``: ingress is validated before any
    reservation (the application transaction derives every binding fact from committed,
    validated state), so the owner's invalid-input outcome is never an attempt failure — the
    port refuses it fail-closed and no failure word is fabricated in its place."""
    INVALID_INPUT = "invalid-input"
    PROBE_IMAGE_UNTRUSTED = application.GenericUnavailableReason.PROBE_IMAGE_UNTRUSTED.value
    DOCKER_UNREACHABLE = application.GenericUnavailableReason.DOCKER_UNREACHABLE.value
    RESOURCE_LIMITS_UNAVAILABLE = \
        application.GenericUnavailableReason.RESOURCE_LIMITS_UNAVAILABLE.value
    CAPABILITY_UNAVAILABLE = application.GenericUnavailableReason.CAPABILITY_UNAVAILABLE.value
    SANDBOX_UNSAFE = application.GenericUnavailableReason.SANDBOX_SCAFFOLDING_UNSAFE.value
    STALE_DAEMON = application.GenericUnavailableReason.QUALIFICATION_STALE_DAEMON.value
    STALE_IMPLEMENTATION = \
        application.GenericUnavailableReason.QUALIFICATION_STALE_IMPLEMENTATION.value
    PROBE_FAILED = application.GenericUnavailableReason.QUALIFICATION_PROBE_FAILED.value
    IMAGE_NOT_LOCAL = application.GenericPreparationReason.IMAGE_NOT_LOCAL.value
    IMAGE_REFERENCE_AMBIGUOUS = application.GenericPreparationReason.IMAGE_REFERENCE_AMBIGUOUS.value
    IMAGE_MALFORMED = application.GenericUnavailableReason.IMAGE_MALFORMED.value
    IMAGE_INSPECT_FAILED = application.GenericUnavailableReason.IMAGE_INSPECT_FAILED.value
    IMAGE_INSPECT_TIMEOUT = application.GenericUnavailableReason.IMAGE_INSPECT_TIMEOUT.value
    IMAGE_INSPECT_OUTPUT_LIMIT = application.GenericUnavailableReason.IMAGE_INSPECT_OUTPUT_LIMIT.value
    IMAGE_PLATFORM_MISMATCH = application.GenericPreparationReason.IMAGE_PLATFORM_MISMATCH.value
    IMAGE_DECLARES_VOLUMES = application.GenericPreparationReason.IMAGE_DECLARES_VOLUMES.value
    IMAGE_TOOL_MISSING = application.GenericUnavailableReason.IMAGE_TOOL_MISSING.value
    IMAGE_UID_UNUSABLE = application.GenericUnavailableReason.IMAGE_UID_UNUSABLE.value
    EGRESS_UNAVAILABLE = application.GenericUnavailableReason.NETWORK_EGRESS_UNAVAILABLE.value
    CLEANUP_UNAVAILABLE = application.GenericUnavailableReason.CLEANUP_UNAVAILABLE.value
    DEADLINE_EXCEEDED = application.GenericUnavailableReason.QUALIFICATION_DEADLINE_EXCEEDED.value

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

    @staticmethod
    def _counter(value: object, where: str) -> int:
        if type(value) is not int or isinstance(value, bool) or not 1 <= value < 2 ** 31:
            raise ValueError("run qualification counter is not closed: " + where)
        return value

    def __post_init__(self) -> None:
        self._identifier(self.project_id, "project_id")
        self._identifier(self.run_id, "run_id")
        self._identifier(self.attempt_id, "attempt_id")
        for name in ("generation", "revision"):
            self._counter(getattr(self, name), name)
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


# --- the closed shapes of the record's definition home (the conformance harness's word) -----
#
# The /3 record schema (contracts/empirica-docker-spike/v1/qualification-record.schema.json is
# the shape's definition home) is registered with the ADR-0075 contract conformance harness
# through its declaration pair: the schema's ``x-core-ingress`` roots with their
# ``x-core-validator`` annotations, and the core's row in ``core.contract_registry``. These
# public callables mirror their schema nodes exactly, so the harness's differential holds the
# two voices of every closed shape together; each raises only the ValueError family — the
# harness's domain of a refusal — as a pure function of one value. The semantic admission
# (``validate_run_record`` below and its runtime row helpers) composes on them and owns every
# rule the schema cannot express: the /2 runtime and daemon-identity admission, the semantic
# authority projection, the platform cross-field identities, the byte-bounded identifiers, the
# policy and implementation bindings, the probe order and outcome equality, and the
# content-addressed file name, digests and run-generation lineage of the store.

#: The probe rows' duration ceiling (the /2 millisecond bound minus one) and the record's elapsed
#: ceiling (the operator timing session's default qualification budget), mirrored from the
#: schema nodes' own descriptions.
PROBE_DURATION_CEILING_MS = 59_999
QUALIFICATION_ELAPSED_CEILING_MS = int(DEFAULT_QUALIFICATION_SECONDS * 1000) - 1
_DAEMON_IDENTIFIER = re.compile(
    # exactly the rule the /2 authority's ``_daemon_id`` applies (registration
    # review, non-blocking 2, aligned so a daemon the authority observes is never
    # refused at sealing after all five probes): no C0/C1 control anywhere, at
    # most 128 characters (checked beside the pattern), and not all whitespace —
    # interior, leading and trailing ASCII spaces stay admissible everywhere.
    # The required middle character excludes the cross-language blank set —
    # Python ``str.strip``'s whitespace minus the controls the outer class already
    # refuses (Python and JavaScript RegExp/u disagree on U+FEFF, so the set is
    # spelled, never ``\S``) — and therefore admits a lone U+FEFF exactly like
    # the authority's ``strip``, which does not treat it as whitespace either.
    r"^[^\u0000-\u001f\u007f-\u009f]*"
    r"[^\u0000-\u0020\u007f-\u009f\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]"
    r"[^\u0000-\u001f\u007f-\u009f]*(?![\s\S])")
_PROBE_ROW_FIELDS = frozenset({
    "name", "expected", "observed", "operation_digest", "cleanup_proven",
    "duration_ms", "running_network_membership", "realization_digest",
})
_PROBE_OUTCOMES = frozenset(qualification.PROBE_EXPECTED)
_PROBE_IMAGE_FIELDS = SPIKE_IMAGE_FIELDS | {"capabilities", "repo_digest"}


def _refuse(where: str) -> None:
    raise ValueError("run qualification " + where)


def validate_binding_identifier(value: object) -> str:
    """The record's identifier lexeme (the schema's ``$defs/identifier``): one nonempty
    string of at most 128 closed characters — bytes stay the semantic admission's, whose
    identifiers are bounded per UTF-8 encoding."""
    if (type(value) is not str or len(value) > MAX_IDENTIFIER_CHARS
            or _IDENTIFIER.fullmatch(value) is None):
        _refuse("identifier is not closed")
    return value  # type: ignore[return-value]


def validate_binding_counter(value: object) -> int:
    """The schema's ``$defs/counter``: a counted integer, never a JSON boolean."""
    if type(value) is not int or isinstance(value, bool) or not 1 <= value <= 2_147_483_647:
        _refuse("counter is not closed")
    return value


def validate_repo_digest(value: object) -> str:
    """The schema's ``$defs/repoDigest``: a canonical repository reference pinned to its content
    digest — the same absolutely anchored lexeme the /2 helper `$repo_digest` admits."""
    if type(value) is not str or _REPO_DIGEST.fullmatch(value) is None:
        _refuse("repo digest is malformed")
    return value


def validate_run_binding_shape(value: object) -> dict[str, Any]:
    """The schema's ``$defs/runBinding``: the run facts a qualification attempt binds its
    record to, as one closed map."""
    if type(value) is not dict or set(value) != RUN_BINDING_FIELDS:
        _refuse("binding shape is unknown")
    for member in ("project_id", "run_id", "attempt_id"):
        validate_binding_identifier(value[member])
    for member in ("generation", "revision"):
        validate_binding_counter(value[member])
    validate_digest256(value["proposal_digest"])
    return value


def validate_daemon_identity_shape(value: object) -> str:
    """The record's optional ``daemon_id`` member's own lexeme — exactly what the /2
    authority's ``_daemon_id`` admits (registration review, non-blocking 2): no C0/C1
    control, at most 128 characters, not all whitespace, interior, leading and trailing
    spaces included, so a daemon the authority observes is never refused at sealing.
    Everything beyond the lexeme — when the identity may appear at all and which daemon
    it names — is the runtime admission's."""
    if (type(value) is not str or not 1 <= len(value) <= 128
            or _DAEMON_IDENTIFIER.fullmatch(value) is None):
        _refuse("daemon identity lexeme is malformed")
    return value


def validate_spike_image_row_shape(value: object) -> dict[str, Any]:
    """The schema's ``$defs/spikeImageRow``: one sealed spike-image row — the requested
    reference, the full daemon image ID (a diagnostic, never an admission gate), the platform
    facts as recorded, and no declared volumes. No capability list: spike images are
    inspected, never probed."""
    if type(value) is not dict or set(value) != SPIKE_IMAGE_FIELDS:
        _refuse("spike row shape is unknown")
    reference = value["reference"]
    if type(reference) is not str or not 1 <= len(reference) <= 255:
        _refuse("spike row reference is abnormal")
    validate_digest256(value["id"])
    raw_os = value["raw_os"]
    raw_architecture = value["raw_architecture"]
    architecture = value["architecture"]
    if (type(raw_os) is not str or not raw_os or value["os"] != "linux"
            or type(raw_architecture) is not str or not raw_architecture
            or type(architecture) is not str or not architecture):
        _refuse("spike row platform is unknown")
    if type(value["declared_volumes"]) is not list or value["declared_volumes"]:
        _refuse("spike row declares volumes")
    return value


def validate_qualification_image_shape(value: object) -> dict[str, Any]:
    """The schema's ``$defs/probeImage``: the one trusted probe image's own row — digest-pinned
    reference, the daemon image ID, the recorded platform facts, no declared volumes, the
    shared helper's closed capability list and the repo-digest admission proof."""
    if type(value) is not dict or set(value) != _PROBE_IMAGE_FIELDS:
        _refuse("probe image row shape is unknown")
    validate_repo_digest(value["reference"])
    validate_digest256(value["id"])
    raw_os = value["raw_os"]
    raw_architecture = value["raw_architecture"]
    architecture = value["architecture"]
    if (type(raw_os) is not str or not raw_os or value["os"] != "linux"
            or type(raw_architecture) is not str or not raw_architecture
            or type(architecture) is not str or not architecture):
        _refuse("probe image row platform is unknown")
    if type(value["declared_volumes"]) is not list or value["declared_volumes"]:
        _refuse("probe image row declares volumes")
    capabilities = value["capabilities"]
    if (type(capabilities) is not list or len(capabilities) != len(qualification.IMAGE_CAPABILITIES)
            or any(type(item) is not str or not item for item in capabilities)):
        _refuse("probe image capability list is unknown")
    validate_repo_digest(value["repo_digest"])
    return value


def validate_spike_images_shape(value: object) -> list:
    """The schema's ``$defs/spikeImages``: the environment proposal's 1..16 approved sealed
    rows."""
    if (type(value) is not list or not 1 <= len(value) <= MAX_SPIKE_IMAGE_REFERENCES):
        _refuse("spike rows are outside their bound")
    for row in value:
        validate_spike_image_row_shape(row)
    return value


def validate_probe_row_shape(value: object) -> dict[str, Any]:
    """The schema's ``$defs/probe``: one recorded probe — its fixed name, the outcomes as
    recorded (which must equal the expected one is the semantic admission's), the operation
    digest, the proven cleanup, the measured duration and the realized image digest when the
    probe reached a verdict."""
    if type(value) is not dict or set(value) != _PROBE_ROW_FIELDS:
        _refuse("probe row shape is unknown")
    if value["name"] not in qualification.PROBE_ORDER:
        _refuse("probe row name is unknown")
    expected, observed = value["expected"], value["observed"]
    if (type(expected) is not str or expected not in _PROBE_OUTCOMES
            or type(observed) is not str or observed not in _PROBE_OUTCOMES):
        _refuse("probe row outcome is unknown")
    validate_digest256(value["operation_digest"])
    if value["cleanup_proven"] is not True or type(value["running_network_membership"]) is not bool:
        _refuse("probe row proofs differ")
    duration = value["duration_ms"]
    if (type(duration) is not int or isinstance(duration, bool)
            or not 0 <= duration <= PROBE_DURATION_CEILING_MS):
        _refuse("probe row duration is outside its bound")
    realization = value["realization_digest"]
    if realization is not None:
        validate_digest256(realization)
    return value


def validate_qualification_elapsed(value: object) -> int:
    """The schema's ``$defs/elapsedMs``: the record's measured elapsed milliseconds,
    zero to the default qualification budget made exclusive on its last millisecond."""
    if (type(value) is not int or isinstance(value, bool)
            or not 0 <= value <= QUALIFICATION_ELAPSED_CEILING_MS):
        _refuse("elapsed time is outside its bound")
    return value


def validate_qualification_record_shape(value: object) -> dict[str, Any]:
    """The schema's root: the whole closed `/3` record shape (the definition home's word). The
    runtime projections themselves stay open maps at this layer — their closed fact sets are
    `/2` semantics the harness cannot draw — and every digest field is the one digest lexeme."""
    if type(value) is not dict:
        _refuse("record shape is unknown")
    base = (_RECORD_BODY_FIELDS - {"daemon_id?"}) | {"qualification_record_digest"}
    if set(value) != base | ({"daemon_id"} if "daemon_id" in value else frozenset()):
        _refuse("record field set is unknown")
    if value["schema"] != QUALIFICATION_RECORD_SCHEMA:
        _refuse("record schema differs")
    validate_run_binding_shape(value["run_binding"])
    profile_id = value["profile_id"]
    if type(profile_id) is not str or profile_id not in spike_plan.NETWORK_PROFILES:
        _refuse("record profile is unknown")
    if type(value["runtime"]) is not dict or type(value["semantic_authority"]) is not dict:
        _refuse("record projections are unknown")
    for member in ("authority_digest", "policy_digest", "implementation_digest",
                   "probe_image_id", "execution_binding_digest", "qualification_record_digest"):
        validate_digest256(value[member])
    if "daemon_id" in value:
        validate_daemon_identity_shape(value["daemon_id"])
    validate_qualification_image_shape(value["qualification_image"])
    validate_spike_images_shape(value["spike_images"])
    if type(value["probes"]) is not list or len(value["probes"]) != len(qualification.PROBE_ORDER):
        _refuse("record must contain five probes")
    for probe in value["probes"]:
        validate_probe_row_shape(probe)
    if type(value["cleanup"]) is not dict or value["cleanup"] != _CLEANUP_PROOF:
        _refuse("record cleanup proof differs")
    validate_qualification_elapsed(value["qualification_elapsed_ms"])
    return value


def validate_run_record(value: object) -> dict[str, Any]:
    """Validate the exact closed `/3` record and fail closed on every malformed form.

    The record's closed shapes are the conformance harness's word (the schema's own
    annotated callables above, `validate_qualification_record_shape` down to every row);
    this admission composes on them and then owns the semantic rules no schema can
    express (the `/2` runtime and daemon-identity admission, the semantic-authority
    projection, the platform cross-fields, the byte-bounded identifiers, the policy and
    implementation bindings, and the probe order and outcome equality).

    Loading additionally verifies the run-generation lineage; the verification itself
    (below) is lineage-free so the record stays a pure function of one attempt.
    """
    if type(value) is not dict:
        raise authority.GenericQualificationError("run qualification record shape is unknown")
    try:
        validate_qualification_record_shape(value)
    except ValueError as exc:
        raise authority.GenericQualificationError(
            "run qualification record schema or shape is unknown") from exc
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
    try:
        validate_digest256(row["implementation_digest"])
    except ValueError as exc:
        raise authority.GenericQualificationError(
            "run qualification record implementation digest differs") from exc
    if ("daemon_id" in row) != ("daemon_id" in runtime) or (
            "daemon_id" in row and row["daemon_id"] != runtime["daemon_id"]):
        raise authority.GenericQualificationError("run qualification record daemon identity differs")
    image_body = row["qualification_image"]
    if type(image_body) is not dict or "repo_digest" not in image_body:
        raise authority.GenericQualificationError("run qualification record probe image digest proof is absent")
    probe_digest_proof = _repo_digest(image_body["repo_digest"], "probe image")
    if probe_digest_proof.rsplit("@", 1)[1] != probe_image.pinned_probe_image().index_digest:
        # (registration review, blocking) the proof may name any repository the daemon
        # observed carrying the pinned content, but its digest part must be the
        # pinned index digest — never another digest, however well-formed the record
        raise authority.GenericQualificationError(
            "run qualification record probe image digest proof differs")
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
    # the validated field set minus the record digest's own member: the body the digest
    # closes over never carries the digest itself
    expected = set(row) - {"qualification_record_digest"}
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


def _read_probe_inspect(argv: tuple, runner: process.Runner, environment: dict,
                        deadline: float, clock: Callable[[], float]) -> object | None:
    """One bounded ``docker image inspect`` read: its parsed JSON object, or ``None``
    when the named reference yields no usable document (a failed read, a nonzero
    exit, a truncated or oversized answer, or an unreadable JSON object). No
    admission happens here - the caller owns the content rules."""
    try:
        result = runner.run(argv, timeout_seconds=min(10.0, deadline - clock()),
                            output_limit_bytes=authority.MAX_POINTER_BYTES, env=environment)
    except Exception:
        return None
    try:
        result = qualification._command_result(result, "the trusted probe image inspection")
    except qualification.GenericQualificationFailure:
        return None
    if result.timed_out or result.output_limited or result.returncode != 0:
        return None
    try:
        return admission.json_object(result.stdout.data)
    except Exception:
        return None


def _locate_probe_image_by_repository_digest(runtime: Mapping[str, Any], pin: Any,
                                             *, runner: process.Runner, environment: dict,
                                             deadline: float, clock: Callable[[], float]) -> str | None:
    """The one bounded ``docker image list --digests`` fallback (drift review,
    non-blocking 6): when the pinned reference finds no local image, the digest is
    still the image's content identity, so the list may name it under some other
    repository - a proxy's own pull name - whose reported repository digest is the
    pin's. Returns the located row's digest-sealed reference to inspect -
    ``<repository>@<pinned index digest>`` - or ``None`` when no row carries the
    pinned digest, every candidate name is unusable (a lexeme-screened ``<none>``
    row, registration review non-blocking 1), or the list is unusable (truncated,
    oversized, or unparsable: never a partial admission). The row's reported image
    ID is never trusted or returned: the classic store's ID is the config digest,
    not the index digest (the digest-sealed reference is the one form both image
    stores answer — the coordinator's real mirror pull on Docker Desktop 29.7.2/
    containerd witnessed it). More than one matching repository is fine (same
    content): only repositories the repo-digest lexeme admits stay, and the choice
    is deterministic, the sorted-first. The located reference is re-inspected by the
    ordinary admission rules, which alone decide."""
    _remain(deadline, clock, "the trusted probe image digest listing")
    argv = (runtime["docker_cli"], "image", "list", "--digests", "--no-trunc", "--format",
            PROBE_IMAGE_LIST_FORMAT)
    try:
        result = runner.run(argv, timeout_seconds=min(10.0, deadline - clock()),
                            output_limit_bytes=MAX_IMAGE_LIST_BYTES, env=environment)
    except Exception:
        return None
    try:
        # the listing's bounded read re-enters through the authority's pointer
        # admission at its OWN fetch bound, never the single-pointer 64 KiB one:
        # a digest listing the 256 KiB fetch legitimately returns (hundreds of
        # rows) must not be refused as malformed, while an answer past the bound
        # refuses the same closed way
        result = authority._admit_command_result(
            result, MAX_IMAGE_LIST_BYTES, "the trusted probe image digest listing")
        if result.timed_out or result.output_limited or result.returncode != 0:
            return None
        lines = result.stdout.data.strip().split(b"\n")
        if not lines or lines == [b""]:
            return None
        if len(lines) > MAX_IMAGE_LIST_ROWS:
            return None
        repositories: set[str] = set()
        for line in lines:
            try:
                row = admission.json_object(line)
            except Exception:
                return None
            if (type(row.get("Repository")) is not str
                    or row.get("Digest") != pin.index_digest):
                continue
            repositories.add(row["Repository"])
        # (registration review, non-blocking 1) a row whose repository name is
        # unusable — a `<none>` dangling name, which sorts before every real one —
        # cannot name a reference any daemon would answer, so the repo-digest lexeme
        # on the combined reference screens it out before the sorted-first choice,
        # never leaving the attempt to die on a name that cannot be re-inspected
        # while a real mirror row exists
        usable = sorted(
            repository for repository in repositories
            if _REPO_DIGEST.fullmatch(repository + "@" + pin.index_digest) is not None)
        if not usable:
            return None
        # deterministic among equal-content mirror names: the sorted-first
        return usable[0] + "@" + pin.index_digest
    except Exception:
        return None
    return None


def admit_probe_image(runtime: Mapping[str, Any], *, config: Path, runner: process.Runner,
                      deadline: float, clock: Callable[[], float]) -> tuple[dict[str, Any], str]:
    """Verify the trusted probe image per spec section 4.3, under the pinned endpoint.

    One ``docker image inspect`` of the exact pinned reference must show the pinned
    index digest in some ``RepoDigests`` entry - under any repository name, because
    the digest is the image's content identity and the repository name is not (a
    mirror or proxy pull reports the digest under its own name), never on the
    daemon-local ``Id`` - plus the server-native platform and no declared volumes;
    anything else, including a locally built, loaded or retagged image (which carries
    no ``RepoDigests`` entry with the pinned digest), is ``probe-image-untrusted``.

    When the pinned reference finds no local image at all, one bounded
    ``docker image list --digests`` locates the pinned digest under any repository
    name and the admission inspects the located row's digest-sealed reference —
    ``<repository>@<pinned digest>``, the one form both image stores answer; the
    row's reported ID is never trusted, because the classic store's ID is the
    config digest, not the index digest — through the same content rules (drift
    review, non-blocking 6; the coordinator's real mirror pull on Docker Desktop
    29.7.2's containerd store witnessed the digest-sealed reference answering
    where the pinned one does not).
    """
    _remain(deadline, clock, "the trusted probe image inspection")
    pin = probe_image.pinned_probe_image()
    environment = {"LANG": "C", "LC_ALL": "C", "DOCKER_HOST": runtime["endpoint"],
                   "DOCKER_CONFIG": str(config)}
    detail = ("the pinned probe image is not admitted locally; pull " + pin.reference
              + ", then qualify")
    argv = (runtime["docker_cli"], "image", "inspect", "--format",
            PROBE_IMAGE_INSPECT_FORMAT, pin.reference)
    document = _read_probe_inspect(argv, runner, environment, deadline, clock)
    if document is None:
        located = _locate_probe_image_by_repository_digest(
            runtime, pin, runner=runner, environment=environment,
            deadline=deadline, clock=clock)
        if located is None:
            raise _RunFailure(RunQualificationReason.PROBE_IMAGE_UNTRUSTED, detail)
        argv = (*argv[:-1], located)
        document = _read_probe_inspect(argv, runner, environment, deadline, clock)
        if document is None:
            raise _RunFailure(RunQualificationReason.PROBE_IMAGE_UNTRUSTED, detail)
    try:
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
    matching = sorted(
        entry for entry in digests
        if _REPO_DIGEST.fullmatch(entry) is not None
        and entry.rsplit("@", 1)[1] == pin.index_digest)
    if not matching:
        # content identity, not repository identity: no entry of any repository
        # name carries the pinned index digest, so the local image is not the pinned
        # one
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
    # (registration review, blocking) the digest proof member is the observation
    # the daemon itself made for the admitted image — the sorted-first
    # ``RepoDigests`` entry carrying the pinned digest, whatever repository name it
    # was reported under, and already shape-checked by the matching screen — never
    # a synthetic value the daemon never observed; the loader checks its digest part
    # against the pin
    row["repo_digest"] = matching[0]
    return row, image


EffectsStage = Callable[[authority.ResolvedGenericAuthority, "planner.ResolvedDockerOperation | None", str],
                        execution.GenericEffects]


def _concrete_effects(resolved: authority.ResolvedGenericAuthority) \
        -> execution.GenericEffects:
    return execution.concrete_effects(Path(resolved.runtime["docker_cli"]),
                                      dict(resolved.environment), resolved.work_directory)


def _qualification_budget(value: object) -> float:
    """The one bound check of a qualification budget in seconds (``None`` is the default)."""
    if value is None:
        return float(DEFAULT_QUALIFICATION_SECONDS)
    if type(value) is not float and type(value) is not int:
        raise ValueError("run qualification budget is malformed")
    if not 0 < value <= DEFAULT_QUALIFICATION_SECONDS:
        raise ValueError("run qualification budget is outside its bound")
    return float(value)


def _prepared_store(sandbox_root: Path | None) -> tuple[Path, "common.ExecutionStore"]:
    """The sandbox root and its execution store, scaffolding and journal store prepared."""
    root = (common.sandbox_home() if sandbox_root is None
            else common.canonical_authority_path(Path(sandbox_root)))
    store = common.ExecutionStore(root)
    store.prepare_execution_scaffolding()
    generic_journal.GenericJournalStore(store).prepare()
    return root, store


class RuntimeLease:
    """Proof that the shared runtime lock is held by the caller: made only by
    :func:`hold_runtime_lock`, valid until that context exits. ``deadline`` is the one bound
    (on the clock the lease was taken with) that every step run under the lease shares."""

    __slots__ = ("root", "held", "deadline")

    def __init__(self, root: Path, deadline: float) -> None:
        self.root, self.held, self.deadline = root, True, deadline


@contextmanager
def hold_runtime_lock(
    *, sandbox_root: Path | None = None, clock: Callable[[], float] = time.monotonic,
) -> Iterator[RuntimeLease]:
    """Take the shared runtime lock without waiting and hold it for the context: an attempt or a
    settle given the lease runs under it instead of taking its own, so a caller can order its own
    durable steps (the transaction's reservation) inside the lock. The lease carries the one
    host deadline of the whole qualify: a settle and the attempt run under it each spend what is
    left of it. A lock another holder has raises :class:`common.RuntimeLockBusy` at once."""
    root, store = _prepared_store(sandbox_root)
    lease = RuntimeLease(root, clock() + DEFAULT_QUALIFICATION_SECONDS)
    with common.runtime_lock(store, lease.deadline, clock=clock, blocking=False):
        try:
            yield lease
        finally:
            lease.held = False


def _lease_refusal(lease: object) -> RunQualificationFailed | None:
    if type(lease) is RuntimeLease and lease.held:
        return None
    return RunQualificationFailed(RunQualificationReason.INVALID_INPUT,
                                  "run qualification runtime lease is not held")


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
    lease: RuntimeLease | None = None,
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
    built. With a ``lease`` the caller already holds the runtime lock (``hold_runtime_lock``) and
    the attempt runs under it instead of taking its own, in the lease's sandbox root.
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
        budget = _qualification_budget(qualification_seconds)
        docker_cli = Path(docker_cli)
    except (ValueError, TypeError, OSError) as exc:
        return RunQualificationFailed(RunQualificationReason.INVALID_INPUT, str(exc))
    refused = None if lease is None else _lease_refusal(lease)
    if refused is not None:
        return refused
    began = clock()
    deadline = began + budget if lease is None else min(began + budget, lease.deadline)
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
                               began=began, clock=clock, stage=stage, lease=lease)
    except _RunFailure as exc:
        if clock() >= deadline:
            # Expiry anywhere reports the deadline reason, including
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


def _recover_prior_journals(
    store: "common.ExecutionStore", root: Path, *, binding_seed: Mapping[str, Any],
    profile_id: str, docker_cli: Path,
    runtime_inspector: Callable[..., authority.GenericRuntimeInspection],
    effects_factory: EffectsStage | None, deadline: float, clock: Callable[[], float],
    stage: list[str],
) -> tuple[Mapping[str, Any], Path, Path, str, EffectsStage]:
    """Under the already-held shared runtime lock: observe the authority once, create the
    run-scoped scaffolding directories, and recover every prior journal on the pinned daemon.
    Shared by the attempt (which then admits and probes) and by the settling of an interrupted
    attempt, so recovery has one owner. Returns the observed facts, the profile's private config
    directory, the work directory, the seed digest and the effects selector."""
    # Initial discovery: one observation (context, version, info including
    # ``daemon_id``) that resolves the endpoint once — reads only, no admission.
    # Recovery precedes the new attempt's admission, so a capability or
    # implementation refusal must never skip journal recovery.
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
                             "attempt": binding_seed})
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
    return observed, config, work, seed, selected_effects


def settle_interrupted_attempt(
    *,
    project_id: str,
    run_id: str,
    generation: int,
    attempt_id: str,
    profile_id: str,
    docker_cli: Path,
    lease: RuntimeLease,
    on_settled: Callable[[], None],
    qualification_seconds: float | None = None,
    runtime_inspector: Callable[..., authority.GenericRuntimeInspection] = authority.inspect_observed_runtime,
    effects_factory: EffectsStage | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> bool | RunQualificationFailed:
    """Settle one open attempt row no live attempt drives (Empirica 7.2 §5), under the runtime
    lock the caller holds (``lease``, from :func:`hold_runtime_lock`): an open row seen under the
    lock is dead, because a live attempt keeps the lock from its reservation to its outcome.

    Recovers every prior journal exactly as an attempt does — the same owner, the same pinned
    daemon — and runs ``on_settled`` (the transaction's durable closing of the row) still under
    the lock, so no new attempt can start between the recovery and the closing. Returns ``True``
    once settled with time left; the deadline failure when the closing landed but the qualify's
    bound is spent (the row is closed, nothing more may be reserved under this call); or one
    closed failure when recovery could not run (the row then stays open for a later settle). It
    never resumes or reuses the dead attempt's effect, and never publishes a record."""
    try:
        for name, value in (("project_id", project_id), ("run_id", run_id),
                            ("attempt_id", attempt_id)):
            RunBinding._identifier(value, name)
        RunBinding._counter(generation, "generation")
        if type(profile_id) is not str or profile_id not in spike_plan.NETWORK_PROFILES:
            raise ValueError("run qualification profile is unknown")
        budget = _qualification_budget(qualification_seconds)
        docker_cli = Path(docker_cli)
    except (ValueError, TypeError, OSError) as exc:
        return RunQualificationFailed(RunQualificationReason.INVALID_INPUT, str(exc))
    refused = _lease_refusal(lease)
    if refused is not None:
        return refused
    deadline = min(clock() + budget, lease.deadline)
    stage: list[str] = ["the prior journal recovery"]
    try:
        store = common.ExecutionStore(lease.root)
        _recover_prior_journals(
            store, lease.root, binding_seed={
                "project_id": project_id, "run_id": run_id, "generation": generation,
                "attempt_id": attempt_id},
            profile_id=profile_id, docker_cli=docker_cli,
            runtime_inspector=runtime_inspector, effects_factory=effects_factory,
            deadline=deadline, clock=clock, stage=stage)
        on_settled()
        if clock() >= deadline:
            # The closing is durable, but the qualify's one bound is spent: the caller must not
            # reserve a fresh attempt that has no time to run.
            return RunQualificationFailed(
                RunQualificationReason.DEADLINE_EXCEEDED,
                "run qualification deadline expired after the settle")
        return True
    except _RunFailure as exc:
        return RunQualificationFailed(exc.reason, exc.detail, exc.probe)
    except Exception as exc:
        if clock() >= deadline:
            return RunQualificationFailed(
                RunQualificationReason.DEADLINE_EXCEEDED,
                "run qualification deadline expired at: " + stage[0])
        return RunQualificationFailed(RunQualificationReason.CAPABILITY_UNAVAILABLE,
                                      "run qualification recovery failed: " + str(exc))


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
    lease: RuntimeLease | None = None,
) -> RunQualificationBound:
    root, store = (_prepared_store(sandbox_root) if lease is None
                   else (lease.root, common.ExecutionStore(lease.root)))
    with (nullcontext() if lease is not None
          else common.runtime_lock(store, deadline, clock=clock)):
        observed, config, work, seed, selected_effects = _recover_prior_journals(
            store, root, binding_seed=binding.to_data(), profile_id=profile_id,
            docker_cli=docker_cli, runtime_inspector=runtime_inspector,
            effects_factory=effects_factory, deadline=deadline, clock=clock, stage=stage)
        # --- admission of the new attempt, strictly after recovery -------------------
        # Capability admission of the same observed facts: a refusal here keeps its own
        # kept reason and has already recovered its prior journals.
        stage[0] = "the authority admission"
        runtime = _admit_observed_facts(observed)
        # The binding's server major (operator decision, drift "Server version") is
        # derived once, here at the facts' admission: a daemon whose version has no
        # derivable major — a private build, a garbage answer — ends the attempt with
        # the capability reason before any Docker image command runs, never after a
        # whole probe run (drift review, non-blocking 1). The builder re-derives the
        # same value for the execution binding; within one attempt the exact version
        # must still match (the after-probe re-observation).
        try:
            server_version_major(runtime["server_version"])
        except authority.GenericQualificationError as exc:
            raise _RunFailure(RunQualificationReason.CAPABILITY_UNAVAILABLE,
                              str(exc)) from exc
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
