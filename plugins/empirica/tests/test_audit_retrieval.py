"""Claude's observed auditor retrieval is read from the plugin-owned definition (ADR-0067, Slice 2)."""
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters import invocation
from application.protocol import host_profile_id  # noqa: E402
CLAUDE_PROFILE = host_profile_id("claude-code")


def definition(tools: str) -> Path:
    path = Path(tempfile.mkdtemp()) / "auditor.md"
    path.write_text(f"---\nname: a\ntools: {tools}\n---\nbody\n", encoding="utf-8")
    return path


class ClaudeAuditRetrievalTests(unittest.TestCase):
    def test_the_shipped_definition_covers_workspace_and_local_but_never_the_web(self):
        shipped = invocation._declared_tools(invocation._CLAUDE_AUDITOR)
        self.assertIn("WebFetch", shipped)  # declared, never observed: nothing shows it is permitted
        self.assertEqual(invocation.audit_retrieval(CLAUDE_PROFILE), ["local", "workspace"])

    def test_the_set_follows_the_definitions_tools(self):
        for tools, expected in (("Read, Glob, Grep", ["local", "workspace"]), ("Glob, Grep", []),
                                ("WebFetch", []), ("Read", ["local", "workspace"]), ("Bash, Read", ["local", "workspace"])):
            with self.subTest(tools=tools), patch.object(invocation, "_CLAUDE_AUDITOR", definition(tools)):
                self.assertEqual(invocation.audit_retrieval(CLAUDE_PROFILE), expected)

    def test_a_definition_without_tools_is_an_error_not_a_default(self):
        bare = Path(tempfile.mkdtemp()) / "a.md"
        bare.write_text("---\nname: a\n---\n", encoding="utf-8")
        with patch.object(invocation, "_CLAUDE_AUDITOR", bare), self.assertRaises(ValueError):
            invocation.audit_retrieval(CLAUDE_PROFILE)

    def test_codex_has_no_auditor_and_pi_observes_its_own_set(self):
        self.assertEqual(invocation.audit_retrieval(host_profile_id("codex-cli")), [])
        with self.assertRaises(KeyError):
            invocation.audit_retrieval(host_profile_id("pi"))

    def test_provenance_carries_the_observed_set(self):
        got = invocation.provenance("claude", True, "s", {}, profile_id=CLAUDE_PROFILE)
        self.assertEqual(got["audit_retrieval"], ["local", "workspace"])


class AuditorDefinitionTests(unittest.TestCase):
    """The duties the dossier's ``verification.retrieve`` flag creates are stated where the auditor reads them."""

    agents = Path(__file__).resolve().parents[1] / "agents"

    def test_both_definitions_bind_retrieval_to_the_flag(self):
        for name in ("empirica-auditor.md", "pi/empirica-auditor.md"):
            text = (self.agents / name).read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertIn("`verification.retrieve`" if name.startswith("pi") else "`retrieve` is `true`", text)
                self.assertIn("exists and contains the cited text" if name.startswith("pi")
                              else "it contains the cited text", text)

    # The service's quoting rule (``core.citations``), as the auditor's text states it
    QUOTE_RULE = ("Judge \"contains the cited text\" by the service's rule: each whitespace run, line breaks included, compares as one space "
                  "(a space is still required where the source has whitespace), and a citation may join separate excerpts, in their source "
                  "order, with `\u2026` or `...`. A quote that matches under that rule contains the cited text; "
                  "do not fail a record for a difference in whitespace or line breaks alone.")

    def test_the_shared_rubric_applies_the_services_quoting_rule(self):
        text = " ".join((self.agents / "empirica-auditor.md").read_text(encoding="utf-8").split())
        self.assertEqual(text.count(self.QUOTE_RULE), 1)
        self.assertLess(text.index(self.QUOTE_RULE), text.index("When `retrieve` is `false`"), "the rule sits with the retrieve: true duty")
        # the Pi wrapper carries no copy: the shared rubric is the one place
        self.assertNotIn("whitespace", (self.agents / "pi/empirica-auditor.md").read_text(encoding="utf-8"))

    def test_the_service_applies_the_rule_the_auditor_text_states(self):
        """The sentence above is the service's behaviour: ``core.citations`` compares whitespace runs (line breaks included) as
        one space and splits a citation at ``\u2026`` or ``...`` into excerpts that must appear in order."""
        from core.citations import citation_problem
        from core.freshness import ObservationState
        present = ObservationState.PRESENT

        def verdict(citation, content):
            return citation_problem("data.txt", citation, content.encode("utf-8"), present)

        self.assertIsNone(verdict("alpha beta gamma", "alpha\nbeta\ngamma\n"))  # the native case: names on separate lines
        self.assertIsNone(verdict("alpha   beta\t\tgamma", "alpha\r\n\r\nbeta  gamma\n"))  # runs of any whitespace are one space
        self.assertIsNone(verdict("alpha \u2026 gamma", "alpha\nbeta\ngamma\n"))  # excerpts joined with an ellipsis
        self.assertIsNone(verdict("alpha ... gamma", "alpha\nbeta\ngamma\n"))
        # every ellipsis spelling the auditor's sentence names is one the service verifies (the sentence cannot name a spelling
        # the service stops accepting, nor leave out one it accepts)
        sentence = " ".join((self.agents / "empirica-auditor.md").read_text(encoding="utf-8").split())
        named = re.findall(r"`([^`]+)`", sentence[sentence.index("may join separate excerpts"):][:200])
        self.assertEqual(named, ["\u2026", "..."])
        for spelling in named:
            self.assertIsNone(verdict(f"alpha {spelling} gamma", "alpha\nbeta\ngamma\n"), spelling)
        from core.citations import _ELLIPSIS
        self.assertEqual(sorted(_ELLIPSIS.pattern.replace("\\s*", "").strip("()?:").split("|")), sorted(["\u2026", "\\.\\.\\."]))
        # whitespace is the only latitude: words, their order, and a joined excerpt out of order still fail
        self.assertEqual(verdict("alphabeta gamma", "alpha\nbeta\ngamma\n"), "text_not_found")  # no space where there was one
        self.assertEqual(verdict("gamma beta alpha", "alpha\nbeta\ngamma\n"), "text_not_found")
        self.assertEqual(verdict("gamma \u2026 alpha", "alpha\nbeta\ngamma\n"), "text_not_found")

    def test_the_shared_rubric_covers_confirmed_records_missing_sources_and_human_decisions(self):
        text = (self.agents / "empirica-auditor.md").read_text(encoding="utf-8")
        for required in ("`verified_by_service` or `confirmed_by_human`", "fail the claim that\n  rests on it",
                         "`human_decision`", "do not second-guess"):
            self.assertIn(required, text)

    def test_the_shared_rubric_judges_author_decided_decisions_and_partial_coverage(self):
        text = " ".join((self.agents / "empirica-auditor.md").read_text(encoding="utf-8").split())
        self.assertRegex(text, r"An approved `needs-decision` claim without `human_decision` was decided by the author\.")
        for required in ("naming the missing comparison", "say which alternative, or the status quo, needs a comparison",
                         "Pass or fail the audit on the claims in `reviewed_claims`",
                         "item 1 (a Fold-1 source) to those claims except those with `human_decision`",
                         "items 2\u20135 to those claims only", "items 6\u20139 to the whole graph",
                         "judged only through items 6\u20139"):
            self.assertIn(required, text)

    def test_I10_the_shared_rubric_ignores_a_record_nobody_confirmed(self):
        text = " ".join((self.agents / "empirica-auditor.md").read_text(encoding="utf-8").split())
        for required in ("`status` is `pending` and `retrieve` is `false`", "supports no claim",
                         "fail the claim, naming it, when no other record supports it"):
            self.assertIn(required, text)

    def test_the_pi_wrapper_carries_no_rubric_copy(self):
        text = (self.agents / "pi/empirica-auditor.md").read_text(encoding="utf-8")
        for absent in ("decided by the author", "outside the verdict", "`human_decision`", "## Rubric",
                       "Every approved gating claim"):
            self.assertNotIn(absent, text)


if __name__ == "__main__":
    unittest.main()
