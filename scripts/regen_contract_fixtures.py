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


def _audit_summary(result: dict, run: dict) -> dict[str, object]:
    """Derive the bounded RunView audit summary for hand-authored conformance fixtures.

    Like the projection, only a failed audit carries its findings into the RunView.
    """
    argument_audit = result.get("argument", {}).get("audit", {})
    state = argument_audit.get("state")
    if run.get("status") == "converged":
        return {"state": "passed", "independence": "distinct", "findings": []}
    if state not in {"passed", "failed"}:
        state = ("pending" if any(child.get("resource_class") == "audit"
                                  and child.get("state") in {"reserved", "launching", "pending"}
                                  for child in run.get("children", ())) else "required")
    return {"state": state,
            "independence": argument_audit.get("independence", "unverified"),
            "findings": list(argument_audit.get("findings", [])) if state == "failed" else []}


def generated(path: Path) -> str:
    document = json.loads(path.read_text(encoding="utf-8"))
    command = document.get("request", {}).get("command", {})
    if (command.get("type") == "GetContract" and "expected" in document
            and not document.get("refused")):
        document["expected"]["result"]["contract_result"] = contract_result(
            command["target"], command.get("section_id"))
    result = document.get("expected", {}).get("result", {})
    run = result.get("run", {})
    governed = run.get("governance") if isinstance(run, dict) else None
    for child in document.get("children", ()):
        dossier = child.get("audit_argument") if isinstance(child, dict) else None
        if isinstance(dossier, dict) and isinstance(dossier.get("audit"), dict):
            # Persisted audit dossiers are argument views, so they carry findings too.
            dossier["audit"].setdefault("findings", [])
    argument_audit = result.get("argument", {}).get("audit") if isinstance(result, dict) else None
    if isinstance(argument_audit, dict):
        # Hand-authored argument fixtures record no verdict findings unless they say so.
        argument_audit.setdefault("findings", [])
    if isinstance(run, dict) and run:
        run["audit"] = _audit_summary(result, run)
    if isinstance(run.get("contract"), dict):
        run["contract"]["digest"] = contract_digest()
    if isinstance(governed, dict) and "proposal_digest" in governed:
        goal = run["goal"]
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
