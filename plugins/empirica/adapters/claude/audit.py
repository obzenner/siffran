"""Host-observed auditor round trip helpers.

The nonce is deliberately returned only to the child-task mutation, never to an author-visible
message.  These functions are transport-neutral so the thin hook can use the same contract as tests.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

_BLOCK = re.compile(r"```empirica-verdict\s*\n(?P<body>.*?)\n```", re.DOTALL)
_AUDITOR = Path(__file__).resolve().parents[2] / "agents" / "empirica-auditor.md"


def auditor_rubric() -> str:
    """The agent body is the one rubric injected on every supporting host."""
    text = _AUDITOR.read_text(encoding="utf-8")
    return text.split("---", 2)[-1].strip()


def child_prompt(argument_text: str, nonce: str) -> str:
    return f"{auditor_rubric()}\n\n--- AUDIT DOSSIER ---\n{argument_text}\n\nYour nonce: {nonce}\n\nReturn only the fenced `empirica-verdict` output contract above."


def verdict_from_final_output(value: object) -> dict | None:
    """Extract exactly one JSON fenced verdict; malformed/missing output remains unrecorded."""
    if not isinstance(value, str):
        return None
    matches = list(_BLOCK.finditer(value))
    if len(matches) != 1:
        return None
    try:
        parsed = json.loads(matches[0].group("body"))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def build_audit_verdict_request(run_id: str, verdict: Mapping[str, object], *, request_id: str) -> dict:
    return {"protocol": "empirica/v1", "request_id": request_id,
            "command": {"type": "ObserveAction", "run_id": run_id,
                        "action": {"kind": "audit_verdict", **dict(verdict)}}}
