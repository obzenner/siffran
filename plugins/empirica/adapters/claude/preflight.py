"""Read-only Claude doctor/preflight over operational snapshots, never side files.

The doctor consumes ``RestoreRun`` output plus invocation metadata and may run only bounded,
non-inferential version probes.  It returns recommendations; it never mutates or gates a run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from .invocation import Invocation, MODES, parse_invocation

_OPTIONAL = {
    "codex": ("codex", "--version"),
    "pi": ("pi", "--version"),
}
Probe = Callable[[str, tuple[str, ...]], dict[str, Any]]


def _probe(tool: str, argv: tuple[str, ...]) -> dict[str, Any]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=3, check=False)
        text = (proc.stdout or proc.stderr).strip().splitlines()
        return {
            "status": "detected" if proc.returncode == 0 else "unavailable",
            "version": text[0] if text else None,
            "exit_status": proc.returncode,
        }
    except (OSError, subprocess.SubprocessError):
        return {"status": "unavailable", "version": None, "exit_status": None}


def _snapshot_modes(response: object) -> dict[str, bool]:
    if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
        return {}
    result = response["result"]
    run = result.get("run")
    snapshot = run.get("snapshot") if isinstance(run, dict) else None
    raw = snapshot.get("modes") if isinstance(snapshot, dict) else None
    return {key: value for key, value in raw.items()
            if key in MODES and isinstance(value, bool)} if isinstance(raw, dict) else {}


def diagnose(
    restore_response: object, *, invocation: Invocation | None = None,
    probe: Probe | None = None,
) -> dict[str, Any]:
    """Return a total preflight report.  No probe failure may escape and wedge SessionStart."""
    try:
        persisted = _snapshot_modes(restore_response)
        modes: dict[str, dict[str, Any]] = {}
        for mode in MODES:
            if invocation is not None and mode in invocation.modes:
                enabled = invocation.modes[mode]
                source = invocation.sources.get(mode, "invocation")
            elif mode in persisted:
                enabled = persisted[mode]
                source = "operational-state"
            else:
                enabled = False
                source = "default"
            modes[mode] = {"enabled": enabled, "source": source}

        probe_optional = modes["multi_provider"]["enabled"]
        tools: dict[str, Any] = {}
        if probe_optional:
            runner = probe or _probe
            for tool, argv in _OPTIONAL.items():
                try:
                    tools[tool] = runner(tool, argv)
                except Exception as exc:  # noqa: BLE001 - a doctor must never wedge a prompt
                    tools[tool] = {
                        "status": "unavailable", "version": None, "exit_status": None,
                        "diagnostic": type(exc).__name__,
                    }
        unknown = list(invocation.unknown_flags) if invocation is not None else []
        recommendations = []
        if unknown:
            recommendations.append(
                "Unknown invocation flags were ignored: " + ", ".join(unknown))
        if not probe_optional:
            recommendations.append(
                "multi-provider mode is OFF; optional actor CLIs were not probed. Enable it with "
                "`make doctor ARGS=\"--multi-provider\"` or EMPIRICA_MODE_MULTI_PROVIDER=1 to probe.")
        else:
            recommendations.append(
                "multi_provider ALLOWS and probes external actors (codex/pi); it does NOT by itself "
                "route the audit to them — the audit still runs on the Claude auditor unless an "
                "actor is dispatched via cli_exec.")
        return {
            "baseline": {"harness": "claude-code", "status": "permitted"},
            "departs_from_baseline": any(item["enabled"] for item in modes.values()),
            "modes": modes,
            "unknown_flags": unknown,
            "tools": tools,
            "probed_optional": probe_optional,
            "spends_inference": False,
            "recommendations": recommendations,
        }
    except Exception as exc:  # noqa: BLE001 - total last-resort degradation
        return {
            "baseline": {"harness": "claude-code", "status": "permitted"},
            "departs_from_baseline": False,
            "modes": {},
            "unknown_flags": [],
            "tools": {},
            "probed_optional": False,
            "spends_inference": False,
            "recommendations": [f"preflight degraded safely ({type(exc).__name__})"],
        }


def main(argv: list[str] | None = None) -> int:
    """Print the preflight report. Mode-aware (ADR-36): ``--multi-provider``/``--cli-exec`` (and the
    ``EMPIRICA_MODE_*`` env vars) are parsed into an invocation so the report reflects the modes a
    run would actually use — bare ``make doctor`` is unchanged (all modes default off)."""
    args = list(sys.argv[1:] if argv is None else argv)
    invocation = parse_invocation(
        {"command_args": " ".join(args)}, environ=os.environ, fallback_goal="")
    json.dump(diagnose({}, invocation=invocation), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
