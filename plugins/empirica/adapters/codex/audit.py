"""Codex native driver for the shared foreground-audit protocol."""
from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from uuid import uuid4

from adapters.audit_protocol import AuditProtocol, AuditProtocolError

from .transport import CODEX_PROFILE_ID, BridgeTransport, Transport

StartedObserver = Callable[[str], None]
AuditRunner = Callable[[str, str, Path, StartedObserver], tuple[int | None, str]]
MANAGED_AUDIT_TIMEOUT_SECONDS = 900


def _default_runner(
    prompt: str, model: str, cwd: Path, observe_started: StartedObserver,
) -> tuple[int | None, str]:
    """Start one isolated native process and report its PID before waiting for output."""
    with tempfile.TemporaryDirectory(prefix="empirica-codex-audit-") as tmp:
        output = Path(tmp) / "final.txt"
        process = subprocess.Popen(  # noqa: S603 - fixed executable/argv, no shell
            ["codex", "exec", "--ephemeral", "--sandbox", "read-only",
             "--model", model, "--cd", str(cwd),
             "--output-last-message", str(output), "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        native_id = f"codex-exec:{process.pid}:{uuid4().hex}"
        try:
            observe_started(native_id)
            stdout, _ = process.communicate(input=prompt, timeout=MANAGED_AUDIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return None, ""
        except Exception:
            process.kill()
            process.communicate()
            raise
        try:
            final = output.read_text(encoding="utf-8")
        except OSError:
            final = stdout
        return process.returncode, final


def execute_audit(
    payload: Mapping[str, object], run_id: str, *, transport: Transport | None = None,
    runner: AuditRunner | None = None,
) -> bool:
    """Execute one native process as a mechanical driver over ``AuditProtocol``."""
    tx = transport or BridgeTransport()
    protocol = AuditProtocol(
        CODEX_PROFILE_ID,
        dispatch=lambda request, _profile: tx.dispatch(request),
    )
    try:
        protocol.reconcile_orphans(run_id, native_prefix="codex-stop-recovery")
        plan = protocol.prepare(run_id, role_profile="empirica:empirica-auditor")
        # Codex still cannot observe the verdict-producing model identity.
        protocol.reject(plan)
    except AuditProtocolError:
        pass
    return False
