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
doctor = _load("doctor")


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
    # The manifest records the claim graph's home as the run directory's claims.json (its
    # default, ADR-22). The model writes the graph there during the run; the gate reads it from
    # the manifest. The graph is never a repository file.
    try:
        run_path = manifest.locate_run(cwd, session_id)
        manifest.start_run(run_path, session_id, cwd, max_passes=_max_passes())
    except OSError:
        return 0  # best-effort: a write failure degrades to fail-open, never a wedge

    # ADR-24 §4: the preflight doctor runs at run-start and records what this machine can reach.
    # Wrapped in a blanket guard on purpose. This hook's contract is "always exit 0 — a run-start
    # failure must never wedge the user's prompt", and the doctor shells out to third-party CLIs
    # whose failure modes are not ours to enumerate. A doctor that could take down a prompt would
    # be a worse defect than any it diagnoses, so ANY exception degrades to "no preflight
    # recorded" and the run proceeds on baseline behaviour. With multi-provider mode off (the
    # default) the doctor runs no subprocesses at all, so on a bare install this is a few file
    # reads.
    try:
        doctor.write_report(run_path.parent, doctor.diagnose(run_path.parent))
    except Exception:  # noqa: BLE001 — see above: never wedge the prompt over a preflight
        pass
    # NOTE: this hook deliberately does NOT publish the run id into the environment. An earlier
    # version set os.environ[RUN_ENV] here, claiming to "unify identity" with budget.py — that
    # never worked. Verified by live experiment (2026-07-24): every hook fires in a FRESH
    # subprocess, so a var set here dies with this process. Two independent captures confirmed
    # a later Stop hook sees None. No mechanism is needed: run identity is DERIVED
    # deterministically from (session_id, cwd) by manifest.run_id, and `session_id` was present
    # on every captured Stop payload — so each hook recomputes the same run id independently.
    return 0


if __name__ == "__main__":
    sys.exit(main())
