"""Inactive Claude knowledge-plane translations for the host-neutral application.

Nothing in this module is registered as a hook.  Normal operations only build ``empirica/v1``
requests and dispatch them through :class:`BridgeTransport`; they never read or write a Claude/Pi
run directory.  The one producer of a fresh spike approval is :func:`run_spike`, which invokes the
existing deterministic ``spike_harness.py`` subprocess and seals its observed result in a
:class:`SpikeExecution` before a request can be built.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .correlation import request_id

_PLUGIN = Path(__file__).resolve().parents[2]
_HOOKS = _PLUGIN / "hooks"
_STATEMENT = "https://in-toto.io/Statement/v1"
_RESEARCH = "https://empirica.dev/attestation/research/v1"
_SPIKE = "https://empirica.dev/attestation/spike/v1"


def _load_hook(name: str):
    spec = importlib.util.spec_from_file_location(f"empirica_legacy_{name}", _HOOKS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _envelope(command: dict, correlation_id: str | None) -> dict:
    operation = str(command.get("action", {}).get("kind") or command.get("type") or "knowledge")
    rid = correlation_id or request_id({}, operation)
    return {"protocol": "empirica/v1", "request_id": rid,
            "command": command}


def build_graph_request(run_id: str, graph: dict, *, correlation_id: str | None = None) -> dict:
    return _envelope({"type": "ObserveAction", "run_id": run_id,
                      "action": {"kind": "graph", "graph": graph}}, correlation_id)


def _normalised_leaves(statements: Iterable[dict]) -> list[dict]:
    evidence = _load_hook("evidence")
    return [leaf for statement in statements if (leaf := evidence.validate_leaf(statement))]


def _leaf_action(evidence_id: str, statement: dict, graph: dict, statements: Iterable[dict],
                 *, supersedes: str | None = None) -> dict:
    evidence = _load_hook("evidence")
    leaf = evidence.validate_leaf(statement)
    if leaf is None:
        raise ValueError("evidence is not a valid empirica in-toto statement")
    node = graph.get("nodes", {}).get(leaf["claim_id"])
    if not isinstance(node, dict):
        raise ValueError(f"claim {leaf['claim_id']!r} is absent from the graph")
    leaves = _normalised_leaves(statements)
    verdicts = {}
    for purpose in ("approve", "refute"):
        ok, reason = evidence.verdict(leaves, leaf["claim_id"], node.get("text", ""),
                                      node.get("kind"), purpose)
        verdicts[purpose] = {"ok": ok, "reason": reason}
    action = {"kind": "evidence_leaf", "evidence_id": evidence_id,
              "statement": statement, "verdicts": verdicts}
    if supersedes is not None:
        action["supersedes"] = supersedes
    return action


def build_research_request(run_id: str, evidence_id: str, statement: dict, graph: dict,
                           statements: Iterable[dict], *, correlation_id: str | None = None) -> dict:
    """Translate an existing research statement while retaining its exact digest-bearing body."""
    if statement.get("predicateType") != _RESEARCH:
        raise ValueError("research translation requires a research/v1 statement")
    return _envelope({"type": "ObserveAction", "run_id": run_id,
                      "action": _leaf_action(evidence_id, statement, graph, statements)},
                     correlation_id)


_SPIKE_PROOF = object()


@dataclass(frozen=True)
class SpikeExecution:
    """A spike statement plus the harness result that authorised its ``gate`` value.

    Instances are accepted only when sealed by :func:`run_spike`; constructing this public value by
    hand does not create the private proof consumed by :func:`build_spike_request`.
    """
    statement: dict
    harness_result: dict
    _proof: object = None


def _files_digest(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for path in sorted(paths, key=lambda p: str(p)):
        h.update(str(path).encode())
        h.update(b"\0")
        try:
            h.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        except OSError:
            h.update(b"__absent__")
        h.update(b"\0")
    return h.hexdigest()


def _result_hash(result: dict) -> str:
    runs = result.get("runs")
    if runs:
        h = hashlib.sha256()
        for run in runs:
            h.update(run["stdout_sha256"].encode("ascii"))
            h.update(b"\0")
        return h.hexdigest()
    return hashlib.sha256("\n".join(result["stdout_tail"]).encode("utf-8")).hexdigest()


def run_spike(claim_id: str, claim_text: str, command: list[str], files: list[Path], ts: str,
              *, repeat: int = 1, timeout: float = 300) -> SpikeExecution:
    """Execute the deterministic harness subprocess and construct the exact in-toto leaf.

    There is deliberately no public constructor accepting a gate.  Existing leaves enter through
    the explicit migration command; every *fresh* normal/regate leaf gets its verdict here from the
    harness process's reported subprocess return code.
    """
    argv = [sys.executable, str(_HOOKS / "spike_harness.py"), "--report-only",
            "--timeout", str(timeout), "--repeat", str(max(1, repeat)), *command]
    proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")
    try:
        result = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"spike harness returned invalid JSON: {proc.stderr.strip()}") from exc
    gate = result.get("gate")
    if gate not in ("pass", "fail"):
        raise RuntimeError("spike harness did not report a deterministic pass/fail gate")
    runs = result.get("runs") if isinstance(result.get("runs"), list) else None
    codes = [r.get("returncode") for r in runs] if runs else [result.get("returncode")]
    samples = len(runs) if runs else 1
    predicate = {"fold": "spike", "kind": "spike", "command": list(command),
                 "command_hash": hashlib.sha256("\0".join(command).encode()).hexdigest(),
                 "gate": gate,
                 "hashes": {"result": _result_hash(result), "files": _files_digest(files)},
                 "files": [str(p) for p in files], "ts": ts, "samples": samples,
                 "exit_codes": codes,
                 "actor": {"source_type": "CODE", "model": "spike_harness.py",
                           "harness": "claude-code", "attribution": "witnessed"}}
    statement = {"_type": _STATEMENT,
                 "subject": [{"name": claim_id,
                              "digest": {"sha256": hashlib.sha256(claim_text.encode()).hexdigest()}}],
                 "predicateType": _SPIKE, "predicate": predicate}
    return SpikeExecution(statement, result, _SPIKE_PROOF)


def build_spike_request(run_id: str, evidence_id: str, execution: SpikeExecution, graph: dict,
                        statements: Iterable[dict], *, supersedes: str | None = None,
                        correlation_id: str | None = None,
                        _replaced_statement: dict | None = None) -> dict:
    if execution._proof is not _SPIKE_PROOF:
        raise ValueError("spike execution was not produced by the deterministic harness")
    prior = [s for s in statements if s is not _replaced_statement]
    all_statements = [*prior, execution.statement]
    return _envelope({"type": "ObserveAction", "run_id": run_id,
                      "action": _leaf_action(evidence_id, execution.statement, graph,
                                             all_statements, supersedes=supersedes)}, correlation_id)


def build_audit_ticket_request(run_id: str, actor: dict | None = None, *, witnessed: bool = False,
                               correlation_id: str | None = None) -> dict:
    action: dict = {"kind": "audit_ticket"}
    if actor is not None:
        action.update(actor=actor, witnessed=witnessed)
    return _envelope({"type": "ObserveAction", "run_id": run_id, "action": action}, correlation_id)


def build_audit_verdict_request(run_id: str, verdict: dict, *, nonce: str | None = None,
                                correlation_id: str | None = None) -> dict:
    action = {"kind": "audit_verdict", "verdict": verdict.get("verdict"),
              "nonce": nonce or verdict.get("nonce"),
              "argument_digest": verdict.get("argument_digest"),
              "claims_reviewed": verdict.get("claims_reviewed", []),
              "findings": verdict.get("findings", [])}
    return _envelope({"type": "ObserveAction", "run_id": run_id, "action": action}, correlation_id)


def build_attribution_request(run_id: str, report: dict, *,
                              correlation_id: str | None = None) -> dict:
    return _envelope({"type": "ObserveAction", "run_id": run_id,
                      "action": {"kind": "attribution", "report": report}}, correlation_id)


def build_regate_requests(run_id: str, graph: dict, stored_leaves: list[dict], ts: str, *,
                          timeout: float = 300) -> list[dict]:
    """Re-execute only stale spike heads and return replacement ObserveAction requests."""
    evidence = _load_hook("evidence")
    superseded = {entry.get("supersedes") for entry in stored_leaves
                  if isinstance(entry.get("supersedes"), str)}
    active = [entry for entry in stored_leaves if entry.get("artifact_id") not in superseded]
    statements = [entry["statement"] for entry in active]
    requests = []
    for entry in active:
        leaf = evidence.validate_leaf(entry["statement"])
        if not leaf or leaf["fold"] != "spike" or not leaf["files"]:
            continue
        paths = [Path(p) for p in leaf["files"]]
        if leaf["files_hash"] == _files_digest(paths):
            continue
        node = graph.get("nodes", {}).get(leaf["claim_id"])
        if not isinstance(node, dict) or not leaf["command"]:
            continue
        execution = run_spike(leaf["claim_id"], node.get("text", ""), leaf["command"], paths, ts,
                              repeat=leaf["samples"], timeout=timeout)
        requests.append(build_spike_request(
            run_id, entry["evidence_id"], execution, graph, statements,
            supersedes=entry["artifact_id"], correlation_id=f"regate-{leaf['claim_id']}",
            _replaced_statement=entry["statement"]))
        statements.append(execution.statement)
    return requests


# Deliberately private: only the explicit migration tool may admit a pre-existing spike statement.
def _build_migrated_leaf_request(run_id: str, evidence_id: str, statement: dict, graph: dict,
                                 statements: Iterable[dict], *,
                                 correlation_id: str | None = None) -> dict:
    return _envelope({"type": "ObserveAction", "run_id": run_id,
                      "action": _leaf_action(evidence_id, statement, graph, statements)},
                     correlation_id)
