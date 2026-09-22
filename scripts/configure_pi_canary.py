#!/usr/bin/env python3
"""Avoid loading siffran's bundled pi-subagents beside a global install."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

BUNDLED_SUBAGENTS = "-node_modules/pi-subagents/index.ts"


def package_source(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("source"), str):
        return entry["source"]
    return None


def has_global_pi_subagents(settings: Any) -> bool:
    if not isinstance(settings, dict) or not isinstance(settings.get("packages"), list):
        return False
    for entry in settings["packages"]:
        source = package_source(entry)
        if source == "npm:pi-subagents" or (
            isinstance(source, str) and source.startswith("npm:pi-subagents@")
        ):
            return True
    return False


def exclude_bundled_subagents(settings: Any, source: str) -> bool:
    if not isinstance(settings, dict) or not isinstance(settings.get("packages"), list):
        raise ValueError("project Pi settings must contain a packages array")

    packages = settings["packages"]
    for index, entry in enumerate(packages):
        if package_source(entry) != source:
            continue
        if isinstance(entry, str):
            packages[index] = {
                "source": entry,
                "extensions": [BUNDLED_SUBAGENTS],
            }
            return True

        extensions = entry.get("extensions")
        if extensions is None:
            entry["extensions"] = [BUNDLED_SUBAGENTS]
            return True
        if not isinstance(extensions, list):
            raise ValueError("package extensions filter must be an array")
        if not extensions or BUNDLED_SUBAGENTS in extensions:
            return False
        extensions.append(BUNDLED_SUBAGENTS)
        return True

    raise ValueError(f"installed canary package is missing from project settings: {source}")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: configure_pi_canary.py <project-dir> <package-source>", file=sys.stderr)
        return 2

    project_dir = Path(argv[1]).resolve()
    source = argv[2]
    agent_dir = Path(
        os.environ.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi" / "agent"))
    )
    global_path = agent_dir / "settings.json"
    if not global_path.exists() or not has_global_pi_subagents(load_json(global_path)):
        return 0

    project_path = project_dir / ".pi" / "settings.json"
    settings = load_json(project_path)
    if exclude_bundled_subagents(settings, source):
        project_path.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print("  using the global pi-subagents package; disabled siffran's bundled duplicate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
