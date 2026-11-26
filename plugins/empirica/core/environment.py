"""The pure environment proposal model for the Empirica 7.2 environment step (spec ``§4.1``, ``§5``).

An environment proposal is the author's closed four-field request for a host Docker qualification.
This module owns only pure admission decisions — closed shape, bounds, canonicalization, digest and
the host profile ceiling (``§4.4``). It performs no I/O and owns no Docker effects; image reference
syntax stays with :mod:`core.spike_plan` and is reused, never duplicated here.
"""
from __future__ import annotations

import re
from typing import Any

from . import spike_plan
from .canonical import canonical_digest
from .contract_bounds import (
    ENVIRONMENT_HISTORY_MAX_ITEMS,
    ENVIRONMENT_PROPOSAL_PREPARATION_MAX_ITEMS,
    ENVIRONMENT_PROPOSAL_PREPARATION_MAX_LENGTH,
    ENVIRONMENT_PROPOSAL_RATIONALE_MAX_LENGTH,
    ENVIRONMENT_PROPOSAL_RATIONALE_PATTERNS,
    ENVIRONMENT_PROPOSAL_SPIKE_IMAGES_MAX_ITEMS,
    QUALIFICATION_ATTEMPTS_MAX_ITEMS,
)

PROPOSAL_SCHEMA = "empirica.environment-proposal/1"

#: The bounds are the contract's own word (contracts/empirica/v2/state.schema.json, regenerated straight from
#: its annotated definitions into core.contract_bounds by ``make contract-bounds``): neither side rewrites the
#: number, and drift fails ``make contract-check``.
MAX_SPIKE_IMAGES = ENVIRONMENT_PROPOSAL_SPIKE_IMAGES_MAX_ITEMS
MAX_PREPARATION_STEPS = ENVIRONMENT_PROPOSAL_PREPARATION_MAX_ITEMS
MAX_PREPARATION_CHARS = ENVIRONMENT_PROPOSAL_PREPARATION_MAX_LENGTH
MAX_RATIONALE_CHARS = ENVIRONMENT_PROPOSAL_RATIONALE_MAX_LENGTH
# State bounds (spec §4.2): immutable earlier revisions and started host attempts per run.
MAX_ENVIRONMENT_HISTORY = ENVIRONMENT_HISTORY_MAX_ITEMS
MAX_QUALIFICATION_ATTEMPTS = QUALIFICATION_ATTEMPTS_MAX_ITEMS

_BODY_FIELDS = frozenset({"profile_id", "spike_images", "preparation", "rationale"})
# C0 and C1 controls never render honestly; free text must never carry them.
_CONTROL_CHARACTERS = re.compile("[\x00-\x1f\x7f-\x9f]")
# The contract writes blankness itself: the explicit cross-host whitespace class of the environmentProposal
# definition (U+FEFF inside it, where JavaScript's \S says blank and Python's strip does not). No host language's
# \s or strip semantics decides what a blank step or rationale is — the pattern is the one word.
_NON_BLANK = re.compile(ENVIRONMENT_PROPOSAL_RATIONALE_PATTERNS[0])


class EnvironmentProposalError(ValueError):
    """The environment proposal is not the exact closed ``empirica.environment-proposal/1`` body."""


def _body(value: object) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != _BODY_FIELDS:
        raise EnvironmentProposalError("environment proposal shape is not closed")
    return value


def _profile_id(value: object) -> str:
    if type(value) is not str or value not in spike_plan.NETWORK_PROFILES:
        raise EnvironmentProposalError("environment proposal profile is unknown")
    return value


def _spike_images(value: object) -> list[str]:
    if type(value) is not list or not 1 <= len(value) <= MAX_SPIKE_IMAGES:
        raise EnvironmentProposalError("environment proposal spike images must be a bounded nonempty array")
    images: list[str] = []
    for item in value:
        try:
            images.append(spike_plan.validate_local_image_reference(item))
        except spike_plan.SpikePlanError as exc:
            raise EnvironmentProposalError("environment proposal spike image is malformed") from exc
    if len(set(images)) != len(images):
        raise EnvironmentProposalError("environment proposal spike images are duplicated")
    return images


def _text(value: object, maximum: int, message: str) -> str:
    if type(value) is not str:
        raise EnvironmentProposalError("environment proposal text must be a string")
    if _CONTROL_CHARACTERS.search(value):
        raise EnvironmentProposalError("environment proposal text carries control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        # A JSON-escaped lone surrogate is not digestible and never renders honestly.
        raise EnvironmentProposalError("environment proposal text must be UTF-8") from exc
    if len(value) > maximum:
        raise EnvironmentProposalError("environment proposal text exceeds its character bound")
    if not _NON_BLANK.search(value):
        raise EnvironmentProposalError(message)
    return value


def _preparation(value: object) -> list[str]:
    if type(value) is not list or not 1 <= len(value) <= MAX_PREPARATION_STEPS:
        raise EnvironmentProposalError("environment proposal preparation must be a bounded nonempty array")
    return [
        _text(item, MAX_PREPARATION_CHARS, "environment proposal preparation step is blank")
        for item in value
    ]


def _rationale(value: object) -> str:
    return _text(value, MAX_RATIONALE_CHARS, "environment proposal rationale is blank")


def validate_proposal(value: object) -> dict[str, Any]:
    """Validate the exact closed proposal body and return it, strings as given, images in given order."""
    body = _body(value)
    return {
        "profile_id": _profile_id(body["profile_id"]),
        "spike_images": _spike_images(body["spike_images"]),
        "preparation": _preparation(body["preparation"]),
        "rationale": _rationale(body["rationale"]),
    }


def validate_action_environment(value: object) -> dict[str, object]:
    """The closed ``environment`` author action wrapper — the contract-conformance harness's validator
    (``x-core-validator``, ADR-0075): exactly ``{kind: "environment", payload: <proposal body>}``
    with the payload the closed ``empirica.environment-proposal/1`` body at its own word."""
    if type(value) is not dict or frozenset(value) != frozenset(("kind", "payload")):
        raise EnvironmentProposalError("environment action is not closed")
    if type(value["kind"]) is not str or value["kind"] != "environment":
        raise EnvironmentProposalError("environment action kind is not environment")
    validate_proposal(value["payload"])
    return value


def validate_action_qualify(value: object) -> dict[str, object]:
    """The closed ``qualify`` author action wrapper — the contract-conformance harness's validator
    (``x-core-validator``, ADR-0075): exactly ``{kind: "qualify"}``, carrying no payload."""
    if type(value) is not dict or frozenset(value) != frozenset(("kind",)):
        raise EnvironmentProposalError("qualify action is not closed")
    if type(value["kind"]) is not str or value["kind"] != "qualify":
        raise EnvironmentProposalError("qualify action kind is not qualify")
    return value


def canonical_proposal(value: object) -> dict[str, Any]:
    """The validated proposal with an order-insensitive ``spike_images`` member (sorted, as given)."""
    body = validate_proposal(value)
    body["spike_images"] = sorted(body["spike_images"])
    return body


def proposal_digest(value: object) -> str:
    """The canonical content address of the proposal body, bound to its one schema identity."""
    return canonical_digest({"schema": PROPOSAL_SCHEMA, **canonical_proposal(value)})


def profile_allowed(profile_id: object, ceiling: object) -> bool:
    """Whether ``profile_id`` stays under the host profile ceiling (spec ``§4.4``); unknown facts allow nothing."""
    if type(profile_id) is not str or profile_id not in spike_plan.NETWORK_PROFILES:
        return False
    if ceiling is None:
        return profile_id == spike_plan.ISOLATED_PROFILE
    if type(ceiling) is not str:
        return False  # a non-str ceiling can never name a profile; no equality comparison runs
    if ceiling == spike_plan.ISOLATED_PROFILE:
        return profile_id == spike_plan.ISOLATED_PROFILE
    if ceiling == spike_plan.NETWORKED_PROFILE:
        return True
    return False


def next_revision(history_revisions: object) -> int:
    """The monotonic next proposal revision for a run: 1 for no history, else one past the highest seen."""
    if type(history_revisions) not in (list, tuple):
        raise EnvironmentProposalError("environment proposal history must be a sequence of revisions")
    highest = 0
    for item in history_revisions:
        if type(item) is not int or item < 1:
            raise EnvironmentProposalError("environment proposal history is malformed")
        highest = max(highest, item)
    return highest + 1
