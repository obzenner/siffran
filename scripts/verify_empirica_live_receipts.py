#!/usr/bin/env python3
"""Require real installed-host Empirica receipts before release promotion."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

EXPECTED = {
    "claude": ("claude-code@2.1.270", "2.1.270"),
    "pi": ("pi@0.84.1+pi-subagents@0.50.0", "0.84.1"),
    "codex": ("codex-cli@0.146.0", "0.146.0"),
}


def main() -> int:
    root = Path(os.environ.get("EMPIRICA_LIVE_RECEIPTS_DIR", "/tmp/empirica-v2-live-receipts"))
    errors: list[str] = []
    for host, (profile, version) in EXPECTED.items():
        path = root / f"{host}.json"
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{host}: missing/unreadable receipt {path}: {exc}")
            continue
        result = receipt.get("result", {})
        transitions = receipt.get("audit_transitions")
        transcript = receipt.get("transcript_path")
        if receipt.get("native_host") is not True:
            errors.append(f"{host}: native_host must be true")
        if receipt.get("profile_id") != profile or receipt.get("host_version") != version:
            errors.append(f"{host}: exact profile/version mismatch")
        if not isinstance(receipt.get("command"), str) or not receipt["command"].strip():
            errors.append(f"{host}: exact invocation command is required")
        if not isinstance(transcript, str) or not Path(transcript).is_file():
            errors.append(f"{host}: transcript_path must name retained host output")
        else:
            actual = "sha256:" + hashlib.sha256(Path(transcript).read_bytes()).hexdigest()
            if receipt.get("transcript_sha256") != actual:
                errors.append(f"{host}: transcript digest mismatch")
        state_path = receipt.get("run_state_path")
        if not isinstance(state_path, str) or not Path(state_path).is_file():
            errors.append(f"{host}: run_state_path must name retained durable state")
        else:
            actual = "sha256:" + hashlib.sha256(Path(state_path).read_bytes()).hexdigest()
            if receipt.get("run_state_sha256") != actual:
                errors.append(f"{host}: run-state digest mismatch")
        if transitions != ["reserved", "launching", "pending", "completed"]:
            errors.append(f"{host}: exact bound audit transition trace is required")
        run = result.get("run", {}) if isinstance(result, dict) else {}
        if not (result.get("type") == "Allow" and result.get("converged") is True
                and isinstance(run, dict) and run.get("status") == "converged"):
            errors.append(f"{host}: receipt does not end in Allow(converged=true)")
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"ok: installed-host receipts for {', '.join(EXPECTED)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
