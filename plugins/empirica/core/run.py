"""Immutable operational run state value for Empirica v2."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


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
    status: str
    modes: Mapping[str, bool]
    budgets: Mapping[str, int]
    selected_graph_artifact_id: str | None
    frozen_claim_ids: tuple[str, ...] | None
    route_stamp: int | None
    investigation_stamp: int | None
    stamp_seq: int
    last_derivation_digest: str | None
    children: tuple[Mapping[str, Any], ...]
    committed_artifact_head_id: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "modes", _immutable(self.modes))
        object.__setattr__(self, "budgets", _immutable(self.budgets))
        object.__setattr__(self, "children", _immutable(self.children))
        if self.frozen_claim_ids is not None:
            object.__setattr__(self, "frozen_claim_ids", tuple(self.frozen_claim_ids))
