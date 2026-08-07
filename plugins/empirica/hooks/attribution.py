#!/usr/bin/env python3
"""Attribution checks — is the independence ADR-20 P6 claims actually obtained? (ADR-24 §3)

ADR-24's finding 1 was not a design gap, it was a live defect in the shipped plugin: the auditor
and the spike-runner agent definitions BOTH declared `model: opus`, so "the author cannot grade
its own work" was, in the shipped configuration, the same weights re-grading their own reasoning.
Nothing detected it, because nothing recorded a model identity anywhere. This module is the
detector.

Two checks, both REPORT-ONLY:

  1. MISMATCH   — a claim assigned to actor X, evidenced by actor Y (§3.1).
  2. SAME-ACTOR — the audit's actor equals an approved claim's actor, so independence was NOT
                  obtained (§3.2). This is the one that makes finding 1 visible.

WHY REPORTING AND NOT BLOCKING (§3.3, and the reason is not timidity). In-session attribution is
DECLARED, not witnessed: `spawn_gate.py` sees `subagent_type`, and the model resolves from agent
frontmatter after the hook has already fired. A gate that failed a run closed on a declared field
would accuse compliant runs — the exact defect just fixed in the P1 route stamp, where a checker
that could never pass was as useless as no checker at all. So this follows the established P1
precedent: report on the allow path, let the auditor and the human judge. Blocking becomes
appropriate for a path once attribution there is WITNESSED (Mode B), not before.

The honest reading of a clean result from this module is therefore "no clash was DETECTED among
the attributions that were RECORDED" — never "independence was proven". A run with no actors
recorded anywhere produces no findings at all, and `coverage` exists so a report can say so out
loud instead of presenting silence as a pass.
"""
import importlib.util
from pathlib import Path


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_actors = _load("actors")

MISMATCH = "actor_mismatch"
SAME_ACTOR = "same_actor_audit"


def evidence_actors(leaves: list[dict]) -> dict[str, list[dict]]:
    """claim_id → the actors that produced evidence for it. Only leaves carrying an actor
    contribute; a leaf without one is silence, not a match."""
    out: dict[str, list[dict]] = {}
    for leaf in leaves:
        actor = leaf.get("actor")
        if actor:
            out.setdefault(leaf["claim_id"], []).append(actor)
    return out


def model_actors(leaves: list[dict]) -> dict[str, list[dict]]:
    """claim_id → the MODEL actors only. A spike's CODE actor is excluded.

    ONE definition of "was this claim answered by a judge?", because the same filter was written
    inline in three places — `check_mismatch`, `check_same_actor_audit`, and `coverage` — and an
    audit found that mutating the copy inside `coverage` flipped a spike-only run from "independence
    could NOT be checked" to "no clash detected" while the suite stayed green. That is the vacuity
    failure re-entering through the one place it was not guarded, which is precisely what duplicated
    predicates do.
    """
    # Dict comprehension rather than a build-and-guard loop: an audit found the `if models:` guard
    # was itself a behaviour — forcing it true added EMPTY entries, which inflated
    # `model_attributed` from 0 to 1 and flipped `vacuous` from True to False on a spike-only run.
    # That is the vacuity defect returning for a fourth time through a guard excused as harmless.
    # Expressing the filter as a comprehension removes the branch instead of guarding it.
    return {claim: models
            for claim, actors_for_claim in evidence_actors(leaves).items()
            if (models := [a for a in actors_for_claim
                           if a.get("source_type") != _actors.CODE])}


def check_mismatch(graph: dict, leaves: list[dict]) -> list[dict]:
    """§3.1 — claims whose ASSIGNED actor is not among the actors that actually evidenced them.

    Only reports when BOTH sides are known: an assignment with no attributed evidence is not a
    mismatch, it is an unanswered question, and reporting it as a mismatch would manufacture
    findings out of missing data. A spike's CODE actor never counts as a model mismatch — the
    exit code is the approver (ADR-13), so a claim assigned to a model and evidenced by the
    harness is working exactly as designed.
    """
    by_claim = model_actors(leaves)
    findings = []
    for nid, node in graph["nodes"].items():
        assigned = node.get("actor")
        if not assigned:
            continue
        observed = by_claim.get(nid, [])
        if not observed:
            continue
        if not any(_actors.same_actor(assigned, a) for a in observed):
            findings.append({
                "check": MISMATCH, "claim": nid,
                "assigned": assigned["model"],
                "observed": sorted({a["model"] for a in observed}),
                "detail": (f"claim {nid} is assigned to `{assigned['model']}` but its evidence is "
                           f"attributed to {sorted({a['model'] for a in observed})}. The claim was "
                           f"resolved by a different actor than the one it was routed to."),
            })
    return findings


def check_same_actor_audit(approved: list[str], leaves: list[dict],
                           audit_actor) -> list[dict]:
    """§3.2 — did the AUDIT run on the same model as the work it certifies?

    One finding per clashing claim, so a report can name them. Compares models only: the same
    weights reached through a different harness or provider are still the same weights, and
    letting harness/provider launder that would defeat the check entirely (see
    `actors.same_actor`).
    """
    if not _actors.normalise(audit_actor):
        return []
    by_claim = model_actors(leaves)
    findings = []
    for nid in approved:
        for actor in by_claim.get(nid, []):
            if _actors.same_actor(actor, audit_actor):
                findings.append({
                    "check": SAME_ACTOR, "claim": nid,
                    "model": actor["model"],
                    "attribution": actor["attribution"],
                    "detail": (f"the audit ran on `{actor['model']}`, the same model that "
                               f"evidenced approved claim {nid}. ADR-20 P6 independence was NOT "
                               f"obtained: this is the author's own weights re-grading their own "
                               f"reasoning. Attribution strength: {actor['attribution']}."),
                })
                break
    return findings


def coverage(graph: dict, leaves: list[dict], audit_actor=None) -> dict:
    """How much of this run carries attribution at all — so a report can distinguish "no clash
    found" from "nothing was recorded to compare".

    Without this, an empty finding list reads as a pass, and a run that recorded no actors
    whatsoever would look maximally independent. That is the vacuity failure this whole plugin
    exists to remove, and it applies to its own new checks too.

    What this measures is COVERAGE, not correctness: it says how many claims carry an attribution
    and whether the auditor was identified, so a reader can tell a checked result from an unchecked
    one. It cannot say whether a recorded attribution is TRUE — on the in-session path nothing
    witnessed it (see the module header), so a wrong `actor` counts as covered.
    """
    goals = [nid for nid, n in graph["nodes"].items() if n["type"] == "Goal"]
    assigned = [nid for nid in goals if graph["nodes"][nid].get("actor")]
    attributed = model_actors(leaves)
    model_attributed = set(attributed)
    witnessed = {cid for cid, actors in attributed.items()
                 if any(a.get("attribution") == _actors.WITNESSED for a in actors)}
    # BOTH SIDES of the comparison must be known, or the check cannot fire. An independent audit
    # found this flag was computed from the claim side ALONE, so an audit actor that was None, a
    # tier alias, or policy-excluded produced "no attribution clash detected" — a clean-looking
    # result from a comparison that never ran. That was this run's own live state at audit time,
    # which is the strongest possible argument for the fix.
    audit_known = normalised_audit = _actors.normalise(audit_actor)
    audit_is_tier = bool(normalised_audit and normalised_audit["is_tier"])
    return {
        "goals": len(goals),
        "assigned": len(assigned),
        "model_attributed": len(model_attributed),
        "witnessed": len(witnessed),
        "audit_attributed": bool(audit_known),
        "audit_is_tier": audit_is_tier,
        # The headline honesty flag: the same-actor check needs an attributed claim AND an
        # identified auditor. Missing either means its silence means nothing. A tier-named auditor
        # counts as unidentified, because "capable" says nothing about which weights ran.
        "vacuous": (not model_attributed) or (not audit_known) or audit_is_tier,
    }


def report(graph: dict, leaves: list[dict], approved: list[str], audit_actor) -> dict:
    """The whole §3 result: findings plus the coverage that says how much they are worth.

    Never raises and never blocks. Shaped for the Stop gate's allow-path payload, where it sits
    beside `converged` so a human reading the result sees whether independence was obtained —
    rather than it only appearing in a block message the agent may never surface.
    """
    findings = check_mismatch(graph, leaves) + check_same_actor_audit(approved, leaves,
                                                                     audit_actor)
    cov = coverage(graph, leaves, audit_actor)
    out: dict = {"findings": findings, "coverage": cov}
    if findings:
        out["note"] = (f"{len(findings)} attribution finding(s) — reported, not blocking "
                       f"(ADR-24 §3.3). " + findings[0]["detail"])
    elif cov["vacuous"]:
        # Name WHICH side is missing. "Unmeasured" is only actionable if the reader knows what to
        # record to measure it.
        missing = []
        if not cov["model_attributed"]:
            missing.append("no claim's evidence carries an actor")
        if not cov["audit_attributed"]:
            missing.append("the audit's actor was not recorded")
        elif cov["audit_is_tier"]:
            missing.append("the audit's actor names a TIER, not a model generation")
        out["note"] = ("P6 independence could NOT be checked: " + "; ".join(missing) +
                       ". This is not a clean result — it is an unmeasured one (ADR-24 §3).")
    else:
        out["note"] = (f"no attribution clash detected across {cov['model_attributed']} "
                       f"attributed claim(s); {cov['witnessed']} witnessed, the rest declared.")
    return out
