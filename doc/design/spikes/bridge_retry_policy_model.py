#!/usr/bin/env python3
"""Executable model for the bridge retry-policy acceptance scenarios."""
from __future__ import annotations

SAFE_COMMANDS = {"ResolveRun", "GetRun", "RestoreRun"}
SAFE_ACTIONS = {
    "graph", "evidence", "evidence_leaf", "audit_verdict", "attribution",
    "configure_budget", "phase", "mode", "freeze", "route", "investigate",
    "consume_audit_ticket",
}


def replay_safe(command: str, *, action: str | None = None, intent: str | None = None) -> bool:
    if command in SAFE_COMMANDS:
        return True
    if command == "StartRun":
        return True  # caller precondition: identical request and active generation
    if command == "EvaluateRun":
        return intent == "continue"
    if command == "ObserveAction":
        return action in SAFE_ACTIONS
    return False


def attempts(command: str, outcomes: list[str], *, action: str | None = None,
             intent: str | None = None, deadline_allows: bool = True) -> tuple[int, str]:
    """Model outcomes: response, pre_transient, pre_permanent, ambiguous, timeout."""
    used = 0
    for outcome in outcomes[:3]:
        used += 1
        if outcome == "response":
            return used, "return_response"
        if outcome in {"pre_permanent", "timeout"}:
            return used, "transport_failure"
        if outcome == "ambiguous" and not replay_safe(command, action=action, intent=intent):
            return used, "outcome_unknown"
        if not deadline_allows:
            return used, "deadline_exhausted"
    return used, "transport_failure"


def scenarios() -> None:
    assert attempts("GetRun", ["ambiguous", "ambiguous", "response"]) == (3, "return_response")
    assert attempts("ObserveAction", ["ambiguous"], action="reserve_spawn") == (1, "outcome_unknown")
    assert attempts("ObserveAction", ["pre_transient", "response"], action="reserve_spawn") == (2, "return_response")
    assert attempts("GetRun", ["response"]) == (1, "return_response")
    assert attempts("ObserveAction", ["ambiguous"], action="audit_ticket") == (1, "outcome_unknown")
    assert attempts("GetRun", ["ambiguous", "response"], deadline_allows=False) == (1, "deadline_exhausted")
    assert attempts("ObserveAction", ["ambiguous"], action="future_action") == (1, "outcome_unknown")
    assert attempts("EvaluateRun", ["ambiguous"], intent="report_convergence") == (1, "outcome_unknown")
    assert attempts("EvaluateRun", ["ambiguous", "response"], intent="continue") == (2, "return_response")


def check() -> None:
    scenarios()
    # Falsification control: deliberately misclassify reserve_spawn as replay-safe. The same
    # acceptance scenarios must reject that mutation, proving the check can go red.
    SAFE_ACTIONS.add("reserve_spawn")
    try:
        try:
            scenarios()
        except AssertionError:
            pass
        else:
            raise AssertionError("falsification control survived")
    finally:
        SAFE_ACTIONS.remove("reserve_spawn")
    scenarios()
    print("PASS: 9 retry-policy scenarios; reserve_spawn mutation killed")


if __name__ == "__main__":
    check()
