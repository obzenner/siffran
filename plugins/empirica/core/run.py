"""Immutable operational run state value for Empirica v2."""
from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Mapping

from . import governance


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

    def __post_init__(self) -> None:
        binding = self.spike_backend
        if not isinstance(binding, Mapping):
            raise ValueError("run/3 requires a spike backend binding")
        qualified = {
            "kind", "backend", "profile_id", "qualification_record_digest",
            "execution_binding_digest",
        }
        if binding.get("kind") == "qualified":
            if (set(binding) != qualified or binding.get("backend") != "docker"
                    or binding.get("profile_id") not in {
                        "docker-isolated-v1", "docker-networked-v1"}
                    or any(not isinstance(binding.get(key), str)
                           or re.fullmatch(r"sha256:[0-9a-f]{64}", binding[key]) is None
                           for key in ("qualification_record_digest", "execution_binding_digest"))):
                raise ValueError("run/3 qualified spike backend is malformed")
        elif set(binding) != {"kind"} or binding.get("kind") != "spikes_unavailable":
            raise ValueError("run/3 unavailable spike backend is malformed")
        object.__setattr__(self, "invocation", _immutable(self.invocation))
        object.__setattr__(self, "budgets", _immutable(self.budgets))
        object.__setattr__(self, "governance", _immutable(self.governance))
        object.__setattr__(self, "children", _immutable(self.children))
        object.__setattr__(self, "spike_backend", _immutable(self.spike_backend))
        if self.frozen_claim_ids is not None:
            object.__setattr__(self, "frozen_claim_ids", tuple(self.frozen_claim_ids))
