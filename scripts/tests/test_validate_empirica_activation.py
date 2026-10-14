#!/usr/bin/env python3
"""The activation validator's skill checks reject stale bundled-runtime claims."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate_empirica_activation as activation

DISCLOSURES = "\n".join(activation.REQUIRED_SKILL_DISCLOSURES)


class SkillRuntimeTests(unittest.TestCase):
    def test_the_committed_skill_is_accepted(self):
        skill = Path(__file__).resolve().parents[2] / activation.SKILL
        self.assertEqual(activation.skill_runtime_problems(skill.read_text(encoding="utf-8")), [])

    def test_complete_external_runtime_disclosure_is_accepted(self):
        self.assertEqual(activation.skill_runtime_problems(DISCLOSURES), [])

    def test_every_stale_bundled_claim_is_rejected(self):
        for term in activation.STALE_PI_RUNTIME_TERMS:
            with self.subTest(term=term):
                problems = activation.skill_runtime_problems(f"{DISCLOSURES}\n{term}")
                self.assertEqual(len(problems), 1)
                self.assertIn(term, problems[0])

    def test_dropping_the_external_runtime_disclosure_is_rejected(self):
        text = DISCLOSURES.replace("external `pi-subagents`", "")
        self.assertEqual(len(activation.skill_runtime_problems(text)), 1)


HOSTS_DOC = Path(__file__).resolve().parents[2] / activation.HOSTS_DOC


class HostsDocRuntimeTests(unittest.TestCase):
    """doc/hosts.md's supported-versions table is the contract's reviewed list, nothing else."""

    def peers(self):
        return activation.recorded_peers()

    def requires(self, version):
        return activation.pi_requirement(self.peers()[version], activation.PI_PROFILE["compatibility"]["minimum"])

    def table(self, versions, requires=None):
        rows = "\n".join(f"| `{v}` | `{(requires or {}).get(v, self.requires(v) if v in self.peers() else '>=1.0.0')}` "
                         f"| reviewed |" for v in versions)
        policy = activation.PI_PROFILE["subagents_compatibility"]
        return (f"{activation.pi_interval(activation.PI_PROFILE)} >={policy['minimum']},<{policy['maximum_exclusive']} {policy['policy_id']} "
                f"{activation.SUPPORT_RANGE_DISCLOSURE}\n{rows}\n")

    def versions(self):
        return activation.reviewed_versions()

    def test_the_committed_hosts_doc_is_accepted(self):
        self.assertEqual(activation.hosts_doc_runtime_problems(HOSTS_DOC.read_text(encoding="utf-8"), activation.PI_PROFILE), [])

    def test_a_table_equal_to_the_reviewed_list_is_accepted(self):
        self.assertEqual(activation.hosts_doc_runtime_problems(self.table(self.versions()), activation.PI_PROFILE), [])

    def test_a_missing_reviewed_version_is_named(self):
        problems = activation.hosts_doc_runtime_problems(self.table(self.versions()[1:]), activation.PI_PROFILE)
        self.assertEqual(problems, [f"doc/hosts.md supported-versions table has no row for pi-subagents {self.versions()[0]}"])

    def test_a_version_with_no_compat_record_in_the_table_is_named(self):
        problems = activation.hosts_doc_runtime_problems(self.table([*self.versions(), "9.9.9"]), activation.PI_PROFILE)
        self.assertEqual(problems, ["doc/hosts.md lists pi-subagents 9.9.9, which has no compat record"])

    def test_a_hosts_doc_that_omits_the_supported_range_is_named(self):
        policy = activation.PI_PROFILE["subagents_compatibility"]
        text = self.table(self.versions()).replace(f">={policy['minimum']},<{policy['maximum_exclusive']}", "")
        problems = activation.hosts_doc_runtime_problems(text, activation.PI_PROFILE)
        self.assertEqual(problems, [f"doc/hosts.md omits the supported pi-subagents range >={policy['minimum']},<{policy['maximum_exclusive']}"])

    def test_a_stale_interval_or_dropped_fail_closed_sentence_is_named(self):
        text = self.table(self.versions()).replace(activation.pi_interval(activation.PI_PROFILE), ">=0.90.0,<0.91.0")
        self.assertEqual(len(activation.hosts_doc_runtime_problems(text, activation.PI_PROFILE)), 1)
        text = self.table(self.versions()).replace(activation.SUPPORT_RANGE_DISCLOSURE, "")
        self.assertEqual(len(activation.hosts_doc_runtime_problems(text, activation.PI_PROFILE)), 1)

    def test_the_requires_pi_column_is_the_declared_peer_raised_to_the_floor_of_the_pi_interval(self):
        minimum = activation.PI_PROFILE["compatibility"]["minimum"]
        self.assertEqual(minimum, "1.0.0")  # ADR-0069: nothing older than Pi 1.0.0 is supported
        self.assertEqual({v: self.requires(v) for v in self.versions()}, {v: ">=1.0.0" for v in self.versions()})
        self.assertEqual(activation.pi_requirement(">=1.2.0", minimum), ">=1.2.0")  # a peer above the floor wins

    def test_a_row_below_the_pi_floor_is_named(self):
        for version in self.versions():
            with self.subTest(version=version):
                text = self.table(self.versions(), requires={version: ">=0.99.0"})
                problems = activation.hosts_doc_runtime_problems(text, activation.PI_PROFILE)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(f"pi-subagents {version} requires Pi '>=0.99.0'", problems[0])
                self.assertIn("'>=1.0.0'", problems[0])

    def test_a_row_without_the_requirement_column_is_rejected(self):
        old = "\n".join(f"| `{v}` | reviewed |" for v in self.versions())
        text = self.table([]) + old + "\n"
        problems = activation.hosts_doc_runtime_problems(text, activation.PI_PROFILE)
        self.assertEqual(len(problems), len(self.versions()))
        self.assertTrue(all("lacks the 'Requires Pi' column" in p for p in problems))

    def test_a_peer_form_the_check_cannot_read_is_an_error_not_a_pass(self):
        with self.assertRaisesRegex(ValueError, "not a plain"):
            activation.pi_requirement("^1.2.3", "1.0.0")

    def test_the_skill_interval_is_read_from_the_contract(self):
        self.assertIn(activation.pi_interval(activation.PI_PROFILE), activation.REQUIRED_SKILL_DISCLOSURES)

    def test_the_skill_claims_no_codex_range(self):
        """G10 (ADR-0069 G10-R1): Empirica does not run on Codex, so the skill is not required to disclose a Codex range."""
        self.assertFalse([t for t in activation.REQUIRED_SKILL_DISCLOSURES if "0.146" in t or "odex" in t])


if __name__ == "__main__":
    unittest.main()
