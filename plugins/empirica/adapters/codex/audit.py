"""Codex audit reservation cleanup while verdict-producing identity is unobservable."""
from __future__ import annotations

from adapters.audit_protocol import AuditProtocol, AuditProtocolError

from .transport import CODEX_PROFILE_ID, BridgeTransport, Transport


def execute_audit(run_id: str, *, transport: Transport | None = None) -> bool:
    """Reject one reservation because Codex cannot observe the auditor identity."""
    tx = transport or BridgeTransport()
    protocol = AuditProtocol(
        CODEX_PROFILE_ID,
        dispatch=lambda request, _profile: tx.dispatch(request),
    )
    try:
        protocol.reconcile_orphans(run_id, native_prefix="codex-stop-recovery")
        plan = protocol.prepare(run_id, role_profile="empirica:empirica-auditor")
        protocol.reject(plan)
    except AuditProtocolError:
        pass
    return False
