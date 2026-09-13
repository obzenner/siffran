"""Frozen values and validation for the obligation contract."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, TypeVar

WitnessKind = Literal["test", "exit_code", "event", "artifact", "predicate", "judgment"]
Mode = Literal["require", "forbid"]
Hold = Literal["blocked", "deferred"]
Outcome = Literal["pass", "fail"]
REF_PATTERN = r"^[a-z][a-z0-9_-]*(/[A-Za-z0-9._:@-]+)+$"
_REF_RE = re.compile(REF_PATTERN)
_KINDS = frozenset({"test", "exit_code", "event", "artifact", "predicate", "judgment"})


def _nonempty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class Witness:
    kind: WitnessKind
    ref: str
    expect: Outcome
    description: str

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"invalid witness kind: {self.kind!r}")
        if not _REF_RE.fullmatch(self.ref):
            raise ValueError(f"invalid witness ref: {self.ref!r}")
        if self.expect not in ("pass", "fail"):
            raise ValueError(f"invalid witness expectation: {self.expect!r}")
        _nonempty("description", self.description)

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "expect": self.expect, "description": self.description}

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> Witness:
        return cls(value["kind"], value["ref"], value["expect"], value["description"])


@dataclass(frozen=True)
class Obligation:
    id: str
    mode: Mode
    must: str
    witnesses: tuple[Witness, ...]
    because: tuple[str, ...] = ()
    hold: Hold | None = None
    hold_reason: str | None = None
    severity: str | None = None

    def __post_init__(self) -> None:
        _nonempty("id", self.id)
        _nonempty("must", self.must)
        if self.mode not in ("require", "forbid"):
            raise ValueError(f"invalid obligation mode: {self.mode!r}")
        if self.hold not in (None, "blocked", "deferred"):
            raise ValueError(f"invalid hold: {self.hold!r}")
        if (self.hold is None) != (self.hold_reason is None):
            raise ValueError("hold and hold_reason must be supplied together")
        if self.hold_reason is not None:
            _nonempty("hold_reason", self.hold_reason)
        if self.severity is not None:
            _nonempty("severity", self.severity)

    def to_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id, "mode": self.mode, "must": self.must,
            "witnesses": [item.to_json() for item in self.witnesses],
            "because": list(self.because),
        }
        if self.hold is not None:
            result.update(hold=self.hold, hold_reason=self.hold_reason)
        if self.severity is not None:
            result["severity"] = self.severity
        return result

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> Obligation:
        return cls(
            value["id"], value["mode"], value["must"],
            tuple(Witness.from_json(item) for item in value["witnesses"]),
            tuple(value.get("because", ())), value.get("hold"),
            value.get("hold_reason"), value.get("severity"),
        )


@dataclass(frozen=True)
class Observation:
    kind: WitnessKind
    ref: str
    outcome: Outcome
    source: str
    at: str
    payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"invalid observation kind: {self.kind!r}")
        if not _REF_RE.fullmatch(self.ref):
            raise ValueError(f"invalid observation ref: {self.ref!r}")
        if self.outcome not in ("pass", "fail"):
            raise ValueError(f"invalid observation outcome: {self.outcome!r}")
        _nonempty("source", self.source)
        _nonempty("at", self.at)
        if self.kind == "judgment" and self.source.strip().lower() in {"anonymous", "unknown", "model"}:
            raise ValueError("judgment source must identify its principal")

    def to_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": self.kind, "ref": self.ref, "outcome": self.outcome,
            "source": self.source, "at": self.at,
        }
        if self.payload is not None:
            result["payload"] = dict(self.payload)
        return result

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> Observation:
        return cls(value["kind"], value["ref"], value["outcome"], value["source"], value["at"], value.get("payload"))


@dataclass(frozen=True)
class Retirement:
    obligation: Obligation
    reason: str
    authority: str
    at_revision: int

    def __post_init__(self) -> None:
        _nonempty("retirement reason", self.reason)
        _nonempty("retirement authority", self.authority)
        if not isinstance(self.at_revision, int) or isinstance(self.at_revision, bool) or self.at_revision < 1:
            raise ValueError("at_revision must be a positive integer")

    def to_json(self) -> dict[str, Any]:
        return {"obligation": self.obligation.to_json(), "reason": self.reason, "authority": self.authority, "at_revision": self.at_revision}

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> Retirement:
        return cls(Obligation.from_json(value["obligation"]), value["reason"], value["authority"], value["at_revision"])


@dataclass(frozen=True)
class Contract:
    contract_id: str
    revision: int
    obligations: tuple[Obligation, ...]
    provenance: tuple[str, ...]
    parent_revision: int | None = None
    supersedes: tuple[str, ...] = ()
    retired: tuple[Retirement, ...] = ()

    def __post_init__(self) -> None:
        _nonempty("contract_id", self.contract_id)
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        ids = [item.id for item in self.obligations]
        ids.extend(item.obligation.id for item in self.retired)
        if len(ids) != len(set(ids)):
            raise ValueError("obligation ids must be unique across live and retired obligations")

    def to_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "contract_id": self.contract_id, "revision": self.revision,
            "obligations": [item.to_json() for item in self.obligations],
            "provenance": list(self.provenance), "supersedes": list(self.supersedes),
            "retired": [item.to_json() for item in self.retired],
        }
        if self.parent_revision is not None:
            result["parent_revision"] = self.parent_revision
        return result

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> Contract:
        return cls(
            value["contract_id"], value["revision"],
            tuple(Obligation.from_json(item) for item in value["obligations"]),
            tuple(value.get("provenance", ())), value.get("parent_revision"),
            tuple(value.get("supersedes", ())),
            tuple(Retirement.from_json(item) for item in value.get("retired", ())),
        )


@dataclass(frozen=True)
class Verdict:
    satisfied: tuple[str, ...] = ()
    holds: tuple[str, ...] = ()
    violated: tuple[str, ...] = ()
    residual: tuple[str, ...] = ()
    unwitnessed: tuple[str, ...] = ()
    held: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {name: list(getattr(self, name)) for name in ("satisfied", "holds", "violated", "residual", "unwitnessed", "held")}

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> Verdict:
        return cls(*(tuple(value.get(name, ())) for name in ("satisfied", "holds", "violated", "residual", "unwitnessed", "held")))


@dataclass(frozen=True)
class Preservation:
    ok: bool
    reasons: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {"ok": self.ok, "reasons": list(self.reasons)}

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> Preservation:
        return cls(bool(value["ok"]), tuple(value.get("reasons", ())))


T = TypeVar("T")


def to_json(value: Any) -> dict[str, Any]:
    """Encode any public contract value into JSON-compatible data."""
    return value.to_json()


def from_json(value_type: type[T], value: Mapping[str, Any]) -> T:
    """Decode any public contract value using its validating constructor."""
    return value_type.from_json(value)
