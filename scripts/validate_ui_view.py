#!/usr/bin/env python3
"""Check the observer read model (``contracts/empirica-ui-view/v1``) and its fixtures.

    python3 scripts/validate_ui_view.py                 committed fixtures + enum mirrors
    python3 scripts/validate_ui_view.py VIEW [RUNS]      a view (and its details) from observe

Three kinds of check, each a pure function returning one line per problem:

* ``mirror_problems``: every enumeration in the two schemas equals its owner (the public-contract
  registry, the state schema, or the plugin's history kinds), so the contract cannot drift from
  the plugin it describes.
* ``view_problems``: the schema, then what a schema cannot say: unique keys, a project's runs
  newest first, families that list each run's attempts by generation, ``previous`` naming the same run's
  prior generation, unique labels, a coverage that counts the diagnostics, claims and edges closed over
  the run, diagnostics only on partial rows (whose derived facts are null, never zero), blocker codes that
  are real reasons, and the token.
* ``detail_problems``: each detail against its schema and its row in the view.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/empirica-ui-view/v1"
V2 = ROOT / "contracts/empirica/v2"
FIXTURES = CONTRACT / "fixtures"
sys.path.insert(0, str(ROOT / "plugins/empirica"))

from application.history_records import HISTORY_KINDS  # noqa: E402
from core import citations, confirmation  # noqa: E402
from core.canonical import canonical_digest  # noqa: E402
from core.evaluation import INDEPENDENCE_REASONS  # noqa: E402
from core.observer import (ACTORS, ASSURANCE_STATES, ATTENTION_STATES, CLAIM_STANDING, DECISION_STATUSES,  # noqa: E402
                           EVIDENCE_KINDS, HUMAN_REACHABLE, LATER, PLACES, REVIEW_STATES, SPIKE_STATES, STEP_SUBJECTS, message_problems)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def schemas() -> tuple[dict, dict]:
    return _load(CONTRACT / "view.schema.json"), _load(CONTRACT / "run.schema.json")


def registry() -> dict:
    return _load(V2 / "public-contract.json")


def _enum(schema: dict, *path: str) -> set:
    node = schema
    for step in path:
        node = node[step]
    if "oneOf" in node:
        node = next(alt for alt in node["oneOf"] if alt.get("type") != "null")
    return {v for v in node["enum"] if v is not None}


def mirror_problems(view_schema: dict, run_schema: dict, reg: dict, state_schema: dict) -> list[str]:
    defs = view_schema["$defs"]
    run = defs["run"]["properties"]
    claim = defs["claim"]["properties"]
    audit = defs["auditSummary"]["properties"]
    detail = run_schema["properties"]
    step = detail["steps"]["oneOf"][1]["items"]["properties"]
    record = run_schema["$defs"]["record"]["properties"]
    item = detail["outstanding"]["oneOf"][1]["items"]["properties"]
    decision = detail["claims"]["oneOf"][1]["items"]["properties"]["decision"]["oneOf"][1]["properties"]
    config = detail["governance"]["oneOf"][1]["properties"]
    governance = state_schema["properties"]["governance"]["properties"]
    waits = record["wait"]["oneOf"][1]["properties"]
    proof = detail["claims"]["oneOf"][1]["items"]["properties"]["proof"]["items"]["properties"]
    ask = {v for v in confirmation.ASK_WHERE.values()}
    pairs = [
        ("view run.state", _enum(run, "state"), set(reg["statuses"]), "registry statuses"),
        ("view run.attention", _enum(run, "attention"), set(ATTENTION_STATES), "core.observer.ATTENTION_STATES"),
        ("view audit.verdict", _enum(audit, "verdict"), set(reg["audit_outcomes"]), "registry audit_outcomes"),
        ("view audit.independence", _enum(audit, "independence"), set(reg["independence_states"]),
         "registry independence_states"),
        ("view claim.kind", _enum(claim, "kind"), set(reg["claim_kinds"]), "registry claim_kinds"),
        ("view claim.state", _enum(claim, "state"), set(reg["claim_states"]), "registry claim_states"),
        ("view claim.evidence", _enum(claim, "evidence"), set(EVIDENCE_KINDS), "core.observer.EVIDENCE_KINDS"),
        ("view claim.review", _enum(claim, "review"), set(REVIEW_STATES), "core.observer.REVIEW_STATES"),
        ("view claim.assurance", _enum(claim, "assurance"), set(ASSURANCE_STATES), "core.observer.ASSURANCE_STATES"),
        ("run stale.state", _enum(detail["stale"]["oneOf"][1]["items"]["properties"], "state"),
         set(reg["freshness_states"]), "registry freshness_states"),
        ("run governance.mode", _enum(config, "mode"), _enum(governance, "control_mode"),
         "state.schema governance.control_mode"),
        ("run governance.approval", _enum(config, "approval"), _enum(governance, "state"),
         "state.schema governance.state"),
        ("record class", _enum(record, "class"),
         {citations.VERBATIM, citations.RETRIEVABLE, citations.OBSERVED}, "core.citations classes"),
        ("record status", _enum(record, "status"),
         {confirmation.VERIFIED_BY_SERVICE, confirmation.CONFIRMED_BY_HUMAN,
          confirmation.REJECTED_BY_HUMAN, confirmation.PENDING}, "core.confirmation statuses"),
        ("record wait.who", _enum(waits, "who"), {"auditor", "human"}, "core.observer._wait"),
        ("record wait.where", _enum(waits, "where"), set(PLACES), "core.observer.PLACES"),
        ("outstanding who", _enum(item, "who"), set(ACTORS), "core.observer.ACTORS"),
        ("outstanding where", _enum(item, "where"), (set(PLACES) | {LATER}) - {"nobody"},
         "core.observer.PLACES and LATER (an ended run owes nothing)"),
        ("decision status", _enum(decision, "status"), set(DECISION_STATUSES), "core.observer.DECISION_STATUSES"),
        ("decision outcome", _enum(decision, "outcome"), {confirmation.APPROVE, confirmation.REJECT},
         "core.confirmation decisions"),
        ("decision where", _enum(decision, "where"), ask, "core.confirmation.ASK_WHERE"),
        ("run proof.state", _enum(proof, "state"), set(SPIKE_STATES), "core.observer.SPIKE_STATES"),
        ("run proof.gate", _enum(proof, "gate"), set(reg["spike_gates"]), "registry spike_gates"),
        ("run proof.outcome", _enum(proof, "outcome"), {"pass", "fail"}, "registry artifact_outcomes (a spike's)"),
        ("run steps.kind", _enum(step, "kind"), set(HISTORY_KINDS), "history_records.HISTORY_KINDS"),
        ("run steps.subject", _enum(step, "subject"), set(STEP_SUBJECTS), "core.observer.STEP_SUBJECTS"),
        ("run steps.standing", _enum(step, "standing"), set(CLAIM_STANDING), "core.observer.CLAIM_STANDING"),
        ("run steps.outcome", _enum(step, "outcome"),
         set(reg["artifact_outcomes"]) | set(reg["audit_verdicts"])
         | {confirmation.CONFIRM, confirmation.REJECT, confirmation.APPROVE},
         "registry artifact_outcomes + audit_verdicts + core.confirmation outcomes"),
    ]
    return [f"{name} {sorted(ours)} != {owner} {sorted(theirs)}"
            for name, ours, theirs, owner in pairs if ours != theirs]


def _structure(instance: Any, schema: dict, where: str) -> list[str]:
    return [f"{where}{''.join(f'[{p!r}]' for p in e.absolute_path)}: {e.message}"
            for e in Draft202012Validator(schema).iter_errors(instance)]


def token_of(view: dict) -> str:
    """``core.canonical.canonical_digest`` of each project's [id, name, repo, [[key, version], ...]]
    in order (compact, sorted)."""
    pairs = [[p["id"], p["name"], p["repo"], [[run["key"], run["version"]] for run in p["runs"]]]
             for p in view["projects"]]
    return canonical_digest(pairs)


def _newest(run: dict) -> tuple:
    return (run["updated"] is None, -(run["updated"] or 0.0), run["key"])


def view_problems(view: dict, view_schema: dict, reasons: set[str]) -> list[str]:
    out = _structure(view, view_schema, "view")
    if out:
        return out
    runs: dict[str, dict] = {}
    ids = [p["id"] for p in view["projects"]]
    if len(set(ids)) != len(ids):
        out.append("projects: duplicate id")
    labels = [p["label"] for p in view["projects"]]
    if len(set(labels)) != len(labels):
        out.append("projects: two projects share a label")
    firsts = [_newest(p["runs"][0]) for p in view["projects"]]
    if firsts != sorted(firsts):
        out.append("projects: not ordered by their newest run")
    for project in view["projects"]:
        order = [_newest(r) for r in project["runs"]]
        if order != sorted(order):
            out.append(f"project {project['id']}: runs not newest first")
        keys = {r["key"]: r for r in project["runs"]}
        listed = [k for f in project["families"] for k in f["attempts"]]
        if sorted(listed) != sorted(keys) or len(set(listed)) != len(listed):
            out.append(f"project {project['id']}: families must list every run exactly once")
        for family in project["families"]:
            attempts = [keys[k] for k in family["attempts"] if k in keys]
            if [r["generation"] for r in attempts] != sorted(r["generation"] for r in attempts) or any(
                    r["run"] != family["run"] for r in attempts):
                out.append(f"project {project['id']}: family {family['run']} is not one run's attempts by generation")
        for run in project["runs"]:
            at = f"run {run['key']}"
            if run["key"] in runs:
                out.append(f"{at}: duplicate key")
            runs[run["key"]] = run
            if run["project"] != project["id"]:
                out.append(f"{at}: project {run['project']} listed under {project['id']}")
            parts = run["key"].split("/")
            if len(parts) != 3 or parts[0] != run["project"] or parts[1] != run["run"] or parts[2] != str(run["generation"]):
                out.append(f"{at}: key is not <project>/<run>/<generation>")
            partial = run["diagnostic"] is not None
            derived = ("audit", "claims", "edges", "attention", "progress")
            if partial and (run["next"] is not None or any(run[f] is not None for f in derived)):
                out.append(f"{at}: a {run['diagnostic']} row carries derived facts")
            if not partial and (run["state"] is None or any(run[f] is None for f in derived)):
                out.append(f"{at}: a fully read row lacks its state or a derived fact")
            if run["diagnostic"] == "unreadable" and (run["state"] is not None or run["goal"] is not None
                                                      or run["session"] is not None):
                out.append(f"{at}: an unreadable row reports facts of its state")
            if run["diagnostic"] != "unreadable" and (run["state"] is None or run["goal"] is None or run["session"] is None):
                out.append(f"{at}: a decoded state reports its status, goal and session")
            if not partial and (run["attention"] == "ended") != (run["state"] != "active"):
                out.append(f"{at}: attention is ended exactly when the run is not active")
            if partial:
                continue
            claims = run["claims"]
            claim_ids = [c["id"] for c in claims]
            if len(set(claim_ids)) != len(claim_ids):
                out.append(f"{at}: duplicate claim id")
            if (run["root"] is None) != (not claim_ids) or (
                    run["root"] is not None and run["root"] not in claim_ids):
                out.append(f"{at}: root must be one of its claims, and null exactly when it has none")
            out += [f"{at}: edge {e['from']}->{e['to']} leaves the run's claims"
                    for e in run["edges"] if not {e["from"], e["to"]} <= set(claim_ids)]
            scoped = [c for c in claims if c["gating"]]
            if run["progress"] != {"required": len(scoped), "approved": sum(c["state"] == "approved" for c in scoped)}:
                out.append(f"{at}: progress is not the count of its scoped and approved claims")
            for claim in claims:
                where = f"{at} claim {claim['id']}"
                if claim["blocker"] is not None and claim["blocker"] not in reasons:
                    out.append(f"{where}: blocker {claim['blocker']!r} is not a registry reason")
                blocked = claim["gating"] and claim["state"] != "approved"
                if (claim["blocker"] is not None) != blocked:
                    out.append(f"{where}: exactly the scoped claims that are not approved have a blocker")
                if (claim["blocker_claim"] is None) != (claim["blocker"] is None) or (
                        claim["blocker_claim"] is not None and claim["blocker_claim"] not in claim_ids):
                    out.append(f"{where}: blocker_claim must be one of the run's claims, null exactly "
                               "when blocker is")
                # Only what the view itself contradicts is checked here: whether the gate accepts the audit is
                # core.evaluation.audit_acceptance's, and tests/v2/test_observer_agreement.py proves the two agree.
                if claim["assurance"] == "assured" and (claim["state"] != "approved" or claim["review"] != "passed"
                                                        or run["audit"]["pending"]):
                    out.append(f"{where}: assured is never said of a claim that is not approved, not reviewed by a current "
                               "pass, or under a pending re-audit")
                if (claim["assurance"] == "assured" and all(c["gating"] for c in claims)
                        and run["audit"]["independence"] in INDEPENDENCE_REASONS):
                    out.append(f"{where}: assured under an audit the registry names as not independent")
                if (claim["assurance"] == "not_approved") != (claim["state"] != "approved"):
                    out.append(f"{where}: not_approved exactly when the claim is not approved")
            audit = run["audit"]
            if (audit["verdict"] == "none") != (audit["current"] is None):
                out.append(f"{at}: the audit's currency is null exactly when it has no verdict")
            if audit["verdict"] == "none" and audit["independence"] is not None:
                out.append(f"{at}: independence of an audit that has no verdict")
    for run in runs.values():
        prev = run["previous"]
        if prev is None:
            continue
        before = runs.get(prev)
        if (before is None or before["project"] != run["project"]
                or prev.split("/")[1] != run["key"].split("/")[1]
                or before["generation"] != run["generation"] - 1):
            out.append(f"run {run['key']}: previous {prev} is not this run's prior generation in the view")
    unreadable = sum(r["diagnostic"] == "unreadable" for r in runs.values())
    read = sum(r["diagnostic"] is None for r in runs.values())
    if view["coverage"] != {"runs": len(runs), "read": read, "unreadable": unreadable,
                            "partial": len(runs) - read - unreadable}:
        out.append("view: coverage does not count the runs by their diagnostics")
    if view["token"] != token_of(view):
        out.append("view: token is not the digest of the ordered run versions")
    return out


def fits(template: str, message: str) -> bool:
    """Whether ``message`` is ``template`` with every ``{name}`` placeholder filled in."""
    pattern = re.sub(r"\\\{\w+\\\}", ".+?", re.escape(template))
    return re.fullmatch(pattern, message, re.DOTALL) is not None


def _wordings(spec: dict, ended: bool) -> list[str]:
    """Every text ``confirmation.reason_text`` may choose for a reason (the ended wording alone for a stopped run)."""
    if ended and "ended_message" in spec:
        return [spec["ended_message"]]
    return [spec["message"], *(spec.get("case_messages") or {}).values()]


def _worded(spec: dict, text: str, ended: bool) -> bool:
    return any(fits(template, text) for template in _wordings(spec, ended))


def detail_problems(details: dict, view: dict, run_schema: dict, reg: dict) -> list[str]:
    out: list[str] = []
    rows = {r["key"]: r for p in view["projects"] for r in p["runs"]}
    if set(details) != set(rows):
        out.append(f"details: keys differ from the view's runs ({len(details)} vs {len(rows)})")
    specs = reg["reasons"]
    for key, detail in sorted(details.items()):
        at = f"detail {key}"
        problems = _structure(detail, run_schema, at)
        if problems:
            out += problems
            continue
        row = rows.get(key)
        if row is None:
            continue
        if detail["key"] != key or detail["version"] != row["version"]:
            out.append(f"{at}: key or version differs from its view row")
        read = row["diagnostic"] is None
        if (detail["audit"] is not None) != read:
            out.append(f"{at}: audit detail must be present exactly when the row was read in full")
        if (detail["governance"] is None) != (row["diagnostic"] == "unreadable"):
            out.append(f"{at}: governance must be null exactly when the state was not decoded")
        gov = detail["governance"]
        if gov is not None and (gov["proposed"] is not None) != gov["proposal_pending"]:
            out.append(f"{at}: ceilings are proposed exactly while a proposal waits on the human")
        stop = detail["stop"]
        for field in ("outstanding", "stale", "steps", "claims"):
            if (detail[field] is not None) != read:
                out.append(f"{at}: {field} must be present exactly when the row was read in full")
        if (detail["residuals"] is not None) != (read and (stop is None or stop["basis"] == "recorded")):
            out.append(f"{at}: residuals must be present exactly when the row was read in full and its stop is recorded")
        if (stop is not None) != (read and row["state"] != "active"):
            out.append(f"{at}: stop must be present exactly for a run that was read in full and has ended")
        if detail["now"] is not None and (stop is None or stop["basis"] != "recorded"):
            out.append(f"{at}: today's evaluation is published only beside a recorded stop")
        if not read:
            if detail["goal"] != row["goal"]:
                out.append(f"{at}: goal differs from its view row")
            continue
        ended = row["state"] != "active"
        if stop is not None and stop["status"] != row["state"]:
            out.append(f"{at}: stop status differs from its view row")
        if detail["goal"] != row["goal"]:
            out.append(f"{at}: goal differs from its view row")
        if [c["id"] for c in detail["claims"]] != [c["id"] for c in row["claims"]]:
            out.append(f"{at}: claims differ from its view row")
        out += [f"{at} claim {c['id']}: blocker_message is not the registry wording of its blocker, filled in"
                for c, r in zip(detail["claims"], row["claims"])
                if (c["blocker_message"] is None) != (r["blocker"] is None)
                or (r["blocker"] is not None and not _worded(specs.get(r["blocker"], {"message": ""}),
                                                              c["blocker_message"], ended))]
        if [s["n"] for s in detail["steps"]] != list(range(1, len(detail["steps"]) + 1)):
            out.append(f"{at}: steps are not numbered 1..n")
        ids = {c["id"] for c in detail["claims"]}
        if stop is not None:
            out += [f"{at}: stop names claim {c!r}, which the run does not have" for c in stop["required"] + stop["deferred"] if c not in ids]
            if sorted(stop["required"] + stop["deferred"]) != sorted(ids):
                out.append(f"{at}: stop does not divide the run's claims into required and deferred")
        for name, wording in (("residuals", ended), ("now", False)):
            listed = detail[name] or []
            for residual in listed:
                if not _worded(specs.get(residual["code"], {"message": ""}), residual["message"], wording):
                    out.append(f"{at}: {name} {residual['code']!r} is not a registry reason with its wording")
                out += [f"{at}: {name} names claim {c!r}, which the run does not have" for c in residual["claims"] if c not in ids]
            pairs = [(r["code"], r["message"]) for r in listed]
            if len(set(pairs)) != len(pairs):
                out.append(f"{at}: {name} are not grouped")
        if ended and detail["outstanding"]:
            out.append(f"{at}: a run that has ended owes nothing")
        texts = [r["message"] for r in (detail["residuals"] or []) + (detail["now"] or [])] + [i["message"] for i in detail["outstanding"]]
        texts += [c["blocker_message"] for c in detail["claims"] if c["blocker_message"]]
        out += [f"{at}: message {text!r} still holds a placeholder" for text in texts if re.search(r"\{\w+\}", text)]
        for item in detail["outstanding"]:
            out += [f"{at}: outstanding names claim {c!r}, which the run does not have" for c in item["claims"] if c not in ids]
        wanted = ("ended" if ended else "needs_you" if any(
            i["who"] == "human" and i["where"] in HUMAN_REACHABLE for i in detail["outstanding"])
            else "not_ended")
        if row["attention"] != wanted:
            out.append(f"{at}: attention {row['attention']!r} is not what its outstanding items say ({wanted!r})")
        items = detail["outstanding"]
        asked = [i for i in items if i["who"] == "human" and i["where"] in HUMAN_REACHABLE]
        first = (asked or [i for i in items if i["where"] != LATER])[:1]
        if row["next"] != ({k: first[0][k] for k in ("who", "where", "message")} if first else None):
            out.append(f"{at}: next is not the human's question when one is asked, else the first obligation not held behind another")
        for c in detail["claims"]:
            d = c["decision"]
            if d is None:
                continue
            if (d["outcome"] is not None) != (d["status"] in {"approved", "rejected"}):
                out.append(f"{at} claim {c['id']}: a decision has an outcome exactly when the human answered")
            if (d["where"] is not None) != (d["status"] == "waiting"):
                out.append(f"{at} claim {c['id']}: a decision says where it is asked exactly while it waits")
            if ended and d["status"] == "waiting":
                out.append(f"{at} claim {c['id']}: a decision of a run that has ended cannot be waiting")
            if (d["status"] == "deferred") == next(r for r in row["claims"] if r["id"] == c["id"])["gating"] and d["outcome"] is None:
                out.append(f"{at} claim {c['id']}: deferred exactly when the claim is outside the run's scope")
        out += [f"{at}: step {s['n']} is the human's consent to end the run but names a claim"
                for s in detail["steps"] if s["terminal"] and s["claim"] is not None]
    return out


def check(view: dict, details: dict | None) -> list[str]:
    view_schema, run_schema = schemas()
    reg = registry()
    out = mirror_problems(view_schema, run_schema, reg, _load(V2 / "state.schema.json"))
    out += [f"registry message not plain: {p}" for code, spec in reg["reasons"].items() for p in message_problems(code, spec)]
    out += view_problems(view, view_schema, set(reg["reasons"]))
    if details is not None and not out:
        out += detail_problems(details, view, run_schema, reg)
    return out


def main(argv: list[str]) -> int:
    if argv:
        view = _load(Path(argv[0]))
        details = _load(Path(argv[1])) if len(argv) > 1 else None
    else:
        view, details = _load(FIXTURES / "view.json"), _load(FIXTURES / "runs.json")
    problems = check(view, details)
    for line in problems:
        print(line)
    if problems:
        return 1
    runs = sum(len(p["runs"]) for p in view["projects"])
    print(f"ok: empirica-ui-view/v1, {len(view['projects'])} projects, {runs} runs"
          + (f", {len(details)} details" if details is not None else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
