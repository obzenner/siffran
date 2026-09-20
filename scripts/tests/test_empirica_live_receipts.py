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
          "children": [{"child_id": "child", "purpose": "audit", "state": "completed"}]}}


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
                {"type": "message_end", "message": {"role": "assistant", "provider": "evroc",
                 "model": "author", "content": [{"type": "text", "text": "done"}]}},
                {"type": "tool_execution_end", "toolName": "subagent", "toolCallId": "native",
                 "result": {"content": [{"type": "text", "text": "[recorded]"}],
                            "details": {"results": [{"sessionFile": str(child_path)}]}}},
                {"type": "tool_execution_end", "toolName": "report_convergence",
                 "result": {"details": RESULT}},
            ])
            author = {"provider_id": "evroc", "model_id": "author"}
            auditor = {"provider_id": "bedrock", "model_id": "auditor"}
        else:
            write_jsonl(child_path, [{"type": "assistant", "agentId": "native", "message": {
                "role": "assistant", "model": "auditor", "content": [{"type": "text", "text": BLOCK}]}}])
            hook = {"hookSpecificOutput": {"updatedInput": {
                "subagent_type": "empirica:empirica-auditor"}}}
            write_jsonl(transcript, [
                {"type": "assistant", "message": {"role": "assistant", "model": "author",
                 "content": [{"type": "text", "text": "start"}]}},
                {"type": "attachment", "attachment": {"hookName": "PreToolUse:Agent",
                 "toolUseID": "tool", "stdout": json.dumps(hook)}},
                {"type": "user", "message": {"content": [{"type": "tool_result",
                 "tool_use_id": "tool", "content": [{"type": "text", "text": BLOCK},
                 {"type": "text", "text": "agentId: native"}]}]}},
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
