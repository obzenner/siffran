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
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sandbox_isolation import force_absent_sandbox_home  # noqa: E402

from adapters import public_tools as _pt
from adapters.public_tools import PublicTools
from application import protocol as _proto
from application.v2 import compose
from core.evaluation import valid_graph
from core.projection import project_presentation
from test_d7_transactions import Runs, Artifacts, Workspace, Harness
from governance_setup import (AUTHOR, TEST_INVOCATION, SIZED_RATIONALE, approve_current, end_with_consent,
                              sized_configure_run)
from core import governance
from core.canonical import canonical_digest
from application.protocol import host_profile_id  # noqa: E402
force_absent_sandbox_home()
CLAUDE_PROFILE = host_profile_id("claude-code")
PI_PROFILE = host_profile_id("pi")


def build_child_event_payload(state: str, *, native_id: str | None = None,
                              result_digest: str | None = None) -> dict:
    """Minimal valid closed child_event payload (mirrors tests/v2/assertions without pulling the
    v2 driver package onto the path)."""
    return {"state": state, "native_id": native_id,
            "fingerprint": canonical_digest({"state": state, "native_id": native_id}),
            "result_digest": result_digest}

PROFILE = PI_PROFILE
CODEX = host_profile_id("codex-cli")
CLAUDE = CLAUDE_PROFILE
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
      * it is object-capable (``_object_capable``) but neither closes with
        ``additionalProperties: false`` nor declares a typed value schema and bounded
        ``propertyNames`` for a deliberately dynamic map;
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
    additional = node.get("additionalProperties")
    names = node.get("propertyNames")
    name_pattern = None
    if isinstance(names, dict):
        name_pattern = names.get("pattern")
        if name_pattern is None and isinstance(names.get("allOf"), list):
            name_pattern = next((item.get("pattern") for item in names["allOf"]
                                 if isinstance(item, dict) and item.get("pattern")), None)
    bounded_map = (
        isinstance(additional, dict) and bool(additional)
        and bool(name_pattern)
        and type(node.get("maxProperties")) is int and node["maxProperties"] >= 0
    )
    if _object_capable(node) and additional is not False and not bounded_map:
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
        # A deliberately dynamic map is closed by bounded key syntax and a typed value schema.
        self.assertEqual(closure_offenders({
            "type": "object", "maxProperties": 2,
            "propertyNames": {"pattern": "^[A-Z]+$", "maxLength": 8},
            "additionalProperties": {"type": "string"},
        }), [])
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
        self.run_id = self.req({"type": "StartRun", "control_mode": "deliberative", "goal": "governed task",
                                "invocation": dict(TEST_INVOCATION),
                                "selector": {"project": "p", "session": "s"}})["run"]["id"]
        # PR 08: mediating tests publish their HITL documents through a store under a temporary
        # machine home — never the author's real $EMPIRICA_HOME (which is this harness's machines
        # write root for the sidecar defaults the mediators construct).
        machine_home = tempfile.TemporaryDirectory(prefix="economy-home-")
        self.addCleanup(machine_home.cleanup)
        machine_env = patch.dict("os.environ", {"EMPIRICA_HOME": machine_home.name})
        machine_env.start()
        self.addCleanup(machine_env.stop)

    def req(self, command):
        return self.service.dispatch({"protocol": "empirica/v2", "request_id": "t",
                                      "command": command})["result"]

    def action(self, kind, **kwargs):
        action = {"kind": kind, **kwargs}
        return self.req({"type": "ObserveAction", "run_id": self.run_id,
                         "action": action})

    def to_pending(self):
        self.action("route", reason="supplied context")
        self.action("graph", payload=copy.deepcopy(GRAPH))
        self.action(**sized_configure_run(
            budgets={"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2},
            rationale=SIZED_RATIONALE))


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
                    "approval_kind": "host_ui", "outcome": "present",
                    "document_digest": context["presentation"]["document"]["digest"]}
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
                                             "action": {"kind": "configure_run", "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}, "rationale": SIZED_RATIONALE}})
        self.assertFalse(out["isError"])
        self.assertPublic(out["structuredContent"], "configure_run after govern")
        self.assertNotIn("presentation", out["structuredContent"])
        # A misbehaving governor that reintroduces presentation becomes a typed closed error.
        def leak(result):
            result["presentation"] = project_presentation(self.service._coordinator.last_snapshot)
            return result
        bad = PublicTools(PROFILE, dispatch=self._observe_dispatch, govern=leak)
        leaked = bad.call("empirica_observe", {"run_id": self.run_id,
                                              "action": {"kind": "configure_run", "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}, "rationale": SIZED_RATIONALE}})
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
                                             "action": {"kind": "configure_run", "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}, "rationale": SIZED_RATIONALE}})
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
        pending RunView read           1051
        approved RunView read          1068
        audit-pending RunView read      896
        terminal RunView report        1147
        audit-pending Block             992
        configure approve result       1068
        configure dismiss result       1255 (1257 UTF-8 bytes)
        configure edit->confirm result 1068
    Largest measured state = 1257 UTF-8 bytes (configure dismiss); CEILING = 1300 leaves 43 bytes headroom.
    The character ceiling remains mirrored with Pi and the largest case also asserts the stricter UTF-8 byte size.
    Re-attaching the full presentation (dialog + scope) to the pending RunView pushes the
    JSON to 3577 chars, well past the ceiling, so the reduction is guarded rather than asserted
    only to be > 0.
    """

    # A LOGICAL bound, not a fixture measurement: it bounds the text the renderer controls. The one line it does not
    # control is the author's sizing rationale, which the ``configure_run`` schema caps at 600 characters (the bound is
    # asserted separately, in ``AuditLaunchViewTests``); the ceiling applies to the view WITHOUT that line wherever the
    # rationale can be long (``AuditLaunchViewTests.renderer_text``).
    CEILING = 1300
    ROLE_PROFILE = CLAUDE_PROFILE

    @staticmethod
    def _size(obj) -> int:
        return len(json.dumps(obj, sort_keys=True, separators=(",", ":")))

    def _text_size(self, tool_result) -> int:
        return len(tool_result["content"][0]["text"])

    def _text_bytes(self, tool_result) -> int:
        return len(tool_result["content"][0]["text"].encode("utf-8"))

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
                         {"run_id": self.run_id, "action": {"kind": "configure_run", "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}, "rationale": SIZED_RATIONALE}})
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
        self.assertEqual(sc["reasons"][0]["code"], "governance.dialog_closed")
        self.assertEqual(sc["run"]["governance"]["state"], "pending")
        self.assertEqual(dialogs, 1, "dismiss is a single dialog")
        unavailable = _proto.author_view_labels()["spike_backend_unavailable"]
        self.assertIn(unavailable, out["content"][0]["text"],
                      "the largest view must retain the unavailable spike guidance")
        self.assertLess(self._text_size(out), self.CEILING, "configure dismiss")
        self.assertLess(self._text_bytes(out), self.CEILING, "configure dismiss UTF-8 bytes")

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
        # (the step that asks the human is a Block that carries the reason and the batch; its size is not a view's)
        self.action("investigate")  # a human's answer is recorded only for an investigated run
        end_with_consent(self.service._coordinator, self.run_id)
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

    def test_reattaching_the_full_presentation_exceeds_the_budget(self):
        self.to_pending()
        pending = self._read_text()
        self.assertLess(self._text_size(pending), self.CEILING)
        result = pending["structuredContent"]
        self.assertFalse(contains_private(result))
        pres = project_presentation(self.service._coordinator.last_snapshot)
        # Full shape: dialog + scope re-attached onto the author RunView governance block.
        result = copy.deepcopy(result)
        result["run"]["governance"]["dialog"] = pres["dialog"]
        result["run"]["governance"]["scope"] = pres["scope"]
        full_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
        self.assertGreaterEqual(len(full_json), self.CEILING,
                                "re-attaching the full presentation must exceed the ceiling")


class DismissalRecoveryTests(_ServiceHarness):
    """After a configuration dialog ends without approval, the view names ONE way on, and the carriers agree.

    The adapter only chooses which contract code applies, from core facts; the code's row owns the actions, and the
    run-level ``Next:`` must agree with it in every case:
      * the human closed the dialog (dismissed, timed out) → ``governance.dialog_closed``: ``configure_run`` or stop;
      * this configuration was shown three times → ``governance.interaction_limit``: ``configure_run`` (changed ceilings) or stop;
      * no dialog can be shown now (no UI, even after a human-capable context) or the run's allowance is spent →
        ``governance.approval_unavailable``: the stop alone.
    A decline is an Allow with no reason; its run-level Next is ``configure_run``. Deliberative and interactive auto."""

    _mediator = SizeBudgetTests._mediator
    _observe_dispatch = SizeBudgetTests._observe_dispatch

    AGAIN = "empirica_observe kind=configure_run"
    STOP = "report_convergence intent=stop"
    BUDGETS = {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}
    CLOSE = {"dismissed": lambda *_a, **_k: {"action": "cancel"}, "timed out": lambda *_a, **_k: None}

    def _start(self, mode, tag=""):
        self.run_id = self.req({"type": "StartRun", "control_mode": mode, "goal": "governed task",
                                "invocation": dict(TEST_INVOCATION),
                                "selector": {"project": "p", "session": f"s-{mode}-{tag}"}})["run"]["id"]
        self.action("route", reason="supplied context")
        self.action("graph", payload=copy.deepcopy(GRAPH))

    def _call(self, elicit, budgets=None, govern=None):
        """One configure_run through the real HostGovernance; (view text, dialogs opened)."""
        shown = {"n": 0}

        def counted(*args, **kwargs):
            shown["n"] += 1
            return elicit(*args, **kwargs)

        tools = PublicTools(PROFILE, dispatch=self._observe_dispatch,
                            govern=govern or self._mediator(None if elicit is None else counted))
        out = tools.call_internal("empirica_observe", {"run_id": self.run_id, "action": {
            "kind": "configure_run", "budgets": budgets or self.BUDGETS, "rationale": SIZED_RATIONALE}})
        return out["content"][0]["text"], shown["n"]

    def _way_on(self, text):
        """(reason code, its ``next:`` actions as printed, the run-level Next); the three must agree."""
        lines = [line.strip() for line in text.splitlines()]
        reasons = [line.split(":")[0] for line in lines if line.startswith("governance.")]
        nexts = [line[len("next: "):].split("; ") for line in lines if line.startswith("next:")]
        run_next = lines[lines.index("Next:") + 1]
        return (reasons[0] if reasons else None), (nexts[0] if nexts else []), run_next

    def _agree(self, text, code, actions):
        got_code, got_next, run_next = self._way_on(text)
        self.assertEqual((got_code, got_next), (code, actions), text)
        self.assertIn(run_next, got_next or [run_next], "the run-level Next is one of the reason's actions")
        self.assertEqual(run_next, actions[0] if code != "governance.approval_unavailable" else self.STOP, text)
        self.assertNotIn("governance_context", text)
        self.assertNotIn("blindly", text)

    def test_a_closed_dialog_names_one_way_on_in_every_mode(self):
        for mode in ("deliberative", "auto"):
            for case, answer in self.CLOSE.items():
                with self.subTest(mode=mode, case=case):
                    self._start(mode, case)
                    text, dialogs = self._call(answer)
                    self.assertEqual(dialogs, 1)
                    self._agree(text, "governance.dialog_closed", [self.AGAIN, self.STOP])
                    self.assertEqual(self._call(answer)[1], 1, "the resubmitted configure_run reopens the dialog")

    def test_a_decline_is_an_allow_with_no_reason_and_the_run_next_asks_again(self):
        for mode in ("deliberative", "auto"):
            with self.subTest(mode=mode):
                self._start(mode, "declined")
                text, dialogs = self._call(lambda *_a, **_k: {"action": "decline"})
                code, reason_next, run_next = self._way_on(text)
                self.assertEqual((dialogs, code, reason_next, run_next), (1, None, [], self.AGAIN), text)
                self.assertEqual(self._call(lambda *_a, **_k: {"action": "decline"})[1], 1)

    def test_three_dismissals_end_the_dialogs_for_these_values_but_changed_ceilings_reopen_it(self):
        for mode in ("deliberative", "auto"):
            with self.subTest(mode=mode):
                self._start(mode, "limit")
                answer = self.CLOSE["dismissed"]
                for _ in range(2):
                    self._agree(self._call(answer)[0], "governance.dialog_closed", [self.AGAIN, self.STOP])
                text, dialogs = self._call(answer)
                self.assertEqual(dialogs, 1)
                self._agree(text, "governance.configuration_shown", [self.AGAIN, self.STOP])
                text, dialogs = self._call(answer)
                self.assertEqual(dialogs, 0, "the same values are not shown a fourth time")
                self._agree(text, "governance.configuration_shown", [self.AGAIN, self.STOP])
                text, dialogs = self._call(answer, budgets={**self.BUDGETS, "max_passes": 7})
                self.assertEqual(dialogs, 1, "changed ceilings are a new configuration and reopen the dialog")
                self._agree(text, "governance.dialog_closed", [self.AGAIN, self.STOP])

    def test_the_runs_spent_allowance_names_the_stop_alone(self):
        from core import governance as core_governance
        for mode in ("deliberative", "auto"):
            with self.subTest(mode=mode), patch.object(core_governance, "MAX_INTERACTIONS", 2):
                self._start(mode, "total")
                self._call(self.CLOSE["dismissed"])
                text, _ = self._call(self.CLOSE["dismissed"])
                self._agree(text, "governance.approval_unavailable", [self.STOP])
                self._agree(self._call(self.CLOSE["dismissed"], budgets={**self.BUDGETS, "max_passes": 7})[0],
                            "governance.approval_unavailable", [self.STOP])

    def _governed(self):
        return self.req({"type": "GetRun", "run_id": self.run_id})["run"]["governance"]

    def _show_out(self, left):
        """Interactive auto: size once, make all but ``left`` of the eight material revisions (the first of the three
        showings below is the last of them: its rationale differs), and dismiss the current configuration three times."""
        self._call(self.CLOSE["dismissed"])
        for n in range(governance.MAX_REVISIONS - left - 1):
            self.action(**sized_configure_run(budgets=self.BUDGETS, rationale=f"material revision {n}"))
        for _ in range(3):
            self._call(self.CLOSE["dismissed"])
        governed = self._governed()
        self.assertEqual((governed["pre_approval_revisions"], governed["interactions_remaining"]["proposal"]),
                         (governance.MAX_REVISIONS - left, 0))

    def test_spent_revisions_and_a_shown_out_configuration_name_the_stop_alone(self):
        """The stuck state: no change is allowed any more and the current configuration has been shown three times, so no
        dialog can open and no change can be made. The run's Next and the adapter's reason agree on the stop (the Pi
        chooser reads the same view: its test is in ``governance-ui-unit.test.ts``)."""
        self._start("auto", "stuck")
        self._show_out(left=0)
        text, dialogs = self._call(self.CLOSE["dismissed"])
        self.assertEqual(dialogs, 0, "the unchanged configuration opens no dialog")
        self._agree(text, "governance.approval_unavailable", [self.STOP])
        self.assertNotIn("configure_run", text)
        self.assertEqual(self._governed()["next_action"], "residual.accept")
        changed = self._call(self.CLOSE["dismissed"], budgets={**self.BUDGETS, "max_passes": 7})[0]
        self.assertIn("governance.revision_exhausted", changed)

    def test_with_one_revision_left_a_shown_out_configuration_still_offers_the_change(self):
        self._start("auto", "one-left")
        self._show_out(left=1)
        self.assertEqual(self._governed()["next_action"], "governance.propose")
        self._agree(self._call(self.CLOSE["dismissed"])[0], "governance.configuration_shown", [self.AGAIN, self.STOP])

    def test_no_approval_ui_names_only_the_stop_even_after_a_human_capable_context(self):
        for mode in ("deliberative", "auto"):
            with self.subTest(mode=mode, history="reported no ingress"):
                self._start(mode, "no-ui")
                # what the host's start hook reports: the host, and that it has no approval ingress
                self.service.trusted_governance_context(run_id=self.run_id, payload={"author": AUTHOR, "ingress": "unavailable"})
                self._agree(self._call(None)[0], "governance.approval_unavailable", [self.STOP])
            with self.subTest(mode=mode, history="was human-capable"):
                self._start(mode, "was-human")
                # the start hook's report: the host's author and a human-capable ingress
                self.service.trusted_governance_context(run_id=self.run_id, payload={"author": AUTHOR, "ingress": "pi_ui"})
                self._agree(self._call(self.CLOSE["dismissed"])[0], "governance.dialog_closed", [self.AGAIN, self.STOP])
                # the same run resumed where nothing can be shown: the earlier context said human-capable
                text, dialogs = self._call(None)
                self.assertEqual(dialogs, 0)
                self._agree(text, "governance.approval_unavailable", [self.STOP])
                self.assertNotIn("configure_run", text)

    def test_a_run_resumed_without_a_ui_after_three_showings_names_the_stop_alone(self):
        """Nobody can be asked now outranks the spent configuration: both hosts answer the same (the choosers
        read ``context.approval_capability`` before ``prompt_error``)."""
        from adapters.governance import closed_code
        for mode in ("deliberative", "auto"):
            with self.subTest(mode=mode):
                self._start(mode, "resumed")
                # the start hook's report: the host's author and a human-capable ingress
                self.service.trusted_governance_context(run_id=self.run_id, payload={"author": AUTHOR, "ingress": "pi_ui"})
                for _ in range(3):
                    self._call(self.CLOSE["dismissed"])
                self._agree(self._call(self.CLOSE["dismissed"])[0], "governance.configuration_shown", [self.AGAIN, self.STOP])
                text, dialogs = self._call(None)
                self.assertEqual(dialogs, 0)
                self._agree(text, "governance.approval_unavailable", [self.STOP])
        governed = {"prompt_error": "governance.interaction_limit", "human_wait": False,
                    "interactions_remaining": {"proposal": 0, "total": 100},
                    "context": {"approval_capability": "unavailable"}}
        self.assertEqual(closed_code({"run": {"governance": governed}}), "governance.approval_unavailable")
        governed["context"]["approval_capability"] = "human_configuration"
        self.assertEqual(closed_code({"run": {"governance": governed}}), "governance.configuration_shown")
        # onto-S F2: no human can approve a pending raise while a non-raising proposal is admitted (the view says propose):
        # the way on is "propose within the approved ceilings, or stop"; the view naming the stop alone wins
        way_on = {"prompt_error": None, "human_wait": False, "raises": True, "next_action": "governance.propose",
                  "interactions_remaining": {"proposal": 3, "total": 100}, "context": {"approval_capability": "unavailable"}}
        self.assertEqual(closed_code({"run": {"governance": way_on}}), "governance.raise_unavailable")
        self.assertEqual(closed_code({"run": {"governance": {**way_on, "next_action": "residual.accept"}}}),
                         "governance.approval_unavailable")
        self.assertEqual(closed_code({"run": {"governance": {**way_on, "raises": False}}}), "governance.approval_unavailable")
        # F2: the run's total allowance spent while a human could be reached (nothing to ask with) and a revision within the
        # approved ceilings admitted: the same way on, whichever host reads it
        spent = {**way_on, "prompt_error": "governance.interaction_limit", "interactions_remaining": {"proposal": 3, "total": 0},
                 "context": {"approval_capability": "human_configuration"}}
        self.assertEqual(closed_code({"run": {"governance": spent}}), "governance.raise_unavailable")
        self.assertEqual(closed_code({"run": {"governance": {**spent, "next_action": "residual.accept"}}}), "governance.approval_unavailable")
        self.assertEqual(closed_code({"run": {"governance": {**spent, "raises": False}}}), "governance.approval_unavailable")
        # presentations spent with the total left is a configuration shown out, whatever is pending
        self.assertEqual(closed_code({"run": {"governance": {**spent, "interactions_remaining": {"proposal": 0, "total": 100}}}}),
                         "governance.configuration_shown")
        # F1: a deliberative run has no automatic revision, so its view names the stop alone and the reason agrees
        delib = {**way_on, "control_mode": "deliberative", "next_action": "residual.accept"}
        self.assertEqual(closed_code({"run": {"governance": delib}}), "governance.approval_unavailable")

    def test_a_document_that_cannot_be_bound_lets_the_chooser_decide_and_agrees_with_next(self):
        from adapters.governance import HostGovernance
        for mode in ("deliberative", "auto"):
            with self.subTest(mode=mode), patch.object(HostGovernance, "_bound_document", lambda *_a, **_k: None):
                self._start(mode, "unbound")
                self.service.trusted_governance_context(run_id=self.run_id, payload={"author": AUTHOR, "ingress": "pi_ui"})
                text, dialogs = self._call(self.CLOSE["dismissed"])
                self.assertEqual(dialogs, 0, "nothing is shown without its bound document")
                self._agree(text, "governance.dialog_closed", [self.AGAIN, self.STOP])

    def test_the_contract_rows_are_true_at_every_site_that_names_them(self):
        reasons = _proto.public_contract()["reasons"]
        self.assertEqual(reasons["governance.configuration_shown"]["next_actions"], ["governance.propose", "residual.accept"])
        self.assertIn("three times", reasons["governance.configuration_shown"]["message"])
        # the core's own sites name the run's total allowance: nothing can be shown, so only the stop
        self.assertEqual(reasons["governance.raise_unavailable"]["next_actions"], ["governance.propose", "residual.accept"])
        self.assertIn("within the approved ceilings", reasons["governance.raise_unavailable"]["message"])
        self.assertEqual(reasons["governance.interaction_limit"]["next_actions"], ["residual.accept"])
        self.assertNotIn("three times", reasons["governance.interaction_limit"]["message"])

    def test_the_read_tool_does_not_forbid_the_recovery_the_view_names(self):
        description = next(d["description"] for d in PublicTools(CLAUDE).definitions() if d["name"] == _pt.READ_TOOL)
        self.assertNotIn("reopen host UI", description)
        self.assertIn("next:", description)


class AuditLaunchViewTests(unittest.TestCase):
    """S4: when an audit is owed the view shows the host's exact auditor launch (the host profile's ``audit_launch``), not
    ``host: child_reserve``; a host with no launch (Codex) renders the protocol surface unchanged. C5 sizes are measured on
    the real audit-owed GetRun and report_convergence Block of each host: at authoring (characters) the largest of them is
    reported by ``test_the_audit_owed_views_stay_under_the_ceiling`` failing with its numbers if the ceiling is crossed."""

    CEILING = SizeBudgetTests.CEILING
    #: What the renderer adds around the author's rationale: the label, a space and the two untrusted-data delimiters.
    #: The rationale line is bounded by ``rationale_line_bound`` = schema maximum (600) x the worst per-character expansion
    #: (6: ``safe_text`` writes an invisible character as ``\uNNNN``; a control as ``\xNN`` and ``<`` / ``>`` as ``\x3c`` /
    #: ``\x3e`` are 4; a backslash is 2) + these markers. The old bound, 600 + markers, held only for text with no escapes.
    RATIONALE_MARKERS = len(_proto.author_view_labels()["proposal_rationale"]) + 1 + len("<<<EMPIRICA_UNTRUSTED_DATA>>>") \
        + len("<<<END_EMPIRICA_UNTRUSTED_DATA>>>")

    @staticmethod
    def renderer_text(text):
        """The view without the rationale line: the part the renderer controls (the rationale is the author's, bounded
        by its schema, see ``rationale_line_bound``)."""
        return "\n".join(line for line in text.splitlines() if not line.startswith("proposal rationale:"))

    def _owed(self, profile, rationale=SIZED_RATIONALE, claims=1):
        """Drive a real run on ``profile`` to the audit-owed state; return (GetRun text, report_convergence Block text)."""
        from governance_setup import invocation_for
        runs, artifacts = Runs(), Artifacts()
        service = compose(Workspace(), Harness(), runs, artifacts, None, profile, {}, None)

        def req(command):
            return service.dispatch({"protocol": "empirica/v2", "request_id": "t", "command": command})["result"]

        def act(kind, **kwargs):
            return req({"type": "ObserveAction", "run_id": run_id, "action": {"kind": kind, **kwargs}})

        run_id = req({"type": "StartRun", "control_mode": "deliberative", "goal": "governed task",
                      "invocation": invocation_for(profile), "selector": {"project": "p", "session": "s"}})["run"]["id"]
        act("route", reason="supplied context")
        graph = copy.deepcopy(GRAPH)
        graph["claims"] += [_claim(f"C{n}") for n in range(1, claims)]
        graph["edges"] = [{"from": "C0", "to": f"C{n}", "type": "SupportedBy"} for n in range(1, claims)]
        act("graph", payload=graph)
        act(**sized_configure_run(budgets={"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}, rationale=rationale))
        approve_current(service._coordinator, run_id)
        act("investigate")
        for n in range(claims):
            act("research", claim_id=f"C{n}", source_kind="code", result="supports",
                payload={"source_ref": "plugins/empirica/tests/test_context_economy.py", "citation": "observed directly"})
        act("freeze")
        tools = PublicTools(profile, dispatch=lambda request, _profile: service.dispatch(request))
        read = tools.call_internal("empirica_read", {"operation": "GetRun", "run_id": run_id})["content"][0]["text"]
        block = tools.call_internal("report_convergence", {"run_id": run_id, "intent": "report_convergence"})["content"][0]["text"]
        return read, block

    def test_the_view_shows_each_hosts_launch_and_the_profile_is_the_only_copy(self):
        for host in ("claude-code", "pi"):
            profile = host_profile_id(host)
            launch = _proto.host_profile(profile)["audit_launch"]
            read, block = self._owed(profile)
            for label, text in (("GetRun", read), ("report_convergence", block)):
                self.assertEqual(text.count(launch), 1, f"{host} {label}: the launch is printed once")
                self.assertIn(f"    next: {launch}", text, f"{host} {label}")
                self.assertNotIn("child_reserve", text, f"{host} {label}")
            self.assertIn("next: the auditor launch under Open obligations", block, host)
            self.assertNotIn("the auditor launch under", read, host)
        # No renderer carries a copy of a launch: the profile is the only place that names the auditor.
        root = Path(__file__).resolve().parents[1] / "adapters"
        for source in (root / "author_view.py", root / "pi" / "src" / "author-view.ts"):
            self.assertNotIn("empirica-auditor", source.read_text(encoding="utf-8"), source.name)

    def test_the_claude_launch_is_the_shape_the_spawn_hook_accepts(self):
        launch = _proto.host_profile(host_profile_id("claude-code"))["audit_launch"]
        tool, _, arguments = launch.partition(" ")
        fields = json.loads(arguments)
        self.assertEqual(tool, "Agent")
        self.assertEqual(fields["subagent_type"], "empirica:empirica-auditor")  # the hook's role check
        self.assertTrue(fields["prompt"].strip() and fields["description"].strip())  # real purpose: the hook replaces both
        self.assertEqual(set(fields), {"subagent_type", "description", "prompt"}, "no model override: the hook denies it")
        # The spawn hook's own admission functions accept it as an executable auditor launch.
        from adapters.claude import lifecycle
        self.assertTrue(lifecycle._launch_is_executable(fields))
        self.assertEqual(lifecycle._agent_identity(fields), (fields["prompt"], "empirica:empirica-auditor"))

    def test_a_host_with_no_launch_renders_without_a_hole(self):
        self.assertNotIn("audit_launch", _proto.host_profile(host_profile_id("codex-cli")))
        from adapters.author_view import render_surface
        self.assertEqual(render_surface("child.spawn_auditor", None), "host: child_reserve")
        self.assertEqual(render_surface("child.spawn_auditor", ""), "host: child_reserve")
        self.assertEqual(render_surface("child.retry", "launch"), "host: child_reserve", "an ordinary child's retry is not an audit")
        self.assertEqual(render_surface("child.retry", "launch", audit=True), "launch")

    @staticmethod
    def _golden(name, host):
        """A checked-in result, as it would arrive from ``host``."""
        path = Path(__file__).resolve().parents[1] / "adapters/pi/test/author-view-golden" / f"{name}.json"
        result = _proto.stored(json.loads(path.read_text(encoding="utf-8")))["result"]
        result["run"]["host"]["profile_id"] = host_profile_id(host)
        return result

    def test_a_terminal_audit_child_never_offers_the_protocol_surface(self):
        from adapters.author_view import render_author_view
        for name in ("block-child-terminal", "block-child-cancelled"):
            for host in ("claude-code", "pi"):
                text = render_author_view(self._golden(name, host), strict=True)
                launch = _proto.host_profile(host_profile_id(host))["audit_launch"]
                self.assertNotIn("child_reserve", text, f"{name} {host}")
                self.assertEqual(text.count(launch), 1, f"{name} {host}: once")
                self.assertIn("next: the auditor launch under Children", text, f"{name} {host}")

    def test_an_investigation_childs_terminal_reason_keeps_the_protocol_surface(self):
        from adapters.author_view import render_author_view
        result = self._golden("block-child-terminal", "pi")
        result["run"]["children"][0].update(purpose="spike", resource_class="investigation")
        self.assertIn("next: host: child_reserve; report_convergence intent=stop", render_author_view(result, strict=True))

    def test_a_mixed_stopped_pair_keeps_the_protocol_surface_for_the_reason(self):
        """An audit child stopped earlier and an investigation child that just timed out: the reason cannot say which child
        it names, so it keeps the protocol surface; each child's own row is exact."""
        from adapters.author_view import render_author_view
        for host in ("claude-code", "pi"):
            result = self._golden("block-child-terminal", host)
            result["run"]["children"].append({"child_id": "ch-spike-1", "purpose": "spike", "resource_class": "investigation",
                                              "state": "timed_out", "recovery_action": "child.retry"})
            text = render_author_view(result, strict=True)
            self.assertIn("next: host: child_reserve; report_convergence intent=stop", text, host)
            self.assertIn("recovery: host: child_reserve", text, host)
            self.assertEqual(text.count(_proto.host_profile(host_profile_id(host))["audit_launch"]), 1, host)

    def test_an_audit_residual_with_a_retry_shows_the_launch(self):
        from adapters.author_view import render_author_view
        for host in ("claude-code", "pi"):
            result = self._golden("audit-owed-pi", host)
            result.pop("reasons")
            result.update(type="Allow", converged=False)
            result["run"]["obligations"]["active"] = []
            result["run"]["residuals"] = [{"code": "audit.unreadable", "parameters": {}, "next_actions": ["child.retry"],
                                           "sections": ["audit"]}]
            text = render_author_view(result, strict=True)
            self.assertNotIn("child_reserve", text, host)
            self.assertEqual(text.count(_proto.host_profile(host_profile_id(host))["audit_launch"]), 1, host)

    def test_a_launch_without_a_home_is_printed_where_it_is_needed(self):
        """The home lookup knows the audit obligation; an action on any other row still prints the launch, not a crash."""
        from adapters.author_view import render_author_view
        for host in ("claude-code", "pi"):
            result = self._golden("audit-owed-pi", host)
            result.pop("reasons")
            result.update(type="Allow", converged=False)
            result["run"]["obligations"]["active"][0]["id"] = "obligation.approval"
            text = render_author_view(result, strict=True)
            self.assertNotIn("child_reserve", text, host)
            self.assertEqual(text.count(_proto.host_profile(host_profile_id(host))["audit_launch"]), 1, host)

    def test_the_run_level_next_shows_the_launch_not_the_protocol_surface(self):
        from adapters.author_view import render_author_view
        result = self._golden("audit-owed-pi", "pi")
        result["run"]["obligations"]["active"] = []
        result.pop("reasons")
        result.update(type="Allow", converged=False)
        result["run"]["next_actions"] = ["child.spawn_auditor"]
        text = render_author_view(result, strict=True)
        self.assertNotIn("child_reserve", text)
        self.assertEqual(text.count(_proto.host_profile(host_profile_id("pi"))["audit_launch"]), 1)

    def test_the_audit_owed_views_stay_under_the_ceiling(self):
        for host in ("claude-code", "pi"):
            for label, text in zip(("GetRun", "report_convergence"), self._owed(host_profile_id(host))):
                self.assertLess(len(self.renderer_text(text)), self.CEILING, f"{host} audit-owed {label}: {len(text)} characters")

    @staticmethod
    def worst_expansion():
        """The most characters one author character can become in the rationale line: ``safe_text`` escapes backslash as two,
        a control or C1 character as ``\\xNN`` (four) and an invisible or bidirectional character as ``\\uNNNN`` (six);
        ``untrusted`` then rewrites ``<`` and ``>`` as ``\\x3c`` / ``\\x3e`` (four). Read from the renderer itself, over the
        whole Basic Multilingual Plane, so a wider escape class changes the bound instead of passing silently."""
        from adapters.author_view import TextSafety
        safety = TextSafety("", "")
        return max(len(safety.untrusted(chr(code))) for code in range(0x10000))

    @classmethod
    def rationale_line_bound(cls):
        """The longest rationale line a schema-valid rationale can render: the schema maximum times the worst per-character
        expansion, plus the label and the two untrusted-data delimiters."""
        limit = _proto._REQUEST_SCHEMA["$defs"]["actionConfigureRun"]["properties"]["rationale"]["maxLength"]
        return limit * cls.worst_expansion() + cls.RATIONALE_MARKERS

    #: Valid 600-character rationales, each stressing a different escape: plain, all `<` (4x), mixed prose with `<` (the
    #: review's example), a backslash run (2x), controls (4x) and zero-width characters (6x, the worst).
    RATIONALES = {
        "plain": ("r" * 599) + ".",
        "all-lt": "<" * 600,
        "mixed": ("x < y; " * 85) + "done.",
        "backslashes": ("\\" * 599) + ".",
        "controls": ("\x01" * 599) + ".",
        "zero-width": ("\u200b" * 599) + ".",
    }

    def test_the_worst_expansion_is_derived_from_the_renderer(self):
        self.assertEqual(self.worst_expansion(), 6)
        self.assertEqual(self.rationale_line_bound(), 600 * 6 + self.RATIONALE_MARKERS)
        for text in self.RATIONALES.values():
            self.assertEqual(len(text), 600)
            self.assertTrue(text.strip())

    def test_every_valid_rationale_renders_within_the_derived_bound(self):
        bound = self.rationale_line_bound()
        measured = {}
        for name, rationale in self.RATIONALES.items():
            for host in ("claude-code", "pi"):
                read, block = self._owed(host_profile_id(host), rationale=rationale, claims=8)
                for label, text in (("GetRun", read), ("Block", block)):
                    line = next(line for line in text.splitlines() if line.startswith("proposal rationale:"))
                    self.assertLessEqual(len(line), bound, f"{name} {host} {label}")
                    self.assertLessEqual(len(self.renderer_text(text)), self.CEILING, f"{name} {host} {label}: renderer text")
                    measured[name, host, label] = len(line)
        # the schema maximum alone is not a bound: the escapes are longer than the characters they replace
        self.assertGreater(measured["all-lt", "pi", "GetRun"], 600 + self.RATIONALE_MARKERS)
        self.assertGreater(measured["mixed", "pi", "GetRun"], 600 + self.RATIONALE_MARKERS)
        self.assertEqual(measured["zero-width", "pi", "GetRun"], bound - 5, "six per character, one full stop")

    #: INFORMATIONAL pin, not a limit: the measured characters of the full audit-owed view (8 claims, the real ~200-character
    #: handle), rationale line included, as (GetRun, report_convergence Block) per host. "plain" is the 600-character
    #: rationale with no escapes (1,619 / 1,755 on Claude, 1,590 / 1,726 on Pi); "zero-width" is the worst case the escapes
    #: allow, six characters per character (4,614 / 4,750 on Claude, 4,585 / 4,721 on Pi). The limit is the logical bound
    #: above (the renderer's own text, rationale line excluded, stays within the ceiling; the rationale line within
    #: ``rationale_line_bound``). The pin fails if the worst case grows or the launch is printed twice again.
    WORST = {"plain": {"claude-code": (1619, 1755), "pi": (1590, 1726)},
             "zero-width": {"claude-code": (4614, 4750), "pi": (4585, 4721)}}

    def test_the_worst_audit_owed_view_is_bounded_by_what_the_renderer_controls(self):
        limit = _proto._REQUEST_SCHEMA["$defs"]["actionConfigureRun"]["properties"]["rationale"]["maxLength"]
        self.assertEqual(limit, 600)
        for case, pins in self.WORST.items():
            for host, (read_max, block_max) in pins.items():
                read, block = self._owed(host_profile_id(host), rationale=self.RATIONALES[case], claims=8)
                for label, text, pin in (("GetRun", read, read_max), ("Block", block, block_max)):
                    line = next(line for line in text.splitlines() if line.startswith("proposal rationale:"))
                    self.assertLessEqual(len(self.renderer_text(text)), self.CEILING, f"{case} {host} {label}")
                    self.assertLessEqual(len(line), self.rationale_line_bound(), f"{case} {host} {label}: the rationale line")
                    self.assertLessEqual(len(text), pin, f"{case} {host} {label} measured: {len(text)}")
                self.assertEqual(block.count(_proto.host_profile(host_profile_id(host))["audit_launch"]), 1)


#: What a human can be asked: the one batch of evidence and decisions (ADR-0067).
HUMAN_OPERATIONS = ("confirmation",)


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
        human owner surface resolves only when it names ``confirmation``."""
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
            return (owner, operation) if operation in HUMAN_OPERATIONS else None
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
            # Host operations must be in the closed vocabulary; human operations must be confirmation.
            if surface.get("owner") == "host":
                self.assertIn(operation, allowed_host_ops,
                              f"{directive} host operation {operation!r} is not in the closed "
                              f"vocabulary (commands, actions.host, actions.trusted, private "
                              f"governance operations)")
            else:
                self.assertIn(operation, HUMAN_OPERATIONS,
                              f"{directive} human operation must be one of {HUMAN_OPERATIONS}, "
                              f"got {operation!r}")

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
        # A human surface naming anything other than "confirmation" does not resolve.
        self.assertIsNone(self._resolve({"owner": "human", "operation": "reject"},
                                        author_kinds, read_ops, report_intents, allowed_host_ops))

    def _decision_reasons(self):
        """Reasons the core itself emits for a ``needs-decision`` claim, derived by running a
        one-claim needs-decision graph to report. Returns ``{reason_code: next_actions}``."""
        service, run_id = self._fresh_run(None)

        def act(action):
            return service.dispatch({"protocol": "empirica/v2", "request_id": "t",
                "command": {"type": "ObserveAction", "run_id": run_id, "action": action}})

        act({"kind": "route", "reason": "decision route"})
        act({"kind": "graph", "payload": {"root": "C0", "claims": [{
            "id": "C0", "text": "a human decides", "gating": True,
            "kind": "needs-decision"}], "edges": []}})
        act({"kind": "configure_run", "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}, "rationale": SIZED_RATIONALE})
        approve_current(service._coordinator, run_id)
        act({"kind": "investigate"})
        service.trusted_human_surface(run_id=run_id, payload={"run_id": run_id, "surface": "human"})
        result = service.dispatch({"protocol": "empirica/v2", "request_id": "t",
            "command": {"type": "EvaluateRun", "run_id": run_id,
                        "intent": "report_convergence"}})["result"]
        return {reason["code"]: tuple(reason.get("next_actions", []))
                for reason in result.get("reasons", []) or []}

    def test_the_evidence_confirmation_directive_belongs_to_the_confirmation_reason_alone(self):
        """``human.confirm_evidence`` (operation ``confirmation``) is listed by, and only by, the two reasons whose
        subject is an open human batch: ``evidence.confirmation_required`` (evidence waits on a human) and
        ``governance.terminal_stop_pending`` (a deliberative run's terminal stop waits on the same batch,
        ADR-0063). ``evidence.document_mismatch`` no longer lists it (review-2 m4/7b): the reason's recovery
        is the author's lever that re-presents — ``convergence.report`` first, ``residual.accept`` second —
        because a mismatched confirmation is rescued only by a fresh showing of the same batch, never
        by repeating the confirm call the stale receipt names."""
        contract = _proto._PUBLIC_CONTRACT
        listing = {code for code, reason in contract["reasons"].items()
                   if "human.confirm_evidence" in reason.get("next_actions", [])}
        self.assertEqual(listing, {"evidence.confirmation_required", "governance.terminal_stop_pending"})
        confirmations = {d for d, row in contract["next_actions"].items()
                         if row["surface"].get("operation") == "confirmation"}
        self.assertEqual(confirmations, {"human.confirm_evidence"})

    def test_a_document_mismatch_routes_the_author_to_the_fresh_showing_first(self):
        """``evidence.document_mismatch`` is model-facing despite its ``host_recovery`` disposition, so its
        next actions lead with the author action that re-presents (``convergence.report``) and keep the honest
        stop (``residual.accept``) second — never the human-step ``human.confirm_evidence``, which names a
        call the host makes, not a recovery the author can follow (review-2 m4, 7b)."""
        row = _proto._PUBLIC_CONTRACT["reasons"]["evidence.document_mismatch"]
        self.assertEqual(tuple(row["next_actions"]), ("convergence.report", "residual.accept"))

    def test_a_needs_decision_claim_emits_the_one_human_batch_reason(self):
        """A needs-decision claim is not a reason of its own: it is part of the one human batch, so the core
        emits ``evidence.confirmation_required`` naming the claim and the one human next action."""
        decision_reasons = self._decision_reasons()
        self.assertEqual(decision_reasons,
                         {"evidence.confirmation_required": ("convergence.report", "human.confirm_evidence",
                                                             "residual.accept")})
        contract = _proto._PUBLIC_CONTRACT
        humans = {d for d, row in contract["next_actions"].items()
                  if row["surface"].get("owner") == "human"}
        self.assertEqual(humans, {"human.confirm_evidence"})
        self.assertNotIn("claim.human_decision", contract["reasons"])

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
            "command": {"invocation": dict(TEST_INVOCATION), "type": "StartRun", "control_mode": "deliberative",
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
                        "action": {"kind": "configure_run", "budgets": {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}, "rationale": SIZED_RATIONALE}}})
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
            root / ".agents/skills/native-qualification",
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


class AutoTextTests(unittest.TestCase):
    """A12: what the skill, the references, the contract texts and the upgrade guide say about auto mode is true. An interactive
    auto run asks a human for its configuration and for a raise above the approved ceilings, and a delegated run asks nobody;
    neither asks about evidence or decisions. Evidence that needs a human is inert; a `needs-decision` claim is decided by research."""

    ROOT = Path(__file__).resolve().parents[3]
    REFERENCES = ROOT / "plugins/empirica/skills/empirica/references"
    CONTRACT = json.loads((ROOT / "contracts/empirica/v2/public-contract.json").read_text())

    def reference(self, name):
        """The reference with its line breaks folded to single spaces, so a pin does not depend on where a line wraps."""
        return " ".join((self.REFERENCES / name).read_text(encoding="utf-8").split())

    # --- the references -------------------------------------------------------------------------------------------------

    def test_evidence_names_the_auto_inert_rule_in_the_views_vocabulary(self):
        text = self.reference("evidence.md")
        raw = (self.REFERENCES / "evidence.md").read_text(encoding="utf-8")
        start = raw.index("\n### In an auto run, evidence that needs a human is inert\n")
        section = " ".join(raw[start:raw.index("\n### ", start + 5)].split())
        for pinned in (
            "An auto run asks a human nothing about evidence or decisions.",
            "Evidence that needs a human is inert: an `http(s)` URL, a `runtime` note with no file path, or a file the auditor's host cannot read.",
            "so a claim resting only on it reads `claim.research_missing` with the text \"has only research that a human would have to confirm\".",
            "cite a workspace file (the service checks it) or a local file your auditor can read; run a spike; or call `report_convergence` with `intent: \"stop\"`",
            "A refuting source of that kind still discards the claim.",
            "To have a human confirm evidence, start the run in deliberative mode.",
            "A `needs-decision` claim in an auto run is yours to decide through research: supporting research approves it, refuting research discards it, and the auditor judges the choice.",
        ):
            self.assertIn(pinned, section)
        # the deliberative sections say so, and the Fold 1 lead names its one exception by mode
        self.assertIn("In a deliberative run the view says how each record is verified:", text)
        self.assertIn("The one exception is a `needs-decision` claim in a deliberative run", text)
        self.assertIn("In a deliberative run a `needs-decision` claim is the exception to \"research first\"", text)
        self.assertNotIn("which is human-owned and needs only", text)
        self.assertEqual(text.count("start the run in deliberative mode"), 1, "the way to a human confirmation is said once")

    def test_governance_names_the_two_auto_questions_and_the_delegated_stop(self):
        text = self.reference("governance.md")
        for pinned in (
            "**What auto asks.** An interactive auto run asks a human for its configuration and for a raise above the approved ceilings;"
            " a delegated run asks nobody. Neither asks anything else: no evidence confirmation and no `needs-decision` answer, since research decides that claim.",
            "A delegated run approves its configuration automatically inside the fixed 8/1/2 envelope and has nobody to ask for a raise,"
            " so an exhausted ceiling ends in the honest stop or a fresh run ([budget-freeze](budget-freeze.md)).",
            "In deliberative mode, confirming research or answering a `needs-decision` claim is a separate human decision",
        ):
            self.assertIn(pinned, text)
        self.assertNotIn("propose a raise", text, "the raise proposal is stated once, in budget-freeze")
        self.assertNotIn("never offers `budget.raise`", text)

    def test_budget_freeze_states_the_raise_once_for_the_interactive_run_and_the_stop_for_the_delegated_one(self):
        text = self.reference("budget-freeze.md")
        self.assertIn("a delegated auto run cannot raise them, and an interactive one only with its human's approval", text)
        self.assertEqual(text.count("propose a raise with `configure_run` for the human to approve"), 1)
        self.assertIn("A delegated auto run is never offered `budget.raise`: it needs a fresh run with a larger up-front approved size.", text)
        self.assertNotIn("auto cannot raise them", text)
        # the settlement sentence is true for a deliberative run: it is offered the raise and is settled all the same, because
        # the raise is the human's to decide; a delegated auto run is settled because it is offered none
        self.assertNotIn("when the core offers no `budget.raise`", text)
        self.assertIn("a deliberative run, whose raise is the human's to decide, or a delegated auto run, which is offered none", text)

    def test_the_recovery_reference_is_generated_from_the_contract(self):
        generator = (self.ROOT / "scripts/gen_recovery_reference.py").read_text(encoding="utf-8")
        recovery = self.reference("recovery.md")
        line = ("A ceiling is spent: stop honestly with `report_convergence intent=stop`; in a deliberative or interactive auto run "
                "you may propose a raise for human approval, in a delegated auto run start a fresh run.")
        self.assertIn(line, generator)
        self.assertIn(line, recovery)

    # --- no text says auto asks about evidence or decisions ----------------------------------------------------------------

    _re = __import__("re")
    # a negation that governs the asking: within a few words before the verb, or right after it ("asks no one", "asks nothing")
    NEGATION_BEFORE = _re.compile(r"\b(?:no|not|never|nothing|nobody|neither|nor|cannot|can't|don't|doesn't|won't|none)\b", _re.IGNORECASE)
    NEGATION_AFTER = _re.compile(r"^\W*(?:\w+\W+){0,2}?(?:no one|nobody|nothing|none|no human|no person|not)\b", _re.IGNORECASE)
    CLAUSE_END = _re.compile(r"[;:,]|\b(?:but|yet|and|while|whereas)\b", _re.IGNORECASE)
    HUMAN = r"(?:the |a )?(?:human|person)"
    # the verbs by which a text says a person is asked, shown, or made to confirm
    ASKS = _re.compile(
        r"(?<!proposal )\bask(?:s|ed|ing)?\b|(?<!when )(?<!is )(?<!are )\bpresents?\b|\bshows? " + HUMAN + r"\b"
        r"|\bconfirm(?:s|ed)? by " + HUMAN + r"\b|\b" + HUMAN + r" confirms?\b"
        r"|\bputs?\b[^.;]{0,60}?\bto " + HUMAN + r"\b|\b(?:is|are) shown to\b|\bwaits? (?:for|on) " + HUMAN + r"\b",
        _re.IGNORECASE)
    ABOUT = _re.compile(r"\b(?:evidence|decisions?|sources?|records?|confirm\w*|research|runtime notes?|urls?)\b", _re.IGNORECASE)
    AUTO = _re.compile(r"\bauto\b|\bunattended\b", _re.IGNORECASE)
    # a sentence that sends the reader to deliberative mode for a human's confirmation says where a human confirms
    DELIBERATIVE = _re.compile(r"\bstart (?:the run|a run|it) in deliberative mode|\bstart a deliberative run", _re.IGNORECASE)

    @classmethod
    def _asked(cls, flat):
        """Whether some asking verb of the sentence is not itself negated: no negation within four words before it, none
        right after it."""
        for verb in cls.ASKS.finditer(flat):
            clause = cls.CLAUSE_END.split(flat[:verb.start()])[-1]  # a negation of an earlier clause governs nothing here
            before = clause.split()[-4:]
            if any(cls.NEGATION_BEFORE.search(word) for word in before):
                continue
            if cls.NEGATION_AFTER.search(flat[verb.end():]):
                continue
            return True
        return False

    @classmethod
    def auto_asks_about_evidence(cls, text):
        """Sentences that say an auto run asks a human about evidence or a decision. A negation vetoes only the verb it
        governs (a few words before it, or "asks no one"/"asks nothing" right after it), and a sentence that names deliberative
        mode as the place a human confirms is not one."""
        found = []
        for sentence in cls._re.split(r"(?<=[.!?])\s+|\n\s*\n|\n[-*] ", text):
            flat = " ".join(sentence.split())
            if (cls.AUTO.search(flat) and cls.ABOUT.search(flat) and not cls.DELIBERATIVE.search(flat) and cls._asked(flat)):
                found.append(flat)
        return found

    @classmethod
    def contract_texts(cls):
        """Every string of the public contract: the texts an agent or a host reads."""
        found = []

        def walk(node):
            if isinstance(node, str):
                found.append(node)
            elif isinstance(node, dict):
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(cls.CONTRACT)
        return found

    def test_no_text_the_agent_reads_says_auto_asks_a_human_about_evidence_or_decisions(self):
        """The scope: the skill, the references, every string of the contract, and the upgrade guide."""
        files = [self.ROOT / "plugins/empirica/skills/empirica/SKILL.md", *sorted(self.REFERENCES.glob("*.md")),
                 self.ROOT / "doc/upgrading-empirica-7.md"]
        offenders = {path.name: self.auto_asks_about_evidence(path.read_text(encoding="utf-8")) for path in files}
        offenders["public-contract.json"] = [flat for text in self.contract_texts() for flat in self.auto_asks_about_evidence(text)]
        self.assertEqual({name: found for name, found in offenders.items() if found}, {})

    FLAGGED = (
        "An auto run asks the human to confirm a web source at the end of the pass.",
        "In auto mode the human is asked to decide each needs-decision claim.",
        "Auto presents the pending records to a person when you report.",
        "- Unattended mode asks a human about the evidence.",
        "In auto mode the agent shows the human each web source before the audit.",
        "An auto run's dialog presents the pending evidence.",
        "A deliberative run presents records; auto runs do too.",
        "An auto run puts each record to the human when you report.",
        "In an auto run, a person confirms each runtime note.",
        "In an auto run each source is confirmed by a person.",
        "Auto mode waits for the human to confirm web sources.",
        "In auto mode a batch of records is shown to the person.",
        "An auto run asks the human to confirm a web source, not the configuration.",
        "In auto, the human is asked about each decision without delay.",
        "An auto run never skips asking a human about evidence; it asks at each report.",
        "Auto presents nothing about evidence but asks about decisions.",
    )
    ACCEPTED = (
        "An auto run asks a human nothing about evidence or decisions.",
        "An auto run asks nobody about evidence.", "An auto run asks no one about evidence.",
        "In auto mode the human is never asked about evidence.", "No human is asked about evidence in an auto run.",
        "In auto mode research decides it and nobody is asked.",
        "An interactive auto run asks a human for its configuration and for a raise above the approved ceilings; a delegated run asks nobody.",
        "An auto run asks a human for its configuration and an interactive run for a raise.",
        "To have a human confirm evidence in an auto run, start the run in deliberative mode.",
        "A deliberative run asks the human to confirm each record.", "The run is automatic.",
        # a proposal "asks for" a raise, and "present" can be an adjective: neither is a person being asked
        "An interactive auto run may propose a raise (governance.raises says a proposal asks for one), and a human answer, when present, decides.",
    )

    def test_the_guard_flags_a_sentence_that_says_auto_asks_and_accepts_the_true_ones(self):
        for text in self.FLAGGED:
            self.assertEqual(len(self.auto_asks_about_evidence(text)), 1, text)
        for text in self.ACCEPTED:
            self.assertEqual(self.auto_asks_about_evidence(text), [], text)

    # --- the contract texts, reworded (the writing pass) ----------------------------------------------------------------

    UNCONFIRMED = (
        "Claim {claim_id} has only research that a human would have to confirm, and an auto run asks no one, so that research does not count. "
        "Cite a file in the workspace (the service checks it) or a local file your auditor can read, or run a spike; or call "
        "report_convergence intent=stop to end with the claim as a residual. To have a human confirm sources, start a deliberative run.")
    UNCONFIRMED_OBSERVER = "Claim {claim_id} has only sources a person must confirm; it needs a source the service or auditor can check, or a spike."
    BUDGET_NOTICE = ("Empirica paused: the {resource} budget is exhausted and the run is not converged. No approval was granted by this "
                     "pause. End honestly with report_convergence intent=stop. A deliberative run may instead "
                     "propose a raise for human approval, if this host can ask a person; a delegated auto run starts a fresh run.")
    AUTO_CEILING = "A delegated auto run cannot raise its ceilings, audit retries included. Keep or lower them, or start a fresh run with a larger approved size."
    AUTO_CEILING_APPROVED = ("A delegated auto run cannot raise its approved ceilings: keep or lower them, or start a fresh run "
                             "with a larger approved size.")

    def test_the_reworded_contract_texts_are_pinned(self):
        reasons = self.CONTRACT["reasons"]
        research = reasons["claim.research_missing"]
        self.assertEqual(research["case_messages"]["unconfirmed"], self.UNCONFIRMED)
        self.assertEqual(research["ui_case_messages"]["unconfirmed"], self.UNCONFIRMED_OBSERVER)
        self.assertEqual(research["ended_case_messages"]["unconfirmed"],
                         "No research that counts was recorded for claim {claim_id} before the run stopped.")
        self.assertEqual(self.CONTRACT["settlement_notices"]["budget_exhausted"], self.BUDGET_NOTICE)
        self.assertEqual(reasons["governance.auto_ceiling"]["message"], self.AUTO_CEILING)
        self.assertEqual(reasons["governance.auto_ceiling"]["case_messages"]["approved"], self.AUTO_CEILING_APPROVED)

    def test_each_reworded_text_names_the_next_action_and_is_positive(self):
        reasons = self.CONTRACT["reasons"]
        texts = {"unconfirmed (agent)": (self.UNCONFIRMED, ("report_convergence intent=stop", "run a spike", "Cite a file")),
                 "unconfirmed (observer)": (self.UNCONFIRMED_OBSERVER, ("the service or auditor can check", "a spike")),
                 "budget_exhausted": (self.BUDGET_NOTICE, ("report_convergence intent=stop", "propose a raise", "fresh run")),
                 "auto_ceiling": (self.AUTO_CEILING, ("Keep or lower", "fresh run")),
                 "auto_ceiling approved": (self.AUTO_CEILING_APPROVED, ("keep or lower", "fresh run"))}
        for name, (text, actions) in texts.items():
            with self.subTest(name):
                for action in actions:
                    self.assertIn(action, text)
                for negative in ("Never ", "never ", "Do not", "do not", "none of it counts", "The bound in the parameters"):
                    self.assertNotIn(negative, text)
        # the observer sentence stays within the observer's own length rule
        from core.observer import UI_MESSAGE_LIMIT, message_problems
        self.assertLessEqual(len(self.UNCONFIRMED_OBSERVER), UI_MESSAGE_LIMIT)
        self.assertEqual(message_problems("claim.research_missing", reasons["claim.research_missing"]), [])

    def test_the_budget_clauses_say_who_is_offered_the_raise(self):
        text = json.dumps(self.CONTRACT)
        for pinned in ("Deliberative runs and interactive auto runs recover with budget.raise (a proposal above the approved ceilings, which the human approves) or residual.accept;"
                       " a delegated auto run is never offered budget.raise and recovers by residual.accept or by starting a fresh run.",
                       "A delegated auto run has nobody to ask, so it recovers from exhaustion by a fresh run, never by budget.raise."):
            self.assertIn(pinned, text)
        self.assertNotIn("restart-only", text)
        self.assertNotIn("excludes budget.raise", text)
        evidence = self.CONTRACT["next_actions"]["human.confirm_evidence"]["description"]
        self.assertTrue(evidence.endswith("an auto run asks no one about evidence."), evidence)

    def test_the_recovery_note_for_an_exhausted_budget_is_mode_exact(self):
        generator = (self.ROOT / "scripts/gen_recovery_reference.py").read_text(encoding="utf-8")
        self.assertNotIn("in deliberative mode you may propose a raise for human approval, in auto mode start a fresh run", generator)

    # --- the upgrade guide ----------------------------------------------------------------------------------------------

    def test_the_upgrade_guide_has_one_section_for_the_7_1_0_auto_behaviour(self):
        text = (self.ROOT / "doc/upgrading-empirica-7.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("(7.1.0)"), 1)
        start = text.index("## Auto mode asks once and decides its own claims (7.1.0)")
        section = " ".join(text[start:text.index("\n## ", start + 5)].split())
        for pinned in ("An auto run asks a person once, for its configuration",
                       "A source that needs a person to confirm it is inert",
                       "`claim.research_missing`",
                       "An interactive auto run asks one more thing",
                       "`governance.raises`",
                       "A delegated auto run has nobody to ask",
                       "A `needs-decision` claim in an auto run is decided by the agent",
                       "Readings that change for a run written by an earlier 7.0.x",
                       "start the run in deliberative mode"):
            self.assertIn(pinned, section)
        # the earlier slices' paragraphs are merged: each fact once
        self.assertEqual(section.count("Two readings change"), 0)
        self.assertEqual(section.count("To confirm evidence yourself"), 0)
        self.assertEqual(section.count("start the run in deliberative mode"), 1)
        self.assertEqual(section.count("`governance.raises`"), 1)

    def test_the_upgrade_guide_names_the_auto_turn_end_bounds_and_the_lean_skill(self):
        text = (self.ROOT / "doc/upgrading-empirica-7.md").read_text(encoding="utf-8")
        start = text.index("## Auto mode asks once and decides its own claims (7.1.0)")
        section = " ".join(text[start:text.index("\n## ", start + 5)].split())
        bounds = json.loads((self.ROOT / "contracts/empirica/v2/host-boundaries.json").read_text())["turn_end"]["bounds"]
        self.assertEqual(bounds["enforced_by"], ["claude", "pi"])
        for pinned in (
            "An auto run's turn end continues the agent's work while the view says the agent has a step.",
            f"Both hosts bound the continuations by `turn_end.bounds`: the K-th turn end on one observation basis (K = {bounds['K']}), "
            f"or the T-th in a chain (T = {bounds['T']}), ends the chain with the `unattended_residual` notice, which offers `report_convergence intent=stop`.",
            "The last continuation before a bound carries that notice as its directive,",
            "`unattended_continue` no longer offers the stop.",
            "A run whose view names nobody to act (`turn_end_actor` `none`) continues once, with `unattended_residual` as the directive,",
            "- **A shorter skill.**",
            "the view, the tool descriptions and the references carry the rules",
        ):
            self.assertIn(pinned, section)
        for stale in ("Claude continues a turn once while nothing changed", "Pi continues at most two turn ends", "fifteen in a chain"):
            self.assertNotIn(stale, section)

    def test_the_inert_kinds_are_named_by_their_form_wherever_the_text_names_a_kind(self):
        guide = " ".join((self.ROOT / "doc/upgrading-empirica-7.md").read_text(encoding="utf-8").split())
        self.assertIn("an `http(s)` URL, a `runtime` note with no file path, or a file the auditor's host cannot read", guide)
        self.assertNotIn("a web page", guide)
        self.assertNotIn("a `web` source", self.reference("evidence.md"))


class ReferenceCleanupTests(unittest.TestCase):
    """S11: the references and the agent definitions carry no internal decision-record citation, no operator-only fact, and
    no field the author view does not render; the auditor's duties live in the agent definition alone."""

    ROOT = Path(__file__).resolve().parents[3]
    SKILL_DIR = ROOT / "plugins/empirica/skills/empirica"
    REFERENCES = sorted((SKILL_DIR / "references").glob("*.md"))
    AGENTS = sorted((ROOT / "plugins/empirica/agents").glob("*.md"))
    # a decision-record citation in any spelling: case, plural, `#`, and a Unicode hyphen (U+2011) as an editor may produce
    ADR = __import__("re").compile(r"\bADRs?\s*[-\u2011#]?\s*\d|\badr[-/]\d|doc/adr/", __import__("re").IGNORECASE)
    ISSUE = __import__("re").compile(r"\bissue\s*#?\d+", __import__("re").IGNORECASE)

    def reference(self, name):
        return (self.SKILL_DIR / "references" / name).read_text(encoding="utf-8")

    @staticmethod
    def section(text, heading):
        start = text.index(f"\n### {heading}\n")
        following = text.find("\n## ", start + 1)
        following_sub = text.find("\n### ", start + 5)
        stops = [i for i in (following, following_sub) if i != -1]
        return text[start:min(stops)] if stops else text[start:]

    def test_no_skill_reference_or_agent_cites_a_decision_record(self):
        offenders = []
        for path in [self.SKILL_DIR / "SKILL.md", *self.REFERENCES, *self.AGENTS]:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if self.ADR.search(line) or self.ISSUE.search(line):
                    offenders.append(f"{path.relative_to(self.ROOT)}:{number}")
        self.assertEqual(offenders, [])

    def test_the_guard_sees_every_citation_shape(self):
        for text in ("(ADR-0067)", "ADR 0063 owns it", "see ADR0068", "doc/adr/0061-x.md", "adr-0067", "ADRs 63 and 67",
                     "ADR #67", "ADR\u20110067", "Adr-63", "../adr/0067"):
            self.assertTrue(self.ADR.search(text), text)
        for text in ("an architecture decision record in the repository's normal ADR/MADR form", "the adr tool", "the ADR tool"):
            self.assertFalse(self.ADR.search(text), text)
        for text in ("issue #71", "see Issue 71", "issue#7"):
            self.assertTrue(self.ISSUE.search(text), text)
        for text in ("an issue with the host", "the issue tracker", "issue: none"):
            self.assertFalse(self.ISSUE.search(text), text)

    def test_the_confirmation_section_speaks_in_the_words_the_view_prints(self):
        labels = json.loads((self.ROOT / "contracts/empirica/v2/public-contract.json").read_text())["author_view"]["labels"]
        text = self.reference("evidence.md")
        section = self.section(text, "What still needs confirming")
        for field in ("confirm: audit", "confirm: human", "confirm: unobserved", "confirm: unavailable", "verification: {"):
            self.assertNotIn(field, text, field)
        for key in ("pending_human", "pending_audit", "confirmed_by_human", "rejected_by_human", "waiting",
                    "waiting_none_error", "waiting_unobserved", "waiting_unavailable", "waiting_terminal",
                    "waiting_already_presented", "waiting_interaction_limit"):
            self.assertIn(labels[key], section, key)
        self.assertIn("governance.terminal_stop_pending", section, "the terminal-consent row says what the human consents to")
        # the host-neutral rule for asking again is not filed under one host's name
        self.assertNotIn("paragraph on Claude", text)
        self.assertEqual(section.count("the rule for asking again below"), 1)

    def test_the_audit_reference_keeps_the_host_procedure_and_points_to_the_agent_for_the_duties(self):
        text = self.reference("audit.md")
        for kept in ("## Procedure", "## Independence reporting", "Report only what the host observed:",
                     '{"agent":"empirica.empirica-auditor","task":"Audit the host-provided dossier."}'):
            self.assertIn(kept, text)
        for deleted in ("## Preconditions", "## Stale pending retry", "## Authority", "## Terminal replay",
                        "retrieves every record the dossier marks", "searches for missing material claims",
                        "evaluates freeze honesty", "judged for coherence only", "Only `agent` and `task` are allowed"):
            self.assertNotIn(deleted, text)
        self.assertEqual(text.count("agents/empirica-auditor.md"), 1, "one pointer to the auditor's duties")
        self.assertTrue((self.ROOT / "plugins/empirica/agents/empirica-auditor.md").is_file())
        for step in ("1. Let the host bind", "2. On Claude, invoke exactly one", "3. Reread the run"):
            self.assertIn(step, text)

    def test_the_assurance_boundary_and_the_audit_limit_are_stated_where_they_are_read(self):
        # S10 review F1, F2: the skill dropped these; the references read at the point of use carry them
        boundary = "Convergence is design-time assurance, not a security boundary; CI remains the trust boundary for shipped code."
        limit = "An audit may block but cannot manufacture deterministic machine evidence, so it never replaces a needed spike."
        telemetry = "Counts and reason strings\n   are telemetry, not authority."
        self.assertEqual(self.reference("handoff.md").count(boundary), 1)
        self.assertEqual(self.reference("audit.md").count(limit), 1)
        self.assertEqual(self.reference("audit.md").count(telemetry), 1)

    def test_the_governance_reference_holds_no_operator_only_fact(self):
        text = self.reference("governance.md")
        for fact in ("empirica.hitl/1", "0400", "CAS", "fingerprint", "EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS",
                     "CLAUDE_CODE_SUBAGENT_MODEL", "ANTHROPIC_DEFAULT", "EMPIRICA_AUTO_DELEGATION", "EMPIRICA_MAX_",
                     "empirica.run/4", "host-boundaries.json", "ctx.hasUI", "native human-approval"):
            self.assertNotIn(fact, text, fact)
        self.assertIn("governance.terminal_stop_pending", text, "the agent-facing recovery stays")

    def test_the_hosts_doc_links_the_operations_doc_beside_the_host_ranges(self):
        hosts = (self.ROOT / "doc/hosts.md").read_text(encoding="utf-8")
        self.assertEqual(hosts.count("](governance-operations.md)"), 1)
        self.assertLess(hosts.index("](governance-operations.md)"), hosts.index("## Empirica 7 spike authority"),
                        "linked in the opening supported-surfaces block")

    def test_the_operator_facts_moved_to_the_operations_doc(self):
        doc = (self.ROOT / "doc/governance-operations.md").read_text(encoding="utf-8")
        for fact in ("empirica.hitl/1", "0400", "EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS", "CLAUDE_CODE_SUBAGENT_MODEL",
                     "EMPIRICA_AUTO_DELEGATION", "EMPIRICA_MAX_", "empirica.run/4"):
            self.assertIn(fact, doc, fact)
        self.assertNotRegex(doc, self.ADR)


if __name__ == "__main__":
    unittest.main(verbosity=2)
