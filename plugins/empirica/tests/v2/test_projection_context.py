"""D4 cases 36–42: structured Blocks, progressive contract, context selection, GetContract.

Owner: D9 (public-contract resolver/context selector). Section selection is a pure presentation
mapping from operation_context + ordered reason codes + terminal status (final DAG §3, D2E).
Cases 37–40 exercise the real pure D9 selector through the driver's selector seam with ONLY
canonical operation_context, ordered reason codes, and terminal status inputs (D4 spec §8) — they
do not infer context by manufacturing unrelated domain state. Expected outputs are built from
caller-supplied loaded registry lists via ``ordered_dedupe`` (no context/reason mapping copied
into the test). No ``_SAFE_FALLBACK`` fallback table and no copied banned-policy constant.
"""
from __future__ import annotations

import unittest

from assertions import (  # noqa: E402
    ConformanceCase, OPERATION_CONTEXTS, REASONS,
    SELECTOR_CONTEXT_SECTIONS, SELECTOR_TERMINAL_SECTIONS, SELECTOR_UNKNOWN_REASON_SECTIONS,
    TERMINAL_RUN_STATUSES, canonical_graph, contract_digest, evaluate, get_contract,
    observe_action, ordered_dedupe, start_run,
)


class ProjectionContextTests(ConformanceCase):
    GOAL = "Prove progressive contract selection is deterministic and bounded."

    # 36 — Two real Blocks: empty run → sole graph.missing; ordinary graph → sole claim.research_missing
    def test_block_reasons_match_canonical_and_scoped_affected(self):
        drv = self.bind_driver(
            "D9", "case-36",
            "Every Block has nonempty structured reasons; each reason matches canonical "
            "parameters, ordered next actions, ordered section IDs, and identifies affected "
            "obligation/witness when the reason is scoped")
        run_id = self.start_run(drv, goal=self.GOAL)
        # Block 1: empty run → exact sole graph.missing (not scoped: no affected)
        resp1 = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        result1 = self.assert_block_sole_reason(resp1, "graph.missing")
        self.assertNotIn("affected", result1["reasons"][0],
                         "graph.missing is not scoped and must not carry affected")
        # Block 2: admitted canonical ordinary graph → exact sole claim.research_missing (scoped)
        self.require_graph_admitted(drv, run_id, canonical_graph(n_claims=1, kind="ordinary"))
        resp2 = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        result2 = self.assert_block_sole_reason(resp2, "claim.research_missing")
        r = result2["reasons"][0]
        # Research-missing affected is exactly one obligation_id (no permissive affected-key subset)
        self.assertIn("affected", r, "claim.research_missing is scoped and must carry affected")
        self.assertEqual(set(r["affected"].keys()), {"obligation_id"},
                         "claim.research_missing affected must be exactly {obligation_id}, "
                         "not a permissive key subset")
        oid = r["affected"]["obligation_id"]
        # the obligation_id resolves to the returned RunView obligation
        obligations = result2["run"].get("obligations", {})
        all_oids = {o.get("id") for o in obligations.get("active", []) + obligations.get("deferred", [])}
        self.assertIn(oid, all_oids,
                      "claim.research_missing affected obligation_id must resolve to a "
                      "returned RunView obligation")

    # 37 — Every canonical operation context (no reasons/status) selects exact registry base; determinism
    def test_identical_inputs_select_identical_sections(self):
        drv = self.bind_driver(
            "D9", "case-37",
            "Identical operation context + ordered reason codes + terminal status selects "
            "identical sections (the real D9 selector, deterministic)")
        for context in OPERATION_CONTEXTS:
            with self.subTest(context=context):
                expected = SELECTOR_CONTEXT_SECTIONS[context]
                a = self.select_sections(drv, context, [], None)
                self.assertEqual(a, expected,
                                 f"no-reason/no-status output for {context!r} must equal the "
                                 f"registry context_sections base exactly and in order")
                b = self.select_sections(drv, context, [], None)
                self.assertEqual(a, b, "identical inputs must select identical ordered sections")
        # A known terminal status appends exact terminal_sections, first-occurrence dedupe
        for status in TERMINAL_RUN_STATUSES:
            with self.subTest(terminal=status):
                ctx = "block"
                expected = ordered_dedupe(SELECTOR_CONTEXT_SECTIONS[ctx], SELECTOR_TERMINAL_SECTIONS)
                out = self.select_sections(drv, ctx, [], status)
                self.assertEqual(out, expected,
                                 f"terminal status {status!r} must append exact terminal_sections, "
                                 f"first-occurrence deduped")

    # 38 — Selection unchanged when presentation inputs fixed (other state varies)
    def test_selection_invariant_to_non_presentation_state(self):
        drv = self.bind_driver(
            "D9", "case-38",
            "Selection does not change when obligations, witnesses, budget, child, workspace, or "
            "host details change while presentation inputs remain fixed")
        code = "claim.research_missing"
        expected = ordered_dedupe(SELECTOR_CONTEXT_SECTIONS["block"], REASONS[code]["sections"])
        a = self.select_sections(drv, "block", [code], None)
        self.assertEqual(a, expected,
                         "block + one known reason + null status must equal the canonical block "
                         "base plus reason sections, first occurrence deduped")
        # vary workspace and fake transport facts only — no domain-state inference
        drv.workspace_write("src/x.py", b"changed")
        drv.workspace_delete("src/x.py")
        drv.workspace_error("src/y.py", "unreadable")
        drv.workspace_write("src/z.py", b"v3")
        b = self.select_sections(drv, "block", [code], None)
        self.assertEqual(a, b, "varying non-presentation state must not change the selection")
        self.assertEqual(b, expected, "the invariant selection must remain exactly canonical")

    # 39 — Unknown reason/status fail closed to exact unknown_reason_sections; unknown action/section Fault
    def test_unknown_references_fail_closed(self):
        drv = self.bind_driver(
            "D9", "case-39",
            "Unknown reason/action/section and unknown/nonterminal status fail closed to exact "
            "unknown_reason_sections or exact Fault/closed (no hardcoded fallback)")
        # Unknown reason → exact ordered unknown_reason_sections (not a subset)
        out = self.select_sections(drv, "block", ["__unknown_reason__"], None)
        self.assertEqual(out, SELECTOR_UNKNOWN_REASON_SECTIONS,
                         "an unknown reason must return exactly unknown_reason_sections in order, "
                         "not a subset")
        # Nonterminal status (active) → exact ordered unknown_reason_sections
        out_nt = self.select_sections(drv, "block", [], "active")
        self.assertEqual(out_nt, SELECTOR_UNKNOWN_REASON_SECTIONS,
                         "a nonterminal non-null status must return exactly unknown_reason_sections")
        # Unknown status → exact ordered unknown_reason_sections
        out_unk = self.select_sections(drv, "block", [], "__unknown_status__")
        self.assertEqual(out_unk, SELECTOR_UNKNOWN_REASON_SECTIONS,
                         "an unknown non-null status must return exactly unknown_reason_sections")
        # unknown action kind via raw dispatch → exact Fault/closed (raw seam)
        unknown_action = self.raw_dispatch(drv, observe_action(
            run_id="probe-run", action={"kind": "not_a_real_action"}))
        self.assert_fault(unknown_action, fail_direction="closed")
        # unknown contract section via ordinary dispatch → exact Fault/closed (ordinary seam)
        unknown_section = self.dispatch(drv, get_contract(target="section", section_id="does/not/exist"))
        self.assert_fault(unknown_section, fail_direction="closed")

    # 40 — Multiple ordered known reasons preserve canonical order; reversed input → corresponding order
    def test_multiple_reasons_preserve_canonical_order(self):
        drv = self.bind_driver(
            "D9", "case-40",
            "Ambiguous/multiple reasons preserve deterministic canonical order and exclude "
            "irrelevant/duplicate sections")
        codes = ["claim.research_missing", "audit.same_model"]
        expected = ordered_dedupe(SELECTOR_CONTEXT_SECTIONS["block"],
                                   REASONS["claim.research_missing"]["sections"],
                                   REASONS["audit.same_model"]["sections"])
        out = self.select_sections(drv, "block", codes, None)
        self.assertEqual(out, expected,
                         "multiple ordered known reasons must select the ordered first-occurrence "
                         "union of block base then each registry reason section; no irrelevant/duplicate")
        # reversed reason input produces its corresponding canonical order
        rev_codes = list(reversed(codes))
        rev_expected = ordered_dedupe(SELECTOR_CONTEXT_SECTIONS["block"],
                                      REASONS["audit.same_model"]["sections"],
                                      REASONS["claim.research_missing"]["sections"])
        rev_out = self.select_sections(drv, "block", rev_codes, None)
        self.assertEqual(rev_out, rev_expected,
                         "reversed reason input must produce the corresponding canonical order")

    # 41 — GetContract index/full and representative sections incl presentation/selector: exact projection
    def test_get_contract_returns_exact_projection(self):
        drv = self.bind_driver(
            "D9", "case-41",
            "GetContract index/full and representative sections including presentation/selector "
            "return exact requested projection, canonical identity/digest, no sibling target payload")
        for target, extra in (("index", {}), ("full", {}),
                              ("section", {"section_id": "evidence/freshness"}),
                              ("section", {"section_id": "presentation/selector"})):
            with self.subTest(target=target, section=extra.get("section_id")):
                resp = self.dispatch(drv, get_contract(target=target,
                                                       section_id=extra.get("section_id")))
                cr = self.assert_contract_result(resp, target, section_id=extra.get("section_id"))
                self.assertEqual(cr.get("digest"), contract_digest(),
                                 "contract_result digest must equal the canonical registry digest")
        # unknown section is Fault/closed (case 39 covers the seam; assert here too)
        unknown = self.dispatch(drv, get_contract(target="section", section_id="does/not/exist"))
        self.assert_fault(unknown, fail_direction="closed")

    # 42 — StartRun exact Allow + no dump; Evaluate empty run exact sole graph.missing Block + no dump
    def test_ordinary_views_never_dump_full_contract(self):
        drv = self.bind_driver(
            "D9", "case-42",
            "Ordinary Start/Block/Allow/compaction never dumps the full PublicContract")
        # StartRun exact Allow and no dump
        start = self.dispatch(drv, start_run(goal=self.GOAL))
        self.assert_allow(start, converged=False)
        self.assert_no_full_contract_dump(start["result"]["run"])
        run_id = start["result"]["run"]["id"]
        # Evaluate on empty run → exact sole graph.missing Block and no dump (no conditional)
        ev = self.dispatch(drv, evaluate(run_id=run_id, intent="report_convergence"))
        self.assert_block_sole_reason(ev, "graph.missing")
        self.assert_no_full_contract_dump(ev["result"]["run"])
        # compacted view no full contract/private/persisted material via separated helpers
        compacted = drv.compact()
        self.assert_no_full_contract_dump(compacted)
        self.assert_no_private_capability(compacted)
        self.assert_no_persisted_operational_fields(compacted)


if __name__ == "__main__":
    unittest.main()
