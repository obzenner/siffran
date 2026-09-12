"""Append-only revision with explicit retirement records."""
from collections.abc import Iterable

from .model import Contract, Obligation, Retirement


def revise(contract: Contract, *, add: Iterable[Obligation] = (), retire: Iterable[str] = (), reason: str, authority: str) -> Contract:
    additions = tuple(add)
    retire_ids = tuple(retire)
    if not reason.strip() or not authority.strip():
        raise ValueError("revision reason and authority are required")
    if len(retire_ids) != len(set(retire_ids)):
        raise ValueError("retire ids must be unique")
    live = {item.id: item for item in contract.obligations}
    missing = sorted(set(retire_ids) - live.keys())
    if missing:
        raise ValueError(f"cannot retire unknown obligations: {missing}")
    existing = set(live) | {item.obligation.id for item in contract.retired}
    if any(item.id in existing for item in additions) or len({item.id for item in additions}) != len(additions):
        raise ValueError("added obligation ids must be unique and new")
    revision = contract.revision + 1
    new_retirements = tuple(Retirement(live[item], reason, authority, revision) for item in retire_ids)
    remaining = tuple(item for item in contract.obligations if item.id not in set(retire_ids))
    return Contract(
        contract.contract_id, revision, remaining + additions,
        contract.provenance + (reason, authority), contract.revision,
        (f"{contract.contract_id}@{contract.revision}",), contract.retired + new_retirements,
    )
