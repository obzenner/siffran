#!/usr/bin/env python3
"""Online smoke test against the real, pinned Codex CLI package.

The test uses fresh HOME and CODEX_HOME directories, installs this repository as
a local marketplace, proves plugin/skill/MCP discovery, then makes one implicit
skill invocation and one structured MCP tool invocation. It requires network
access and either an existing Codex login or ``OPENAI_API_KEY`` because both
invocation checks call a model.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODEX_NPM_PACKAGE = "@openai/codex@0.146.0"
EXPECTED_VERSION = "codex-cli 0.146.0"


def _codex(
    npx: str, command: list[str], env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess:
    argv = [
        npx,
        "--yes",
        f"--package={CODEX_NPM_PACKAGE}",
        "--",
        "codex",
        *command,
    ]
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
        check=True,
    )


def _json_lines(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line.strip().startswith("{")]


def _final_message(events: list[dict]) -> str:
    texts: list[str] = []
    for event in events:
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str):
                texts.append(text)
    return texts[-1] if texts else ""


def main() -> int:
    source_auth = Path.home() / ".codex" / "auth.json"
    if not source_auth.is_file() and not os.environ.get("OPENAI_API_KEY"):
        print(
            "  FAIL methodologist-codex-smoke requires a Codex login or OPENAI_API_KEY",
            file=sys.stderr,
        )
        return 2
    npx = shutil.which("npx")
    if npx is None:
        print("  FAIL methodologist-codex-smoke requires npx", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="siffran-codex-smoke-") as temporary:
        root = Path(temporary)
        codex_home = root / "codex-home"
        isolated_home = root / "home"
        workspace = root / "workspace"
        codex_home.mkdir()
        isolated_home.mkdir()
        workspace.mkdir()

        # Copy an existing login into the throwaway Codex home when available.
        # The test never reads or prints its contents, and TemporaryDirectory
        # removes the copy with all other smoke state.
        if source_auth.is_file():
            shutil.copy2(source_auth, codex_home / "auth.json")

        env = os.environ.copy()
        env["CODEX_HOME"] = str(codex_home)
        env["HOME"] = str(isolated_home)
        # Reuse only npm's download cache. Codex config, auth, skills, plugins,
        # sessions, and repository context remain inside the temporary roots.
        original_cache = Path.home() / ".npm"
        if original_cache.is_dir():
            env["npm_config_cache"] = str(original_cache)

        version = _codex(npx, ["--version"], env, workspace).stdout.strip()
        if version != EXPECTED_VERSION:
            raise AssertionError(f"expected {EXPECTED_VERSION!r}, got {version!r}")

        added = _codex(
            npx,
            ["plugin", "marketplace", "add", str(ROOT), "--json"], env, workspace
        )
        payload = json.loads(added.stdout)
        if payload.get("marketplaceName") != "siffran":
            raise AssertionError(f"marketplace add did not return siffran: {payload!r}")

        available = json.loads(
            _codex(
                npx,
                ["plugin", "list", "--marketplace", "siffran", "--available", "--json"],
                env,
                workspace,
            ).stdout
        )
        if "methodologist" not in json.dumps(available):
            raise AssertionError("Codex did not discover Methodologist in the marketplace")

        installed_result = _codex(
            npx,
            ["plugin", "add", "methodologist@siffran", "--json"],
            env,
            workspace,
        )
        installed_payload = json.loads(installed_result.stdout)
        installed_path = Path(str(installed_payload.get("installedPath", "")))
        if not (installed_path / "skills" / "think" / "SKILL.md").is_file():
            raise AssertionError("Codex install cache does not contain the think skill")
        installed = json.loads(
            _codex(npx, ["plugin", "list", "--json"], env, workspace).stdout
        )
        installed_text = json.dumps(installed)
        if "methodologist@siffran" not in installed_text or '"enabled": true' not in installed_text:
            raise AssertionError("Codex did not enable the installed plugin")

        implicit = _codex(
            npx,
            [
                "exec",
                "--json",
                "--ephemeral",
                "--skip-git-repo-check",
                "--ignore-rules",
                "--dangerously-bypass-approvals-and-sandbox",
                "Think through this architecture decision: should a retry queue preserve "
                "original request ordering? Use the matching installed workflow implicitly "
                "and follow it completely. This is a smoke test, so keep each required phase "
                "to one sentence.",
            ],
            env,
            workspace,
        )
        implicit_final = _final_message(_json_lines(implicit.stdout))
        implicit_markers = ("## Methodology:", "### Reasoning trace", "### Conclusion")
        if not all(marker in implicit_final for marker in implicit_markers):
            raise AssertionError(
                "implicit Codex invocation did not execute the think skill; "
                f"final response was {implicit_final!r}"
            )

        structured = _codex(
            npx,
            [
                "exec",
                "--json",
                "--ephemeral",
                "--skip-git-repo-check",
                "--ignore-rules",
                "--dangerously-bypass-approvals-and-sandbox",
                "Call the installed methodologist_select tool with methodology "
                "formal-reasoning and reason codex-0.146.0-smoke. After it returns a "
                "six-phase plan, respond exactly CODEX_METHODOLOGIST_BRIDGE_OK.",
            ],
            env,
            workspace,
        )
        structured_events = _json_lines(structured.stdout)
        calls = [
            event["item"]
            for event in structured_events
            if isinstance(event.get("item"), dict)
            and event["item"].get("type") == "mcp_tool_call"
            and event["item"].get("tool") == "methodologist_select"
            and event["item"].get("status") == "completed"
        ]
        if not calls or calls[-1].get("result", {}).get("structured_content", {}).get(
            "methodology"
        ) != "formal-reasoning":
            raise AssertionError("Codex did not invoke the Methodologist MCP tool")
        if _final_message(structured_events).strip() != "CODEX_METHODOLOGIST_BRIDGE_OK":
            raise AssertionError("Codex did not complete the structured bridge invocation")

    print(
        "  ok: codex-cli 0.146.0 discovered Methodologist and invoked implicit + MCP modes"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        print(f"  FAIL {exc}: {detail}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (AssertionError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"  FAIL {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
