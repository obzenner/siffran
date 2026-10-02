#!/usr/bin/env python3
"""Read-only report of requested versus served models for Claude Code subagent launches.

Why this exists: Empirica's audit independence depends on which model actually served the auditor,
not on what was requested or configured. Claude Code records each ``Agent`` tool call in the parent
session transcript and the child's messages in ``<session>/subagents/agent-<id>.jsonl`` (with an
``agent-<id>.meta.json`` naming the parent ``toolUseId``). This tool joins the two and reports, per
launch: subagent type, requested ``model`` (if any), the parent's most recent served model at launch,
every distinct model the child transcript records, and whether the child used ``SubagentHandback``.

Layout (fixed depth, never a recursive walk):

    <projects-root>/<project-dir>/<session-id>.jsonl
    <projects-root>/<project-dir>/<session-id>/subagents/agent-<agent-id>.{jsonl,meta.json}

Guarantees: read-only; malformed lines are counted, not fatal; a launch whose child transcript is
missing is reported as ``child: missing`` rather than dropped.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".claude" / "projects"


@dataclass
class Launch:
    session: str
    tool_use_id: str
    subagent_type: str | None
    requested_model: str | None
    parent_model: str | None
    agent_id: str | None = None
    child: str = "missing"  # present | missing | unreadable
    served_models: list[str] = field(default_factory=list)
    handback: bool = False
    malformed_child_lines: int = 0


def _rows(path: Path) -> Iterator[dict | None]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                yield None
                continue
            yield row if isinstance(row, dict) else None


def _content(row: dict) -> list:
    message = row.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, list) else []


def _model(row: dict) -> str | None:
    message = row.get("message")
    if row.get("type") == "assistant" and isinstance(message, dict):
        model = message.get("model")
        return model if isinstance(model, str) and model else None
    return None


def _child_agents(session: Path) -> dict[str, str]:
    """toolUseId -> agent id, from the fixed subagents/agent-*.meta.json files."""
    mapping: dict[str, str] = {}
    directory = session.with_suffix("") / "subagents"
    if not directory.is_dir():
        return mapping
    for meta in sorted(directory.glob("agent-*.meta.json")):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        tool_use = data.get("toolUseId") if isinstance(data, dict) else None
        if isinstance(tool_use, str):
            mapping[tool_use] = meta.name[len("agent-"):-len(".meta.json")]
    return mapping


def _fill_child(launch: Launch, session: Path) -> None:
    if launch.agent_id is None:
        return
    transcript = session.with_suffix("") / "subagents" / f"agent-{launch.agent_id}.jsonl"
    if not transcript.is_file():
        return
    try:
        models: list[str] = []
        for row in _rows(transcript):
            if row is None:
                launch.malformed_child_lines += 1
                continue
            if (model := _model(row)) and model != "<synthetic>" and model not in models:
                models.append(model)
            if any(isinstance(item, dict) and item.get("type") == "tool_use"
                   and item.get("name") == "SubagentHandback" for item in _content(row)):
                launch.handback = True
        launch.served_models = models
        launch.child = "present"
    except OSError:
        launch.child = "unreadable"


def launches(session: Path, *, agent_filter: str | None = None) -> list[Launch]:
    agents = _child_agents(session)
    out: list[Launch] = []
    parent_model: str | None = None
    for row in _rows(session):
        if row is None:
            continue
        if model := _model(row):
            parent_model = model
        for item in _content(row):
            if not (isinstance(item, dict) and item.get("type") == "tool_use"
                    and item.get("name") == "Agent"):
                continue
            payload = item.get("input") if isinstance(item.get("input"), dict) else {}
            subagent = payload.get("subagent_type")
            if agent_filter and subagent != agent_filter:
                continue
            tool_use = str(item.get("id"))
            requested = payload.get("model")
            launch = Launch(session.stem, tool_use, subagent if isinstance(subagent, str) else None,
                            requested if isinstance(requested, str) else None, parent_model,
                            agents.get(tool_use))
            _fill_child(launch, session)
            out.append(launch)
    return out


def sessions_under(root: Path) -> list[Path]:
    """Exactly <root>/<project-dir>/<session>.jsonl; never recursive."""
    return sorted(root.glob("*/*.jsonl")) if root.is_dir() else []


def render(items: list[Launch]) -> str:
    if not items:
        return "(no matching Agent launches)"
    lines = []
    for launch in items:
        served = ",".join(launch.served_models) or "-"
        lines.append(f"{launch.session[:8]} {str(launch.subagent_type):<28} "
                     f"requested={str(launch.requested_model):<10} parent={str(launch.parent_model):<24} "
                     f"child={launch.child:<10} served={served:<28} handback={launch.handback}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sessions", nargs="*", type=Path, help="parent session transcripts (.jsonl)")
    parser.add_argument("--projects-root", type=Path,
                        help=f"scan <root>/<project>/<session>.jsonl (default root: {DEFAULT_ROOT})")
    parser.add_argument("--agent", help="only this subagent_type, e.g. empirica:empirica-auditor")
    parser.add_argument("--requested-only", action="store_true",
                        help="only launches that passed an explicit model")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    files = list(args.sessions)
    if args.projects_root is not None or not files:
        files += sessions_under(args.projects_root or DEFAULT_ROOT)
    missing = [str(f) for f in args.sessions if not f.is_file()]
    if missing:
        print("session transcript not found: " + ", ".join(missing), file=sys.stderr)
        return 2
    items = [launch for f in files for launch in launches(f, agent_filter=args.agent)
             if not args.requested_only or launch.requested_model]
    print(json.dumps([asdict(i) for i in items], indent=2) if args.json else render(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
