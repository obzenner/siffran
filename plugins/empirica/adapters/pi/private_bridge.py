#!/usr/bin/env python3
"""Pi adapter-private bridge to the canonical audit protocol and trusted ingress."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from adapters import bridge, confirmation  # noqa: E402
from application import host_runtime  # noqa: E402
from adapters.audit_protocol import (  # noqa: E402
    AuditLaunchPlan, AuditorExcluded, AuditProtocol, IdentityObservation,
)
from adapters.identity import observe  # noqa: E402


def _plan(profile: str, raw: dict) -> AuditLaunchPlan:
    value = raw["plan"]
    durable = bridge.trusted_audit_plan(profile, raw["run_id"], value["child_id"])
    if (not isinstance(durable, dict)
            or durable.get("operation_id") != value.get("operation_id")
            or durable.get("role_profile") != value.get("role_profile")
            or durable.get("argument") != value.get("argument")):
        raise ValueError("audit operation does not match durable plan")
    return AuditLaunchPlan(
        profile, raw["run_id"], value["child_id"], value["role_profile"], durable["argument"],
        durable["operation_id"])


def _plan_json(plan: AuditLaunchPlan) -> dict:
    return {"child_id": plan.child_id, "role_profile": plan.role_profile,
            "argument": plan.argument, "operation_id": plan.operation_id}


def _classify(_profile: str, _raw: dict, payload: dict):
    return observe(payload.get("provider_id"), payload.get("model_id"),
                   source=payload.get("source", "pi-model"))


#: A document identity in full — the private answer's ``document.digest`` and the sidecar name.
_FULL_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _bind_document(run_id: object, document: object) -> tuple[str, Path, dict | None, str] | None:
    """Write-first (ADR-0072, PR 09): verify the core's rendered ``{text, digest}`` pair, publish
    its bytes once through the sidecar store beside the run's own record, and return
    ``(digest, absolute path, priors, question)``. ``priors`` is the document's own amendment
    block read from the verified bytes — never recomputed — so the confirm form shows the
    record's ``was`` values, not a host-held dialog. ``question`` is the parsed document's
    core-derived discriminator (M1, review 2) — ``approve`` or ``confirm_amendment`` — the one
    fact the TypeScript mediation chooses which dialog to show from; anything else refuses.
    ``None`` means the mediation must refuse: the pair was absent, malformed, digest-
    inconsistent, its question not one of the two, or the storage refused the write."""
    if not isinstance(document, dict):
        return None
    text, digest = document.get("text"), document.get("digest")
    if not isinstance(text, str) or not isinstance(digest, str) or _FULL_DIGEST.fullmatch(digest) is None:
        return None
    # Lazy on purpose: the sidecar store chain binds only the mediator's own refresh — the raw
    # ``bind_document: true`` request (m2, review 2) — so its import cost is paid by the one
    # operation that writes, never by the plain per-tool_call refreshes or the twelve other
    # private operations this one-shot process shape also serves, whose startup stays
    # exactly forms-08's (PR 09 measurement: whole-private-bridge import ~95 ms before and
    # after this change).
    from adapters.state.hitl import DocumentMismatch, HardLinkUnsupported, HitlDocuments
    from application.location import decode_handle
    from core import hitl as core_hitl
    try:
        data = text.encode("utf-8")
        if core_hitl.digest(data) != digest:
            return None
        key = decode_handle(run_id)
        if key is None:
            return None
        path, written = HitlDocuments().write(key, data)
        if written != digest:
            return None
        parsed = json.loads(text)
        question = parsed.get("question")
        if question not in (core_hitl.APPROVE, core_hitl.CONFIRM_AMENDMENT):
            return None
        amendment = parsed.get("amendment")
        priors = None
        if isinstance(amendment, dict) and isinstance(amendment.get("budgets"), dict):
            priors = {name: row.get("was") for name, row in amendment["budgets"].items()
                      if isinstance(row, dict) and isinstance(row.get("was"), int)}
    except (DocumentMismatch, HardLinkUnsupported, OSError, ValueError):
        return None
    return digest, path, priors, question


def _governance_context(profile: str, raw: dict, payload: dict):
    response = bridge.trusted_governance_context(profile, raw["run_id"], payload)
    result = response.get("result") if isinstance(response, dict) else None
    presentation = result.get("presentation") if isinstance(result, dict) else None
    if not isinstance(presentation, dict):
        return response
    document = presentation.get("document")
    if document is None:
        return response  # a state that waits on no human carries no document; that is normal
    if raw.get("bind_document") is not True:
        # (m2, review 2) A plain refresh — the one the TypeScript adapter runs in front of every
        # tool_call, model_select and session event while the human is deciding — never binds:
        # it is not the mediator's own refresh, no dialog is being shown from its answer, and
        # the judgment an unbound document would serve is not the human's. Strip the pair and
        # write nothing; the mediator binds when it actually mediates (bind_document: true).
        presentation.pop("document", None)
        return response
    bound = _bind_document(raw["run_id"], document)
    if bound is None:
        # The pair could not be proven or published. Never hand the bytes to the transport and
        # never let an adapter present a document no one has verified: strip the pair and mark
        # the presentation, so the TypeScript mediation refuses `governance.approval_unavailable`
        # before any present is sent.
        presentation["document_unavailable"] = True
        presentation.pop("document", None)
        return response
    digest, path, priors, question = bound
    presentation["document"] = {"digest": digest, "path": str(path), "priors": priors,
                                 "question": question}
    return response


def _governance_decision(profile: str, raw: dict, payload: dict):
    return bridge.trusted_governance_decision(profile, raw["run_id"], payload)


def _audit_prepare(profile: str, raw: dict, payload: dict):
    # The runtime is recorded once at StartRun; a reload or resume may since have put another package
    # behind the `subagent` tool. Admit the audit only when the adapter's runtime observed *now* is the
    # recorded one: refuse closed, before any reservation or state write.
    recorded = bridge.trusted_host_runtime(profile, raw["run_id"])
    if recorded is None or not host_runtime.same_runtime(
            recorded.get("host_runtime"), payload.get("host_runtime")):
        return {"type": "audit_refused", "reason": "runtime_changed"}
    try:
        plan = AuditProtocol(profile, audit_retrieval=payload["audit_retrieval"]).prepare(
            raw["run_id"], role_profile=raw["role_profile"], auditor_identity=payload.get("auditor_identity"))
    except AuditorExcluded as excluded:  # before any spawn: the adapter picks another class, or shows the core's text
        return {"type": "audit_excluded", "message": str(excluded), "identities": excluded.identities}
    return {"type": "audit_plan", "plan": _plan_json(plan)}


def _audit_reject(profile: str, raw: dict, _payload: dict):
    AuditProtocol(profile).reject(_plan(profile, raw))
    return {"type": "audit_terminal"}


def _audit_start(profile: str, raw: dict, _payload: dict):
    AuditProtocol(profile).observe_started(_plan(profile, raw), raw["native_id"])
    return {"type": "audit_started"}


def _audit_identity(profile: str, raw: dict, _payload: dict):
    auditor = raw["auditor"]
    AuditProtocol(profile).observe_reviewer(
        _plan(profile, raw), raw["native_id"], auditor=IdentityObservation(
            auditor.get("provider_id"), auditor.get("model_id"), auditor["source"]))
    return {"type": "audit_identity"}


def _audit_failure(profile: str, raw: dict, _payload: dict):
    AuditProtocol(profile).observe_failure(
        _plan(profile, raw), raw["native_id"], raw.get("state", "failed"))
    return {"type": "audit_terminal"}


def _audit_verdict(profile: str, raw: dict, payload: dict):
    admitted = AuditProtocol(profile).observe_verdict(
        _plan(profile, raw), raw["native_id"], payload)
    return {"type": "audit_verdict", "admitted": admitted}


def _evidence_presentation(profile: str, raw: dict, payload: dict):
    """Record the presentation and hand back the items to show (shared flow: adapters/confirmation.py).

    The shared ``present`` writes the batch's document first (fetch, verify, publish once,
    present with the digest), so the sidecar file is written before anything is shown on either
    host. The screen data the adapter receives is unchanged; the presentation answer names the
    written document's ``{digest, path}`` — and, for a terminal batch, ``residuals``, the count of
    obligations ending now would leave unresolved, read out of the written document itself — never
    the bytes (PR 12: the dialog renders the identity; the count is data from the record, never
    recomputed by any host). The confirmation repeat of the digest rides the mediator's
    ``evidence_confirmation`` payload, so this bridge's confirmation leg forwards it when the
    adapter sends it."""
    batch = payload.get("batch")
    shown = confirmation.present(profile, raw["run_id"], terminal=batch if confirmation.is_terminal(batch) else None)
    if isinstance(shown, confirmation.Outcome):  # nothing was shown or spent; the host reports presentation_failed
        return {"type": "evidence_refused", "result": {"failure": shown.failure}}
    receipt_id, items, document = shown
    identity = {"digest": document["digest"], "path": document["path"]}
    if "residuals" in document:
        identity["residuals"] = document["residuals"]
    return {"type": "evidence_presentation", "receipt_id": receipt_id, "items": items,
            "document": identity}


def _human_surface(profile: str, raw: dict, payload: dict):
    return bridge.trusted_human_surface(profile, raw["run_id"], {"run_id": raw["run_id"], **payload})


def _evidence_confirmation(profile: str, raw: dict, payload: dict):
    answers = payload["answers"]
    # PR 12: the mediator sends its presentation's digest, so a Pi receipt — like a Claude one — names
    # the document the human was shown. ``submit`` omits the member when it does not carry one (the
    # core's staging until PR 13); ``None`` would be the fetch, which refuses every item: never send it.
    digest = payload.get("document_digest")
    stored = confirmation.submit(profile, raw["run_id"], payload["receipt_id"], payload["items"],
                                 answers, document_digest=digest if isinstance(digest, str) else None)
    if stored is None:
        return {"type": "evidence_none"}
    return {"type": "evidence_receipt", "result": stored}


def _child_event(profile: str, raw: dict, payload: dict):
    return bridge.trusted_child_event(profile, raw["run_id"], raw["child_id"], payload)


def _attribution(profile: str, raw: dict, payload: dict):
    return bridge.trusted_attribution(profile, raw["run_id"], payload)


def _evidence_leaf(profile: str, raw: dict, payload: dict):
    return bridge.trusted_evidence_leaf(profile, raw["run_id"], payload)


_HANDLERS = {
    "classify_identity": _classify,
    "governance_context": _governance_context,
    "governance_decision": _governance_decision,
    "audit_prepare": _audit_prepare,
    "audit_reject": _audit_reject,
    "audit_start": _audit_start,
    "audit_identity": _audit_identity,
    "audit_failure": _audit_failure,
    "audit_verdict": _audit_verdict,
    "human_surface": _human_surface,
    "evidence_presentation": _evidence_presentation,
    "evidence_confirmation": _evidence_confirmation,
    "child_event": _child_event,
    "attribution": _attribution,
    "evidence_leaf": _evidence_leaf,
}


def _dispatch(profile: str, raw: dict, payload: dict):
    handler = _HANDLERS.get(raw["operation"])
    if handler is None:
        raise ValueError("unknown private ingress operation")
    return handler(profile, raw, payload)


def main() -> int:
    try:
        raw = json.load(sys.stdin)
        profile = os.environ["EMPIRICA_HOST_PROFILE_ID"]
        result = _dispatch(profile, raw, raw.get("payload", {}))
        json.dump(result, sys.stdout)
        return 0
    except Exception as exc:  # noqa: BLE001 - private adapter boundary fails closed
        print(f"empirica private ingress failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
