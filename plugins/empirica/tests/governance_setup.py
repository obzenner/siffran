"""Explicit test-side host consent through production private transactions.

No runtime default, decoder shim, or production switch grants approval. Tests
must first select their exact graph, then call this helper as a host operator.
"""
from uuid import uuid4

from application import protocol as _proto
from application.governance import transact
from adapters.identity import observe
from core.governance import expected_approval_kind

TEST_INVOCATION = {"host": "test", "interactive": True,
                   "signal": "test", "delegation": False}


def host_runtime_for(profile_id):
    """The runtime provenance a host of ``profile_id`` records at StartRun (``None`` for a profile with
    no external audit runtime), built from the profile's own reviewed policy."""
    policy = _proto.host_profile(profile_id).get("subagents_compatibility")
    if policy is None:
        return None
    root = "/opt/" + policy["package"]
    return {"policy_id": policy["policy_id"],
            "subagents": {"package": policy["package"], "version": policy["reviewed_versions"][-1],
                          "owner_path": root + "/src/extension/index.js", "package_root": root,
                          "preflight_path": root + "/src/api/preflight.js", "source": root + "/index.ts"}}


def invocation_for(profile_id, **changes):
    """``TEST_INVOCATION`` plus the host runtime the profile requires."""
    runtime = host_runtime_for(profile_id)
    return {**TEST_INVOCATION, **changes, **({} if runtime is None else {"host_runtime": runtime})}


AUTHOR = {**observe("anthropic", "claude-sonnet-4-6", source="test-host"),
          "observed_by": "host"}
AUDITOR = {**observe("anthropic", "claude-opus-4-6", source="test-host"),
           "observed_by": "host"}
SIZED_RATIONALE = "sized for the gating claims and one audit retry"


def sized_configure_run(*, budgets, rationale):
    """Build one complete request exactly from explicit caller-supplied fields."""
    return {"kind": "configure_run", "budgets": dict(budgets), "rationale": rationale}


def approve_current(coordinator, run_id, *, author=None, auditor=None):
    c = coordinator
    author = author or AUTHOR
    before = c.handle({"type": "GetRun", "run_id": run_id}, "test-consent-read")["result"]
    g = before["run"]["governance"]
    # scope moved off the author RunView into the private presentation; confirm a graph is
    # selected through the public GetArgument projection instead of governance.scope.
    argument = c.handle({"type": "GetArgument", "run_id": run_id}, "test-consent-graph")["result"]
    if argument.get("type") != "Allow":
        raise AssertionError("explicit test consent requires its actual graph first")
    if g["state"] == "approved":
        return
    # Only size the placeholder; a test that has already issued a sized
    # configure_run keeps its own task-sized ceilings (ADR-0063).
    if g["proposal"]["rationale"] is None:
        # Size the placeholder with the seeded ceilings (which StartRun/limits may have
        # narrowed) plus a rationale; never overwrite a test's own task-sized proposal.
        configured = c.handle({"type": "ObserveAction", "run_id": run_id, "action":
                               sized_configure_run(
                                   budgets=g["proposal"]["budgets"],
                                   rationale=SIZED_RATIONALE)}, "test-consent-configure")
        assert configured["result"]["type"] == "Allow", configured
    ingress = "pi_ui" if c.profile_id.startswith("pi@") else "mcp_elicitation"
    if c.profile_id.startswith("codex"):
        ingress = "unavailable"
    context = {"author": author, "ingress": ingress}
    response = transact(c, run_id, context, context=True)
    assert response["result"]["type"] in {"Allow", "Inert"}, response
    g = response["result"]["run"]["governance"]
    approval_kind = expected_approval_kind(g)
    decision = {"run_id": run_id, "receipt_id": uuid4().hex, "proposal_digest": g["proposal_digest"],
                "plan_revision": g["plan_revision"], "approval_kind": approval_kind}
    if decision["approval_kind"] == "host_ui":
        presented = transact(c, run_id, {**decision, "outcome": "present"})
        assert presented["result"]["type"] == "Allow", presented
        decision["submission"] = {"action": "approve", "configuration": {
            "budgets": g["proposal"]["budgets"]}}
    else:
        decision["outcome"] = "approve"
    approved = transact(c, run_id, decision)
    assert approved["result"]["type"] == "Allow", approved
