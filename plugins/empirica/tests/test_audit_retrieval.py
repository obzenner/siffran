"""Claude's observed auditor retrieval is read from the plugin-owned definition (ADR-0067, Slice 2)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters import invocation


def definition(tools: str) -> Path:
    path = Path(tempfile.mkdtemp()) / "auditor.md"
    path.write_text(f"---\nname: a\ntools: {tools}\n---\nbody\n", encoding="utf-8")
    return path


class ClaudeAuditRetrievalTests(unittest.TestCase):
    def test_the_shipped_definition_covers_workspace_and_local_but_never_the_web(self):
        shipped = invocation._declared_tools(invocation._CLAUDE_AUDITOR)
        self.assertIn("WebFetch", shipped)  # declared, never observed: nothing shows it is permitted
        self.assertEqual(invocation.audit_retrieval("claude-code@2.1.278"), ["local", "workspace"])

    def test_the_set_follows_the_definitions_tools(self):
        for tools, expected in (("Read, Glob, Grep", ["local", "workspace"]), ("Glob, Grep", []),
                                ("WebFetch", []), ("Read", ["local", "workspace"]), ("Bash, Read", ["local", "workspace"])):
            with self.subTest(tools=tools), patch.object(invocation, "_CLAUDE_AUDITOR", definition(tools)):
                self.assertEqual(invocation.audit_retrieval("claude-code@2.1.278"), expected)

    def test_a_definition_without_tools_is_an_error_not_a_default(self):
        bare = Path(tempfile.mkdtemp()) / "a.md"
        bare.write_text("---\nname: a\n---\n", encoding="utf-8")
        with patch.object(invocation, "_CLAUDE_AUDITOR", bare), self.assertRaises(ValueError):
            invocation.audit_retrieval("claude-code@2.1.278")

    def test_codex_has_no_auditor_and_pi_observes_its_own_set(self):
        self.assertEqual(invocation.audit_retrieval("codex-cli@0.146.0"), [])
        with self.assertRaises(KeyError):
            invocation.audit_retrieval("pi@1.0.0")

    def test_provenance_carries_the_observed_set(self):
        got = invocation.provenance("claude", True, "s", {}, profile_id="claude-code@2.1.278")
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

    def test_the_shared_rubric_covers_confirmed_records_missing_sources_and_human_decisions(self):
        text = (self.agents / "empirica-auditor.md").read_text(encoding="utf-8")
        for required in ("`verified_by_service` or `confirmed_by_human`", "fail the claim that\n  rests on it",
                         "`human_decision`", "do not second-guess"):
            self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
