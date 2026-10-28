"""Pure closed author draft and host sealing boundary for one Docker spike."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canonical import canonical_digest
from .freshness import validate_relative_posix_path
from . import spike_plan

DRAFT_SCHEMA = "empirica.spike-plan-draft/2"
_ROOT_FIELDS = frozenset({"schema", "image", "argv", "inputs", "environment"})


class SpikePlanDraftError(ValueError):
    """The author draft or host-supplied sealed facts are not exact and closed."""


@dataclass(frozen=True, slots=True)
class SpikePlanDraft:
    image: str
    argv: tuple[str, ...]
    inputs: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]

    def to_data(self) -> dict[str, Any]:
        return {
            "schema": DRAFT_SCHEMA,
            "image": self.image,
            "argv": list(self.argv),
            "inputs": list(self.inputs),
            "environment": dict(self.environment),
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_data())


def _closed(value: object, fields: frozenset[str], path: str) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != fields:
        raise SpikePlanDraftError(f"{path} shape is not closed")
    return value


def _input_path(value: object) -> str:
    if type(value) is not str or not value:
        raise SpikePlanDraftError("draft input path must be nonempty text")
    directory = value.endswith("/")
    candidate = value[:-1] if directory else value
    try:
        validate_relative_posix_path(candidate)
    except ValueError as exc:
        raise SpikePlanDraftError("draft input is not repository-relative") from exc
    if len(value.encode("utf-8")) > spike_plan.MAX_PATH_BYTES:
        raise SpikePlanDraftError("draft input path exceeds its byte bound")
    if ".git" in candidate.split("/"):
        raise SpikePlanDraftError(".git cannot be captured")
    return value


def _canonical_inputs(value: object) -> tuple[str, ...]:
    if type(value) is not list or len(value) > spike_plan.MAX_STAGING_FILES:
        raise SpikePlanDraftError("draft inputs must be a bounded array")
    checked = tuple(sorted(_input_path(item) for item in value))
    if len(set(checked)) != len(checked):
        raise SpikePlanDraftError("draft inputs must be duplicate-free")
    bases = tuple(item[:-1] if item.endswith("/") else item for item in checked)
    if len(set(bases)) != len(bases):
        raise SpikePlanDraftError("draft inputs have duplicate canonical roots")
    for index, base in enumerate(bases):
        for other in bases[index + 1:]:
            if other.startswith(base + "/"):
                raise SpikePlanDraftError("draft inputs must be a canonical non-overlapping closure")
    return checked


def _canonical_argv(value: object) -> tuple[str, ...]:
    if type(value) is not list or not 1 <= len(value) <= spike_plan.MAX_ARGV:
        raise SpikePlanDraftError("draft argv must be a bounded nonempty vector")
    checked = []
    for index, item in enumerate(value):
        try:
            checked.append(spike_plan._bounded_text(
                item, f"argv[{index}]", nonempty=index == 0))
        except spike_plan.SpikePlanError as exc:
            raise SpikePlanDraftError("draft argv is malformed") from exc
    return tuple(checked)


def canonicalize_spike_plan_draft(value: object) -> SpikePlanDraft:
    """Validate the exact five-field author contract without filesystem or Docker effects."""
    try:
        root = _closed(value, _ROOT_FIELDS, "draft")
        if root["schema"] != DRAFT_SCHEMA:
            raise SpikePlanDraftError("draft schema is not supported")
        image = spike_plan.validate_local_image_reference(root["image"])
        argv = _canonical_argv(root["argv"])
        inputs = _canonical_inputs(root["inputs"])
        environment = spike_plan.environment_from_data(root["environment"])
        return SpikePlanDraft(image, argv, inputs, environment)
    except SpikePlanDraftError:
        raise
    except (spike_plan.SpikePlanError, TypeError, ValueError, UnicodeError) as exc:
        raise SpikePlanDraftError("draft is malformed") from exc


def _covers(declarations: tuple[str, ...], path: str) -> bool:
    return any(
        path == item.rstrip("/") or path.startswith(item.rstrip("/") + "/")
        for item in declarations
    )


def seal_spike_plan(
    draft: SpikePlanDraft,
    resolved_image: spike_plan.ResolvedImage,
    captured_files: tuple[spike_plan.CapturedFile, ...],
    *,
    engine_ncpu: int,
    network_profile: str,
) -> spike_plan.SealedSpikePlan:
    """Seal host-only immutable facts; callers own image resolution and path expansion."""
    if type(draft) is not SpikePlanDraft:
        raise SpikePlanDraftError("sealing requires an exact validated draft")
    if canonicalize_spike_plan_draft(draft.to_data()) != draft:
        raise SpikePlanDraftError("sealing draft is not canonical")
    if type(resolved_image) is not spike_plan.ResolvedImage:
        raise SpikePlanDraftError("sealing requires an exact resolved image")
    if resolved_image.requested_reference != draft.image:
        raise SpikePlanDraftError("resolved image reference differs from the draft")
    if (type(captured_files) is not tuple
            or any(type(item) is not spike_plan.CapturedFile for item in captured_files)):
        raise SpikePlanDraftError("sealing requires exact captured files")
    if tuple(sorted(captured_files, key=lambda item: item.path)) != captured_files:
        raise SpikePlanDraftError("captured files must be in canonical path order")
    if any(not _covers(draft.inputs, item.path) for item in captured_files):
        raise SpikePlanDraftError("captured file falls outside declared inputs")
    if not draft.inputs and captured_files:
        raise SpikePlanDraftError("an empty declaration cannot capture files")
    if type(engine_ncpu) is not int or isinstance(engine_ncpu, bool) or engine_ncpu <= 0:
        raise SpikePlanDraftError("engine NCPU must be positive")
    if network_profile not in spike_plan.NETWORK_PROFILES:
        raise SpikePlanDraftError("network profile is not framework-approved")
    capture_digest = canonical_digest([item.to_data() for item in captured_files])
    try:
        return spike_plan.SealedSpikePlan(
            draft.digest, resolved_image, draft.argv, draft.inputs, captured_files,
            capture_digest, draft.environment, engine_ncpu, min(2, engine_ncpu),
            network_profile,
            spike_plan.FRAMEWORK_SPIKE_POLICY.digest)
    except spike_plan.SpikePlanError as exc:
        raise SpikePlanDraftError("host facts do not form an exact sealed plan") from exc
