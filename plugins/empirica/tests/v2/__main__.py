#!/usr/bin/env python3
"""Single committed runner for the Empirica 2.0 D4-S1 conformance suite.

    python3 plugins/empirica/tests/v2/__main__.py             # real-SUT RED mode
    python3 plugins/empirica/tests/v2/__main__.py --preflight  # structural preflight (GREEN)

Adds the two path roots (``plugins/empirica`` for ``application``/``core``/``vendor`` and this
directory for ``driver``/``sut_adapter``/``assertions``/``test_*``), discovers every ``test_*.py``
module here, and runs the combined unittest suite.

* **Real-SUT red mode (default):** the ``empirica/v2`` seam is absent on the pre-D6/D7 tree, so
  every case fails with a named FAILURE (owner + behavior), never a collection ERROR. Exit nonzero.
* **Structural preflight (``--preflight``):** GREEN, NO driver/behavioral simulation. Validates the
  harness without instantiating a SUT/probe or run/child/evidence/freeze state. Zero post-binding
  semantic behavior is executed. Exit 0 iff every structural check passes.
"""
from __future__ import annotations

import importlib
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent.parent  # plugins/empirica — makes application/core/vendor importable

sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(HERE))


def _load_suite() -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for path in sorted(HERE.glob("test_*.py")):
        module = importlib.import_module(path.stem)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite


# Structural preflight (D4-S1). No SUT/probe, no behavioral simulation.

# Exact named strict methods allowed raw_dispatch / inject_run_state (D4 spec §8). Other methods
# must use self.dispatch; a method may call a prerequisite helper instead, so this does NOT require
# every method to call dispatch directly.
_STRICT_RAW_METHODS: set[tuple[str, str]] = {
    ("test_protocol_host.py", "test_v2_identity_checked_before_decoding"),
    ("test_protocol_host.py", "test_noncurrent_wire_and_persisted_state_are_rejected"),
    ("test_protocol_host.py", "test_unknown_fields_actions_fail_closed"),
    ("test_projection_context.py", "test_unknown_references_fail_closed"),
}
# Explicit wrapper (LiveDriver) -> fake-port pairs. Names differ, so params are compared by
# position (kind + default); names compared only where identical (semantic positional mapping).
_FAKE_PAIRS: list[tuple[str, str, str]] = [
    ("workspace_write", "FakeWorkspace", "write"),
    ("workspace_delete", "FakeWorkspace", "delete"),
    ("workspace_error", "FakeWorkspace", "set_error"),
    ("harness_complete", "FakeSpikeHarness", "complete"),
    ("artifacts", "FakeArtifactRepository", "all"),
    ("inject_run_state", "FakeRunRepository", "inject"),  # state/value semantic name mapping
    # D4-S2 policy-free telemetry seams (transport facts only).
    ("workspace_observe_history", "FakeWorkspace", "observe_history"),
    ("harness_invocations", "FakeSpikeHarness", "invocations"),
]


def _run_preflight() -> int:
    import ast
    import inspect
    import json

    import jsonschema

    import assertions
    import driver
    import sut_adapter
    from assertions import (
        start_run, resolve_run, observe_action, evaluate, get_run, get_argument,
        get_contract, restore_run, action_graph, action_research,
        action_spike_request, action_configure_run, action_route, action_investigate,
        action_freeze, action_dispatch, action_child_reserve, action_evidence_leaf,
        action_attribution, action_child_event, action_audit_verdict,
        build_child_event_payload, build_attribution_payload, build_evidence_leaf_payload,
    )

    errors: list[str] = []
    test_paths = sorted(HERE.glob("test_*.py"))
    if len(test_paths) != 8:
        errors.append(f"expected 8 test_*.py modules, found {len(test_paths)}")

    # ---- check 1: collect exactly 50 unique test_* methods ----
    method_keys: set[str] = set()
    for path in test_paths:
        mod = importlib.import_module(path.stem)
        for cname in dir(mod):
            cls = getattr(mod, cname)
            if not (isinstance(cls, type) and issubclass(cls, unittest.TestCase)):
                continue
            for mname in dir(cls):
                if mname.startswith("test_") and callable(getattr(cls, mname)):
                    method_keys.add(f"{path.stem}.{cname}.{mname}")
    if len(method_keys) != 50:
        errors.append(f"expected exactly 50 unique test_* methods, found {len(method_keys)}")

    # ---- check 2: reject skips, expected failures, placeholder/TODO bodies, probe/checkpoint symbols ----
    _TODO_RE = re.compile(r"#\s*(TODO|FIXME|XXX)\b", re.IGNORECASE)

    def _is_placeholder(body: list[ast.stmt]) -> bool:
        stmts = list(body)
        if stmts and isinstance(stmts[0], ast.Expr) and isinstance(stmts[0].value, ast.Constant) \
                and isinstance(stmts[0].value.value, str):
            stmts = stmts[1:]
        if not stmts:
            return True
        ok = (ast.Pass,)
        for s in stmts:
            if isinstance(s, ok) or (isinstance(s, ast.Return) and s.value is None) or \
               (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is Ellipsis):
                continue
            return False
        return True

    def _has_skip(func: ast.FunctionDef) -> bool:
        for dec in func.decorator_list:
            name = dec.id if isinstance(dec, ast.Name) else (dec.attr if isinstance(dec, ast.Attribute) else None)
            if name in ("skip", "expectedFailure", "skipIf", "skipUnless"):
                return True
        return False

    def _has_banned_symbol(node: ast.AST) -> bool:
        for child in ast.walk(node):
            name = child.id if isinstance(child, ast.Name) else (child.attr if isinstance(child, ast.Attribute) else None)
            if name and ("checkpoint" in name.lower() or "probe" in name.lower()):
                return True
        return False
    for path in test_paths:
        src = path.read_text(encoding="utf-8")
        src_lines = src.splitlines()
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            qual = f"{path.stem}.{node.name}"
            if _has_skip(node):
                errors.append(f"{qual}: skip/expectedFailure decorator is forbidden")
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "skipTest":
                    errors.append(f"{qual}: self.skipTest() is forbidden")
            if _is_placeholder(node.body):
                errors.append(f"{qual}: empty/pass/placeholder body")
            # actual TODO/FIXME/XXX marker rejection in the method's source span (comments)
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            for ln in range(node.lineno, end + 1):
                if ln - 1 < len(src_lines) and _TODO_RE.search(src_lines[ln - 1]):
                    errors.append(f"{qual}:{ln}: TODO/FIXME/XXX marker in body")
                    break
            if _has_banned_symbol(node):
                errors.append(f"{qual}: remaining checkpoint/probe symbol")

    # ---- check 3: representative request builders against the request schema ----
    request_schema = assertions._REQUEST_SCHEMA
    _obs = lambda a: observe_action(run_id="r1", action=a)  # noqa: E731
    samples = [
        start_run(goal="g"),
        start_run(goal="g", budgets={"max_passes": 1, "max_spawns": 1}, modes={"multi_provider": False}),
        resolve_run(), _obs(action_graph()), _obs(action_graph(payload={"k": 1})),
        _obs(action_research(claim_id="c0", source_kind="code", result="supports")),
        _obs(action_spike_request(claim_id="c0", command="pytest", dependent_files=["f.py"])),
        _obs(action_configure_run(budgets={"max_passes": 1})),
        _obs(action_configure_run(modes={"multi_provider": True})),
        _obs(action_route(reason="primary-claim")), _obs(action_investigate()), _obs(action_freeze()),
        _obs(action_dispatch(target="claim")), _obs(action_dispatch(target="claim", claim_id="c0")),
        _obs(action_child_reserve(purpose="audit", role_profile="claude-code@2.1.270", execution="async")),
        _obs(action_child_reserve(purpose="audit", role_profile="claude-code@2.1.270", execution="foreground", deadline="2026-01-01T00:00:00Z")),
        _obs(action_evidence_leaf(payload=build_evidence_leaf_payload(
            harness_request_id="hreq-pf", command_digest="sha256:" + "0" * 64,
            prerequisite_research_ids=["sha256:" + "1" * 64],
            file_bindings=[{"path": "src/main.py", "sha256": "sha256:" + "2" * 64}],
            exit_code=0, result_digest="sha256:" + "3" * 64))),
        _obs(action_evidence_leaf(payload=build_evidence_leaf_payload(
            harness_request_id="hreq-pf2", command_digest="sha256:" + "4" * 64,
            prerequisite_research_ids=["sha256:" + "5" * 64],
            file_bindings=[{"path": "src/other.py", "sha256": "sha256:" + "6" * 64}],
            exit_code=1, result_digest="sha256:" + "7" * 64))),
        _obs(action_attribution(payload=build_attribution_payload(
            subject_kind="covered_actor", subject_id="actor-1",
            child_id=None, provider_id="p1", model_id="m1",
            observed_by="host", covered_artifact_ids=["sha256:" + "8" * 64]))),
        _obs(action_child_event(child_id="ch1", payload=build_child_event_payload(
            "completed", native_id="n1", result_digest="sha256:" + "9" * 64))),
        _obs(action_audit_verdict(child_id="ch1", payload={
            "verdict": "pass", "findings": ["ok"],
            "argument_digest": "sha256:" + "a" * 64,
            "goal_digest": "sha256:" + "b" * 64,
            "frozen_scope_digest": None,
            "deferred_scope_digest": "sha256:" + "c" * 64,
            "reviewed_claims": [{"claim_id": "C0", "evidence_digest": "sha256:" + "d" * 64}],
            "scope_review": None,
        })),
        evaluate(run_id="r1"), evaluate(run_id="r1", intent="stop"),
        get_run(run_id="r1"), get_argument(run_id="r1"),
        get_contract(target="index"), get_contract(target="section", section_id="evidence/freshness"),
        get_contract(target="full"), restore_run(run_id="r1"),
    ]
    n_envelopes = len(samples)
    for env in samples:
        try:
            jsonschema.validate(instance=env, schema=request_schema)
        except jsonschema.ValidationError as exc:
            errors.append(f"request builder {env.get('command', {}).get('type')!r} invalid: {exc.message}")

    # ---- check 4: accepted response fixtures through response schema; one malformed rejected ----
    response_schema = assertions._RESPONSE_SCHEMA
    fixtures_dir = assertions._V2 / "fixtures"
    n_fixtures = 0
    if not fixtures_dir.is_dir():
        errors.append(f"response fixtures directory not found: {fixtures_dir}")
    else:
        for fx_path in sorted(fixtures_dir.glob("*.json")):
            fx = json.loads(fx_path.read_text(encoding="utf-8"))
            if "expected" not in fx:
                continue
            n_fixtures += 1
            try:
                jsonschema.validate(instance=fx["expected"], schema=response_schema)
            except jsonschema.ValidationError as exc:
                errors.append(f"fixture {fx_path.name} 'expected' failed response-schema: {exc.message}")
    malformed = {"protocol": assertions.protocol(), "request_id": "x", "result": {"type": "NotARealResultType"}}
    try:
        jsonschema.validate(instance=malformed, schema=response_schema)
        errors.append("malformed response was NOT rejected by response.schema.json")
    except jsonschema.ValidationError:
        pass  # good — rejected
    if n_fixtures == 0:
        errors.append("no accepted response fixtures with 'expected' were validated")

    # ---- check 5: AST dispatch discipline (exact-method raw allowlist) ----
    def _parent_map(tree):
        pm = {}
        for n in ast.walk(tree):
            for c in ast.iter_child_nodes(n):
                pm[c] = n
        return pm

    def _enclosing_test(pm, target):
        p = pm.get(target)
        while p is not None:
            if isinstance(p, ast.FunctionDef) and p.name.startswith("test_"):
                return p.name
            p = pm.get(p)
        return None

    for path in test_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        pm = _parent_map(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "request":
                errors.append(f"{path.stem}:{node.lineno}: .request() outside central dispatch")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("raw_dispatch", "inject_run_state"):
                if (path.name, _enclosing_test(pm, node)) not in _STRICT_RAW_METHODS:
                    errors.append(f"{path.stem}:{node.lineno}: {node.func.attr}() outside exact strict methods")
    ast_assn = ast.parse((HERE / "assertions.py").read_text(encoding="utf-8"), filename="assertions.py")
    if not any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "request"
               for n in ast.walk(ast_assn) if isinstance(n, ast.FunctionDef) and n.name in ("dispatch", "raw_dispatch")
               for c in ast.walk(n)):
        errors.append(".request() not found inside central dispatch/raw_dispatch")

    # ---- check 6: full signature agreement (kind/name/default) ----
    proto_names = {n for n, v in vars(driver.ConformanceDriver).items() if callable(v) and not n.startswith("_")}
    live_names = {n for n, v in vars(sut_adapter.LiveDriver).items() if callable(v) and not n.startswith("_")}
    if missing := proto_names - live_names:
        errors.append(f"LiveDriver missing ConformanceDriver methods: {sorted(missing)}")
    if extra := live_names - proto_names:
        errors.append(f"LiveDriver has methods not in ConformanceDriver: {sorted(extra)}")
    def _params_without_self(obj) -> list[inspect.Parameter]:
        return [p for n, p in inspect.signature(obj).parameters.items() if n != "self"]

    def _cmp(label, a, b, *, names: bool = True) -> None:
        if len(a) != len(b):
            errors.append(f"{label}: param count {len(a)} != {len(b)}")
            return
        for i, (pa, pb) in enumerate(zip(a, b)):
            if pa.kind != pb.kind:
                errors.append(f"{label}: pos {i} kind {pa.kind!r} != {pb.kind!r}")
            if pa.default != pb.default:
                errors.append(f"{label}: pos {i} default {pa.default!r} != {pb.default!r}")
            if names and pa.name != pb.name:
                errors.append(f"{label}: pos {i} name {pa.name!r} != {pb.name!r}")

    for name in sorted(proto_names & live_names):
        _cmp(f"{name}: Proto/Live", _params_without_self(getattr(driver.ConformanceDriver, name)),
             _params_without_self(getattr(sut_adapter.LiveDriver, name)))
    for wrapper, fake_cls, fake_meth in _FAKE_PAIRS:
        fake = getattr(driver, fake_cls, None)
        if fake is None:
            errors.append(f"fake port class {fake_cls} not found")
            continue
        _cmp(f"{wrapper}->{fake_cls}.{fake_meth}", _params_without_self(getattr(sut_adapter.LiveDriver, wrapper)),
             _params_without_self(getattr(fake, fake_meth)), names=False)  # semantic positional mapping (state/value)

    # ---- check 7: copied full inventories and permissive patterns ----
    inv_sets = {frozenset(assertions.PROFILE_IDS): "profiles", frozenset(assertions.HOST_TIERS): "tiers",
                frozenset(assertions.STATUSES): "statuses", frozenset(assertions.CHILD_STATES): "child_states",
                frozenset(assertions.CHILD_TERMINAL): "child_terminal", frozenset(assertions.REASONS): "reasons",
                frozenset(assertions.NEXT_ACTIONS): "next_actions", frozenset(assertions.SECTIONS): "sections",
                frozenset(assertions.AUTHOR_ACTIONS + assertions.TRUSTED_ACTIONS): "actions",
                # D2E presentation-selector arrays: copying any as a literal is a copied fallback table.
                frozenset(assertions.SELECTOR_UNKNOWN_REASON_SECTIONS): "unknown_reason_sections"}
    for _ctx, _secs in assertions.SELECTOR_CONTEXT_SECTIONS.items():
        if len(_secs) >= 2:
            inv_sets[frozenset(_secs)] = f"context_sections[{_ctx!r}]"

    def _str_collection(node: ast.AST) -> list[str] | None:
        if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return None
        out = [el.value for el in node.elts if isinstance(el, ast.Constant) and isinstance(el.value, str)]
        return out if len(out) == len(node.elts) else None

    for path in test_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        pm = _parent_map(tree)
        for node in ast.walk(tree):
            strs = _str_collection(node)
            if strs and len(strs) >= 2 and frozenset(strs) in inv_sets:
                errors.append(f"{path.stem}:{node.lineno}: copied full {inv_sets[frozenset(strs)]} inventory")
            if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
                if any(isinstance(v, ast.Constant) and v.value is True for v in node.values):
                    errors.append(f"{path.stem}:{node.lineno}: permissive `or True`")
            if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)):
                if ast.dump(node.left) == ast.dump(node.comparators[0]):
                    errors.append(f"{path.stem}:{node.lineno}: self-membership (x in x)")
        # check 7d/7e: set-only selector comparison + conditional-only assertion
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef) or not func.name.startswith("test_"):
                continue
            asserts = [c for c in ast.walk(func)
                       if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                       and c.func.attr.startswith("assert")]
            if not asserts:
                continue
            calls_sel = any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                           and c.func.attr == "select_sections" for c in ast.walk(func))
            if calls_sel:
                has_set = any(c.func.attr.startswith("assertEqual") and len(c.args) >= 2
                              and any(isinstance(a, ast.Call) and isinstance(a.func, ast.Name)
                                      and a.func.id == "set" for a in c.args[:2]) for c in asserts)
                has_ord = any(c.func.attr.startswith("assertEqual") and len(c.args) >= 2
                              and not any(isinstance(a, ast.Call) and isinstance(a.func, ast.Name)
                                          and a.func.id == "set" for a in c.args[:2]) for c in asserts)
                if has_set and not has_ord:
                    errors.append(f"{path.stem}:{func.name}: set-only selector comparison (add ordered equality)")
            top = False
            for c in asserts:
                p, guarded = pm.get(c), False
                while p is not None and p is not func:
                    if isinstance(p, ast.If):
                        guarded = True
                        break
                    p = pm.get(p)
                if not guarded:
                    top = True
                    break
            if not top:
                errors.append(f"{path.stem}:{func.name}: conditional-only semantic assertion")

    # ---- check 8: helper-signature binding (unsupported helper keywords fail) ----
    # AST-walk test files for calls to ConformanceCase artifact/claim helper methods and verify
    # the keyword arguments match the actual method signature. This catches a signature mismatch
    # (e.g. calling find_argument_artifacts with an unsupported 'outcome' keyword) structurally
    # before SUT binding, so the absent seam can no longer hide a TypeError.
    _helper_targets = (
        "find_argument_artifacts", "find_one_argument_artifact",
        "assert_research_artifact", "assert_spike_request_artifact",
        "assert_spike_result_artifact", "assert_artifact_sequence_order",
        "get_argument_claim", "assert_claim_kind",
        # D4-S3a child/audit/operational helpers.
        "require_operational_int",
        "require_audit_scope", "require_pending_audit_child",
        "require_trusted_audit_attribution",
        "build_audit_verdict_payload", "assert_audit_view",
        # D4-S3b strict-result and persisted-operational-field helpers.
        "assert_block_sole_reason", "assert_no_persisted_operational_fields",
    )
    for path in test_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in _helper_targets:
                continue
            method = getattr(assertions.ConformanceCase, node.func.attr, None)
            if method is None:
                errors.append(f"{path.stem}:{node.lineno}: call to unknown helper "
                              f"{node.func.attr!r}")
                continue
            valid_kws = {n for n, p in inspect.signature(method).parameters.items()
                         if n != "self"}
            for kw in node.keywords:
                if kw.arg is None:  # **kwargs splat
                    continue
                if kw.arg not in valid_kws:
                    errors.append(
                        f"{path.stem}:{node.lineno}: {node.func.attr}() unsupported keyword "
                        f"{kw.arg!r}; valid: {sorted(valid_kws)}")

    # ---- check 9: build_audit_verdict_payload calls in test_audit.py must pass scope_review
    # explicitly (structural AST/text rule; no SUT instantiation or behavior execution) ----
    audit_path = HERE / "test_audit.py"
    if audit_path.is_file():
        atree = ast.parse(audit_path.read_text(encoding="utf-8"),
                          filename=str(audit_path))
        for node in ast.walk(atree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "build_audit_verdict_payload":
                continue
            kws = {kw.arg for kw in node.keywords if kw.arg is not None}
            if "scope_review" not in kws:
                errors.append(
                    f"test_audit.py:{node.lineno}: build_audit_verdict_payload() must pass "
                    f"scope_review explicitly (pass|fail)")

    # ---- check 10: presentation_selector loaded (not copied) + structural integrity (D2E) ----
    # Verify the canonical presentation_selector is loaded from the PublicContract in assertions
    # and that every key/section resolves. No SUT behavior is executed; no context/fallback table
    # is copied into a test file (check 7 flags literal copies of the selector arrays).
    if not getattr(assertions, "PRESENTATION_SELECTOR", None):
        errors.append("assertions must load presentation_selector from the PublicContract (D2E)")
    else:
        ctx_secs = assertions.SELECTOR_CONTEXT_SECTIONS
        # every context_sections key equals a registered operation_context
        for key in ctx_secs:
            if key not in assertions.OPERATION_CONTEXTS:
                errors.append(f"presentation_selector context_sections key {key!r} is not a "
                              f"registered operation_context")
        for key in assertions.OPERATION_CONTEXTS:
            if key not in ctx_secs:
                errors.append(f"operation_context {key!r} has no presentation_selector "
                              f"context_sections entry")
        # every selector array value resolves to a canonical section (ordered/unique)
        for _ctx, _secs in ctx_secs.items():
            if len(_secs) != len(set(_secs)):
                errors.append(f"presentation_selector context_sections[{_ctx!r}] must be unique")
            for sid in _secs:
                if sid not in assertions.SECTIONS:
                    errors.append(f"presentation_selector context_sections[{_ctx!r}] section "
                                  f"{sid!r} does not resolve to a canonical section")
        for sid in assertions.SELECTOR_TERMINAL_SECTIONS:
            if sid not in assertions.SECTIONS:
                errors.append(f"presentation_selector terminal_sections {sid!r} does not "
                              f"resolve to a canonical section")
        for sid in assertions.SELECTOR_UNKNOWN_REASON_SECTIONS:
            if sid not in assertions.SECTIONS:
                errors.append(f"presentation_selector unknown_reason_sections {sid!r} does not "
                              f"resolve to a canonical section")

    # ---- report ----
    if errors:
        for e in errors:
            print(f"  FAIL  {e}")
        print(f"\npreflight: {len(errors)} structural defect(s) found — FAILED")
        return 1
    print("preflight: structural only; zero post-binding semantic behavior was executed")
    print(f"  50 test_* methods; {n_envelopes} request envelopes validated; "
          f"{n_fixtures} response fixtures validated + 1 malformed rejected")
    print("  .request() in central dispatch only; raw_dispatch/inject_run_state in exact strict methods; "
          "Proto/Live + wrapper-to-fake signatures agree (kind/name/default); helper-signature binding "
          "check passes; build_audit_verdict_payload scope_review enforced; presentation_selector "
          "loaded + structurally integral; no SUT/probe instantiated")
    return 0


def main(argv: list[str]) -> int:
    if "--preflight" in argv:
        return _run_preflight()
    suite = _load_suite()
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
