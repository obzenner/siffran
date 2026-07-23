#!/usr/bin/env python3
"""Run-start hook — creates the active-run manifest when /empirica is invoked (ADR-19).

This is the run-activation signal the whole fail-closed story rests on. A
UserPromptExpansion hook with matcher "empirica" fires once when the skill expands,
carrying session_id + cwd — which both starts the run and PROVES the empirica skill (not
an unrelated session) started it. Verified against code.claude.com/docs/en/hooks
(2026-07-24): UserPromptExpansion carries `command_name`; `session_id`/`cwd` are common
fields across all hook types.

Contract:
  stdin  : JSON with at least {"session_id": str, "cwd": str}
  effect : idempotently create/continue `.claude/empirica/<run_id>/run.json`
  stdout : empty (this hook only establishes state; it injects nothing)
  exit   : always 0 — a run-start failure must never wedge the user's prompt. If we cannot
           write the manifest, the Stop gate simply sees "no manifest → fail open," i.e. the
           pre-ADR-19 behaviour, never a hard block.

max_passes: from EMPIRICA_MAX_PASSES if a valid positive int, else the module default.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


manifest = _load("manifest")
cg = _load("convergence_gate")


def _max_passes() -> int:
    raw = os.environ.get("EMPIRICA_MAX_PASSES")
    if raw is None:
        return manifest.DEFAULT_MAX_PASSES
    try:
        value = int(raw)
    except ValueError:
        return manifest.DEFAULT_MAX_PASSES
    return value if value >= 1 else manifest.DEFAULT_MAX_PASSES


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return 0  # can't parse the start signal → no run activated → gate fails open

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return 0  # no run identity available → cannot key a manifest → fail open

    cwd = Path(str(payload.get("cwd") or "."))
    # Record the spec path relative to cwd so the gate's fail-closed check and the manifest
    # agree on WHICH file must exist for an active run.
    spec_path = cg.locate_spec(cwd)
    spec_rel = os.environ.get("EMPIRICA_SPEC", "spec.md")

    try:
        manifest.start_run(
            manifest.locate_run(cwd, session_id),
            session_id, cwd,
            max_passes=_max_passes(),
            spec_path=spec_rel,
        )
    except OSError:
        return 0  # best-effort: a write failure degrades to fail-open, never a wedge
    # Publish the run id so budget.py keys its ledger to the SAME run (unifies identity,
    # review 2.4). Only affects this hook's own subprocess env; harmless if unused.
    os.environ[manifest.RUN_ENV] = manifest.run_id(session_id, cwd)
    _ = spec_path  # located for symmetry with the gate; not needed beyond spec_rel here
    return 0


if __name__ == "__main__":
    sys.exit(main())
