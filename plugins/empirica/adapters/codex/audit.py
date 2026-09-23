"""Codex native driver for the shared foreground-audit protocol."""
from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from uuid import uuid4

from adapters.audit import child_prompt, verdict_from_final_output
from adapters.audit_protocol import AuditProtocol, AuditProtocolError, IdentityObservation

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
    cwd_raw = payload.get("cwd")
    cwd = Path(cwd_raw) if isinstance(cwd_raw, str) and cwd_raw else Path.cwd()
    try:
        protocol.reconcile_orphans(run_id, native_prefix="codex-stop-recovery")
        plan = protocol.prepare(run_id, role_profile="empirica:empirica-auditor")
        if not plan.auditor:
            protocol.reject(plan)
            return False
        model = plan.auditor["model_id"]
    except AuditProtocolError:
        return False

    author_model = payload.get("model") if isinstance(payload.get("model"), str) else None
    native_id: str | None = None

    def started(observed_native_id: str) -> None:
        nonlocal native_id
        native_id = observed_native_id
        protocol.observe_started(plan, observed_native_id)
        protocol.observe_identities(
            plan, observed_native_id,
            author=IdentityObservation(
                "openai" if author_model else None, author_model, "host", "codex-hook"),
            auditor=IdentityObservation(
                None, None, "host", f"codex-process:{observed_native_id}:resolved-model-unobservable"),
        )

    try:
        code, output = (runner or _default_runner)(child_prompt(plan.argument), model, cwd, started)
    except Exception:
        if native_id is None:
            protocol.reject(plan)
        else:
            try:
                protocol.observe_failure(plan, native_id, "failed")
            except AuditProtocolError:
                pass  # identity admission may already have closed the operation
        return False

    if native_id is None:
        protocol.reject(plan)
        return False
    if code is None:
        protocol.observe_failure(plan, native_id, "timed_out")
        return False
    candidate = verdict_from_final_output(output) if code == 0 else None
    if candidate is None:
        protocol.observe_failure(plan, native_id, "failed")
        return False
    if not protocol.observe_verdict(plan, native_id, candidate):
        return False
    return True
