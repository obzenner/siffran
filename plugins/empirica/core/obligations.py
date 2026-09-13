"""Pure Empirica-to-obligation projection.

This module deliberately walks the normalized claim graph rather than ``Block.open_claims``:
the latter is an advisory, capped explanation, not the run's contract.
"""
from __future__ import annotations

from collections.abc import Iterable
import hashlib

from vendor.obligations import Contract, Obligation, Observation, Witness, verify, project

from . import claims
from .evidence import evidence_fold


def _witnesses(claim_id: str, node: dict) -> tuple[Witness, ...]:
    blocked = node.get("blocked")
    if blocked == "needs-decision":
        return (Witness("judgment", f"decision/{claim_id}", "pass",
                        "a human decision recorded for this claim"),)
    if blocked == "needs-budget":
        return (Witness("event", f"budget/{claim_id}", "pass",
                        "an authorized budget increase recorded before further work"),)
    research = Witness("artifact", f"research/{claim_id}", "pass",
                       "recorded Fold-1 research attestation supporting this claim")
    if node.get("kind") == "needs-experiment":
        return (research, Witness("exit_code", f"spike/{claim_id}", "pass",
                                  "passing recorded deterministic Fold-2 spike after research"))
    return (research,)


def observations_from_knowledge(evidence_leaves: Iterable[dict], verdicts: Iterable[dict],
                                *, audit_source: str = "audit dispatcher") -> tuple[Observation, ...]:
    """Map recorded machine facts to observations without inspecting explanatory prose."""
    observations: list[Observation] = []
    for leaf in evidence_leaves:
        statement = leaf.get("statement")
        subjects = statement.get("subject") if isinstance(statement, dict) else None
        claim_id = (subjects[0].get("name") if isinstance(subjects, list) and subjects
                    and isinstance(subjects[0], dict) else None)
        predicate = statement.get("predicate") if isinstance(statement, dict) else None
        fold = evidence_fold(statement)
        if not isinstance(claim_id, str) or not isinstance(predicate, dict):
            continue
        if fold == "research":
            # A validated research leaf records Fold 1 itself. Its combined approve verdict can
            # remain false until Fold 2 arrives, so never use it as a proxy for this witness.
            outcome = "pass" if predicate.get("result") == "supports" else "fail"
            observations.append(Observation("artifact", f"research/{claim_id}", outcome,
                                            "knowledge store", "recorded"))
        elif fold == "spike":
            outcome = "pass" if predicate.get("gate") == "pass" else "fail"
            observations.append(Observation("exit_code", f"spike/{claim_id}", outcome,
                                            "spike harness", "recorded"))

    for verdict in verdicts:
        digest = verdict.get("argument_digest")
        outcome = verdict.get("verdict")
        if isinstance(digest, str) and outcome in ("pass", "fail"):
            observations.append(Observation("judgment", f"audit/{digest}", outcome,
                                            audit_source, "recorded"))
    return tuple(observations)


def trusted(observation: Observation) -> bool:
    """Empirica's trust boundary: never trust the executing model."""
    if observation.kind == "exit_code":
        return observation.source == "spike harness"
    if observation.kind == "artifact":
        return observation.source == "knowledge store"
    if observation.kind == "judgment" and observation.ref.startswith("audit/"):
        return observation.source not in {"", "anonymous", "unknown", "model"}
    if observation.kind == "judgment" and observation.ref.startswith("decision/"):
        return observation.source == "human"
    return observation.kind == "event" and observation.source == "operator" and observation.ref.startswith("budget/")


def contract_for_graph(contract_id: str, revision: int, graph: dict, theta: float, evidence,
                       *, frozen_claims: tuple[str, ...] | None = None,
                       include_budget: bool = False, include_stall: bool = False,
                       audit_digest: str | None = None) -> Contract:
    """Build the complete live contract from graph derivation, in deterministic ID order."""
    def ev_ok(nid: str, purpose: str) -> bool:
        return evidence(nid, purpose)[0]

    frozen = set(frozen_claims or ())
    items: list[Obligation] = []
    for claim_id in sorted(claims.gating_goals(graph, theta, ev_ok)):
        node = graph["nodes"][claim_id]
        status = claims.state_of(graph, claim_id, theta, ev_ok)
        # Approval records observed witnesses; it is not retirement.  Keep approved claims in
        # the live set so verify() can expose their satisfied status across graph revisions.
        # Only an evidence-linked refutation retires a claim obligation.
        if status == claims.STATE_DISCARDED:
            continue
        hold = hold_reason = None
        if frozen_claims is not None and claim_id not in frozen:
            hold, hold_reason = "deferred", "claim is outside frozen scope"
        elif status == claims.STATE_BLOCKED:
            hold, hold_reason = "blocked", str(node.get("blocked") or "claim requires external action")
        items.append(Obligation(f"empirica/{claim_id}", "require", node.get("text") or claim_id,
                                _witnesses(claim_id, node), (claim_id,), hold, hold_reason))
    if audit_digest:
        approved = tuple(sorted(nid for nid in claims.gating_goals(graph, theta, ev_ok)
                                if claims.state_of(graph, nid, theta, ev_ok) == claims.STATE_APPROVED))
        items.append(Obligation(f"empirica/audit/{audit_digest}", "require",
                                "obtain an independent audit covering the current argument",
                                (Witness("judgment", f"audit/{audit_digest}", "pass",
                                         "independent audit pass covering the current argument and approved claims"),),
                                approved or (graph["root"],)))
    residual_ids = tuple(sorted(item.id.removeprefix("empirica/") for item in items))
    if include_budget:
        items.append(Obligation("empirica/run/budget", "require", "obtain authorized run budget",
                                (Witness("event", "budget/run", "pass", "authorized pass or spawn budget increase"),), residual_ids,
                                "blocked", "run budget exhausted"))
    if include_stall:
        resume_ref = hashlib.sha256(contract_id.encode("utf-8")).hexdigest()
        items.append(Obligation("empirica/run/stall", "require", "resume trusted evidence collection",
                                (Witness("event", f"resume/{resume_ref}", "pass", "renewed trusted evidence or audit observation"),), residual_ids,
                                "blocked", "run stalled"))
    return Contract(contract_id, revision, tuple(sorted(items, key=lambda item: item.id)), ("empirica",))


def view_for_graph(contract_id: str, revision: int, graph: dict, theta: float, evidence,
                   evidence_leaves: Iterable[dict], verdicts: Iterable[dict], **kwargs) -> dict:
    contract = contract_for_graph(contract_id, revision, graph, theta, evidence, **kwargs)
    observations = observations_from_knowledge(evidence_leaves, verdicts)
    accepted = tuple(item for item in observations if trusted(item))
    return project(contract, verify(contract, accepted, trusted), accepted)
