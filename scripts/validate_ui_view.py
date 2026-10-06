#!/usr/bin/env python3
"""Check the observer read model (``contracts/empirica-ui-view/v1``) and its fixtures.

    python3 scripts/validate_ui_view.py                 committed fixtures + enum mirrors
    python3 scripts/validate_ui_view.py VIEW [RUNS]      a view (and its details) from observe

Three kinds of check, each a pure function returning one line per problem:

* ``mirror_problems``: every enumeration in the two schemas equals its owner (the public-contract
  registry, the state schema, or the plugin's history kinds), so the contract cannot drift from
  the plugin it describes.
* ``view_problems``: the schema, then what a schema cannot say: unique keys, a project's runs
  newest first, ``previous`` naming the same run's prior generation, claims and edges closed over
  the run, diagnostics only on partial rows, blocker codes that are real reasons, and the token.
* ``detail_problems``: each detail against its schema and its row in the view.
"""
from __future__ import annotations

import hashlib
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
from core.observer import message_problems, ENDED, EVIDENCE_KINDS, REVIEW_STATES, SPENT_ROUTES  # noqa: E402

#: The routes a pending human-owned confirmation can read as: where it goes, or why nobody will ask.
HUMAN_ASK = {*confirmation.HUMAN_ROUTES, *SPENT_ROUTES.values(), ENDED}


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
    run = view_schema["$defs"]["run"]["properties"]
    claim = view_schema["$defs"]["claim"]["properties"]
    detail = run_schema["properties"]
    audit = detail["audit"]["oneOf"][1]["properties"]
    control = detail["control"]["oneOf"][1]["properties"]
    step = detail["steps"]["items"]["properties"]
    record = run_schema["$defs"]["record"]["properties"]
    waiting = detail["waiting"]["items"]["properties"]
    decision = detail["claims"]["items"]["properties"]["decision"]["oneOf"][1]["properties"]
    governance = state_schema["properties"]["governance"]["properties"]
    pairs = [
        ("view run.state", _enum(run, "state"), set(reg["statuses"]), "registry statuses"),
        ("view run.audit", _enum(run, "audit"), set(reg["audit_states"]), "registry audit_states"),
        ("view claim.kind", _enum(claim, "kind"), set(reg["claim_kinds"]), "registry claim_kinds"),
        ("view claim.state", _enum(claim, "state"), set(reg["claim_states"]), "registry claim_states"),
        ("run audit.independence", _enum(audit, "independence"), set(reg["independence_states"]),
         "registry independence_states"),
        ("run stale.state", _enum(detail["stale"]["items"]["properties"], "state"),
         set(reg["freshness_states"]), "registry freshness_states"),
        ("run control.mode", _enum(control, "mode"), _enum(governance, "control_mode"),
         "state.schema governance.control_mode"),
        ("run control.approval", _enum(control, "approval"), _enum(governance, "state"),
         "state.schema governance.state"),
        ("view claim.evidence", _enum(claim, "evidence"), set(EVIDENCE_KINDS),
         "core.observer.EVIDENCE_KINDS"),
        ("view claim.review", _enum(claim, "review"), set(REVIEW_STATES), "core.observer.REVIEW_STATES"),
        ("record class", _enum(record, "class"),
         {citations.VERBATIM, citations.RETRIEVABLE, citations.OBSERVED}, "core.citations classes"),
        ("record status", _enum(record, "status"),
         {confirmation.VERIFIED_BY_SERVICE, confirmation.CONFIRMED_BY_HUMAN,
          confirmation.REJECTED_BY_HUMAN, confirmation.PENDING}, "core.confirmation statuses"),
        ("record route", _enum(record, "route"),
         {confirmation.AUDIT, *HUMAN_ASK}, "core.confirmation routes + core.observer spent routes"),
        ("waiting route", _enum(waiting, "route"), set(confirmation.HUMAN_ROUTES) | set(SPENT_ROUTES.values()),
         "core.confirmation.HUMAN_ROUTES + core.observer.SPENT_ROUTES"),
        ("run prompt_error", _enum(detail, "prompt_error"),
         {"unavailable", "already_presented", "interaction_limit"}, "core.confirmation.prompt_error"),
        ("decision outcome", _enum(decision, "outcome"), {confirmation.APPROVE, confirmation.REJECT},
         "core.confirmation decisions"),
        ("decision route", _enum(decision, "route"), set(HUMAN_ASK), "core.confirmation + core.observer routes"),
        ("run steps.kind", _enum(step, "kind"), set(HISTORY_KINDS), "history_records.HISTORY_KINDS"),
        ("run steps.outcome", _enum(step, "outcome"),
         set(reg["artifact_outcomes"]) | set(reg["audit_verdicts"]),
         "registry artifact_outcomes + audit_verdicts"),
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
    text = json.dumps(pairs, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    firsts = [_newest(p["runs"][0]) for p in view["projects"]]
    if firsts != sorted(firsts):
        out.append("projects: not ordered by their newest run")
    for project in view["projects"]:
        order = [_newest(r) for r in project["runs"]]
        if order != sorted(order):
            out.append(f"project {project['id']}: runs not newest first")
        for run in project["runs"]:
            at = f"run {run['key']}"
            if run["key"] in runs:
                out.append(f"{at}: duplicate key")
            runs[run["key"]] = run
            if run["project"] != project["id"]:
                out.append(f"{at}: project {run['project']} listed under {project['id']}")
            parts = run["key"].split("/")
            if len(parts) != 3 or parts[0] != run["project"] or parts[2] != str(run["generation"]):
                out.append(f"{at}: key is not <project>/<run>/<generation>")
            partial = run["diagnostic"] is not None
            if partial and (run["claims"] or run["edges"] or run["audit"] is not None):
                out.append(f"{at}: a {run['diagnostic']} row carries derived facts")
            if not partial and (run["state"] is None or run["audit"] is None):
                out.append(f"{at}: a fully read row lacks its state or audit")
            if run["diagnostic"] == "unreadable" and run["state"] is not None:
                out.append(f"{at}: an unreadable row reports a state")
            claim_ids = [c["id"] for c in run["claims"]]
            if len(set(claim_ids)) != len(claim_ids):
                out.append(f"{at}: duplicate claim id")
            if (run["root"] is None) != (not claim_ids) or (
                    run["root"] is not None and run["root"] not in claim_ids):
                out.append(f"{at}: root must be one of its claims, and null exactly when it has none")
            out += [f"{at}: edge {e['from']}->{e['to']} leaves the run's claims"
                    for e in run["edges"] if not {e["from"], e["to"]} <= set(claim_ids)]
            for claim in run["claims"]:
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
    for run in runs.values():
        prev = run["previous"]
        if prev is None:
            continue
        before = runs.get(prev)
        if (before is None or before["project"] != run["project"]
                or prev.split("/")[1] != run["key"].split("/")[1]
                or before["generation"] != run["generation"] - 1):
            out.append(f"run {run['key']}: previous {prev} is not this run's prior generation in the view")
    if view["token"] != token_of(view):
        out.append("view: token is not the digest of the ordered run versions")
    return out


def fits(template: str, message: str) -> bool:
    """Whether ``message`` is ``template`` with every ``{name}`` placeholder filled in."""
    pattern = re.sub(r"\\\{\w+\\\}", ".+?", re.escape(template))
    return re.fullmatch(pattern, message, re.DOTALL) is not None


def detail_problems(details: dict, view: dict, run_schema: dict, reg: dict) -> list[str]:
    out: list[str] = []
    rows = {r["key"]: r for p in view["projects"] for r in p["runs"]}
    if set(details) != set(rows):
        out.append(f"details: keys differ from the view's runs ({len(details)} vs {len(rows)})")
    messages = {code: spec["message"] for code, spec in reg["reasons"].items()}
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
        if (detail["audit"] is None) != (row["audit"] is None):
            out.append(f"{at}: audit detail must be present exactly when its view row has an audit state")
        undecoded = row["diagnostic"] in {"unreadable", "older-format"}
        if undecoded != (detail["budgets"] is None) or undecoded != (detail["control"] is None):
            out.append(f"{at}: budgets and control must be null exactly when the state was not decoded")
        if [c["id"] for c in detail["claims"]] != [c["id"] for c in row["claims"]]:
            out.append(f"{at}: claims differ from its view row")
        out += [f"{at} claim {c['id']}: blocker_message is not the registry message of its blocker, filled in"
                for c, r in zip(detail["claims"], row["claims"])
                if (c["blocker_message"] is None) != (r["blocker"] is None)
                or (r["blocker"] is not None and not fits(messages.get(r["blocker"], ""), c["blocker_message"]))]
        if [s["n"] for s in detail["steps"]] != list(range(1, len(detail["steps"]) + 1)):
            out.append(f"{at}: steps are not numbered 1..n")
        for residual in detail["residuals"]:
            if not fits(messages.get(residual["code"], ""), residual["message"]):
                out.append(f"{at}: residual {residual['code']!r} is not a registry reason with its message")
        texts = [residual["message"] for residual in detail["residuals"]]
        texts += [c["blocker_message"] for c in detail["claims"] if c["blocker_message"]]
        out += [f"{at}: message {text!r} still holds a placeholder" for text in texts if re.search(r"\{\w+\}", text)]
        ids = {c["id"] for c in detail["claims"]}
        out += [f"{at}: waiting names claim {w['claim']!r}, which the run does not have"
                for w in detail["waiting"] if w["claim"] not in ids]
        out += [f"{at} claim {c['id']}: a decision has a route exactly while it waits"
                for c in detail["claims"] if c["decision"] and (c["decision"]["outcome"] is None) == (c["decision"]["route"] is None)]
        out += [f"{at} claim {c['id']}: more active passing spikes than spikes ever run"
                for c in detail["claims"] if c["spikes"] > c["spikes_ever"]]
    return out


def check(view: dict, details: dict | None) -> list[str]:
    view_schema, run_schema = schemas()
    reg = registry()
    out = mirror_problems(view_schema, run_schema, reg, _load(V2 / "state.schema.json"))
    out += [f"registry message not plain: {p}" for code, spec in reg["reasons"].items() for p in message_problems(code, spec["message"])]
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
