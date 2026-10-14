#!/usr/bin/env python3
"""Check the observer read model (``contracts/empirica-ui-view/v1``) and its fixtures.

    python3 scripts/validate_ui_view.py                 committed fixtures + enum mirrors
    python3 scripts/validate_ui_view.py SUMMARY ROWS [RUNS]   a summary, rows and details (as ``observe`` gives them)

Three kinds of check, each a pure function returning one line per problem:

* ``mirror_problems`` and ``owner_mirror_problems``: every enumeration in the schemas equals its owner (the public-contract
  registry, the state schema, the plugin's history kinds, or the core's own vocabularies), so the contract cannot drift
  from the plugin it describes.
* ``row_problems``: each row against what a schema cannot say: a key that is its parts, diagnostics only on partial rows
  (whose derived facts are null, never zero), claims and edges closed over the run, an attention that is ended exactly
  when the run is, blocker codes that are real reasons, assurance that agrees with the claim, a ``previous`` that names
  the same run's prior generation.
* ``detail_problems``: each detail against its schema and its row (the row it carries is the index's row).
* ``view_problems``: the summary, rows, a page and each detail against their schemas; coverage that sums, marks that are the
  rows, ids that are functions of the key, places in each project's start order (the untimed in the outer band), the
  projects in island order, the calendar each project's runs by civil day in its zone, window flags within the flags, a standing that agrees with the read and the attention, asks only
  on active runs, the latest audit as the row's verdict, flags that are the details' facts, and the needs list.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "contracts/empirica/v2"
UI_VIEW = ROOT / "contracts/empirica-ui-view/v1"
FIXTURES = UI_VIEW / "fixtures"
sys.path.insert(0, str(ROOT / "plugins/empirica"))

from application.history_records import HISTORY_KINDS  # noqa: E402
from core import citations, confirmation  # noqa: E402
from core.evaluation import INDEPENDENCE_REASONS  # noqa: E402
from core.observer import (ACTORS, ASSURANCE_STATES, ATTENTION_STATES, CLAIM_STANDING, DECISION_STATUSES,  # noqa: E402
                           EVIDENCE_KINDS, HUMAN_REACHABLE, LATER, PLACES, REVIEW_STATES, SPIKE_STATES, STEP_SUBJECTS, message_problems)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def schemas() -> tuple[dict, dict]:
    """The row schema and the run (detail) schema of the contract."""
    return _load(UI_VIEW / "row.schema.json"), _load(UI_VIEW / "run.schema.json")


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
    """Every enumeration of the row and run schemas against its owner (``view_schema`` is the row schema)."""
    defs = view_schema["$defs"]
    run = defs["row"]["properties"]
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


def _structure(instance: Any, schema: dict, where: str, ui: dict[str, dict] | None = None) -> list[str]:
    return [f"{where}{''.join(f'[{p!r}]' for p in e.absolute_path)}: {e.message}"
            for e in Draft202012Validator(schema, registry=_registry(ui or view_schemas())).iter_errors(instance)]


def row_problems(rows: list[dict], reasons: set[str]) -> list[str]:
    """What the row schema cannot say about the rows of a store: each row's key, its derived facts and diagnostic, its
    claims, edges and blockers, and ``previous`` naming the prior generation of the same run."""
    out: list[str] = []
    by_key: dict[str, dict] = {}
    for run in rows:
        at = f"run {run['key']}"
        if run["key"] in by_key:
            out.append(f"{at}: duplicate key")
        by_key[run["key"]] = run
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
        # Times are labels (ADR-0070): nothing orders by them, but an undecoded state has none to give and only a run
        # that ended has an end.
        if run["state"] is None and any(run[f] is not None for f in ("started", "updated", "ended")):
            out.append(f"{at}: a state that was not decoded has a time")
        if run["ended"] is not None and run["state"] in (None, "active"):
            out.append(f"{at}: an end time on a run that has not ended")
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
            # Only what the row itself contradicts is checked here: whether the gate accepts the audit is
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
    for run in rows:
        prev = run["previous"]
        if prev is None:
            continue
        before = by_key.get(prev)
        if (before is None or before["project"] != run["project"]
                or prev.split("/")[1] != run["key"].split("/")[1]
                or before["generation"] != run["generation"] - 1):
            out.append(f"run {run['key']}: previous {prev} is not this run's prior generation in the index")
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


def detail_problems(details: dict, rows: list[dict], run_schema: dict, reg: dict) -> list[str]:
    out: list[str] = []
    attempts = {}
    for r in rows:
        attempts.setdefault((r["project"], r["run"]), []).append(r)
    rows = {r["key"]: r for r in rows}
    if set(details) != set(rows):
        out.append(f"details: keys differ from the index's runs ({len(details)} vs {len(rows)})")
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
            out.append(f"{at}: key or version differs from its row")
        if detail["row"] != row:
            out.append(f"{at}: the row it carries is not the index's row")
        if detail["attempts"] != sorted(attempts[(row["project"], row["run"])], key=lambda r: r["generation"]):
            out.append(f"{at}: attempts are not every attempt at the run, by generation")
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
                out.append(f"{at}: goal differs from its row")
            continue
        ended = row["state"] != "active"
        if stop is not None and stop["status"] != row["state"]:
            out.append(f"{at}: stop status differs from its row")
        if detail["goal"] != row["goal"]:
            out.append(f"{at}: goal differs from its row")
        if [c["id"] for c in detail["claims"]] != [c["id"] for c in row["claims"]]:
            out.append(f"{at}: claims differ from its row")
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


def view_schemas() -> dict[str, dict]:
    return {name: _load(UI_VIEW / f"{name}.schema.json") for name in ("summary", "row", "page", "run")}


def _registry(ui: dict[str, dict]) -> Registry:
    """The four contract schemas by their ``$id``, so the detail's reference to a row resolves."""
    return Registry().with_resources([(schema["$id"], Resource.from_contents(schema)) for schema in ui.values()])


def _schema_errors(schema: dict, value: Any, at: str, ui: dict[str, dict] | None = None) -> list[str]:
    return [f"{at}: {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in Draft202012Validator(schema, registry=_registry(ui or view_schemas())).iter_errors(value)]


def owner_mirror_problems(ui: dict[str, dict]) -> list[str]:
    """Every contract vocabulary equals its owner in the core (``core.observer``) or the index (``application.observer_index``)."""
    from application import observer_index as index
    from core import observer as owner
    row = ui["row"]["$defs"]["row"]["properties"]
    run = ui["run"]["properties"]
    item = lambda name: run[name]["oneOf"][1]["items"]["properties"]  # noqa: E731
    pairs = [("row standing", row["standing"]["enum"], owner.STANDINGS), ("row ask", row["ask"]["enum"], [*owner.ASKS, None]),
             ("marks standings", ui["summary"]["properties"]["marks"]["properties"]["standings"]["const"], owner.STANDINGS),
             ("marks flag bits", ui["summary"]["properties"]["marks"]["properties"]["flag_bits"]["const"], index.FLAGS),
             ("dropped why", item("dropped")["why"]["enum"], owner.DROP_REASONS),
             ("decision asked", item("decisions")["asked"]["enum"], owner.ASKS),
             ("decision answer", item("decisions")["answer"]["enum"], owner.ANSWERS),
             ("decision effect", item("decisions")["effect"]["enum"], owner.EFFECTS),
             ("audit verdict", item("audits")["verdict"]["enum"], owner.AUDIT_OUTCOMES),
             ("facets", ui["summary"]["properties"]["facets"]["required"], list(index.FACETS))]
    return [f"vocabulary {name}: schema {list(got)} != owner {list(want)}" for name, got, want in pairs if list(got) != list(want)]


def view_problems(summary: dict, rows: list, details: dict, ui: dict[str, dict] | None = None) -> list[str]:
    """The summary, rows and details: their schemas, then what a schema cannot say."""
    from application import observer_index as index
    from adapters.observer import civil
    from core.observer import STANDINGS
    ui = ui or view_schemas()
    out = owner_mirror_problems(ui)
    out += _schema_errors(ui["summary"], summary, "summary", ui)
    out += _schema_errors(ui["row"], rows, "rows", ui)
    out += [p for key, d in details.items() for p in _schema_errors(ui["run"], d, f"run {key}", ui)]
    labels = {p["id"]: p["label"] for p in summary["projects"]}
    out += _schema_errors(ui["page"], {"contract": "empirica-ui-view/v1", "pending": 0,
                                       **index.page(rows, labels, lambda r: True, None, index.PAGE_LIMIT,
                                                   summary["token"])}, "page", ui)
    if out:
        return out
    reg = registry()
    out += [f"registry message not plain: {p}" for code, spec in reg["reasons"].items() for p in message_problems(code, spec)]
    out += mirror_problems(ui["row"], ui["run"], reg, _load(V2 / "state.schema.json"))
    out += row_problems(rows, set(reg["reasons"]))
    if out:
        return out
    cov = summary["coverage"]
    if cov["read"] + cov["unreadable"] + cov["partial"] + cov["pending"] != cov["runs"]:
        out.append("summary: coverage does not sum to runs")
    if len(rows) != cov["runs"] - cov["pending"]:
        out.append("summary: a row for every run read, and none for a pending one")
    if cov["reindexing"] > cov["read"] + cov["unreadable"] + cov["partial"]:
        out.append("summary: only a run read before can be reindexing")
    if sorted(rows, key=index.order) != rows:
        out.append("rows: not in page order (standing precedence, then each bucket's declared order: the needs bucket oldest question first, every other the most recent activity first; then key)")
    marks = summary["marks"]
    columns = {k: v for k, v in marks.items() if k not in {"standings", "flag_bits"}}
    if any(w & ~f for w, f in zip(marks["window_flags"], marks["flags"])):
        out.append("marks: a run carries a flag within the window only when it carries it")
    if summary["aggregates"]["since"] is None and marks["window_flags"] != marks["flags"]:
        out.append("marks: without a window the window flags are the flags")
    if index.island_order(summary["projects"]) != summary["projects"]:
        out.append("summary: the projects are in island order (the first run's start, then id)")
    cal, ids = summary["calendar"], [p["id"] for p in summary["projects"]]
    if index.calendar(rows, ids, cal["tz"], civil.zone(cal["tz"])) != cal:
        out.append("summary: the calendar is each project's runs by their civil day of updated, in its tz")
    if {len(v) for v in columns.values()} != {len(rows)}:
        out.append("marks: every column has one entry per row")
    if marks["id"] != [r["id"] for r in rows] or len(set(marks["id"])) != len(rows):
        out.append("marks: ids are the rows' ids, each once")
    for n, r in enumerate(rows):
        at = f"row {r['key']}"
        if r["id"] != index.short_id(r["key"]):
            out.append(f"{at}: id is not its key's short id")
        if (r["standing"] == "unreadable") != (r["diagnostic"] is not None):
            out.append(f"{at}: unreadable exactly when the run was not read in full")
        if r["standing"] == "needs_human" and r["attention"] != "needs_you":
            out.append(f"{at}: needs_human exactly when a human is owed a question a host shows")
        if r["ask"] is not None and r["state"] != "active":
            out.append(f"{at}: only an active run asks a human")
        if r["asked_at"] is not None and r["ask"] is None:
            out.append(f"{at}: asked_at without an ask")
        if marks["standing"][n] != STANDINGS.index(r["standing"]) or marks["updated"][n] != r["updated"]:
            out.append(f"{at}: its mark is not its row")
        d = details.get(r["key"])
        if d is None:
            out.append(f"{at}: no detail")
            continue
        if r["diagnostic"] is not None:
            if any(d[k] is not None for k in ("audits", "dropped", "decisions")) or r["flags"] is not None:
                out.append(f"{at}: a run not read in full has no audits, dropped claims, decisions or flags (null)")
            continue
        audits = d["audits"]
        if (audits[0]["verdict"] if audits else "none") != r["audit"]["verdict"]:
            out.append(f"{at}: the first of audits (newest first) is the row's audit verdict")
        if any(x["at"] is not None and y["at"] is not None and x["at"] < y["at"] for x, y in zip(audits, audits[1:])):
            out.append(f"{at}: audits are newest first")
        known = {c["id"] for c in r["claims"] or ()} | {x["claim"] for x in d["dropped"]}
        if any(x["claim"] is not None and x["claim"] not in known for x in d["decisions"]):
            out.append(f"{at}: a decision is attached to a claim of its run")
        if r["flags"]["failed_audit"] != any(a["verdict"] == "failed" for a in audits):
            out.append(f"{at}: failed_audit exactly when an audit failed")
        if r["flags"]["refuted"] != any(x["why"] in {"refuted", "spike_failed"} for x in d["dropped"]):
            out.append(f"{at}: refuted exactly when evidence dropped a claim")
        if r["standing"] == "changed_since_audit" and not r["flags"]["changed_since_audit"]:
            out.append(f"{at}: changed_since_audit standing without its flag")
    # every project is read in full once nothing is pending: each place is then given; before, a given place is final
    places = index.places(rows, [p["id"] for p in summary["projects"]])
    for n, r in enumerate(rows):
        given = marks["place"][n]
        if (given is None and cov["pending"] == 0) or (given is not None and given != places[r["key"]]):
            out.append(f"row {r['key']}: its place is not its ordinal in its project's start order")
    needs = sorted((n for n in summary["needs"]), key=index.asked_order)
    if {n["id"] for n in summary["needs"]} != {r["id"] for r in rows if r["standing"] == "needs_human"}:
        out.append("summary: needs lists exactly the runs that need a human")
    if summary["needs"] != needs:
        out.append("summary: needs is in asked order (the oldest question first), never grouped by project")
    for p in summary["projects"]:
        if p["needs"] != sum(r["project"] == p["id"] for r in rows if r["standing"] == "needs_human"):
            out.append(f"summary: project {p['id']} needs counts exactly its runs that need a human")
        held = [r["standing"] for r in rows if r["project"] == p["id"]]
        if p["standings"] != {k: held.count(k) for k in STANDINGS if k in held}:
            out.append(f"summary: project {p['id']} standings count exactly its runs by standing")
    read = [r for r in rows if r["diagnostic"] is None]
    if summary["aggregates"]["not_read"] != len(rows) - len(read):
        out.append("summary: aggregates count the runs not read")
    if summary["aggregates"]["since"] is None and sum(summary["facets"]["standing"].values()) != len(read):
        out.append("summary: the standing facet counts every read run once")
    if summary["aggregates"]["total"] != sum(summary["aggregates"]["by"]["standing"].values()):
        out.append("summary: the aggregates' total is the runs they count")
    if summary["aggregates"]["since"] is None and summary["aggregates"]["by"] != summary["facets"]:
        out.append("summary: without a window the aggregates are the facets")
    if not out:
        out += detail_problems(details, rows, ui["run"], reg)
    return out


class _Empty:
    """A store with no runs: what the observer serves for it is the contract id it serves for any store."""

    def keys(self):
        return []

    def refresh(self):
        pass


def contract_id_problems(schemas: dict[str, dict] | None = None, folder: Path = UI_VIEW) -> list[str]:
    """The contract's folder owns its id (``empirica-ui-view/<folder>``): every schema's ``$id`` and ``$ref`` and its
    declared ``contract``, what the observer serves, what the server's demo serves, both copies of the fixtures, and what
    the UI's client expects all say it."""
    from adapters.observer.server import Demo
    from application.observer import Observer
    want = f"empirica-ui-view/{folder.name}"
    base = f"https://siffran.dev/contracts/{want}/"
    out = []
    for name, schema in (schemas or view_schemas()).items():
        if schema["$id"] != f"{base}{name}.schema.json":
            out.append(f"{name}.schema.json: $id {schema['$id']} is not under {base}")
        declared = schema.get("properties", {}).get("contract", {}).get("const")
        if declared is not None and declared != want:
            out.append(f"{name}.schema.json: declares contract {declared}, not {want}")
        refs = re.findall(r'"\$ref": "(https://siffran\.dev/contracts/empirica-ui-view/[^"#]*)', json.dumps(schema, indent=0))
        out += [f"{name}.schema.json: refers to {ref}, not under {base}" for ref in refs if not ref.startswith(base)]
    observer = Observer(_Empty())
    demo = Demo()
    any_key = next(iter(_load(FIXTURES / "runs.json")))
    served = {"the observer's summary": observer.summary()["contract"], "the observer's runs page": observer.runs({})["contract"],
              "the demo's summary": demo.summary()["contract"], "the demo's runs page": demo.runs({})["contract"],
              "the demo's search": demo.search("")["contract"], "the demo's sheet": demo.sheet(any_key)["contract"]}
    out += [f"served by {where}: {got}, not {want}" for where, got in served.items() if got != want]
    vendored = ROOT / "plugins/empirica/vendor/contracts/empirica-ui-view" / folder.name / "fixtures"
    for copy in (FIXTURES, vendored):
        held = {_load(copy / "summary.json")["contract"], *(d["contract"] for d in _load(copy / "runs.json").values())}
        out += [f"{copy.relative_to(ROOT)}: holds contract {got}, not {want}" for got in sorted(held - {want})]
    client = (ROOT / "plugins/empirica/ui/app/client.js").read_text(encoding="utf-8")
    expected = re.search(r'const CONTRACT = "([^"]+)"', client)
    if expected is None or expected.group(1) != want:
        out.append(f"expected by the UI's client (ui/app/client.js): {expected and expected.group(1)}, not {want}")
    return out


_BRANCHES: dict[tuple[int, int], tuple[dict, Draft202012Validator]] = {}


def _branch(branch: dict, doc: dict, ui: dict[str, dict]) -> Draft202012Validator:
    """One validator per alternative of a loaded schema (kept with it, so its identity is not reused)."""
    key = (id(branch), id(ui))
    if key not in _BRANCHES or _BRANCHES[key][0] is not branch:
        _BRANCHES[key] = (branch, Draft202012Validator({**branch, "$id": doc["$id"], "$defs": doc.get("$defs", {})},
                                                       registry=_registry(ui)))
    return _BRANCHES[key][1]


def _walk(node: dict, doc: dict, value: Any, pointer: str, ui: dict[str, dict], seen: dict[str, set]) -> None:
    """What a document holds of each enumeration (the schema-derived generators' reachability oracle, T1 B): record under each enumeration's ``schema#pointer`` (references resolved, so a definition used in several places
    is one enumeration) the values ``value`` holds there; a ``oneOf`` or ``anyOf`` is followed into the branches it fits."""
    by_id = {s["$id"]: s for s in ui.values()}
    while "$ref" in node:
        url, _, fragment = node["$ref"].partition("#")
        doc = by_id[url] if url else doc
        node, pointer = doc, fragment
        for part in (p for p in fragment.split("/") if p):
            node = node[part]
    if "enum" in node:
        seen.setdefault(f"{doc['$id'].rsplit('/', 1)[1]}#{pointer}", set()).add(json.dumps(value))
    for word in ("oneOf", "anyOf"):
        for n, branch in enumerate(node.get(word, ())):
            if _branch(branch, doc, ui).is_valid(value):
                _walk(branch, doc, value, f"{pointer}/{word}/{n}", ui, seen)
    if isinstance(value, dict):
        for name, sub in node.get("properties", {}).items():
            if name in value:
                _walk(sub, doc, value[name], f"{pointer}/properties/{name}", ui, seen)
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            for name, sub in value.items():
                if name not in node.get("properties", {}):
                    _walk(extra, doc, sub, f"{pointer}/additionalProperties", ui, seen)
    if isinstance(value, list) and isinstance(node.get("items"), dict):
        for item in value:
            _walk(node["items"], doc, item, f"{pointer}/items", ui, seen)


def declared_values(ui: dict[str, dict] | None = None) -> dict[str, list]:
    """Every enumeration the contract declares, by ``schema#pointer``, with its values."""
    out: dict[str, list] = {}

    def collect(node: Any, name: str, pointer: str) -> None:
        if isinstance(node, dict):
            if "enum" in node:
                out[f"{name}.schema.json#{pointer}"] = node["enum"]
            for key, sub in node.items():
                collect(sub, name, f"{pointer}/{key}")
        elif isinstance(node, list):
            for n, sub in enumerate(node):
                collect(sub, name, f"{pointer}/{n}")
    for name, schema in (ui or view_schemas()).items():
        collect(schema, name, "")
    return out


def check(summary: dict, rows: list, details: dict) -> list[str]:
    """Every problem of a summary, its rows and their details."""
    return view_problems(summary, rows, details)


def main(argv: list[str]) -> int:
    if argv:
        summary, rows = _load(Path(argv[0])), _load(Path(argv[1]))
        details = _load(Path(argv[2])) if len(argv) > 2 else None
        if details is None:
            print("a summary and rows are checked with their details: give the third file")
            return 2
    else:
        summary, rows, details = (_load(FIXTURES / f) for f in ("summary.json", "rows.json", "runs.json"))
    problems = check(summary, rows, details)
    if not argv:
        problems += contract_id_problems()
    for line in problems:
        print(line)
    if problems:
        return 1
    print(f"ok: empirica-ui-view/v1, {len(rows)} runs, {len(details)} details")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
