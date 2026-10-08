"""Structural verification for operator-captured installed-host Empirica receipts."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "empirica"))
from adapters.git.artifact_repo import ref_component, tree_path  # noqa: E402 - the one owner of the refname derivation
from application.snapshot import HistoryCorrupt, committed_artifacts, index_artifacts, manifest_chain  # noqa: E402
from core.records import Artifact  # noqa: E402

FORMAT = "empirica-live-receipt/v3"
_CONTRACTS = Path(__file__).resolve().parents[1] / "contracts/empirica/v2"
_PROFILE_REGISTRY = json.loads((_CONTRACTS / "host-profiles.json").read_text())
# Author-view headings and line labels are contract-owned; the projection is the repository JSON.
_LABELS = json.loads((_CONTRACTS / "public-tools.json").read_text())["author_view"]["labels"]
_RECEIPT_HOSTS = {
    "claude": ("claude-code", "empirica:empirica-auditor"),
    "pi": ("pi", "empirica.empirica-auditor"),
}
_POLICIES = {}
for _receipt_host, (_host_id, _role) in _RECEIPT_HOSTS.items():
    _profile = next(profile for profile in _PROFILE_REGISTRY["profiles"]
                    if profile["host_id"] == _host_id and profile["promotion_status"] == "promoted")
    _POLICIES[_receipt_host] = _profile
EXPECTED = {host: (policy["profile_id"], _RECEIPT_HOSTS[host][1]) for host, policy in _POLICIES.items()}
MAX_TRACE_BYTES = 128 << 20
_VERSION_OUTPUT = {
    "claude": re.compile(r"^([0-9]+\.[0-9]+\.[0-9]+)(?:\s+\(Claude Code\))?\s*$"),
    "pi": re.compile(r"^([0-9]+\.[0-9]+\.[0-9]+)\s*$"),
}
_VERDICT = re.compile(r"```empirica-verdict\s*\n(\{.*?\})\s*\n```", re.DOTALL)
_AGENT_ID = re.compile(r"agentId:\s*([A-Za-z0-9_-]+)")
_HANDBACK_FRAME = re.compile(
    r'<agent-message from="([A-Za-z0-9_-]+)">\n\[Subagent hand-back\][^\n]* The report follows:\n'
    r'((?:  [^\n]*\n)*  [^\n]*)\n</agent-message>')


def safe_read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_TRACE_BYTES:
            raise ValueError(f"invalid retained file: {path}")
        with os.fdopen(os.dup(fd), "rb") as stream:
            content = stream.read(MAX_TRACE_BYTES + 1)
        after = os.fstat(fd)
        if (len(content) > MAX_TRACE_BYTES or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise ValueError(f"changed/oversized retained file: {path}")
        return content
    finally:
        os.close(fd)


def native_version(host: str, path: Path) -> str:
    try:
        text = safe_read(path).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("native version output is not UTF-8") from exc
    pattern = _VERSION_OUTPUT.get(host)
    if pattern is None:
        raise ValueError(f"unsupported receipt host: {host!r}")
    match = pattern.fullmatch(text)
    if match is None:
        raise ValueError("native version output is malformed")
    return match.group(1)


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)", value)
    if match is None:
        raise ValueError(f"invalid semantic version: {value!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def require_compatible_version(host: str, version: str) -> None:
    """A receipt's host is inside the profile's interval and at or above the profile's qualified version.

    The qualified version is the profile's ``version``: raising it asks for exactly one new receipt, from that
    release or a later one in the interval (ADR-0069).
    """
    policy = _POLICIES[host]
    compatibility = policy["compatibility"]
    current = _version_tuple(version)
    minimum = _version_tuple(compatibility["minimum"])
    maximum = _version_tuple(compatibility["maximum_exclusive"])
    if not minimum <= current < maximum:
        raise ValueError(
            f"host version {version} is outside compatible range "
            f"[{compatibility['minimum']}, {compatibility['maximum_exclusive']})")
    if current < _version_tuple(policy["version"]):
        raise ValueError(
            f"{host} {version} is below the qualified version {policy['version']}, so a native receipt "
            f"from {host} >= {policy['version']} is required")


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(safe_read(path)).hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = safe_read(path).decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(f"non-UTF-8 trace: {path}") from exc
    rows = []
    for number, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"invalid JSONL at {path}:{number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"non-object JSONL at {path}:{number}")
        rows.append(row)
    if not rows:
        raise ValueError(f"empty trace: {path}")
    return rows


def text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") for item in content
            if isinstance(item, dict) and item.get("type") == "text"
            and isinstance(item.get("text"), str))
    return ""


def verdict(text: str) -> dict[str, Any]:
    matches = _VERDICT.findall(text)
    if len(matches) != 1:
        raise ValueError("expected exactly one fenced empirica verdict")
    value = json.loads(matches[0])
    if not isinstance(value, dict) or value.get("verdict") not in {"pass", "fail"}:
        raise ValueError("invalid empirica verdict")
    return value


def final_assistant(rows: list[dict[str, Any]], host: str) -> tuple[dict, dict, str]:
    found = []
    for row in rows:
        message = row.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        model = message.get("model")
        if not isinstance(model, str) or not model:
            continue
        text = text_content(message.get("content"))
        if text:
            found.append((row, message, text))
    if not found:
        raise ValueError(f"{host}: no native assistant message")
    return found[-1]


def converged_result(value: Any) -> bool:
    return (isinstance(value, dict) and value.get("type") == "Allow"
            and value.get("converged") is True
            and isinstance(value.get("run"), dict)
            and value["run"].get("status") == "converged")


_LIVE_CHILD_STATES = frozenset({"reserved", "launching", "pending"})
_CHILD_STATES = ("reserved", "launching", "pending", "completed", "launch_rejected", "failed",
                 "cancelled", "timed_out", "orphaned")
_STORAGE_ID = re.compile(r"s256-[0-9a-f]{64}")
_GENERATION_DIR = re.compile(r"gen-([1-9][0-9]*)")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def run_handle(state_path: Path) -> str:
    """The public ``er2:`` run handle of the durable run stored at ``state_path``.

    The host stores one run generation at ``<home>/projects/<p>/runs/<s>/gen-<g>/run.json`` and
    its public handle is ``er2:<b64url(payload)>:<b64url(sha256(payload))>`` where ``payload`` is
    the compact, key-sorted JSON ``{"g": <g>, "p": "<p>", "s": "<s>"}``. The handle is therefore
    derived from the retained state path exactly as the host derives it; a path outside that
    layout (or with non-canonical ids) is not a durable host run and is rejected.
    """
    parts = state_path.parts
    if len(parts) < 6 or (parts[-6], parts[-4], parts[-1]) != ("projects", "runs", "run.json"):
        raise ValueError("durable state is not stored at projects/<p>/runs/<s>/gen-<g>/run.json")
    project, session, generation = parts[-5], parts[-3], _GENERATION_DIR.fullmatch(parts[-2])
    if (_STORAGE_ID.fullmatch(project) is None or _STORAGE_ID.fullmatch(session) is None
            or generation is None):
        raise ValueError("durable state path does not carry canonical run identifiers")
    payload = json.dumps({"g": int(generation.group(1)), "p": project, "s": session},
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "er2:" + _b64url(payload) + ":" + _b64url(hashlib.sha256(payload).digest())


_RETRIEVAL_KINDS = ("local", "web", "workspace")
_EVIDENCE_KINDS = ("evidence_presentation", "evidence_confirmation")


def audit_retrieval(state: dict) -> list[str]:
    """The retrieval kinds the run recorded for its auditor (``invocation.audit_retrieval``), checked.

    A run that recorded nothing, or something that is not a sorted set of known kinds, has no honest
    answer to the question the receipt exists to settle: whether the auditor could retrieve what it
    was asked to verify."""
    invocation = state.get("invocation")
    recorded = invocation.get("audit_retrieval") if isinstance(invocation, dict) else None
    if (not isinstance(recorded, list) or any(kind not in _RETRIEVAL_KINDS for kind in recorded)
            or recorded != sorted(set(recorded))):
        raise ValueError("durable state does not record a valid invocation.audit_retrieval")
    return list(recorded)




def _git_dir(common: Path | None, *args: str) -> str:
    done = subprocess.run(["git", "--git-dir", str(common), *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise ValueError(f"git {args[0]} failed: {done.stderr.strip()}")
    return done.stdout


def _common_dir(repo: object) -> Path | None:
    """The canonical Git common directory of ``repo`` (a worktree of a repository shares it), or ``None``."""
    try:
        done = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                              capture_output=True, text=True, check=False)
    except (OSError, ValueError):
        return None
    return Path(done.stdout.strip()).resolve() if done.returncode == 0 and done.stdout.strip() else None


def bind_to_recorded_repository(repo: Path, state_path: Path) -> None:
    """The artifact store must be the repository the run itself recorded (``projects/<project>/project.json``,
    written when the run began): the same Git common directory, so a worktree alias is legitimate. A run that
    recorded none, or an unreadable one, binds nothing and is refused."""
    refusal = ValueError("the artifact store is not the repository the run recorded")
    try:
        recorded = json.loads(state_path.parents[3].joinpath("project.json").read_text(encoding="utf-8")).get("repo")
    except (OSError, ValueError, IndexError, AttributeError):
        raise refusal from None
    held, wanted = _common_dir(repo), _common_dir(recorded) if isinstance(recorded, str) and recorded else None
    if held is None or wanted is None or held != wanted:
        raise refusal


def evidence_receipts(repo: Path, state_path: Path) -> list[dict[str, Any]]:
    """Every human presentation and confirmation the run's artifact store holds, projected and sorted.

    Read from the run's own ref ``refs/empirica/artifacts/<gen>/<project>/<run>`` (named by the retained
    state path) with Git plumbing only, so a receipt cannot claim a human answer the store does not hold
    and cannot leave one out. A converged run always holds artifacts (its research), so an absent ref means
    the wrong repository or a drifted ref name, and fails closed instead of reading as "no human was asked"."""
    parts = state_path.parts
    generation = _GENERATION_DIR.fullmatch(parts[-2])
    if generation is None or len(parts) < 6:
        raise ValueError("durable state path does not carry canonical run identifiers")
    bind_to_recorded_repository(repo, state_path)
    # Always the canonical Git common directory and the full tree, whatever directory the caller named: a
    # subdirectory of the repository is the same repository, and must not list a different (empty) tree.
    common = _common_dir(repo)
    git_dir = ["--git-dir", str(common)]
    ref = f"refs/empirica/artifacts/{int(generation.group(1))}/{ref_component(parts[-5])}/{ref_component(parts[-3])}"
    if subprocess.run(["git", *git_dir, "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                      capture_output=True, check=False).returncode != 0:
        raise ValueError("the artifact store holds no ref for this run, so no receipt can be checked against it")
    listing = subprocess.run(["git", *git_dir, "ls-tree", "--full-tree", ref], capture_output=True, text=True, check=False)
    if listing.returncode != 0:
        raise ValueError(f"git ls-tree failed: {listing.stderr.strip()}")
    entries = listing.stdout.splitlines()
    # The run's retained state names the manifest it committed last. The history is the chain from that head through its
    # ``parent`` links (the owner's own traversal): a ref rolled back below the head, or a store missing any artifact the
    # chain references, is not the run's history. Artifacts the chain does not reference are not history and are ignored.
    try:
        head = json.loads(safe_read(state_path)).get("committed_artifact_head_id")
    except (OSError, ValueError, AttributeError):
        head = None
    stored = []
    for line in entries:
        meta, _, path = line.partition("\t")
        fields = meta.split()
        if len(fields) != 3 or fields[1] != "blob":
            raise ValueError("artifact store holds a non-artifact entry")
        record = json.loads(_git_dir(common, "cat-file", "blob", fields[2]))
        if (not isinstance(record, dict) or not isinstance(record.get("id"), str) or not isinstance(record.get("body"), str)
                or tree_path(record["id"]) != path):
            raise ValueError("artifact store holds a malformed artifact")
        stored.append(Artifact(record["id"], record["body"]))
    by_id = index_artifacts(stored)
    try:
        history = committed_artifacts(manifest_chain(head, by_id), by_id)
    except HistoryCorrupt as error:
        raise ValueError(f"the artifact store does not hold the run's committed history: {error}") from None
    rows = []
    for body in history:
        if body.get("kind") not in _EVIDENCE_KINDS:
            continue
        if body["kind"] == "evidence_presentation":
            rows.append({"kind": body["kind"], "receipt_id": body["receipt_id"],
                         "artifact_id": body["artifact_id"], "record_ids": sorted(body["record_ids"]),
                         "claim_ids": sorted(c["claim_id"] for c in body["claims"])})
        else:
            rows.append({"kind": body["kind"], "receipt_id": body["receipt_id"],
                         "artifact_id": body["artifact_id"], "subject": body["subject"],
                         "outcome": body["outcome"],
                         "target": body["record_id"] if body["subject"] == "research" else body["claim_id"]})
    return sorted(rows, key=lambda row: (row["kind"], row["receipt_id"], row["artifact_id"]))


def require_answered_presentations(rows: list[dict[str, Any]]) -> None:
    """A confirmation must answer a presentation the same store holds, and a receipt id names one."""
    shown = [row["receipt_id"] for row in rows if row["kind"] == "evidence_presentation"]
    if len(shown) != len(set(shown)):
        raise ValueError("a presentation receipt id is recorded twice")
    for row in rows:
        if row["kind"] == "evidence_confirmation" and row["receipt_id"] not in shown:
            raise ValueError("a human confirmation names no recorded presentation")


def state_facts(path: Path, expected_role: str) -> tuple[dict, dict]:
    state = json.loads(safe_read(path))
    if not isinstance(state, dict) or state.get("status") != "converged":
        raise ValueError("durable state is not converged")
    children = [row for row in state.get("children", [])
                if isinstance(row, dict) and row.get("purpose") == "audit"]
    if not children:
        raise ValueError("durable state contains no audit child")
    # A failed audit may be retried. The receipt binds the latest reservation, the audit that
    # the convergence relied on; every earlier audit must already be settled.
    if any(row.get("state") in _LIVE_CHILD_STATES for row in children):
        raise ValueError("durable state retains an unsettled audit child")
    child = children[-1]
    if child.get("state") != "completed" or child.get("audit_role_profile") != expected_role:
        raise ValueError("durable audit child is not completed with the canonical role")
    for key in ("child_id", "native_id", "audit_operation_id"):
        if not isinstance(child.get(key), str) or not child[key]:
            raise ValueError(f"durable audit child lacks {key}")
    return state, child


# The receipt tool reads the Claude author plain-text view from the native transcript. It is
# deliberately independent of plugin code: these recognizers mirror the first line and the
# ``Title:`` sections of ``plugins/empirica/adapters/author_view.py``, with every heading and line
# label read from the contract projection, and are pinned to the renderer's real output by
# ``scripts/tests/test_empirica_live_receipts.py``.
_AUTHOR_HEADER = re.compile(
    rf"(Allow \({re.escape(_LABELS['converged'])}(true|false)\)|Block) ([a-z_]+) — [^\n]*")
_REASON_ENTRY = re.compile(r"  ([A-Za-z0-9_.]+)(?:: | — |$)")
_AUDIT_PENDING = "audit.pending"
_PENDING_AUDIT_CHILD = "  audit: pending"
_COMPLETED_AUDIT_CHILD = "  audit: completed"


def author_view_header(text: str) -> tuple[str, str, bool | None] | None:
    """Parse the first line of a run-bearing author view, or return ``None``.

    The renderer emits ``<Allow (converged=true|false)|Block> <status> — <governance summary>``.
    Returns ``(result_type, status, converged)`` with ``result_type`` ``"Allow"`` or ``"Block"``
    and ``converged`` ``True``/``False`` for Allow and ``None`` for Block. Fault, Inert, hook
    acknowledgements such as ``{"continue": true}``, and the retired JSON shapes are not run views.
    """
    match = _AUTHOR_HEADER.fullmatch(text.split("\n", 1)[0])
    if match is None:
        return None
    result_type = match.group(1).split(" ", 1)[0]
    converged = {"true": True, "false": False}.get(match.group(2))
    return result_type, match.group(3), converged


def author_view_section(text: str, title: str) -> list[str]:
    """Return the indented lines of the author-view section ``title`` (for example ``Reasons:``).

    Sections are blank-line separated blocks whose first line is exactly ``title`` (a Stop hook's
    single trailing newline is ignored); an absent or duplicated section yields ``[]``. Reason and obligation entries are indented two spaces and
    their ``params:``/``next:`` continuations four.
    """
    blocks = [block.split("\n") for block in text.rstrip("\n").split("\n\n")
              if block.split("\n", 1)[0] == title]
    return blocks[0][1:] if len(blocks) == 1 else []


def handback_report(content: str) -> tuple[str, str] | None:
    """Parse a Claude Code subagent hand-back frame into ``(native_id, report)``, or ``None``.

    Claude Code delivers a background subagent's final report as
    ``<agent-message from="<id>">`` + a ``[Subagent hand-back] … The report follows:`` preamble +
    the report with every line indented two spaces + ``</agent-message>``. The indentation is the
    harness's forgery guard, so the whole body must be indented; it is removed exactly once.
    """
    match = _HANDBACK_FRAME.fullmatch(content)
    if match is None:
        return None
    return match.group(1), "\n".join(line[2:] for line in match.group(2).split("\n"))


def _entries(lines: list[str]) -> list[str]:
    """Keep the two-space entry lines of a section, dropping four-space continuations."""
    return [line for line in lines if line.startswith("  ") and not line.startswith("   ")]


_CHILD_LINE = re.compile(r"  (.*): (" + "|".join(_CHILD_STATES) + r")(?: — "
                         + re.escape(_LABELS["recovery"]) + r" [^\n]*)?")
_RUN_ID_LINE = re.compile(re.escape(_LABELS["run_id"]) + r" (\S+)")


def author_view_run_id(text: str) -> str | None:
    """The run handle of an author view, or ``None`` when it is missing, repeated, or misplaced.

    The renderer prints exactly one ``<run_id label> <handle>`` line, directly after the header.
    Any other line that starts with the run-id label, or a run id that is not the second line,
    is ambiguous and yields ``None``.
    """
    lines = text.split("\n")
    marked = [line for line in lines if line.startswith(_LABELS["run_id"])]
    if len(lines) < 2 or len(marked) != 1 or marked[0] != lines[1]:
        return None
    match = _RUN_ID_LINE.fullmatch(lines[1])
    return match.group(1) if match else None


def pending_audit_settlement(text: str, run: str) -> bool:
    """Whether ``text`` is the Stop-hook author view of run ``run`` blocked on one pending audit.

    Requires the view's ``run_id`` to equal ``run`` (the handle of the durable run being
    receipted, see ``run_handle``), header ``Block`` with status ``active``, a ``Reasons:``
    section whose sole entry is ``audit.pending``, and a ``Children:`` section with exactly one
    live child, ``audit: pending``. Settled children (``completed``, ``failed``, ``cancelled``,
    …, with the renderer's `` — recovery: …`` suffix) are earlier audits of a retry and are
    allowed; each audit of a retry has its own settlement, and the caller takes the one after the
    bound launch. The text view carries no child id: the pending child is bound to the durable child
    by the run binding above, the Agent-launch acknowledgement (``agentId`` equals the state's
    ``native_id``), and the ordering launch < launch result < settlement < notification < report,
    the last two enforced by the caller.
    """
    header = author_view_header(text)
    if author_view_run_id(text) != run or header is None or header[:2] != ("Block", "active"):
        return False
    reasons = _entries(author_view_section(text, _LABELS["reasons"]))
    codes = [match.group(1) for line in reasons if (match := _REASON_ENTRY.match(line))]
    children = author_view_section(text, _LABELS["children"])
    parsed = [_CHILD_LINE.fullmatch(line) for line in children]
    live = [match.group(0) for match in parsed if match and match.group(2) in _LIVE_CHILD_STATES]
    return (len(reasons) == 1 and codes == [_AUDIT_PENDING]
            and bool(children) and None not in parsed and live == [_PENDING_AUDIT_CHILD])


def converged_report_view(text: str, run: str) -> bool:
    """Whether ``text`` is the author view of run ``run`` as ``Allow (converged=true)``, converged.

    The view's ``run_id`` must equal ``run`` and its ``Children:`` section must show a completed
    audit child.
    """
    return (author_view_run_id(text) == run
            and author_view_header(text) == ("Allow", "converged", True)
            and _COMPLETED_AUDIT_CHILD in author_view_section(text, _LABELS["children"]))


def claude_report_result(child: dict) -> dict:
    """The structured receipt ``result`` for a Claude converged report.

    Claude's tool result is plain text with no structured payload, so the result is projected from
    the recognized view (``Allow``/converged/``converged``) and the durable completed audit child.
    """
    return {"type": "Allow", "converged": True, "run": {"status": "converged", "children": [{
        "child_id": child["child_id"], "purpose": "audit", "resource_class": "audit",
        "state": "completed"}]}}


def _tool_result_text(item: dict) -> str:
    return text_content(item.get("content"))


def _claude_handbacks(rows: list[dict]) -> list[tuple[int, str]]:
    found = []
    for index, row in enumerate(rows):
        message = row.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        for item in content if isinstance(content, list) else []:
            if (not isinstance(item, dict) or item.get("type") != "tool_use"
                    or item.get("name") != "SubagentHandback"):
                continue
            tool_input = item.get("input")
            candidate = tool_input.get("message") if isinstance(tool_input, dict) else None
            found.append((index, candidate if isinstance(candidate, str) else ""))
    return found


def require_row_versions(rows: list[dict], indexes: list[int], version: str, label: str) -> None:
    """Require the native ``version`` of each listed transcript record to equal ``version``.

    Only the records that support the accepted lifecycle are checked, so unrelated historical
    rows written by an older host (a session resumed after an upgrade) do not matter. Claude
    Code stamps its own version on every ``user``/``assistant``/``attachment`` record; a missing
    or different value is rejected. ``queue-operation`` bookkeeping records carry no version, so
    one without the field is skipped, while one that carries a value must match.
    """
    for index in sorted(set(indexes)):
        row = rows[index]
        if row.get("type") == "queue-operation" and "version" not in row:
            continue
        if row.get("version") != version:
            raise ValueError(
                f"claude {label} record {index} native version {row.get('version')!r} "
                f"does not match attested host version {version}")


def inspect_claude(parent: list[dict], child_rows: list[dict], child: dict, run: str,
                   host_version: str) -> dict:
    """Project a Claude installed-host receipt from the native transcripts.

    ``run`` is the ``run_handle`` of the durable state being receipted and ``host_version`` the
    attested ``claude --version``; the records that support the lifecycle must carry both.
    """
    launches = []
    for index, row in enumerate(parent):
        attachment = row.get("attachment")
        if not isinstance(attachment, dict) or attachment.get("hookName") != "PreToolUse:Agent":
            continue
        try:
            output = json.loads(attachment.get("stdout", ""))
        except ValueError:
            continue
        updated = output.get("hookSpecificOutput", {}).get("updatedInput", {})
        if (updated.get("subagent_type") == "empirica:empirica-auditor"
                and updated.get("run_in_background") is True):
            launches.append((index, attachment.get("toolUseID"), updated))
    acknowledged = {
        item.get("tool_use_id"): _tool_result_text(item)
        for row in parent
        for item in (row.get("message", {}).get("content") or []
                     if isinstance(row.get("message"), dict)
                     and isinstance(row["message"].get("content"), list) else [])
        if isinstance(item, dict) and item.get("type") == "tool_result"
    }
    bound = [launch for launch in launches
             if (match := _AGENT_ID.search(acknowledged.get(launch[1], ""))) is not None
             and match.group(1) == child["native_id"]]
    if len(bound) != 1 or bound[0] != launches[-1]:
        raise ValueError("claude: the durable audit child is not the last canonical Agent launch")
    launch_index, tool_id, _ = bound[0]
    report_uses = []
    settlements = []
    for index, row in enumerate(parent):
        attachment = row.get("attachment")
        if isinstance(attachment, dict) and attachment.get("hookName") == "Stop":
            stdout = attachment.get("stdout")
            if isinstance(stdout, str) and pending_audit_settlement(stdout, run):
                settlements.append(index)
        message = row.get("message", {})
        for item in message.get("content", []) if isinstance(message, dict) else []:
            if (isinstance(item, dict) and item.get("type") == "tool_use"
                    and isinstance(item.get("name"), str)
                    and item["name"].endswith("report_convergence")
                    and isinstance(item.get("id"), str)):
                report_uses.append((index, item["id"]))
    converged_ids = {
        item.get("tool_use_id")
        for row in parent
        for item in (row.get("message", {}).get("content") or []
                     if isinstance(row.get("message"), dict)
                     and isinstance(row["message"].get("content"), list) else [])
        if isinstance(item, dict) and item.get("type") == "tool_result"
        and converged_report_view(_tool_result_text(item), run)
    }
    report_uses = [use for use in report_uses if use[1] in converged_ids]
    # Each audit of a retry has its own Stop settlement; the receipt is bound to the settlement
    # that follows the bound (last) launch, and there must be exactly one of those.
    settlements = [index for index in settlements if index > launch_index]
    if len(report_uses) != 1 or len(settlements) != 1:
        raise ValueError("claude: expected one pending Stop settlement and converged report")
    report_use_index, report_id = report_uses[0]
    settlement_index = settlements[0]
    agent_results = []
    report_results = []
    notifications = []
    for index, row in enumerate(parent):
        if row.get("type") == "queue-operation" and row.get("operation") == "enqueue":
            content = row.get("content")
            if isinstance(content, str):
                handback = handback_report(content)
                if handback is not None and handback[0] == child["native_id"]:
                    notifications.append((index, handback[1]))
        message = row.get("message", {})
        content = message.get("content") if isinstance(message, dict) else None
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict) or item.get("type") != "tool_result":
                continue
            item_text = _tool_result_text(item)
            if item.get("tool_use_id") == tool_id:
                agent_results.append((index, item_text))
            if item.get("tool_use_id") != report_id:
                continue
            if converged_report_view(item_text, run):
                report_results.append((index, claude_report_result(child)))
    if len(agent_results) != 1 or len(notifications) != 1 or len(report_results) != 1:
        raise ValueError("claude: missing unique launch acknowledgement/notification/report result")
    launch_result_index, launch_text = agent_results[0]
    notification_index, notification_text = notifications[0]
    report_result_index, report_result = report_results[0]
    match = _AGENT_ID.search(launch_text)
    if (match is None or match.group(1) != child["native_id"]
            or "Async agent launched successfully" not in launch_text
            or _VERDICT.search(launch_text)):
        raise ValueError("claude: launch acknowledgement does not bind one async native id")
    handbacks = _claude_handbacks(child_rows)
    if len(handbacks) != 1:
        raise ValueError("claude: child transcript lacks one authoritative SubagentHandback")
    handback_index, child_handback = handbacks[0]
    # The auditor's identity is the message that emitted the handback: a real Claude child ends
    # with that tool call and no trailing text message.
    child_row = child_rows[handback_index]
    child_message = child_row["message"]
    if not isinstance(child_message.get("model"), str) or not child_message["model"]:
        raise ValueError("claude: child handback message lacks native model identity")
    child_verdict = verdict(child_handback)
    if child_row.get("agentId") != child["native_id"]:
        raise ValueError("claude: child transcript/native id mismatch")
    if verdict(notification_text) != child_verdict:
        raise ValueError("claude: completion handback/child verdict mismatch")
    if not (launch_index < launch_result_index < settlement_index < notification_index
            < report_use_index <= report_result_index):
        raise ValueError("claude: async launch/notification/convergence order is invalid")
    require_row_versions(parent, [launch_index, launch_result_index, settlement_index,
                                  notification_index, report_use_index, report_result_index],
                         host_version, "parent")
    require_row_versions(child_rows, [handback_index], host_version, "child")
    author_parent = [row for row in parent if row.get("isSidechain") is not True]
    _, author_message, _ = final_assistant(author_parent, "claude")
    return {
        "result": report_result,
        "author": {"provider_id": "anthropic", "model_id": author_message["model"]},
        "auditor": {"provider_id": "anthropic", "model_id": child_message["model"]},
        "verdict": child_verdict,
    }
_SUBAGENTS_RECORDED = ("package", "version", "reviewed", "owner_path", "package_root", "preflight_path", "source")
_SUBAGENTS_RECEIPT = ("package", "version", "reviewed", "owner_path", "package_root", "preflight_path")
_SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def _version_in_range(version: Any, minimum: str, maximum_exclusive: str) -> bool:
    """Whether ``version`` is an exact semver in ``[minimum, maximum_exclusive)`` (numeric comparison)."""
    if not isinstance(version, str) or _SEMVER.fullmatch(version) is None:
        return False
    parts = tuple(int(part) for part in version.split("."))
    return tuple(int(p) for p in minimum.split(".")) <= parts < tuple(int(p) for p in maximum_exclusive.split("."))


def _inside(path: Any, root: Any) -> bool:
    """Whether ``path`` is strictly below the absolute directory ``root`` (lexical; no filesystem)."""
    if not isinstance(path, str) or not isinstance(root, str):
        return False
    candidate, base = PurePosixPath(path), PurePosixPath(root)
    return (candidate.is_absolute() and base.is_absolute() and candidate != base
            and ".." not in candidate.parts and base in candidate.parents)


def host_runtime_rejection(policy: dict | None, host_runtime: Any) -> str | None:
    """A stable code when the run's recorded ``host_runtime`` does not satisfy the profile policy.

    The receipt tool is independent of plugin code, so this is its own copy of the rule the host
    applies when the run starts (``application/host_runtime.py``); the shared cases in
    ``plugins/empirica/tests/fixtures/host-runtime-cases.json`` pin the copies together.
    """
    if policy is None:
        return None if host_runtime is None else "unexpected"
    if host_runtime is None:
        return "missing"
    if not isinstance(host_runtime, dict) or not isinstance(host_runtime.get("subagents"), dict):
        return "malformed"
    subagents = host_runtime["subagents"]
    optional = {"reviewed"}  # an absent value reads as not reviewed
    if set(host_runtime) != {"policy_id", "subagents"} \
            or not set(_SUBAGENTS_RECORDED) - optional <= set(subagents) <= set(_SUBAGENTS_RECORDED):
        return "malformed"
    if host_runtime["policy_id"] != policy["policy_id"]:
        return "policy"
    if subagents["package"] != policy["package"]:
        return "package"
    if not _version_in_range(subagents["version"], policy["minimum"], policy["maximum_exclusive"]):
        return "version"
    if "reviewed" in subagents and not isinstance(subagents["reviewed"], bool):  # absent reads as not reviewed
        return "malformed"
    if not (_inside(subagents["owner_path"], subagents["package_root"])
            and _inside(subagents["preflight_path"], subagents["package_root"])):
        return "paths"
    return None


def subagents_runtime(state: dict, host: str) -> dict | None:
    """The receipt's ``subagents_runtime``: derived from the persisted run state, never from an argument.

    ``None`` for a host whose profile has no external audit runtime (Claude); for Pi it is the exact
    package, version, owner file, package root and preflight module the host recorded when the run
    started, plus the policy id and whether the version had a compat record, after they are checked against the
    profile's version range (ADR-0069).
    """
    policy = _POLICIES[host].get("subagents_compatibility")
    invocation = state.get("invocation")
    recorded = invocation.get("host_runtime") if isinstance(invocation, dict) else None
    rejection = host_runtime_rejection(policy, recorded)
    if rejection is not None:
        raise ValueError(f"durable state does not carry an acceptable pi-subagents runtime ({rejection})")
    if policy is None:
        return None
    subagents = recorded["subagents"]
    return {**{key: subagents.get(key, False if key == "reviewed" else None) for key in _SUBAGENTS_RECEIPT},
            "policy_id": recorded["policy_id"]}


def inspect_pi(parent: list[dict], child_rows: list[dict], child: dict,
               child_path: Path, state: dict) -> dict:
    """Project a Pi installed-host receipt from the native session files and the durable run state.

    Pi session records carry no host version: the only ``version`` field is the session header's
    file-format number (``3``), which is unrelated to the ``pi --version`` string, so there is
    nothing to cross-check against the attested host version.
    """
    calls: list[tuple[int, dict, dict]] = []
    results: list[tuple[int, dict]] = []
    for index, row in enumerate(parent):
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            content = message.get("content")
            for item in content if isinstance(content, list) else []:
                if isinstance(item, dict) and item.get("type") == "toolCall":
                    calls.append((index, message, item))
        elif message.get("role") == "toolResult":
            results.append((index, message))

    launch_calls = []
    report_calls = []
    for index, message, item in calls:
        arguments = item.get("arguments")
        if (item.get("name") == "subagent" and isinstance(arguments, dict)
                and arguments.get("agent") == "empirica.empirica-auditor"
                and set(arguments) == {"agent", "task"}):
            launch_calls.append((index, message, item))
        if item.get("name") == "report_convergence":
            report_calls.append((index, message, item))
    bound = [call for call in launch_calls if call[2].get("id") == child["native_id"]]
    if len(bound) != 1 or bound[0] is not launch_calls[-1]:
        raise ValueError("pi: the durable audit child is not the last canonical child call")
    converged_ids = {message.get("toolCallId") for _, message in results
                     if message.get("toolName") == "report_convergence"
                     and converged_result(message.get("details"))}
    report_calls = [call for call in report_calls if call[2].get("id") in converged_ids]
    if len(report_calls) != 1:
        raise ValueError("pi: expected one converged convergence call")
    launch_index, author_message, launch_call = bound[0]
    report_index, _, report_call = report_calls[0]
    launch_id = launch_call.get("id")
    report_id = report_call.get("id")
    launch_results = [(index, message) for index, message in results
                      if message.get("toolName") == "subagent"
                      and message.get("toolCallId") == launch_id]
    report_results = [(index, message) for index, message in results
                      if message.get("toolName") == "report_convergence"
                      and message.get("toolCallId") == report_id]
    if len(launch_results) != 1 or len(report_results) != 1:
        raise ValueError("pi: native calls lack unique paired results")
    launch_result_index, launch_result = launch_results[0]
    report_result_index, report_result_message = report_results[0]
    details = launch_result.get("details", {})
    child_results = details.get("results", []) if isinstance(details, dict) else []
    if (len(child_results) != 1 or not isinstance(child_results[0], dict)
            or child_results[0].get("sessionFile") != str(child_path)
            or launch_id != child["native_id"]):
        raise ValueError("pi: child result does not bind session/native id")
    if "```empirica-verdict" in json.dumps(launch_result):
        raise ValueError("pi: author-visible child result retains a verdict")
    report_result = report_result_message.get("details")
    if not converged_result(report_result):
        raise ValueError("pi: report result is not converged")

    audit_events = [(index, row.get("data")) for index, row in enumerate(parent)
                    if row.get("type") == "custom" and row.get("customType") == "empirica.audit"
                    and isinstance(row.get("data"), dict)
                    and row["data"].get("toolCallId") == launch_id]
    done_events = [(index, row.get("data")) for index, row in enumerate(parent)
                   if row.get("type") == "custom" and row.get("customType") == "empirica.audit.done"
                   and isinstance(row.get("data"), dict)
                   and row["data"].get("toolCallId") == launch_id]
    if len(audit_events) != 1 or len(done_events) != 1:
        raise ValueError("pi: missing unique host audit binding events")
    audit_index, audit_data = audit_events[0]
    done_index, _ = done_events[0]
    plan = audit_data.get("plan", {})
    if (not isinstance(plan, dict) or audit_data.get("nativeId") != child["native_id"]
            or plan.get("child_id") != child["child_id"]
            or plan.get("operation_id") != child["audit_operation_id"]):
        raise ValueError("pi: host audit binding does not match durable child")
    if not (launch_index < audit_index < done_index < launch_result_index
            < report_index <= report_result_index):
        raise ValueError("pi: child/report ordering is invalid")

    _, child_message, child_text = final_assistant(child_rows, "pi")
    child_verdict = verdict(child_text)
    provider = child_message.get("provider")
    if not isinstance(provider, str) or not provider:
        raise ValueError("pi: child transcript lacks provider")
    if not author_message.get("provider") or not author_message.get("model"):
        raise ValueError("pi: launch message lacks native author identity")
    return {
        "result": report_result,
        "author": {"provider_id": author_message["provider"], "model_id": author_message["model"]},
        "auditor": {"provider_id": provider, "model_id": child_message["model"]},
        "verdict": child_verdict,
        "subagents_runtime": subagents_runtime(state, "pi"),
    }


def inspect(receipt: dict, host: str, expected_commit: str,
            expected_plugin_version: str) -> list[str]:
    errors = []
    try:
        profile, role = EXPECTED[host]
        if receipt.get("format") != FORMAT or receipt.get("operator_attested") is not True:
            raise ValueError("operator-attested v3 receipt is required")
        if receipt.get("host") != host or receipt.get("profile_id") != profile:
            raise ValueError("host/capability profile mismatch")
        host_version = receipt.get("host_version")
        if not isinstance(host_version, str):
            raise ValueError("exact observed host version is required")
        require_compatible_version(host, host_version)
        if receipt.get("plugin_version") != expected_plugin_version:
            raise ValueError("release plugin version mismatch")
        if receipt.get("release_commit") != expected_commit:
            raise ValueError("release commit mismatch")
        if not isinstance(receipt.get("command"), str) or not receipt["command"].strip():
            raise ValueError("exact invocation command is required")
        paths = {name: Path(receipt[f"{name}_path"])
                 for name in ("transcript", "run_state", "child_session", "version_output")}
        for name, path in paths.items():
            if digest(path) != receipt.get(f"{name}_sha256"):
                raise ValueError(f"{name} digest mismatch")
        if native_version(host, paths["version_output"]) != host_version:
            raise ValueError("native version output mismatch")
        state, child = state_facts(paths["run_state"], role)
        parent = jsonl(paths["transcript"])
        child_rows = jsonl(paths["child_session"])
        facts = (inspect_claude(parent, child_rows, child, run_handle(paths["run_state"]),
                                host_version) if host == "claude"
                 else inspect_pi(parent, child_rows, child, paths["child_session"], state))
        if receipt.get("audit_retrieval") != audit_retrieval(state):
            raise ValueError("receipt audit_retrieval does not equal the retrieval recorded in the durable state")
        repo = receipt.get("artifacts_repo_path")
        if not isinstance(repo, str) or not repo:
            raise ValueError("the artifact store path is required to check the human receipts")
        held = evidence_receipts(Path(repo), paths["run_state"])
        require_answered_presentations(held)
        if receipt.get("evidence_receipts") != held:
            raise ValueError("receipt evidence_receipts do not equal the presentations and confirmations "
                             "the artifact store holds for this run")
        for key, value in (("audit_child_id", child["child_id"]),
                           ("audit_native_id", child["native_id"]),
                           ("audit_operation_id", child["audit_operation_id"])):
            if receipt.get(key) != value:
                raise ValueError(f"{key} mismatch")
        if receipt.get("subagents_runtime") != facts.get("subagents_runtime"):
            raise ValueError(
                "receipt subagents_runtime is missing" if facts.get("subagents_runtime") is not None
                and receipt.get("subagents_runtime") is None else
                "receipt subagents_runtime does not equal the runtime recorded in the durable state")
        if receipt.get("result") != facts["result"]:
            raise ValueError("reported result does not equal native report result")
        if receipt.get("observed_author") != facts["author"]:
            raise ValueError("author identity mismatch")
        if receipt.get("observed_auditor") != facts["auditor"]:
            raise ValueError("auditor identity mismatch")
        if facts["verdict"].get("verdict") != "pass":
            raise ValueError("native auditor verdict is not pass")
        if facts["author"] == facts["auditor"]:
            raise ValueError("author and auditor native identities are equal")
        result_children = facts["result"]["run"].get("children", [])
        completed_child = any(
            isinstance(row, dict)
            and row.get("child_id") == child["child_id"]
            and row.get("purpose") == "audit"
            and row.get("resource_class") == "audit"
            and row.get("state") == "completed"
            for row in result_children
        )
        if not completed_child:
            raise ValueError("native report does not include the completed bound child")
        if state.get("status") != facts["result"]["run"].get("status"):
            raise ValueError("durable/native terminal status mismatch")
    except (KeyError, OSError, TypeError, ValueError) as exc:
        errors.append(f"{host}: {exc}")
    return errors
