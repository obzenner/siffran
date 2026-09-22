#!/usr/bin/env python3
"""Avoid loading siffran's bundled pi-subagents beside a global install."""

from __future__ import annotations

import fnmatch
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


def _is_pinned_subagents(source: str | None) -> bool:
    return source == "npm:pi-subagents@0.50.0"


def _normalize_filter_pattern(pattern: str) -> str:
    return pattern[2:] if pattern.startswith("./") else pattern


def _extension_enabled(entry: Any) -> bool:
    if isinstance(entry, str):
        return True
    if not isinstance(entry, dict) or entry.get("autoload") is False:
        return False
    extensions = entry.get("extensions")
    if extensions is None:
        return True
    if not isinstance(extensions, list) or not extensions:
        return False
    patterns = [item for item in extensions if isinstance(item, str)]
    negatives = [_normalize_filter_pattern(item[1:]) for item in patterns
                 if item.startswith(("-", "!"))]
    if any(fnmatch.fnmatch("index.ts", pattern) for pattern in negatives):
        return False
    positives = [_normalize_filter_pattern(item[1:] if item.startswith("+") else item)
                 for item in patterns if not item.startswith(("-", "!"))]
    return not positives or any(fnmatch.fnmatch("index.ts", pattern) for pattern in positives)


def _package_entry(settings: Any) -> Any | None:
    if not isinstance(settings, dict) or not isinstance(settings.get("packages"), list):
        return None
    return next((entry for entry in settings["packages"]
                 if package_source(entry) and
                 package_source(entry).split("@", 1)[0] == "npm:pi-subagents"), None)


def has_global_pi_subagents(global_settings: Any, project_settings: Any | None = None) -> bool:
    global_entry = _package_entry(global_settings)
    if not _is_pinned_subagents(package_source(global_entry)) or not _extension_enabled(global_entry):
        return False
    project_entry = _package_entry(project_settings)
    if project_entry is None:
        return True
    if isinstance(project_entry, dict) and project_entry.get("autoload") is False:
        extensions = project_entry.get("extensions")
        if isinstance(extensions, list) and any(
                isinstance(item, str) and item.startswith(("-", "!"))
                and fnmatch.fnmatch(
                    "index.ts", _normalize_filter_pattern(item[1:]))
                for item in extensions):
            return False
        return True
    return (_is_pinned_subagents(package_source(project_entry))
            and _extension_enabled(project_entry))


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


def restore_bundled_subagents(settings: Any, source: str) -> bool:
    if not isinstance(settings, dict) or not isinstance(settings.get("packages"), list):
        raise ValueError("project Pi settings must contain a packages array")
    packages = settings["packages"]
    for index, entry in enumerate(packages):
        if package_source(entry) != source or not isinstance(entry, dict):
            continue
        extensions = entry.get("extensions")
        if not isinstance(extensions, list) or BUNDLED_SUBAGENTS not in extensions:
            return False
        remaining = [item for item in extensions if item != BUNDLED_SUBAGENTS]
        if remaining:
            entry["extensions"] = remaining
        elif set(entry) == {"source", "extensions"}:
            packages[index] = entry["source"]
        else:
            entry.pop("extensions")
        return True
    return False


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
    project_path = project_dir / ".pi" / "settings.json"
    settings = load_json(project_path)
    global_usable = (global_path.exists()
                     and has_global_pi_subagents(load_json(global_path), settings))
    changed = (exclude_bundled_subagents(settings, source) if global_usable
               else restore_bundled_subagents(settings, source))
    if changed:
        project_path.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if global_usable:
            print("  using the global pi-subagents package; disabled siffran's bundled duplicate")
        else:
            print("  global pi-subagents unavailable; restored siffran's bundled runtime")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
