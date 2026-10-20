"""D11: one complete positive path through the shared public model tools."""
from __future__ import annotations

from adapters.public_tools import PublicTools
from assertions import (
    ConformanceCase,
    action_child_reserve,
    action_freeze,
    action_graph,
    action_investigate,
    action_research,
    action_route,
    action_spike_request,
    build_child_event_payload,
    canonical_graph,
    observe_action,
)
from application.protocol import host_profile_id  # noqa: E402
CLAUDE_PROFILE = host_profile_id("claude-code")


class PublicHostPathTests(ConformanceCase):
    def start_run(self, drv, *, goal=None, **kw):
        return super().start_qualified_run(drv, goal=goal, **kw)

    def test_public_tools_plus_private_host_observation_converge(self):
        drv = self.bind_driver(
            "D11", "public-host-path",
            "Public model tools drive every author transition; host-only ingress binds audit")
        run_id = self.start_run(drv, goal="Prove one host-reachable Empirica path.")
        tools = PublicTools(CLAUDE_PROFILE,
                            dispatch=lambda request, _profile: drv.request(request))

        def observe(action: dict) -> dict:
            out = tools.call_internal("empirica_observe", {"run_id": run_id, "action": action})
            self.assertFalse(out["isError"], out)
            return out["structuredContent"]

        observe(action_route(reason="route before investigation"))
        graph = canonical_graph(n_claims=1)
        root_id = graph["root"]
        observe(action_graph(graph))
        self.require_governance_approved(drv, run_id)
        observe(action_investigate())
        observe(action_research(claim_id=root_id, source_kind="code", result="supports"))

        drv.workspace_write("src/reachable.py", b"print('reachable')\n")
        drv.generic_harness_complete(0)
        observe(action_spike_request(
            claim_id=root_id, paths=["src/reachable.py"]))
        artifacts = self.get_argument_artifacts(drv, run_id)
        request = self.find_one_argument_artifact(
            artifacts, kind="spike_request", claim_id=root_id)
        result = self.find_one_argument_artifact(
            artifacts, kind="spike", claim_id=root_id, active=True)
        self.assert_spike_request_artifact(
            request, claim_id=root_id, paths=["src/reachable.py"])
        self.assert_spike_result_artifact(
            result, request=request, claim_id=root_id, exit_code=0,
            spike_gate="PASS", active=True)
        observe(action_freeze())

        # Concrete child reservation is a host protocol operation, never a model tool action.
        reserve = self.dispatch(drv, observe_action(run_id=run_id, action=action_child_reserve(
            purpose="audit", resource_class="audit",
            role_profile="empirica:empirica-auditor", execution="foreground")))
        child = next(c for c in reserve["result"]["run"]["children"] if c["purpose"] == "audit")
        child_id = child["child_id"]
        native_id = "host-auditor-1"
        self.assertEqual(
            drv.trusted_child_event(
                run_id, child_id,
                build_child_event_payload("launching", native_id=native_id)
            )["result"]["type"],
            "Allow",
        )
        self.assertEqual(
            drv.trusted_child_event(
                run_id, child_id,
                build_child_event_payload("pending", native_id=native_id)
            )["result"]["type"],
            "Allow",
        )

        self.require_trusted_audit_attribution(
            drv, run_id, child_id, variant="distinct")
        verdict = self.build_audit_verdict_payload(
            drv, run_id, verdict="pass", scope_review="pass")
        admitted = drv.trusted_audit_verdict(run_id, child_id, verdict)
        self.assertEqual(admitted["result"]["type"], "Allow")

        final = tools.call_internal("report_convergence", {"run_id": run_id})
        self.assertFalse(final["isError"], final)
        result = final["structuredContent"]
        self.assertEqual(result["type"], "Allow")
        self.assertTrue(result["converged"])
        self.assertEqual(result["run"]["status"], "converged")

        # The model-visible tool registry cannot express any trusted ingress.
        tool_text = repr(tools.definitions())
        for private in ("evidence_leaf", "attribution", "child_event", "audit_verdict",
                        "capability_ref"):
            self.assertNotIn(private, tool_text)
