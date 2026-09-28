#!/usr/bin/env python3
"""QUAL-1 context-economy guards (C1–C6): the agent completes every normal step from tool
input schemas + the last RunView + the skill, and the private governance presentation never
reaches the model.

Design invariant under test: closed model-facing schemas carry every action shape; the one
author RunView has a single shape for every audience; dialog and scope live only in the
private governance presentation.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters import public_tools as _pt
from adapters.public_tools import PublicTools
from application import protocol as _proto
from application.v2 import compose
from core.evaluation import valid_graph
from core.projection import project_presentation
from test_d7_transactions import Runs, Artifacts, Workspace, Harness
from governance_setup import AUTHOR, TEST_INVOCATION, approve_current
from core.canonical import canonical_digest


def build_child_event_payload(state: str, *, native_id: str | None = None,
                              result_digest: str | None = None) -> dict:
    """Minimal valid closed child_event payload (mirrors tests/v2/assertions without pulling the
    v2 driver package onto the path)."""
    return {"state": state, "native_id": native_id,
            "fingerprint": canonical_digest({"state": state, "native_id": native_id}),
            "result_digest": result_digest}

PROFILE = "pi@0.84.1+pi-subagents@0.50.0"
CODEX = "codex-cli@0.146.0"
CLAUDE = "claude-code@2.1.278"
GRAPH = {"root": "C0", "claims": [{"id": "C0", "text": "supplied uncertainty", "gating": True,
                                   "kind": "ordinary"}], "edges": []}
CONTEXT = {"author": AUTHOR, "ingress": "pi_ui"}
PRIVATE_KEYS = ("presentation", "dialog", "scope")


def _claim(cid, kind="ordinary"):
    return {"id": cid, "text": f"Claim {cid}", "kind": kind, "gating": True}



def contains_private(value) -> bool:
    if isinstance(value, dict):
        if any(key in value for key in PRIVATE_KEYS):
            return True
        return any(contains_private(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_private(item) for item in value)
    return False


def _object_capable(node: dict) -> bool:
    """A schema node can validate an object when it declares an object-shaping keyword
    (``properties``, ``required``, ``additionalProperties``, ``patternProperties``) or a
    ``type`` that is ``object`` or an object-capable union (e.g. ``["object", "null"]``). Such a
    node must close extra keys with ``additionalProperties: false``."""
    if not isinstance(node, dict):
        return False
    if any(key in node for key in
           ("properties", "required", "additionalProperties", "patternProperties")):
        return True
    kind = node.get("type")
    return kind == "object" or (isinstance(kind, list) and "object" in kind)


def closure_offenders(node, path="", found=None):
    """Return the paths of every open (non-closed) or unresolved object-capable schema node.

    A model-facing schema node is an offender when any of the following holds:
      * it is object-capable (``_object_capable``) but does not close with
        ``additionalProperties: false``;
      * it declares a ``properties`` map with an unconstrained property schema ``{}`` (an open
        property that admits any object);
      * it carries a ``$ref`` -- a projected model-facing schema must be fully dereferenced, so
        an unresolved ``$ref`` is itself a failure.
    Combinator branches (oneOf/anyOf/allOf), ``items``, ``$defs`` and property schemas are
    reached by the generic recursion below and flagged at their own path.
    """
    found = [] if found is None else found
    if isinstance(node, list):
        for i, item in enumerate(node):
            closure_offenders(item, f"{path}[{i}]", found)
        return found
    if not isinstance(node, dict):
        return found
    if "$ref" in node:
        found.append(path)
    if _object_capable(node) and node.get("additionalProperties") is not False:
        found.append(path)
    # An unconstrained property schema {} is open even inside a closed parent.
    props = node.get("properties")
    if isinstance(props, dict):
        for key, sub in props.items():
            if isinstance(sub, dict) and not sub:
                found.append(f"{path}.properties.{key}")
    for key, item in node.items():
        closure_offenders(item, f"{path}.{key}", found)
    return found


class SchemaCompletenessTests(unittest.TestCase):
    """C1: every model-facing tool schema object is closed (additionalProperties: false). No
    exception list. The closure predicate itself is guarded by mutation negative controls."""

    def test_closure_predicate_rejects_open_and_accepts_closed_objects(self):
        # Mutation negative controls: both open shapes the design forbids must be flagged.
        self.assertTrue(closure_offenders({"type": "object", "additionalProperties": True}),
                        "additionalProperties: true must be flagged as open")
        self.assertTrue(closure_offenders({"type": "object", "properties": {}}),
                        "properties: {} without additionalProperties: false must be flagged")
        self.assertTrue(closure_offenders({"type": "object"}), "bare object must be flagged")
        self.assertTrue(closure_offenders({"type": ["object", "null"]}),
                        "object-capable type union must be flagged")
        # Object-capable via a shaping keyword alone (no explicit type) must be flagged.
        self.assertEqual(closure_offenders({"properties": {"x": {"type": "string"}}}), [""],
                        "a schema with properties but no additionalProperties: false is open")
        # An unconstrained property schema {} inside a closed parent is flagged at its path.
        self.assertEqual(
            closure_offenders({"type": "object", "additionalProperties": False,
                               "properties": {"x": {}}}),
            [".properties.x"],
            "an unconstrained {} property schema is open even under a closed parent")
        # An unresolved $ref in a projected model-facing schema is itself a failure.
        self.assertIn(".properties.x", closure_offenders(
            {"type": "object", "additionalProperties": False,
             "properties": {"x": {"$ref": "#/$defs/foo"}}}))
        # A nested open object inside a closed parent is flagged at its own path.
        parent = {"type": "object", "additionalProperties": False,
                  "properties": {"child": {"type": "object", "additionalProperties": True}}}
        self.assertEqual(closure_offenders(parent), [".properties.child"])
        # An open branch inside a combinator is flagged.
        self.assertTrue(closure_offenders({"oneOf": [
            {"type": "object", "additionalProperties": False, "properties": {}},
            {"type": "object"}]}))
        # A fully closed object (recursively) passes.
        self.assertEqual(closure_offenders(
            {"type": "object", "additionalProperties": False,
             "properties": {"a": {"type": "string"},
                            "b": {"type": "object", "additionalProperties": False,
                                  "properties": {}}}}), [])

    def test_claude_and_pi_model_schemas_have_no_open_objects(self):
        opened: list[str] = []
        # Claude/Codex model-facing definitions.
        for profile in (CLAUDE, CODEX, PROFILE):
            tools = PublicTools(profile, dispatch=lambda r, p: {
                "protocol": "empirica/v2", "request_id": r["request_id"],
                "result": {"type": "Inert", "reason": "no_run"}})
            for definition in tools.definitions():
                opened += [f"{profile}:{definition['name']}{p}"
                           for p in closure_offenders(definition["inputSchema"])]
        # The exact Pi registration schemas (host_handle) from the shared projection artifact.
        artifact = json.loads(_pt._PUBLIC_TOOL_ARTIFACT.read_text(encoding="utf-8"))
        for family in ("model", "host_handle"):
            for name, schema in artifact["schemas"][family].items():
                opened += [f"pi:{family}:{name}{p}" for p in closure_offenders(schema)]
        self.assertEqual(opened, [], f"open model-facing schema objects: {opened}")

    def test_graph_action_payload_is_the_closed_ssot_shape(self):
        observe = PublicTools(CLAUDE).definitions()[1]["inputSchema"]
        graph = next(row for row in observe["properties"]["action"]["oneOf"]
                     if row["properties"]["kind"].get("const") == "graph")
        payload = graph["properties"]["payload"]
        self.assertEqual(payload["additionalProperties"], False)
        self.assertEqual(set(payload["required"]), {"root", "claims", "edges"})


class SchemaCoreAgreementTests(unittest.TestCase):
    """C2: every graph accepted by valid_graph passes the A1 schema; malformed cases fail both."""

    def _graph_schema(self):
        defs = _proto._REQUEST_SCHEMA["$defs"]
        return {"$schema": "https://json-schema.org/draft/2020-12/schema",
                "$defs": defs, "$ref": "#/$defs/graphPayload"}

    def _valid(self, instance) -> bool:
        try:
            jsonschema.validate(instance, self._graph_schema())
            return True
        except jsonschema.ValidationError:
            return False

    def test_valid_graphs_pass_both_core_and_schema(self):
        good = [
            {"root": "C0", "claims": [_claim("C0")], "edges": []},
            {"root": "C0", "claims": [_claim("C0"), _claim("C1", "needs-decision")],
             "edges": [{"from": "C0", "to": "C1", "type": "SupportedBy"}]},
            _proto._PUBLIC_CONTRACT["bootstrap"]["actions"]["graph"]["example"]["payload"],
        ]
        for graph in good:
            self.assertTrue(valid_graph(graph), graph)
            self.assertTrue(self._valid(graph), graph)

    def test_structurally_malformed_graphs_fail_the_schema(self):
        # Structural defects the closed schema owns: missing fields, unknown edge type, extra keys.
        malformed = [
            {"malformed": True},
            {"root": "C0", "claims": [], "edges": []},
            {"root": "C0", "claims": [_claim("C0")],
             "edges": [{"from": "C0", "to": "C0", "type": "InContextOf"}]},
            {"root": "C0", "claims": [{**_claim("C0"), "extra": 1}], "edges": []},
        ]
        for graph in malformed:
            self.assertFalse(self._valid(graph), graph)
            self.assertFalse(valid_graph(graph), graph)

    def test_semantic_defects_stay_core_only(self):
        # Uniqueness, membership, acyclicity, reachability are core-only: the closed schema admits
        # them (defence in depth, never a replacement) while valid_graph rejects them.
        core_only = [
            {"root": "C0", "claims": [_claim("C0")],  # unknown endpoint
             "edges": [{"from": "C0", "to": "C1", "type": "SupportedBy"}]},
            {"root": "C0", "claims": [_claim("C0"), _claim("C1")],  # duplicate edge
             "edges": [{"from": "C0", "to": "C1", "type": "SupportedBy"},
                       {"from": "C0", "to": "C1", "type": "SupportedBy"}]},
            {"root": "C0", "claims": [_claim("C0"), _claim("C1")], "edges": []},  # detached
        ]
        for graph in core_only:
            self.assertTrue(self._valid(graph), graph)
            self.assertFalse(valid_graph(graph), graph)


class _ServiceHarness(unittest.TestCase):
    def setUp(self):
        self.runs, self.artifacts = Runs(), Artifacts()
        self.service = compose(Workspace(), Harness(), self.runs, self.artifacts, None, PROFILE, {}, None)
        self.run_id = self.req({"type": "StartRun", "goal": "governed task",
                                "selector": {"project": "p", "session": "s"}})["run"]["id"]

    def req(self, command):
        if command.get("type") == "StartRun":
            command = {"invocation": dict(TEST_INVOCATION), **command}
        return self.service.dispatch({"protocol": "empirica/v2", "request_id": "t",
                                      "command": command})["result"]

    def action(self, kind, **kwargs):
        return self.req({"type": "ObserveAction", "run_id": self.run_id,
                         "action": {"kind": kind, **kwargs}})

    def to_pending(self):
        self.action("route", reason="supplied context")
        self.action("graph", payload=copy.deepcopy(GRAPH))
        self.action("configure_run")


class AudienceTests(_ServiceHarness):
    """C4: no public/model-facing envelope carries presentation/dialog/scope; private
    context/decision run-bearing responses always carry the full presentation."""

    def assertPublic(self, envelope, label):
        self.assertFalse(contains_private(envelope), f"{label} leaked a private governance field")

    def test_public_envelopes_never_carry_presentation_fields(self):
        # graphless start + graphless GetRun
        self.assertPublic(self.req({"type": "GetRun", "run_id": self.run_id}), "graphless GetRun")
        # Block (graph.missing) via Evaluate on a graphless run
        self.assertPublic(self.req({"type": "EvaluateRun", "run_id": self.run_id,
                                    "intent": "report_convergence"}), "graph.missing Block")
        self.to_pending()
        self.assertPublic(self.req({"type": "GetRun", "run_id": self.run_id}), "pending GetRun")
        self.assertPublic(self.req({"type": "GetArgument", "run_id": self.run_id}), "pending GetArgument")
        self.assertPublic(self.req({"type": "RestoreRun", "run_id": self.run_id}), "RestoreRun")
        self.assertPublic(self.service.compact(), "compact")
        # Inert (unknown run id)
        self.assertPublic(self.req({"type": "GetRun", "run_id": "er2:missing:0"}), "unknown-run read")
        approve_current(self.service._coordinator, self.run_id)
        self.assertPublic(self.req({"type": "GetRun", "run_id": self.run_id}), "approved GetRun")
        self.assertPublic(self.req({"type": "GetArgument", "run_id": self.run_id}), "approved GetArgument")
        # terminal
        stopped = self.req({"type": "EvaluateRun", "run_id": self.run_id, "intent": "stop"})
        self.assertPublic(stopped, "terminal stop")
        self.assertPublic(self.req({"type": "GetRun", "run_id": self.run_id}), "terminal GetRun")

    def test_private_context_and_decision_carry_the_full_presentation(self):
        self.to_pending()
        context = self.service.trusted_governance_context(
            run_id=self.run_id, payload=copy.deepcopy(CONTEXT))["result"]
        self.assertEqual(context["type"], "Allow")
        self.assertIn("presentation", context)
        self.assertEqual(context["presentation"]["dialog"]["goal"], "governed task")
        self.assertEqual(context["presentation"]["scope"]["root"], "C0")
        # The author RunView inside the very same private response still has one public shape.
        self.assertFalse(contains_private(context["run"]),
                         "the author RunView inside a private response must stay public")
        g = context["run"]["governance"]
        decision = {"run_id": self.run_id, "receipt_id": "r-" + str(g["plan_revision"]),
                    "proposal_digest": g["proposal_digest"], "plan_revision": g["plan_revision"],
                    "approval_kind": "host_ui", "outcome": "present"}
        presented = self.service.trusted_governance_decision(
            run_id=self.run_id, payload=decision)["result"]
        self.assertIn("presentation", presented)
        self.assertEqual(presented["presentation"]["dialog"]["goal"], "governed task")

    def test_configure_after_govern_and_a_misbehaving_governor(self):
        self.to_pending()
        # A well-behaved governor whose returned result is reprojected/filtered to the author view.
        good = PublicTools(PROFILE, dispatch=self._observe_dispatch,
                           govern=lambda result: result)
        out = good.call_internal("empirica_observe", {"run_id": self.run_id,
                                             "action": {"kind": "configure_run"}})
        self.assertFalse(out["isError"])
        self.assertPublic(out["structuredContent"], "configure_run after govern")
        self.assertNotIn("presentation", out["structuredContent"])
        # A misbehaving governor that reintroduces presentation becomes a typed closed error.
        def leak(result):
            result["presentation"] = project_presentation(self.service._coordinator.last_snapshot)
            return result
        bad = PublicTools(PROFILE, dispatch=self._observe_dispatch, govern=leak)
        leaked = bad.call("empirica_observe", {"run_id": self.run_id,
                                              "action": {"kind": "configure_run"}})
        self.assertTrue(leaked["isError"])
        self.assertNotIn("structuredContent", leaked)
        self.assertNotIn("presentation", json.dumps(leaked))

    def test_codex_configure_results_stay_public(self):
        # Codex uses the shared HostGovernance built by the shared MCP server. Its auto and
        # unavailable results carry no presentation, and a private Fault stays public too.
        from adapters.governance import HostGovernance
        self.to_pending()
        view = self.req({"type": "GetRun", "run_id": self.run_id})["run"]
        allow = {"type": "Allow", "converged": False, "run": view}
        # Unavailable ingress (no elicit) on the Codex profile fails closed without presentation.
        codex_mediator = HostGovernance(CODEX, elicit=None)
        self.assertPublic(codex_mediator(allow), "codex unavailable mediation")
        # The shared public tool wrapper reprojects a govern result to the author view.
        tools = PublicTools(CODEX, dispatch=self._observe_dispatch, govern=lambda r: r)
        out = tools.call_internal("empirica_observe", {"run_id": self.run_id,
                                             "action": {"kind": "configure_run"}})
        self.assertFalse(contains_private(out.get("structuredContent", {})))

    def _observe_dispatch(self, request, profile_id):
        return self.service.dispatch(request)


class FinalizerTests(_ServiceHarness):
    """B6/C4: the shared HostGovernance finalizer strips presentation and fails closed on any
    thrown ingress/UI error, never returning an untyped exception or a private object."""

    def test_thrown_ingress_becomes_typed_fail_closed_block_without_presentation(self):
        from adapters.governance import HostGovernance
        self.to_pending()
        view = self.req({"type": "GetRun", "run_id": self.run_id})["run"]
        allow = {"type": "Allow", "converged": False, "run": view}

        def boom(*_args, **_kwargs):
            raise RuntimeError("ingress down")

        mediator = HostGovernance(PROFILE, elicit=lambda *_a, **_k: {"action": "accept",
                                  "content": {}},
                                  context_ingress=boom, decision_ingress=boom)
        out = mediator(allow)
        self.assertEqual(out["type"], "Block")
        self.assertEqual(out["reasons"][0]["code"], "governance.approval_unavailable")
        self.assertFalse(contains_private(out))

    def test_finalizer_strips_presentation_from_a_terminal_private_return(self):
        from adapters.governance import HostGovernance
        self.to_pending()
        view = self.req({"type": "GetRun", "run_id": self.run_id})["run"]
        allow = {"type": "Allow", "converged": False, "run": view}
        # Real ingress + an accepting elicit drives approval; the returned model result is stripped.
        mediator = HostGovernance(PROFILE, elicit=lambda *_a, **_k: {"action": "accept",
                                  "content": {}})
        out = mediator(allow)
        self.assertFalse(contains_private(out), out)


class SizeBudgetTests(_ServiceHarness):
    """C5: the model-visible tool result text at each host's post-mediation return, and the
    author RunView at pending/approved/audit-pending/terminal, stay under a ceiling derived from
    the largest measured state.

    Measured objects (model-visible result text = the ``content[0].text`` a host returns to the
    model; UTF-8 characters):
      * post-mediation ``configure_run`` result via the shared ``PublicTools.call`` with an
        injected ``HostGovernance`` wired to this service — the approve, dismiss and
        locked-confirmation (edit -> confirm) returns, i.e. the model-facing text AFTER host
        mediation, not the pre-mediation service result the previous C5 measured.
      * the pending / approved / terminal author RunView read text.
      * the audit-pending author RunView read text, driven through the real lifecycle
        (research -> freeze -> reserved -> launching -> pending audit child).

    Wire-envelope bytes are tracked separately from this ceiling. The MCP wire result carries only
    rendered text; validated JSON is available solely through the test/host-internal inspection
    path and is not serialized onto Claude/Codex's MCP result.

    Measured sizes at authoring (chars of model-visible rendered text):
        pending RunView read            855
        approved RunView read           872
        audit-pending RunView read      676
        terminal RunView report         928
        audit-pending Block             743
        configure approve result        872
        configure dismiss result       1059
        configure edit->confirm result  872
    Largest measured state = 1059 (configure dismiss); CEILING = 1200 leaves 141 chars headroom.
    Re-attaching the legacy presentation (dialog + scope) to the pending RunView pushes the
    JSON to 3577 chars, well past the ceiling, so the reduction is guarded rather than asserted
    only to be > 0.
    """

    CEILING = 1200
    ROLE_PROFILE = "claude-code@2.1.278"

    @staticmethod
    def _size(obj) -> int:
        return len(json.dumps(obj, sort_keys=True, separators=(",", ":")))

    def _text_size(self, tool_result) -> int:
        return len(tool_result["content"][0]["text"])

    def _mediator(self, elicit):
        from adapters.governance import HostGovernance
        return HostGovernance(
            PROFILE, elicit=elicit,
            context_ingress=lambda _p, rid, ctx: self.service.trusted_governance_context(
                run_id=rid, payload=ctx),
            decision_ingress=lambda _p, rid, payload: self.service.trusted_governance_decision(
                run_id=rid, payload=payload))

    def _configure(self, elicit):
        """Run one post-mediation configure_run through the shared PublicTools + injected
        HostGovernance and return (tool_result, dialog_count). The dialog count is the number of
        elicitations the mediator actually issued."""
        calls = {"n": 0}

        def counted(*args, **kwargs):
            calls["n"] += 1
            return elicit(*args, **kwargs)

        tools = PublicTools(PROFILE, dispatch=self._observe_dispatch, govern=self._mediator(counted))
        out = tools.call_internal("empirica_observe",
                         {"run_id": self.run_id, "action": {"kind": "configure_run"}})
        self.assertFalse(contains_private(out.get("structuredContent", {})))
        return out, calls["n"]

    def _observe_dispatch(self, request, profile_id):
        return self.service.dispatch(request)

    def _reach_audit_pending(self):
        """Drive the run to a pending audit child through the real lifecycle."""
        self.to_pending()
        approve_current(self.service._coordinator, self.run_id)
        self.action("investigate")
        self.action("research", claim_id="C0", source_kind="code", result="supports",
                    payload={"source_ref": "plugins/empirica/tests/test_context_economy.py",
                             "citation": "observed directly"})
        self.action("freeze")
        reserved = self.action("child_reserve", purpose="audit", resource_class="audit",
                               role_profile=self.ROLE_PROFILE, execution="foreground")
        child_id = reserved["run"]["children"][-1]["child_id"]
        for state in ("launching", "pending"):
            self.service.trusted_child_event(
                run_id=self.run_id, child_id=child_id,
                event=build_child_event_payload(state, native_id="n-pre"))

    def _read_text(self) -> dict:
        tools = PublicTools(PROFILE, dispatch=self._observe_dispatch)
        return tools.call_internal("empirica_read", {"operation": "GetRun", "run_id": self.run_id})

    def test_post_mediation_and_runview_text_stay_under_the_ceiling(self):
        # Canonical form answers (approve, dismiss, edit) drive the real mediation; each intended
        # transition and dialog count is asserted BEFORE the size is measured, so a size that
        # passes without reaching approval/confirmation can no longer masquerade as coverage.
        def approve(*_a, **_k):
            return {"action": "accept", "content": {}}

        def dismiss(*_a, **_k):
            return {"action": "cancel"}

        # approve -> Allow + governance approved + exactly one dialog.
        self.to_pending()
        out, dialogs = self._configure(approve)
        sc = out["structuredContent"]
        self.assertEqual(sc["type"], "Allow", "approve must return Allow")
        self.assertIs(sc["converged"], False)
        self.assertEqual(sc["run"]["governance"]["state"], "approved")
        self.assertEqual(dialogs, 1, "approve is a single dialog")
        self.assertLess(self._text_size(out), self.CEILING, "configure approve")

        # dismiss -> the documented pending Block + exactly one dialog.
        self.setUp()
        self.to_pending()
        out, dialogs = self._configure(dismiss)
        sc = out["structuredContent"]
        self.assertEqual(sc["type"], "Block", "dismiss must return Block")
        self.assertEqual(sc["reasons"][0]["code"], "governance.approval_unavailable")
        self.assertEqual(sc["run"]["governance"]["state"], "pending")
        self.assertEqual(dialogs, 1, "dismiss is a single dialog")
        self.assertLess(self._text_size(out), self.CEILING, "configure dismiss")

        # edit -> locked confirmation -> Allow + approved + edited budget + exactly two dialogs.
        self.setUp()
        self.to_pending()
        confirm = iter([
            {"action": "accept", "content": {"max_passes": 6}},
            {"action": "accept", "content": {}}])
        out, dialogs = self._configure(lambda *_a, **_k: next(confirm))
        sc = out["structuredContent"]
        self.assertEqual(sc["type"], "Allow", "edit->confirm must return Allow")
        self.assertEqual(sc["run"]["governance"]["state"], "approved")
        self.assertEqual(sc["run"]["governance"]["proposal"]["budgets"]["max_passes"], 6,
                         "the confirmed configuration must carry the edited budget")
        self.assertEqual(dialogs, 2, "edit then locked confirmation is two dialogs")
        self.assertLess(self._text_size(out), self.CEILING, "configure locked confirmation")

    def test_runview_states_stay_under_the_ceiling(self):
        self.to_pending()
        pending = self._read_text()
        self.assertLess(self._text_size(pending), self.CEILING, "pending RunView")
        approve_current(self.service._coordinator, self.run_id)
        self.assertLess(self._text_size(self._read_text()), self.CEILING, "approved RunView")
        tools = PublicTools(PROFILE, dispatch=self._observe_dispatch)
        terminal = tools.call_internal("report_convergence", {"run_id": self.run_id, "intent": "stop"})
        self.assertLess(self._text_size(terminal), self.CEILING, "terminal RunView")

    def test_audit_pending_runview_stays_under_the_ceiling(self):
        self._reach_audit_pending()
        audit_read = self._read_text()
        self.assertLess(self._text_size(audit_read), self.CEILING, "audit-pending RunView read")
        blocked = PublicTools(PROFILE, dispatch=self._observe_dispatch).call_internal(
            "report_convergence", {"run_id": self.run_id, "intent": "report_convergence"})
        run = blocked["structuredContent"]["run"]
        self.assertEqual([c["state"] for c in run["children"]], ["pending"])
        self.assertLess(self._text_size(blocked), self.CEILING, "audit-pending Block")

    def test_reattaching_legacy_presentation_exceeds_the_budget(self):
        self.to_pending()
        pending = self._read_text()
        self.assertLess(self._text_size(pending), self.CEILING)
        result = pending["structuredContent"]
        self.assertFalse(contains_private(result))
        pres = project_presentation(self.service._coordinator.last_snapshot)
        # Legacy shape: dialog + scope re-attached onto the author RunView governance block.
        result = copy.deepcopy(result)
        result["run"]["governance"]["dialog"] = pres["dialog"]
        result["run"]["governance"]["scope"] = pres["scope"]
        legacy_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
        self.assertGreaterEqual(len(legacy_json), self.CEILING,
                                "re-attaching the legacy presentation must exceed the ceiling")


class NextActionReachabilityTests(_ServiceHarness):
    """C3: every registered next_actions directive resolves through the canonical ``surface``
    field the contract now owns (QUAL-1-F2 D1a/D1b) -- there is no hand-maintained directive map
    here. A model surface (``empirica_observe`` author action, ``empirica_read`` operation, or a
    ``report_convergence`` intent) resolves only when its named action/operation/intent is present
    in the projected tool schema (closure proved by C1); a host/human ``owner`` surface resolves
    when it names an operation and claims no model tool. Resolving the whole registry (a superset
    of every graphless/pending/approved/research/spike/audit/terminal view) is stronger than the
    old ``emitted <= registry`` membership check.

    Negative controls prove the guard bites: removing ``stop`` from the report tool breaks
    ``residual.accept``; a surface naming a missing author action fails; an entry carrying no
    surface fails.
    """

    def _surfaces(self):
        definitions = {d["name"]: d["inputSchema"] for d in PublicTools(CLAUDE).definitions()}
        author_kinds = {row["properties"]["kind"]["const"]
                        for row in definitions[_pt.OBSERVE_TOOL]["properties"]["action"]["oneOf"]}
        read_ops = set(definitions[_pt.READ_TOOL]["properties"]["operation"]["enum"])
        report_intents = set(definitions[_pt.REPORT_TOOL]["properties"]["intent"]["enum"])
        return author_kinds, read_ops, report_intents

    @staticmethod
    def _allowed_host_ops():
        """Closed vocabulary of host-owned surface operations: contract commands,
        actions.host, actions.trusted, plus the canonical private governance operations."""
        c = _proto._PUBLIC_CONTRACT
        ops: set[str] = set(c.get("commands", []))
        actions = c.get("actions", {})
        ops |= set(actions.get("host", {}))
        ops |= set(actions.get("trusted", {}))
        from application.v2 import PRIVATE_GOVERNANCE_OPERATIONS
        ops |= set(PRIVATE_GOVERNANCE_OPERATIONS)
        return ops

    @staticmethod
    def _surface_of(directive):
        entry = _proto._PUBLIC_CONTRACT["next_actions"].get(directive)
        return entry.get("surface") if isinstance(entry, dict) else None

    def _resolve(self, surface, author_kinds, read_ops, report_intents, allowed_host_ops):
        """Resolve a canonical ``surface`` object to a (kind, target) pair, or None. A model
        surface resolves only when its named action/operation/intent is present in the projected
        tool schema; a host owner surface resolves only when it names a known host operation; a
        human owner surface resolves only when it names ``decision``."""
        if not isinstance(surface, dict):
            return None
        tool = surface.get("tool")
        if tool == "empirica_observe":
            action = surface.get("action")
            return ("observe", action) if action in author_kinds else None
        if tool == "empirica_read":
            operation = surface.get("operation")
            return ("read", operation) if operation in read_ops else None
        if tool == "report_convergence":
            intent = surface.get("intent")
            return ("report", intent) if intent in report_intents else None
        owner = surface.get("owner")
        if owner == "host":
            operation = surface.get("operation")
            if not isinstance(operation, str) or not operation:
                return None
            if operation not in allowed_host_ops:
                return None
            return (owner, operation)
        if owner == "human":
            operation = surface.get("operation")
            return (owner, operation) if operation == "decision" else None
        return None

    def _resolve_directive(self, directive, author_kinds, read_ops, report_intents,
                           allowed_host_ops):
        return self._resolve(self._surface_of(directive), author_kinds, read_ops,
                             report_intents, allowed_host_ops)

    def test_every_registered_directive_resolves_via_its_surface(self):
        registry = sorted(_proto._PUBLIC_CONTRACT["next_actions"])
        author_kinds, read_ops, report_intents = self._surfaces()
        allowed_host_ops = self._allowed_host_ops()
        unresolved = [d for d in registry
                      if self._resolve_directive(d, author_kinds, read_ops, report_intents,
                                                allowed_host_ops) is None]
        self.assertEqual(unresolved, [], f"unresolved registered directives: {unresolved}")
        # Every registered directive carries a surface; nothing resolves by hand.
        missing = [d for d in registry if self._surface_of(d) is None]
        self.assertEqual(missing, [], f"directives without a canonical surface: {missing}")

    def test_owner_surfaces_name_an_operation_and_claim_no_model_tool(self):
        author_kinds, read_ops, report_intents = self._surfaces()
        allowed_host_ops = self._allowed_host_ops()
        for directive in _proto._PUBLIC_CONTRACT["next_actions"]:
            surface = self._surface_of(directive)
            if not (isinstance(surface, dict) and surface.get("owner") in ("host", "human")):
                continue
            self.assertNotIn("tool", surface, f"{directive} owner surface must not claim a tool")
            operation = surface.get("operation")
            self.assertTrue(isinstance(operation, str) and operation,
                            f"{directive} owner surface must name an operation")
            # A host/human directive is genuinely not a model author action.
            self.assertNotIn(operation, author_kinds,
                             f"{directive} owner operation must not be a model author action")
            # Host operations must be in the closed vocabulary; human operations must be decision.
            if surface.get("owner") == "host":
                self.assertIn(operation, allowed_host_ops,
                              f"{directive} host operation {operation!r} is not in the closed "
                              f"vocabulary (commands, actions.host, actions.trusted, private "
                              f"governance operations)")
            else:
                self.assertEqual(operation, "decision",
                                 f"{directive} human operation must be 'decision', got {operation!r}")

    def test_residual_accept_binds_to_the_stop_intent(self):
        self.assertEqual(self._surface_of("residual.accept"),
                         {"tool": "report_convergence", "intent": "stop"})

    def test_negative_control_removing_stop_from_the_report_tool_fails(self):
        author_kinds, read_ops, _ = self._surfaces()
        allowed_host_ops = self._allowed_host_ops()
        # residual.accept binds to intent "stop"; dropping it from the report tool must break it.
        without_stop = {"report_convergence"}
        self.assertIsNone(self._resolve_directive("residual.accept", author_kinds, read_ops,
                                                  without_stop, allowed_host_ops))

    def test_negative_control_surface_names_missing_action_fails(self):
        _, read_ops, report_intents = self._surfaces()
        allowed_host_ops = self._allowed_host_ops()
        without_graph = {k for k in self._surfaces()[0] if k != "graph"}
        # graph.record's surface still names "graph", but with that kind absent it cannot resolve.
        self.assertIsNone(self._resolve_directive("graph.record", without_graph, read_ops,
                                                  report_intents, allowed_host_ops))
        # A surface naming a wholly unknown action never resolves.
        author_kinds, _, _ = self._surfaces()
        self.assertIsNone(self._resolve({"tool": "empirica_observe", "action": "nonexistent"},
                                        author_kinds, read_ops, report_intents, allowed_host_ops))

    def test_negative_control_entry_with_no_surface_fails(self):
        author_kinds, read_ops, report_intents = self._surfaces()
        allowed_host_ops = self._allowed_host_ops()
        # An entry whose surface is absent does not resolve.
        self.assertIsNone(self._resolve(None, author_kinds, read_ops, report_intents,
                                        allowed_host_ops))
        self.assertIsNone(self._resolve({}, author_kinds, read_ops, report_intents,
                                        allowed_host_ops))

    def test_negative_control_nonexistent_host_operation_fails(self):
        author_kinds, read_ops, report_intents = self._surfaces()
        allowed_host_ops = self._allowed_host_ops()
        # A host surface naming a nonexistent operation does not resolve.
        self.assertIsNone(self._resolve({"owner": "host", "operation": "nonexistent_operation"},
                                        author_kinds, read_ops, report_intents, allowed_host_ops))
        # A human surface naming anything other than "decision" does not resolve.
        self.assertIsNone(self._resolve({"owner": "human", "operation": "reject"},
                                        author_kinds, read_ops, report_intents, allowed_host_ops))

    def _decision_reasons(self):
        """Reasons the core itself emits for a ``needs-decision`` claim, derived by running a
        one-claim needs-decision graph to report and keeping the reasons whose ``affected``
        obligation is that claim. Returns ``{reason_code: next_actions}``."""
        service, run_id = self._fresh_run(None)

        def act(action):
            return service.dispatch({"protocol": "empirica/v2", "request_id": "t",
                "command": {"type": "ObserveAction", "run_id": run_id, "action": action}})

        act({"kind": "route", "reason": "decision route"})
        act({"kind": "graph", "payload": {"root": "C0", "claims": [{
            "id": "C0", "text": "a human decides", "gating": True,
            "kind": "needs-decision"}], "edges": []}})
        act({"kind": "configure_run"})
        approve_current(service._coordinator, run_id)
        act({"kind": "investigate"})
        result = service.dispatch({"protocol": "empirica/v2", "request_id": "t",
            "command": {"type": "EvaluateRun", "run_id": run_id,
                        "intent": "report_convergence"}})["result"]
        return {reason["code"]: tuple(reason.get("next_actions", []))
                for reason in result.get("reasons", []) or []
                if reason.get("affected", {}).get("obligation_id") == "claim:C0"}

    @staticmethod
    def _human_binding_offenders(next_actions, reasons, decision_reasons):
        """Human-owned directives that are not tied to the needs-decision claim kind: each must
        be emitted by a core decision reason, and every contract reason listing it must be one
        of those decision reasons."""
        decision_directives = {d for actions in decision_reasons.values() for d in actions}
        offenders = []
        for directive, entry in sorted(next_actions.items()):
            surface = entry.get("surface") if isinstance(entry, dict) else None
            if not (isinstance(surface, dict) and surface.get("owner") == "human"):
                continue
            listing = {code for code, reason in reasons.items()
                       if directive in reason.get("next_actions", [])}
            if directive not in decision_directives or not listing <= set(decision_reasons):
                offenders.append(directive)
        return offenders

    def test_human_ownership_is_tied_to_the_needs_decision_kind(self):
        decision_reasons = self._decision_reasons()
        self.assertTrue(decision_reasons, "a needs-decision claim must emit a decision reason")
        contract = _proto._PUBLIC_CONTRACT
        self.assertEqual(self._human_binding_offenders(contract["next_actions"],
                                                       contract["reasons"], decision_reasons),
                         [])
        # The contract has at least one human-owned directive, so the check is not vacuous.
        self.assertTrue(any(isinstance(row.get("surface"), dict)
                            and row["surface"].get("owner") == "human"
                            for row in contract["next_actions"].values()))

    def test_negative_control_human_ownership_on_an_unrelated_directive_fails(self):
        decision_reasons = self._decision_reasons()
        mutated = copy.deepcopy(_proto._PUBLIC_CONTRACT)
        mutated["next_actions"]["route.record"]["surface"] = {"owner": "human",
                                                              "operation": "decision"}
        self.assertIn("route.record",
                      self._human_binding_offenders(mutated["next_actions"], mutated["reasons"],
                                                    decision_reasons))

    def test_private_governance_operations_canonical_and_used(self):
        """The private governance operation names have one canonical definition
        (application.v2.PRIVATE_GOVERNANCE_OPERATIONS).  Pi private_bridge.py's operation
        table and the Claude caller (adapters.bridge) use exactly those names."""
        from application.v2 import PRIVATE_GOVERNANCE_OPERATIONS
        canonical = set(PRIVATE_GOVERNANCE_OPERATIONS)
        # Pi private_bridge.py: the governance keys in _HANDLERS match exactly.
        from adapters.pi.private_bridge import _HANDLERS
        pi_gov_ops = {k for k in _HANDLERS if k.startswith("governance_")}
        self.assertEqual(pi_gov_ops, canonical,
                         f"Pi _HANDLERS governance keys {pi_gov_ops} != canonical {canonical}")
        # Claude caller (adapters.bridge): the trusted_governance_* callables match exactly.
        from adapters import bridge
        claude_gov_ops = {name[len("trusted_"):] for name in dir(bridge)
                          if name.startswith("trusted_governance_") and callable(getattr(bridge, name))}
        self.assertEqual(claude_gov_ops, canonical,
                         f"Claude bridge governance callables {claude_gov_ops} != canonical {canonical}")

    def test_params_subset_of_bound_tool_shape(self):
        """Every ``params`` of an observe/report-bound directive must be a subset of the bound
        tool shape: for ``empirica_observe`` the action's top-level properties (minus ``kind``)
        plus any nested ``payload`` properties; for ``report_convergence`` the report tool's
        properties minus the fixed ``intent`` and the host-supplied ``run_id``."""
        definitions = {d["name"]: d["inputSchema"] for d in PublicTools(CLAUDE).definitions()}
        observe_one_of = definitions[_pt.OBSERVE_TOOL]["properties"]["action"]["oneOf"]
        action_shapes = {row["properties"]["kind"]["const"]: row for row in observe_one_of}
        report_props = set(definitions[_pt.REPORT_TOOL]["properties"])
        report_props.discard("intent")
        report_props.discard("run_id")
        for directive, entry in sorted(_proto._PUBLIC_CONTRACT["next_actions"].items()):
            surface = entry.get("surface")
            if not isinstance(surface, dict):
                continue
            params = entry.get("params")
            if not isinstance(params, dict):
                continue
            param_props = set(params.get("properties", {}))
            tool = surface.get("tool")
            if tool == "empirica_observe":
                action = surface.get("action")
                shape = action_shapes.get(action, {})
                allowed = set(shape.get("properties", {})) - {"kind"}
                payload = shape.get("properties", {}).get("payload", {})
                if isinstance(payload, dict):
                    allowed |= set(payload.get("properties", {}))
                extra = param_props - allowed
                self.assertFalse(extra, f"{directive} params {sorted(extra)} not in {action} tool")
            elif tool == "report_convergence":
                extra = param_props - report_props
                self.assertFalse(extra,
                                 f"{directive} params {sorted(extra)} not in report_convergence tool")

    def test_negative_control_params_outside_report_tool_fails(self):
        """A ``note`` param on residual.accept (report-bound) is not in the report tool shape."""
        definitions = {d["name"]: d["inputSchema"] for d in PublicTools(CLAUDE).definitions()}
        report_props = set(definitions[_pt.REPORT_TOOL]["properties"])
        report_props.discard("intent")
        report_props.discard("run_id")
        self.assertNotIn("note", report_props,
                         "note must not be in the report tool shape for the negative control")

    def _fresh_run(self, graph):
        runs, artifacts = Runs(), Artifacts()
        service = compose(Workspace(), Harness(), runs, artifacts, None, PROFILE, {}, None)
        run_id = service.dispatch({"protocol": "empirica/v2", "request_id": "t",
            "command": {"invocation": dict(TEST_INVOCATION), "type": "StartRun",
                        "goal": "governed task",
                        "selector": {"project": "p", "session": "s"}}})["result"]["run"]["id"]
        return service, run_id

    def test_emitted_next_actions_resolve_via_their_surface(self):
        author_kinds, read_ops, report_intents = self._surfaces()
        allowed_host_ops = self._allowed_host_ops()
        emitted: set[str] = set()

        def collect(view):
            emitted.update(view.get("next_actions", []))
            for group in view.get("obligations", {}).values():
                for row in group:
                    emitted.update(row.get("next", []))
            for residual in view.get("residuals", []):
                emitted.update(residual.get("next_actions", []))

        def collect_reasons(result):
            for reason in result.get("reasons", []) or []:
                emitted.update(reason.get("next_actions", []))
            if isinstance(result.get("run"), dict):
                collect(result["run"])

        # graphless + graph.missing block.
        collect(self.req({"type": "GetRun", "run_id": self.run_id})["run"])
        collect_reasons(self.req({"type": "EvaluateRun", "run_id": self.run_id,
                                  "intent": "report_convergence"}))
        # pending + approved.
        self.to_pending()
        collect(self.req({"type": "GetRun", "run_id": self.run_id})["run"])
        approve_current(self.service._coordinator, self.run_id)
        collect(self.req({"type": "GetRun", "run_id": self.run_id})["run"])
        # research-owed view (ordinary claim after investigation).
        self.action("investigate")
        collect_reasons(self.req({"type": "EvaluateRun", "run_id": self.run_id,
                                  "intent": "report_convergence"}))
        # audit-pending view (child.wait) driven through the real lifecycle.
        self.action("research", claim_id="C0", source_kind="code", result="supports",
                    payload={"source_ref": "plugins/empirica/tests/test_context_economy.py",
                             "citation": "observed directly"})
        self.action("freeze")
        reserved = self.action("child_reserve", purpose="audit", resource_class="audit",
                               role_profile=CLAUDE, execution="foreground")
        child_id = reserved["run"]["children"][-1]["child_id"]
        for state in ("launching", "pending"):
            self.service.trusted_child_event(
                run_id=self.run_id, child_id=child_id,
                event=build_child_event_payload(state, native_id="n-pre"))
        collect_reasons(self.req({"type": "EvaluateRun", "run_id": self.run_id,
                                  "intent": "report_convergence"}))
        # spike view: a needs-experiment claim owes spike.run.
        spike_service, spike_run = self._fresh_run(None)
        spike_service.dispatch({"protocol": "empirica/v2", "request_id": "t",
            "command": {"type": "ObserveAction", "run_id": spike_run,
                        "action": {"kind": "route", "reason": "spike route"}}})
        spike_service.dispatch({"protocol": "empirica/v2", "request_id": "t",
            "command": {"type": "ObserveAction", "run_id": spike_run, "action": {"kind": "graph",
                "payload": {"root": "C0", "claims": [{"id": "C0", "text": "needs experiment",
                             "gating": True, "kind": "needs-experiment"}], "edges": []}}}})
        spike_service.dispatch({"protocol": "empirica/v2", "request_id": "t",
            "command": {"type": "ObserveAction", "run_id": spike_run,
                        "action": {"kind": "configure_run"}}})
        approve_current(spike_service._coordinator, spike_run)
        spike_service.dispatch({"protocol": "empirica/v2", "request_id": "t",
            "command": {"type": "ObserveAction", "run_id": spike_run,
                        "action": {"kind": "investigate"}}})
        spike_service.dispatch({"protocol": "empirica/v2", "request_id": "t",
            "command": {"type": "ObserveAction", "run_id": spike_run, "action": {
                "kind": "research", "claim_id": "C0", "source_kind": "code",
                "result": "supports", "payload": {
                    "source_ref": "plugins/empirica/tests/test_context_economy.py",
                    "citation": "observed directly"}}}})
        collect_reasons(spike_service.dispatch({"protocol": "empirica/v2", "request_id": "t",
            "command": {"type": "EvaluateRun", "run_id": spike_run,
                        "intent": "report_convergence"}})["result"])
        # stopped view.
        stopped = self.req({"type": "EvaluateRun", "run_id": self.run_id, "intent": "stop"})
        collect(stopped["run"])

        self.assertTrue(emitted, "expected at least one emitted next action")
        # The lifecycle actually exercised the research, spike and audit-pending directives.
        for expected in ("research.record", "spike.run", "child.wait"):
            self.assertIn(expected, emitted, f"expected {expected!r} to be emitted")
        unresolved = sorted(d for d in emitted
                            if self._resolve_directive(d, author_kinds, read_ops, report_intents,
                                                       allowed_host_ops)
                            is None)
        self.assertEqual(unresolved, [], f"emitted directives that do not resolve: {unresolved}")


class PrivateResponseSchemaTests(_ServiceHarness):
    """D3: a run-bearing private Allow/Block/Inert must carry the presentation; the public
    Block/Inert alternatives are runless. The corrupt/decode fallbacks fail closed with a runless
    Fault (no synthetic-run Block), and the shared host mediator treats that Fault as fail-closed.
    """

    def _presented_allow(self):
        self.to_pending()
        resp = self.service.trusted_governance_context(
            run_id=self.run_id, payload=copy.deepcopy(CONTEXT))
        self.assertEqual(resp["result"]["type"], "Allow")
        self.assertIn("presentation", resp["result"])
        return resp

    def test_deleting_presentation_from_run_bearing_allow_block_inert_is_rejected(self):
        resp = self._presented_allow()
        run, pres = resp["result"]["run"], resp["result"]["presentation"]
        reason = {"code": "governance.approval_required", "parameters": {},
                  "next_actions": [], "sections": ["protocol"]}
        variants = {
            "Allow": {"type": "Allow", "converged": False, "run": run, "presentation": pres},
            "Block": {"type": "Block", "run": run, "reasons": [reason], "presentation": pres},
            "Inert": {"type": "Inert", "reason": "unsupported_host_event", "run": run,
                      "presentation": pres},
        }
        for name, result in variants.items():
            env = {"protocol": "empirica/v2", "request_id": "t", "result": result}
            self.assertTrue(_proto.validate_private_governance_response(env),
                            f"presented run-bearing {name} must validate")
            without = copy.deepcopy(env)
            without["result"].pop("presentation")
            self.assertFalse(_proto.validate_private_governance_response(without),
                             f"run-bearing {name} without presentation must be rejected")

    def test_decode_fallback_returns_runless_closed_fault(self):
        self.to_pending()
        key = next(iter(self.runs.data))
        raw = copy.deepcopy(self.runs.data[key].value)
        del raw["governance"]
        self.runs.data[key] = type(self.runs.data[key])(self.runs.data[key].revision, raw)
        decode = self.service.trusted_governance_context(
            run_id=self.run_id, payload=copy.deepcopy(CONTEXT))["result"]
        self.assertEqual(decode,
                         {"type": "Fault", "code": "corrupt_run", "fail_direction": "closed"})
        self.assertFalse(contains_private(decode))

    def test_corrupt_read_fallback_returns_runless_closed_fault(self):
        from core.records import Corrupt
        self.to_pending()
        key = next(iter(self.runs.data))
        self.runs.data[key] = Corrupt("bad bytes")
        corrupt = self.service.trusted_governance_context(
            run_id=self.run_id, payload=copy.deepcopy(CONTEXT))["result"]
        self.assertEqual(corrupt["type"], "Fault")
        self.assertEqual(corrupt["fail_direction"], "closed")
        self.assertNotIn("run", corrupt)

    def test_shared_host_treats_the_corrupt_fault_as_fail_closed(self):
        from adapters.governance import HostGovernance
        self.to_pending()
        view = self.req({"type": "GetRun", "run_id": self.run_id})["run"]
        allow = {"type": "Allow", "converged": False, "run": view}
        fault = {"protocol": "empirica/v2", "request_id": "t",
                 "result": {"type": "Fault", "code": "corrupt_run", "fail_direction": "closed"}}
        mediator = HostGovernance(
            PROFILE, elicit=lambda *_a, **_k: {"action": "accept", "content": {}},
            context_ingress=lambda *_a, **_k: fault, decision_ingress=lambda *_a, **_k: fault)
        out = mediator(allow)
        self.assertEqual(out["type"], "Fault")  # no approval; fail closed
        self.assertFalse(contains_private(out))


class DocumentationGuardTests(unittest.TestCase):
    """C6: no agent-facing text mentions `target: full` (the runtime guard is the A3 refusals)."""

    def test_no_agent_facing_text_mentions_target_full(self):
        root = Path(__file__).resolve().parents[3]
        agent_facing = [
            root / "plugins/empirica/skills",
            root / "plugins/empirica/agents",
            root / ".claude/skills/native-qualification",
        ]
        offenders: list[str] = []
        needles = ("target: full", "target=full", 'target": "full', "target:full")
        for base in agent_facing:
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if not path.is_file() or path.suffix not in {".md", ".txt"}:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for needle in needles:
                    if needle in text:
                        offenders.append(f"{path}:{needle}")
        # Also guard the projected model-facing tool descriptions.
        for definition in PublicTools(CLAUDE).definitions():
            if "full" in definition["description"] and "target" in definition["description"]:
                offenders.append(f"tool:{definition['name']}")
        self.assertEqual(offenders, [], f"agent-facing text mentions target: full: {offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
