"""Validated typed transport from Claude adapters to the shared v2 bridge."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from adapters import bridge
from application.protocol import validate_public_result

from .correlation import correlate

CLAUDE_PROFILE_ID = "claude-code@2.1.278"


def _freeze(value: Any) -> Any:
    if type(value) is dict:
        return FrozenMapping({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, eq=False)
class FrozenMapping(Mapping[str, Any]):
    """Immutable mapping used after transport-boundary validation."""

    _data: Mapping[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def as_dict(self) -> dict[str, Any]:
        """Return a plain ``dict``/``list`` copy for ``render_author_view``, which takes plain JSON containers.

        The Claude restore and completion hooks render the result through it.
        """
        def thaw(value: Any) -> Any:
            if isinstance(value, FrozenMapping):
                return {key: thaw(item) for key, item in value.items()}
            if type(value) is tuple:
                return [thaw(item) for item in value]
            return value
        return {key: thaw(item) for key, item in self.items()}


@dataclass(frozen=True, eq=False)
class Reason(FrozenMapping):
    """Validated block reason."""

    @property
    def code(self) -> str:
        return self["code"]

    @property
    def message(self) -> str | None:
        return self.get("message")


@dataclass(frozen=True, eq=False)
class GovernanceContext(FrozenMapping):
    """Validated governance host context."""

    @property
    def ingress(self) -> str:
        return self["ingress"]


@dataclass(frozen=True, eq=False)
class Governance(FrozenMapping):
    """Validated run-governance projection."""

    @property
    def state(self) -> str:
        return self["state"]

    @property
    def control_mode(self) -> str:
        return self["control_mode"]

    @property
    def context(self) -> GovernanceContext | None:
        return self.get("context")


@dataclass(frozen=True, eq=False)
class Child(FrozenMapping):
    """Validated child projection."""

    @property
    def child_id(self) -> str:
        return self["child_id"]

    @property
    def resource_class(self) -> str:
        return self["resource_class"]

    @property
    def state(self) -> str:
        return self["state"]


@dataclass(frozen=True, eq=False)
class RunView(FrozenMapping):
    """Validated public run projection."""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RunView":
        data = {key: _freeze(item) for key, item in value.items()}
        data["children"] = tuple(Child(dict(item)) for item in data["children"])
        if data.get("governance") is not None:
            governance = dict(data["governance"])
            if governance.get("context") is not None:
                governance["context"] = GovernanceContext(dict(governance["context"]))
            data["governance"] = Governance(governance)
        return cls(data)

    @property
    def id(self) -> str:
        return self["id"]

    @property
    def status(self) -> str:
        return self["status"]

    @property
    def children(self) -> tuple[Child, ...]:
        return self["children"]

    @property
    def governance(self) -> Governance | None:
        return self.get("governance")


@dataclass(frozen=True, eq=False)
class Result(FrozenMapping):
    """Validated result union with an optional run projection."""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Result":
        data = {key: _freeze(item) for key, item in value.items()}
        if "run" in value:
            data["run"] = RunView.from_mapping(value["run"])
        if "reasons" in value:
            data["reasons"] = tuple(Reason(dict(item)) for item in data["reasons"])
        return cls(data)

    @property
    def type(self) -> str:
        return self["type"]

    @property
    def run(self) -> RunView | None:
        return self.get("run")

    @property
    def reasons(self) -> tuple[Reason, ...]:
        return self.get("reasons", ())

    @property
    def first_reason(self) -> Reason | None:
        return self.reasons[0] if self.reasons else None

    @property
    def inert_reason(self) -> str | None:
        return self.get("reason")

    @property
    def next_actions(self) -> tuple[Any, ...]:
        return self.get("next_actions", ())

    @property
    def presentation(self) -> FrozenMapping | None:
        return self.get("presentation")

    @property
    def code(self) -> str | None:
        return self.get("code")

    @property
    def message(self) -> str | None:
        return self.get("message")

    @property
    def fail_direction(self) -> str | None:
        return self.get("fail_direction")


@dataclass(frozen=True, eq=False)
class Response(FrozenMapping):
    """Correlated, schema-validated response envelope."""

    @classmethod
    def from_envelope(cls, request: Mapping[str, Any], envelope: object) -> "Response":
        raw = correlate(request, envelope)
        if not validate_public_result(raw["result"]):
            raise ValueError("bridge response failed the public response schema")
        return cls({"protocol": raw["protocol"], "request_id": raw["request_id"],
                    "result": Result.from_mapping(raw["result"])})

    @property
    def result(self) -> Result:
        return self["result"]


class Transport(Protocol):
    """Narrow injectable transport seam used by translators and parity tests."""

    def dispatch(self, request: dict) -> object: ...


class BridgeTransport:
    """Dispatch through the shared bridge and validate exactly once on return."""

    __slots__ = ()

    def dispatch(self, request: dict) -> Response:
        return Response.from_envelope(
            request, bridge.handle(request, profile_id=CLAUDE_PROFILE_ID))


def dispatch_with(transport: Transport | None, request: dict) -> Response:
    """Dispatch through a production or test transport and apply the same boundary."""
    value = (transport if transport is not None else BridgeTransport()).dispatch(request)
    return value if isinstance(value, Response) else Response.from_envelope(request, value)


def dispatch(request: dict) -> Response:
    """One-shot convenience form of :class:`BridgeTransport`."""
    return BridgeTransport().dispatch(request)
