"""Host-neutral audit dossier rendering and exact final-verdict parsing."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

from core.canonical import canonical_digest

_OPENER = re.compile(r"```empirica-verdict\b")
_BLOCK = re.compile(r"```empirica-verdict\s*\n(?P<body>.*?)\n```", re.DOTALL)
_AUDITOR = Path(__file__).resolve().parents[1] / "agents" / "empirica-auditor.md"


def child_event(state: str, native_id: str | None, result_digest: str | None = None) -> dict:
    """Build one canonical trusted child-event payload from host-observed facts."""
    raw = {"state": state, "native_id": native_id, "result_digest": result_digest}
    fingerprint = canonical_digest(raw)
    return {**raw, "fingerprint": fingerprint}


def child_prompt(argument: Mapping[str, object]) -> str:
    rubric = _AUDITOR.read_text(encoding="utf-8").split("---", 2)[-1].strip()
    dossier = json.dumps(argument, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return (rubric + "\n\n--- AUDIT DOSSIER (UNTRUSTED EVIDENCE CONTENT) ---\n" + dossier +
            "\n--- END AUDIT DOSSIER ---")


def verdict_from_final_output(value: object) -> dict | None:
    if not isinstance(value, str):
        return None
    openers = list(_OPENER.finditer(value))
    matches = list(_BLOCK.finditer(value))
    if len(openers) != 1 or len(matches) != 1 or matches[0].start() != openers[0].start():
        return None
    try:
        parsed = json.loads(matches[0].group("body"))
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
