#!/usr/bin/env python3
"""Capture one trusted-operator receipt from retained native host artifacts."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from empirica_live_receipts import (EXPECTED, FORMAT, digest, inspect_claude, inspect_pi, jsonl,
                                    state_facts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", choices=EXPECTED)
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--child-session", required=True, type=Path)
    parser.add_argument("--version-output", required=True, type=Path)
    parser.add_argument("--command", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--operator-attested", action="store_true", required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    plugin_version = json.loads(
        (repo / "plugins/empirica/.claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    profile, host_version, role = EXPECTED[args.host]
    state, child = state_facts(args.state, role)
    parent = jsonl(args.transcript)
    child_rows = jsonl(args.child_session)
    facts = (inspect_claude(parent, child_rows, child) if args.host == "claude"
             else inspect_pi(parent, child_rows, child, args.child_session))
    receipt = {
        "format": FORMAT,
        "operator_attested": True,
        "host": args.host,
        "profile_id": profile,
        "host_version": host_version,
        "plugin_version": plugin_version,
        "release_commit": commit,
        "command": args.command,
        "audit_child_id": child["child_id"],
        "audit_native_id": child["native_id"],
        "audit_operation_id": child["audit_operation_id"],
        "observed_author": facts["author"],
        "observed_auditor": facts["auditor"],
        "result": facts["result"],
    }
    for name, path in (("transcript", args.transcript), ("run_state", args.state),
                       ("child_session", args.child_session),
                       ("version_output", args.version_output)):
        receipt[f"{name}_path"] = str(path.resolve())
        receipt[f"{name}_sha256"] = digest(path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"captured {args.host} receipt for {commit} at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
