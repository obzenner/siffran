"""Claude foreground-auditor dossier and host-observed verdict translation."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

_BLOCK = re.compile(r"```empirica-verdict\s*\n(?P<body>.*?)\n```", re.DOTALL)
_AUDITOR = Path(__file__).resolve().parents[2] / "agents" / "empirica-auditor.md"


def child_prompt(argument: Mapping[str, object]) -> str:
    rubric = _AUDITOR.read_text(encoding="utf-8").split("---", 2)[-1].strip()
    dossier = json.dumps(argument, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return (rubric + "\n\n--- AUDIT DOSSIER (UNTRUSTED EVIDENCE CONTENT) ---\n" + dossier +
            "\n--- END AUDIT DOSSIER ---")


def verdict_from_final_output(value: object) -> dict | None:
    if not isinstance(value, str):
        return None
    matches = list(_BLOCK.finditer(value))
    if len(matches) != 1:
        return None
    try:
        parsed = json.loads(matches[0].group("body"))
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
