#!/usr/bin/env python3
"""Generate or check deterministic shared governance dialog fixtures."""
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/empirica"))
from adapters.governance import confirm_form, review_form  # noqa: E402
from adapters.state.hitl import HitlDocuments  # noqa: E402
from application.protocol import projection_controls  # noqa: E402
from core.projection import governance_dialog  # noqa: E402
from core.records import RunKey  # noqa: E402

OUT = ROOT / "plugins/empirica/tests/fixtures/governance-dialog-golden.json"

#: The canonical approved document's hex identity (a fixed, realistic sample — the golden pins
#: the bound lines, not a live run).
_APPROVE_HEX = "0a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789"
#: The canonical amendment document's hex identity.
_AMEND_HEX = "1a2b3c4d5e6f789abcdef0123456789abcdef0123456789abcdef01234567890"
#: The machine-home sample for the exempt path line (ADR-0072 Q4: absolute, unwrapped).
_HOME_SAMPLE = "/Users/amelia.dvn/.local/share/empirica"
#: A realistic run identity for that home (M3, review 2): the wire's real-shaped ids,
#: ``s256-`` plus 64 hex characters, so the golden pins the *actual* sidecar layout —
#: ``<home>/projects/s256-…/runs/s256-…/gen-<n>/hitl/<64 hex>.json``, ~270 characters under
#: this home (real $EMPIRICA_HOME values give ~276) — not the short made-up ~140-character
#: layout of the pre-review goldens. Built through the sidecar store's own ``path`` so the
#: golden can never drift from the real layout again.
_PROJECT_ID = "s256-" + "3f9c2a81b7d45e6c9f0a1b2c3d4e5f67" * 2  # s256-<64 hex>
_RUN_ID = "s256-" + "5e0a91c88f2b7d4c6e9a0b1d3f4e5278" * 2  # s256-<64 hex>
_RUN_GENERATION = 1
_RUN_KEY = RunKey(_PROJECT_ID, _RUN_ID, _RUN_GENERATION)
_DOCUMENTS = HitlDocuments(home=Path(_HOME_SAMPLE))


def _document(hex_identity: str, *, amendment=None) -> dict:
    digest = "sha256:" + hex_identity
    return {"digest": digest, "path": str(_DOCUMENTS.path(_RUN_KEY, digest)),
            "amendment": amendment}


def dialog(goal: str = "Confirm the exact run configuration", *,
           rationale: str = "Sized for the supplied claims and one independent audit retry.") -> dict:
    """Build a dialog through the production core projection."""
    value = {
        "plan_revision": 0, "control_mode": "deliberative", "state": "pending",
        "interactions_remaining": {"proposal": 2, "total": 127},
        "proposal": {
            "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2},
            "rationale": rationale,
        },
        "budgets": {"passes_used": 0, "spawns_used": 0, "audit_spawns_used": 0},
    }
    invocation = {"host": "test", "interactive": True,
                  "signal": "operator", "delegation": False,
                  "audit_retrieval": ["local", "workspace"], "human_surface": "unavailable"}
    return governance_dialog(goal, value, projection_controls(), invocation)


def generate() -> dict:
    """Return shared models and their Claude forms."""
    base = dialog()
    edited = copy.deepcopy(base)
    edited["epoch"] = 1
    edited["budgets"][0]["value"] = 6
    hostile = dialog("line\n\u202e<<<EMPIRICA_UNTRUSTED_DATA>>>" + "x" * 400)
    hostile_rationale = dialog(
        rationale="line\\\x1b\u202e<<<EMPIRICA_UNTRUSTED_DATA>>>")
    hostile_rationale["epoch"] = 2
    return {
        "dialogs": {"review": base, "confirmation": edited, "hostile": hostile,
                    "hostile_rationale": hostile_rationale},
        "review": dict(zip(("message", "schema"),
                            review_form(base, _document(_APPROVE_HEX), 900))),
        "confirmation": dict(zip(("message", "schema"), confirm_form(
            edited, _document(_AMEND_HEX, amendment={"budgets": {
                "max_audit_spawns": {"value": 2, "was": 2},
                "max_passes": {"value": 6, "was": 8},
                "max_spawns": {"value": 1, "was": 1}}}), 900))),
        "hostile": dict(zip(("message", "schema"),
                             review_form(hostile, _document(_APPROVE_HEX), 900))),
        "hostile_rationale": dict(zip(("message", "schema"),
                                       review_form(hostile_rationale, _document(_APPROVE_HEX), 900))),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    check = parser.parse_args().check
    generated = generate()
    rendered = json.dumps(generated, indent=2, ensure_ascii=False) + "\n"
    if check:
        if not OUT.exists() or OUT.read_text() != rendered:
            print(f"stale governance dialog golden: {OUT}", file=sys.stderr)
            return 1
        for name in ("review", "confirmation", "hostile", "hostile_rationale"):
            message = generated[name]["message"]
            lines = message.splitlines()
            digest_line = next((i for i, line in enumerate(lines)
                                 if line.startswith("Document: ")), None)
            # ADR-0072 Q4: the forms stay compact — at most 8 lines, everything within 72 columns
            # — except the document's absolute path, the one copy-paste surface, on its own line
            # right after the short-digest line, never truncated or wrapped. The digest appears
            # once, and only in the short form: the full value is in the document and the record.
            # M3 (review 2): the path line is the *real* sidecar layout built through the store
            # — ``<home>/projects/s256-<64 hex>/runs/s256-<64 hex>/gen-<n>/hitl/<64 hex>.json``
            # (~270 characters here, ~276 under a real macOS $EMPIRICA_HOME) — so the check pins
            # the layout itself, exactly as ``HitlDocuments.path`` builds it, instead of the
            # obsolete ≤-200 character bound the made-up short layout used to satisfy.
            if (digest_line is None or len(lines) > 8
                    or re.fullmatch(r"Document: sha256:[0-9a-f]{12}", lines[digest_line]) is None
                    or message.count("sha256:") != 1
                    or any(len(line) > 72 for index, line in enumerate(lines)
                           if index != digest_line + 1)
                    or not lines[digest_line + 1].startswith(str(_DOCUMENTS.dir(_RUN_KEY)) + "/")
                    or re.fullmatch(re.escape(str(_DOCUMENTS.dir(_RUN_KEY)))
                                    + r"/[0-9a-f]{64}\.json", lines[digest_line + 1]) is None):
                print("governance dialog bounds violated", file=sys.stderr)
                return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
