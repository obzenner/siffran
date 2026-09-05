#!/usr/bin/env python3
"""Explicit, idempotent import of one legacy ``.claude/empirica`` run.

This command is the *only* legacy reader in the adapter.  Runtime construction and bridge reads do
not call it and never fall back to legacy files.  The source is read-only; imported operational
state goes to ``EMPIRICA_HOME`` and exact knowledge statements go to Git shadow refs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parents[2]
if str(_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_PLUGIN))

from adapters import bridge  # noqa: E402
from adapters.claude.knowledge import (  # noqa: E402
    _build_migrated_leaf_request,
    _load_hook,
    build_attribution_request,
    build_audit_ticket_request,
    build_audit_verdict_request,
    build_graph_request,
)
from adapters.claude.run_start import build_start_run_request  # noqa: E402
from adapters.claude.transport import BridgeTransport  # noqa: E402
from application import knowledge  # noqa: E402
from application.state import OperationalState  # noqa: E402
from application.wire import decode_handle, encode_handle  # noqa: E402
from core.records import Corrupt, Present, RunKey  # noqa: E402


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _legacy_inputs(run_dir: Path) -> tuple[dict, list[tuple[str, dict]], dict, list[dict], dict | None]:
    graph = _read_json(run_dir / "claims.json")
    if not isinstance(graph, dict):
        raise ValueError(f"no readable claims.json in {run_dir}")
    leaves = []
    evidence_dir = run_dir / "evidence"
    for path in sorted(evidence_dir.glob("*.json")) if evidence_dir.is_dir() else []:
        raw = _read_json(path)
        if isinstance(raw, dict):
            leaves.append((path.stem, raw))
    manifest = _read_json(run_dir / "run.json", {})
    tickets_doc = _read_json(run_dir / "audit-tickets.json", {})
    tickets = tickets_doc.get("tickets", []) if isinstance(tickets_doc, dict) else []
    tickets = [t for t in tickets if isinstance(t, dict)]
    verdict = _read_json(run_dir / "audit-verdict.json")
    return graph, leaves, manifest if isinstance(manifest, dict) else {}, tickets, verdict


def _existing_graph(service, handle: str):
    key = decode_handle(handle)
    read = service._runs.read(key)  # composition-owned repositories; migration is an admin tool
    if not isinstance(read, Present):
        return None, None, knowledge.Knowledge()
    state = OperationalState.decode(read.value)
    if state is None or state.claim_graph_artifact_id is None:
        return state, None, knowledge.Knowledge()
    artifacts = service._artifacts.read(key)
    if not isinstance(artifacts, Present):
        return state, None, knowledge.Knowledge()
    decoded = knowledge.Knowledge.from_artifacts(artifacts.value)
    return state, decoded.graphs.get(state.claim_graph_artifact_id), decoded


def _compatible_target(service, key: RunKey, goal: str, canonical: dict,
                       expected_leaves: dict[str, dict], expected_verdict: dict | None,
                       needs_ticket: bool, expected_attribution: dict) -> tuple[str, object]:
    """Classify one explicit migration target as ``full``, ``partial``, or ``incompatible``."""
    read = service._runs.read(key)
    if isinstance(read, Corrupt) or not isinstance(read, Present):
        return "incompatible", None
    state = OperationalState.decode(read.value)
    if state is None or state.goal != goal:
        return "incompatible", state
    handle = encode_handle(key)
    _, graph, current = _existing_graph(service, handle)
    if graph is None:
        return ("partial", state) if state.status == "active" else ("incompatible", state)
    if graph != canonical or current.evidence:
        return "incompatible", state
    current_ids: set[str] = set()
    for record in knowledge.active_evidence_leaves(current.evidence_leaves):
        evidence_id = record.get("evidence_id")
        if (evidence_id not in expected_leaves
                or record.get("statement") != expected_leaves[evidence_id]):
            return "incompatible", state
        current_ids.add(evidence_id)
    all_evidence = current_ids == set(expected_leaves)
    if ((not needs_ticket and state.audit_tickets)
            or (needs_ticket and len(state.audit_tickets) > 1)):
        return "incompatible", state
    if expected_verdict is not None and current.verdicts:
        if not state.audit_tickets:
            return "incompatible", state
        target_verdict = dict(expected_verdict, nonce=state.audit_tickets[0]["nonce"],
                              kind=knowledge.KIND_AUDIT_VERDICT)
        if current.verdicts != [target_verdict]:
            return "incompatible", state
    elif expected_verdict is None and current.verdicts:
        return "incompatible", state
    target_attribution = expected_attribution
    if current.attributions and current.attributions != [target_attribution]:
        return "incompatible", state
    audit_complete = ((not needs_ticket or bool(state.audit_tickets))
                      and (expected_verdict is None or bool(current.verdicts)))
    attribution_complete = bool(current.attributions)
    if all_evidence and audit_complete and attribution_complete:
        return "full", state
    return ("partial", state) if state.status == "active" else ("incompatible", state)


def migrate(run_dir: Path, repo: Path, *, session_id: str | None = None) -> dict:
    run_dir, repo = run_dir.resolve(), repo.resolve()
    graph, leaves, manifest, legacy_tickets, verdict = _legacy_inputs(run_dir)
    canonical = knowledge.canonicalize_graph(graph)
    root = canonical["nodes"][canonical["root"]]
    session = session_id or manifest.get("session_id") or manifest.get("run_id") or run_dir.name
    if not isinstance(session, str) or not session:
        raise ValueError("legacy run has no usable session id; pass --session-id")
    goal = manifest.get("goal") if isinstance(manifest.get("goal"), str) else root["text"]
    evidence_hook = _load_hook("evidence")
    attribution_hook = _load_hook("attribution")
    normalised = [leaf for _, raw in leaves if (leaf := evidence_hook.validate_leaf(raw))]
    approved = [nid for nid, node in canonical["nodes"].items()
                if evidence_hook.verdict(normalised, nid, node["text"], node.get("kind"),
                                         "approve")[0]]
    actor = next((t.get("actor") for t in reversed(legacy_tickets) if t.get("actor")), None)
    report = attribution_hook.report(canonical, normalised, approved, actor)
    expected_verdict = None
    if isinstance(verdict, dict):
        expected_verdict = {
            "verdict": verdict.get("verdict"), "argument_digest": verdict.get("argument_digest"),
            "claims_reviewed": verdict.get("claims_reviewed", []),
            "findings": verdict.get("findings", []),
        }
    transport = BridgeTransport(repo)
    start = build_start_run_request({"session_id": session, "cwd": str(repo),
                                     "command_name": "empirica:empirica", "command_args": goal},
                                    correlation_id="legacy-migrate-start", environ={})
    service = bridge.build_service(repo)
    selector = start["command"]["selector"]
    expected_leaves = {evidence_id: statement for evidence_id, statement in leaves}
    generations = service._runs.generations(selector["project"], selector["session"])
    full: tuple[str, object] | None = None
    partial: tuple[str, object] | None = None
    for generation in generations:
        key = RunKey(selector["project"], selector["session"], generation)
        classification, candidate_state = _compatible_target(
            service, key, goal, canonical, expected_leaves, expected_verdict,
            bool(legacy_tickets or isinstance(verdict, dict)), report)
        if classification == "full":
            full = (encode_handle(key), candidate_state)
            break
        if classification == "partial":
            partial = (encode_handle(key), candidate_state)
    if full is not None:
        handle, _ = full
        return {"migrated": True, "idempotent": True, "run_id": handle,
                "source": str(run_dir), "source_untouched": True,
                "evidence": len(leaves), "operations": 0}
    if partial is not None:
        handle, _ = partial
    elif generations:
        raise RuntimeError("migration target is occupied by non-identical state")
    else:
        started = transport.dispatch(start)
        result = started["result"]
        if result.get("type") != "Allow":
            raise RuntimeError(f"cannot open migration target: {result}")
        handle = result["run"]["id"]

    state, current_graph, current_knowledge = _existing_graph(service, handle)
    if state is not None and state.goal != goal:
        raise RuntimeError("migration target is occupied by a non-identical goal")
    if current_graph is not None and current_graph != canonical:
        raise RuntimeError("migration target is occupied by a non-identical claim graph")
    for record in knowledge.active_evidence_leaves(current_knowledge.evidence_leaves):
        evidence_id = record.get("evidence_id")
        if evidence_id not in expected_leaves or record.get("statement") != expected_leaves[evidence_id]:
            raise RuntimeError("migration target is occupied by non-identical evidence")
    if current_knowledge.evidence:
        raise RuntimeError("migration target contains non-legacy evidence")

    operations = 0
    if current_graph is None:
        response = transport.dispatch(build_graph_request(handle, graph,
                                                          correlation_id="legacy-migrate-graph"))
        if response["result"].get("type") != "Allow":
            raise RuntimeError(f"graph import failed: {response['result']}")
        operations += 1

    statements = [statement for _, statement in leaves]
    for evidence_id, statement in leaves:
        request = _build_migrated_leaf_request(
            handle, evidence_id, statement, canonical, statements,
            correlation_id=f"legacy-migrate-evidence-{evidence_id}")
        response = transport.dispatch(request)
        if response["result"].get("type") != "Allow":
            raise RuntimeError(f"evidence import failed for {evidence_id}: {response['result']}")
        operations += 1

    # A target ticket is application-minted.  On retry reuse the already-issued ticket, preventing
    # ticket growth and making the command idempotent.  The legacy nonce is binding, not secret; the
    # imported verdict is rebound to this target spawn while all claim/evidence/argument digests are
    # retained verbatim.
    state, _, _ = _existing_graph(service, handle)
    nonce = state.audit_tickets[0]["nonce"] if state and state.audit_tickets else None
    if nonce is None and (legacy_tickets or isinstance(verdict, dict)):
        ticket_response = transport.dispatch(build_audit_ticket_request(
            handle, actor, correlation_id="legacy-migrate-ticket"))
        if ticket_response["result"].get("type") != "Allow":
            raise RuntimeError(f"ticket import failed: {ticket_response['result']}")
        nonce = ticket_response["result"]["run"]["ticket"]["nonce"]
        operations += 1
    if isinstance(verdict, dict) and nonce:
        response = transport.dispatch(build_audit_verdict_request(
            handle, verdict, nonce=nonce, correlation_id="legacy-migrate-verdict"))
        if response["result"].get("type") != "Allow":
            raise RuntimeError(f"verdict import failed: {response['result']}")
        operations += 1

    response = transport.dispatch(build_attribution_request(
        handle, report, correlation_id="legacy-migrate-attribution"))
    if response["result"].get("type") != "Allow":
        raise RuntimeError(f"attribution import failed: {response['result']}")
    operations += 1

    return {"migrated": True, "idempotent": current_graph == canonical,
            "run_id": handle, "source": str(run_dir), "source_untouched": True,
            "evidence": len(leaves), "operations": operations}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--session-id")
    args = parser.parse_args(argv)
    try:
        report = migrate(args.run_dir, args.repo, session_id=args.session_id)
    except Exception as exc:  # explicit admin CLI: concise nonzero failure
        print(json.dumps({"migrated": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
