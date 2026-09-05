"""Pure Claude invocation-mode translation (ADR-28), with no mode side files."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

MODES = ("multi_provider", "cli_exec")
FLAGS = {"--multi-provider": "multi_provider", "--cli-exec": "cli_exec"}
ENV_KEYS = {
    "multi_provider": "EMPIRICA_MODE_MULTI_PROVIDER",
    "cli_exec": "EMPIRICA_MODE_CLI_EXEC",
}
_TRUE = frozenset({"1", "true", "on", "enabled"})
_FALSE = frozenset({"0", "false", "off", "disabled", ""})


@dataclass(frozen=True)
class Invocation:
    goal: str
    modes: dict[str, bool]
    sources: dict[str, str]
    unknown_flags: tuple[str, ...]


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


def _env_value(environ: Mapping[str, str], mode: str) -> bool | None:
    raw = environ.get(ENV_KEYS[mode])
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return None


def parse_invocation(
    payload: Mapping[str, object], *, environ: Mapping[str, str], fallback_goal: str,
) -> Invocation:
    """Resolve env > leading invocation flag > default and retain unknown leading flags.

    Unknown environment values do not override a valid invocation flag.  Unknown flags never enter
    the closed mode vocabulary, but remain visible to the doctor report instead of disappearing.
    """
    tokens = invocation_args(payload).split()
    flags: dict[str, bool] = {}
    unknown: list[str] = []
    index = 0
    while index < len(tokens) and tokens[index].startswith("--"):
        token = tokens[index]
        if token in FLAGS:
            flags[FLAGS[token]] = True
        elif token.startswith("--no-") and f"--{token[5:]}" in FLAGS:
            flags[FLAGS[f"--{token[5:]}"]] = False
        else:
            unknown.append(token)
        index += 1

    modes: dict[str, bool] = {}
    sources: dict[str, str] = {}
    for mode in MODES:
        env = _env_value(environ, mode)
        if env is not None:
            modes[mode] = env
            sources[mode] = "env"
        elif mode in flags:
            modes[mode] = flags[mode]
            sources[mode] = "invocation"
        else:
            sources[mode] = "default"
    goal = " ".join(tokens[index:]).strip() or fallback_goal
    return Invocation(goal, modes, sources, tuple(unknown))


def build_mode_request(run_id: str, modes: Mapping[str, bool], *, request_id: str) -> dict:
    """Build the typed operational mode update used when configuring an existing run."""
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty application run handle")
    selected = {key: value for key, value in modes.items() if key in MODES}
    unknown = sorted(set(modes) - set(MODES))
    if unknown or any(not isinstance(value, bool) for value in selected.values()):
        raise ValueError(f"invalid mode configuration: unknown={unknown!r}")
    return {
        "protocol": "empirica/v1",
        "request_id": request_id,
        "command": {
            "type": "ObserveAction",
            "run_id": run_id,
            "action": {"kind": "mode", "modes": selected},
        },
    }
