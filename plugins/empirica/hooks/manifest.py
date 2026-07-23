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
_CORRUPT = {"status": "__corrupt__", "passes": 0, "max_passes": 0}
RUN_ENV = "EMPIRICA_RUN_ID"  # harness may pass a precomputed run id; else derived


def run_id(session_id: str, root: Path) -> str:
    """Stable per (session, canonical root); distinct across sessions. 16 hex chars.

    Keying on BOTH means two concurrent sessions in one repo get distinct runs (fixes the
    shared-`default`-ledger bug, review 2.4) and the same session resuming in the same tree
    continues its run. sha256 avoids leaking the raw session id / path into the filename.
    """
    raw = f"{session_id}:{root.resolve()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def locate_run(cwd: Path, session_id: str) -> Path:
    """Path to this run's manifest. run_id sanitised to a single safe segment — no traversal."""
    rid = re.sub(r"[^a-f0-9]", "", run_id(session_id, cwd)) or "default"
    return cwd / ".claude" / "empirica" / rid / "run.json"


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
        "spec_path": data.get("spec_path") if isinstance(data.get("spec_path"), str) else "spec.md",
        "evidence": evidence if isinstance(evidence, dict) else {},
    }


def start_run(path: Path, session_id: str, root: Path,
              max_passes: int = DEFAULT_MAX_PASSES, spec_path: str = "spec.md") -> dict:
    """Create the manifest for a run. IDEMPOTENT: an already-ACTIVE run keeps its pass
    count, so re-invoking `/empirica` mid-run continues rather than resetting the counter
    (which would let the model escape the cap). A corrupt/stopped file is replaced with a
    fresh active run — starting is an explicit new-run intent.
    """
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
            "spec_path": spec_path,
            "evidence": {},
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


def variant(run: dict) -> int:
    """The well-founded termination measure over (ℕ, <): max(0, max_passes - passes)."""
    return max(0, run["max_passes"] - run["passes"])


def at_cap(run: dict) -> bool:
    """True when the pass budget is exhausted — the loop must stop (stopped_residual)."""
    return run["passes"] >= run["max_passes"]
