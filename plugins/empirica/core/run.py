"""Immutable operational run state value for Empirica v2."""
from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from . import environment, governance, spike_plan


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


# State-bound vocabularies the pure core owns for the run/5 members (spec §4.2).
ENVIRONMENT_STATES = ("pending", "approved", "rejected")
ATTEMPT_OUTCOMES = ("reserved", "succeeded", "failed", "superseded")
_ENVIRONMENT_MEMBER_FIELDS = ("revision", "proposal", "digest", "state", "receipt_id")
_QUALIFICATION_ATTEMPT_FIELDS = ("attempt_id", "revision", "started_stamp", "outcome",
                                 "record_digest", "reason")


def _environment_member(value: Any, where: str) -> Mapping[str, Any]:
    """The closed {revision, proposal, digest, state, receipt_id} member, or None.

    The bounds mirror ``core.environment``; the digest is the member's own coherence check —
    it must equal the canonical digest of the exact proposal body it carries.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != set(_ENVIRONMENT_MEMBER_FIELDS):
        raise ValueError(f"run/5 {where} is malformed")
    revision = value["revision"]
    if type(revision) is not int or revision < 1:
        raise ValueError(f"run/5 {where} revision is malformed")
    proposal = value["proposal"]
    try:
        if not spike_plan.is_digest256(value["digest"]):
            raise environment.EnvironmentProposalError("digest shape")
        if environment.proposal_digest(proposal) != value["digest"]:
            raise environment.EnvironmentProposalError("digest coherence")
    except environment.EnvironmentProposalError as exc:
        raise ValueError(f"run/5 {where} proposal or digest is malformed") from exc
    if value["state"] not in ENVIRONMENT_STATES:
        raise ValueError(f"run/5 {where} state is unknown")
    receipt = value["receipt_id"]
    if receipt is not None and (type(receipt) is not str or not receipt):
        raise ValueError(f"run/5 {where} receipt id is malformed")
    return value


def _qualification_attempt(value: Any) -> Mapping[str, Any]:
    """One closed host qualification attempt; shape only — sequence and admission are derived."""
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
                               or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", reason)):
        raise ValueError("run/5 qualification attempt reason is malformed")
    if type(value["attempt_id"]) is not str or not value["attempt_id"] or len(value["attempt_id"]) > 128:
        raise ValueError("run/5 qualification attempt id is malformed")
    return value


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
        if isinstance(self.environment_history, (str, bytes)) or not hasattr(
                self.environment_history, "__iter__"):
            raise ValueError("run/5 environment history is malformed")
        if len(self.environment_history) > environment.MAX_ENVIRONMENT_HISTORY:
            raise ValueError("run/5 environment history exceeds its bound")
        history = tuple(_immutable(_environment_member(item, "environment history"))
                        for item in self.environment_history)
        if isinstance(self.qualification_attempts, (str, bytes)) or not hasattr(
                self.qualification_attempts, "__iter__"):
            raise ValueError("run/5 qualification attempts are malformed")
        if len(self.qualification_attempts) > environment.MAX_QUALIFICATION_ATTEMPTS:
            raise ValueError("run/5 qualification attempts exceed their bound")
        attempts = tuple(_immutable(_qualification_attempt(item))
                         for item in self.qualification_attempts)
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
