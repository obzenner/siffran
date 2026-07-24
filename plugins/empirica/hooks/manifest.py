#!/usr/bin/env python3
"""Active-run manifest — run identity, fail-closed gating, bounded termination (ADR-19).

ONE record the harness owns answers three findings the external review left open, which a
design pass showed to be one missing thing, not three:

  * identity (1.2a): distinguish "our empirica run, spec tampered" (block) from "unrelated
    repo that happens to have a spec.md" (allow). Absence of a manifest is the "not our run
    → fail open" signal; an ACTIVE manifest turns a missing/corrupt spec into fail-closed.
  * termination (2.3): a monotone `passes` counter with cap `max_passes`. The well-founded
    variant is `max_passes - passes` over (ℕ, <): strictly decreasing, bounded below by 0,
    so the loop ends in ≤ max_passes passes whether or not it converges. This is the real
    proof that replaces ADR-9's "specialize-only" prose.
  * evidence-ready (ADR-18): a dormant `evidence` map, empty until the future run mode binds
    unknown → evidence into it, so that feature needs no new substrate.

Trust level (G3, honest): manifest.py is the sole writer, called only from hooks (run-start
+ Stop gate), never the model. But it is a file on disk and the model has Bash/Write — so
this is "the model has no instruction to touch it and tampering is visible," NOT kernel
isolation. Same trust level as the spec itself; documented, not overclaimed (ADR-19 G3).

Transient scratch (ADR-14): lives under `.claude/`, git-ignored, recomputed on a resumed
run. File-io (lock + atomic write) is reused from atomicio.py — one hardened implementation.

read_run returns:
  * None                        — NO manifest exists (the fail-OPEN signal)
  * {"status": "__corrupt__"}   — a manifest EXISTS but is unparseable/invalid (fail CLOSED;
                                  corruption of an active run is not the same as no run)
  * a normalised dict           — a well-formed manifest
"""
import hashlib
import importlib.util
import json
import re
from pathlib import Path


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_io = _load("atomicio")

DEFAULT_MAX_PASSES = 8  # aligns with the platform's 8-consecutive-block Stop cap (ADR-8)
# Real, non-corrupt lifecycle states. Anything else in a present file → __corrupt__.
STATUSES = frozenset({"active", "converged", "stopped_residual", "stopped_budget"})
# The run's phase machine (ADR-21 M1): route → resolve → assess → audit → converged.
# `phase` is where the run says it is; the gate independently checks the evidence, so a phase
# label is a record, never a permission. Ordered, because P1's route-before-investigate check
# compares the route stamp against the first investigative tool call.
PHASES = ("route", "resolve", "assess", "audit", "converged")
DEFAULT_PHASE = "route"
_CORRUPT = {"status": "__corrupt__", "passes": 0, "max_passes": 0, "phase": DEFAULT_PHASE}
RUN_ENV = "EMPIRICA_RUN_ID"  # harness may pass a precomputed run id; else derived


def run_id(session_id: str, root: Path) -> str:
    """Stable per (session, canonical root); distinct across sessions. 16 hex chars.

    Keying on BOTH means two concurrent sessions in one repo get distinct runs (fixes the
    shared-`default`-ledger bug, review 2.4) and the same session resuming in the same tree
    continues its run. sha256 avoids leaking the raw session id / path into the filename.
    """
    raw = f"{session_id}:{root.resolve()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def locate_run_dir(cwd: Path, session_id: str) -> Path:
    """The run's private directory: `.claude/empirica/<run_id>/`. run_id is sanitised to a
    single safe segment — no traversal. This directory is the run's entire home: the
    manifest, the claim graph, the evidence store, and the audit artifacts. It is transient
    scratch (git-ignored) and the model must write the run's state here, never to the repo."""
    rid = re.sub(r"[^a-f0-9]", "", run_id(session_id, cwd)) or "default"
    return cwd / ".claude" / "empirica" / rid


def locate_run(cwd: Path, session_id: str) -> Path:
    """Path to this run's manifest, inside the run directory."""
    return locate_run_dir(cwd, session_id) / "run.json"


def default_graph_path(cwd: Path, session_id: str) -> Path:
    """The run's claim graph: `claims.json` inside the run directory (ADR-22).

    This replaced `spec.md` when the substrate moved from a markdown document to a typed
    claim graph. The graph is the run's internal working memory — never a repository
    deliverable, never at the repo root: mistaking the working memory for output is the exact
    failure ADR-22 exists to prevent. It lives beside the manifest and dies with the run.
    """
    return locate_run_dir(cwd, session_id) / "claims.json"


def _raise_non_finite(_c):
    raise ValueError("non-finite JSON constant")


def _coerce_int(value: object, default: int, *, minimum: int) -> int:
    """A plain int ≥ minimum, else default. Bools reject (bool ⊂ int)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return default
    return value


def read_run(path: Path) -> dict | None:
    """The manifest, or None if there is NO manifest (fail-open signal), or a __corrupt__
    sentinel if a manifest EXISTS but is unparseable/invalid (fail-closed signal).

    parse_constant rejects JSON Infinity/NaN so a crafted manifest cannot inject a
    non-finite pass count / cap. Any structural problem collapses to __corrupt__ rather
    than raising — corruption of an active run must fail the gate CLOSED, never crash it.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_constant=_raise_non_finite)
    except (OSError, json.JSONDecodeError, ValueError):
        return dict(_CORRUPT)
    if not isinstance(data, dict) or data.get("status") not in STATUSES:
        return dict(_CORRUPT)
    evidence = data.get("evidence")
    return {
        "run_id": data.get("run_id") if isinstance(data.get("run_id"), str) else None,
        "project_root": data.get("project_root") if isinstance(data.get("project_root"), str) else None,
        "status": data["status"],
        "passes": _coerce_int(data.get("passes"), 0, minimum=0),
        "max_passes": _coerce_int(data.get("max_passes"), DEFAULT_MAX_PASSES, minimum=1),
        "graph_path": (data.get("graph_path")
                       if isinstance(data.get("graph_path"), str) else None),
        # A manifest written before the ADR-22 substrate change records `spec_path` and no
        # `graph_path`. Surfaced so the gate can recognise a LEGACY run and fail OPEN on it
        # rather than wedging a session that started under the old rules (ADR-19's "never
        # wedge" direction outranks gating a run the new code cannot evaluate).
        "spec_path": data.get("spec_path") if isinstance(data.get("spec_path"), str) else None,
        "evidence": evidence if isinstance(evidence, dict) else {},
        "audit": data.get("audit") if isinstance(data.get("audit"), dict) else {},
        "phase": data["phase"] if data.get("phase") in PHASES else DEFAULT_PHASE,
        # P1 evidence: when the run announced its route, and when it first touched evidence.
        # Both caller-stamped strings (hooks never generate time — ADR-19). Comparing them is
        # how the auditor detects a route applied retroactively to justify a shortcut.
        "route_ts": data.get("route_ts") if isinstance(data.get("route_ts"), str) else None,
        "first_tool_ts": (data.get("first_tool_ts")
                          if isinstance(data.get("first_tool_ts"), str) else None),
    }


def is_legacy(run: dict) -> bool:
    """True for a pre-ADR-22 manifest: a spec_path, no graph_path. Such a run's state is a
    markdown spec this code no longer parses, so the gate must not treat its missing claim
    graph as tampering."""
    return not run.get("graph_path") and bool(run.get("spec_path"))


def start_run(path: Path, session_id: str, root: Path,
              max_passes: int = DEFAULT_MAX_PASSES, graph_path: str | None = None) -> dict:
    """Create the manifest for a run. IDEMPOTENT: an already-ACTIVE run keeps its pass
    count, so re-invoking `/empirica` mid-run continues rather than resetting the counter
    (which would let the model escape the cap). A corrupt/stopped file is replaced with a
    fresh active run — starting is an explicit new-run intent.

    graph_path defaults to the run directory's `claims.json` (ADR-22). It is stored absolute
    so the Stop gate reads exactly the file the run tracks, independent of the process's cwd.
    """
    if graph_path is None:
        graph_path = str((path.parent / "claims.json").resolve())
    with _io.lock(path):
        existing = read_run(path)
        if existing and existing.get("status") == "active":
            return existing
        run = {
            "run_id": run_id(session_id, root),
            "project_root": str(root.resolve()),
            "status": "active",
            "passes": 0,
            "max_passes": _coerce_int(max_passes, DEFAULT_MAX_PASSES, minimum=1),
            "graph_path": graph_path,
            "evidence": {},
            # The independent auditor's verdict lands here (ADR-20 P6). Empty until a
            # DISTINCT principal writes one; the Stop gate refuses `converged` without it.
            "audit": {},
            # A run starts at `route`: it must classify its dependencies BEFORE investigating
            # (ADR-20 P1). The stamps below record whether it actually did.
            "phase": DEFAULT_PHASE,
            "route_ts": None,
            "first_tool_ts": None,
        }
        _io.atomic_write_json(path, run)
        return run


def record_pass(path: Path) -> dict:
    """Monotone +1 on the pass counter under the lock. No-op on a corrupt/absent manifest
    (nothing safe to increment). The variant `max_passes - passes` therefore strictly
    decreases by exactly 1 per real pass — the termination measure."""
    with _io.lock(path):
        run = read_run(path)
        if not run or run["status"] == "__corrupt__":
            return run or dict(_CORRUPT)
        run["passes"] += 1
        _io.atomic_write_json(path, run)
        return run


def set_status(path: Path, status: str) -> dict | None:
    """Move the run to a terminal (or other valid) status. No-op on corrupt/absent."""
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    with _io.lock(path):
        run = read_run(path)
        if run and run["status"] != "__corrupt__":
            run["status"] = status
            _io.atomic_write_json(path, run)
        return run


def set_phase(path: Path, phase: str) -> dict | None:
    """Record the run's phase (ADR-21 M1). No-op on a corrupt/absent manifest.

    This is a RECORD, not a permission: the gate never allows something because the phase says
    so, it allows it because the evidence says so. Recording the phase is what makes the
    ordering auditable.
    """
    if phase not in PHASES:
        raise ValueError(f"invalid phase: {phase!r}")
    with _io.lock(path):
        run = read_run(path)
        if run and run["status"] != "__corrupt__":
            run["phase"] = phase
            _io.atomic_write_json(path, run)
        return run


def stamp_route(path: Path, ts: str) -> dict | None:
    """Record WHEN the run announced its route (ADR-20 P1). First write wins — a run cannot
    re-stamp a later route over an earlier investigation to make the order look right."""
    with _io.lock(path):
        run = read_run(path)
        if run and run["status"] != "__corrupt__" and not run.get("route_ts"):
            run["route_ts"] = ts
            _io.atomic_write_json(path, run)
        return run


def stamp_first_tool(path: Path, ts: str) -> dict | None:
    """Record the FIRST investigative tool call (ADR-21 M1). First write wins, so the stamp
    marks the genuine start of evidence-gathering and cannot be pushed later."""
    with _io.lock(path):
        run = read_run(path)
        if run and run["status"] != "__corrupt__" and not run.get("first_tool_ts"):
            run["first_tool_ts"] = ts
            _io.atomic_write_json(path, run)
        return run


def route_before_investigation(run: dict) -> tuple[bool, str]:
    """Did routing precede investigation (ADR-20 P1)? Returns (ok, reason).

    Honest about what it can prove:
      * both stamps present  → a real comparison, and the verdict is meaningful
      * no tool stamp        → nothing was investigated yet; nothing to violate
      * no route stamp but a tool stamp → the run investigated without ever announcing a
        route. Reported as a violation, because P1 requires the announcement up front.
    String comparison is valid for ISO-8601 UTC timestamps, which is what callers stamp.
    """
    route_ts, tool_ts = run.get("route_ts"), run.get("first_tool_ts")
    if tool_ts is None:
        return True, "no investigative tool call recorded yet"
    if route_ts is None:
        return False, ("investigation began before any route was announced (ADR-20 P1: "
                       "routing is a commitment made up front, not a label applied "
                       "retroactively)")
    if route_ts <= tool_ts:
        return True, "route was announced before investigation began"
    return False, (f"the route was announced ({route_ts}) AFTER investigation began "
                   f"({tool_ts}) — the routing decision was applied retroactively (ADR-20 P1)")


def variant(run: dict) -> int:
    """The well-founded termination measure over (ℕ, <): max(0, max_passes - passes)."""
    return max(0, run["max_passes"] - run["passes"])


def at_cap(run: dict) -> bool:
    """True when the pass budget is exhausted — the loop must stop (stopped_residual)."""
    return run["passes"] >= run["max_passes"]
