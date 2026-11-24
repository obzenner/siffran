"""The wire-view validators the 7.2 registration names (ADR-0075, ``x-core-validator``).

The response's read-only run projections — ``environmentView`` and ``qualificationView`` (Empirica
7.2 §5.1), composed by nobody yet on the wire — and their members are the contract's own word. These
functions are the plugin's other, independent declaration of the same shapes, with every vocabulary
and bound read from its owner, never restated: the environment states from ``core.run``, the
profiles from ``core.spike_plan``, the digest form from ``core.freshness``, the attempt ceiling from
the contract-generated ``core.environment.MAX_QUALIFICATION_ATTEMPTS``, the closed failure-reason
set from ``application.generic_spike.QUALIFICATION_FAILURE_REASONS``, and the probe names from the
support-contract-pinned ``core.run.QUALIFICATION_PROBE_NAMES``. The contract conformance harness
holds the schema and this side to agreement in both directions; nothing else imports this module —
it is a declaration, not a dependency."""
from __future__ import annotations

from typing import Any, Mapping

from core import environment, freshness, run, spike_plan
from core.contract_bounds import ENVIRONMENT_MEMBER_REVISION_MINIMUM
from . import generic_spike

_ENVIRONMENT_VIEW_FIELDS = frozenset(("revision", "state", "profile_id", "spike_image_count",
                                       "digest"))
_QUALIFICATION_VIEW_FIELDS = frozenset(("attempts_used", "attempts_max", "active", "last_failure"))
_BINDING_FIELDS = frozenset(("seq", "profile_id", "record_digest"))
_LAST_FAILURE_PROBE_FIELDS = frozenset(("reason", "probe"))


def validate_environment_view(value: Any) -> Mapping[str, Any]:
    """The run's read-only environment reference (``response#/$defs/environmentView``): a closed
    five-member projection of the current revision and its proposal body."""
    if not isinstance(value, Mapping) or frozenset(value) != _ENVIRONMENT_VIEW_FIELDS:
        raise ValueError("environment view is not the closed projection")
    revision = value["revision"]
    if type(revision) is not int or revision < ENVIRONMENT_MEMBER_REVISION_MINIMUM:
        raise ValueError("environment view revision is malformed")
    if value["state"] not in run.ENVIRONMENT_STATES:
        raise ValueError("environment view state is unknown")
    if type(value["profile_id"]) is not str or value["profile_id"] not in spike_plan.NETWORK_PROFILES:
        raise ValueError("environment view profile is unknown")
    count = value["spike_image_count"]
    if (type(count) is not int
            or not 1 <= count <= environment.MAX_SPIKE_IMAGES):
        raise ValueError("environment view spike image count is malformed")
    freshness.validate_digest256(value["digest"])
    return value


def validate_qualification_view(value: Any) -> Mapping[str, Any]:
    """The run's read-only qualification accounting (``response#/$defs/qualificationView``): the
    attempts used against the one per-run ceiling, the installed binding reference, and the last
    failure with its closed reason, naming the failed probe exactly when one failed."""
    if not isinstance(value, Mapping) or frozenset(value) != _QUALIFICATION_VIEW_FIELDS:
        raise ValueError("qualification view is not the closed projection")
    used = value["attempts_used"]
    if type(used) is not int or not 0 <= used <= environment.MAX_QUALIFICATION_ATTEMPTS:
        raise ValueError("qualification view attempts used are malformed")
    ceiling = value["attempts_max"]
    if type(ceiling) is not int or ceiling != environment.MAX_QUALIFICATION_ATTEMPTS:
        raise ValueError("qualification view attempt ceiling is not the run's own")
    active = value["active"]
    if active is not None:
        if not isinstance(active, Mapping) or frozenset(active) != _BINDING_FIELDS:
            raise ValueError("qualification view active binding is malformed")
        if (type(active["seq"]) is not int
                or not 1 <= active["seq"] <= environment.MAX_QUALIFICATION_ATTEMPTS):
            raise ValueError("qualification view active binding sequence is malformed")
        if type(active["profile_id"]) is not str \
                or active["profile_id"] not in spike_plan.NETWORK_PROFILES:
            raise ValueError("qualification view active binding profile is unknown")
        freshness.validate_digest256(active["record_digest"])
    failure = value["last_failure"]
    if failure is not None:
        if not isinstance(failure, Mapping):
            raise ValueError("qualification view last failure is malformed")
        reason = failure.get("reason")
        if (type(reason) is not str
                or reason not in generic_spike.QUALIFICATION_FAILURE_REASONS):
            raise ValueError("qualification view last failure reason is unknown")
        probe = failure.get("probe")
        guard_failed = reason == run.QUALIFICATION_PROBE_FAILED_REASON
        if guard_failed:
            if type(probe) is not str or probe not in run.QUALIFICATION_PROBE_NAMES:
                raise ValueError("qualification view last failure does not name its failed probe")
        elif probe is not None:
            raise ValueError("qualification view last failure names a probe without the probe "
                             "failure reason")
        if frozenset(failure) - _LAST_FAILURE_PROBE_FIELDS:
            raise ValueError("qualification view last failure is not closed")
    return value
