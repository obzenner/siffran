#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
from adapters.git.artifact_repo import GitArtifactRepository  # noqa: E402
from application.snapshot import make_artifact  # noqa: E402
from core.freshness import observations_digest  # noqa: E402
from core.records import Artifact, RunKey  # noqa: E402
from empirica_live_receipts import (  # noqa: E402
    EXPECTED, FORMAT, _POLICIES, author_view_header, author_view_run_id, author_view_section,
    converged_report_view, digest, evidence_receipts, handback_report, host_runtime_rejection, inspect,
    pending_audit_settlement, run_handle, subagents_runtime)

# The durable run of the test receipts: the host stores it at projects/<p>/runs/<s>/gen-<g>/run.json
# and its public handle encodes exactly those ids. The handle is produced by the host's own codec,
# independently of the tool's ``run_handle``.
PROJECT_ID = "s256-" + "a" * 64
SESSION_ID = "s256-" + "b" * 64
RUN_HANDLE = encode_handle(RunKey(PROJECT_ID, SESSION_ID, 1))
OTHER_HANDLE = encode_handle(RunKey(PROJECT_ID, "s256-" + "c" * 64, 1))
STATE_PARTS = ("projects", PROJECT_ID, "runs", SESSION_ID, "gen-1", "run.json")

# The audit runtime a Pi run records when it starts (invocation.host_runtime): a version with a compat record.
PI_POLICY = _POLICIES["pi"]["subagents_compatibility"]
PACKAGE_ROOT = "/opt/" + PI_POLICY["package"]
PI_HOST_RUNTIME = {"policy_id": PI_POLICY["policy_id"], "subagents": {
    "package": PI_POLICY["package"], "version": "0.76.1", "reviewed": True,
    "owner_path": PACKAGE_ROOT + "/src/extension/index.js", "package_root": PACKAGE_ROOT,
    "preflight_path": PACKAGE_ROOT + "/src/api/preflight.js", "source": PACKAGE_ROOT + "/index.js"}}
RUNTIME_CASES = json.loads((ROOT / "plugins/empirica/tests/fixtures/host-runtime-cases.json")
                           .read_text(encoding="utf-8"))["cases"]

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


def _version_lt(left: str, right: str) -> bool:
    return tuple(int(part) for part in left.split(".")) < tuple(int(part) for part in right.split("."))


def _patch(version: str, delta: int) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + delta}"


# A receipt must be from the profile's qualified version or later; the tests read it from the profile, so raising
# it in `host-profiles.json` needs no edit here (ADR-0069).
QUALIFIED = {host: _POLICIES[host]["version"] for host in EXPECTED}
CLAUDE_EARLIER = _patch(QUALIFIED["claude"], -1)
CLAUDE_LATER = _patch(QUALIFIED["claude"], 1)


RETRIEVAL = ["local", "workspace"]
RECORD_ID = "sha256:" + "f" * 64


_STORES: dict[tuple[bool, bool], tuple[Path, list[dict]]] = {}


def evidence_store(root: Path, *, presentation: bool = True, confirmation: bool = True) -> tuple[Path, list[dict]]:
    """A copy of the (once built) store for this combination, so each test owns its repository."""
    if (presentation, confirmation) not in _STORES:
        _STORES[presentation, confirmation] = build_store(
            Path(tempfile.mkdtemp(prefix="empirica-store-")), presentation, confirmation)
    template, rows = _STORES[presentation, confirmation]
    repo = root / "artifact-store"
    shutil.rmtree(repo, ignore_errors=True)
    shutil.copytree(template, repo)
    return repo, copy.deepcopy(rows)


def build_store(root: Path, presentation: bool, confirmation: bool) -> tuple[Path, list[dict]]:
    """A Git artifact store with a real committed manifest chain: the research, then one manifest per human step.

    The artifacts and manifests are content-addressed by the application's own constructors and written by the host's
    store adapter; the rows a receipt must then carry are spelled out here, independently of the tool that derives them."""
    repo = root / "artifact-store"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    store, key = GitArtifactRepository(repo), RunKey(PROJECT_ID, SESSION_ID, 1)
    basis = make_artifact({"kind": "observation_basis", "version": 1, "clock_inputs": [], "policy_inputs": {
        "contract_id": "c", "contract_version": "2", "contract_digest": "sha256:" + "1" * 64, "profile_id": "p"},
        "sources": [{"name": "workspace", "basis_id": "b", "digest": observations_digest([]), "observations": []}]})
    rows, parent = [], None

    def commit(*artifacts: Artifact) -> None:
        """Append the artifacts, then the manifest that references them (the newest commit is the head)."""
        nonlocal parent
        for artifact in artifacts:
            store.append(key, artifact)
        manifest = make_artifact({
            "kind": "transaction_manifest", "version": 2, "parent": parent,
            "artifact_ids": [a.artifact_id for a in artifacts], "observation_basis_id": "b",
            "observation_digest": "sha256:" + "2" * 64, "observation_basis_digest": basis.artifact_id,
            "next_state_digest": "sha256:" + "3" * 64})
        store.append(key, manifest)
        parent = manifest.artifact_id

    # A converged run always holds its research, so its ref exists whatever the human was asked.
    commit(make_artifact({"kind": "research", "claim_id": "C1"}), basis)
    if presentation:
        shown = make_artifact({
            "kind": "evidence_presentation", "receipt_id": "p-1", "key": "sha256:" + "9" * 64,
            "record_ids": [RECORD_ID], "claims": [{"claim_id": "C1", "claim_digest": "sha256:" + "8" * 64}],
            "approval_kind": "host_ui"})
        commit(shown)
        rows.append({"kind": "evidence_presentation", "receipt_id": "p-1", "artifact_id": shown.artifact_id,
                     "record_ids": [RECORD_ID], "claim_ids": ["C1"]})
    if confirmation:
        decided = make_artifact({
            "kind": "evidence_confirmation", "receipt_id": "p-1", "subject": "research", "record_id": RECORD_ID,
            "claim_id": "C1", "outcome": "confirm", "approval_kind": "host_ui"})
        commit(decided)
        rows.append({"kind": "evidence_confirmation", "receipt_id": "p-1", "artifact_id": decided.artifact_id,
                     "subject": "research", "outcome": "confirm", "target": RECORD_ID})
    return repo, sorted(rows, key=lambda row: (row["kind"], row["receipt_id"], row["artifact_id"]))


def head_of(repo: Path) -> str:
    """The artifact id the store's newest commit added: what the run's retained state records as its committed head."""
    refs = subprocess.run(["git", "-C", str(repo), "for-each-ref", "--format=%(refname)", "refs/empirica/artifacts/"],
                          capture_output=True, text=True, check=True).stdout.split()
    if not refs:
        return ""
    ref = refs[0]
    name = subprocess.run(["git", "-C", str(repo), "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", ref],
                          capture_output=True, text=True, check=True).stdout.split()[-1]
    return bytes.fromhex(name[1:]).decode() if name.startswith("=") else name


def record_repo(root: Path, repo: Path) -> None:
    """The run's recorded artifact location (``projects/<project>/project.json``, written when the run began), and
    the committed head its retained state names (``committed_artifact_head_id``): the store's newest artifact."""
    state_path = root.joinpath(*STATE_PARTS)
    if state_path.exists() and (repo / ".git").exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["committed_artifact_head_id"] = head_of(repo) or state.get("committed_artifact_head_id")
        state_path.write_text(json.dumps(state), encoding="utf-8")
    sidecar = root / "projects" / PROJECT_ID / "project.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({"repo": str(repo)}), encoding="utf-8")


class LiveReceiptTests(unittest.TestCase):
    def receipt(self, root: Path, host: str) -> dict:
        profile, role = EXPECTED[host]
        version = QUALIFIED[host]
        state_path = root.joinpath(*STATE_PARTS)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {"status": "converged", "children": [{
            "child_id": "child", "native_id": "native", "purpose": "audit",
            "state": "completed", "audit_role_profile": role,
            "audit_operation_id": "sha256:" + "4" * 64}]}
        state["invocation"] = {"host": host, "audit_retrieval": list(RETRIEVAL)}
        if host == "pi":
            state["invocation"] = {"host": "pi", "interactive": True, "signal": "ctx.mode=tui",
                                   "delegation": False, "audit_retrieval": list(RETRIEVAL),
                                   "host_runtime": copy.deepcopy(PI_HOST_RUNTIME)}
        state_path.write_text(json.dumps(state), encoding="utf-8")
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
                   "observed_author": author, "observed_auditor": auditor, "result": RESULT,
                   "audit_retrieval": list(RETRIEVAL)}
        repo, held = evidence_store(root)
        record_repo(root, repo)
        receipt["artifacts_repo_path"], receipt["evidence_receipts"] = str(repo), held
        if host == "pi":
            receipt["subagents_runtime"] = {
                **{key: PI_HOST_RUNTIME["subagents"][key] for key in (
                    "package", "version", "reviewed", "owner_path", "package_root", "preflight_path")},
                "policy_id": PI_HOST_RUNTIME["policy_id"]}
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
            ], CLAUDE_EARLIER)  # earlier rows legitimately predate the attested host version
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
        # The qualified version and any later one inside the interval; Pi's interval is the whole 1.x line.
        cases = (("claude", QUALIFIED["claude"], f"{QUALIFIED['claude']} (Claude Code)\n"),
                 ("claude", CLAUDE_LATER, f"{CLAUDE_LATER} (Claude Code)\n"),
                 ("pi", QUALIFIED["pi"], f"{QUALIFIED['pi']}\n"), ("pi", "1.9.9", "1.9.9\n"))
        for host, version, native_output in cases:
            with self.subTest(host=host, version=version), tempfile.TemporaryDirectory() as directory:
                receipt = self.receipt(Path(directory), host)
                version_path = Path(receipt["version_output_path"])
                version_path.write_text(native_output, encoding="utf-8")
                receipt["version_output_sha256"] = digest(version_path)
                receipt["host_version"] = version
                if host == "claude":
                    self.restamp(receipt, version)
                self.assertEqual(inspect(receipt, host, "commit", "2.0.0"), [])

    def test_a_receipt_below_the_qualified_version_is_refused(self):
        # The profile's `version` is the receipt floor: a receipt from an older host inside the interval is refused.
        for host in EXPECTED:
            below = ((CLAUDE_EARLIER, "2.1.278") if host == "claude" else ("1.0.9", "0.84.1", "0.99.9"))
            below = [v for v in below if _version_lt(v, QUALIFIED[host])]
            self.assertTrue(below, host)
            for version in below:
                with self.subTest(host=host, version=version), tempfile.TemporaryDirectory() as directory:
                    receipt = self.receipt(Path(directory), host)
                    version_path = Path(receipt["version_output_path"])
                    version_path.write_text(version + ("\n" if host == "pi" else " (Claude Code)\n"), encoding="utf-8")
                    receipt["version_output_sha256"] = digest(version_path)
                    receipt["host_version"] = version
                    errors = inspect(receipt, host, "commit", "2.0.0")
                    self.assertEqual(len(errors), 1, errors)
                    self.assertIn(f"{host} {version} is below the qualified version {QUALIFIED[host]}", errors[0])
                    self.assertIn(f"a native receipt from {host} >= {QUALIFIED[host]} is required", errors[0])

    def test_raising_the_qualified_version_in_the_profile_asks_for_exactly_a_new_receipt(self):
        from empirica_live_receipts import require_compatible_version
        for host in EXPECTED:
            require_compatible_version(host, QUALIFIED[host])
            raised = {**_POLICIES[host], "version": _patch(QUALIFIED[host], 1)}
            with mock.patch.dict(_POLICIES, {host: raised}):
                require_compatible_version(host, raised["version"])
                with self.assertRaisesRegex(ValueError, f"a native receipt from {host} >= {raised['version']} is required"):
                    require_compatible_version(host, QUALIFIED[host])

    def test_incompatible_or_misreported_harness_versions_fail(self):
        cases = {"claude": "2.2.0", "pi": "2.0.0"}
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
            receipt["host_version"] = CLAUDE_LATER
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

    # --- the audit runtime (pi-subagents) of a Pi receipt (Empirica 4.1, D7) ---------------------
    # Each case starts from a valid Pi receipt and changes exactly one fact.

    def rewrite_state(self, receipt: dict, mutate) -> dict:
        """Change the durable run state the receipt names and re-attest its digest."""
        path = Path(receipt["run_state_path"])
        state = json.loads(path.read_text(encoding="utf-8"))
        mutate(state)
        path.write_text(json.dumps(state), encoding="utf-8")
        receipt["run_state_sha256"] = digest(path)
        return receipt

    def pi_errors(self, mutate_receipt=None, mutate_state=None) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.receipt(Path(directory), "pi")
            if mutate_state is not None:
                self.rewrite_state(receipt, mutate_state)
            if mutate_receipt is not None:
                mutate_receipt(receipt)
            return inspect(receipt, "pi", "commit", "2.0.0")

    def test_pi_receipt_carries_the_runtime_recorded_in_the_durable_state(self):
        self.assertEqual(self.pi_errors(), [])
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.receipt(Path(directory), "pi")
            self.assertEqual(receipt["subagents_runtime"]["version"], "0.76.1")
            self.assertEqual(set(receipt["subagents_runtime"]),
                             {"package", "version", "reviewed", "owner_path", "package_root", "preflight_path", "policy_id"})

    def test_pi_receipt_from_an_in_range_unreviewed_version_is_valid_and_records_reviewed_false(self):
        """ADR-0069: a version inside the range with no compat record is admitted, so its qualification receipt is too."""
        def unreviewed(state):
            state["invocation"]["host_runtime"]["subagents"].update(version="0.99.0", reviewed=False)

        with tempfile.TemporaryDirectory() as directory:
            receipt = self.receipt(Path(directory), "pi")
            self.rewrite_state(receipt, unreviewed)
            receipt["subagents_runtime"].update(version="0.99.0", reviewed=False)
            self.assertEqual(inspect(receipt, "pi", "commit", "2.0.0"), [])
            state = json.loads(Path(receipt["run_state_path"]).read_text(encoding="utf-8"))
            self.assertIs(subagents_runtime(state, "pi")["reviewed"], False)

    def test_pi_receipt_derives_reviewed_false_when_the_run_state_has_no_flag(self):
        """An absent `reviewed` reads as not reviewed (shared case `reviewed-missing`), end to end in the receipt."""
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.receipt(Path(directory), "pi")
            self.rewrite_state(receipt, lambda state: state["invocation"]["host_runtime"]["subagents"].pop("reviewed"))
            receipt["subagents_runtime"]["reviewed"] = False
            self.assertEqual(inspect(receipt, "pi", "commit", "2.0.0"), [])

    def test_pi_receipt_requires_subagents_runtime(self):
        errors = self.pi_errors(lambda receipt: receipt.pop("subagents_runtime"))
        self.assertEqual(errors, ["pi: receipt subagents_runtime is missing"])

    def test_pi_receipt_rejects_a_receipt_that_disagrees_with_the_state_on_one_fact(self):
        for key, value in (("version", "0.64.0"), ("package", "other-subagents"),
                           ("owner_path", PACKAGE_ROOT + "/src/other.js"), ("package_root", "/opt/elsewhere"),
                           ("preflight_path", PACKAGE_ROOT + "/src/api/other.js"),
                           ("policy_id", "pi-subagents-foreground-audit-v2")):
            with self.subTest(fact=key):
                errors = self.pi_errors(lambda receipt: receipt["subagents_runtime"].update({key: value}))
                self.assertEqual(errors, ["pi: receipt subagents_runtime does not equal the runtime recorded in the durable state"])

    def test_pi_receipt_rejects_an_extra_or_missing_receipt_member(self):
        self.assertTrue(self.pi_errors(lambda receipt: receipt["subagents_runtime"].update(latest="1.0.0")))
        self.assertTrue(self.pi_errors(lambda receipt: receipt["subagents_runtime"].pop("owner_path")))

    def test_pi_receipt_rejects_state_that_records_no_or_an_unacceptable_runtime(self):
        def runtime(state):
            return state["invocation"]["host_runtime"]
        cases = {
            "missing": (lambda state: state["invocation"].pop("host_runtime"), "missing"),
            "at_the_exclusive_maximum": (lambda state: runtime(state)["subagents"].update(version="1.0.0"), "version"),
            "far_future_version": (lambda state: runtime(state)["subagents"].update(version="9.9.9"), "version"),
            "below_the_minimum": (lambda state: runtime(state)["subagents"].update(version="0.49.9"), "version"),
            "reviewed_not_a_boolean": (lambda state: runtime(state)["subagents"].update(reviewed="yes"), "malformed"),
            "other_policy": (lambda state: runtime(state).update(policy_id="pi-subagents-foreground-audit-v2"), "policy"),
            "other_package": (lambda state: runtime(state)["subagents"].update(package="other-subagents"), "package"),
            "preflight_outside_package": (lambda state: runtime(state)["subagents"].update(
                preflight_path="/opt/other/src/api/preflight.js"), "paths"),
            "owner_outside_package": (lambda state: runtime(state)["subagents"].update(
                owner_path="/opt/other/src/extension/index.js"), "paths"),
            "extra_member": (lambda state: runtime(state)["subagents"].update(sha="0"), "malformed"),
            "not_an_object": (lambda state: state["invocation"].update(host_runtime="pi-subagents@0.75.0"), "malformed"),
        }
        for name, (mutate, code) in cases.items():
            with self.subTest(case=name):
                errors = self.pi_errors(mutate_state=mutate)
                self.assertEqual(errors, [f"pi: durable state does not carry an acceptable pi-subagents runtime ({code})"])

    def test_pi_receipt_rejects_a_legacy_4_0_receipt_without_a_recorded_runtime(self):
        """A 4.0 receipt (no runtime anywhere) is not migrated or promoted."""
        def legacy(state):
            state.pop("invocation")
        errors = self.pi_errors(lambda receipt: receipt.pop("subagents_runtime"), legacy)
        self.assertEqual(len(errors), 1)
        self.assertIn("does not carry an acceptable pi-subagents runtime (missing)", errors[0])

    # ---- v3: what the auditor could retrieve, and what the human was asked and answered -------

    def v3_errors(self, host="claude", *, mutate_receipt=None, mutate_state=None, store=None) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.receipt(Path(directory), host)
            if mutate_state is not None:
                self.rewrite_state(receipt, mutate_state)
            if store is not None:
                repo, held = evidence_store(Path(directory) / "other", **store) if (
                    (Path(directory) / "other").mkdir() or True) else None
                receipt["artifacts_repo_path"], receipt["evidence_receipts"] = str(repo), held
                record_repo(Path(directory), repo)  # the run recorded this store; only its contents differ
                receipt["run_state_sha256"] = digest(Path(receipt["run_state_path"]))
            if mutate_receipt is not None:
                mutate_receipt(receipt)
            return inspect(receipt, host, "commit", "2.0.0")

    def test_v3_receipts_pass_and_carry_what_the_run_recorded(self):
        for host in EXPECTED:
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory:
                receipt = self.receipt(Path(directory), host)
                self.assertEqual(receipt["format"], "empirica-live-receipt/v3")
                self.assertEqual(receipt["audit_retrieval"], RETRIEVAL)
                self.assertEqual([row["kind"] for row in receipt["evidence_receipts"]],
                                 ["evidence_confirmation", "evidence_presentation"])
                self.assertEqual(inspect(receipt, host, "commit", "2.0.0"), [])

    def test_v2_receipts_are_rejected_not_migrated(self):
        for host in EXPECTED:
            errors = self.v3_errors(host, mutate_receipt=lambda r: r.update(format="empirica-live-receipt/v2"))
            self.assertEqual(errors, [f"{host}: operator-attested v3 receipt is required"])
            legacy = self.v3_errors(host, mutate_receipt=lambda r: [r.pop(k) for k in (
                "audit_retrieval", "artifacts_repo_path", "evidence_receipts")])
            self.assertEqual(len(legacy), 1)

    def test_the_audit_retrieval_must_equal_the_one_the_run_recorded(self):
        for host in EXPECTED:
            for label, change in (("narrower", ["workspace"]), ("wider", ["local", "web", "workspace"]),
                                  ("empty", []), ("absent", None)):
                with self.subTest(host=host, receipt=label):
                    def mutate(receipt, change=change):
                        receipt.pop("audit_retrieval") if change is None else receipt.update(audit_retrieval=change)
                    self.assertEqual(self.v3_errors(host, mutate_receipt=mutate), [
                        f"{host}: receipt audit_retrieval does not equal the retrieval recorded in the durable state"])

    def test_a_state_without_a_valid_recorded_retrieval_cannot_be_receipted(self):
        for label, mutate in (("missing", lambda s: s["invocation"].pop("audit_retrieval")),
                              ("unknown kind", lambda s: s["invocation"].update(audit_retrieval=["ftp"])),
                              ("unsorted", lambda s: s["invocation"].update(audit_retrieval=["workspace", "local"])),
                              ("duplicate", lambda s: s["invocation"].update(audit_retrieval=["local", "local"])),
                              ("not a list", lambda s: s["invocation"].update(audit_retrieval="local"))):
            for host in EXPECTED:
                with self.subTest(host=host, state=label):
                    errors = self.v3_errors(host, mutate_state=mutate)
                    self.assertEqual(errors, [f"{host}: durable state does not record a valid invocation.audit_retrieval"])

    def test_the_human_receipts_must_equal_what_the_artifact_store_holds(self):
        def drop(receipt):
            receipt["evidence_receipts"] = receipt["evidence_receipts"][:1]

        def invent(receipt):
            receipt["evidence_receipts"] = receipt["evidence_receipts"] + [
                {**receipt["evidence_receipts"][0], "outcome": "confirm", "artifact_id": "sha256:" + "1" * 64}]

        def flip(receipt):
            row = next(r for r in receipt["evidence_receipts"] if r["kind"] == "evidence_confirmation")
            row["outcome"] = "reject"
        message = "receipt evidence_receipts do not equal the presentations and confirmations the artifact store holds for this run"
        for label, mutate in (("left out", drop), ("invented", invent), ("outcome changed", flip),
                              ("emptied", lambda r: r.update(evidence_receipts=[]))):
            with self.subTest(label):
                self.assertEqual(self.v3_errors(mutate_receipt=mutate), [f"claude: {message}"])

    def test_D_a_wrong_repository_holding_the_same_ref_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = self.receipt(root, "claude")
            (root / "wrong").mkdir()
            wrong, held = evidence_store(root / "wrong", presentation=False, confirmation=False)
            receipt["artifacts_repo_path"], receipt["evidence_receipts"] = str(wrong), held
            self.assertEqual(held, [])  # it holds the same ref, only without the human's answers
            errors = inspect(receipt, "claude", "commit", "2.0.0")
            self.assertEqual(errors, ["claude: the artifact store is not the repository the run recorded"])

    def test_D_a_run_that_recorded_no_location_or_an_inconsistent_one_is_refused(self):
        message = ["claude: the artifact store is not the repository the run recorded"]
        for label, mutate in (("absent", lambda root: (root / "projects" / PROJECT_ID / "project.json").unlink()),
                              ("malformed", lambda root: (root / "projects" / PROJECT_ID / "project.json").write_text("{")),
                              ("no repo member", lambda root: (root / "projects" / PROJECT_ID / "project.json").write_text("{}")),
                              ("not a repository", lambda root: record_repo(root, root / "nowhere"))):
            with self.subTest(label), tempfile.TemporaryDirectory() as directory:
                receipt = self.receipt(Path(directory), "claude")
                mutate(Path(directory))
                self.assertEqual(inspect(receipt, "claude", "commit", "2.0.0"), message)

    def test_D_a_nested_directory_of_the_recorded_repository_keeps_exactly_the_original_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = self.receipt(root, "claude")
            repo = Path(receipt["artifacts_repo_path"])
            nested = repo / "deeper" / "still"
            nested.mkdir(parents=True)
            state_path = root.joinpath(*STATE_PARTS)
            self.assertEqual(evidence_receipts(nested, state_path), receipt["evidence_receipts"])
            self.assertEqual(len(receipt["evidence_receipts"]), 2)
            receipt["artifacts_repo_path"] = str(nested)
            self.assertEqual(inspect(receipt, "claude", "commit", "2.0.0"), [])

    def test_a_symlink_to_the_repository_root_keeps_both_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = self.receipt(root, "claude")
            link = root / "alias-link"
            link.symlink_to(receipt["artifacts_repo_path"])
            self.assertEqual(evidence_receipts(link, root.joinpath(*STATE_PARTS)), receipt["evidence_receipts"])

    def test_D_a_ref_rolled_back_below_the_runs_committed_head_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = self.receipt(root, "claude")
            repo = Path(receipt["artifacts_repo_path"])
            state_path = root.joinpath(*STATE_PARTS)
            ref = subprocess.run(["git", "-C", str(repo), "for-each-ref", "--format=%(refname)",
                                  "refs/empirica/artifacts/"], capture_output=True, text=True, check=True).stdout.split()[0]
            first = subprocess.run(["git", "-C", str(repo), "rev-list", "--max-parents=0", ref],
                                   capture_output=True, text=True, check=True).stdout.split()[0]
            subprocess.run(["git", "-C", str(repo), "update-ref", ref, first], check=True)
            with self.assertRaises(ValueError) as caught:
                evidence_receipts(repo, state_path)
            self.assertIn("committed history", str(caught.exception))
            self.assertEqual(inspect(receipt, "claude", "commit", "2.0.0"),
                             ["claude: the artifact store does not hold the run's committed history: "
                              "missing reachable manifest"])

    def _rewrite_ref(self, repo: Path, drop) -> None:
        """Replace the run's ref by a commit whose tree lacks the entries ``drop(path, blob_json)`` selects."""
        ref = subprocess.run(["git", "-C", str(repo), "for-each-ref", "--format=%(refname)", "refs/empirica/artifacts/"],
                             capture_output=True, text=True, check=True).stdout.split()[0]
        kept = []
        for line in subprocess.run(["git", "-C", str(repo), "ls-tree", ref], capture_output=True, text=True,
                                   check=True).stdout.splitlines():
            meta, _, path = line.partition("\t")
            blob = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", meta.split()[2]],
                                  capture_output=True, text=True, check=True).stdout
            if not drop(path, json.loads(json.loads(blob)["body"])):
                kept.append(line + "\n")
        tree = subprocess.run(["git", "-C", str(repo), "mktree"], input="".join(kept), capture_output=True, text=True,
                              check=True).stdout.strip()
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t"}
        commit = subprocess.run(["git", "-C", str(repo), "commit-tree", tree, "-m", "x"], capture_output=True, text=True,
                                check=True, env=env).stdout.strip()
        subprocess.run(["git", "-C", str(repo), "update-ref", ref, commit], check=True)

    def test_D_a_tree_that_holds_the_head_but_lost_a_presentation_is_refused(self):
        for lost in ("evidence_presentation", "evidence_confirmation", "research"):
            with self.subTest(lost), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                receipt = self.receipt(root, "claude")
                repo = Path(receipt["artifacts_repo_path"])
                self._rewrite_ref(repo, lambda _path, body: body.get("kind") == lost)
                with self.assertRaises(ValueError) as caught:
                    evidence_receipts(repo, root.joinpath(*STATE_PARTS))
                self.assertIn("committed history", str(caught.exception))
                self.assertIn("missing committed domain artifact", str(caught.exception))
                self.assertEqual(inspect(receipt, "claude", "commit", "2.0.0"),
                                 ["claude: the artifact store does not hold the run's committed history: "
                                  "missing committed domain artifact"])

    def test_a_valid_store_yields_exactly_the_receipts_of_its_chain_and_ignores_an_unreferenced_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = self.receipt(root, "claude")
            repo = Path(receipt["artifacts_repo_path"])
            orphan = make_artifact({"kind": "evidence_presentation", "receipt_id": "orphan", "key": "sha256:" + "9" * 64,
                                    "record_ids": [], "claims": [], "approval_kind": "host_ui"})
            GitArtifactRepository(repo).append(RunKey(PROJECT_ID, SESSION_ID, 1), orphan)
            self.assertEqual(evidence_receipts(repo, root.joinpath(*STATE_PARTS)), receipt["evidence_receipts"])
            self.assertEqual(len(receipt["evidence_receipts"]), 2)

    def test_D_a_state_that_names_no_committed_head_is_refused(self):
        for label, value in (("absent", None), ("not a string", 7), ("not in the store", "sha256:" + "f" * 64)):
            with self.subTest(label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                receipt = self.receipt(root, "claude")
                state_path = root.joinpath(*STATE_PARTS)
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state.pop("committed_artifact_head_id", None)
                if value is not None:
                    state["committed_artifact_head_id"] = value
                state_path.write_text(json.dumps(state), encoding="utf-8")
                receipt["run_state_sha256"] = digest(state_path)
                with self.assertRaises(ValueError):
                    evidence_receipts(Path(receipt["artifacts_repo_path"]), state_path)

    def test_a_worktree_of_the_recorded_repository_is_the_same_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = self.receipt(root, "claude")
            repo = Path(receipt["artifacts_repo_path"])
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                   "GIT_COMMITTER_EMAIL": "t@t"}
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "x"], check=True, env=env)
            alias = root / "alias"
            subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(alias)], check=True, env=env)
            receipt["artifacts_repo_path"] = str(alias)
            self.assertEqual(inspect(receipt, "claude", "commit", "2.0.0"), [])

    def test_a_converged_run_whose_store_has_no_ref_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.receipt(Path(directory), "claude")
            empty = Path(directory) / "empty"
            subprocess.run(["git", "init", "-q", str(empty)], check=True)
            record_repo(Path(directory), empty)
            receipt["run_state_sha256"] = digest(Path(receipt["run_state_path"]))
            receipt["artifacts_repo_path"], receipt["evidence_receipts"] = str(empty), []
            self.assertEqual(inspect(receipt, "claude", "commit", "2.0.0"),
                             ["claude: the artifact store holds no ref for this run, so no receipt can be checked against it"])

    def test_a_run_that_never_asked_a_human_carries_an_empty_list(self):
        for host in EXPECTED:
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory:
                receipt = self.receipt(Path(directory), host)
                repo, held = evidence_store(Path(directory) / "x" if (Path(directory) / "x").mkdir() is None else None,
                                            presentation=False, confirmation=False)
                record_repo(Path(directory), repo)
                receipt["run_state_sha256"] = digest(Path(receipt["run_state_path"]))
                receipt["artifacts_repo_path"], receipt["evidence_receipts"] = str(repo), held
                self.assertEqual((held, inspect(receipt, host, "commit", "2.0.0")), ([], []))

    def test_a_confirmation_needs_a_presentation_in_the_same_store(self):
        errors = self.v3_errors(store={"presentation": False})
        self.assertEqual(errors, ["claude: a human confirmation names no recorded presentation"])

    def test_the_artifact_store_path_is_required_and_must_be_a_repository(self):
        self.assertEqual(self.v3_errors(mutate_receipt=lambda r: r.pop("artifacts_repo_path")),
                         ["claude: the artifact store path is required to check the human receipts"])
        self.assertEqual(len(self.v3_errors(mutate_receipt=lambda r: r.update(artifacts_repo_path="/nonexistent/x"))), 1)

    def test_capture_requires_the_artifact_store_and_records_what_it_holds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            receipt = self.receipt(root, "claude")
            output = root / "receipt.json"
            command = [sys.executable, str(SCRIPTS / "capture_empirica_live_receipt.py"), "claude",
                       "--transcript", receipt["transcript_path"], "--state", receipt["run_state_path"],
                       "--child-session", receipt["child_session_path"],
                       "--version-output", receipt["version_output_path"], "--command", "native command",
                       "--output", str(output), "--operator-attested"]
            missing = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)
            self.assertIn("--artifacts-repo", missing.stderr)
            done = subprocess.run([*command, "--artifacts-repo", receipt["artifacts_repo_path"]],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            captured = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual((captured["audit_retrieval"], captured["evidence_receipts"]),
                             (RETRIEVAL, receipt["evidence_receipts"]))

    def test_claude_receipt_is_unaffected_and_may_not_claim_a_pi_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.receipt(Path(directory), "claude")
            self.assertNotIn("subagents_runtime", receipt)
            self.assertEqual(inspect(receipt, "claude", "commit", "2.0.0"), [])
            receipt["subagents_runtime"] = copy.deepcopy(self.receipt(Path(directory) / "pi", "pi")["subagents_runtime"])
            self.assertTrue(inspect(receipt, "claude", "commit", "2.0.0"))

    def test_the_receipt_rule_decides_every_shared_case_like_the_host_does(self):
        for case in RUNTIME_CASES:
            policy = PI_POLICY if case["profile"] == "external" else None
            with self.subTest(case=case["id"]):
                self.assertEqual(host_runtime_rejection(policy, case["host_runtime"]), case["expect"], case["why"])

    def test_capture_takes_the_runtime_from_the_state_and_offers_no_flag_for_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()  # the capture records resolved paths (macOS: /var -> /private/var)
            receipt = self.receipt(root, "pi")
            output = root / "pi-receipt.json"
            command = [sys.executable, str(SCRIPTS / "capture_empirica_live_receipt.py"), "pi",
                       "--transcript", receipt["transcript_path"], "--state", receipt["run_state_path"],
                       "--child-session", receipt["child_session_path"],
                       "--version-output", receipt["version_output_path"],
                       "--command", "native command", "--output", str(output),
                       "--artifacts-repo", receipt["artifacts_repo_path"], "--operator-attested"]
            done = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            captured = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(captured["subagents_runtime"], receipt["subagents_runtime"])
            self.assertEqual(inspect(captured, "pi", captured["release_commit"], captured["plugin_version"]), [])
            # No way to claim a different package: the option does not exist.
            forged = subprocess.run([*command, "--subagents-version", "0.75.0"], capture_output=True, text=True)
            self.assertEqual(forged.returncode, 2)
            self.assertIn("unrecognized arguments", forged.stderr)
            # A state that recorded no runtime cannot be captured at all.
            self.rewrite_state(receipt, lambda state: state.pop("invocation"))
            refused = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(refused.returncode, 0)

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
        unrelated = {index: CLAUDE_EARLIER for index in (0, 3, 7, 8)}

        def attest(receipt: dict, version: str) -> None:
            path = Path(receipt["version_output_path"])
            path.write_text(f"{version} (Claude Code)\n", encoding="utf-8")
            receipt["version_output_sha256"], receipt["host_version"] = digest(path), version

        def check(receipt: dict) -> list[str]:
            return inspect(receipt, "claude", "commit", "2.0.0")

        with tempfile.TemporaryDirectory() as directory:
            receipt = self.restamp(self.receipt(Path(directory), "claude"), QUALIFIED["claude"])
            attest(receipt, CLAUDE_LATER)
            errors = check(receipt)
            self.assertTrue(any(f"does not match attested host version {CLAUDE_LATER}" in error
                                for error in errors), errors)
            attest(receipt, QUALIFIED["claude"])
            self.assertEqual(check(receipt), [])
            # Records unrelated to the lifecycle, e.g. from before an upgrade, are not judged.
            self.restamp(receipt, QUALIFIED["claude"], parent=unrelated)
            self.assertEqual(check(receipt), [])
        for name, index in supporting.items():
            for value in (CLAUDE_LATER, None):
                with self.subTest(parent=name, version=value), \
                        tempfile.TemporaryDirectory() as directory:
                    receipt = self.receipt(Path(directory), "claude")
                    self.restamp(receipt, receipt["host_version"], parent={index: value})
                    errors = check(receipt)
                    self.assertTrue(any(f"parent record {index}" in error for error in errors),
                                    errors)
        for value in (CLAUDE_LATER, None):
            with self.subTest(child=True, version=value), tempfile.TemporaryDirectory() as directory:
                receipt = self.receipt(Path(directory), "claude")
                self.restamp(receipt, receipt["host_version"], child={0: value})
                errors = check(receipt)
                self.assertTrue(any("child record 0" in error for error in errors), errors)
        with tempfile.TemporaryDirectory() as directory:
            # The notification is a queue-operation: no version field is fine, a wrong one is not.
            receipt = self.receipt(Path(directory), "claude")
            self.restamp(receipt, receipt["host_version"], parent={5: CLAUDE_LATER})
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
