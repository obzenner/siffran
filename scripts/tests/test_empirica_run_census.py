#!/usr/bin/env python3
"""Regression tests for the read-only Empirica run census (scripts/empirica_run_census.py)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import empirica_run_census as census  # noqa: E402

CLAUDE_ID = "a" + "0123456789abcdef"


def _doc(status: str, *, role: str | None = None, native: str | None = None,
         state: str = "completed", governance: dict | None = None, goal: str = "g") -> dict:
    doc: dict = {"protocol": "empirica/v2", "status": status, "goal": goal, "children": []}
    if role:
        doc["children"].append({"purpose": "audit", "audit_role_profile": role,
                                "native_id": native, "state": state,
                                "audit_argument": {"large": "x" * 100}})
    if governance is not None:
        doc["governance"] = governance
    return doc


class CensusTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def _put(self, run: str, doc: object, *, gen: str = "gen-1", raw: str | None = None) -> Path:
        path = self.home / "projects" / "p1" / "runs" / run / gen / "run.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw if raw is not None else json.dumps(doc), encoding="utf-8")
        (path.parent / "run.json.lock").write_text("", encoding="utf-8")
        return path

    def _snapshot(self) -> dict[str, tuple[float, int]]:
        return {str(p): (p.stat().st_mtime, p.stat().st_size)
                for p in self.home.rglob("*")}

    def test_enumerates_only_the_documented_fixed_depth_layout(self) -> None:
        self._put("r1", _doc("converged", role=census.PI_ROLE, native="call_x"))
        deeper = self.home / "projects" / "p1" / "runs" / "r2" / "gen-1" / "nested" / "run.json"
        deeper.parent.mkdir(parents=True)
        deeper.write_text(json.dumps(_doc("converged")), encoding="utf-8")
        stray = self.home / "elsewhere" / "run.json"
        stray.parent.mkdir(parents=True)
        stray.write_text(json.dumps(_doc("converged")), encoding="utf-8")
        found = census.run_documents(self.home)
        self.assertEqual([p.parts[-3] for p in found], ["r1"])

    def test_missing_home_is_empty_not_an_error(self) -> None:
        self.assertEqual(census.run_documents(self.home / "absent"), [])

    def test_host_inference_rules_are_explicit(self) -> None:
        self.assertEqual(census.infer_host(census.PI_ROLE, "chatcmpl-tool-1"), "pi")
        self.assertEqual(census.infer_host(census.COLON_ROLE, CLAUDE_ID), "claude")
        self.assertEqual(census.infer_host(census.COLON_ROLE, "call_abc"), "colon-role")
        self.assertEqual(census.infer_host(census.COLON_ROLE, None), "colon-role")
        self.assertEqual(census.infer_host("other", CLAUDE_ID), "unknown-role")

    def test_corrupt_documents_are_counted_not_skipped(self) -> None:
        self._put("bad", None, raw="{not json")
        self._put("list", [1, 2])
        self._put("ok", _doc("active"))
        records, corrupt = census.collect([self.home], [])
        self.assertEqual([r.run_id for r in records], ["ok"])
        self.assertEqual(len(corrupt), 2)

    def test_summary_separates_host_status_and_governance(self) -> None:
        self._put("p", _doc("converged", role=census.PI_ROLE, native="call_1"))
        self._put("c", _doc("stopped_residual", role=census.COLON_ROLE, native=CLAUDE_ID,
                            governance={"state": "approved", "control_mode": "auto"}))
        self._put("n", _doc("active"))
        records, corrupt = census.collect([self.home], [])
        data = census.summary(records, corrupt)
        rows = {(r["host"], r["status"], r["governance"]): r["count"] for r in data["runs"]}
        self.assertEqual(rows, {("pi", "converged", "pre-governance"): 1,
                                ("claude", "stopped_residual", "governed"): 1,
                                ("no-audit", "active", "pre-governance"): 1})
        self.assertEqual({(a["host"], a["state"]) for a in data["audit_children"]},
                         {("pi", "completed"), ("claude", "completed")})

    def test_filters_and_explicit_files(self) -> None:
        self._put("p", _doc("converged", role=census.PI_ROLE, native="call_1"))
        self._put("c", _doc("stopped_residual", role=census.COLON_ROLE, native=CLAUDE_ID))
        extra = self.home / "snapshot" / "run.json"
        extra.parent.mkdir()
        extra.write_text(json.dumps(_doc("converged", role=census.COLON_ROLE, native=CLAUDE_ID)),
                         encoding="utf-8")
        records, _ = census.collect([self.home], [extra])
        self.assertEqual(len(records), 3)
        converged = census.select(records, status="converged", host=None, run=None)
        self.assertEqual(len(converged), 2)
        claude = census.select(records, status="converged", host="claude", run=None)
        self.assertEqual([r.path for r in claude], [str(extra)])

    def test_duplicate_paths_are_read_once(self) -> None:
        path = self._put("p", _doc("active"))
        records, _ = census.collect([self.home], [path])
        self.assertEqual(len(records), 1)

    def test_run_detail_omits_audit_argument_and_requires_unique_prefix(self) -> None:
        self._put("abc1", _doc("converged", role=census.PI_ROLE, native="call_1"))
        self._put("abc2", _doc("active"))
        out = io.StringIO()
        with redirect_stdout(out):
            code = census.main(["--home", str(self.home), "--run", "abc1"])
        self.assertEqual(code, 0)
        self.assertNotIn("audit_argument", out.getvalue())
        self.assertIn('"native_id": "call_1"', out.getvalue())
        err = io.StringIO()
        with redirect_stdout(io.StringIO()):
            sys_stderr, sys.stderr = sys.stderr, err
            try:
                self.assertEqual(census.main(["--home", str(self.home), "--run", "abc"]), 2)
            finally:
                sys.stderr = sys_stderr
        self.assertIn("matched 2 runs", err.getvalue())

    def test_every_output_mode_is_read_only(self) -> None:
        self._put("p", _doc("converged", role=census.PI_ROLE, native="call_1"))
        self._put("bad", None, raw="{")
        before = self._snapshot()
        for argv in ([], ["--runs"], ["--json"], ["--runs", "--json"], ["--run", "p"],
                     ["--status", "converged", "--host", "pi"]):
            with redirect_stdout(io.StringIO()):
                census.main(["--home", str(self.home), *argv])
        self.assertEqual(self._snapshot(), before)

    def test_qualification_root_uses_fixed_candidate_cell_state_layout(self) -> None:
        root = self.home / "qual"
        good = root / "cand" / "pi" / "state"
        (good / "projects").mkdir(parents=True)
        (root / "cand" / "claude" / "state").mkdir(parents=True)  # no run store
        (root / "cand" / "deep" / "x" / "state" / "projects").mkdir(parents=True)
        self.assertEqual(census.qualification_homes(root), [good])

    def test_default_home_honours_empirica_home(self) -> None:
        self.assertEqual(census.default_home({"EMPIRICA_HOME": str(self.home)}), self.home)
        self.assertEqual(census.default_home({}), Path.home() / ".empirica-plugin")
        self.assertEqual(census.default_home({"EMPIRICA_HOME": ""}),
                         Path(os.path.expanduser("~")) / ".empirica-plugin")


if __name__ == "__main__":
    unittest.main()
