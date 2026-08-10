#!/usr/bin/env python3
"""The two optional modes, both OFF by default, independently toggled (ADR-24 §5).

empirica must stay A PLUGIN ANYONE CAN INSTALL. It cannot assume this author's machine: no
assuming `pi` or `codex` exists, no assuming a Bedrock account, no assuming a token-minting
script. A run on a bare Claude Code install must behave EXACTLY as 0.4.x does — that is the
requirement these flags exist to protect, and it is why the default is off rather than
auto-detect. Auto-detection would make behaviour depend on what happens to be installed, which
is the opposite of a plugin that behaves the same for everyone.

  MODE A — multi-provider. Actors outside the host harness (`pi`, `codex`). While off, those
           tools are NOT PROBED AT ALL: there is no reason to inspect someone's machine for a
           feature they did not enable, and a preflight that scans regardless would be
           surveillance rather than a doctor.

  MODE B — CLI-exec. Dispatch actors as non-interactive subprocesses (`claude -p`, `codex exec`,
           `pi -p`) instead of in-session spawns. Buys dispatcher-WITNESSED attribution, schema
           validated verdicts, and per-claim session continuity.

Orthogonal on purpose: Mode B with Mode A off means dispatching CLAUDE models as CLI subprocesses
— the cheapest way to get witnessed attribution with no external dependency, and probably the
right first increment.

Configuration precedence, most specific first:
  1. environment (`EMPIRICA_MODE_MULTI_PROVIDER`, `EMPIRICA_MODE_CLI_EXEC`) — per-invocation
  2. `<run_dir>/modes.json`                                                 — per-run
  3. off                                                                    — the default

Env wins because it is how an operator overrides one run without editing state, and because a
Makefile target or CI job must be able to force a mode off regardless of what a file says.

Time/determinism: pure reads, no clock, no randomness (ADR-19).
"""
import importlib.util
import json
import os
from pathlib import Path


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_io = _load("atomicio")

MULTI_PROVIDER = "multi_provider"
CLI_EXEC = "cli_exec"
MODES = (MULTI_PROVIDER, CLI_EXEC)
ENV_KEYS = {MULTI_PROVIDER: "EMPIRICA_MODE_MULTI_PROVIDER",
            CLI_EXEC: "EMPIRICA_MODE_CLI_EXEC"}

# Only these spellings turn a mode ON. Anything else — including "yes", "2", "" and garbage — is
# OFF. A permissive truthiness test would let a typo silently enable a mode that changes which
# processes run on a user's machine, so the parse is strict and fails toward the default.
_TRUE = frozenset({"1", "true", "on", "enabled"})
_FALSE = frozenset({"0", "false", "off", "disabled", ""})


def config_path(run_dir: Path) -> Path:
    """Per-run mode configuration. Transient run state (ADR-14), git-ignored, never committed."""
    return run_dir / "modes.json"


def _env_value(mode: str) -> bool | None:
    """The env override for `mode`: True, False, or None when unset/unrecognised.

    An unrecognised value returns None rather than False, so a typo falls through to the file and
    then to the default instead of actively overriding a deliberate per-run setting.
    """
    raw = os.environ.get(ENV_KEYS[mode])
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return None


def _raise_non_finite(_c):
    raise ValueError("non-finite JSON constant")


def _file_modes(run_dir: Path | None) -> dict:
    """Modes from `<run_dir>/modes.json`, or {} when absent/unreadable.

    An unreadable config reads as "no modes configured" — i.e. OFF. Unlike the claim graph, a
    corrupt mode file must NOT fail closed: it configures optional capability, so the safe
    direction is the baseline everyone can run, not a wedged session.
    """
    if run_dir is None:
        return {}
    path = config_path(run_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), parse_constant=_raise_non_finite)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    # BOOLS ONLY. A JSON string `{"cli_exec": "yes"}` must NOT enable a mode: an audit found that
    # relaxing this type check turned a type-corrupt config into an ON switch while the suite stayed
    # green (the corrupt-config test used unparseable JSON, which takes the earlier branch, so the
    # type check itself was unguarded). A mode controls which processes run on a user's machine, so
    # anything but an explicit boolean falls through to the default: OFF.
    return {m: raw[m] for m in MODES if isinstance(raw.get(m), bool)}


def enabled(mode: str, run_dir: Path | None = None) -> bool:
    """Is `mode` on? Env, then per-run file, then OFF."""
    if mode not in MODES:
        return False
    env = _env_value(mode)
    if env is not None:
        return env
    return bool(_file_modes(run_dir).get(mode, False))


def state(run_dir: Path | None = None) -> dict:
    """Both modes plus where each answer came from — so a run report can say "off (default)"
    rather than just "off", and a user who thinks they enabled something can see why it did not
    take effect."""
    out = {}
    file_modes = _file_modes(run_dir)
    for mode in MODES:
        env = _env_value(mode)
        if env is not None:
            out[mode] = {"enabled": env, "source": "env"}
        elif mode in file_modes:
            out[mode] = {"enabled": file_modes[mode], "source": "run-config"}
        else:
            out[mode] = {"enabled": False, "source": "default"}
    return out


def write(run_dir: Path, **flags: bool) -> dict:
    """Set modes for a run. Merges into any existing config, so enabling one mode does not
    silently disable the other. Only the two known modes are written; an unknown key is ignored
    rather than persisted, keeping the file a closed vocabulary."""
    path = config_path(run_dir)
    unknown = sorted(k for k in flags if k not in MODES)
    if unknown:
        # Refuse rather than silently drop. An audit found that removing the filter persisted
        # arbitrary keys — including near-miss typos like `cli_exex` — into the config, so a user who
        # misspelled a mode got a file that LOOKED like it enabled something and did nothing. A
        # closed vocabulary that fails loudly is the point; silently ignoring the key is how the typo
        # survives to the next reader.
        raise ValueError(f"unknown mode(s) {unknown}; valid modes are {sorted(MODES)}")
    with _io.lock(path):
        current = _file_modes(run_dir)
        current.update({m: bool(v) for m, v in flags.items()})
        # Preserve the recorded unknown flags (ADR-28). `_file_modes` deliberately returns only
        # known modes with bool values, so rebuilding the file from it alone DROPPED the typo
        # record — found by its own test. The record has to survive a later mode write, or a run
        # that sets a mode after a typo silently loses the evidence of the typo.
        preserved = _read_unknown(path)
        payload = {**current, UNKNOWN_KEY: preserved} if preserved else current
        _io.atomic_write_json(path, payload)
    return current


def any_enabled(run_dir: Path | None = None) -> bool:
    """True when this run departs from baseline behaviour at all. The doctor uses it to decide
    whether to probe anything beyond the baseline (ADR-24 §4)."""
    return any(enabled(m, run_dir) for m in MODES)


# --- invocation flags (ADR-28) ----------------------------------------------
# The flag spelling a user types, mapped to the mode it sets. `--no-` prefixes are derived, so
# adding a mode here gives you both polarities and cannot forget one.
FLAGS = {"--multi-provider": MULTI_PROVIDER, "--cli-exec": CLI_EXEC}


def parse_flags(text: str) -> tuple[dict, list[str]]:
    """Read mode flags out of an invocation's argument text. Returns (flags, unknown_flags).

    `/empirica --cli-exec design X` → ({"cli_exec": True}, []). `--no-cli-exec` sets it False, so
    an operator can force a mode off for one run without editing state.

    UNRECOGNISED FLAGS ARE RETURNED, NOT IGNORED. A user who types `--cli-exex` must be told,
    for the same reason `write()` refuses unknown keys: a typo that silently does nothing gives
    you a run you believe is in a mode it is not in. The caller decides how loud to be — the
    run-start hook cannot print (it must never wedge a prompt), so it records them for the report.

    Only tokens in the LEADING flag run are considered, and parsing stops at the first
    non-flag token. Otherwise a goal like "make `--cli-exec` the default" would enable the mode
    just by mentioning it — the same command-position bug that `dispatch_gate` already had to fix
    for actor dispatches.

    Pure: no clock, no environment, no writes (ADR-19). The caller persists the result.
    """
    flags: dict[str, bool] = {}
    unknown: list[str] = []
    for token in (text or "").split():
        if not token.startswith("--"):
            break  # end of the flag run; the rest is the goal
        if token in FLAGS:
            flags[FLAGS[token]] = True
        elif token.startswith("--no-") and f"--{token[5:]}" in FLAGS:
            flags[FLAGS[f"--{token[5:]}"]] = False
        else:
            unknown.append(token)
    return flags, unknown


UNKNOWN_KEY = "unknown_flags"


def _read_unknown(path: Path) -> list[str]:
    """The recorded unknown flags in a mode file, or [] — one reader, shared by `write`,
    `record_unknown_flags` and `unknown_flags`, so the three cannot disagree about the shape."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), parse_constant=_raise_non_finite)
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    return [f for f in (raw.get(UNKNOWN_KEY) or []) if isinstance(f, str)]


def record_unknown_flags(run_dir: Path, unknown: list[str]) -> None:
    """Persist flags that matched no mode, so a typo is VISIBLE instead of silent.

    Kept in `modes.json` under a key outside the mode vocabulary, so `_file_modes` (which reads
    only known modes with bool values) ignores it entirely and no mode can be enabled by it. The
    doctor surfaces it; that is the whole mechanism — a record, never a gate.

    Best-effort and non-raising on a corrupt file: failing to record a typo must not be worse
    than the typo.
    """
    if not unknown:
        return
    path = config_path(run_dir)
    with _io.lock(path):
        current = _file_modes(run_dir)
        seen = _read_unknown(path)
        merged = seen + [f for f in unknown if f not in seen]
        _io.atomic_write_json(path, {**current, UNKNOWN_KEY: merged})


def unknown_flags(run_dir: Path | None) -> list[str]:
    """Unrecognised flags recorded for this run, for the doctor's report."""
    return [] if run_dir is None else _read_unknown(config_path(run_dir))


def strip_flags(text: str) -> str:
    """The invocation text with its leading mode flags removed — i.e. the actual goal.

    The skill needs this because `$ARGUMENTS` still contains the flags: without stripping them,
    `--cli-exec` becomes part of the goal the run is trying to establish, and a claim graph rooted
    in "--cli-exec design X" is a subtly corrupted intent.
    """
    tokens = (text or "").split()
    i = 0
    while i < len(tokens) and tokens[i].startswith("--"):
        i += 1
    return " ".join(tokens[i:])
