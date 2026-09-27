"""Shared host invocation translation primitives."""
from __future__ import annotations

from collections.abc import Mapping
import re

from application import protocol as _proto

_TRUE = frozenset({"1", "true", "on", "enabled"})
_FALSE = frozenset({"0", "false", "off", "disabled", ""})


def env_mode(environ: Mapping[str, str], key: str) -> bool | None:
    """Decode the common tri-state environment vocabulary."""
    raw = environ.get(key)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return None


def provenance(
    host: str,
    interactive: bool | None,
    signal: str,
    environ: Mapping[str, str],
    *,
    profile_id: str,
) -> dict[str, object]:
    """Build the canonical attested invocation provenance shape."""
    delegation_env = _proto.host_profile(profile_id)["delegation_env"]
    return {
        "host": host,
        "interactive": interactive,
        "signal": signal,
        "delegation": environ.get(delegation_env) == "1",
    }


def split_leading_flags(args: str) -> tuple[list[str], str]:
    """Return leading ``--`` tokens and the verbatim remaining goal."""
    matches = list(re.finditer(r"\S+", args))
    index = 0
    while index < len(matches) and matches[index].group().startswith("--"):
        index += 1
    goal = args if index == 0 else (args[matches[index].start():] if index < len(matches) else "")
    return [match.group() for match in matches[:index]], goal
