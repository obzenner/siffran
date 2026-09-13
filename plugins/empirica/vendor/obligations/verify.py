"""Pure deterministic verification against caller-trusted observations."""
from collections.abc import Callable, Iterable

from .model import Contract, Observation, Verdict


def verify(contract: Contract, observations: Iterable[Observation], trusted: Callable[[Observation], bool]) -> Verdict:
    accepted = frozenset((item.kind, item.ref, item.outcome) for item in observations if trusted(item))
    satisfied: list[str] = []
    holds: list[str] = []
    violated: list[str] = []
    residual: list[str] = []
    unwitnessed: list[str] = []
    held: list[str] = []
    for obligation in sorted(contract.obligations, key=lambda item: item.id):
        expected = [(item.kind, item.ref, item.expect) for item in obligation.witnesses]
        opposite = [(kind, ref, "fail" if outcome == "pass" else "pass") for kind, ref, outcome in expected]
        hits = [item in accepted for item in expected]
        contradictions = [item in accepted for item in opposite]
        observed_any = any(hits) or any(contradictions)
        if obligation.mode == "forbid":
            (violated if any(hits) else holds).append(obligation.id)
        elif expected and all(hits):
            satisfied.append(obligation.id)
        elif any(contradictions):
            violated.append(obligation.id)
        else:
            residual.append(obligation.id)
            if not observed_any:
                unwitnessed.append(obligation.id)
            if obligation.hold is not None:
                held.append(obligation.id)
    return Verdict(*(tuple(items) for items in (satisfied, holds, violated, residual, unwitnessed, held)))
