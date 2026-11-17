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
    "permission_denied.py": "permission_denied_main",
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
HOSTS_DOC = Path("doc/hosts.md")
COMPAT_DIR = ROOT / "adapters/pi/compat"
SUPPORT_RANGE_DISCLOSURE = "outside the supported range fails closed"


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
    ">=2.1.278,<2.2.0", pi_interval(PI_PROFILE), "external pi-subagents",
    "empirica_observe", "empirica_read", "report_convergence",
)


def _semver(text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)
    if match is None:
        raise ValueError(f"not an exact MAJOR.MINOR.PATCH version: {text!r}")
    return int(match[1]), int(match[2]), int(match[3])


def reviewed_versions() -> list[str]:
    """The versions with a checked-in compat record, ascending: the reviewed ones (ADR-0069)."""
    return sorted((path.name.removeprefix("pi-subagents-").removesuffix(".json")
                   for path in COMPAT_DIR.glob("pi-subagents-*.json")), key=_semver)


def recorded_peers() -> dict[str, str]:
    """``{version: declared pi-ai peer range}`` from the generated inventories (never retyped)."""
    return {version: json.loads((COMPAT_DIR / f"pi-subagents-{version}.json").read_text(encoding="utf-8"))["peer_pi_ai"]
            for version in reviewed_versions()}


def pi_requirement(peer: str, host_minimum: str) -> str:
    """The Pi a pi-subagents version needs, as doc/hosts.md spells it: its declared pi-ai floor, raised to
    the floor of the Pi interval Empirica supports (a peer below it adds no constraint)."""
    match = re.fullmatch(r">=(\d+\.\d+\.\d+)", peer)
    if match is None:
        raise ValueError(f"the pi-ai peer range {peer!r} is not a plain `>=MAJOR.MINOR.PATCH` floor; teach this check its form")
    return ">=" + max(match[1], host_minimum, key=_semver)


def hosts_doc_runtime_problems(doc_text: str, profile: dict, peers: dict[str, str] | None = None) -> list[str]:
    """How doc/hosts.md misstates which pi-subagents versions are supported (interval, table, fail-closed)
    and which Pi each needs (``peers``: version -> declared pi-ai peer, by default the generated inventories)."""
    policy = profile["subagents_compatibility"]
    peers = recorded_peers() if peers is None else peers
    minimum = profile["compatibility"]["minimum"]
    requirements = {version: pi_requirement(peers[version], minimum) for version in reviewed_versions()}
    problems = []
    if pi_interval(profile) not in doc_text:
        problems.append(f"doc/hosts.md omits the Pi interval {pi_interval(profile)}")
    rows = {match[1]: match for match in re.finditer(
        r"^\|\s*`(\d+\.\d+\.\d+)`\s*\|\s*`([^`|]*)`\s*\|", doc_text, re.MULTILINE)}
    for version in reviewed_versions():
        if not re.search(rf"^\|\s*`?{re.escape(version)}`?\s*\|", doc_text, re.MULTILINE):
            problems.append(f"doc/hosts.md supported-versions table has no row for pi-subagents {version}")
        elif version not in rows:
            problems.append(f"doc/hosts.md row for pi-subagents {version} lacks the 'Requires Pi' column")
        else:
            if rows[version][2] != requirements[version]:
                problems.append(f"doc/hosts.md says pi-subagents {version} requires Pi {rows[version][2]!r}; "
                                f"its declared pi-ai peer {peers[version]} makes it {requirements[version]!r}")
    table = set(re.findall(r"^\|\s*`?(\d+\.\d+\.\d+)`?\s*\|", doc_text, re.MULTILINE))
    for version in sorted(table - set(reviewed_versions())):
        problems.append(f"doc/hosts.md lists pi-subagents {version}, which has no compat record")
    range_text = f">={policy['minimum']},<{policy['maximum_exclusive']}"
    if range_text not in doc_text:
        problems.append(f"doc/hosts.md omits the supported pi-subagents range {range_text}")
    if policy["policy_id"] not in doc_text:
        problems.append(f"doc/hosts.md omits the policy id {policy['policy_id']}")
    if SUPPORT_RANGE_DISCLOSURE not in doc_text:
        problems.append(f"doc/hosts.md omits: {SUPPORT_RANGE_DISCLOSURE}")
    return problems


def skill_runtime_problems(skill_text: str) -> list[str]:
    """How a SKILL.md misstates the host/runtime boundary (stale bundled claims, missing disclosures)."""
    problems = [f"SKILL.md retains stale bundled Pi runtime claim: {term}"
                for term in STALE_PI_RUNTIME_TERMS if term in skill_text]
    problems += [f"SKILL.md omits complete host/tool disclosure: {required}"
                 for required in REQUIRED_SKILL_DISCLOSURES if required not in skill_text]
    return problems


# The size ratchet for SKILL.md, measured in characters (code points) as a proxy for tokens.
# Measured: 19,502 characters = 6,576 tokens (Bedrock Sonnet 5.5, `usage.input_tokens`), 2.966 characters per token.
# After compaction Claude Code re-attaches only the first 5,000 tokens of an invoked skill
# (https://code.claude.com/docs/en/skills, "skill content lifecycle"). Re-attached skills share a 25,000-token budget and
# older skills can be dropped entirely, so even the top 5,000 tokens are not guaranteed. END TARGET: 7,400 characters,
# about 2,500 tokens. A ratchet: later changes lower SKILL_MAX_CHARS toward the target and must never raise it;
# SKILL_MAX_CHARS may exceed the file by at most SKILL_MAX_HEADROOM, so shrinking the skill forces the ceiling down.
# The rule across revisions: the constants test's upper bound is the CURRENT ceiling, and every PR that lowers
# SKILL_MAX_CHARS lowers that bound in the same edit, so a later raise needs a visible test edit.
SKILL_MAX_CHARS = 17_600
SKILL_MAX_HEADROOM = 1_000
# SKILL_TOP_CHARS is the early-anchor cut. 5,000 x 2.966 = 14,830; 13,300 is about 90% of that, because the ratio is a
# whole-file average (a prefix dense in code or identifiers fits fewer characters) and the counter Claude Code uses for
# the cut is undocumented. The basis is the RENDERED body: the text after the closing frontmatter `---` (Claude strips
# it), in code points. Claude substitutes the user's goal (up to 4,096 characters, plus flags) for `$ARGUMENTS` before it
# cuts, so no fixed allowance is safe: every anchor must end before the first `$ARGUMENTS`, where the offsets are exact.
# The prefix's own ratio is to be measured when the anchors are chosen.
SKILL_TOP_CHARS = 13_300
ARGUMENTS_TOKEN = "$ARGUMENTS"
# Literal strings that must appear exactly once, entirely within the rendered top SKILL_TOP_CHARS characters, and before
# the `$ARGUMENTS` line: the facts an agent needs after compaction. In order: the
# stance, experiments only through Empirica, no reads before the `investigate` witness, never touch Empirica state, never grade contrary evidence away, never fabricate host ingress, the audit
# launch, `Allow(converged=true)`, the honest `stop`, and the end of the terminal-kinds list.
REQUIRED_EARLY_ANCHORS: tuple[str, ...] = (
    "Treat training-weight knowledge as hypothesis",
    "**Experiments go through Empirica only.**",
    "**Do not read any file, including a skill reference, before the",
    "Never edit operational state under",
    "grading contrary evidence away",
    "are trusted host ingress",
    "Claude launches `empirica:empirica-auditor`, Pi calls the",
    "`Allow(converged=true)` permits a convergence claim.",
    "call it once with `intent: \"stop\"`",
    "**faulted** — state, artifacts, transport, or protocol failed closed.",
)


# The skill body names no host version, range, or `pi-subagents` range: the host profiles
# (contracts/empirica/v2/host-profiles.json), the start refusals (`host.*`, `host.subagents_*`) and doc/hosts.md own them,
# so a copy in the skill only drifts. Three shapes: a dotted version of three or more numbers (`2.1.296`), a comparator
# before a version (`>=1.0`, `<2.0.0`), and a version right after `pi-subagents` (`@0.50.0`, ` 0.74`, ` (0.74, 1.0)`).
# The frontmatter's `compatibility` field is install metadata, outside this guard.
_HOST_LITERALS = (
    re.compile(r"(?<![\w.])\d+\.\d+\.\d+(?![\w.])"),
    re.compile(r"(?:[<>]=?|~=|\^)\s*v?\d+(?:\.\d+)*"),
    re.compile(r"pi-subagents[^\n]{0,40}?(?:@\s*v?\d|\bv?\d+\.\d+)"),
)


def skill_host_literal_problems(skill_text: str) -> list[str]:
    """One problem when the rendered body carries a host version literal or range (see ``_HOST_LITERALS``)."""
    body = rendered_body(skill_text)
    found = []
    for pattern in _HOST_LITERALS:
        found += [" ".join(match.group(0).split()) for match in pattern.finditer(body)]
    if not found:
        return []
    return [f"SKILL.md body names a host version or range ({', '.join(sorted(set(found))[:5])}); host compatibility "
            "belongs to the host profiles and the start refusals, never to the skill"]


def skill_size_problems(skill_text: str, max_chars: int | None = None) -> list[str]:
    """The whole SKILL.md (frontmatter included) is within the ceiling, and the ceiling within SKILL_MAX_HEADROOM of it."""
    limit = SKILL_MAX_CHARS if max_chars is None else max_chars
    size = len(skill_text)
    if size > limit:
        return [f"SKILL.md is {size:,} characters, over the {limit:,}-character ceiling; "
                "move sometimes-needed material to references/ (Claude re-attaches only ~5,000 tokens after compaction)"]
    if limit - size > SKILL_MAX_HEADROOM:
        return [f"SKILL.md is {size:,} characters, {limit - size:,} under the {limit:,}-character ceiling "
                f"(more than {SKILL_MAX_HEADROOM:,}); lower SKILL_MAX_CHARS to {-(-(size + 500) // 100) * 100:,}"]
    return []


def rendered_body(skill_text: str) -> str:
    """The text Claude keeps for the cut: ``skill_text`` without its frontmatter (delimiter lines are ``strip() == "---"``,
    as in scripts/validate_skills.py)."""
    lines = skill_text.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                return "".join(lines[index + 1:])
    return skill_text


def skill_anchor_problems(skill_text: str, anchors: tuple[str, ...] | list[str] | None = None,
                          top_chars: int = SKILL_TOP_CHARS) -> list[str]:
    """Anchors that are empty, absent from the body, or not entirely within its first ``top_chars`` rendered characters."""
    body = rendered_body(skill_text)
    goal_at = body.find(ARGUMENTS_TOKEN)
    problems = []
    if body.count(ARGUMENTS_TOKEN) > 1 or re.search(r"\$ARGUMENTS\[|\$\{CLAUDE_|\$\d", body):
        problems.append(f"SKILL.md body has more than one `{ARGUMENTS_TOKEN}` or another substitution form "
                        "(`$ARGUMENTS[`, `${CLAUDE_`, `$` and a digit): the anchor offsets would move by each substituted value; "
                        "escape or reword the `$` form")
    for anchor in REQUIRED_EARLY_ANCHORS if anchors is None else anchors:
        if not anchor.strip() or "$" in anchor:
            problems.append(f"SKILL.md early anchor {anchor!r} is empty or contains `$` (not literal after substitution)")
            continue
        at = body.find(anchor)
        if at == -1:
            problems.append(f"SKILL.md anchor {anchor!r} is missing")
            continue
        end = at + len(anchor)
        if goal_at != -1 and end > goal_at:
            problems.append(f"SKILL.md anchor {anchor!r} must end before the first `{ARGUMENTS_TOKEN}` "
                            f"(ends at {end:,}, `{ARGUMENTS_TOKEN}` at {goal_at:,}): the goal's length would move it")
            continue
        if end > top_chars:
            problems.append(f"SKILL.md anchor {anchor!r} is not entirely within the first {top_chars:,} rendered characters "
                            f"(ends at {end:,})")
    return problems


# The three references whose judgment an agent needs before the `investigate` witness, when it cannot read any file: the
# skill carries that judgment inline, so their header says they only add detail for later.
PRE_INVESTIGATION_REFERENCES = ("claim-graph.md", "governance.md", "budget-freeze.md")
REFERENCE_LATER_HEADER = ("The rules needed before investigation are in the skill; "
                          "this reference adds the detail for later.")
_LINK = re.compile(r"\]\((?:\./)?references/([A-Za-z0-9._-]+\.md)(?:#([^)]*))?\)")
_BEFORE_ROUTE_HEADER = re.compile(r"\b[Rr]ead\b[^.]*\bbefore\b[^.]*\b(?:seeding|routing|the route)\b")


def _slugs(text: str) -> set[str]:
    return {re.sub(r"[^a-z0-9 _-]", "", line.lstrip("#").strip().lower()).replace(" ", "-")
            for line in text.splitlines() if line.startswith("#")}


def skill_link_problems(skill_text: str, references: Path) -> list[str]:
    """Every reference link in the skill resolves (file, and heading when an anchor is named; a leading ``./`` is the same
    link), and the route section (6) carries no reference link at all: the agent cannot read any file until the
    `investigate` witness, so a link there is an instruction it cannot follow."""
    problems = []
    for match in _LINK.finditer(skill_text):
        name, anchor = match.groups()
        path = references / name
        if not path.is_file():
            problems.append(f"SKILL.md links to references/{name}, which does not exist")
        elif anchor and anchor not in _slugs(path.read_text(encoding="utf-8")):
            problems.append(f"SKILL.md links to references/{name}#{anchor}, which has no such heading")
    start = skill_text.find("\n## 6. ")
    end = skill_text.find("\n## 7. ", start + 1)
    if start != -1 and end != -1:
        for link in _LINK.finditer(skill_text[start:end]):
            problems.append(f"SKILL.md section 6 links to references/{link.group(1)} before investigation is admitted; "
                            "inline the judgment instead (every read is denied until the `investigate` witness)")
    return problems


def reference_header_problems(references: Path) -> list[str]:
    """The three pre-investigation references open with the standard sentence, and no reference tells the agent to
    read it before seeding a graph or routing."""
    problems = []
    for path in sorted(references.glob("*.md")):
        blocks = path.read_text(encoding="utf-8").split("\n\n", 2)
        header = blocks[1] if len(blocks) > 1 else ""
        if path.name in PRE_INVESTIGATION_REFERENCES and header != REFERENCE_LATER_HEADER:
            problems.append(f"references/{path.name} must open with: {REFERENCE_LATER_HEADER}")
        elif _BEFORE_ROUTE_HEADER.search(header):
            problems.append(f"references/{path.name} header says to read it before routing: {header!r}")
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


HOOK_EVENTS = frozenset({
    "UserPromptExpansion", "PreToolUse", "PostToolUseFailure", "PermissionDenied",
    "Stop", "SubagentStart", "SubagentStop", "SessionStart", "PostModelSwitch"})


def hook_event_problems(hooks: dict) -> list[str]:
    """The Claude hook set is closed, and every hook is a registered thin entrypoint.

    Validates the invariant (only registered thin entrypoints) rather than freezing hook config.
    """
    if set(hooks["hooks"]) != HOOK_EVENTS:
        return ["hooks.json lifecycle events changed"]
    problems = []
    for groups in hooks["hooks"].values():
        for group in groups:
            for hook in group.get("hooks", []):
                parts = [hook.get("command"), *hook.get("args", [])]
                names = [match.group(1) for part in parts if isinstance(part, str)
                         for match in [re.search(r"hooks/([^/]+\.py)", part)] if match]
                if len(names) != 1 or names[0] not in ENTRYPOINTS:
                    problems.append(f"hooks.json bypasses a registered thin entrypoint: {hook}")
    return problems


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
    for problem in hook_event_problems(hooks):
        fail(problem)

    skill_text = SKILL.read_text(encoding="utf-8")
    if len(skill_text.splitlines()) > 300:
        fail("SKILL.md exceeds the 300-line progressive-disclosure budget")
    if len(skill_text.split()) > 3500:
        fail("SKILL.md exceeds the conservative 3,500-word context budget")
    for problem in [*skill_size_problems(skill_text), *skill_anchor_problems(skill_text),
                    *skill_host_literal_problems(skill_text)]:
        fail(problem)
    references = SKILL.parent / "references"
    for name in REQUIRED_SKILL_REFERENCES:
        if not (references / name).is_file():
            fail(f"required progressive-disclosure reference is missing: {name}")
        if f"references/{name}" not in skill_text:
            fail(f"SKILL.md does not route to reference: {name}")
    for problem in [*skill_link_problems(skill_text, references), *reference_header_problems(references)]:
        fail(problem)
    for term in STALE_SKILL_TERMS:
        if term in skill_text:
            fail(f"SKILL.md retains stale runtime term: {term}")
    for problem in skill_runtime_problems(skill_text):
        fail(problem)
    for problem in hosts_doc_runtime_problems(HOSTS_DOC.read_text(encoding="utf-8"), PI_PROFILE):
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
