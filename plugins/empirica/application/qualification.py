"""The run-scoped qualification attempt transaction's host-neutral pieces (Empirica 7.2 §5, E9).

One pure module beside the transaction that performs it: the closed validation of the outcome the
neutral host port (``application.ports``, which the bridge binds to E5's sandbox owner,
``adapters.sandbox.run_qualification.qualify_run_environment``) returns, and the pure state
projections of a recorded outcome — the attempt row's resolved form and the qualified
spike-backend binding a bound outcome installs. The transaction itself (reservation, effect, binding compare-and-set, orphan handling)
lives in :class:`application.transaction.Coordinator._qualify`; consumption of the recorded rows
(views, audit context) is E11's.

The reason vocabulary has one owner: every failure outcome reason is read from
``application.generic_spike.QUALIFICATION_FAILURE_REASONS`` and the probe identity — present
exactly with the probe-failure reason, absent otherwise, named with one of the five probe names
the support contract owns — is the same invariant the run view's ``last_failure`` member carries
(``application.wire_views``). The invalid-input spelling of the low-level owner is deliberately
absent: a reservation is derived entirely from committed, validated run state, so an attempt can
never end with it, and no failure word is fabricated in its place.
"""
from __future__ import annotations

from typing import Any, Mapping

from core import run as core_run, spike_plan
from . import generic_spike
from .ports import (QualificationAttemptBound,
                    QualificationAttemptFailed)

#: The closed cause a lost binding compare-and-set records (spec §5.1): the CAS itself owns it;
#: the host attempt owner never emits it.
QUALIFICATION_CONFLICT = generic_spike.GenericUnavailableReason.QUALIFICATION_CONFLICT.value
#: The closed cause an interrupted attempt's row closes with, and the refusal reason while another
#: attempt is live: the application owns both, the host attempt owner never emits them.
QUALIFICATION_INTERRUPTED = generic_spike.GenericUnavailableReason.QUALIFICATION_INTERRUPTED.value
QUALIFICATION_IN_PROGRESS = generic_spike.GenericUnavailableReason.QUALIFICATION_IN_PROGRESS.value


def validate_attempt_outcome(value: Any) -> QualificationAttemptBound | QualificationAttemptFailed:
    """Validate one closed port outcome and return it. A bound outcome carries the durably
    published ``/3`` record's content digest and the binding's execution digest; a failed outcome
    carries one registered attempt-failure reason and — exactly when the reason is the probe
    failure — the failing probe's name. Anything else (an unknown shape, an unregistered reason,
    a probe named without the probe reason or missing with it) is refused: the transaction fails
    closed on it and leaves the reservation unresolved, never recording a guess."""
    if isinstance(value, QualificationAttemptBound):
        for member in (value.record_digest, value.execution_binding_digest):
            if type(member) is not str or not spike_plan.is_digest256(member):
                raise ValueError("qualification bound outcome digests are malformed")
        return value
    if isinstance(value, QualificationAttemptFailed):
        reason, probe = value.reason, value.probe
        if type(reason) is not str or reason not in generic_spike.QUALIFICATION_FAILURE_REASONS:
            raise ValueError("qualification attempt failure reason is not registered")
        probe_failed = reason == core_run.QUALIFICATION_PROBE_FAILED_REASON
        if not probe_failed and probe is not None:
            raise ValueError("qualification attempt probe identity does not match its reason")
        if probe_failed and probe is None:
            raise ValueError("qualification attempt probe identity does not match its reason")
        if probe is not None and core_run.validate_qualification_probe_name(probe) is not probe:
            raise ValueError("qualification attempt probe name is not permitted")
        return value
    raise ValueError("qualification attempt outcome shape is unknown")


def bound_attempt_row(row: Mapping[str, Any], record_digest: str) -> dict[str, Any]:
    """The attempt row's resolved form after a bound outcome (spec §5, step 6): the record digest
    is recorded and the outcome is succeeded; the reason and probe members close as null."""
    return {**dict(row), "outcome": "succeeded", "record_digest": record_digest,
            "reason": None, "probe": None}


def failed_attempt_row(row: Mapping[str, Any], reason: str, probe: str | None) -> dict[str, Any]:
    """The attempt row's resolved form after one failed outcome: the closed reason this attempt
    ended with — and only the probe-failure reason may name its probe (the row reproduces it
    without message prose, exactly the state schema's closed identity rule)."""
    return {**dict(row), "outcome": "failed", "record_digest": None,
            "reason": reason, "probe": probe}


def superseded_attempt_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """The attempt row's resolved form after a lost binding compare-and-set (spec §5, step 6):
    the already-published record is orphaned (its digest names no installed binding), nothing
    binds, and the row closes with the conflict cause, never a fabricated probe."""
    return {**dict(row), "outcome": "superseded", "record_digest": None,
            "reason": QUALIFICATION_CONFLICT, "probe": None}


def interrupted_attempt_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """The attempt row's resolved form once the next qualify finds its attempt dead (spec §5,
    E9): it stays charged, closes failed with the interrupted cause, binds nothing, and records no
    record digest — a record the dead attempt may have published is an orphan no row names."""
    return {**dict(row), "outcome": "failed", "record_digest": None,
            "reason": QUALIFICATION_INTERRUPTED, "probe": None}


def qualified_binding(profile_id: str, record_digest: str,
                      execution_binding_digest: str) -> dict[str, Any]:
    """The qualified spike-backend binding one bound outcome installs by compare-and-set
    (spec §4.2): the existing ``qualified`` shape's closed member set, built by its own
    constructor — never retyped here."""
    return generic_spike.GenericQualifiedBackend(
        "docker", profile_id, record_digest, execution_binding_digest).to_data()
