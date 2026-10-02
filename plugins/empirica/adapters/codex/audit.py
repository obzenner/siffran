"""Codex audit reservation rejection while verdict-producing identity is unobservable."""
from __future__ import annotations

from adapters.audit_protocol import AuditProtocol

from .transport import CODEX_PROFILE_ID, BridgeTransport, Transport


def reject_unsupported_audit(run_id: str, *, transport: Transport | None = None) -> None:
    """Reserve the owed audit child and reject it: Codex cannot observe the auditor identity.

    Orphaned audit reservations are reconciled first. Any ``AuditProtocolError`` or transport
    failure propagates so the Stop hook denies instead of continuing past an unreconciled
    reservation.
    """
    tx = transport or BridgeTransport()
    protocol = AuditProtocol(
        CODEX_PROFILE_ID,
        dispatch=lambda request, _profile: tx.dispatch(request),
    )
    protocol.reconcile_orphans(run_id, native_prefix="codex-stop-recovery")
    plan = protocol.prepare(run_id, role_profile="empirica:empirica-auditor")
    protocol.reject(plan)
