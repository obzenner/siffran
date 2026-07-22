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

State substrate (ADR-15): unknowns are checkbox items under a `## Unknowns` heading in
spec.md, each carrying a confidence in a trailing HTML comment `<!-- confidence: N -->`
(N in [0,1]). An unknown the agent genuinely cannot resolve is surfaced to the human with
`<!-- confidence: N, blocked: needs-decision -->` (tags per evidence-over-recall §3);
blocked unknowns stop gating (they are a residual for the human, not a loop to spin on).

Fail direction (deliberate, per adversarial review):
  - no spec.md            → fail OPEN  (not an empirica run; never wedge an unrelated session)
  - spec.md unreadable    → fail CLOSED (the moment you most want a gate)
  - unscored / malformed / out-of-range confidence → treated as 0.0 → BLOCKS
    (absence of proof is not proof of convergence)
"""
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_THETA = 0.8

# A checkbox list item (any bullet, any indent) inside the Unknowns section.
UNKNOWN_LINE_RE = re.compile(r"^\s*[-*+]\s+\[[ xX]\]\s+(?P<body>.*)$")
# Confidence and optional blocked-tag inside the trailing HTML comment.
CONFIDENCE_RE = re.compile(
    r"<!--\s*confidence:\s*(?P<value>[^,>]+?)\s*"
    r"(?:,\s*blocked:\s*(?P<blocked>[^>]+?)\s*)?-->"
)
UNKNOWNS_HEADING_RE = re.compile(r"^(#+)\s+unknowns\b", re.IGNORECASE)
HEADING_RE = re.compile(r"^(#+)\s+")


@dataclass(frozen=True)
class Unknown:
    """One unknown as the gate sees it. `confidence` is 0.0 when missing/malformed."""
    body: str
    confidence: float
    blocked: str | None  # human-surfaced residual tag, or None if still in the loop


def theta() -> float:
    """θ from env, guarded: a malformed EMPIRICA_THETA falls back to the default,
    never crashes the hook at import (adversarial review — env-var crash surface)."""
    try:
        value = float(os.environ.get("EMPIRICA_THETA", str(DEFAULT_THETA)))
    except ValueError:
        return DEFAULT_THETA
    return value if 0.0 <= value <= 1.0 else DEFAULT_THETA


def locate_spec(cwd: Path) -> Path:
    """spec.md under cwd, or EMPIRICA_SPEC — relative overrides resolve against cwd."""
    override = os.environ.get("EMPIRICA_SPEC")
    if override:
        p = Path(override)
        return p if p.is_absolute() else cwd / p
    return cwd / "spec.md"


def unknowns_section(text: str) -> list[str]:
    """Lines under the `## Unknowns` heading, until the next same-or-higher heading.

    Scoping to the section is what lets 'missing confidence → block' be safe: only lines
    the author placed under Unknowns are gated, so unrelated checklists never false-block.
    """
    section: list[str] = []
    depth: int | None = None
    for line in text.splitlines():
        heading = UNKNOWNS_HEADING_RE.match(line)
        if heading:
            depth = len(heading.group(1))
            section = []
            continue
        if depth is not None:
            other = HEADING_RE.match(line)
            if other and len(other.group(1)) <= depth:
                break
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
        comment = CONFIDENCE_RE.search(body)
        if comment:
            blocked = (comment.group("blocked") or "").strip() or None
            try:
                value = float(comment.group("value"))
                confidence = value if 0.0 <= value <= 1.0 else 0.0
            except ValueError:
                confidence = 0.0
        unknowns.append(Unknown(body=body, confidence=confidence, blocked=blocked))
    return unknowns


def pending(unknowns: list[Unknown], th: float) -> list[Unknown]:
    """Unknowns still in the loop: below θ and not surfaced-to-human."""
    return [u for u in unknowns if u.confidence < th and not u.blocked]


def converged(unknowns: list[Unknown], th: float) -> bool:
    """Fixed point reached ⇔ nothing pending (blocked residuals don't gate) — ADR-7/9."""
    return not pending(unknowns, th)


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        payload = {}
    cwd = Path(str(payload.get("cwd") or "."))
    spec_path = locate_spec(cwd)

    if not spec_path.exists():
        print(json.dumps({"continue": True}))  # not an empirica run — fail open
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
        blocked = [u for u in unknowns if u.blocked]
        out: dict[str, object] = {"continue": True}
        if blocked:
            out["note"] = f"{len(blocked)} unknown(s) surfaced to human (blocked), not gated"
        print(json.dumps(out))
        return 0

    scores = ", ".join(f"{u.confidence:.2f}" for u in open_unknowns)
    reason = (
        f"Convergence not reached: {len(open_unknowns)} unknown(s) below θ={th} "
        f"({scores}). Resolve them in {spec_path.name} — run one Assessor pass (score "
        f"updates + specialize-only derivation). If one is genuinely unresolvable, mark it "
        f"`<!-- confidence: N, blocked: needs-decision|needs-data|needs-experiment -->` to "
        f"surface it to the human instead of looping (ADR-9)."
    )
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
