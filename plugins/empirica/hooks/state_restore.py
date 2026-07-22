#!/usr/bin/env python3
"""SessionStart:compact re-injection (ADR-8).

After context compaction, Claude loses the in-conversation view of the unknowns. This
hook re-injects the living spec's convergence state so the loop is durable-resumable
across turns and compaction (ADR-8/9). It reads state from the committable spec — never
from memory — which is the whole point: the agent forgets, the spec doesn't.

Contract (verified against code.claude.com/docs plugins-reference, SessionStart):
  stdin  : JSON with at least {"cwd": str}
  stdout : text to add to context after compaction (exit 0). Empty if no spec.

It shares the SINGLE source of truth for "what is an unknown" with the Stop gate, so the
re-injected view is exactly the enforced set — no second, drifting definition.
"""
import importlib.util
import json
import sys
from pathlib import Path

# Load the sibling gate module by path without mutating sys.path (import hygiene:
# no shadowing of stdlib modules by files in this dir).
_gate_path = Path(__file__).with_name("convergence_gate.py")
_spec = importlib.util.spec_from_file_location("convergence_gate", _gate_path)
cg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cg)


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        payload = {}
    cwd = Path(str(payload.get("cwd") or "."))
    spec_path = cg.locate_spec(cwd)

    if not spec_path.exists():
        return 0  # nothing to restore; not an empirica run

    try:
        text = spec_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0  # re-injection is best-effort; the Stop gate is the enforcer

    th = cg.theta()
    unknowns = cg.parse_unknowns(text)
    open_unknowns = cg.pending(unknowns, th)
    blocked = [u for u in unknowns if u.blocked]

    if not open_unknowns:
        status = "CONVERGED" + (f" ({len(blocked)} blocked, surfaced to human)" if blocked else "")
    else:
        status = f"{len(open_unknowns)} unknown(s) below θ={th}"

    print(
        f"[empirica] Resuming convergence loop from {spec_path}. "
        f"State: {len(unknowns)} unknown(s), {status}. "
        f"The Stop hook enforces convergence — continue resolving sub-θ unknowns.\n\n"
        f"Unknowns:\n{_render(unknowns, th)}"
    )
    return 0


def _render(unknowns: list, th: float) -> str:
    """Compact, gate-faithful view of each tracked unknown."""
    if not unknowns:
        return "(none found under the Unknowns heading)"
    lines = []
    for u in unknowns:
        tag = f" [blocked: {u.blocked}]" if u.blocked else ("" if u.confidence >= th else " [pending]")
        lines.append(f"- {u.confidence:.2f}{tag} {u.body}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
