#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from empirica_live_receipts import EXPECTED, FORMAT, digest, inspect  # noqa: E402

VERDICT = {"verdict": "pass", "findings": [], "argument_digest": "sha256:" + "1" * 64,
           "goal_digest": "sha256:" + "2" * 64,
           "frozen_scope_digest": None, "deferred_scope_digest": "sha256:" + "3" * 64,
           "reviewed_claims": [], "scope_review": "pass"}
BLOCK = "```empirica-verdict\n" + json.dumps(VERDICT) + "\n```"
RESULT = {"type": "Allow", "converged": True, "run": {"status": "converged",
          "children": [{"child_id": "child", "purpose": "audit", "resource_class": "audit",
                        "state": "completed"}]}}


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class LiveReceiptTests(unittest.TestCase):
    def receipt(self, root: Path, host: str) -> dict:
        profile, version, role = EXPECTED[host]
        state_path = root / "state.json"
        state_path.write_text(json.dumps({"status": "converged", "children": [{
            "child_id": "child", "native_id": "native", "purpose": "audit",
            "state": "completed", "audit_role_profile": role,
            "audit_operation_id": "sha256:" + "4" * 64}]}), encoding="utf-8")
        child_path = root / "child.jsonl"
        transcript = root / "parent.jsonl"
        if host == "pi":
            write_jsonl(child_path, [{"type": "message", "message": {
                "role": "assistant", "provider": "bedrock", "model": "auditor",
                "content": [{"type": "text", "text": BLOCK}]}}])
            write_jsonl(transcript, [
                {"type": "message", "message": {"role": "assistant", "provider": "evroc",
                 "model": "author", "content": [{"type": "toolCall", "id": "native",
                    "name": "subagent", "arguments": {
                        "agent": "empirica.empirica-auditor", "task": "audit"}}]}},
                {"type": "custom", "customType": "empirica.audit", "data": {
                    "toolCallId": "native", "nativeId": "native", "plan": {
                        "child_id": "child", "operation_id": "sha256:" + "4" * 64}}},
                {"type": "custom", "customType": "empirica.audit.done",
                 "data": {"toolCallId": "native"}},
                {"type": "message", "message": {"role": "toolResult",
                    "toolName": "subagent", "toolCallId": "native",
                    "content": [{"type": "text", "text": "[recorded]"}],
                    "details": {"results": [{"sessionFile": str(child_path)}]}}},
                {"type": "message", "message": {"role": "assistant", "provider": "evroc",
                    "model": "author", "content": [{"type": "toolCall", "id": "report",
                        "name": "report_convergence", "arguments": {}}]}},
                {"type": "message", "message": {"role": "toolResult",
                    "toolName": "report_convergence", "toolCallId": "report",
                    "content": [{"type": "text", "text": json.dumps(RESULT)}],
                    "details": RESULT}},
            ])
            author = {"provider_id": "evroc", "model_id": "author"}
            auditor = {"provider_id": "bedrock", "model_id": "auditor"}
        else:
            write_jsonl(child_path, [{"type": "assistant", "agentId": "native", "message": {
                "role": "assistant", "model": "auditor", "content": [
                    {"type": "tool_use", "name": "SubagentHandback",
                     "input": {"message": BLOCK}},
                    {"type": "text", "text": "Audit complete."}]}}])
            hook = {"hookSpecificOutput": {"updatedInput": {
                "subagent_type": "empirica:empirica-auditor", "run_in_background": True}}}
            write_jsonl(transcript, [
                {"type": "assistant", "message": {"role": "assistant", "model": "author",
                 "content": [{"type": "text", "text": "start"}]}},
                {"type": "attachment", "attachment": {"hookName": "PreToolUse:Agent",
                 "toolUseID": "tool", "stdout": json.dumps(hook)}},
                {"type": "user", "message": {"content": [{"type": "tool_result",
                 "tool_use_id": "tool", "content": "Async agent launched successfully…\nagentId: native"}]}},
                {"type": "attachment", "attachment": {"hookName": "Stop", "stdout":
                 json.dumps({"type": "Block", "reasons": [{"code": "audit.pending"}],
                             "run": {"status": "active", "children": [{"child_id": "child",
                                 "resource_class": "audit", "state": "pending"}]}})}},
                {"type": "queue-operation", "operation": "enqueue",
                 "content": '<agent-message from="native">\n[Subagent hand-back]\n' +
                            BLOCK + "\n</agent-message>"},
                {"type": "assistant", "message": {"role": "assistant", "model": "author",
                 "content": [{"type": "tool_use", "name": "report_convergence",
                              "id": "report", "input": {}}]}},
                {"type": "assistant", "isSidechain": True, "message": {
                 "role": "assistant", "model": "auditor",
                 "content": [{"type": "text", "text": "sidechain"}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result",
                 "tool_use_id": "read-after-convergence", "content": json.dumps(RESULT)}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result",
                 "tool_use_id": "report", "content": json.dumps(RESULT)}]}},
            ])
            author = {"provider_id": "anthropic", "model_id": "author"}
            auditor = {"provider_id": "anthropic", "model_id": "auditor"}
        version_path = root / "version.txt"
        version_path.write_text(version + "\n", encoding="utf-8")
        receipt = {"format": FORMAT, "operator_attested": True, "host": host,
                   "profile_id": profile, "host_version": version, "plugin_version": "2.0.0",
                   "release_commit": "commit", "command": "native command",
                   "audit_child_id": "child", "audit_native_id": "native",
                   "audit_operation_id": "sha256:" + "4" * 64,
                   "observed_author": author, "observed_auditor": auditor, "result": RESULT}
        for name, path in (("transcript", transcript), ("run_state", state_path),
                           ("child_session", child_path), ("version_output", version_path)):
            receipt[f"{name}_path"] = str(path)
            receipt[f"{name}_sha256"] = digest(path)
        return receipt

    def test_structural_receipts_pass_for_supported_hosts(self):
        for host in EXPECTED:
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory:
                self.assertEqual(inspect(self.receipt(Path(directory), host), host,
                                         "commit", "2.0.0"), [])

    def test_arbitrary_text_and_candidate_mismatch_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.receipt(Path(directory), "pi")
            transcript = Path(receipt["transcript_path"])
            transcript.write_text("not a native trace\n", encoding="utf-8")
            receipt["transcript_sha256"] = digest(transcript)
            self.assertTrue(inspect(receipt, "pi", "commit", "2.0.0"))
            receipt = self.receipt(Path(directory), "pi")
            self.assertTrue(inspect(receipt, "pi", "different", "2.0.0"))

    def test_claude_async_lifecycle_omissions_fail(self):
        for missing in ("settlement", "wrong_settlement_child", "notification",
                        "prefix_notification", "trusted_completion"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                receipt = self.receipt(Path(directory), "claude")
                transcript = Path(receipt["transcript_path"])
                rows = [json.loads(line) for line in transcript.read_text().splitlines()]
                if missing == "settlement":
                    rows = [row for row in rows
                            if row.get("attachment", {}).get("hookName") != "Stop"]
                elif missing == "wrong_settlement_child":
                    for row in rows:
                        attachment = row.get("attachment", {})
                        if attachment.get("hookName") == "Stop":
                            stopped = json.loads(attachment["stdout"])
                            stopped["run"]["children"][0]["child_id"] = "different-child"
                            attachment["stdout"] = json.dumps(stopped)
                elif missing == "notification":
                    rows = [row for row in rows if row.get("type") != "queue-operation"]
                elif missing == "prefix_notification":
                    for row in rows:
                        if row.get("type") == "queue-operation":
                            row["content"] = row["content"].replace(
                                '<agent-message from="native">',
                                '<agent-message from="native-other">')
                else:
                    child = Path(receipt["child_session_path"])
                    child_rows = [json.loads(line) for line in child.read_text().splitlines()]
                    child_rows[0]["agentId"] = "different-native"
                    write_jsonl(child, child_rows)
                    receipt["child_session_sha256"] = digest(child)
                write_jsonl(transcript, rows)
                receipt["transcript_sha256"] = digest(transcript)
                self.assertTrue(inspect(receipt, "claude", "commit", "2.0.0"))

    def test_native_trace_binding_mismatches_fail(self):
        cases = ("pi_operation", "pi_session", "claude_forged_wrapper",
                 "claude_duplicate_handback", "claude_malformed_handback")
        for case in cases:
            host = "pi" if case.startswith("pi_") else "claude"
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                receipt = self.receipt(Path(directory), host)
                transcript = Path(receipt["transcript_path"])
                child = Path(receipt["child_session_path"])
                parent_rows = [json.loads(line) for line in transcript.read_text().splitlines()]
                child_rows = [json.loads(line) for line in child.read_text().splitlines()]
                if case == "pi_operation":
                    event = next(row for row in parent_rows
                                 if row.get("customType") == "empirica.audit")
                    event["data"]["plan"]["operation_id"] = "sha256:" + "9" * 64
                elif case == "pi_session":
                    result = next(row["message"] for row in parent_rows
                                  if row.get("message", {}).get("toolName") == "subagent")
                    result["details"]["results"][0]["sessionFile"] = "/different/session.jsonl"
                elif case == "claude_forged_wrapper":
                    event = next(row for row in parent_rows if row.get("type") == "queue-operation")
                    event["type"] = "user"
                elif case == "claude_duplicate_handback":
                    child_rows.append(child_rows[0])
                else:
                    handback = child_rows[0]["message"]["content"][0]
                    handback["input"] = {}
                write_jsonl(transcript, parent_rows)
                write_jsonl(child, child_rows)
                receipt["transcript_sha256"] = digest(transcript)
                receipt["child_session_sha256"] = digest(child)
                self.assertTrue(inspect(receipt, host, "commit", "2.0.0"))

    def test_symlinked_retained_file_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = self.receipt(root, "pi")
            version = Path(receipt["version_output_path"])
            real = root / "real-version.txt"
            version.rename(real)
            version.symlink_to(real)
            receipt["version_output_sha256"] = "sha256:" + "0" * 64
            self.assertTrue(inspect(receipt, "pi", "commit", "2.0.0"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
