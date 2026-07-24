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

File-io note: the lock + atomic-write discipline is shared with manifest.py via atomicio.py
(ADR-19) — one hardened implementation, not two copies of the same knowledge.
"""
import importlib.util
import json
import math
import os
import re
from pathlib import Path


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_io = _load("atomicio")

LEDGER_ENV = "EMPIRICA_BUDGET"  # optional path override
DEFAULT_REL = Path(".claude") / "empirica"


def locate_ledger(cwd: Path, run_id: str | None = None) -> Path:
    """Ledger path under cwd's .claude scratch, or EMPIRICA_BUDGET override.

    run_id keys the ledger per run so concurrent sessions in one repo do not share a
    counter (review finding 2.4). Callers should PASS run_id explicitly — derived from
    (session_id, cwd) via manifest.run_id, which is how every hook independently arrives at the
    same identity.

    The $EMPIRICA_RUN_ID fallback is retained only for an operator setting it manually (e.g. in
    settings.json `env`, or when driving these modules from a script). It is NOT populated by
    another hook: verified by live experiment (2026-07-24) that each hook runs in a fresh
    subprocess, so no hook can publish an env var to a later one. Do not rely on it.
    run_id is sanitised to a single safe path segment — no traversal.
    """
    override = os.environ.get(LEDGER_ENV)
    if override:
        p = Path(override)
        return p if p.is_absolute() else cwd / p
    rid = run_id or os.environ.get("EMPIRICA_RUN_ID") or "default"
    rid = re.sub(r"[^A-Za-z0-9._-]", "_", rid) or "default"
    return cwd / DEFAULT_REL / rid / "budget.json"


def _int_or_none(value: object) -> int | None:
    """A finite, non-negative integer, or None. Booleans and non-finite reject.

    Strict on purpose (review 2.5): a string cap "5", a bool, a negative, a float, or
    JSON Infinity must NOT silently become an unbounded or bogus cap.
    """
    if isinstance(value, bool):  # bool is a subclass of int — exclude explicitly
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    return None


def _coerce(data: dict) -> dict:
    """Normalise a raw ledger dict; a malformed field must never crash a caller."""
    if not isinstance(data, dict):
        data = {}
    spawns = data.get("spawns", 0)
    spawns = spawns if (isinstance(spawns, int) and not isinstance(spawns, bool)
                        and spawns >= 0) else 0
    cost = data.get("cost_usd")
    cost = float(cost) if (isinstance(cost, (int, float)) and not isinstance(cost, bool)
                           and math.isfinite(cost)) else None
    return {
        "max_spawns": _int_or_none(data.get("max_spawns")),
        "spawns": spawns,
        "run_id": data.get("run_id") if isinstance(data.get("run_id"), str) else None,
        "cost_usd": cost,
        "updated_at": data.get("updated_at") if isinstance(data.get("updated_at"), str) else None,
    }


def read_ledger(path: Path) -> dict:
    """Read the ledger; a missing/unreadable ledger reads as unbounded + zero spawns.

    Fail-open on read: absence of a ledger means 'no ceiling set', not 'deny all'.
    """
    try:
        # parse_constant rejects Infinity/-Infinity/NaN (Python's json accepts them by
        # default), so a crafted ledger cannot inject a non-finite value (review 2.5).
        data = json.loads(path.read_text(encoding="utf-8"),
                          parse_constant=lambda _c: (_ for _ in ()).throw(ValueError))
        return _coerce(data)
    except (OSError, json.JSONDecodeError, ValueError):
        return _coerce({})


def remaining_spawns(ledger: dict) -> float:
    """Spawns left before the ceiling. Unbounded (max_spawns null) → math.inf."""
    cap = ledger.get("max_spawns")
    if cap is None:
        return math.inf
    return max(0, cap - ledger.get("spawns", 0))


def write_ledger(path: Path, ledger: dict) -> None:
    """Persist a full ledger dict atomically, UNDER THE LOCK (review 2.6).

    write_ledger previously wrote without the lock, so an operator cap change could
    clobber a concurrent reservation. It now shares reserve_spawn's lock discipline —
    both via the atomicio helpers manifest.py also uses (ADR-19).
    """
    with _io.lock(path):
        _io.atomic_write_json(path, _coerce(ledger))


def reserve_spawn(path: Path, updated_at: str | None = None) -> tuple[bool, dict]:
    """Atomically attempt to reserve ONE spawn against the cap.

    Returns (allowed, ledger). allowed is False iff a finite cap is already reached;
    in that case the count is NOT incremented (a denied spawn didn't happen). When
    allowed, `spawns` is incremented and persisted before returning — this is the
    ground-truth counter the PreToolUse gate enforces on.

    The read-modify-write is guarded by an exclusive OS lock so concurrent parallel
    spawns cannot race past the cap. Configuration-detection AND reservation happen under
    the SAME lock (review 2.5 TOCTOU): the caller must not separately pre-check "is it
    unbounded?" outside the lock. Without fcntl (Windows) it is best-effort.
    """
    with _io.lock(path):
        ledger = read_ledger(path)
        cap = ledger.get("max_spawns")
        if cap is None:
            # No finite budget for this run → allow, and do NOT create/grow a ledger
            # (keeps "no budget set → no file", the fail-open contract). Nothing to count.
            return True, ledger
        if ledger.get("spawns", 0) >= cap:
            return False, ledger  # cap reached — do not increment; the spawn is denied
        ledger["spawns"] = ledger.get("spawns", 0) + 1
        if updated_at is not None:
            ledger["updated_at"] = updated_at
        _io.atomic_write_json(path, ledger)
        return True, ledger
