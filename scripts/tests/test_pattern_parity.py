#!/usr/bin/env python3
"""Unit controls for scripts/check_pattern_parity.py — the scope, the corpus, and the pins.

The parity target itself runs both engines for real (``make contract-pattern-parity``, from
contract-check); these controls pin the logic without Node: the corpus carries the standing
cross-engine families (blank, mixed, astral, lone-surrogate — the review's demand), every registered
pattern and grammar piece is in scope, a Node verdict that disagrees with Python outside the declared
open family is a finding, and the excusing would span a declared open anchor's trailing-terminator
family only — the registry is empty at this head (the run/5 digest anchors healed when 07c5f66b
re-anchored them, and the pins fired as designed). A machine without Node reports a skip
and never a fake verdict (CI runners carry no Node; a developer machine always does).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "scripts", ROOT / "plugins" / "empirica"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import check_pattern_parity as parity  # noqa: E402
from contract_strategies import Contract, module_documents  # noqa: E402

_real_node_verdicts = parity.node_verdicts


class CorpusTests(unittest.TestCase):
    def test_the_standing_families_reach_everything_a_host_language_decides_differently(self):
        for required in ("", " ", "\t", "\n", "\r", "\x0b", "\x0c", "\u00a0", "\u1680", "\u2000",
                         "\u200a", "\u2028", "\u2029", "\u202f", "\u205f", "\u3000", "\ufeff",
                         "\U0001F600", "\U0001F600abc", "\U0001D11E", "\ud800", "\udfff", "a\ud800",
                         "\u00e9", "\u06f1"):
            self.assertIn(required, parity.FAMILIES)

    def test_the_corpus_carries_the_families_and_the_witness_seeded_probes(self):
        corpus = parity.corpus_for(r"^[a-z]+$")
        for family in ("\u2028", "\ud800", "\U0001F600"):
            self.assertIn(family, corpus)
        self.assertTrue(any(case.startswith("a") and case.endswith("\r") for case in corpus),
                        "a witness-seeded line-terminator suffix is present")

    def test_the_corpus_seeds_the_arabic_indic_digit_at_witness_ends(self):
        """Sol's seeded cross-engine split (E6c review, blocking 2): a pattern whose final class
        complements `\\d` — `^sha256:[0-9a-f]{63}[^\\d](?![\\s\\S])` — admits the
        Arabic-Indic digit `U+0661` in JavaScript (`\\d` is ASCII-only) and refuses it in Python
        (`\\d` is Unicode-wide). The corpus must end a pattern's own witness with that digit, and
        a pattern wearing the class must show the split as a finding through the two engines."""
        seed = "^sha256:[0-9a-f]{63}[^\\d](?![\\s\\S])"
        corpus = parity.corpus_for(seed)
        ending = "sha256:" + "a" * 63 + "\u0661"
        self.assertTrue(any(case == ending for case in corpus),
                        "the witness+digit ending is in the seeded pattern's corpus")
        records = [{"pattern": seed, "cases": corpus}]
        from_python = parity.python_verdicts(records)
        from_node = parity.node_verdicts(records)
        if from_node is None:  # a machine without Node may neither fake nor require the split
            self.skipTest("no Node on PATH")
        self.assertTrue(any(from_python[(seed, ending)] != from_node[(seed, ending)]
                            for ending_probe in {(seed, ending)} if ending_probe in from_node),
                        "the two engines must disagree on the Arabic-Indic digit ending")

    def test_every_registered_pattern_and_piece_is_in_scope(self):
        contract = Contract(documents=module_documents())
        patterns, open_patterns = parity.patterns_of(contract)
        self.assertIn("^sha256:[0-9a-f]{64}(?![\\s\\S])", patterns,
                      "the re-anchored run/5 digest pattern is a registered pattern")
        self.assertEqual(open_patterns, {}, "no pattern is excused at this head: the run/5 digest "
                         "anchors healed (07c5f66b re-anchored both absolutely) and the registry is empty")

    def _findings(self, node_flip=None):
        def seeing(records):
            answers = parity.python_verdicts(records)
            if node_flip is not None:
                answers = {key: node_flip(key, value) for key, value in answers.items()}
            return answers

        parity.node_verdicts = seeing
        try:
            return parity.check()
        finally:
            parity.node_verdicts = _real_node_verdicts

    def test_a_node_keeping_a_verdict_to_itself_is_a_finding(self):
        def flip(pattern_and_case, value):
            pattern, probe = pattern_and_case
            if value and probe == "\u06f1":  # an ordinary Arabic-digit-only case, outside any declared family
                return False
            return value

        findings = self._findings(node_flip=flip)
        self.assertTrue(any("Python accepts and Node/u refuses" in word for word in findings),
                        findings)

    def test_the_excusing_would_span_only_a_declared_anchors_trailing_terminators(self):
        """The narrowness logic, kept as a control against a future re-entry: a disagreement on a
        digest-family pattern whose case is not a trailing line terminator stays a finding — the
        excusing follows a declared node and its declared kind, never a whole pattern blanketly."""
        def flip(pattern_and_case, value):
            pattern, probe = pattern_and_case
            if "sha256" in pattern and probe and not probe.endswith(parity.LINE_TERMINATORS):
                return not value
            return value

        findings = self._findings(node_flip=flip)
        self.assertTrue(any("Python" in word and "Node/u" in word for word in findings), findings)

    def test_an_agreeing_pair_of_engines_has_no_finding_and_nothing_to_pin(self):
        """The empty-registry shape, as it stands at this head: both engines agree everywhere, the
        live pin stays silent (nothing to demand removal), and no mismatch hides behind an entry.
        A future registry entry comes back only by re-declaring its node and kind in the engine
        registrar, at which point the live pins cover it again."""
        findings = self._findings(node_flip=lambda key, value: value)
        self.assertEqual(findings, [], findings)

    def test_check_runs_one_corpus_through_both_engines(self):
        patterns, _ = parity.patterns_of(Contract(documents=module_documents()))
        served = []

        def recording(records):
            served.append(list(records))
            return dict(parity.python_verdicts(records))

        parity.node_verdicts = recording
        try:
            parity.check()
        finally:
            parity.node_verdicts = _real_node_verdicts
        self.assertEqual([record["pattern"] for record in served[0]], patterns)

    def test_a_machine_without_node_reports_a_skip_never_a_fake_verdict(self):
        real_which, real_verdicts = parity.shutil.which, parity.node_verdicts

        def bare(name, path=None):
            return None if name == "node" else real_which(name)

        parity.shutil.which = bare
        try:
            self.assertIsNone(parity.node_verdicts([{"pattern": "^a$", "cases": ["a"]}]))
            self.assertEqual(parity.main_from_findings([]), 0, "the skip path never fabricates")
        finally:
            parity.shutil.which, parity.node_verdicts = real_which, real_verdicts


if __name__ == "__main__":
    unittest.main()
