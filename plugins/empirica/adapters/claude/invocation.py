"""Pure Claude invocation translation with explicit unknown-flag reporting."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from adapters.invocation import split_leading_flags


@dataclass(frozen=True)
class Invocation:
    goal: str
    unknown_flags: tuple[str, ...]
    control_mode: str = "deliberative"


def invocation_args(payload: Mapping[str, object]) -> str:
    args = payload.get("command_args")
    if isinstance(args, str) and args.strip():
        return args
    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        parts = prompt.split(None, 1)
        if len(parts) == 2:
            return parts[1]
    return ""


def parse_invocation(payload: Mapping[str, object], *, environ: Mapping[str, str]) -> Invocation:
    """Consume only ``--auto`` and retain every other leading flag as unknown."""
    del environ
    leading, goal = split_leading_flags(invocation_args(payload))
    unknown = tuple(token for token in leading if token != "--auto")
    control_mode = "auto" if "--auto" in leading else "deliberative"
    return Invocation(goal, unknown, control_mode)
