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


README = Path(__file__).resolve().parents[2] / activation.README


class ReadmeRuntimeTests(unittest.TestCase):
    """The README's supported-versions table is the contract's reviewed list, nothing else."""

    def peers(self):
        return activation.recorded_peers(activation.PI_PROFILE["subagents_compatibility"])

    def requires(self, version):
        return activation.pi_requirement(self.peers()[version], activation.PI_PROFILE["compatibility"]["minimum"])

    def table(self, versions, requires=None, receipt="pending A9", instruction=True):
        rows = "\n".join(f"| `{v}` | `{(requires or {}).get(v, self.requires(v) if v in self.peers() else '>=0.84.1')}` "
                         f"| {receipt} | reviewed |" for v in versions)
        policy = activation.PI_PROFILE["subagents_compatibility"]
        fallback = activation.pi_fallback({v: self.requires(v) for v in self.peers()},
                                          activation.PI_PROFILE["compatibility"]["minimum"])
        older = f"Pi <{fallback[0]}: `pi-subagents@{fallback[1]}`" if instruction and fallback else ""
        return (f"{activation.pi_interval(activation.PI_PROFILE)} {policy['policy_id']} "
                f"{activation.UNREVIEWED_DISCLOSURE} {older}\n{rows}\n")

    def versions(self):
        return activation.PI_PROFILE["subagents_compatibility"]["reviewed_versions"]

    def test_the_committed_readme_is_accepted(self):
        self.assertEqual(activation.readme_runtime_problems(README.read_text(encoding="utf-8"), activation.PI_PROFILE), [])

    def test_a_table_equal_to_the_reviewed_list_is_accepted(self):
        self.assertEqual(activation.readme_runtime_problems(self.table(self.versions()), activation.PI_PROFILE), [])

    def test_a_missing_reviewed_version_is_named(self):
        problems = activation.readme_runtime_problems(self.table(self.versions()[1:]), activation.PI_PROFILE)
        self.assertEqual(problems, [f"README supported-versions table has no row for pi-subagents {self.versions()[0]}"])

    def test_an_unreviewed_version_in_the_table_is_named(self):
        problems = activation.readme_runtime_problems(self.table([*self.versions(), "9.9.9"]), activation.PI_PROFILE)
        self.assertEqual(problems, ["README lists pi-subagents 9.9.9, which the contract does not review"])

    def test_a_stale_interval_or_dropped_fail_closed_sentence_is_named(self):
        text = self.table(self.versions()).replace(activation.pi_interval(activation.PI_PROFILE), ">=0.84.1,<0.90.0")
        self.assertEqual(len(activation.readme_runtime_problems(text, activation.PI_PROFILE)), 1)
        text = self.table(self.versions()).replace(activation.UNREVIEWED_DISCLOSURE, "")
        self.assertEqual(len(activation.readme_runtime_problems(text, activation.PI_PROFILE)), 1)

    def test_the_requires_pi_column_is_derived_from_the_recorded_pi_ai_peer(self):
        self.assertEqual({v: self.requires(v) for v in self.versions()},
                         {"0.50.0": ">=0.84.1", "0.64.0": ">=0.84.1", "0.74.0": ">=0.86.1", "0.75.0": ">=0.86.1"})
        self.assertEqual(activation.pi_fallback({v: self.requires(v) for v in self.versions()}, "0.84.1"),
                         ("0.86.1", "0.64.0"))

    def test_a_row_missing_the_recorded_pi_floor_is_named(self):
        for version in ("0.74.0", "0.75.0"):
            with self.subTest(version=version):
                text = self.table(self.versions(), requires={version: ">=0.84.1"})
                problems = activation.readme_runtime_problems(text, activation.PI_PROFILE)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(f"pi-subagents {version} requires Pi '>=0.84.1'", problems[0])
                self.assertIn("'>=0.86.1'", problems[0])

    def test_a_requirement_that_the_recorded_peer_does_not_imply_is_named(self):
        text = self.table(self.versions(), requires={"0.64.0": ">=0.86.1"})
        self.assertEqual(len(activation.readme_runtime_problems(text, activation.PI_PROFILE)), 1)

    def test_a_table_in_the_old_shape_without_the_new_columns_is_rejected(self):
        old = "\n".join(f"| `{v}` | reviewed |" for v in self.versions())
        text = self.table([]) + old + "\n"
        problems = activation.readme_runtime_problems(text, activation.PI_PROFILE)
        self.assertEqual(len(problems), len(self.versions()))
        self.assertTrue(all("lacks the 'Requires Pi' and native-receipt columns" in p for p in problems))

    def test_a_row_that_does_not_say_whether_a_native_receipt_exists_is_named(self):
        problems = activation.readme_runtime_problems(self.table(self.versions(), receipt=""), activation.PI_PROFILE)
        self.assertEqual(len(problems), len(self.versions()))
        self.assertTrue(all("native receipt exists" in p for p in problems))

    def test_dropping_the_older_pi_install_instruction_is_named(self):
        problems = activation.readme_runtime_problems(self.table(self.versions(), instruction=False), activation.PI_PROFILE)
        self.assertEqual(problems, ["README omits the install instruction for older Pi: Pi <0.86.1: `pi-subagents@0.64.0`"])

    def test_a_peer_form_the_check_cannot_read_is_an_error_not_a_pass(self):
        with self.assertRaisesRegex(ValueError, "not a plain"):
            activation.pi_requirement("^0.86.1", "0.84.1")

    def test_the_skill_interval_is_read_from_the_contract(self):
        self.assertIn(activation.pi_interval(activation.PI_PROFILE), activation.REQUIRED_SKILL_DISCLOSURES)
        self.assertEqual(activation.pi_interval(activation.PI_PROFILE), ">=0.84.1,<1.1.0")


if __name__ == "__main__":
    unittest.main()
