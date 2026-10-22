#!/usr/bin/env python3
"""Regenerate runtime-derived fields in Empirica contract fixtures."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "empirica"
sys.path.insert(0, str(PLUGIN))

from application.protocol import (  # noqa: E402
    contract_digest, contract_result, public_contract, stored, to_stored)
from core import spike_plan  # noqa: E402
from core.governance import human_wait, proposal_digest  # noqa: E402

FIXTURES = ROOT / "contracts" / "empirica" / "v2" / "fixtures"
STATE_FIXTURES = ROOT / "contracts" / "empirica" / "v2" / "state-fixtures"

_UNAVAILABLE_SPIKES = {"kind": "spikes_unavailable"}
_QUALIFIED_SPIKES = {
    "kind": "qualified", "backend": "docker", "profile_id": "docker-isolated-v1",
    "qualification_record_digest": "sha256:" + "a" * 64,
    "execution_binding_digest": "sha256:" + "b" * 64,
}


def _has_spike(node) -> bool:
    if isinstance(node, dict):
        if node.get("kind") == "spike":
            return True
        return any(_has_spike(value) for value in node.values())
    return isinstance(node, list) and any(_has_spike(value) for value in node)


def _has_spike_reason(node) -> bool:
    if isinstance(node, dict):
        if isinstance(node.get("code"), str) and node["code"].startswith("claim.spike_"):
            return True
        return any(_has_spike_reason(value) for value in node.values())
    return isinstance(node, list) and any(_has_spike_reason(value) for value in node)


def _spike_binding(document: dict) -> dict:
    requires_spikes = _has_spike(document) or _has_spike_reason(document)
    return dict(_QUALIFIED_SPIKES if requires_spikes else _UNAVAILABLE_SPIKES)


def _audit_summary(result: dict, run: dict) -> dict[str, object]:
    """Derive the bounded RunView audit summary for hand-authored conformance fixtures.

    Like the projection, only a failed audit carries its findings into the RunView.
    """
    # A settled verdict is not derivable from children, so a Block fixture (which has no argument)
    # authors it on the run view; required/pending stay derived from the children below.
    argument_audit = result.get("argument", {}).get("audit") or run.get("audit", {})
    verdict = argument_audit.get("verdict", "none")
    if run.get("status") == "converged":
        return {"verdict": "passed", "current": True, "pending": False, "independence": "distinct", "findings": []}
    pending = any(child.get("resource_class") == "audit" and child.get("state") in {"reserved", "launching", "pending"}
                  for child in run.get("children", ()))
    settled = verdict != "none" and not pending
    return {"verdict": verdict, "current": None if verdict == "none" else argument_audit.get("current", True),
            "pending": pending,
            "independence": argument_audit.get("independence") if settled else None,
            "findings": list(argument_audit.get("findings", [])) if settled and verdict == "failed" else []}


def _correlate_evidence_leaf(document: dict) -> None:
    requests: list[dict] = []
    results: list[dict] = []
    def collect(node) -> None:
        if isinstance(node, dict):
            if node.get("kind") == "spike_request" and "artifact_id" in node:
                requests.append(node)
            elif node.get("kind") == "spike" and "artifact_id" in node:
                results.append(node)
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)
    collect(document)
    if not requests or not results:
        return
    request, result = requests[0], results[0]
    def rewrite(node) -> None:
        if isinstance(node, dict):
            if node.get("kind") == "evidence_leaf" and isinstance(node.get("payload"), dict):
                payload = node["payload"]
                payload.update({
                    "harness_request_id": request["harness_request_id"],
                    "request_artifact_id": request["artifact_id"],
                    "plan_digest": request["plan_digest"],
                    "policy_digest": request["policy_digest"],
                    "snapshot_digest": request["snapshot_digest"],
                    "result_digest": result["artifact_id"],
                    "exit_code": result["exit_code"],
                    "file_bindings": result["file_bindings"],
                    "prerequisite_research_ids": request["prerequisite_research_ids"],
                })
            for value in node.values():
                rewrite(value)
        elif isinstance(node, list):
            for value in node:
                rewrite(value)
    rewrite(document)


def _persisted_operation_timeout(node) -> None:
    """Project the immutable framework operation bound on every public generic request fixture."""
    if isinstance(node, dict):
        if node.get("kind") == "spike_request" and "harness_request_id" in node:
            node["operation_timeout_seconds"] = spike_plan.BACKEND_OPERATION_TIMEOUT_SECONDS
        for value in node.values():
            _persisted_operation_timeout(value)
    elif isinstance(node, list):
        for value in node:
            _persisted_operation_timeout(value)


def generated(path: Path) -> str:
    document = stored(json.loads(path.read_text(encoding="utf-8")))
    command = document.get("request", {}).get("command", {})
    if (command.get("type") == "GetContract" and "expected" in document
            and not document.get("refused")):
        document["expected"]["result"]["contract_result"] = contract_result(
            command["target"], command.get("section_id"))
    result = document.get("expected", {}).get("result", {})
    run = result.get("run", {})
    binding = _spike_binding(document)
    if isinstance(run, dict) and run:
        run["spike_backend"] = dict(binding)
    argument = result.get("argument") if isinstance(result, dict) else None
    if isinstance(argument, dict):
        argument["spike_backend"] = dict(binding)
    governed = run.get("governance") if isinstance(run, dict) else None
    for child in document.get("children", ()):
        dossier = child.get("audit_argument") if isinstance(child, dict) else None
        if isinstance(dossier, dict) and isinstance(dossier.get("audit"), dict):
            # Persisted audit dossiers are argument views, so they carry the exact run backend and findings too.
            dossier["spike_backend"] = dict(binding)
            dossier["audit"].setdefault("findings", [])
    argument_audit = result.get("argument", {}).get("audit") if isinstance(result, dict) else None
    if isinstance(argument_audit, dict):
        # Hand-authored argument fixtures record no verdict findings unless they say so.
        argument_audit.setdefault("findings", [])
    if isinstance(run, dict) and run:
        run["audit"] = _audit_summary(result, run)
        # Hand-authored fixtures wait on no human unless they say so (ADR-0067).
        run.setdefault("confirmation_batch", None)
    # Hand-authored fixtures observed no human surface unless they say so (ADR-0067).
    for invocation in (run.get("invocation"), document.get("invocation"),
                       document.get("request", {}).get("command", {}).get("invocation"),
                       document.get("state", {}).get("invocation") if isinstance(document.get("state"), dict) else None):
        if isinstance(invocation, dict):
            invocation.setdefault("human_surface", "unavailable")
    if isinstance(run.get("contract"), dict):
        run["contract"]["digest"] = contract_digest()
    _single_container_spikes(document)
    _correlate_evidence_leaf(document)
    _persisted_operation_timeout(document)
    _live_contract_label(document)
    if isinstance(governed, dict) and "proposal_digest" in governed:
        goal = run["goal"]
        governed.pop("scope", None)
        governed["proposal_digest"] = proposal_digest(goal, governed)
    if isinstance(governed, dict) and "prompt_error" in governed:
        # Who acts on the configuration is the core's answer, never a fixture's (core.governance.human_wait).
        governed["human_wait"] = human_wait(governed, governed["prompt_error"])
    return json.dumps(to_stored(document), indent=2) + "\n"


def _single_container_spikes(node) -> None:
    """Normalize hand-authored public spike examples to the released /2 projection."""
    if isinstance(node, dict):
        if node.get("kind") == "spike_request" and "artifact_id" in node:
            draft = node.get("draft")
            if isinstance(draft, dict) and draft.get("schema") == "empirica.spike-plan-draft/2":
                environment = draft.get("environment", {})
                if isinstance(environment, dict):
                    draft["environment"] = sorted(environment)
            inputs = node.get("inputs")
            if isinstance(inputs, list) and inputs and isinstance(inputs[0], dict) \
                    and "file_bindings" in inputs[0]:
                node["inputs"] = [
                    {"path": binding["path"], "sha256": binding["sha256"], "bytes": 0}
                    for item in inputs for binding in item.get("file_bindings", [])
                ]
        facts = node.get("result_facts")
        if isinstance(facts, dict) and "containers" in facts:
            containers = facts.pop("containers")
            verdict_row = containers[0]
            verdict_exit = facts.pop("verdict_exit")
            facts.pop("verdict_container_id")
            facts.update({
                "run_id": "r-" + "1" * 32,
                "stdout": verdict_row["stdout"], "stderr": verdict_row["stderr"],
                "natural_exit": verdict_row["natural_exit"],
                "forced_termination": verdict_row["forced_termination"],
                "exit_code": verdict_exit,
            })
        for value in node.values():
            _single_container_spikes(value)
    elif isinstance(node, list):
        for value in node:
            _single_container_spikes(value)


def _live_contract_label(node) -> None:
    """The contract's version is the contract's own: every reference to ``empirica/public`` states the live one (G14 D5), so a
    label change is one edit of ``public-contract.json``, not one per fixture."""
    if isinstance(node, dict):
        if node.get("id") == "empirica/public" and "version" in node:
            node["version"] = public_contract()["version"]
        for value in node.values():
            _live_contract_label(value)
    elif isinstance(node, list):
        for value in node:
            _live_contract_label(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    paths = sorted(FIXTURES.glob("*.json")) + sorted(STATE_FIXTURES.glob("*.json"))
    for path in paths:
        rendered = generated(path)
        if args.check:
            if path.read_text(encoding="utf-8") != rendered:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_text(rendered, encoding="utf-8")
    if stale:
        print("stale generated contract fixtures: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
