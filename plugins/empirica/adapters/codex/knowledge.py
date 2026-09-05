"""Codex-facing knowledge operations over the shared validated adapter machinery.

The graph, research, spike, audit, attribution, and re-gate request builders are deliberately
the same implementations used by Claude.  Only the witnessed harness label on freshly executed
spikes changes, so Codex does not fork any evidence or convergence rule.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable

from adapters.claude import knowledge as _shared

SpikeExecution = _shared.SpikeExecution
build_attribution_request = _shared.build_attribution_request
build_audit_ticket_request = _shared.build_audit_ticket_request
build_audit_verdict_request = _shared.build_audit_verdict_request
build_graph_request = _shared.build_graph_request
build_research_request = _shared.build_research_request
build_spike_request = _shared.build_spike_request


def _codex_execution(execution: SpikeExecution) -> SpikeExecution:
    statement = {
        **execution.statement,
        "predicate": {
            **execution.statement["predicate"],
            "actor": {
                **execution.statement["predicate"].get("actor", {}),
                "harness": "codex",
            },
        },
    }
    return replace(execution, statement=statement)


def run_spike(
    claim_id: str,
    claim_text: str,
    command: list[str],
    files: list[Path],
    ts: str,
    *,
    repeat: int = 1,
    timeout: float = 300,
) -> SpikeExecution:
    """Run the one shared deterministic harness and attribute the dispatch to Codex."""
    return _codex_execution(_shared.run_spike(
        claim_id, claim_text, command, files, ts, repeat=repeat, timeout=timeout,
    ))


def build_regate_requests(
    run_id: str,
    graph: dict,
    stored_leaves: list[dict],
    ts: str,
    *,
    timeout: float = 300,
) -> list[dict]:
    """Re-run stale shared spikes and correct only their host attribution."""
    requests = _shared.build_regate_requests(
        run_id, graph, stored_leaves, ts, timeout=timeout,
    )
    for request in requests:
        predicate = request["command"]["action"]["statement"]["predicate"]
        predicate["actor"] = {**predicate.get("actor", {}), "harness": "codex"}
    return requests


def normalised_statements(statements: Iterable[dict]) -> list[dict]:
    """Expose the shared validation projection for adapter tests and auditors."""
    return _shared._normalised_leaves(statements)
