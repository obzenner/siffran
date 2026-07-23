#!/usr/bin/env python3
"""Stop-hook convergence gate (ADR-7, ADR-8).

Blocks completion while any unknown in the living spec is unresolved. Proven by the
spike + regression suite at .claude/spike-m3.

Contract (verified against code.claude.com/docs/en/hooks + plugins-reference,
re-verified 2026-07-22 — the canonical Stop-block mechanism is exit code 2, NOT a
stdout `decision` field, which the current Stop spec does not honor):
  stdin  : JSON, at least {"cwd": str, ...}
  block  → write the reason to STDERR and exit 2 (Claude reads stderr, keeps going).
  allow  → exit 0 (stdout {"continue": true} is informational only).

State substrate (ADR-15): the living spec is the run's internal working memory, held in the
run directory (`.claude/empirica/<run_id>/spec.md`) and located via the manifest's
`spec_path`. Unknowns are checkbox items under a `## Unknowns` heading, each carrying a
confidence in a trailing HTML comment `<!-- confidence: N -->` (N in [0,1]). An unknown the
agent genuinely cannot resolve is surfaced to the human with
`<!-- confidence: N, blocked: <tag> -->` (tags per evidence-over-recall §3, plus
`needs-budget` from ADR-17), where <tag> ∈ {needs-decision, needs-data, needs-experiment,
needs-budget}; blocked unknowns stop gating (they are a residual for the human, not a
loop to spin on).

Convergence reporting (ADR-17): when the gate allows the stop, it reports whether the run
truly CONVERGED (no unknowns blocked) or merely STOPPED with residuals. A budget-exhausted
run (`blocked: needs-budget`) allows the stop but is flagged `converged: false` — the gate
never lets budget exhaustion fabricate a green result.

Identity and fail direction (ADR-19 active-run manifest): the manifest is the sole signal
that a session is an empirica run.
  - no manifest         → not an empirica run → fail OPEN (never wedge an unrelated session)
  - manifest corrupt    → fail CLOSED (corruption of the record that proves a run is live)
  - active run, spec missing/unreadable → fail CLOSED (the spec was deleted/tampered)
  - status ≠ active (already stopped/converged) → fail OPEN (done, don't re-block)
  - unscored / malformed / out-of-range confidence → treated as 0.0 → BLOCKS
    (absence of proof is not proof of convergence)

Termination (ADR-19, refines ADR-9): on an active run the gate ticks a monotone pass
counter each time it would block. The well-founded variant `max_passes - passes` over
(ℕ, <) guarantees the loop stops in ≤ max_passes passes whether or not it converges — at
the cap the gate records `stopped_residual` and allows the stop as honestly non-converged,
rather than grinding to the platform's forced 8-block override.
"""
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_THETA = 0.8


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


manifest = _load("manifest")

# A checkbox list item (any bullet, any indent) inside the Unknowns section.
UNKNOWN_LINE_RE = re.compile(r"^\s*[-*+]\s+\[[ xX]\]\s+(?P<body>.*)$")
# Confidence and optional blocked-tag inside the trailing HTML comment.
CONFIDENCE_RE = re.compile(
    r"<!--\s*confidence:\s*(?P<value>[^,>]+?)\s*"
    r"(?:,\s*blocked:\s*(?P<blocked>[^>]+?)\s*)?-->"
)
UNKNOWNS_HEADING_RE = re.compile(r"^(#+)\s+unknowns\b", re.IGNORECASE)
HEADING_RE = re.compile(r"^(#+)\s+")

# The CLOSED set of residual tags that legitimately stop gating (ADR-9/17). A blocked
# value outside this set is NOT honored — the item stays pending and BLOCKS (fail-closed).
# This is what stops a one-token `blocked: made-up` from bypassing the gate: the
# constrained model cannot invent a tag to declare its own work done (review finding 1.1).
VALID_BLOCKED_TAGS = frozenset(
    {"needs-decision", "needs-data", "needs-experiment", "needs-budget"}
)


@dataclass(frozen=True)
class Unknown:
    """One unknown as the gate sees it. `confidence` is 0.0 when missing/malformed."""
    body: str
    confidence: float
    blocked: str | None  # a VALID residual tag, or None (unknown/invalid tags → None)


def theta() -> float:
    """θ from env, guarded: a malformed EMPIRICA_THETA falls back to the default,
    never crashes the hook at import (adversarial review — env-var crash surface)."""
    try:
        value = float(os.environ.get("EMPIRICA_THETA", str(DEFAULT_THETA)))
    except ValueError:
        return DEFAULT_THETA
    return value if 0.0 <= value <= 1.0 else DEFAULT_THETA


def spec_path_for(cwd: Path, session_id: str, run: dict | None) -> Path:
    """The living spec's path. The spec is the run's internal working memory and lives in the
    run directory (`.claude/empirica/<run_id>/spec.md`), recorded in the manifest's
    `spec_path`. An active manifest's recorded path is authoritative; otherwise the path is
    derived from the run identity. The spec is never a repository file."""
    if run and isinstance(run.get("spec_path"), str) and run["spec_path"]:
        return Path(run["spec_path"])
    return manifest.default_spec_path(cwd, session_id)


def unknowns_section(text: str) -> list[str]:
    """Lines under EVERY `## Unknowns` heading, each until the next same/higher heading.

    Scoping to the section is what lets 'missing confidence → block' be safe: only lines
    the author placed under Unknowns are gated, so unrelated checklists never false-block.
    All Unknowns sections are AGGREGATED (not just the last), so a second section with a
    pending item cannot be hidden from the gate (review finding 1.2b).
    """
    section: list[str] = []
    depth: int | None = None
    for line in text.splitlines():
        heading = UNKNOWNS_HEADING_RE.match(line)
        if heading:
            depth = len(heading.group(1))
            continue  # accumulate across sections; do NOT reset
        if depth is not None:
            other = HEADING_RE.match(line)
            if other and len(other.group(1)) <= depth:
                depth = None  # left this section; wait for the next Unknowns heading
                continue
            section.append(line)
    return section


def parse_unknowns(text: str) -> list[Unknown]:
    """Every checkbox item in the Unknowns section, scored. The one novel piece (ADR-15).

    Missing, malformed, or out-of-range confidence → 0.0, so an unproven unknown blocks
    rather than silently satisfying the gate.
    """
    unknowns: list[Unknown] = []
    for line in unknowns_section(text):
        item = UNKNOWN_LINE_RE.match(line)
        if not item:
            continue
        body = item.group("body")
        confidence = 0.0
        blocked = None
        malformed = True  # no parseable comment at all → malformed → blocks
        comment = CONFIDENCE_RE.search(body)
        if comment:
            raw_blocked = (comment.group("blocked") or "").strip()
            # Only a tag in the closed set stops gating; anything else → None (stays
            # pending, blocks). A made-up tag cannot bypass the gate (review 1.1).
            blocked = raw_blocked if raw_blocked in VALID_BLOCKED_TAGS else None
            try:
                value = float(comment.group("value"))
                if 0.0 <= value <= 1.0:
                    confidence = value
                    malformed = False
            except ValueError:
                pass
        # Malformed/out-of-range confidence fails CLOSED even with a valid blocked tag
        # (review 1.1 residual): a residual must carry a real score, so we drop the
        # blocked exemption and let the item stay pending.
        if malformed:
            blocked = None
        unknowns.append(Unknown(body=body, confidence=confidence, blocked=blocked))
    return unknowns


def pending(unknowns: list[Unknown], th: float) -> list[Unknown]:
    """Unknowns still in the loop: below θ and not surfaced-to-human."""
    return [u for u in unknowns if u.confidence < th and not u.blocked]


def converged(unknowns: list[Unknown], th: float) -> bool:
    """Fixed point reached ⇔ nothing pending (blocked residuals don't gate) — ADR-7/9."""
    return not pending(unknowns, th)


def _resolve_run(cwd: Path, session_id: object) -> tuple[Path | None, dict | None]:
    """Locate and read this session's active-run manifest (ADR-19).

    Returns (manifest_path, run). run is:
      * None            — no session id OR no manifest file → NOT an empirica run
      * {"__corrupt__"} — a manifest exists but is unparseable → caller fails CLOSED
      * a normalised dict — a well-formed manifest
    The Stop payload carries `session_id` as a common hook field; when it is absent (e.g.
    a bare unit invocation) we behave exactly as before ADR-19 — no manifest, no identity.
    """
    if not isinstance(session_id, str) or not session_id:
        return None, None
    path = manifest.locate_run(cwd, session_id)
    return path, manifest.read_run(path)


def _allow_converged(unknowns: list[Unknown]) -> tuple[int, dict]:
    """The allow-path payload: truly converged ⇔ nothing blocked; residuals ⇒ stopped."""
    blocked = [u for u in unknowns if u.blocked]
    budget_blocked = [u for u in blocked if u.blocked == "needs-budget"]
    out: dict[str, object] = {"continue": True, "converged": not blocked}
    if budget_blocked:
        out["note"] = (f"NON-CONVERGED: budget exhausted, {len(budget_blocked)} unknown(s) "
                       f"unresolved (blocked: needs-budget). Raise the budget to continue.")
    elif blocked:
        out["note"] = f"{len(blocked)} unknown(s) surfaced to human (blocked), not gated"
    return 0, out


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        payload = {}
    cwd = Path(str(payload.get("cwd") or "."))
    session_id = payload.get("session_id")
    run_path, run = _resolve_run(cwd, session_id)
    is_active = bool(run) and run.get("status") == "active"

    # --- Identity + fail direction (ADR-19) ---------------------------------
    # The active-run manifest is the sole signal that this is an empirica run. A session with
    # no manifest is not an empirica run — fail OPEN, never wedge it.
    if run is None:
        print(json.dumps({"continue": True}))
        return 0

    if run.get("status") == "__corrupt__":
        # An active run whose manifest is corrupt → fail CLOSED: corruption of the record
        # that proves a run is live is exactly when you want the gate.
        print("empirica: active-run manifest is corrupt; refusing to stop until run state "
              "can be read (fail-closed, ADR-19).", file=sys.stderr)
        return 2

    spec_path = spec_path_for(cwd, str(session_id), run)

    if is_active and not spec_path.exists():
        # The run's spec vanished (deleted/renamed to bypass convergence) → fail CLOSED.
        print(f"empirica: active run but the living spec is missing ({spec_path}); refusing "
              f"to stop — restore it in the run directory (fail-closed, ADR-19).", file=sys.stderr)
        return 2

    if not is_active:
        # Manifest says this run already stopped/converged — don't re-block a finished run.
        print(json.dumps({"continue": True, "converged": run.get("status") == "converged"}))
        return 0

    try:
        text = spec_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        # Spec exists but cannot be read — fail CLOSED, not open.
        print(f"empirica: {spec_path.name} exists but is unreadable ({exc}); "
              f"refusing to stop until convergence can be evaluated.", file=sys.stderr)
        return 2

    th = theta()
    unknowns = parse_unknowns(text)
    open_unknowns = pending(unknowns, th)

    if not open_unknowns:
        code, out = _allow_converged(unknowns)
        if is_active:  # record the terminal status so a later Stop fails open, not re-blocks
            manifest.set_status(run_path, "converged" if out["converged"] else "stopped_residual")
        print(json.dumps(out))
        return code

    # --- Still converging → BLOCK, but tick the termination variant (ADR-19) ---
    if is_active:
        run = manifest.record_pass(run_path)  # monotone +1; variant strictly decreases
        if manifest.at_cap(run):
            # Pass budget exhausted: stop HONESTLY as non-converged rather than grind to the
            # platform's forced 8-block override. The variant guarantees we reach here.
            manifest.set_status(run_path, "stopped_residual")
            print(json.dumps({
                "continue": True, "converged": False,
                "note": (f"NON-CONVERGED: reached max_passes={run['max_passes']} with "
                         f"{len(open_unknowns)} unknown(s) still below θ={th}. Loop terminated "
                         f"by the pass-count variant (ADR-19). Raise EMPIRICA_MAX_PASSES or "
                         f"resolve/blocked-tag the remaining unknowns."),
            }))
            return 0

    scores = ", ".join(f"{u.confidence:.2f}" for u in open_unknowns)
    passes_note = (f" [pass {run['passes']}/{run['max_passes']}]" if is_active else "")
    reason = (
        f"Convergence not reached: {len(open_unknowns)} unknown(s) below θ={th} "
        f"({scores}){passes_note}. Resolve them in {spec_path.name} — run one Assessor pass "
        f"(score updates + specialize-only derivation). If one is genuinely unresolvable, mark "
        f"it `<!-- confidence: N, blocked: needs-decision|needs-data|needs-experiment -->` to "
        f"surface it to the human instead of looping (ADR-9). If budget is exhausted, mark "
        f"it `blocked: needs-budget` (ADR-17)."
    )
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
