"""Pure deterministic PublicContract section selection.

The literals are a mechanical projection of ``public-contract.json`` and are checked by the
contract validator; this module performs no I/O and inspects no run/domain state.
"""
from __future__ import annotations

_CONTEXT = {
    "bootstrap": ("core", "roles", "hosts/capabilities"),
    "block": (),
    "auditor": ("audit", "audit/independence", "evidence/research", "evidence/spike",
                "evidence/order", "evidence/freshness"),
    "restore": ("core", "run/lifecycle", "children", "hosts/capabilities", "recovery"),
    "terminal": ("terminal",),
    "on_demand": (),
}
_TERMINAL = {"converged", "stopped_residual", "stopped_frozen", "stopped_budget"}
_TERMINAL_SECTIONS = ("terminal",)
_UNKNOWN = ("core", "run/lifecycle", "recovery")
_REASON_SECTIONS = {
    "run.no_active": ("run/lifecycle",), "run.old_version": ("run/lifecycle",),
    "run.terminal": ("terminal",), "run.corrupt": ("run/lifecycle",),
    "graph.missing": ("claims/graph",), "graph.invalid": ("claims/graph",),
    "route.required": ("route",), "route.late": ("route",),
    "claim.research_missing": ("evidence/research",),
    "claim.research_unbound": ("evidence/research",),
    "claim.spike_prerequisite_missing": ("evidence/order",),
    "claim.spike_missing": ("evidence/spike",),
    "claim.spike_stale": ("evidence/freshness",),
    "claim.human_decision": ("claims/residuals",),
    "claim.refuted": ("claims/refutation",), "evidence.conflict": ("claims/refutation",),
    "budget.exhausted": ("budget",), "audit.required": ("audit",),
    "audit.pending": ("audit",), "audit.unreadable": ("audit",),
    "audit.failed": ("audit",), "audit.stale": ("audit",),
    "audit.same_model": ("audit/independence",),
    "audit.independence_unverified": ("audit/independence",),
    "child.terminal": ("children",), "host.async_unsupported": ("hosts/capabilities",),
    "host.audit_output_unobservable": ("hosts/capabilities",),
    "freeze.deferred": ("freeze",),
}


def select_sections(operation_context: str, reason_codes: list[str],
                    terminal_status: str | None) -> list[str]:
    if operation_context not in _CONTEXT or type(reason_codes) is not list:
        return list(_UNKNOWN)
    if terminal_status is not None and terminal_status not in _TERMINAL:
        return list(_UNKNOWN)
    if any(type(code) is not str or code not in _REASON_SECTIONS for code in reason_codes):
        return list(_UNKNOWN)
    values = list(_CONTEXT[operation_context])
    for code in reason_codes:
        values.extend(_REASON_SECTIONS[code])
    if terminal_status is not None:
        values.extend(_TERMINAL_SECTIONS)
    return list(dict.fromkeys(values))
