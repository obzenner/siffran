"""Canonical projection, decoding, preservation, and text rendering.

The preservation normal form detects text change, witness removal, and provenance removal. It does
not claim to decide whether one natural-language sentence is semantically more specific than another.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .model import Contract, Observation, Obligation, Preservation, Verdict, Witness


def _collapse(value: str) -> str:
    return " ".join(value.split())


def _witness_key(item: Witness) -> tuple[str, str, str, str]:
    return (item.kind, item.ref, item.expect, item.description)


def _canonical_obligation(item: Obligation) -> dict[str, Any]:
    value = item.to_json()
    value["must"] = _collapse(item.must)
    value["witnesses"] = [witness.to_json() for witness in sorted(item.witnesses, key=_witness_key)]
    value["because"] = sorted(set(item.because))
    return value


def canonical(contract: Contract) -> dict[str, Any]:
    """Return the deterministic normal form of a contract."""
    value: dict[str, Any] = {
        "contract_id": contract.contract_id,
        "revision": contract.revision,
        "obligations": [_canonical_obligation(item) for item in sorted(contract.obligations, key=lambda item: item.id)],
        "provenance": sorted(set(contract.provenance)),
        "supersedes": sorted(set(contract.supersedes)),
        "retired": [
            {
                "obligation": _canonical_obligation(item.obligation),
                "reason": item.reason,
                "authority": item.authority,
                "at_revision": item.at_revision,
            }
            for item in sorted(contract.retired, key=lambda item: item.obligation.id)
        ],
    }
    if contract.parent_revision is not None:
        value["parent_revision"] = contract.parent_revision
    return value


def parse(view: Mapping[str, Any]) -> Contract:
    """Decode the contract portion of a canonical or projected view."""
    value = dict(view)
    value.pop("verdict", None)
    obligations = []
    for item in value.get("obligations", []):
        clean = dict(item)
        clean.pop("status", None)
        clean["witnesses"] = [
            {key: witness[key] for key in ("kind", "ref", "expect", "description")}
            for witness in item["witnesses"]
        ]
        obligations.append(clean)
    value["obligations"] = obligations
    return Contract.from_json(value)


def _observed(witness: Witness, accepted: frozenset[tuple[str, str, str]]) -> str | None:
    if (witness.kind, witness.ref, witness.expect) in accepted:
        return witness.expect
    opposite = "fail" if witness.expect == "pass" else "pass"
    if (witness.kind, witness.ref, opposite) in accepted:
        return opposite
    return None


def project(contract: Contract, verdict: Verdict, observations: Iterable[Observation] = ()) -> dict[str, Any]:
    """Emit the one canonical agent-facing view."""
    accepted = frozenset((item.kind, item.ref, item.outcome) for item in observations)
    value = canonical(contract)
    statuses = {}
    for name in ("satisfied", "holds", "violated", "residual"):
        statuses.update({item: name for item in getattr(verdict, name)})
    by_id = {item.id: item for item in contract.obligations}
    for item in value["obligations"]:
        obligation = by_id[item["id"]]
        item["status"] = statuses.get(item["id"], "residual")
        for encoded, witness in zip(item["witnesses"], sorted(obligation.witnesses, key=_witness_key), strict=True):
            encoded["observed"] = _observed(witness, accepted)
    value["verdict"] = verdict.to_json()
    return value


def preserved(before_view: Mapping[str, Any], after_view: Mapping[str, Any]) -> Preservation:
    """Check the exact non-weakening rule defined by the v1 contract."""
    before = parse(before_view)
    after = parse(after_view)
    live_after = {item.id: item for item in after.obligations}
    retired_after = {item.obligation.id: item for item in after.retired}
    reasons: list[str] = []
    for old in sorted(before.obligations, key=lambda item: item.id):
        if old.id in retired_after:
            if not retired_after[old.id].reason.strip():
                reasons.append(f"{old.id}: retirement has no reason")
            continue
        new = live_after.get(old.id)
        if new is None:
            reasons.append(f"{old.id}: obligation disappeared without retirement")
            continue
        if old.mode != new.mode:
            reasons.append(f"{old.id}: mode changed")
        if _collapse(old.must) != _collapse(new.must):
            reasons.append(f"{old.id}: must changed")
        old_witnesses = {_witness_key(item) for item in old.witnesses}
        new_witnesses = {_witness_key(item) for item in new.witnesses}
        if not old_witnesses <= new_witnesses:
            reasons.append(f"{old.id}: witnesses were removed")
        if not set(old.because) <= set(new.because):
            reasons.append(f"{old.id}: because provenance was removed")
    return Preservation(not reasons, tuple(reasons))


def _ids(values: list[str]) -> str:
    return ", ".join(values) if values else "-"


def render_text(view: Mapping[str, Any]) -> str:
    """Render a deterministic, complete string-channel representation."""
    contract = parse(view)
    verdict = view.get("verdict", {})
    observed = {
        (item["id"], witness["kind"], witness["ref"], witness["expect"], witness["description"]): witness.get("observed")
        for item in view.get("obligations", [])
        for witness in item.get("witnesses", [])
    }
    lines = [f"Obligation contract {contract.contract_id}@{contract.revision}"]
    for item in sorted(contract.obligations, key=lambda value: value.id):
        lines.append(f"{item.id} [{item.mode}] {_collapse(item.must)}")
        hold = "none" if item.hold is None else f"{item.hold} — {item.hold_reason}"
        lines.append(f"  hold: {hold}")
        for witness in sorted(item.witnesses, key=_witness_key):
            key = (item.id, witness.kind, witness.ref, witness.expect, witness.description)
            outcome = observed.get(key)
            lines.append(
                f"  - {witness.kind}:{witness.ref} ({witness.expect}) — "
                f"{witness.description} [observed: {outcome or 'null'}]"
            )
    for item in sorted(contract.retired, key=lambda value: value.obligation.id):
        lines.append(
            f"{item.obligation.id} [retired@{item.at_revision}] "
            f"{item.reason} — authority: {item.authority}"
        )
    lines.append(
        "Verdict: "
        + "; ".join(
            f"{name}={_ids(list(verdict.get(name, [])))}"
            for name in ("satisfied", "holds", "violated", "residual", "unwitnessed", "held")
        )
    )
    return "\n".join(lines)
