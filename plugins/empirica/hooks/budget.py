#!/usr/bin/env python3
"""Spawn-budget ledger (ADR-17, corrected).

WHY SPAWNS, NOT TOKENS. Enforcement must gate on something the harness can both
COUNT truthfully and DENY. Verified against code.claude.com/docs (2026-07-23):
  - A PreToolUse hook (matcher "Agent") fires once per subagent spawn and can DENY
    it → spawns are denyable, and the fire-once-per-spawn contract makes the count
    ground truth.
  - Actual TOKEN spend is NOT readable mid-session by any hook: payloads exclude
    token counts, SubagentStop carries none, the transcript is undocumented/async,
    there is no /cost command, and OTEL is opt-in + external (post-hoc only).
So a token budget cannot be enforced at spawn time; a SPAWN budget can. The enforced
currency is therefore spawns. Token cost, if wanted, is a post-hoc OTEL audit that
NEVER gates (see `cost_usd`, optional, informational).

The ledger is TRANSIENT scratch (ADR-14): it lives under `.claude/`, is git-ignored,
and a resumed run recomputes it. It is the one file this workflow adds.

Concurrency: parallel `Agent` spawns fire their PreToolUse hooks concurrently — the
exact case a spawn budget matters most — so reserve_spawn() takes an OS file lock
around read-modify-write. POSIX (mac/linux) uses fcntl; where fcntl is absent
(Windows) it degrades to best-effort (a logged caveat, not a crash).

Ledger shape:
  {"max_spawns": int|null, "spawns": int, "run_id": str, "cost_usd": float|null,
   "updated_at": str|null}
  max_spawns null → unbounded (the gate allows every spawn and logs it).

Time note: hooks cannot call Date.now()/datetime.now() safely in a resumable run,
so `updated_at` is stamped by the CALLER (passed in), never by this module.
"""
import json
import math
import os
from pathlib import Path

try:
    import fcntl  # POSIX only
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    _HAVE_FCNTL = False

LEDGER_ENV = "EMPIRICA_BUDGET"  # optional path override
DEFAULT_REL = Path(".claude") / "empirica"


def locate_ledger(cwd: Path, run_id: str = "default") -> Path:
    """Ledger path under cwd's .claude scratch, or EMPIRICA_BUDGET override."""
    override = os.environ.get(LEDGER_ENV)
    if override:
        p = Path(override)
        return p if p.is_absolute() else cwd / p
    return cwd / DEFAULT_REL / run_id / "budget.json"


def _coerce(data: dict) -> dict:
    """Normalise a raw ledger dict; a malformed field must never crash a caller."""
    max_spawns = data.get("max_spawns")
    max_spawns = int(max_spawns) if isinstance(max_spawns, (int, float)) else None
    spawns = data.get("spawns", 0)
    spawns = max(0, int(spawns)) if isinstance(spawns, (int, float)) else 0
    cost = data.get("cost_usd")
    cost = float(cost) if isinstance(cost, (int, float)) else None
    return {
        "max_spawns": max_spawns,
        "spawns": spawns,
        "run_id": data.get("run_id"),
        "cost_usd": cost,
        "updated_at": data.get("updated_at"),
    }


def read_ledger(path: Path) -> dict:
    """Read the ledger; a missing/unreadable ledger reads as unbounded + zero spawns.

    Fail-open on read: absence of a ledger means 'no ceiling set', not 'deny all'.
    """
    try:
        return _coerce(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        return _coerce({})


def remaining_spawns(ledger: dict) -> float:
    """Spawns left before the ceiling. Unbounded (max_spawns null) → math.inf."""
    cap = ledger.get("max_spawns")
    if cap is None:
        return math.inf
    return max(0, cap - ledger.get("spawns", 0))


def _write(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic on POSIX


def write_ledger(path: Path, ledger: dict) -> None:
    """Persist a full ledger dict atomically (caller supplies any timestamp)."""
    _write(path, _coerce(ledger))


def reserve_spawn(path: Path, updated_at: str | None = None) -> tuple[bool, dict]:
    """Atomically attempt to reserve ONE spawn against the cap.

    Returns (allowed, ledger). allowed is False iff a finite cap is already reached;
    in that case the count is NOT incremented (a denied spawn didn't happen). When
    allowed, `spawns` is incremented and persisted before returning — this is the
    ground-truth counter the PreToolUse gate enforces on.

    The read-modify-write is guarded by an exclusive OS lock so concurrent parallel
    spawns cannot race past the cap. Without fcntl (Windows) it is best-effort.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if _HAVE_FCNTL:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        ledger = read_ledger(path)
        cap = ledger.get("max_spawns")
        if cap is not None and ledger.get("spawns", 0) >= cap:
            return False, ledger  # cap reached — do not increment; the spawn is denied
        ledger["spawns"] = ledger.get("spawns", 0) + 1
        if updated_at is not None:
            ledger["updated_at"] = updated_at
        _write(path, ledger)
        return True, ledger
    finally:
        if _HAVE_FCNTL:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
