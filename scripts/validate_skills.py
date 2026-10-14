#!/usr/bin/env python3
"""Validate every SKILL.md in the repository against the Agent Skills specification (agentskills.io/specification).

Rules: ``name`` is 1-64 characters of lowercase letters, digits and single hyphens (no leading, trailing or consecutive
hyphen) and equals the skill's directory; ``description`` is 1-1024 characters; ``compatibility`` at most 500; only known
frontmatter keys; the Claude entry of the portable skill is a relative symlink to its canonical directory; every
relative Markdown link in a skill resolves. ``argument-hint`` is the Claude extension the plugin skills use."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWN_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools", "argument-hint"}
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
LINK = re.compile(r"\]\(([^)\s]+)\)")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".ruff_cache"}
PORTABLE = (".claude/skills/native-qualification", ".agents/skills/native-qualification")


def frontmatter(text: str) -> dict[str, str] | None:
    """Top-level ``key: value`` pairs of the leading ``---`` block (indented lines belong to the previous key)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    keys: dict[str, str] = {}
    last = None
    for line in lines[1:]:
        if line.strip() == "---":
            return keys
        if line[:1] in (" ", "\t") or not line.strip():
            if last is not None:
                keys[last] += " " + line.strip()
            continue
        key, sep, value = line.partition(":")
        if not sep:
            return None
        last = key.strip()
        keys[last] = value.strip().strip("\"'")
    return None  # never closed


def skill_files(root: Path) -> list[Path]:
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)  # a symlinked directory is listed but not entered
        if "SKILL.md" in files:
            found.append(Path(base) / "SKILL.md")
    return found


def links(skill_dir: Path) -> list[tuple[Path, str]]:
    out = []
    for md in sorted(skill_dir.rglob("*.md")):
        for target in LINK.findall(md.read_text(encoding="utf-8")):
            if not re.match(r"[a-z][a-z0-9+.-]*:|#", target):
                out.append((md, target))
    return out


def check_skill(skill: Path, root: Path) -> list[str]:
    where, directory = skill.relative_to(root), skill.parent
    meta = frontmatter(skill.read_text(encoding="utf-8"))
    if meta is None:
        return [f"{where}: no closed YAML frontmatter"]
    problems = []
    name = meta.get("name", "")
    if not 1 <= len(name) <= 64 or not NAME.fullmatch(name):
        problems.append(f"{where}: name {name!r} must be 1-64 lowercase letters, digits and single hyphens")
    if name != directory.name:
        problems.append(f"{where}: name {name!r} must equal the directory {directory.name!r}")
    if not 1 <= len(meta.get("description", "")) <= 1024:
        problems.append(f"{where}: description must be 1-1024 characters")
    if len(meta.get("compatibility", "")) > 500:
        problems.append(f"{where}: compatibility must be at most 500 characters")
    problems += [f"{where}: unknown frontmatter key {key!r}" for key in sorted(set(meta) - KNOWN_KEYS)]
    for md, target in links(directory):
        if not (md.parent / target.split("#", 1)[0]).exists():
            problems.append(f"{md.relative_to(root)}: link {target!r} does not resolve")
    return problems


def check_entry(root: Path) -> list[str]:
    entry, canonical = (root / p for p in PORTABLE)
    if not canonical.is_dir():
        return [f"{PORTABLE[1]}: the canonical skill directory is missing"]
    if not entry.is_symlink():
        return [f"{PORTABLE[0]}: must be a symlink to the canonical directory"]
    wanted = os.path.relpath(canonical, entry.parent)
    if os.readlink(entry) != wanted or entry.resolve() != canonical.resolve():
        return [f"{PORTABLE[0]}: must be the relative symlink {wanted}"]
    return []


def validate(root: Path = ROOT) -> list[str]:
    problems = check_entry(root)
    for skill in skill_files(root):
        problems += check_skill(skill, root)
    return problems


def main() -> int:
    problems = validate()
    for line in problems:
        print(line, file=sys.stderr)
    if not problems:
        print(f"skills ok: {len(skill_files(ROOT))} SKILL.md files")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
