"""Shared host invocation translation primitives."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re

from application import protocol as _proto


#: Reviewed mapping from a Claude auditor tool to the retrieval kinds it is observed to cover. ``Read``,
#: ``Glob`` and ``Grep`` are all file tools; only ``Read`` is credited. ``WebFetch`` is declared by the
#: definition but nothing observes that it is available or permitted, so it adds nothing: a URL routes to a
#: human (ADR-0067). A tool missing here adds nothing.
_CLAUDE_TOOL_RETRIEVAL = {"Read": ("local", "workspace")}
_CLAUDE_AUDITOR = Path(__file__).resolve().parents[1] / "agents" / "empirica-auditor.md"


def _declared_tools(definition: Path) -> list[str]:
    """The ``tools:`` list in the front matter of a plugin-owned agent definition."""
    head = definition.read_text(encoding="utf-8").split("---", 2)
    if len(head) < 3:
        raise ValueError(f"{definition.name} has no front matter")
    for line in head[1].splitlines():
        if line.startswith("tools:"):
            return [tool.strip() for tool in line.partition(":")[2].split(",") if tool.strip()]
    raise ValueError(f"{definition.name} declares no tools")


def audit_retrieval(profile_id: str) -> list[str]:
    """The retrieval kinds the profile's auditor is observed to cover, sorted.

    Claude reads the plugin-owned auditor definition; Codex has no auditor. Pi observes its own set from
    the live preflight in TypeScript and sends it, so it has no answer here."""
    host = _proto.host_profile(profile_id)["host_id"]
    if host == "codex-cli":
        return []
    if host != "claude-code":
        raise KeyError(profile_id)
    kinds = {kind for tool in _declared_tools(_CLAUDE_AUDITOR) for kind in _CLAUDE_TOOL_RETRIEVAL.get(tool, ())}
    return sorted(kinds)


def provenance(
    host: str,
    interactive: bool | None,
    signal: str,
    environ: Mapping[str, str],
    *,
    profile_id: str,
    human_surface: str = "unobserved",
    session: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build the canonical attested invocation provenance shape.

    ``human_surface`` is what the host observes it can show a human at start; a host that cannot yet tell
    (Claude's hook runs before the MCP client has declared form elicitation) starts ``unobserved`` and records
    ``human`` or ``unavailable`` through the private ingress inside ``report_convergence``. A host that knows at
    start (Pi's ``ctx.hasUI``) or has no human ingress at all (Codex) passes its answer. ``session`` is where the host
    observed the session to live (``{"label", "locator"}``); a host that observed nothing passes none and the member is
    absent: the value is then unknown, never invented."""
    delegation_env = _proto.host_profile(profile_id)["delegation_env"]
    return {
        "host": host,
        "interactive": interactive,
        "signal": signal,
        "delegation": environ.get(delegation_env) == "1",
        "audit_retrieval": audit_retrieval(profile_id),
        "human_surface": human_surface,
        **({} if session is None else {"session": dict(session)}),
    }


def split_leading_flags(args: str) -> tuple[list[str], str]:
    """Return leading ``--`` tokens and the verbatim remaining goal."""
    matches = list(re.finditer(r"\S+", args))
    index = 0
    while index < len(matches) and matches[index].group().startswith("--"):
        index += 1
    goal = args if index == 0 else (args[matches[index].start():] if index < len(matches) else "")
    return [match.group() for match in matches[:index]], goal
