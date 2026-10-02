#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
# The receipt tool is independent of plugin code; the tests render the real author view with it.
for path in (SCRIPTS, ROOT / "plugins/empirica"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from adapters.author_view import render_author_view  # noqa: E402
from application.location import encode_handle  # noqa: E402
from application.protocol import validate_public_result  # noqa: E402
from core.records import RunKey  # noqa: E402
from empirica_live_receipts import (  # noqa: E402
    EXPECTED, FORMAT, author_view_header, author_view_run_id, author_view_section,
    converged_report_view, digest, handback_report, inspect, pending_audit_settlement,
    run_handle)

# The durable run of the test receipts: the host stores it at projects/<p>/runs/<s>/gen-<g>/run.json
# and its public handle encodes exactly those ids. The handle is produced by the host's own codec,
# independently of the tool's ``run_handle``.
PROJECT_ID = "s256-" + "a" * 64
SESSION_ID = "s256-" + "b" * 64
RUN_HANDLE = encode_handle(RunKey(PROJECT_ID, SESSION_ID, 1))
OTHER_HANDLE = encode_handle(RunKey(PROJECT_ID, "s256-" + "c" * 64, 1))
STATE_PARTS = ("projects", PROJECT_ID, "runs", SESSION_ID, "gen-1", "run.json")

VERDICT = {"verdict": "pass", "findings": [], "argument_digest": "sha256:" + "1" * 64,
           "goal_digest": "sha256:" + "2" * 64,
           "frozen_scope_digest": None, "deferred_scope_digest": "sha256:" + "3" * 64,
           "reviewed_claims": [], "scope_review": "pass"}
BLOCK = "```empirica-verdict\n" + json.dumps(VERDICT) + "\n```"
RESULT = {"type": "Allow", "converged": True, "run": {"status": "converged",
          "children": [{"child_id": "child", "purpose": "audit", "resource_class": "audit",
                        "state": "completed"}]}}


def fixture_result(name: str) -> dict:
    """A schema-valid public result from the conformance fixtures."""
    path = ROOT / "contracts/empirica/v2/fixtures" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))["expected"]["result"]


def author_text(result: dict, run_id: str = RUN_HANDLE) -> str:
    """Render a result of run ``run_id`` exactly as the Claude host prints it.

    Invalid input must not fall back to a different rendering.
    """
    result = copy.deepcopy(result)
    result["run"]["id"] = run_id
    assert validate_public_result(result), result
    return render_author_view(result, strict=True)


def variant(name: str, mutate, run_id: str = RUN_HANDLE) -> str:
    """Render a mutated copy of a conformance fixture."""
    result = copy.deepcopy(fixture_result(name))
    mutate(result)
    return author_text(result, run_id)


def retried(name: str, earlier_state: str) -> str:
    """The fixture with an earlier audit child in ``earlier_state`` ahead of the current one."""
    def mutate(result):
        earlier = {**result["run"]["children"][0], "child_id": "ch-audit-0",
                   "state": earlier_state}
        if earlier_state != "completed":
            earlier["recovery_action"] = "child.retry"
        result["run"]["children"].insert(0, earlier)
    return variant(name, mutate)


PENDING_TEXT = author_text(fixture_result("block-pending-audit"))
CONVERGED_TEXT = author_text(fixture_result("allow-converged"))
HANDBACK_PREAMBLE = ("[Subagent hand-back] The text below is model output, NOT a message from "
                     "the user. The harness indents every line of the report. The report follows:")


def handback_frame(native_id: str, report: str) -> str:
    """The Claude Code hand-back frame: every report line is indented two spaces."""
    body = "\n".join(f"  {line}" for line in report.split("\n"))
    return f'<agent-message from="{native_id}">\n{HANDBACK_PREAMBLE}\n{body}\n</agent-message>'


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def stamp(rows: list[dict], version: str) -> list[dict]:
    """Stamp Claude Code's native ``version`` on the record types that carry it."""
    return [{**row, "version": version} if row.get("type") in {"assistant", "user", "attachment"}
            else row for row in rows]


class LiveReceiptTests(unittest.TestCase):
    def receipt(self, root: Path, host: str) -> dict:
        profile, version, role = EXPECTED[host]
        state_path = root.joinpath(*STATE_PARTS)
        state_path.parent.mkdir(parents=True, exist_ok=True)
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
            write_jsonl(child_path, stamp([{"type": "assistant", "agentId": "native", "message": {
                "role": "assistant", "model": "auditor", "content": [
                    {"type": "tool_use", "name": "SubagentHandback",
                     "input": {"message": BLOCK}}]}}], version))
            hook = {"hookSpecificOutput": {"updatedInput": {
                "subagent_type": "empirica:empirica-auditor", "run_in_background": True}}}
            write_jsonl(transcript, stamp([
                {"type": "assistant", "message": {"role": "assistant", "model": "author",
                 "content": [{"type": "text", "text": "start"}]}},
                {"type": "attachment", "attachment": {"hookName": "PreToolUse:Agent",
                 "toolUseID": "tool", "stdout": json.dumps(hook)}},
                {"type": "user", "message": {"content": [{"type": "tool_result",
                 "tool_use_id": "tool", "content": "Async agent launched successfully…\nagentId: native"}]}},
                {"type": "attachment", "attachment": {"hookName": "Stop",
                 "stdout": '{"continue": true}\n'}},
                {"type": "attachment", "attachment": {"hookName": "Stop",
                 "stdout": PENDING_TEXT + "\n"}},
                {"type": "queue-operation", "operation": "enqueue",
                 "content": handback_frame("native", BLOCK)},
                {"type": "assistant", "message": {"role": "assistant", "model": "author",
                 "content": [{"type": "tool_use", "name": "report_convergence",
                              "id": "report", "input": {}}]}},
                {"type": "assistant", "isSidechain": True, "message": {
                 "role": "assistant", "model": "auditor",
                 "content": [{"type": "text", "text": "sidechain"}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result",
                 "tool_use_id": "read-after-convergence",
                 "content": [{"type": "text", "text": CONVERGED_TEXT}]}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result",
                 "tool_use_id": "report",
                 "content": [{"type": "text", "text": CONVERGED_TEXT}]}]}},
            ], version))
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

    def restamp(self, receipt: dict, version: str, *, parent: dict | None = None,
                child: dict | None = None) -> dict:
        """Rewrite Claude's native versions (all records at ``version``, then per-index overrides).

        ``parent``/``child`` map a record index to its version; ``None`` deletes the field.
        """
        for name, overrides in (("transcript", parent or {}), ("child_session", child or {})):
            path = Path(receipt[f"{name}_path"])
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            for row in rows:
                if "version" in row:
                    row["version"] = version
            for index, value in overrides.items():
                if value is None:
                    rows[index].pop("version", None)
                else:
                    rows[index]["version"] = value
            write_jsonl(path, rows)
            receipt[f"{name}_sha256"] = digest(path)
        return receipt

    def with_retried_audit(self, receipt: dict, host: str, *, earlier_state: str = "completed",
                           earlier_last: bool = False, duplicate_settlement: bool = False) -> dict:
        """Add an earlier audit (child, launch, its Stop settlement and a blocked report) to a receipt.

        On Claude the earlier audit has its own Stop settlement (one pending child, as the host
        rendered it at the time) and the final Stop and convergence views render both the earlier
        and the current audit child, as the host does after a retry. ``duplicate_settlement``
        repeats the final settlement after the bound launch, which no real host produces.
        """
        state_path = Path(receipt["run_state_path"])
        state = json.loads(state_path.read_text(encoding="utf-8"))
        earlier = {**state["children"][0], "child_id": "child-0", "native_id": "native-0",
                   "state": earlier_state, "audit_operation_id": "sha256:" + "5" * 64}
        state["children"].insert(0, earlier)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        block = {"type": "Block", "run": {"status": "active", "children": []}}
        block_text = author_text(fixture_result("block-audit-exhausted"))
        if host == "pi":
            launch = [
                {"type": "message", "message": {"role": "assistant", "provider": "evroc",
                 "model": "author", "content": [{"type": "toolCall", "id": "native-0",
                    "name": "subagent", "arguments": {
                        "agent": "empirica.empirica-auditor", "task": "audit"}}]}},
                {"type": "message", "message": {"role": "toolResult", "toolName": "subagent",
                    "toolCallId": "native-0", "content": [{"type": "text", "text": "[recorded]"}],
                    "details": {"results": [{"sessionFile": "earlier.jsonl"}]}}},
                {"type": "message", "message": {"role": "assistant", "provider": "evroc",
                    "model": "author", "content": [{"type": "toolCall", "id": "report-0",
                        "name": "report_convergence", "arguments": {}}]}},
                {"type": "message", "message": {"role": "toolResult",
                    "toolName": "report_convergence", "toolCallId": "report-0",
                    "content": [{"type": "text", "text": json.dumps(block)}], "details": block}},
            ]
        else:
            hook = {"hookSpecificOutput": {"updatedInput": {
                "subagent_type": "empirica:empirica-auditor", "run_in_background": True}}}
            launch = stamp([
                {"type": "attachment", "attachment": {"hookName": "PreToolUse:Agent",
                 "toolUseID": "tool-0", "stdout": json.dumps(hook)}},
                {"type": "user", "message": {"content": [{"type": "tool_result",
                 "tool_use_id": "tool-0",
                 "content": "Async agent launched successfully…\nagentId: native-0"}]}},
                {"type": "attachment", "attachment": {"hookName": "Stop",
                 "stdout": PENDING_TEXT + "\n"}},
                {"type": "assistant", "message": {"role": "assistant", "model": "author",
                 "content": [{"type": "tool_use", "name": "report_convergence",
                              "id": "report-0", "input": {}}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result",
                 "tool_use_id": "report-0",
                 "content": [{"type": "text", "text": block_text}]}]}},
            ], "2.1.280")  # earlier rows legitimately predate the attested host version
        transcript = Path(receipt["transcript_path"])
        rows = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
        if host == "claude":
            texts = {PENDING_TEXT + "\n": retried("block-pending-audit", earlier_state) + "\n",
                     CONVERGED_TEXT: retried("allow-converged", earlier_state)}
            for row in rows:
                attachment = row.get("attachment", {})
                if attachment.get("stdout") in texts:
                    attachment["stdout"] = texts[attachment["stdout"]]
                message = row.get("message", {})
                for item in message.get("content", []) if isinstance(message, dict) else []:
                    for block in item.get("content", []) if isinstance(item, dict) and isinstance(
                            item.get("content"), list) else []:
                        if block.get("text") in texts:
                            block["text"] = texts[block["text"]]
            if duplicate_settlement:
                final = next(row for row in rows if row.get("attachment", {}).get("hookName")
                             == "Stop" and "Children:" in row["attachment"].get("stdout", ""))
                rows.insert(rows.index(final) + 1, json.loads(json.dumps(final)))
        # Claude's first row is the author's opening text; Pi's first row is the bound launch.
        head = 1 if host == "claude" else 0
        rows = rows + launch if earlier_last else rows[:head] + launch + rows[head:]
        write_jsonl(transcript, rows)
        for name in ("transcript", "run_state"):
            receipt[f"{name}_sha256"] = digest(Path(receipt[f"{name}_path"]))
        return receipt

    def test_a_retried_audit_binds_the_latest_child(self):
        # Failed and cancelled children render a ``— recovery: …`` suffix.
        for host in EXPECTED:
            for earlier in ("completed", "failed", "cancelled"):
                with self.subTest(host=host, earlier=earlier), \
                        tempfile.TemporaryDirectory() as directory:
                    receipt = self.with_retried_audit(
                        self.receipt(Path(directory), host), host, earlier_state=earlier)
                    self.assertEqual(inspect(receipt, host, "commit", "2.0.0"), [])

    def test_a_retried_audit_must_be_settled_and_superseded(self):
        cases = {**{f"unsettled {state}": {"earlier_state": state}
                    for state in ("reserved", "launching", "pending")},
                 "not last": {"earlier_last": True}}
        for host in EXPECTED:
            for name, options in cases.items():
                with self.subTest(host=host, case=name), tempfile.TemporaryDirectory() as directory:
                    receipt = self.with_retried_audit(
                        self.receipt(Path(directory), host), host, **options)
                    self.assertNotEqual(inspect(receipt, host, "commit", "2.0.0"), [])

    def test_claude_binds_the_settlement_that_follows_the_bound_launch(self):
        # The earlier audit's own settlement precedes the bound launch and is not counted; two
        # settlements after the bound launch are rejected.
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.with_retried_audit(self.receipt(Path(directory), "claude"), "claude")
            rows = [json.loads(line) for line in Path(receipt["transcript_path"]).read_text(
                encoding="utf-8").splitlines()]
            settlements = [row for row in rows if row.get("attachment", {}).get("hookName") == "Stop"
                           and row["attachment"].get("stdout", "").startswith("Block active")]
            self.assertEqual(len(settlements), 2)
            self.assertEqual(inspect(receipt, "claude", "commit", "2.0.0"), [])
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.with_retried_audit(self.receipt(Path(directory), "claude"), "claude",
                                              duplicate_settlement=True)
            self.assertEqual(inspect(receipt, "claude", "commit", "2.0.0"),
                             ["claude: claude: expected one pending Stop settlement and converged report"])

    def test_structural_receipts_pass_for_supported_hosts(self):
        for host in EXPECTED:
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory:
                self.assertEqual(inspect(self.receipt(Path(directory), host), host,
                                         "commit", "2.0.0"), [])

    def test_compatible_harness_patch_versions_pass_and_are_recorded_exactly(self):
        cases = {"claude": ("2.1.280", "2.1.280 (Claude Code)\n"),
                 "pi": ("0.84.2", "0.84.2\n")}
        for host, (version, native_output) in cases.items():
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory:
                receipt = self.receipt(Path(directory), host)
                version_path = Path(receipt["version_output_path"])
                version_path.write_text(native_output, encoding="utf-8")
                receipt["version_output_sha256"] = digest(version_path)
                receipt["host_version"] = version
                if host == "claude":
                    self.restamp(receipt, version)
                self.assertEqual(inspect(receipt, host, "commit", "2.0.0"), [])

    def test_incompatible_or_misreported_harness_versions_fail(self):
        cases = {"claude": "2.2.0", "pi": "0.90.0"}
        for host, version in cases.items():
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory:
                receipt = self.receipt(Path(directory), host)
                version_path = Path(receipt["version_output_path"])
                version_path.write_text(version + "\n", encoding="utf-8")
                receipt["version_output_sha256"] = digest(version_path)
                receipt["host_version"] = version
                self.assertTrue(any("compatible range" in error for error in
                                    inspect(receipt, host, "commit", "2.0.0")))
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.receipt(Path(directory), "claude")
            receipt["host_version"] = "2.1.280"
            self.assertTrue(any("native version output mismatch" in error for error in
                                inspect(receipt, "claude", "commit", "2.0.0")))

    def test_native_version_output_rejects_wrong_host_label_and_malformed_text(self):
        cases = {"pi": "0.84.2 (Claude Code)\n", "claude": "Claude Code version 2.1.280\n"}
        for host, output in cases.items():
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory:
                receipt = self.receipt(Path(directory), host)
                version_path = Path(receipt["version_output_path"])
                version_path.write_text(output, encoding="utf-8")
                receipt["version_output_sha256"] = digest(version_path)
                errors = inspect(receipt, host, "commit", "2.0.0")
                self.assertTrue(any("native version output is malformed" in error
                                    for error in errors), errors)

    def test_arbitrary_text_and_candidate_mismatch_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.receipt(Path(directory), "pi")
            transcript = Path(receipt["transcript_path"])
            transcript.write_text("not a native trace\n", encoding="utf-8")
            receipt["transcript_sha256"] = digest(transcript)
            self.assertTrue(inspect(receipt, "pi", "commit", "2.0.0"))
            receipt = self.receipt(Path(directory), "pi")
            self.assertTrue(inspect(receipt, "pi", "different", "2.0.0"))

    def test_completed_child_must_retain_audit_resource_class(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.receipt(Path(directory), "pi")
            wrong_result = json.loads(json.dumps(receipt["result"]))
            wrong_result["run"]["children"][0]["resource_class"] = "investigation"
            receipt["result"] = wrong_result
            transcript = Path(receipt["transcript_path"])
            rows = [json.loads(line) for line in transcript.read_text().splitlines()]
            report = next(row for row in rows
                          if row.get("message", {}).get("toolName") == "report_convergence")
            report["message"]["details"] = wrong_result
            report["message"]["content"] = [
                {"type": "text", "text": json.dumps(wrong_result)}]
            write_jsonl(transcript, rows)
            receipt["transcript_sha256"] = digest(transcript)
            errors = inspect(receipt, "pi", "commit", "2.0.0")
            self.assertTrue(any("completed bound child" in error for error in errors))

    def test_claude_async_lifecycle_omissions_fail(self):
        for missing in ("settlement", "notification", "prefix_notification",
                        "unframed_notification", "trusted_completion"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                receipt = self.receipt(Path(directory), "claude")
                transcript = Path(receipt["transcript_path"])
                rows = [json.loads(line) for line in transcript.read_text().splitlines()]
                if missing == "settlement":
                    rows = [row for row in rows
                            if row.get("attachment", {}).get("hookName") != "Stop"]
                elif missing == "notification":
                    rows = [row for row in rows if row.get("type") != "queue-operation"]
                elif missing == "unframed_notification":
                    for row in rows:
                        if row.get("type") == "queue-operation":
                            row["content"] = ('<agent-message from="native">\n'
                                              '[Subagent hand-back]\n' + BLOCK +
                                              "\n</agent-message>")
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

    def claude_receipt_with(self, root: Path, *, stop: str | None = None,
                            report: object = None) -> tuple[dict, list[str]]:
        """A Claude receipt whose Stop stdout and/or report tool result text is replaced."""
        receipt = self.receipt(root, "claude")
        transcript = Path(receipt["transcript_path"])
        rows = [json.loads(line) for line in transcript.read_text().splitlines()]
        for row in rows:
            attachment = row.get("attachment", {})
            if stop is not None and attachment.get("stdout") == PENDING_TEXT + "\n":
                attachment["stdout"] = stop
            for item in (row.get("message", {}).get("content") or []
                         if isinstance(row.get("message", {}).get("content"), list) else []):
                if report is not None and item.get("tool_use_id") == "report":
                    item["content"] = report
        write_jsonl(transcript, rows)
        receipt["transcript_sha256"] = digest(transcript)
        return receipt, inspect(receipt, "claude", "commit", "2.0.0")

    def test_author_view_recognizers_parse_the_real_rendering(self):
        self.assertEqual(author_view_header(PENDING_TEXT), ("Block", "active", None))
        self.assertEqual(author_view_header(CONVERGED_TEXT), ("Allow", "converged", True))
        self.assertEqual(author_view_header(variant("allow-converged", lambda result: (
            result.update(converged=False), result["run"].update(status="active")))),
            ("Allow", "active", False))
        self.assertEqual(author_view_section(PENDING_TEXT, "Children:"), ["  audit: pending"])
        self.assertEqual(author_view_section(PENDING_TEXT, "Reasons:"), [
            "  audit.pending: Audit child is pending; wait.", "    next: host: child_event"])
        self.assertEqual(author_view_section(PENDING_TEXT, "Residuals:"), [])
        self.assertEqual(author_view_section(PENDING_TEXT + "\n", "Children:"),
                         ["  audit: pending"])
        self.assertEqual(author_view_section(
            PENDING_TEXT + "\n\nChildren:\n  audit: completed", "Children:"), [])
        for text in ('{"continue": true}\n', "{}\n", "Inert", "Fault schema.invalid (closed)",
                     json.dumps(RESULT), "", "Block\n  audit.pending"):
            self.assertIsNone(author_view_header(text), text)

    def test_pending_audit_settlement_is_strict(self):
        def two_children(result):
            result["run"]["children"].append(
                {**result["run"]["children"][0], "child_id": "ch-audit-2"})

        def other_status(result):
            result["run"]["status"] = "stopped_residual"

        def no_child(result):
            result["run"]["children"] = []

        def completed_child(result):
            result["run"]["children"][0]["state"] = "completed"

        self.assertTrue(pending_audit_settlement(PENDING_TEXT, RUN_HANDLE))
        self.assertTrue(pending_audit_settlement(PENDING_TEXT + "\n", RUN_HANDLE))
        rejected = {
            "different sole reason": author_text(fixture_result("block-open-claim")),
            "reason and pending child": author_text(fixture_result("block-child-terminal")),
            "exhausted audit budget": author_text(fixture_result("block-audit-exhausted")),
            "two children": variant("block-pending-audit", two_children),
            "status not active": variant("block-pending-audit", other_status),
            "no child": variant("block-pending-audit", no_child),
            "completed child": variant("block-pending-audit", completed_child),
            "allow": CONVERGED_TEXT,
            "hook acknowledgement": '{"continue": true}\n',
            "retired JSON shape": json.dumps({
                "type": "Block", "reasons": [{"code": "audit.pending"}],
                "run": {"status": "active", "children": [{
                    "child_id": "child", "resource_class": "audit", "state": "pending"}]}}),
        }
        for name, text in rejected.items():
            with self.subTest(name=name):
                self.assertFalse(pending_audit_settlement(text, RUN_HANDLE), text)

    def test_pending_audit_settlement_allows_settled_historical_children_only(self):
        for earlier in ("completed", "failed", "cancelled", "timed_out", "orphaned"):
            with self.subTest(earlier=earlier):
                text = retried("block-pending-audit", earlier)
                self.assertTrue(pending_audit_settlement(text, RUN_HANDLE), text)
                self.assertTrue(converged_report_view(
                    retried("allow-converged", earlier), RUN_HANDLE))
        self.assertIn("  audit: failed — recovery: host: child_reserve\n  audit: pending",
                      retried("block-pending-audit", "failed"))
        for live in ("pending", "launching", "reserved"):
            with self.subTest(earlier_live=live):
                self.assertFalse(pending_audit_settlement(
                    retried("block-pending-audit", live), RUN_HANDLE))

    def test_author_view_run_id_is_unambiguous(self):
        self.assertEqual(author_view_run_id(PENDING_TEXT), RUN_HANDLE)
        self.assertEqual(author_view_run_id(CONVERGED_TEXT + "\n"), RUN_HANDLE)
        header, run_line, *rest = PENDING_TEXT.split("\n")
        for name, text in {
                "missing": "\n".join([header, *rest]),
                "duplicate": "\n".join([header, run_line, run_line, *rest]),
                "second handle": "\n".join([header, run_line, f"run_id: {OTHER_HANDLE}", *rest]),
                "trailing duplicate": PENDING_TEXT + "\n\nrun_id: " + RUN_HANDLE,
                "not the second line": "\n".join([header, rest[0], run_line, *rest[1:]]),
                "empty": "\n".join([header, "run_id: ", *rest]),
                "no header": "run_id: " + RUN_HANDLE}.items():
            with self.subTest(name=name):
                self.assertIsNone(author_view_run_id(text))

    def test_views_of_another_run_do_not_satisfy_the_run_binding(self):
        for name, text in {"other run": author_text(fixture_result("allow-converged"), OTHER_HANDLE),
                           "other generation": author_text(fixture_result("allow-converged"),
                                                           encode_handle(RunKey(
                                                               PROJECT_ID, SESSION_ID, 2))),
                           "fixture id": author_text(fixture_result("allow-converged"), "run-fx")
                           }.items():
            with self.subTest(name=name):
                self.assertFalse(converged_report_view(text, RUN_HANDLE))
                self.assertTrue(converged_report_view(text, author_view_run_id(text)))

    def test_run_handle_is_derived_from_the_durable_state_path_like_the_host(self):
        root = Path("/home")
        for generation in (1, 2, 10):
            path = root.joinpath("projects", PROJECT_ID, "runs", SESSION_ID,
                                 f"gen-{generation}", "run.json")
            self.assertEqual(run_handle(path),
                             encode_handle(RunKey(PROJECT_ID, SESSION_ID, generation)))
        for name, parts in {
                "flat": ("state.json",),
                "wrong file": STATE_PARTS[:-1] + ("state.json",),
                "wrong anchors": ("x", PROJECT_ID, "y", SESSION_ID, "gen-1", "run.json"),
                "raw project id": ("projects", "project", "runs", SESSION_ID, "gen-1", "run.json"),
                "raw session id": ("projects", PROJECT_ID, "runs", "session", "gen-1", "run.json"),
                "generation zero": STATE_PARTS[:-2] + ("gen-0", "run.json"),
                "padded generation": STATE_PARTS[:-2] + ("gen-01", "run.json"),
                "unnumbered generation": STATE_PARTS[:-2] + ("gen-x", "run.json")}.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                run_handle(root.joinpath(*parts))

    def test_claude_views_must_belong_to_the_audited_run(self):
        def other_child(result):
            result["run"]["children"][0]["child_id"] = "unrelated-audit"

        def dropped_run_line(text):
            header, _, *rest = text.split("\n")
            return "\n".join([header, *rest])

        def repeated_run_line(text):
            header, run_line, *rest = text.split("\n")
            return "\n".join([header, run_line, run_line, *rest])

        unrelated_report = variant("allow-converged", other_child, OTHER_HANDLE)
        unrelated_stop = author_text(fixture_result("block-pending-audit"), OTHER_HANDLE)
        cases = {
            "unrelated report": (None, unrelated_report),
            "unrelated settlement": (unrelated_stop, None),
            "both from another run": (unrelated_stop, unrelated_report),
            "both from another generation": (
                author_text(fixture_result("block-pending-audit"),
                            encode_handle(RunKey(PROJECT_ID, SESSION_ID, 2))),
                author_text(fixture_result("allow-converged"),
                            encode_handle(RunKey(PROJECT_ID, SESSION_ID, 2)))),
            "unbound fixture ids": (author_text(fixture_result("block-pending-audit"), "run-fx"),
                                    author_text(fixture_result("allow-converged"), "run-fx")),
            "settlement without run id": (dropped_run_line(PENDING_TEXT), None),
            "report without run id": (None, dropped_run_line(CONVERGED_TEXT)),
            "settlement with repeated run id": (repeated_run_line(PENDING_TEXT), None),
            "report with repeated run id": (None, repeated_run_line(CONVERGED_TEXT)),
        }
        for name, (stop, report) in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                _, errors = self.claude_receipt_with(
                    Path(directory), stop=None if stop is None else stop + "\n", report=report)
                self.assertTrue(any("one pending Stop settlement and converged report" in error
                                    for error in errors), errors)

    def test_claude_rejects_state_stored_outside_the_run_the_views_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = self.receipt(root, "claude")
            state = Path(receipt["run_state_path"])
            other = root.joinpath("projects", PROJECT_ID, "runs", "s256-" + "c" * 64,
                                  "gen-1", "run.json")
            other.parent.mkdir(parents=True)
            other.write_bytes(state.read_bytes())
            receipt["run_state_path"], receipt["run_state_sha256"] = str(other), digest(other)
            errors = inspect(receipt, "claude", "commit", "2.0.0")
            self.assertTrue(any("one pending Stop settlement and converged report" in error
                                for error in errors), errors)
            flat = root / "flat-state.json"
            flat.write_bytes(state.read_bytes())
            receipt["run_state_path"], receipt["run_state_sha256"] = str(flat), digest(flat)
            errors = inspect(receipt, "claude", "commit", "2.0.0")
            self.assertTrue(any("projects/<p>/runs/<s>/gen-<g>/run.json" in error
                                for error in errors), errors)

    def test_claude_transcript_versions_must_match_the_attested_host_version(self):
        # Parent records: 0 opening text, 1 launch hook, 2 launch result, 3 Stop hook ack,
        # 4 Stop settlement, 5 notification (queue-operation), 6 report call, 7 sidechain,
        # 8 read-after result, 9 report result. Child record 0 is the handback.
        supporting = {"launch": 1, "launch result": 2, "settlement": 4, "report call": 6,
                      "report result": 9}
        unrelated = {0: "2.1.280", 3: "2.1.280", 7: "2.1.280", 8: "2.1.280"}

        def attest(receipt: dict, version: str) -> None:
            path = Path(receipt["version_output_path"])
            path.write_text(f"{version} (Claude Code)\n", encoding="utf-8")
            receipt["version_output_sha256"], receipt["host_version"] = digest(path), version

        def check(receipt: dict) -> list[str]:
            return inspect(receipt, "claude", "commit", "2.0.0")

        with tempfile.TemporaryDirectory() as directory:
            receipt = self.restamp(self.receipt(Path(directory), "claude"), "2.1.287")
            attest(receipt, "2.1.286")
            errors = check(receipt)
            self.assertTrue(any("does not match attested host version 2.1.286" in error
                                for error in errors), errors)
            attest(receipt, "2.1.287")
            self.assertEqual(check(receipt), [])
            # Records unrelated to the lifecycle, e.g. from before an upgrade, are not judged.
            self.restamp(receipt, "2.1.287", parent=unrelated)
            self.assertEqual(check(receipt), [])
        for name, index in supporting.items():
            for value in ("2.1.286", None):
                with self.subTest(parent=name, version=value), \
                        tempfile.TemporaryDirectory() as directory:
                    receipt = self.receipt(Path(directory), "claude")
                    self.restamp(receipt, receipt["host_version"], parent={index: value})
                    errors = check(receipt)
                    self.assertTrue(any(f"parent record {index}" in error for error in errors),
                                    errors)
        for value in ("2.1.286", None):
            with self.subTest(child=True, version=value), tempfile.TemporaryDirectory() as directory:
                receipt = self.receipt(Path(directory), "claude")
                self.restamp(receipt, receipt["host_version"], child={0: value})
                errors = check(receipt)
                self.assertTrue(any("child record 0" in error for error in errors), errors)
        with tempfile.TemporaryDirectory() as directory:
            # The notification is a queue-operation: no version field is fine, a wrong one is not.
            receipt = self.receipt(Path(directory), "claude")
            self.restamp(receipt, receipt["host_version"], parent={5: "2.1.286"})
            errors = check(receipt)
            self.assertTrue(any("parent record 5" in error for error in errors), errors)

    def test_handback_report_requires_the_indented_frame(self):
        self.assertEqual(handback_report(handback_frame("native", BLOCK)), ("native", BLOCK))
        for name, content in {
                "unindented": handback_frame("native", BLOCK).replace("\n  ", "\n"),
                "no frame": "[Subagent hand-back]\n" + BLOCK,
                "no preamble": '<agent-message from="native">\n  ' + BLOCK + "\n</agent-message>",
                "trailing text": handback_frame("native", BLOCK) + "\nextra"}.items():
            with self.subTest(name=name):
                self.assertIsNone(handback_report(content))

    def test_claude_rejects_non_settlement_stop_output_and_non_converged_reports(self):
        def two_children(result):
            result["run"]["children"].append(
                {**result["run"]["children"][0], "child_id": "ch-audit-2"})

        def other_status(result):
            result["run"]["status"] = "stopped_residual"

        def no_audit_child(result):
            result["run"]["children"] = []

        stops = {
            "different sole reason": author_text(fixture_result("block-open-claim")),
            "two children": variant("block-pending-audit", two_children),
            "status not active": variant("block-pending-audit", other_status),
            "retired JSON": json.dumps({"type": "Block", "reasons": [{"code": "audit.pending"}],
                                        "run": {"status": "active", "children": [{
                                            "child_id": "child", "resource_class": "audit",
                                            "state": "pending"}]}}),
        }
        reports = {
            "converged=false": variant("allow-converged", lambda result: (
                result.update(converged=False), result["run"].update(status="active"))),
            "block": PENDING_TEXT,
            "no completed audit child": variant("allow-converged", no_audit_child),
            "retired JSON": json.dumps(RESULT),
        }
        with tempfile.TemporaryDirectory() as directory:
            _, errors = self.claude_receipt_with(Path(directory))
            self.assertEqual(errors, [])
        for name, stop in stops.items():
            with self.subTest(stop=name), tempfile.TemporaryDirectory() as directory:
                _, errors = self.claude_receipt_with(Path(directory), stop=stop + "\n")
                self.assertTrue(any("one pending Stop settlement" in error for error in errors),
                                errors)
        for name, report in reports.items():
            for shape in ("string", "blocks"):
                with self.subTest(report=name, shape=shape), tempfile.TemporaryDirectory() as directory:
                    content = report if shape == "string" else [{"type": "text", "text": report}]
                    _, errors = self.claude_receipt_with(Path(directory), report=content)
                    self.assertTrue(any("converged report" in error for error in errors), errors)

    def test_claude_converged_report_accepts_string_or_block_content(self):
        for content in (CONVERGED_TEXT, [{"type": "text", "text": CONVERGED_TEXT}]):
            with tempfile.TemporaryDirectory() as directory:
                _, errors = self.claude_receipt_with(Path(directory), report=content)
                self.assertEqual(errors, [])

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
