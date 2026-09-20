"""D4 cases 43–44: compaction and reload.

Owner: D9 (compaction subset / restore projection). Compaction preserves the run's public facts;
reload re-derives the same public view without a persisted obligation-contract revision/pointer.
Case 43 uses a real public setup (C0 ordinary approved, freeze, C1 deferred, foreground audit child
driven to pending) and captures the complete GetRun before compaction. Case 44 dispatches
GetRun/RestoreRun through central validation on the reloaded driver and compares the complete
bounded public view before/after; banned persisted/private fields are asserted recursively, not
only top-level. No copied banned-policy constant — the persisted-operational key set is
owned solely by ``assert_no_persisted_operational_fields`` in ``assertions``.
"""
from __future__ import annotations

import copy
import unittest

from assertions import (  # noqa: E402
    ConformanceCase, UNTRUSTED_CLOSE, UNTRUSTED_OPEN,
    contract_digest, contract_id, contract_version, get_run, restore_run,
)


class CompactionTests(ConformanceCase):
    GOAL = "Prove compaction and reload preserve the public view."

    # 43 — Compaction preserves exact public facts from the captured RunView; no private/persisted dump
    def test_compaction_preserves_public_facts(self):
        drv = self.bind_driver(
            "D9", "case-43",
            "Compaction preserves exact goal, status, modes, contract identity/digest/relevant "
            "sections, active/deferred obligation rows, residuals, freshness, pending child "
            "summaries, host, and ordered next_actions; exact canonical untrusted delimiters; "
            "one freeze.deferred residual binding claim_ids [C1] and exact dossier "
            "deferred_scope_digest; no full contract, native/capability, operational counters, or "
            "persisted revision/history/phase/hashes")
        run_id = self.start_run(drv, goal=self.GOAL, modes={"multi_provider": False})
        # Real public setup: C0 ordinary approved, freeze, C1 deferred, foreground audit child → pending
        scope = self.require_audit_scope(drv, run_id, deferred_kind="ordinary")
        c1_id = scope["c1_id"]
        dossier = scope["dossier"]
        child_id = self.require_pending_audit_child(drv, run_id)
        # Capture complete GetRun BEFORE compaction
        before = self.dispatch(drv, get_run(run_id=run_id))
        run_before = before["result"]["run"]
        compacted = drv.compact()
        # Compaction preserves exact facts from the captured RunView
        self.assertEqual(compacted.get("goal"), run_before["goal"],
                         "compaction must preserve the exact goal")
        self.assertEqual(compacted.get("status"), run_before["status"],
                         "compaction must preserve the exact status")
        self.assertEqual(compacted.get("modes"), run_before["modes"],
                         "compaction must preserve the exact modes")
        c_comp = compacted.get("contract", {})
        self.assertEqual(c_comp.get("id"), run_before["contract"]["id"])
        self.assertEqual(c_comp.get("version"), run_before["contract"]["version"])
        self.assertEqual(c_comp.get("digest"), run_before["contract"]["digest"])
        self.assertEqual(c_comp.get("id"), contract_id(),
                         "compaction contract id must equal the canonical registry id")
        self.assertEqual(c_comp.get("version"), contract_version(),
                         "compaction contract version must equal the canonical registry version")
        self.assertEqual(c_comp.get("digest"), contract_digest(),
                         "compaction contract digest must equal the canonical registry digest")
        self.assertEqual(c_comp.get("relevant_sections"), run_before["contract"]["relevant_sections"],
                         "compaction must preserve the exact relevant sections")
        self.assertEqual(compacted.get("obligations"), run_before["obligations"],
                         "compaction must preserve exact active/deferred obligation rows")
        self.assertEqual(compacted.get("residuals"), run_before["residuals"],
                         "compaction must preserve exact residuals")
        self.assertEqual(compacted.get("freshness"), run_before["freshness"],
                         "compaction must preserve exact freshness")
        self.assertEqual(compacted.get("children"), run_before["children"],
                         "compaction must preserve exact pending child summaries")
        self.assertEqual(compacted.get("host"), run_before["host"],
                         "compaction must preserve the exact host projection")
        self.assertEqual(compacted.get("next_actions"), run_before["next_actions"],
                         "compaction must preserve ordered next_actions")
        # pending child preserved with exact pending state
        self.assert_child_summary(compacted, child_id, state="pending")
        # exact one freeze.deferred residual binding claim_ids [C1] and exact dossier digest
        deferred = [r for r in compacted.get("residuals", []) if r["code"] == "freeze.deferred"]
        self.assertEqual(len(deferred), 1,
                         "compaction must preserve exactly one freeze.deferred residual")
        params = deferred[0].get("parameters", {})
        self.assertEqual(list(params.get("claim_ids", [])), [c1_id],
                         "freeze.deferred claim_ids must be [C1] in order")
        self.assertEqual(params.get("deferred_scope_digest"), dossier["deferred_scope_digest"],
                         "freeze.deferred deferred_scope_digest must equal the dossier")
        # exact canonical untrusted delimiters (required, not conditional)
        self.assertIn("untrusted_delimiters", compacted,
                      "compaction surface must carry canonical untrusted delimiters")
        self.assertEqual(compacted["untrusted_delimiters"],
                         {"open": UNTRUSTED_OPEN, "close": UNTRUSTED_CLOSE},
                         "compaction untrusted delimiters must equal the canonical registry values")
        # no full contract, native/capability, operational counters, persisted revision/history/phase
        self.assert_no_full_contract_dump(compacted)
        self.assert_no_private_capability(compacted)
        self.assert_no_persisted_operational_fields(compacted)

    # 44 — Reload derives the same public view; banned persisted/private fields absent recursively
    def test_reload_derives_same_public_view(self):
        drv = self.bind_driver(
            "D9", "case-44",
            "After compact + reload with the same real setup (C0 ordinary approved, freeze, C1 "
            "deferred, foreground audit child → pending), GetRun and RestoreRun on the reloaded "
            "driver derive the same complete bounded public view including pending child and exact "
            "C1 deferred residual/digest; banned persisted/private fields absent recursively")
        run_id = self.start_run(drv, goal=self.GOAL)
        # Same real setup as case 43: audit scope with deferred C1, pending audit child
        scope = self.require_audit_scope(drv, run_id, deferred_kind="ordinary")
        c1_id = scope["c1_id"]
        dossier = scope["dossier"]
        child_id = self.require_pending_audit_child(drv, run_id)
        # capture complete GetRun BEFORE reload (central validation, no direct driver.request)
        before = self.dispatch(drv, get_run(run_id=run_id))
        run_before = before["result"]["run"]
        drv.compact()
        reloaded = drv.reload()
        # use the reloaded driver for GetRun and RestoreRun
        after_get = self.dispatch(reloaded, get_run(run_id=run_id))
        after_restore = self.dispatch(reloaded, restore_run(run_id=run_id))
        # both complete bounded RunViews equal the pre-compaction RunView exactly
        self.assertEqual(after_get["result"]["run"], run_before,
                         "reloaded GetRun must equal the pre-compaction RunView exactly")
        restored_run = after_restore["result"]["run"]
        self.assertEqual(restored_run["contract"]["relevant_sections"],
                         reloaded.select_sections("restore", [], None),
                         "RestoreRun must use the canonical restore presentation context")
        normalized_restore = copy.deepcopy(restored_run)
        normalized_restore["contract"]["relevant_sections"] = \
            run_before["contract"]["relevant_sections"]
        self.assertEqual(normalized_restore, run_before,
                         "RestoreRun may differ only by its operation-local relevant sections")
        # run ID/pending child/deferred facts preserved
        self.assertEqual(after_get["result"]["run"]["id"], run_id,
                         "reload must re-derive the same run identity")
        # before/after exact includes pending child and exact C1 deferred residual/digest
        for run in (run_before, after_get["result"]["run"], after_restore["result"]["run"]):
            self.assert_child_summary(run, child_id, state="pending")
            deferred = [r for r in run.get("residuals", []) if r["code"] == "freeze.deferred"]
            self.assertEqual(len(deferred), 1,
                             "must preserve exactly one freeze.deferred residual")
            params = deferred[0].get("parameters", {})
            self.assertEqual(list(params.get("claim_ids", [])), [c1_id],
                             "freeze.deferred claim_ids must be [C1] in order")
            self.assertEqual(params.get("deferred_scope_digest"), dossier["deferred_scope_digest"],
                             "freeze.deferred deferred_scope_digest must equal the dossier")
        # banned full-contract/private/persisted fields asserted recursively, not only top-level
        for env in (after_get, after_restore):
            self.assert_no_full_contract_dump(env["result"])
            self.assert_no_persisted_operational_fields(env["result"])
            self.assert_no_private_capability(env["result"])


if __name__ == "__main__":
    unittest.main()
