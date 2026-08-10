#!/usr/bin/env python3
"""`empirica doctor` — a preflight that DETECTS, and never infers (ADR-24 §4).

Answers one question before a run starts: what can this machine actually reach? It writes
`<run_dir>/actors.json` and returns a RECOMMENDATION. It never reassigns a claim, never enables a
mode, and never blocks a run.

FIVE RULES, each closing a specific way a preflight goes wrong:

  1. DETECTION IS VERSION/CONFIG ONLY — NEVER A MODEL CALL. Probing capability by asking a model
     a question costs tokens, needs credentials, and fails for reasons unrelated to availability
     (a rate limit is not an absence). `--version`, a config read, and a `doctor` subcommand are
     free, deterministic, and answer the actual question. This is enforced structurally: every
     subprocess this module runs comes from `_NON_INFERENTIAL`, and `probe_is_non_inferential()`
     is asserted by the test suite.

  2. THE BASELINE IS NEVER GATED. Claude Code + python3 is the only hard requirement and it is
     present by construction — this code is running. So the doctor cannot fail a baseline run,
     and a doctor that finds nothing optional still returns a usable report.

  3. OPTIONAL TOOLS ARE ONLY PROBED WHEN THEIR MODE IS ON. With multi-provider off, `codex` and
     `pi` are not inspected at all. Scanning regardless would inspect a user's machine for a
     feature they did not enable.

  4. AVAILABLE ≠ PERMITTED. A tool that is installed and working but routed somewhere the user
     did not choose is `configured-but-unapproved`, and never selected by default. Verified
     motivation, not hypothetical: this author's `codex doctor` reported provider `openai` / auth
     mode `chatgpt` — working perfectly, and routing direct to a vendor rather than through the
     Bedrock tenancy. For a user who excludes models on data-retention grounds that is a DIFFERENT
     DECISION, not a detail.

  5. OUTPUT IS A RECOMMENDATION, NOT AN ACTION. The doctor may say "a cross-vendor auditor is
     available — assign it?". Acting on that is the human's call, because a doctor that can be
     wrong (a false "unavailable" silently narrows routing) must not also be able to reroute.

Statuses, coarsest to most useful:
  absent                     — not on PATH
  installed-unconfigured     — on PATH, no provider determinable
  configured-but-unapproved  — provider determined, not in the approved set (rule 4)
  permitted                  — provider determined and approved

Determinism (ADR-19): no clock, no randomness. Timestamps are caller-supplied.
"""
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_io = _load("atomicio")
_actors = _load("actors")
_modes = _load("modes")

ABSENT = "absent"
UNCONFIGURED = "installed-unconfigured"
UNAPPROVED = "configured-but-unapproved"
PERMITTED = "permitted"

# Providers that route through the user's own tenancy. The point of an allow-list rather than a
# deny-list: an unrecognised provider reports as unapproved, so a NEW provider is surfaced for a
# human decision instead of silently trusted.
APPROVED_PROVIDERS = frozenset({"bedrock-mantle", "bedrock-mantle-openai", "amazon-bedrock"})

# RULE 1, made structural. Every argv this module may run, keyed by tool. None of these performs
# inference — they read a version string or a local config. A test asserts this set contains no
# inferential subcommand, so adding one would fail the suite rather than silently start spending
# tokens in a preflight.
_NON_INFERENTIAL = {
    "codex": (("codex", "--version"), ("codex", "doctor")),
    "pi": (("pi", "--version"),),
}
# Subcommands that WOULD invoke a model. Named explicitly so rule 1 is a checkable claim about
# this file rather than a promise in a docstring.
_INFERENTIAL_MARKERS = frozenset({"exec", "-p", "--print", "run", "chat", "complete", "ask",
                                  "prompt", "--mode"})

_TIMEOUT = 60  # a version/config read that hangs is a broken tool, not a slow one


def probe_is_non_inferential() -> bool:
    """RULE 1 as an assertion: no argv this module can execute invokes a model.

    Exists so the property is TESTED rather than trusted. A future edit that adds `codex exec` to
    the probe table fails the suite — which is the only reliable way to keep a "spends no
    inference" guarantee true over time.
    """
    for argvs in _NON_INFERENTIAL.values():
        for argv in argvs:
            if any(part in _INFERENTIAL_MARKERS for part in argv[1:]):
                return False
    return True


def _run(argv: tuple[str, ...]) -> tuple[int | None, str]:
    """Run a non-inferential probe. Returns (returncode|None, combined output).

    Refuses any argv not in the table above — the choke point that makes rule 1 structural rather
    than advisory. A missing/hanging/crashing tool yields None, which reads as "cannot determine",
    never as an exception: a doctor that raises is a doctor that can wedge a run start.
    """
    if argv not in _NON_INFERENTIAL.get(argv[0], ()):
        raise ValueError(f"doctor may only run non-inferential probes, refused: {argv!r}")
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _version(tool: str) -> str | None:
    """The tool's own version string, or None. First line only, truncated: this is for a report."""
    rc, out = _run((tool, "--version"))
    if rc != 0 or not out.strip():
        return None
    return out.strip().splitlines()[0][:60]


def _codex_provider() -> str | None:
    """codex's configured model provider, parsed from `codex doctor`.

    V6 verified this output is regex-parseable; the pattern is deliberately loose about
    surrounding words because a version bump that reflows the line should degrade to "cannot
    determine" (→ unconfigured) rather than mis-report a provider.
    """
    rc, out = _run(("codex", "doctor"))
    if rc is None:
        return None
    m = re.search(r"model provider[:\s]+(\S+)", out, re.IGNORECASE)
    return m.group(1).strip().strip(".,") if m else None


def _pi_provider(home: Path | None = None) -> str | None:
    """pi's configured provider, from `~/.pi/agent/models.json`.

    Prefers an APPROVED provider when several are configured: pi supports many at once, and the
    question this answers is "can this run route through something the user approved?", not
    "what is first in the file".
    """
    root = home or Path.home()
    cfg = root / ".pi" / "agent" / "models.json"
    try:
        raw = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    providers = raw.get("providers")
    if not isinstance(providers, dict) or not providers:
        return None
    names = [n for n in providers if isinstance(n, str)]
    approved = [n for n in names if n in APPROVED_PROVIDERS]
    return (approved or names or [None])[0]


_PROVIDER_READERS = {"codex": lambda home=None: _codex_provider(),
                     "pi": _pi_provider}


def classify(provider: str | None) -> str:
    """provider → status. RULE 4 in one place: available ≠ permitted.

    Split out of `probe` so it is testable WITHOUT the tool being installed. An independent audit
    found this mapping was the one §4 rule with no guard at all: every test either drove
    `_pi_provider` in isolation or asserted only that `probe`'s status was a member of the status
    set — and `permitted` is a member, so replacing the whole branch with `status = PERMITTED`
    destroyed rule 4 and left the suite green. A pure function can be asserted exhaustively on any
    machine, which is what closes that hole.
    """
    if provider is None:
        return UNCONFIGURED
    return PERMITTED if provider in APPROVED_PROVIDERS else UNAPPROVED


def probe(tool: str, home: Path | None = None) -> dict:
    """Classify ONE optional tool using version/config reads only (rules 1 and 4)."""
    if shutil.which(tool) is None:
        return {"tool": tool, "status": ABSENT, "version": None, "provider": None}
    version = _version(tool)
    provider = _PROVIDER_READERS[tool](home) if tool in _PROVIDER_READERS else None
    return {"tool": tool, "status": classify(provider), "version": version, "provider": provider}


def diagnose(run_dir: Path | None = None, *, ts: str | None = None,
             home: Path | None = None) -> dict:
    """The full preflight report.

    RULE 3 is the load-bearing branch: `tools` is EMPTY unless multi-provider mode is on. RULE 2
    means `baseline` is always present and always fine. RULE 5 means `recommendations` are
    sentences for a human, never instructions this code will carry out.
    """
    mode_state = _modes.state(run_dir)
    probe_optional = mode_state[_modes.MULTI_PROVIDER]["enabled"]
    tools = {t: probe(t, home=home) for t in _actors.OPTIONAL_HARNESSES} if probe_optional else {}
    report = {
        # Does this run depart from baseline at all? Reported so the single most important fact
        # about a run — "this is the plugin as everyone else gets it" vs "this one dispatches
        # external processes" — is one field rather than an inference over two mode records.
        "departs_from_baseline": _modes.any_enabled(run_dir),
        # Rule 2: the baseline is a statement of fact, not a check that can fail. If this code is
        # running then Claude Code and python3 are both present.
        "baseline": {"harness": _actors.HARNESS_BASELINE, "status": PERMITTED,
                     "note": "baseline is never gated (ADR-24 §4)"},
        "modes": mode_state,
        # ADR-28: flags typed at invocation that matched no mode. A record, never a gate — but a
        # visible one, because a mode typo otherwise leaves a user believing a run is in a mode it
        # is not in. Empty for every run that typed no flags, so this is additive.
        "unknown_flags": _modes.unknown_flags(run_dir),
        "tools": tools,
        "probed_optional": probe_optional,
        "spends_inference": False,          # rule 1, asserted by probe_is_non_inferential()
        "policy_excluded": dict(_actors.POLICY_EXCLUDED),
        "ts": ts,
    }
    report["recommendations"] = recommend(report)
    return report


def recommend(report: dict) -> list[str]:
    """Sentences for a human (rule 5). Every one describes a choice; none is an action taken."""
    out: list[str] = []
    if not report["probed_optional"]:
        out.append("multi-provider mode is OFF, so codex/pi were not probed. Baseline behaviour "
                   "is unchanged — enable it in modes.json to route claims to other providers.")
    # Each sentence is keyed to the status and RESTATES it verbatim. An audit found the previous
    # form could be mutated so the UNAPPROVED branch rendered "permitted via openai" to a human
    # while `classify()` stayed correct — the pure function was hardened but the sentence a person
    # actually reads was free to lie, and nothing compared them. Naming the status inside the text
    # makes the two inseparable: a branch rendering the wrong sentence now contradicts itself.
    # The USABLE/NOT-USABLE verdict is computed from the status, not written per branch. An audit
    # swapped the UNAPPROVED branch's body for permitted wording while keeping the `{status}`
    # interpolation, and it rendered "status configured-but-unapproved: permitted via `openai`" to a
    # human with the suite green. Interpolating the status was not enough, because the CLAIM in the
    # sentence was still free-text. Now the load-bearing phrase is derived, so a branch cannot say
    # "permitted" about a status that is not PERMITTED.
    detail = {
        ABSENT: "not installed",
        UNCONFIGURED: "installed but no provider could be determined",
        UNAPPROVED: "installed and working, but its provider is not in the approved set",
        PERMITTED: "installed with an approved provider",
    }
    for tool, info in sorted(report["tools"].items()):
        status = info["status"]
        usable = status == PERMITTED
        out.append(
            f"`{tool}` — status {status}: {detail[status]}"
            + (f" (provider `{info['provider']}`)" if info.get("provider") else "")
            + (f" [{info['version']}]" if info.get("version") else "")
            + (". USABLE as an actor: assign it to a claim's `actor` if you want decorrelated "
               "error; the doctor will not assign it for you."
               if usable else
               ". NOT usable as an actor — available is not permitted (ADR-24 §4). Approve its "
               "provider deliberately, or leave it unused."))
    if not report["modes"][_modes.CLI_EXEC]["enabled"]:
        out.append("CLI-exec mode is OFF, so attribution stays DECLARED rather than witnessed "
                   "(ADR-24 §2). Enable it to have empirica dispatch actors itself.")
    return out


def actors_path(run_dir: Path) -> Path:
    """Where the preflight report lands. Transient run state (ADR-14), never committed."""
    return run_dir / "actors.json"


def write_report(run_dir: Path, report: dict) -> Path:
    """Persist the report atomically. Best-effort by contract: see run_start.py, which must never
    let a doctor failure wedge a user's prompt."""
    path = actors_path(run_dir)
    with _io.lock(path):
        _io.atomic_write_json(path, report)
    return path


# --- CLI: `python3 doctor.py [--run-dir <dir>] [--json]` ---------------------
# A human-runnable entry point (`make doctor`) as well as a library the run-start hook calls.
# The doctor is the one part of ADR-24 a user has a direct reason to invoke: "what can my machine
# actually do, and what is empirica going to use?" is a question worth answering before a run, not
# only during one.


def _cli(argv: list[str]) -> int:
    """Print the preflight report. Exit 0 ALWAYS: the doctor diagnoses, it does not gate (rule 2 —
    the baseline is never gated, so there is no failing state for it to return)."""
    run_dir = None
    for i, arg in enumerate(argv):
        if arg == "--run-dir" and i + 1 < len(argv):
            run_dir = Path(argv[i + 1])
    report = diagnose(run_dir)
    if "--json" in argv:
        print(json.dumps(report, indent=2))
        return 0
    print("empirica doctor — preflight (no inference spent)\n")
    print(f"  baseline    {report['baseline']['harness']}: {report['baseline']['status']}")
    for mode, info in report["modes"].items():
        print(f"  mode        {mode}: {'ON' if info['enabled'] else 'off'} ({info['source']})")
    for tool, info in sorted(report["tools"].items()):
        version = f" [{info['version']}]" if info.get("version") else ""
        provider = f" via {info['provider']}" if info.get("provider") else ""
        print(f"  optional    {tool}: {info['status']}{provider}{version}")
    for model, reason in report["policy_excluded"].items():
        print(f"  excluded    {model}: {reason.split(' — ')[0]}")
    print("\n  Recommendations (the doctor never acts on these itself):")
    for line in report["recommendations"]:
        print(f"    - {line}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
