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
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl  # POSIX only
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    _HAVE_FCNTL = False

LEDGER_ENV = "EMPIRICA_BUDGET"  # optional path override
DEFAULT_REL = Path(".claude") / "empirica"

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


@contextmanager
def _ledger_lock(path: Path):
    """Exclusive OS lock on a per-ledger `.lock` file, opened O_NOFOLLOW (no symlink),
    mode 0600. Best-effort where fcntl is absent (Windows). Shared by every writer so
    reservation and full-ledger writes serialise against each other (review 2.6)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | _O_NOFOLLOW | _O_CLOEXEC, 0o600)
    try:
        if _HAVE_FCNTL:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if _HAVE_FCNTL:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def locate_ledger(cwd: Path, run_id: str | None = None) -> Path:
    """Ledger path under cwd's .claude scratch, or EMPIRICA_BUDGET override.

    run_id keys the ledger per run so concurrent sessions in one repo do not share a
    counter (review finding 2.4). It defaults to $EMPIRICA_RUN_ID (set by the harness
    from the Claude session id) and only falls back to "default" when nothing else is
    available. run_id is sanitised to a single safe path segment — no traversal.
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


def _write(path: Path, ledger: dict) -> None:
    """Atomically write via a fresh unique temp file in the target dir, then rename.

    Uses tempfile.mkstemp (O_CREAT|O_EXCL|O_RDWR, mode 0600) so we never follow a
    pre-planted symlink at a predictable `.tmp` path (review finding 1.3), and never
    clobber an attacker-chosen target. The unique name also means uncoordinated writers
    don't share one temp path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".budget.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(path)  # atomic on POSIX
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_ledger(path: Path, ledger: dict) -> None:
    """Persist a full ledger dict atomically, UNDER THE LOCK (review 2.6).

    write_ledger previously wrote without the lock, so an operator cap change could
    clobber a concurrent reservation. It now shares reserve_spawn's lock discipline.
    """
    with _ledger_lock(path):
        _write(path, _coerce(ledger))


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
    with _ledger_lock(path):
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
        _write(path, ledger)
        return True, ledger
