#!/usr/bin/env python3
"""Verify operator-captured native traces for the supported Empirica release set."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from empirica_live_receipts import EXPECTED, inspect


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    root = Path(os.environ.get("EMPIRICA_LIVE_RECEIPTS_DIR", "/tmp/empirica-v2-live-receipts"))
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    plugin_version = json.loads(
        (repo / "plugins/empirica/.claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    errors = []
    for host in EXPECTED:
        path = root / f"{host}.json"
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{host}: missing/unreadable receipt {path}: {exc}")
            continue
        errors.extend(inspect(receipt, host, commit, plugin_version))
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"ok: operator-attested candidate-bound structural receipts for {', '.join(EXPECTED)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
