#!/usr/bin/env python3
"""Advisory guard (ADR-38): warn when the checkout is behind the installed plugin.

Reads ``~/.claude/plugins/installed_plugins.json`` and compares each installed plugin's version to
the checkout's ``plugins/<name>/.claude-plugin/plugin.json``. When the checkout is *behind* what is
installed and active, it prints a one-line advisory to stderr — the exact "you are editing a version
behind what runs" condition that dogfooding hit on a stale branch.

It is advice, not a gate: it always exits 0, and it no-ops silently when the installed file is
absent or unparsable (CI, fresh clones) so it can be wired into ``make status`` without ever failing
a build. It never mutates anything.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

INSTALLED = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
CHECKOUT_ROOT = Path(__file__).resolve().parents[1]


def _semver(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def _installed_versions() -> dict[str, str]:
    """Map plugin name -> installed version, keyed on the name before ``@marketplace``.

    Any structural surprise is treated as "no data" (return empty) — this guard must never raise.
    """
    try:
        data = json.loads(INSTALLED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return {}
    out: dict[str, str] = {}
    for key, entries in plugins.items():
        entry = entries[0] if isinstance(entries, list) and entries else entries
        version = entry.get("version") if isinstance(entry, dict) else None
        name = str(key).split("@", 1)[0]
        if isinstance(version, str):
            out[name] = version
    return out


def _checkout_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for manifest in CHECKOUT_ROOT.glob("plugins/*/.claude-plugin/plugin.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name, version = data.get("name"), data.get("version")
        if isinstance(name, str) and isinstance(version, str):
            out[name] = version
    return out


def main() -> int:
    installed = _installed_versions()
    if not installed:  # no installed cache (CI, fresh clone) — nothing to compare against
        return 0
    for name, checkout_version in _checkout_versions().items():
        installed_version = installed.get(name)
        co, inst = _semver(checkout_version), _semver(installed_version)
        if co is not None and inst is not None and co < inst:
            print(
                f"  warning: {name} checkout is {checkout_version} but installed is "
                f"{installed_version} — this checkout is BEHIND the active plugin; "
                "base changes on the branch that matches the installed version.",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
