#!/usr/bin/env python3
"""Static guard for the final Claude adapter activation."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path("plugins/empirica")
HOOKS = ROOT / "hooks"
ENTRYPOINTS = {
    "run_start.py": "run_start_main",
    "spawn_gate.py": "spawn_main",
    "route_stamp.py": "route_main",
    "convergence_gate.py": "completion_main",
    "state_restore.py": "restore_main",
    "agent_failure.py": "agent_failure_main",
    "subagent_start.py": "subagent_start_main",
    "subagent_stop.py": "subagent_stop_main",
}
FORBIDDEN = re.compile(r"(?:^|[/'\"`])\.(?:claude|pi)(?:/|[\"'`])")
SKILL = ROOT / "skills/empirica/SKILL.md"
REQUIRED_SKILL_REFERENCES = {
    "governance.md",
    "host-capabilities.md",
    "claim-graph.md",
    "evidence.md",
    "budget-freeze.md",
    "audit.md",
    "handoff.md",
}
STALE_SKILL_TERMS = {"spike_harness.py", "EMPIRICA_STALL_DEADLINE_SEC"}
# Empirica does not ship pi-subagents: the audit runtime is the external extension that owns the
# `subagent` tool. The skill may not claim a bundled, packaged, or single-pinned runtime.
STALE_PI_RUNTIME_TERMS = ("pi-subagents@0.50.0", "pi-subagents 0.50.0", "packaged `pi-subagents",
                          "bundled pi-subagents", "bundled `pi-subagents")
HOST_PROFILES = Path("contracts/empirica/v2/host-profiles.json")
README = Path("README.md")
COMPAT_DIR = ROOT / "adapters/pi/compat"
UNREVIEWED_DISCLOSURE = "unreviewed `pi-subagents` version fails closed"


def pi_profile(document: dict) -> dict:
    """The one profile of ``document`` (parsed host-profiles.json) that declares an external audit runtime."""
    found = [p for p in document["profiles"] if "subagents_compatibility" in p]
    if len(found) != 1:
        raise ValueError(f"expected exactly one profile with subagents_compatibility, found {len(found)}")
    return found[0]


def pi_interval(profile: dict) -> str:
    """The Pi compatibility interval as the docs spell it, read from the contract (never retyped)."""
    compatibility = profile["compatibility"]
    return f">={compatibility['minimum']},<{compatibility['maximum_exclusive']}"


PI_PROFILE = pi_profile(json.loads(HOST_PROFILES.read_text(encoding="utf-8")))
REQUIRED_SKILL_DISCLOSURES = (
    ">=2.1.278,<2.2.0", pi_interval(PI_PROFILE), "external `pi-subagents`", UNREVIEWED_DISCLOSURE,
    ">=0.146.0,<0.147.0", "empirica_observe", "empirica_read", "report_convergence",
)


def _semver(text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)
    if match is None:
        raise ValueError(f"not an exact MAJOR.MINOR.PATCH version: {text!r}")
    return int(match[1]), int(match[2]), int(match[3])


def recorded_peers(policy: dict) -> dict[str, str]:
    """``{version: declared pi-ai peer range}`` from the generated inventories (never retyped)."""
    return {version: json.loads((COMPAT_DIR / f"pi-subagents-{version}.json").read_text(encoding="utf-8"))["peer_pi_ai"]
            for version in policy["reviewed_versions"]}


def pi_requirement(peer: str, host_minimum: str) -> str:
    """The Pi a pi-subagents version needs, as the README spells it: its declared pi-ai floor, raised to
    the floor of the Pi interval Empirica supports (a peer below it adds no constraint)."""
    match = re.fullmatch(r">=(\d+\.\d+\.\d+)", peer)
    if match is None:
        raise ValueError(f"the pi-ai peer range {peer!r} is not a plain `>=MAJOR.MINOR.PATCH` floor; teach this check its form")
    return ">=" + max(match[1], host_minimum, key=_semver)


def pi_fallback(requirements: dict[str, str], host_minimum: str) -> tuple[str, str] | None:
    """``(first Pi that lacks the newest versions, newest version the oldest supported Pi satisfies)``,
    or ``None`` when every reviewed version runs on the whole Pi interval."""
    floors = {version: requirement[2:] for version, requirement in requirements.items()}
    above = sorted({floor for floor in floors.values() if _semver(floor) > _semver(host_minimum)}, key=_semver)
    if not above:
        return None
    fits = [version for version in floors if _semver(floors[version]) <= _semver(host_minimum)]
    return above[0], max(fits, key=_semver)


def readme_runtime_problems(readme_text: str, profile: dict, peers: dict[str, str] | None = None) -> list[str]:
    """How a README misstates which pi-subagents versions are supported (interval, table, fail-closed)
    and which Pi each needs (``peers``: version -> declared pi-ai peer, by default the generated inventories)."""
    policy = profile["subagents_compatibility"]
    peers = recorded_peers(policy) if peers is None else peers
    minimum = profile["compatibility"]["minimum"]
    requirements = {version: pi_requirement(peers[version], minimum) for version in policy["reviewed_versions"]}
    problems = []
    if pi_interval(profile) not in readme_text:
        problems.append(f"README omits the Pi interval {pi_interval(profile)}")
    rows = {match[1]: match for match in re.finditer(
        r"^\|\s*`(\d+\.\d+\.\d+)`\s*\|\s*`([^`|]*)`\s*\|\s*([^|]*?)\s*\|", readme_text, re.MULTILINE)}
    for version in policy["reviewed_versions"]:
        if not re.search(rf"^\|\s*`?{re.escape(version)}`?\s*\|", readme_text, re.MULTILINE):
            problems.append(f"README supported-versions table has no row for pi-subagents {version}")
        elif version not in rows:
            problems.append(f"README row for pi-subagents {version} lacks the 'Requires Pi' and native-receipt columns")
        else:
            if rows[version][2] != requirements[version]:
                problems.append(f"README says pi-subagents {version} requires Pi {rows[version][2]!r}; "
                                f"its declared pi-ai peer {peers[version]} makes it {requirements[version]!r}")
            if not rows[version][3]:
                problems.append(f"README row for pi-subagents {version} does not say whether a native receipt exists")
    table = set(re.findall(r"^\|\s*`?(\d+\.\d+\.\d+)`?\s*\|", readme_text, re.MULTILINE))
    for version in sorted(table - set(policy["reviewed_versions"])):
        problems.append(f"README lists pi-subagents {version}, which the contract does not review")
    fallback = pi_fallback(requirements, minimum)
    if fallback is not None:
        instruction = f"Pi <{fallback[0]}: `pi-subagents@{fallback[1]}`"
        if instruction not in readme_text:
            problems.append(f"README omits the install instruction for older Pi: {instruction}")
    if policy["policy_id"] not in readme_text:
        problems.append(f"README omits the policy id {policy['policy_id']}")
    if UNREVIEWED_DISCLOSURE not in readme_text:
        problems.append(f"README omits: {UNREVIEWED_DISCLOSURE}")
    return problems


def skill_runtime_problems(skill_text: str) -> list[str]:
    """How a SKILL.md misstates the host/runtime boundary (stale bundled claims, missing disclosures)."""
    problems = [f"SKILL.md retains stale bundled Pi runtime claim: {term}"
                for term in STALE_PI_RUNTIME_TERMS if term in skill_text]
    problems += [f"SKILL.md omits complete host/tool disclosure: {required}"
                 for required in REQUIRED_SKILL_DISCLOSURES if required not in skill_text]
    return problems


def fail(message: str) -> None:
    print(f"FAIL activation: {message}", file=sys.stderr)
    raise SystemExit(1)


def normal_runtime_files() -> list[Path]:
    files = []
    for base in (ROOT / "core", ROOT / "application", ROOT / "adapters"):
        for path in base.rglob("*.py"):
            if "tests" in path.parts or "quarantine" in path.parts:
                continue
            files.append(path)
    files.extend(HOOKS / name for name in ENTRYPOINTS)
    return files


def main() -> int:
    quarantine = ROOT / "quarantine"
    if quarantine.exists():
        fail(f"retired duplicate authority still exists: {quarantine}")
    # Codex has its own command-string hook schema and one thin multiplexer. Claude hooks are
    # constrained below to registered thin entrypoints.
    allowed_hooks = {"hooks.json", "codex.json", "codex_hook.py", *ENTRYPOINTS}
    extra_hooks = sorted(path.name for path in HOOKS.iterdir()
                         if path.is_file() and path.name not in allowed_hooks)
    if extra_hooks:
        fail(f"duplicate hook authority remains: {extra_hooks}")

    for path in normal_runtime_files():
        text = path.read_text(encoding="utf-8")
        if FORBIDDEN.search(text):
            fail(f"normal runtime contains a forbidden host-state path: {path}")
        if "quarantine" in text or "legacy-hooks" in text:
            fail(f"normal runtime can reach the legacy quarantine: {path}")

    for name, function in ENTRYPOINTS.items():
        path = HOOKS / name
        text = path.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) > 12 or f"import {function}" not in text:
            fail(f"{path} is not a thin lifecycle entrypoint")
        if "adapters.claude.lifecycle" not in text:
            fail(f"{path} bypasses the Claude lifecycle adapter")

    hooks = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    # Validate the invariant (only registered thin entrypoints), rather than freezing hook config.
    if set(hooks["hooks"]) != {"UserPromptExpansion", "PreToolUse", "PostToolUseFailure",
                                  "Stop", "SubagentStart", "SubagentStop", "SessionStart", "PostModelSwitch"}:
        fail("hooks.json lifecycle events changed")
    for groups in hooks["hooks"].values():
        for group in groups:
            for hook in group.get("hooks", []):
                parts = [hook.get("command"), *hook.get("args", [])]
                names = [match.group(1) for part in parts if isinstance(part, str)
                         for match in [re.search(r"hooks/([^/]+\.py)", part)] if match]
                if len(names) != 1 or names[0] not in ENTRYPOINTS:
                    fail(f"hooks.json bypasses a registered thin entrypoint: {hook}")

    skill_text = SKILL.read_text(encoding="utf-8")
    if len(skill_text.splitlines()) > 300:
        fail("SKILL.md exceeds the 300-line progressive-disclosure budget")
    if len(skill_text.split()) > 3500:
        fail("SKILL.md exceeds the conservative 3,500-word context budget")
    references = SKILL.parent / "references"
    for name in REQUIRED_SKILL_REFERENCES:
        if not (references / name).is_file():
            fail(f"required progressive-disclosure reference is missing: {name}")
        if f"references/{name}" not in skill_text:
            fail(f"SKILL.md does not route to reference: {name}")
    for term in STALE_SKILL_TERMS:
        if term in skill_text:
            fail(f"SKILL.md retains stale runtime term: {term}")
    for problem in skill_runtime_problems(skill_text):
        fail(problem)
    for problem in readme_runtime_problems(README.read_text(encoding="utf-8"), PI_PROFILE):
        fail(problem)

    for path in [SKILL, *sorted((ROOT / "agents").glob("**/*.md"))]:
        text = path.read_text(encoding="utf-8")
        if "~/.empirica-plugin" not in text or "refs/empirica" not in text:
            fail(f"instruction omits authoritative storage locations: {path}")
        matches = [line for line in text.splitlines() if FORBIDDEN.search(line)]
        if matches:
            # Instructions may name forbidden locations only in an explicit prohibition.
            bad = [line for line in matches
                   if not any(word in line.lower() for word in ("never", "forbid", "do not"))]
            if bad:
                fail(f"instruction contains a non-prohibitive legacy path reference: {path}")

    print(f"ok: {len(normal_runtime_files())} runtime files and {len(ENTRYPOINTS)} thin hooks enforce activation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
