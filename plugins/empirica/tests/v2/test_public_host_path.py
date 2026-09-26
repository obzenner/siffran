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


class PublicHostPathTests(ConformanceCase):
    def test_public_tools_plus_private_host_observation_converge(self):
        drv = self.bind_driver(
            "D11", "public-host-path",
            "Public model tools drive every author transition; host-only ingress binds audit")
        run_id = self.start_run(drv, goal="Prove one host-reachable Empirica path.")
        tools = PublicTools("claude-code@2.1.278",
                            dispatch=lambda request, _profile: drv.request(request))

        def observe(action: dict) -> dict:
            out = tools.call("empirica_observe", {"run_id": run_id, "action": action})
            self.assertFalse(out["isError"], out)
            return out["structuredContent"]

        observe(action_route(reason="route before investigation"))
        graph = canonical_graph(n_claims=1)
        root_id = graph["root"]
        observe(action_graph(graph))
        self.require_governance_approved(drv, run_id)
        observe(action_investigate())
        observe(action_research(
            claim_id=root_id, source_kind="code", result="supports",
            payload={"source_ref": "plugins/empirica/core/evaluation.py"}))

        drv.workspace_write("src/reachable.py", b"print('reachable')\n")
        drv.harness_complete("python src/reachable.py", 0)
        observe(action_spike_request(
            claim_id=root_id,
            command="python src/reachable.py",
            dependent_files=["src/reachable.py"],
        ))
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

        argument_result = tools.call(
            "empirica_read", {"run_id": run_id, "operation": "GetArgument"}
        )["structuredContent"]
        argument = argument_result["argument"]
        evidence_ids = [artifact_id for claim in argument["claims"] if claim["gating"]
                        for artifact_id in claim["active_evidence_ids"]]
        self.require_trusted_audit_attribution(
            drv, run_id, child_id, evidence_ids, variant="decorrelated")
        verdict = self.build_audit_verdict_payload(
            drv, run_id, verdict="pass", scope_review="pass")
        admitted = drv.trusted_audit_verdict(run_id, child_id, verdict)
        self.assertEqual(admitted["result"]["type"], "Allow")

        final = tools.call("report_convergence", {"run_id": run_id})
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
