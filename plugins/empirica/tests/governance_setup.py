"""Explicit test-side host consent through production private transactions.

No runtime default, decoder shim, or production switch grants approval. Tests
must first select their exact graph, then call this helper as a host operator.
"""
from uuid import uuid4

from application.governance import transact

AUTHOR = {"provider_id": "anthropic", "model_id": "claude-sonnet-4-6"}
AUDITOR = {"provider_id": "anthropic", "model_id": "claude-opus-4-6"}


def approve_current(coordinator, run_id, *, author=None, auditor=None, singleton=False):
    c = coordinator
    author, auditor = author or AUTHOR, auditor or AUDITOR
    before = c.handle({"type": "GetRun", "run_id": run_id}, "test-consent-read")["result"]
    g = before["run"]["governance"]
    if g["scope"] is None:
        raise AssertionError("explicit test consent requires its actual graph first")
    if g["state"] == "approved":
        return
    configured = c.handle({"type": "ObserveAction", "run_id": run_id, "action": {
        "kind": "configure_run", "auditor": auditor, "allow_same_model": singleton}}, "test-consent-configure")
    assert configured["result"]["type"] == "Allow", configured
    ingress = "pi_ui" if c.profile_id.startswith("pi@") else "mcp_elicitation"
    if c.profile_id.startswith("codex"):
        ingress = "unavailable"
    context = {"inventory": {"members": [author] if singleton else [author, auditor],
               "source": "pi_registry" if ingress == "pi_ui" else "operator_declared",
               "complete": True, "authorized": True}, "author": author, "ingress": ingress}
    response = transact(c, run_id, context, context=True)
    assert response["result"]["type"] in {"Allow", "Inert"}, response
    g = response["result"]["run"]["governance"]
    decision = {"run_id": run_id, "receipt_id": uuid4().hex, "proposal_digest": g["proposal_digest"],
                "plan_revision": g["plan_revision"], "approval_kind": "auto" if g["control_mode"] == "auto" else "host_ui",
                "outcome": "approve"}
    if decision["approval_kind"] == "host_ui":
        presented = transact(c, run_id, {**decision, "outcome": "present"})
        assert presented["result"]["type"] == "Allow", presented
    approved = transact(c, run_id, decision)
    assert approved["result"]["type"] == "Allow", approved
