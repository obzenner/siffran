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
_AGENT_MESSAGE = re.compile(r'^<agent-message from="([A-Za-z0-9_-]+)">\n')


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
        if row.get("type") == "queue-operation" and row.get("operation") == "enqueue":
            content = row.get("content")
            if isinstance(content, str):
                match = _AGENT_MESSAGE.match(content)
                if (match is not None and match.group(1) == child["native_id"]
                        and content.rstrip().endswith("</agent-message>")
                        and "[Subagent hand-back]" in content):
                    notifications.append((index, content))
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
    child_row, child_message, _ = final_assistant(child_rows, "claude")
    handbacks = _claude_handbacks(child_rows)
    if len(handbacks) != 1:
        raise ValueError("claude: child transcript lacks one authoritative SubagentHandback")
    _, child_handback = handbacks[0]
    child_verdict = verdict(child_handback)
    if child_row.get("agentId") != child["native_id"]:
        raise ValueError("claude: child transcript/native id mismatch")
    if verdict(notification_text) != child_verdict:
        raise ValueError("claude: completion handback/child verdict mismatch")
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
    if len(launch_calls) != 1 or len(report_calls) != 1:
        raise ValueError("pi: expected one canonical child call and one convergence call")
    launch_index, author_message, launch_call = launch_calls[0]
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
        completed_child = any(
            isinstance(row, dict)
            and row.get("child_id") == child["child_id"]
            and row.get("purpose") == "audit"
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
