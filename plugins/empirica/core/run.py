"""Immutable operational run state value for Empirica v2."""
from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from . import environment, governance, spike_plan
from .contract_bounds import (
    QUALIFICATION_ATTEMPT_ATTEMPT_ID_MAX_LENGTH,
    QUALIFICATION_ATTEMPT_REASON_PATTERNS,
)


def delegated_auto(command: Mapping[str, Any]) -> bool:
    """Whether admitted invocation facts select delegated automatic authority."""
    invocation = command["invocation"]
    return (command["control_mode"] == "auto"
            and invocation["delegation"] is True
            and invocation["interactive"] is not True)


def start_admission(command: Mapping[str, Any], limits: Mapping[str, int]) -> str | None:
    """Admit each authority-bearing StartRun source without last-writer-wins merging.

    ``limits`` and ``command["budgets"]`` are the application-boundary ceiling mappings; each
    holds every ceiling. In delegated auto, each source must fit the fixed policy and StartRun
    may only narrow the operator limit.
    """
    goal = command["goal"]
    if not goal.strip():
        return "run.goal_required"
    invocation = command["invocation"]
    if (command["control_mode"] == "auto"
            and not (invocation["interactive"] is True
                     or invocation["delegation"] is True)):
        return "governance.auto_invocation_required"
    if not delegated_auto(command):
        return None
    start_budgets = command["budgets"]
    for source in (limits, start_budgets):
        for ceiling in governance.CEILINGS:
            if source[ceiling] > governance.DEFAULT_CEILINGS[ceiling]:
                return "governance.budget_contradictory"
    for ceiling in governance.CEILINGS:
        if start_budgets[ceiling] > limits[ceiling]:
            return "governance.budget_contradictory"
    return None


def effective_ceilings(command: Mapping[str, Any], limits: Mapping[str, int]) -> dict[str, int]:
    """Return the componentwise-narrowed operational seed after successful admission.

    ``limits`` and ``command["budgets"]`` hold every ceiling (see :func:`start_admission`).
    """
    return {
        ceiling: min(governance.DEFAULT_CEILINGS[ceiling], limits[ceiling],
                     command["budgets"][ceiling])
        for ceiling in governance.CEILINGS
    }


def _immutable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _immutable(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_immutable(v) for v in value)
    return value


def _sequence(value: Any, where: str, limit: int) -> list | tuple:
    """A real bounded sequence of rows: a list or a tuple. A mapping is never one (an empty
    one would silently read as \"no rows\"), and neither is an unsized iterable (its length is
    not a fact the bound can be judged on)."""
    if type(value) not in (list, tuple):
        raise ValueError(f"run/5 {where} must be a list or a tuple")
    if len(value) > limit:
        raise ValueError(f"run/5 {where} exceeds its bound")
    return value


# State-bound vocabularies the pure core owns for the run/5 members (spec §4.2).
ENVIRONMENT_STATES = ("pending", "approved", "rejected")
ATTEMPT_OUTCOMES = ("reserved", "succeeded", "failed", "superseded")
_ENVIRONMENT_MEMBER_FIELDS = ("revision", "proposal", "digest", "state", "receipt_id")
_QUALIFICATION_ATTEMPT_FIELDS = ("attempt_id", "revision", "started_stamp", "outcome",
                                 "record_digest", "reason", "probe")
# The attempt's closed reason lexeme, exactly as the state contract writes it (core.contract_bounds).
_ATTEMPT_REASON_PATTERN = QUALIFICATION_ATTEMPT_REASON_PATTERNS[0]
# Empirica 7.2 §5.1, the probe-name decision: the attempt row keeps its failed probe durably and
# never in message prose. The wire vocabulary owners are the Docker spike support contract (the
# five probe names) and the application reason set (generic_spike's qualification-probe-failed);
# both mirrors are pinned equal to those owners by empirica-docker-support-contract-check, and
# registering the shared qualificationProbeName definition with the conformance harness replaces
# the names with generated core constants.
QUALIFICATION_PROBE_NAMES = ("realization", "natural-125", "output-overflow", "timeout",
                             "network-profile")
QUALIFICATION_PROBE_FAILED_REASON = "qualification-probe-failed"


def validate_environment_member(value: Any, where: str = "environment member") -> Mapping[str, Any]:
    """One run/5 environment member's closed shape — the contract-conformance harness's validator
    (``x-core-validator``, ADR-0075): bounds, vocabularies and patterns exactly as state.schema.json
    writes them. The member's digest is held to its shape here; its coherence with the member's own
    proposal body stays core-only (the schema cannot speak it), pinned by the contract's one named test."""
    if not isinstance(value, Mapping) or set(value) != set(_ENVIRONMENT_MEMBER_FIELDS):
        raise ValueError(f"run/5 {where} is malformed")
    revision = value["revision"]
    if type(revision) is not int or revision < 1:
        raise ValueError(f"run/5 {where} revision is malformed")
    try:
        # A replace() passes the frozen member back in; the core's one plain() (governance.plain,
        # the reuse E2a's review demanded — never a second copy in this module) thaws it to the
        # plain dict/list shapes the ingress validator requires.
        environment.validate_proposal(governance.plain(value["proposal"]))
    except environment.EnvironmentProposalError as exc:
        raise ValueError(f"run/5 {where} proposal or digest is malformed") from exc
    if not spike_plan.is_digest256(value["digest"]):
        raise ValueError(f"run/5 {where} proposal or digest is malformed")
    if value["state"] not in ENVIRONMENT_STATES:
        raise ValueError(f"run/5 {where} state is unknown")
    receipt = value["receipt_id"]
    if receipt is not None and (type(receipt) is not str or not receipt):
        raise ValueError(f"run/5 {where} receipt id is malformed")
    return value


def _environment_member(value: Any, where: str, *, nullable: bool = True) -> Mapping[str, Any]:
    """The closed {revision, proposal, digest, state, receipt_id} member with its digest coherence —
    it must equal the canonical digest of the exact proposal body it carries. ``nullable``: only
    the current member may be null \"none exists yet\"; a history row is always an object."""
    if value is None:
        if nullable:
            return None
        raise ValueError(f"run/5 {where} must be an environment member, not null")
    validate_environment_member(value, where)
    if environment.proposal_digest(governance.plain(value["proposal"])) != value["digest"]:
        raise ValueError(f"run/5 {where} proposal or digest is malformed")
    return value


def validate_qualification_probe_name(value: Any) -> str:
    """One qualification probe name — the contract-conformance harness's validator for the shared
    ``qualificationProbeName`` definition (``x-core-validator``, ADR-0075): the enumeration the
    Docker spike support contract owns, pinned here by ``empirica-docker-support-contract-check``."""
    if type(value) is not str or value not in QUALIFICATION_PROBE_NAMES:
        raise ValueError("qualification probe name is unknown")
    return value


def validate_qualification_attempt(value: Any) -> Mapping[str, Any]:
    """One closed host qualification attempt — the contract-conformance harness's validator for the
    annotated ``qualificationAttempt`` definition (ADR-0075); shape only, sequence and admission are derived."""
    if not isinstance(value, Mapping) or set(value) != set(_QUALIFICATION_ATTEMPT_FIELDS):
        raise ValueError("run/5 qualification attempt is malformed")
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise ValueError("run/5 qualification attempt revision is malformed")
    if type(value["started_stamp"]) is not int or value["started_stamp"] < 0:
        raise ValueError("run/5 qualification attempt stamp is malformed")
    if value["outcome"] not in ATTEMPT_OUTCOMES:
        raise ValueError("run/5 qualification attempt outcome is unknown")
    record = value["record_digest"]
    if record is not None and not spike_plan.is_digest256(record):
        raise ValueError("run/5 qualification attempt record digest is malformed")
    reason = value["reason"]
    if reason is not None and (type(reason) is not str
                               or not re.fullmatch(_ATTEMPT_REASON_PATTERN, reason)):
        raise ValueError("run/5 qualification attempt reason is malformed")
    if (type(value["attempt_id"]) is not str or not value["attempt_id"]
            or len(value["attempt_id"]) > QUALIFICATION_ATTEMPT_ATTEMPT_ID_MAX_LENGTH):
        raise ValueError("run/5 qualification attempt id is malformed")
    probe = value["probe"]
    if probe is not None and probe not in QUALIFICATION_PROBE_NAMES:
        raise ValueError("run/5 qualification attempt probe is unknown")
    if (reason == QUALIFICATION_PROBE_FAILED_REASON) != (probe is not None):
        raise ValueError("run/5 qualification attempt names its failed probe exactly when its "
                         "reason is the probe-failure reason, never otherwise")
    return value


def validate_environment(value: Any) -> Mapping[str, Any] | None:
    """The run's current environment member — harness validator for the annotated ``environment`` state
    member: null \"none exists yet\", or one member of the closed shape (its digest coherence stays
    core-only, pinned by the member definition's one named test)."""
    if value is None:
        return None
    return validate_environment_member(value, "environment")


def validate_environment_history(value: Any) -> list | tuple:
    """The run's immutable earlier revisions — harness validator for ``environment_history``: a real
    bounded sequence (never a mapping, never an unsized iterable), each row a shaped member. The rows'
    digest coherence stays core-only, pinned by the member definition's one named test."""
    rows = _sequence(value, "environment history", environment.MAX_ENVIRONMENT_HISTORY)
    for row in rows:
        validate_environment_member(row, "environment history")
    return rows


def validate_qualification_attempts(value: Any) -> list | tuple:
    """The run's append-only host qualification attempts — harness validator for
    ``qualification_attempts``: a real bounded sequence of shaped attempts."""
    rows = _sequence(value, "qualification attempts", environment.MAX_QUALIFICATION_ATTEMPTS)
    for row in rows:
        validate_qualification_attempt(row)
    return rows


@dataclass(frozen=True)
class OperationalState:
    protocol: str
    state_schema: str
    goal: str
    invocation: Mapping[str, Any]
    status: str
    budgets: Mapping[str, int]
    governance: Mapping[str, Any]
    selected_graph_artifact_id: str | None
    frozen_claim_ids: tuple[str, ...] | None
    frozen_semantic_digest: str | None
    route_stamp: int | None
    investigation_stamp: int | None
    stamp_seq: int
    last_derivation_digest: str | None
    children: tuple[Mapping[str, Any], ...]
    observation_basis_digest: str
    committed_artifact_head_id: str | None
    spike_backend: Mapping[str, Any]
    environment: Mapping[str, Any] | None
    environment_history: tuple[Mapping[str, Any], ...]
    qualification_attempts: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        binding = self.spike_backend
        if not isinstance(binding, Mapping):
            raise ValueError("run/5 requires a spike backend binding")
        if spike_plan.spike_backend_kind(binding) is None:
            if binding.get("kind") in ("qualified", "unqualified"):
                raise ValueError("run/5 closed spike backend kind is malformed")
            raise ValueError("run/5 unavailable spike backend is malformed")
        member = _environment_member(self.environment, "environment")
        history_rows = _sequence(self.environment_history, "environment history",
                                 environment.MAX_ENVIRONMENT_HISTORY)
        history = tuple(_immutable(_environment_member(item, "environment history", nullable=False))
                        for item in history_rows)
        attempt_rows = _sequence(self.qualification_attempts, "qualification attempts",
                                 environment.MAX_QUALIFICATION_ATTEMPTS)
        attempts = tuple(_immutable(validate_qualification_attempt(item)) for item in attempt_rows)
        object.__setattr__(self, "invocation", _immutable(self.invocation))
        object.__setattr__(self, "budgets", _immutable(self.budgets))
        object.__setattr__(self, "governance", _immutable(self.governance))
        object.__setattr__(self, "children", _immutable(self.children))
        object.__setattr__(self, "spike_backend", _immutable(self.spike_backend))
        object.__setattr__(self, "environment", _immutable(member))
        object.__setattr__(self, "environment_history", history)
        object.__setattr__(self, "qualification_attempts", attempts)
        if self.frozen_claim_ids is not None:
            object.__setattr__(self, "frozen_claim_ids", tuple(self.frozen_claim_ids))
