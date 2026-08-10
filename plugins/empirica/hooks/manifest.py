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
_stamps = _load("stamps")

DEFAULT_MAX_PASSES = 8  # aligns with the platform's 8-consecutive-block Stop cap (ADR-8)
# Real, non-corrupt lifecycle states. Anything else in a present file → __corrupt__.
# `stopped_frozen` (ADR-26) is deliberately DISTINCT from `stopped_residual`: "closed with a
# declared scope and an open-items list" and "ran out of passes" are different outcomes, and a
# status that conflated them would be the dressing-up ADR-17 forbids.
STATUSES = frozenset({"active", "converged", "stopped_residual", "stopped_budget",
                      "stopped_frozen"})
# The run's phase machine (ADR-21 M1): route → resolve → assess → audit → converged.
# `phase` is where the run says it is; the gate independently checks the evidence, so a phase
# label is a record, never a permission. Ordered, because P1's route-before-investigate check
# compares the route stamp against the first investigative tool call.
PHASES = ("route", "resolve", "assess", "audit", "converged")
DEFAULT_PHASE = "route"
_CORRUPT = {"status": "__corrupt__", "passes": 0, "max_passes": 0, "phase": DEFAULT_PHASE}
RUN_ENV = "EMPIRICA_RUN_ID"  # harness may pass a precomputed run id; else derived


# Markers that identify a PROJECT ROOT, in priority order.
#
# `.git` comes FIRST, and the order is the whole point. It was originally the other way round —
# an established run store outranked the VCS marker, reasoning that a run in progress should be
# resumed rather than forked. An independent audit falsified that: this defect's OWN leftover
# artifact is a stray `plugins/empirica/.claude/empirica/` directory, so preferring the run store
# made the litter re-split identity exactly as the bug had, and the anchor that fixes the defect
# was defeated by the defect's debris. The repository boundary is the honest definition of "this
# project" and it does not move when a run scatters state.
#
# The run-store marker is retained BELOW `.git` for the genuine case it serves: a project that is
# not a git repository but already has a run directory should keep using it rather than start a
# second identity beside it.
ANCHOR_MARKERS = (Path(".git"), Path(".claude") / "empirica")


def project_anchor(cwd: Path) -> Path:
    """The directory run identity is keyed on: the nearest ancestor (including `cwd`) holding a
    project marker, else `cwd` itself.

    WHY THIS EXISTS — a real, reproduced failure, not a hypothetical. run_id used to key on the
    raw `cwd` of whichever hook was firing. But the hooks docs define `cwd` as "Current working
    directory when the hook is invoked" (and ship a dedicated `CwdChanged` event), so it MOVES
    mid-session. Observed: `/empirica` was invoked while cwd was `<repo>/plugins/empirica`, so
    run_start.py wrote its manifest under that subdirectory; every later hook fired from `<repo>`,
    derived a different run_id, found no manifest, and the ADR-19 matrix correctly failed OPEN.
    The whole harness went inert for the rest of the run — no convergence gate, no spawn cap, no
    mandatory audit — and the symptom was easy to misread as "the run-start hook never fired".

    Anchoring keeps the property ADR-19 wanted from keying on the tree (two different repos get two
    different runs) while dropping the one it did not intend (two cwds in ONE repo getting two
    different runs). Deterministic: a pure function of the filesystem, no clock, no randomness —
    ADR-19's resumability rule holds.

    HONEST LIMIT (ADR-21): this is only as stable as the marker it finds. Two sessions whose cwds
    sit in DIFFERENT repositories under one project — a git submodule, a nested checkout, a
    worktree — still anchor differently, and correctly so; but a run that legitimately spans them
    would still split. And a project with no `.git` and no established run store anchors on cwd,
    which is the original behaviour with the original weakness. The claim is "the observed defect
    cannot recur", not "identity is stable under every layout".

    Falls back to `cwd` rather than walking to `/`: a run in an unmarked directory should be keyed
    to that directory, never to the filesystem root, which would collide across projects.

    Marker PRIORITY beats marker DISTANCE, deliberately: the nearest `.git` wins over a closer run
    store, because a stray run store is exactly what this defect leaves behind (see
    ANCHOR_MARKERS). Within one marker kind, the nearest ancestor wins.
    """
    cwd = cwd.resolve()
    for marker in ANCHOR_MARKERS:
        for candidate in (cwd, *cwd.parents):
            if (candidate / marker).exists():
                return candidate
    return cwd


def _run_id_from(session_id: str, anchor: Path) -> str:
    """The identity hash itself, over an ALREADY-ANCHORED directory.

    Split out from `run_id` so a test can reproduce the pre-fix behaviour by passing an
    unanchored path — a regression check for this defect has to be able to express the broken
    scheme, or it cannot show the fix changed anything.
    """
    return hashlib.sha256(f"{session_id}:{anchor}".encode("utf-8")).hexdigest()[:16]


def run_id(session_id: str, root: Path) -> str:
    """Stable per (session, project root); distinct across sessions. 16 hex chars.

    Keying on BOTH means two concurrent sessions in one repo get distinct runs (fixes the
    shared-`default`-ledger bug, review 2.4) and the same session resuming in the same tree
    continues its run. sha256 avoids leaking the raw session id / path into the filename.

    `root` is ANCHORED to the project root before hashing (see `project_anchor`), so callers may
    keep passing the hook payload's `cwd` verbatim and still land on one identity per session per
    project — which is what every call site already assumed.
    """
    return _run_id_from(session_id, project_anchor(root))


def locate_run_dir(cwd: Path, session_id: str) -> Path:
    """The run's private directory: `.claude/empirica/<run_id>/`. run_id is sanitised to a
    single safe segment — no traversal. This directory is the run's entire home: the
    manifest, the claim graph, the evidence store, and the audit artifacts. It is transient
    scratch (git-ignored) and the model must write the run's state here, never to the repo.

    Rooted at the PROJECT ANCHOR, not at the caller's cwd. Both halves must anchor together: a
    hook firing from a subdirectory would otherwise compute the right run_id and then look for it
    in the wrong place, scattering run artifacts into subdirectories where `make clean-runs` never
    finds them. That is precisely how the observed inert-harness run left a stray manifest under
    `plugins/empirica/.claude/` (see `project_anchor`)."""
    anchor = project_anchor(cwd)
    rid = re.sub(r"[^a-f0-9]", "", _run_id_from(session_id, anchor)) or "default"
    return anchor / ".claude" / "empirica" / rid


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


def _coerce_opt_int(value: object) -> int | None:
    """A plain non-negative int, or None. Unlike _coerce_int there is no default: absence is
    meaningful here (a manifest predating the ordering counters), so it must stay None rather
    than collapse to 0, which would read as a real position in the order."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
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
        # The ordering the HARNESS witnessed, independent of what the stamps say. Stamps arrive
        # in incomparable kinds (ISO vs `pass:<n>`) depending on what the harness supplied, so
        # comparing them alone left P1 unverifiable in the common case. These counters are
        # assigned under this module's lock at the moment each event is recorded, giving a total
        # order that is always comparable. None on manifests written before they existed.
        "route_seq": _coerce_opt_int(data.get("route_seq")),
        "first_tool_seq": _coerce_opt_int(data.get("first_tool_seq")),
        "stamp_seq": _coerce_int(data.get("stamp_seq"), 0, minimum=0),
        # ADR-26 freeze. `frozen_claims` is the set of claims that were ALREADY GATING when the
        # run froze — the scope it committed to discharge. None means "not frozen", which is the
        # baseline where every claim gates.
        #
        # A malformed value normalises to None, i.e. NOT FROZEN. This is the opposite direction
        # from `modes.json`, and deliberately so: a corrupt mode file falls back to the mode being
        # off, but a corrupt freeze record must fall back to gating MORE, never less. A freeze
        # record that freed a blocking run when unreadable would be the legacy-shape exploit
        # again.
        "frozen_claims": (sorted({c for c in data["frozen_claims"] if isinstance(c, str) and c})
                          if isinstance(data.get("frozen_claims"), list) else None),
        "freeze_ts": data.get("freeze_ts") if isinstance(data.get("freeze_ts"), str) else None,
        "freeze_seq": _coerce_opt_int(data.get("freeze_seq")),
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


def _stamp_event(path: Path, ts_field: str, seq_field: str, ts: str) -> dict | None:
    """Record an ordering event: its caller-supplied stamp AND its position in the manifest's
    own write order. FIRST WRITE WINS, so a run cannot re-stamp to make a bad ordering look good.

    The `seq` half is what makes P1 checkable at all. Stamps come in kinds that may not be
    comparable to each other (an ISO time vs. a `pass:<n>` fallback), but this counter is
    assigned here, under the lock, in the order the harness actually observed the events — a
    total order no caller can forge by choosing a stamp format. See stamps.py.
    """
    with _io.lock(path):
        run = read_run(path)
        if run and run["status"] != "__corrupt__" and not run.get(ts_field):
            run[ts_field] = ts
            run["stamp_seq"] = run.get("stamp_seq", 0) + 1
            run[seq_field] = run["stamp_seq"]
            _io.atomic_write_json(path, run)
        return run


def stamp_route(path: Path, ts: str) -> dict | None:
    """Record WHEN the run announced its route (ADR-20 P1). First write wins — a run cannot
    re-stamp a later route over an earlier investigation to make the order look right."""
    return _stamp_event(path, "route_ts", "route_seq", ts)


def stamp_first_tool(path: Path, ts: str) -> dict | None:
    """Record the FIRST investigative tool call (ADR-21 M1). First write wins, so the stamp
    marks the genuine start of evidence-gathering and cannot be pushed later."""
    return _stamp_event(path, "first_tool_ts", "first_tool_seq", ts)


def freeze(path: Path, gating_claims: list[str], ts: str) -> dict | None:
    """Commit the run's scope: the claims it will discharge (ADR-26). No-op on corrupt/absent.

    FIRST WRITE WINS, like the route and first-tool stamps. This is the anti-bypass property, not
    a convenience: `gating_claims` is the set ALREADY GATING at the moment of the freeze, so a
    claim derived afterwards can never be in it. The attack "freeze early, then add the hard
    claims" therefore does not buy a pass with less work — it buys a pass with less SCOPE,
    declared up front, with every omission printed in the run's result and handed to the auditor.
    Re-freezing to enlarge the set is refused for the same reason a run cannot re-stamp its route:
    a commitment that can be rewritten per pass is unbounded shrinking with extra steps.

    Freezing with an EMPTY gating set is allowed and yields a run that discharges nothing and
    defers everything — visibly vacuous rather than illegal, the same treatment as a spike that
    binds no files (evidence.py's `_files_intact`). Making the degenerate case loud beats making
    it an error the caller works around.

    Termination is untouched: freeze only ever lets a run reach a terminal state SOONER, so the
    ADR-19 variant `max_passes - passes` still bounds the loop and remains the only termination
    argument.
    """
    with _io.lock(path):
        run = read_run(path)
        if not run or run["status"] == "__corrupt__" or run.get("frozen_claims") is not None:
            return run
        run["frozen_claims"] = sorted({c for c in gating_claims if isinstance(c, str) and c})
        run["freeze_ts"] = ts
        run["stamp_seq"] = run.get("stamp_seq", 0) + 1
        run["freeze_seq"] = run["stamp_seq"]
        _io.atomic_write_json(path, run)
        return run


def is_frozen(run: dict) -> bool:
    """Has this run committed its scope (ADR-26)? A malformed record reads as NOT frozen, so the
    baseline where every claim gates is what an unreadable freeze falls back to."""
    return isinstance(run.get("frozen_claims"), list)


def deferred_claims(run: dict, gating: list[str]) -> list[str]:
    """Gating claims that arrived AFTER the freeze — the run's honest open-items list (ADR-26).

    Empty when the run is not frozen: without a commitment there is nothing to be outside of, and
    every claim gates.
    """
    if not is_frozen(run):
        return []
    frozen = set(run["frozen_claims"])
    return [nid for nid in gating if nid not in frozen]


def route_before_investigation(run: dict) -> tuple[bool, str]:
    """Did routing precede investigation (ADR-20 P1)? Returns (ok, reason).

    Thin wrapper over `stamps.route_verdict` — the ordering logic lives in ONE place because
    this check and audit.py's copy of it drifted into carrying the same bug twice.

    Collapses three outcomes to a bool, so INCONCLUSIVE reports as not-ok: an ordering that
    could not be verified must never read as verified. Callers needing the distinction (the
    Stop gate, which words its report differently) should call `stamps.route_verdict` directly.
    """
    verdict, reason = _stamps.route_verdict(run)
    return verdict == _stamps.OK, reason


def variant(run: dict) -> int:
    """The well-founded termination measure over (ℕ, <): max(0, max_passes - passes)."""
    return max(0, run["max_passes"] - run["passes"])


def at_cap(run: dict) -> bool:
    """True when the pass budget is exhausted — the loop must stop (stopped_residual)."""
    return run["passes"] >= run["max_passes"]
