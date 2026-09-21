"""Structural verification for operator-captured installed-host Empirica receipts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

FORMAT = "empirica-live-receipt/v2"
EXPECTED = {
    "claude": ("claude-code@2.1.278", "2.1.278", "empirica:empirica-auditor"),
    "pi": ("pi@0.84.1+pi-subagents@0.50.0", "0.84.1", "empirica.empirica-auditor"),
}
MAX_TRACE_BYTES = 128 << 20
_VERDICT = re.compile(r"```empirica-verdict\s*\n(\{.*?\})\s*\n```", re.DOTALL)
_AGENT_ID = re.compile(r"agentId:\s*([A-Za-z0-9_-]+)")
_TASK_ID = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")


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


def state_facts(path: Path, expected_role: str) -> tuple[dict, dict]:
    state = json.loads(safe_read(path))
    if not isinstance(state, dict) or state.get("status") != "converged":
        raise ValueError("durable state is not converged")
    children = [row for row in state.get("children", [])
                if isinstance(row, dict) and row.get("purpose") == "audit"]
    if len(children) != 1:
        raise ValueError("durable state must contain exactly one audit child")
    child = children[0]
    if child.get("state") != "completed" or child.get("audit_role_profile") != expected_role:
        raise ValueError("durable audit child is not completed with the canonical role")
    for key in ("child_id", "native_id", "audit_operation_id"):
        if not isinstance(child.get(key), str) or not child[key]:
            raise ValueError(f"durable audit child lacks {key}")
    return state, child


def _tool_result_text(item: dict) -> str:
    return text_content(item.get("content"))


def inspect_claude(parent: list[dict], child_rows: list[dict], child: dict) -> dict:
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
    if len(launches) != 1:
        raise ValueError("claude: expected one bound background canonical Agent launch")
    launch_index, tool_id, _ = launches[0]
    report_uses = []
    settlements = []
    for index, row in enumerate(parent):
        attachment = row.get("attachment")
        if isinstance(attachment, dict) and attachment.get("hookName") == "Stop":
            try:
                stopped = json.loads(attachment.get("stdout", ""))
            except ValueError:
                stopped = {}
            reasons = stopped.get("reasons", []) if isinstance(stopped, dict) else []
            stopped_run = stopped.get("run", {}) if isinstance(stopped, dict) else {}
            stopped_children = stopped_run.get("children", []) if isinstance(stopped_run, dict) else []
            pending = [item for item in stopped_children if isinstance(item, dict)
                       and item.get("resource_class") == "audit" and item.get("state") == "pending"]
            if (stopped.get("type") == "Block" and stopped_run.get("status") == "active"
                    and len(pending) == 1 and pending[0].get("child_id") == child["child_id"]
                    and [reason.get("code") for reason in reasons
                         if isinstance(reason, dict)] == ["audit.pending"]):
                settlements.append(index)
        message = row.get("message", {})
        for item in message.get("content", []) if isinstance(message, dict) else []:
            if (isinstance(item, dict) and item.get("type") == "tool_use"
                    and isinstance(item.get("name"), str)
                    and item["name"].endswith("report_convergence")
                    and isinstance(item.get("id"), str)):
                report_uses.append((index, item["id"]))
    if len(report_uses) != 1 or len(settlements) != 1:
        raise ValueError("claude: expected one pending Stop settlement and convergence tool use")
    report_use_index, report_id = report_uses[0]
    settlement_index = settlements[0]
    agent_results = []
    report_results = []
    notifications = []
    for index, row in enumerate(parent):
        message = row.get("message", {})
        content = message.get("content") if isinstance(message, dict) else None
        text = text_content(content)
        if (message.get("role") == "user" and "<task-notification>" in text
                and _TASK_ID.findall(text) == [child["native_id"]]):
            notifications.append((index, text))
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict) or item.get("type") != "tool_result":
                continue
            item_text = _tool_result_text(item)
            if item.get("tool_use_id") == tool_id:
                agent_results.append((index, item_text))
            if item.get("tool_use_id") != report_id:
                continue
            try:
                value = json.loads(item_text)
            except ValueError:
                continue
            if converged_result(value):
                report_results.append((index, value))
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
    child_row, child_message, child_text = final_assistant(child_rows, "claude")
    child_verdict = verdict(child_text)
    if child_row.get("agentId") != child["native_id"] or verdict(notification_text) != child_verdict:
        raise ValueError("claude: completion notification/child verdict/native id mismatch")
    if not (launch_index < launch_result_index < settlement_index < notification_index
            < report_use_index <= report_result_index):
        raise ValueError("claude: async launch/notification/convergence order is invalid")
    author_parent = [row for row in parent if row.get("isSidechain") is not True]
    _, author_message, _ = final_assistant(author_parent, "claude")
    return {
        "result": report_result,
        "author": {"provider_id": "anthropic", "model_id": author_message["model"]},
        "auditor": {"provider_id": "anthropic", "model_id": child_message["model"]},
        "verdict": child_verdict,
    }
def inspect_pi(parent: list[dict], child_rows: list[dict], child: dict,
               child_path: Path) -> dict:
    launches = []
    reports = []
    for row in parent:
        if row.get("type") != "tool_execution_end":
            continue
        result = row.get("result", {})
        if row.get("toolName") == "subagent" and result.get("details", {}).get("results"):
            launches.append(row)
        if row.get("toolName") == "report_convergence":
            value = result.get("details")
            if converged_result(value):
                reports.append(value)
    if len(launches) != 1 or len(reports) != 1:
        raise ValueError("pi: expected one native child result and one converged report")
    launch = launches[0]
    rows = launch["result"]["details"]["results"]
    if (len(rows) != 1 or rows[0].get("sessionFile") != str(child_path)
            or launch.get("toolCallId") != child["native_id"]):
        raise ValueError("pi: child result does not bind session/native id")
    rendered = json.dumps(launch["result"])
    if "```empirica-verdict" in rendered:
        raise ValueError("pi: author-visible child result retains a verdict")
    child_row, child_message, child_text = final_assistant(child_rows, "pi")
    child_verdict = verdict(child_text)
    provider = child_message.get("provider")
    if not isinstance(provider, str) or not provider:
        raise ValueError("pi: child transcript lacks provider")
    authors = []
    for row in parent:
        if row.get("type") == "message_end" and isinstance(row.get("message"), dict):
            message = row["message"]
            if message.get("role") == "assistant" and message.get("provider") and message.get("model"):
                authors.append(message)
    if not authors:
        raise ValueError("pi: parent transcript lacks native author identity")
    return {
        "result": reports[0],
        "author": {"provider_id": authors[-1]["provider"], "model_id": authors[-1]["model"]},
        "auditor": {"provider_id": provider, "model_id": child_message["model"]},
        "verdict": child_verdict,
    }


def inspect(receipt: dict, host: str, expected_commit: str,
            expected_plugin_version: str) -> list[str]:
    errors = []
    try:
        profile, host_version, role = EXPECTED[host]
        if receipt.get("format") != FORMAT or receipt.get("operator_attested") is not True:
            raise ValueError("operator-attested v2 receipt is required")
        if receipt.get("host") != host or receipt.get("profile_id") != profile:
            raise ValueError("exact host/profile mismatch")
        if receipt.get("host_version") != host_version:
            raise ValueError("exact host version mismatch")
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
        if safe_read(paths["version_output"]).decode("utf-8").strip() != host_version:
            raise ValueError("native version output mismatch")
        state, child = state_facts(paths["run_state"], role)
        parent = jsonl(paths["transcript"])
        child_rows = jsonl(paths["child_session"])
        facts = (inspect_claude(parent, child_rows, child) if host == "claude"
                 else inspect_pi(parent, child_rows, child, paths["child_session"]))
        for key, value in (("audit_child_id", child["child_id"]),
                           ("audit_native_id", child["native_id"]),
                           ("audit_operation_id", child["audit_operation_id"])):
            if receipt.get(key) != value:
                raise ValueError(f"{key} mismatch")
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
        if {"child_id": child["child_id"], "purpose": "audit", "state": "completed"} not in result_children:
            raise ValueError("native report does not include the completed bound child")
        if state.get("status") != facts["result"]["run"].get("status"):
            raise ValueError("durable/native terminal status mismatch")
    except (KeyError, OSError, TypeError, ValueError) as exc:
        errors.append(f"{host}: {exc}")
    return errors
