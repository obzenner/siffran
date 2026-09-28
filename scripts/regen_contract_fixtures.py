#!/usr/bin/env python3
"""Regenerate runtime-derived fields in Empirica contract fixtures."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "empirica"
sys.path.insert(0, str(PLUGIN))

from application.protocol import (  # noqa: E402
    contract_digest, contract_result)
from core.governance import proposal_digest  # noqa: E402

FIXTURES = ROOT / "contracts" / "empirica" / "v2" / "fixtures"
STATE_FIXTURES = ROOT / "contracts" / "empirica" / "v2" / "state-fixtures"


def generated(path: Path) -> str:
    document = json.loads(path.read_text(encoding="utf-8"))
    command = document.get("request", {}).get("command", {})
    if (command.get("type") == "GetContract" and "expected" in document
            and not document.get("refused")):
        document["expected"]["result"]["contract_result"] = contract_result(
            command["target"], command.get("section_id"))
    run = document.get("expected", {}).get("result", {}).get("run", {})
    governed = run.get("governance") if isinstance(run, dict) else None
    if isinstance(run.get("contract"), dict):
        run["contract"]["digest"] = contract_digest()
    if isinstance(governed, dict) and "proposal_digest" in governed:
        goal = run["goal"]
        # QUAL-1: review_text and scope moved off the author RunView into the private
        # presentation object; the RunView governance never carries them.
        governed.pop("review_text", None)
        governed.pop("scope", None)
        governed["proposal_digest"] = proposal_digest(goal, governed)
    return json.dumps(document, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    paths = sorted(FIXTURES.glob("*.json")) + sorted(STATE_FIXTURES.glob("*.json"))
    for path in paths:
        rendered = generated(path)
        if args.check:
            if path.read_text(encoding="utf-8") != rendered:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_text(rendered, encoding="utf-8")
    if stale:
        print("stale generated contract fixtures: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
