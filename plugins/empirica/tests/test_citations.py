"""A research record's citation must be verbatim from the repository file it cites (core.citations).

The pure rule first, then the coordinator: a refused record says why and records nothing, so the
author learns at once that a paraphrase is not a citation; a verbatim one is recorded as before.
"""
from __future__ import annotations

import unittest

from test_d7_transactions import Artifacts, Harness, Runs, Workspace, activate_investigation, traverse_history

from application.transaction import Coordinator as ProductionCoordinator
from core.citations import submission_problem, PROBLEMS, citation_problem, cited_file
from core.freshness import ObservationState
from governance_setup import TEST_INVOCATION, approve_current

PROFILE = "pi@0.84.1+pi-subagents-foreground-audit-v1"
SOURCE = b"""def load(path):
    # Read the index once; later calls hit the cache.
    with open(path) as handle:
        return parse(handle.read())
"""
PRESENT = ObservationState.PRESENT


def problem(ref: str, citation: str, content: bytes | None = SOURCE, state=PRESENT):
    return citation_problem(ref, citation, content, state)


class CitationRuleTests(unittest.TestCase):
    def test_only_a_workspace_path_is_checked_verbatim(self):
        self.assertEqual(cited_file("code", "src/load.py:2-3"), "src/load.py")
        self.assertEqual(cited_file("docs", "README.md"), "README.md")
        # The class follows the ref's form; the kind does not change it.
        self.assertEqual(cited_file("web", "src/load.py"), "src/load.py")
        for kind, ref in (("docs", "https://example.test/guide"), ("code", "/opt/lib/x.py"),
                          ("docs", "~/notes.md"), ("runtime", "the build log")):
            with self.subTest(kind=kind, ref=ref):
                self.assertIsNone(cited_file(kind, ref))

    def test_a_ref_that_is_not_one_file_is_refused_at_submission(self):
        many = "src/a.py:1-3,9; src/b.py:4"
        self.assertIsNone(cited_file("code", many))
        for ref in (many, "../outside.py", "the build log"):
            with self.subTest(ref=ref):
                self.assertEqual(submission_problem("code", {"source_ref": ref, "citation": "x"},
                                                    SOURCE, PRESENT), "source_ref_form")

    def test_verbatim_text_is_accepted_whatever_its_whitespace(self):
        self.assertIsNone(problem("src/load.py", "def load(path):"))
        self.assertIsNone(problem("src/load.py:2-3", "Read the index once;   later calls\nhit the cache."))
        self.assertIsNone(problem("src/load.py:1,4", "def load(path): … return parse(handle.read())"))

    def test_excerpts_joined_by_an_ellipsis_must_each_appear_in_order(self):
        self.assertIsNone(problem("src/load.py", "def load(path): ... with open(path) as handle:"))
        self.assertEqual(problem("src/load.py", "with open(path) as handle: … def load(path):"), "text_not_found")
        self.assertEqual(problem("src/load.py", " … "), "text_not_found")

    def test_a_paraphrase_or_text_outside_the_cited_lines_is_refused(self):
        self.assertEqual(problem("src/load.py", "load() caches the index after the first read"), "text_not_found")
        self.assertEqual(problem("src/load.py:1", "Read the index once"), "text_not_found")

    def test_lines_must_exist_and_the_file_must_be_readable(self):
        self.assertEqual(problem("src/load.py:3-9", "with open"), "lines_out_of_range")
        self.assertEqual(problem("src/load.py:3-2", "with open"), "lines_out_of_range")
        self.assertEqual(problem("src/load.py:0", "with open"), "lines_out_of_range")
        for state in (ObservationState.MISSING, ObservationState.UNREADABLE, ObservationState.NON_REGULAR):
            with self.subTest(state=state):
                self.assertEqual(problem("src/load.py", "def load", None, state), "file_unavailable")

    def test_every_problem_is_the_contracts_vocabulary(self):
        import json
        from pathlib import Path
        contract = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "empirica" / "v2"
                               / "public-contract.json").read_text())
        params = contract["reasons"]["research.citation_unverified"]["params"]
        self.assertEqual(tuple(params["properties"]["problem"]["enum"]), PROBLEMS)


class CitationAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.runs, self.artifacts, self.workspace = Runs(), Artifacts(), Workspace()
        self.workspace.write("src/load.py", SOURCE)
        self.coordinator = ProductionCoordinator(
            self.workspace, Harness(), self.runs, self.artifacts, PROFILE, {})
        started = self.coordinator.handle({
            "type": "StartRun", "control_mode": "deliberative", "invocation": dict(TEST_INVOCATION),
            "selector": {"project": "citations", "session": "s"}, "goal": "cache the index"}, "start")
        self.run_id = started["result"]["run"]["id"]
        self.handle({"kind": "graph", "payload": {"root": "C0", "claims": [
            {"id": "C0", "text": "The index is read once.", "gating": True, "kind": "ordinary"}], "edges": []}})
        approve_current(self.coordinator, self.run_id)
        activate_investigation(self.coordinator, self.run_id)

    def handle(self, action):
        return self.coordinator.handle({"type": "ObserveAction", "run_id": self.run_id,
                                        "action": action}, "action")["result"]

    def research(self, ref, citation, kind="code"):
        return self.handle({"kind": "research", "claim_id": "C0", "source_kind": kind, "result": "supports",
                            "payload": {"source_ref": ref, "citation": citation}})

    def recorded(self):
        from test_d7_transactions import decode_state
        key = next(iter(self.runs.data))
        state = decode_state(self.runs.data[key].value)
        history = traverse_history(state, self.artifacts.values.get(key, {}).values())
        return [a for a in history if a.get("kind") == "research"]

    def claim_status(self, result):
        """The claim's obligation status in the run view: ``satisfied`` once its evidence approves it."""
        return next(o for o in result["run"]["obligations"]["active"] if o["id"] == "claim:C0")["status"]

    def test_a_paraphrase_is_refused_with_its_reason_and_records_nothing(self):
        result = self.research("src/load.py:1-4", "load() reads the index a single time and caches it")
        self.assertEqual(result["type"], "Block")
        self.assertEqual([(r["code"], r["parameters"]) for r in result["reasons"]],
                         [("research.citation_unverified", {"problem": "text_not_found"})])
        self.assertEqual(result["reasons"][0]["next_actions"], ["research.record"])
        self.assertEqual(self.recorded(), [])
        self.assertNotEqual(self.claim_status(result), "satisfied")

    def test_a_missing_file_is_refused(self):
        result = self.research("src/absent.py", "def load(path):")
        self.assertEqual(result["reasons"][0]["parameters"], {"problem": "file_unavailable"})
        self.assertEqual(self.recorded(), [])

    def test_a_verbatim_citation_is_recorded_and_supports_the_claim(self):
        result = self.research("src/load.py:2", "# Read the index once; later calls hit the cache.")
        self.assertEqual(result["type"], "Allow")
        self.assertEqual([a["citation"] for a in self.recorded()],
                         ["# Read the index once; later calls hit the cache."])
        self.assertEqual(self.claim_status(result), "satisfied")

    def test_a_web_source_is_left_to_the_audit(self):
        result = self.research("https://example.test/caching", "a paraphrase the audit must judge", kind="web")
        self.assertEqual(result["type"], "Allow")
        self.assertEqual(len(self.recorded()), 1)


if __name__ == "__main__":
    unittest.main()
