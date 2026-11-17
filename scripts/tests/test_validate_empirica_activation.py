#!/usr/bin/env python3
"""The activation validator's skill checks reject stale bundled-runtime claims."""
import json
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


class SkillSizeTests(unittest.TestCase):
    """The size ratchet: SKILL.md is measured in characters, whole and at the top (Claude re-attaches ~5,000 tokens)."""

    def committed(self):
        return (Path(__file__).resolve().parents[2] / activation.SKILL).read_text(encoding="utf-8")

    def test_the_committed_skill_is_accepted(self):
        text = self.committed()
        self.assertEqual(activation.skill_size_problems(text), [])
        self.assertEqual(activation.skill_anchor_problems(text), [])

    def test_a_skill_of_ceiling_plus_one_characters_is_rejected_naming_both_numbers(self):
        limit = activation.SKILL_MAX_CHARS
        self.assertEqual(activation.skill_size_problems("x" * limit), [])
        problems = activation.skill_size_problems("x" * (limit + 1))
        self.assertEqual(len(problems), 1)
        self.assertIn(f"{limit + 1:,}", problems[0])
        self.assertIn(f"{limit:,}", problems[0])
        self.assertIn("references/", problems[0])

    def anchored(self, position):
        text = "x" * (activation.SKILL_TOP_CHARS + 100)
        return text[:position] + "ANCHOR" + text[position + len("ANCHOR"):]

    def test_an_anchor_only_after_the_cut_is_rejected(self):
        text = self.anchored(activation.SKILL_TOP_CHARS - 5)  # starts before the cut but ends after it
        problems = activation.skill_anchor_problems(text, ["ANCHOR"])
        self.assertEqual(len(problems), 1)
        self.assertIn("ANCHOR", problems[0])
        self.assertIn(f"is not entirely within the first {activation.SKILL_TOP_CHARS:,} rendered characters", problems[0])

    def test_an_anchor_missing_entirely_is_rejected(self):
        problems = activation.skill_anchor_problems("x" * 100, ["ANCHOR"])
        self.assertEqual(len(problems), 1)
        self.assertIn("ANCHOR", problems[0])
        self.assertIn("missing", problems[0])

    def test_an_anchor_before_the_cut_is_accepted(self):
        text = self.anchored(activation.SKILL_TOP_CHARS - len("ANCHOR"))  # ends exactly at the cut
        self.assertEqual(activation.skill_anchor_problems(text, ["ANCHOR"]), [])

    def test_empty_and_whitespace_anchors_are_rejected(self):
        for anchor in ("", " ", "\n\t"):
            with self.subTest(anchor=anchor):
                problems = activation.skill_anchor_problems("x" * 100, [anchor])
                self.assertEqual(len(problems), 1)
                self.assertIn("empty", problems[0])

    def test_the_cut_is_measured_after_the_frontmatter(self):
        front = "---\nname: x\ndescription: " + "d" * 900 + "\n---\n"
        body = "y" * activation.SKILL_TOP_CHARS
        fits = front + body[:-len("ANCHOR")] + "ANCHOR"  # ends exactly at the cut of the body, far past it in the file
        self.assertEqual(activation.skill_anchor_problems(fits, ["ANCHOR"]), [])
        late = front + body + "ANCHOR"
        self.assertEqual(len(activation.skill_anchor_problems(late, ["ANCHOR"])), 1)
        in_front = "---\nname: ANCHOR\n---\nbody"
        self.assertIn("missing", activation.skill_anchor_problems(in_front, ["ANCHOR"])[0])

    def test_an_anchor_after_the_arguments_substitution_carries_the_goal_allowance(self):
        allowance = activation.GOAL_ALLOWANCE
        head = "The user invocation is `$ARGUMENTS`.\n"
        def at(end):  # a skill whose ANCHOR ends at body offset `end`
            return head + "y" * (end - len(head) - len("ANCHOR")) + "ANCHOR"
        self.assertEqual(activation.skill_anchor_problems(at(activation.SKILL_TOP_CHARS - allowance), ["ANCHOR"]), [])
        problems = activation.skill_anchor_problems(at(activation.SKILL_TOP_CHARS - allowance + 1), ["ANCHOR"])
        self.assertEqual(len(problems), 1)
        # An anchor before the substitution carries no allowance: it ends exactly at the cut, `$ARGUMENTS` after it.
        before = "y" * (activation.SKILL_TOP_CHARS - len("ANCHOR")) + "ANCHOR" + " `$ARGUMENTS`\n"
        self.assertEqual(activation.skill_anchor_problems(before, ["ANCHOR"]), [])

    def test_the_ceiling_may_only_be_lowered_and_stays_within_headroom_of_the_skill(self):
        self.assertLessEqual(activation.SKILL_MAX_CHARS, 20_000)
        self.assertLessEqual(activation.SKILL_TOP_CHARS, 13_300)
        self.assertLessEqual(activation.SKILL_TOP_CHARS, activation.SKILL_MAX_CHARS)
        self.assertTrue(5 <= len(activation.REQUIRED_EARLY_ANCHORS) <= 9, "the early anchors are chosen")
        limit = activation.SKILL_MAX_CHARS
        self.assertEqual(activation.skill_size_problems("x" * (limit - 1_000)), [], "exactly 1,000 of headroom is allowed")
        problems = activation.skill_size_problems("x" * (limit - 1_001))
        self.assertEqual(len(problems), 1)
        self.assertIn("lower SKILL_MAX_CHARS to", problems[0])
        self.assertIn(f"to {-(-(limit - 1_001 + 500) // 100) * 100:,}", problems[0], "size plus 500, rounded up to 100")
        self.assertEqual(activation.skill_size_problems(self.committed()), [], "the committed skill is within the headroom")


    def test_anchors_with_a_dollar_and_substitution_forms_are_rejected(self):
        self.assertIn("$", activation.skill_anchor_problems("a $ARGUMENTS b", ["$ARGUMENTS"])[0])
        self.assertEqual(activation.skill_anchor_problems("goal: `$ARGUMENTS`", []), [], "one `$ARGUMENTS` is the rule")
        for body in ("`$ARGUMENTS` and `$ARGUMENTS`", "`$ARGUMENTS[0]`", "${CLAUDE_SKILL_DIR}/x", "use $1 here"):
            with self.subTest(body=body):
                problems = activation.skill_anchor_problems(body, [])
                self.assertEqual(len(problems), 1)
                self.assertIn("substitution", problems[0])
        self.assertEqual(activation.skill_anchor_problems("---\nname: $ARGUMENTS $ARGUMENTS\n---\n`$ARGUMENTS`", []), [], "frontmatter is not rendered")


class SkillOrderTests(unittest.TestCase):
    """S3: the reorder moved lines and changed none; the anchors are unique and before the goal substitution."""

    # sha256 of the sorted non-blank, non-heading lines (right-stripped, no re-wrapping) of SKILL.md before the reorder.
    LINES_DIGEST = "483fa149751e31796dec9e9ed020a2883ef12fdcc08691830aa17dcb581f9cc6"
    # The only lines the reorder added: one lead-in per moved block whose cross-reference would otherwise dangle.
    LEAD_INS = ("Claim discipline; the two folds named here are defined in section 8 below.",)
    # The one allowed sentence split: this original line is now two lines (the read-before-route rule moved up as its own
    # paragraph; "Use this inline bootstrap matrix:" stayed with the host matrix). The digest sees the original line.
    SPLIT = ("(route → graph → approved configuration).** Use this inline bootstrap matrix:",
             "(route → graph → approved configuration).**", "Use this inline bootstrap matrix:")

    def skill(self):
        return (Path(__file__).resolve().parents[2] / activation.SKILL).read_text(encoding="utf-8")

    def digest(self, text, lead_ins=LEAD_INS):
        import hashlib
        lines = [line.rstrip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
        for lead in lead_ins:
            self.assertEqual(lines.count(lead), 1, lead)
            lines.remove(lead)
        whole, first, second = self.SPLIT
        self.assertEqual((lines.count(first), lines.count(second)), (1, 1), "the split lines")
        lines.remove(first)
        lines.remove(second)
        lines.append(whole)
        return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()

    def test_every_line_is_preserved_and_none_added(self):
        text = self.skill()
        self.assertEqual(self.digest(text), self.LINES_DIGEST)
        lines = text.splitlines()
        gone = next(i for i, line in enumerate(lines) if line.startswith("Treat training-weight"))
        deleted = "\n".join(lines[:gone] + lines[gone + 1:])
        self.assertNotEqual(self.digest(deleted), self.LINES_DIGEST, "a deleted line is caught")
        self.assertNotEqual(self.digest(text + "\nOne more sentence.\n"), self.LINES_DIGEST, "an added line is caught")
        self.assertNotEqual(self.digest(text.replace("hypothesis", "guess", 1)), self.LINES_DIGEST, "a rewritten line is caught")

    def test_each_anchor_occurs_exactly_once_and_before_the_invocation(self):
        body = activation.rendered_body(self.skill())
        invocation = body.index(activation.ARGUMENTS_TOKEN)
        for anchor in activation.REQUIRED_EARLY_ANCHORS:
            with self.subTest(anchor=anchor):
                self.assertEqual(body.count(anchor), 1)
                self.assertLessEqual(body.index(anchor) + len(anchor), invocation, "no anchor needs the goal allowance")


class SkillSizeWiringTests(unittest.TestCase):
    """main() enforces the size ratchet: the checks are called, with the module's own constants."""

    def run_main(self, skill_text, **patches):
        import contextlib
        import io
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md"
            skill.write_text(skill_text, encoding="utf-8")
            err = io.StringIO()
            with mock.patch.object(activation, "SKILL", skill), contextlib.redirect_stderr(err):
                try:
                    for name, value in patches.items():
                        mock.patch.object(activation, name, value).start()
                    with self.assertRaises(SystemExit) as raised:
                        activation.main()
                finally:
                    mock.patch.stopall()
        return raised.exception.code, err.getvalue()

    def test_an_oversize_skill_fails_the_validator(self):
        code, err = self.run_main("x" * (activation.SKILL_MAX_CHARS + 1))
        self.assertEqual(code, 1)
        self.assertIn("characters, over the", err)

    def test_a_missing_required_anchor_fails_the_validator(self):
        committed = (Path(__file__).resolve().parents[2] / activation.SKILL).read_text(encoding="utf-8")
        code, err = self.run_main(committed, REQUIRED_EARLY_ANCHORS=("NO SUCH ANCHOR",))
        self.assertEqual(code, 1)
        self.assertIn("anchor 'NO SUCH ANCHOR' is missing", err)


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
        # Releases through 0.79.2 declare a pi-ai peer below the floor; 0.80.0 and later declare >=1.2.0 (their own manifests).
        expected = {v: ">=1.2.0" if activation._semver(v) >= activation._semver("0.80.0") else ">=1.0.0" for v in self.versions()}
        self.assertEqual({v: self.requires(v) for v in self.versions()}, expected)
        self.assertEqual(activation.pi_requirement(">=1.2.0", minimum), ">=1.2.0")  # a peer above the floor wins

    def test_a_row_below_the_pi_floor_is_named(self):
        for version in self.versions():
            with self.subTest(version=version):
                text = self.table(self.versions(), requires={version: ">=0.99.0"})
                problems = activation.hosts_doc_runtime_problems(text, activation.PI_PROFILE)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(f"pi-subagents {version} requires Pi '>=0.99.0'", problems[0])
                self.assertIn(f"'{self.requires(version)}'", problems[0])

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


HOOKS_JSON = Path(__file__).resolve().parents[2] / "plugins/empirica/hooks/hooks.json"


class HookRegistrationTests(unittest.TestCase):
    """The Claude hook set is closed, and the auto-mode denial hook is part of it (issue #69)."""

    def hooks(self):
        return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))

    def test_the_committed_hook_set_is_accepted(self):
        self.assertEqual(activation.hook_event_problems(self.hooks()), [])

    def test_permission_denied_is_an_expected_event_with_a_registered_thin_entrypoint(self):
        self.assertIn("PermissionDenied", activation.HOOK_EVENTS)
        self.assertEqual(activation.ENTRYPOINTS["permission_denied.py"], "permission_denied_main")

    def test_the_old_set_without_permission_denied_is_rejected(self):
        hooks = self.hooks()
        del hooks["hooks"]["PermissionDenied"]
        problems = activation.hook_event_problems(hooks)
        self.assertEqual(problems, ["hooks.json lifecycle events changed"])

    def test_the_expected_set_is_exactly_the_old_set_plus_permission_denied(self):
        self.assertEqual(activation.HOOK_EVENTS - {"PermissionDenied"}, {
            "UserPromptExpansion", "PreToolUse", "PostToolUseFailure", "Stop", "SubagentStart",
            "SubagentStop", "SessionStart", "PostModelSwitch"})

    def test_an_unexpected_event_is_rejected(self):
        hooks = self.hooks()
        hooks["hooks"]["Notification"] = hooks["hooks"]["PermissionDenied"]
        self.assertEqual(activation.hook_event_problems(hooks), ["hooks.json lifecycle events changed"])

    def test_a_hook_that_bypasses_the_registered_entrypoints_is_rejected(self):
        hooks = self.hooks()
        hooks["hooks"]["PermissionDenied"][0]["hooks"][0]["args"] = ["${CLAUDE_PLUGIN_ROOT}/hooks/other.py"]
        problems = activation.hook_event_problems(hooks)
        self.assertEqual(len(problems), 1)
        self.assertIn("bypasses a registered thin entrypoint", problems[0])


if __name__ == "__main__":
    unittest.main()
