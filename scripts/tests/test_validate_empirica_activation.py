#!/usr/bin/env python3
"""The activation validator's skill checks reject stale bundled-runtime claims."""
import json
import sys
import re
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
        text = DISCLOSURES.replace("external pi-subagents", "")
        self.assertEqual(len(activation.skill_runtime_problems(text)), 1)


class SkillSizeTests(unittest.TestCase):
    """The size ratchet: SKILL.md is measured in characters, whole and at the top (Claude re-attaches ~5,000 tokens)."""

    def committed(self):
        return (Path(__file__).resolve().parents[2] / activation.SKILL).read_text(encoding="utf-8")

    def test_the_committed_skill_is_accepted(self):
        text = self.committed()
        self.assertEqual(activation.skill_size_problems(text), [])
        self.assertEqual(activation.skill_anchor_problems(text), [])

    def test_the_ratchet_counts_the_rendered_body_and_the_description_only(self):
        # S10 fix F4: `compatibility` and `allowed-tools` are install metadata; the description is what triggers the skill.
        front = "---\nname: x\ndescription: \"%s\"\nallowed-tools: %s\ncompatibility: %s\n---\n"
        base = front % ("d" * 100, "a" * 10, "c" * 10) + "b" * 500
        self.assertEqual(activation.measured_size(base), 600)
        padded = front % ("d" * 100, "a" * 400, "c" * 400) + "b" * 500
        self.assertEqual(activation.measured_size(padded), 600, "metadata beyond the description is not counted")
        self.assertEqual(activation.measured_size(front % ("d" * 150, "a", "c") + "b" * 500), 650, "the description counts")
        self.assertEqual(activation.measured_size(front % ("d" * 100, "a", "c") + "b" * 700), 800, "the body counts")
        self.assertEqual(activation.measured_size("x" * 40), 40, "no frontmatter: the whole text is the body")
        self.assertIn("SKILL.md is 600 characters, over the 599", activation.skill_size_problems(padded, max_chars=599)[0])
        self.assertEqual(activation.skill_size_problems(padded, max_chars=600), [], "the ceiling counts the same number")

    def test_the_description_says_when_to_use_the_skill(self):
        description = re.search(r"^description: \"(.*)\"$", self.committed(), re.MULTILINE).group(1)
        self.assertIn("Use for", description, "a when-to-use clause, not only what the skill is")
        for trigger in ("architectural uncertainty", "competing approaches", "risky assumptions"):
            self.assertIn(trigger, description)

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

    def test_an_anchor_must_end_before_the_goal_substitution(self):
        # A goal may be thousands of characters, so no allowance is safe: every anchor ends before the first `$ARGUMENTS`.
        self.assertFalse(hasattr(activation, "GOAL_ALLOWANCE"), "no fixed goal allowance")
        head = "Intro. "
        ok = head + "ANCHOR" + " `$ARGUMENTS`\n"
        self.assertEqual(activation.skill_anchor_problems(ok, ["ANCHOR"]), [])
        after = head + "`$ARGUMENTS`\n" + "ANCHOR"
        problems = activation.skill_anchor_problems(after, ["ANCHOR"])
        self.assertEqual(len(problems), 1)
        self.assertIn("must end before the first `$ARGUMENTS`", problems[0])
        # An anchor cannot overlap the placeholder: one containing `$` is refused, so none can straddle it.
        self.assertIn("contains `$`", activation.skill_anchor_problems(ok, ["ANCHOR `$ARG"])[0])

    def test_the_goal_offset_is_measured_on_the_rendered_body_not_the_file(self):
        # S3 r2 N2: an anchor after `$ARGUMENTS` by less than the frontmatter's length would pass if the offset were taken on
        # the raw file (the anchor is found in the body, the placeholder in the file).
        front = "---\nname: x\ndescription: " + "d" * 900 + "\n---\n"
        text = front + "Intro `$ARGUMENTS` ANCHOR\n"
        problems = activation.skill_anchor_problems(text, ["ANCHOR"])
        self.assertEqual(len(problems), 1)
        self.assertIn("must end before the first `$ARGUMENTS`", problems[0])
        self.assertEqual(activation.skill_anchor_problems(front + "Intro ANCHOR `$ARGUMENTS`\n", ["ANCHOR"]), [])

    def test_the_closing_frontmatter_delimiter_may_have_trailing_whitespace(self):
        # The same rule as scripts/validate_skills.py: a delimiter line is `line.strip() == "---"`.
        text = "---\nname: x\n--- \nbody ANCHOR `$ARGUMENTS`"
        self.assertEqual(activation.rendered_body(text), "body ANCHOR `$ARGUMENTS`")
        self.assertEqual(activation.skill_anchor_problems(text, ["ANCHOR"]), [])
        self.assertEqual(activation.rendered_body("--- \nname: x\n---\nbody"), "body")

    def test_the_ceiling_may_only_be_lowered_and_stays_within_headroom_of_the_skill(self):
        # The bound is the CURRENT ceiling: a PR that lowers SKILL_MAX_CHARS lowers this number in the same edit, so a later
        # raise needs a visible edit here. The cut is a rendered-window size, independent of the file size.
        self.assertLessEqual(activation.SKILL_MAX_CHARS, 7_300)
        self.assertLessEqual(activation.SKILL_TOP_CHARS, 13_300)
        self.assertTrue(5 <= len(activation.REQUIRED_EARLY_ANCHORS) <= 12, "the early anchors are chosen")
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
                self.assertIn("escape or reword", problems[0])
        self.assertEqual(activation.skill_anchor_problems("---\nname: $ARGUMENTS $ARGUMENTS\n---\n`$ARGUMENTS`", []), [], "frontmatter is not rendered")


class SkillOrderTests(unittest.TestCase):
    """S10: the skill keeps only the judgment nothing else carries. Each judgment sentence is pinned by its exact text, so a
    deletion is a visible edit here; the anchors are unique and end before the goal substitution.

    S3's paragraph digest proved that a REORDER moved text and changed none. S10 rewrites most paragraphs on purpose, so the
    digest would need an exception for nearly every one; the exact-text pins below replace it."""

    # (D) The judgment, in its shortest positive form. Everything else was enforced by a schema, a contract reason or the
    # start refusals, or shown by the author view, the start context or a reference (the S10 report maps each sentence).
    JUDGMENT = (
        "Never edit operational state under `~/.empirica-plugin/` or knowledge under `refs/empirica/*` directly, and never create Empirica state under `.claude/`, `.codex/`, or `.pi/`.",
        "Treat training-weight knowledge as hypothesis: discharge load-bearing claims against evidence or surface them as unverified.",
        "**Experiments go through Empirica only.** During an active run, every experimental workload is an `empirica_observe` `spike_request`; never run one through the ambient host shell, `docker run`, or `docker exec`, including after an unavailable or refused result. Neither an image-acquisition approval nor read-only inspection substitutes for one.",
        "**Do not read any file, including a skill reference, before the `investigate` witness is admitted\n(route \u2192 graph \u2192 approved configuration).**",
        "A run exists only if `/empirica` injected a handle; without one, nothing was started.",
        "Discard refuted claims or replace them with narrower ones; never preserve a preferred design by grading contrary evidence away. New claims attach to the root and pass through both folds (section 8).",
        "Evidence leaves, attribution, child events, and audit verdicts are trusted host ingress; submit only the author actions the host exposes. Do delegated work through Empirica's children: work by an external agent CLI is neither budgeted nor attributed to a producer.",
        "Stop before investigation and report the exact unsupported capability when a public tool or the auditor launch is absent, when the start refuses the host, or when the host is unknown; never borrow another host's capabilities, imitate a missing operation in prose, or write runtime artifacts by hand.",
        "At invocation, begin with section 6; the loop, audit and terminal steps in sections 2\u20135 apply once investigation is admitted.",
        "After each evidence batch, reread it, drop refuted branches, add only claims the evidence forces, and take the next call it names. Do not loop on unchanged state or infer convergence from confidence-like language;",
        "Approval is not convergence: it means the evidence passed the service's rules, not that it convinces.",
        "Check `GetArgument`, the argument the auditor will receive: each claim must follow from its cited sources alone.",
        "Never substitute an ordinary model response for the audit.",
        "- `Allow(converged=true)` permits a convergence claim.",
        "- `Allow(converged=false)` permits an honest non-converged report; `Block`, `Inert` with an active handle, `Fault`, malformed output, or transport failure never permits convergence. A terminal run is reported, never re-judged.",
        "To accept residual or deferred scope, or an exhausted pass budget, call it once with `intent: \"stop\"`; only the resulting `Allow(converged=false)` authorizes an honest stopped report.",
        "**converged** \u2014 a passing bound audit covers the current argument; **stopped** as residual (human or data obligations remain), frozen (scope closed, later claims deferred) or budget (the process ended); **unsupported** \u2014 the host lacked a capability; **faulted** \u2014 state, artifacts, transport, or protocol failed closed.",
        "Commit only the goal's product artifacts, tests, and accepted decisions.",
        "2. Record `{\"kind\":\"route\",\"reason\":\"<why>\"}`, then propose the smallest claim graph from supplied context only, by the rules in section 7.",
        "1. Restate the goal without the flags; list each dependency as **known** or **unknown**.",
        "3. Read `empirica_read(operation=\"GetRun\")`, then size the run from the gating claims, how many need experiments, and the expected audit rounds (normally 2: an initial audit and one retry).",
        "Propose all three ceilings with `configure_run` and a rationale explaining the sizing.",
        "4. Wait until the view says `approved`, taking its `next:` after any dismissal.",
        "The three public tools return a plain-text author view whose `Reasons`, `Open obligations` and `Next` calls are the worklist.",
        "Follow the view's `next:` rather than re-calling the gate;",
        "Call `report_convergence` when the graph, evidence, and scope are current; after the bound audit its `Allow` is the terminal decision.",
        "Build the smallest closed graph whose gating root is the goal and whose gating claims cover every material unknown:",
        "The view prints the auditor launch.",
        "The user invocation is `$ARGUMENTS`.",
        "Investigation children draw on `max_spawns` and the auditor on `max_audit_spawns`; neither borrows from the other. A pass is charged only when the derivation changes.",
        "Then record `{\"kind\":\"investigate\"}` before any native read, search, command, evidence submission, or child launch; approval supplies neither witness.",
        "- One gating claim per material unknown or invariant; scope you want to defer goes in non-gating claims below the root.",
        "- `needs-experiment` only when a deterministic spike plan can falsify the claim.",
        "- `needs-decision` only for an irreducible choice: in deliberative mode the human answers it when you report, it needs no research, and where nobody can be asked it stays open as a residual; in auto mode research decides it (Fold 1) and nobody is asked.",
        "- Every other claim is `ordinary`.",
        "- Rewording a claim stales its evidence and audit coverage, so keep claims stable once evidence binds; state no verdict or confidence in a claim's text.",
        "Apply the folds in order (Fold 1, then Fold 2):",
        "1. **Fold 1: research first, for every gating claim except a deliberative `needs-decision` claim.** Cite a concrete code, docs, runtime, or web source that supports or refutes the exact current claim text; generic precedent is not evidence, and a summary of a source is not a citation.",
        "2. **Fold 2: spike second, only for `needs-experiment`, after supporting research.** Derive the falsifier from the claim: a plan whose verdict container would fail if the claim were false.",
    )
    # Every sentence that links a reference, pinned by exact text. The skill's whole rendered body is the headings, JUDGMENT
    # and LINKS and nothing else (test_the_body_is_exactly_the_pinned_sentences).
    LINKS = (
        "[references/host-capabilities.md](references/host-capabilities.md) helps diagnose a capability failure.",
        "[references/budget-freeze.md](references/budget-freeze.md) covers budgets, stalls and scope closure.",
        "When every in-scope gating claim is approved, read [references/audit.md](references/audit.md).",
        "[references/recovery.md](references/recovery.md) covers other reason codes.",
        "Read [references/handoff.md](references/handoff.md) before producing the final artifact, and report one of:",
        "Once investigation is admitted, see [claim-graph](references/claim-graph.md) and [governance](references/governance.md).",
        "Read [references/evidence.md](references/evidence.md) before adding evidence.",
    )
    # The unlisted remainder of the body: none. A sentence added to the skill must be listed above, visibly.
    SKELETON = ""
    # What each deleted restatement said is now carried elsewhere (the S10 report maps every sentence); none may return.
    DELETED = ("`missing: <code> via claim", "Pi retains", "hidden digests", "exactly once", "report first", "to reopen the dialog",
               "spike_backend", "spikes_unavailable", "spike-plan-draft", "qualified on", "inline bootstrap matrix",
               "EMPIRICA_AUTO_DELEGATION", "needs no research, and a", "not a prerequisite read", "The optional",
               "run.governance.state", "Claude can settle the turn", "fixed 8/1/2", "unattended", "a `needs-decision`\n   claim is human-owned")
    HEADINGS = ("1. Non-negotiables", "2. The loop", "3. Obtain independent audit", "4. Request the terminal decision",
                "5. Finalize and hand off", "6. Invocation, and route before investigating", "7. Refine the claim graph",
                "8. Earn or discard every gating claim")
    # The facts a first read and a compacted run must keep in the first screenful, pinned by exact string. A later change
    # may add anchors or reword one only by editing this list, visibly.
    REQUIRED = ("The view prints the auditor launch.",
                "`Allow(converged=true)` permits a convergence claim.",
                "call it once with `intent: \"stop\"`",
                "**faulted** \u2014 state, artifacts, transport, or protocol failed closed.",
                "**Do not read any file, including a skill reference, before the",
                "**Experiments go through Empirica only.**",
                "are trusted host ingress")

    def skill(self):
        return (Path(__file__).resolve().parents[2] / activation.SKILL).read_text(encoding="utf-8")

    def section(self, number):
        text = self.skill()
        start = text.index(f"\n## {number}. ")
        following = text.find(f"\n## {number + 1}. ", start + 1)
        return text[start:] if following == -1 else text[start:following]

    def residual(self, text):
        """What is left of the rendered body after the headings, JUDGMENT and LINKS are removed (list markers and blanks aside)."""
        body = "\n".join(line for line in activation.rendered_body(text).splitlines() if not line.startswith("#"))
        for sentence in sorted((*self.JUDGMENT, *self.LINKS), key=len, reverse=True):
            body = body.replace(sentence, "", 1)
        return re.sub(r"\s+", " ", body).strip()

    def test_the_body_is_exactly_the_pinned_sentences(self):
        # The guard closes both ways: a pinned sentence cannot go (the exactly-once test), and no unlisted sentence can come.
        self.assertEqual(self.residual(self.skill()), self.SKELETON)

    def test_deleting_a_judgment_sentence_or_adding_an_unlisted_one_fails(self):
        text = self.skill()
        for sentence in ("Follow the view's `next:` rather than re-calling the gate;",
                         "4. Wait until the view says `approved`, taking its `next:` after any dismissal.",
                         "The three public tools return a plain-text author view whose `Reasons`, `Open obligations` and `Next` calls are the worklist."):
            self.assertEqual(self.residual(text), self.SKELETON)
            gone = text.replace(sentence, "", 1)
            self.assertNotEqual(gone, text)
            self.assertFalse(all(gone.count(p) == 1 for p in (*self.JUDGMENT, *self.LINKS)), sentence)
        for added in ("\n\nAlways ask the human first.\n", "\n- A new bullet.\n"):
            self.assertNotEqual(self.residual(text + added), self.SKELETON, added)
        reworded = text.replace("Never substitute an ordinary model response", "Never use an ordinary model response")
        self.assertFalse(all(reworded.count(p) == 1 for p in (*self.JUDGMENT, *self.LINKS)))

    def test_every_judgment_sentence_is_present_exactly_once(self):
        text = self.skill()
        for sentence in (*self.JUDGMENT, *self.LINKS):
            with self.subTest(sentence=sentence[:60]):
                self.assertEqual(text.count(sentence), 1)

    def test_a_deleted_or_reworded_judgment_sentence_is_caught(self):
        text = self.skill()
        sentence = self.JUDGMENT[4]
        for broken in (text.replace(sentence, ""), text.replace("injected a handle", "set a handle")):
            self.assertNotEqual(sum(broken.count(s) == 1 for s in self.JUDGMENT), len(self.JUDGMENT))

    def test_the_needs_decision_rule_is_qualified_by_mode_and_auto_asks_nobody(self):
        """A12: the human answers a `needs-decision` claim only in deliberative mode; in auto, research decides it (Fold 1) and
        the auditor judges the choice. The skill says so once, in section 7, and Fold 1 names the same exception."""
        bullet = next(line for line in self.section(7).splitlines() if line.startswith("- `needs-decision`"))
        self.assertIn("in deliberative mode the human answers it when you report", bullet)
        self.assertIn("in auto mode research decides it (Fold 1) and nobody is asked", bullet)
        self.assertNotIn("irreducible human choice", self.skill())
        self.assertIn("except a deliberative `needs-decision` claim", self.section(8))
        self.assertNotIn("human-owned", self.skill())

    def test_the_restatements_the_skill_used_to_carry_are_gone(self):
        text = self.skill()
        for gone in self.DELETED:
            with self.subTest(gone=gone):
                self.assertNotIn(gone, text)
        self.assertEqual(text.count("needs no research"), 1, "section 7 alone carries the needs-decision rule")

    def test_the_sections_are_one_to_eight_in_order_and_section_nine_is_gone(self):
        text = self.skill()
        positions = [text.index(f"\n## {heading}\n") for heading in self.HEADINGS]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(re.findall(r"\n## (\d+)\. ", text), [str(n) for n in range(1, 9)])
        self.assertNotIn("\n## 9. ", text)

    def test_the_pointers_name_the_sections_they_mean(self):
        text = self.skill()
        # every `section N` / `sections N\u2013M` reference resolves to a heading, and the two lead-ins say what they point at
        for number in re.findall(r"\bsections? (\d)", text):
            self.assertIn(f"\n## {number}. ", text)
        self.assertIn("begin with section 6; the loop, audit and terminal steps in sections 2\u20135 apply", text)
        self.assertIn("pass through both folds (section 8)", text)
        self.assertIn("by the rules in section 7", text)
        self.assertTrue(self.section(8).lstrip().startswith("## 8. Earn or discard every gating claim"))
        self.assertIn("Fold 1", self.section(8))
        self.assertIn("Fold 2", self.section(8))

    def test_the_governance_link_is_one_and_after_the_route_section(self):
        text = self.skill()
        self.assertEqual(text.count("](references/governance.md)"), 1)
        self.assertGreater(text.index("](references/governance.md)"), text.index("\n## 7. "))

    def test_the_spike_fold_names_no_plan_shape(self):
        section = self.section(8)
        for gone in ("spike-plan-draft", "argv", "environment", "inputs", "schema", "image", "uid/gid", "120"):
            self.assertNotIn(gone, section, gone)
        self.assertTrue(section.rstrip().endswith(self.JUDGMENT[-1]), "the spike fold closes section 8")
        # S9 F4: naming `spike_request` is allowed (section 1 does); the plan shape is what the tool schema owns
        self.assertEqual(activation.skill_spike_number_problems(self.skill()), [])

    def test_the_required_facts_are_pinned_by_exact_string(self):
        self.assertLessEqual(set(self.REQUIRED), set(activation.REQUIRED_EARLY_ANCHORS))
        for anchor in self.REQUIRED:
            self.assertEqual(activation.skill_anchor_problems(self.skill(), anchors=(anchor,)), [], anchor)
        without = tuple(a for a in activation.REQUIRED_EARLY_ANCHORS if a != self.REQUIRED[2])
        self.assertFalse(set(self.REQUIRED) <= set(without), "removing the `stop` anchor must turn this red")

    def test_each_anchor_occurs_exactly_once_and_before_the_invocation(self):
        body = activation.rendered_body(self.skill())
        invocation = body.index(activation.ARGUMENTS_TOKEN)
        for anchor in activation.REQUIRED_EARLY_ANCHORS:
            with self.subTest(anchor=anchor):
                self.assertEqual(body.count(anchor), 1)
                self.assertLessEqual(body.index(anchor) + len(anchor), invocation, "no anchor needs the goal allowance")

    def test_the_whole_skill_fits_the_ratchet_with_the_measured_ratio(self):
        text = self.skill()
        self.assertLessEqual(activation.measured_size(text), 7_300)
        self.assertLessEqual(len(text.splitlines()), 120)


class HostLiteralTests(unittest.TestCase):
    """The skill body names no host version or range: the host profiles and the start refusals own them."""

    def committed(self):
        return (Path(__file__).resolve().parents[2] / activation.SKILL).read_text(encoding="utf-8")

    def problems(self, body):
        return activation.skill_host_literal_problems("---\nname: x\n---\n" + body)

    def test_the_committed_skill_names_no_host_version(self):
        self.assertEqual(activation.skill_host_literal_problems(self.committed()), [])

    def test_a_version_literal_or_range_is_rejected(self):
        for literal in ("qualified on `2.1.296`", "compatible `>=1.0.0,<2.0.0`", "the range `<2.0.0`", "needs >=2.1",
                        "pi-subagents >=0.74.0,<1.0.0", "pi-subagents@0.50.0", "pi-subagents 0.50.0", "`pi-subagents` v0.74",
                        "a pi-subagents version in (0.74, 1.0)",
                        # S8 review F1: a version that ends a sentence, a two-part version near a host name, \u2265, an interval
                        "qualified on Claude Code 2.1.296", "Claude Code 2.1 or later", "Pi 1.0 or later", "Pi \u2265 1.0",
                        "Pi \u2265 1.0.0", "versions \u2264 2.0", "the range [1.0, 2.0)", "the range (1.0.0, 2.0.0]"):
            with self.subTest(literal=literal):
                self.assertEqual(len(self.problems(f"Text. {literal}. More.")), 1, literal)

    def test_a_v_prefixed_version_and_a_host_name_with_inline_code_are_rejected(self):
        for literal in ("qualified on `v2.1.296`", "qualified on v2.1.296", "Pi `1.0` or later", "Claude Code `2.1` or later",
                        "`Pi` 1.0 or later", "`Claude Code` `2.1` or later", "Claude Code 2.1 or later", "Pi 1.0 or later",
                        "`pi-subagents` `0.74` or later", "Pi@1.0"):
            with self.subTest(literal=literal):
                self.assertEqual(len(self.problems(f"Text. {literal}. More.")), 1, literal)

    def test_other_numbers_and_the_runtime_name_are_accepted(self):
        for text in ("`docker-isolated-v1` and the fixed 8/1/2 envelope", "normally 2: an initial audit and one retry",
                     "a 1\u2013600 character rationale", "`empirica.spike-plan-draft/2`", "section 7", "up to 4,096 characters",
                     "an external `pi-subagents` owner", "Pi outside its range is refused", "1. read the view;\n2. submit",
                     "Pi 2 children", "a Claude Code session", "Pi and Claude Code differ", "a [1, 2] list"):
            with self.subTest(text=text):
                self.assertEqual(self.problems(text), [])

    def test_the_frontmatter_is_outside_the_guard(self):
        text = "---\ncompatibility: Claude Code >=2.1.278,<2.2.0\n---\nBody."
        self.assertEqual(activation.skill_host_literal_problems(text), [])


class SpikeNumberTests(unittest.TestCase):
    """The skill body states no spike time limit, resource number or plan schema version: the contract's
    evidence/spike clauses, the spike_request tool description and references/evidence.md own them."""

    def problems(self, body):
        return activation.skill_spike_number_problems("---\nname: x\n---\n" + body)

    def test_the_committed_skill_states_no_spike_number(self):
        text = (Path(__file__).resolve().parents[2] / activation.SKILL).read_text(encoding="utf-8")
        self.assertEqual(activation.skill_spike_number_problems(text), [])

    def test_a_time_limit_a_resource_number_or_the_schema_version_is_rejected(self):
        for literal in ("a 120 s workload", "the 300 seconds margin", "the 120-second workload", "inside the 210-second operation",
                        "Pi's 300-second limit", "1 MiB per stream", "512 MiB tmpfs", "2 GiB memory", "256 PIDs",
                        "uid/gid 65534", "at most 128 environment overrides", "`nofile 1024`", "64 MiB per file",
                        "a closed `empirica.spike-plan-draft/2`", "empirica.spike-plan-draft/3", "a 5 min limit",
                        "at most 4096 files", "2 CPUs", "1 CPU", "`timeout 120`", "timeout   30"):
            with self.subTest(literal=literal):
                self.assertEqual(len(self.problems(f"Text. {literal}. More.")), 1, literal)

    def test_other_numbers_are_accepted(self):
        for text in ("`docker-isolated-v1` and the fixed 8/1/2 envelope", "normally 2: an initial audit and one retry",
                     "a 1\u2013600 character rationale", "up to 4,096 characters", "section 7", "1. read the view;\n2. submit",
                     "a spike plan whose verdict container would fail", "the `spike_request` tool", "Fold 2",
                     "two seconds later", "the first 5,000 tokens", "the files you cite", "a CPU-bound probe", "the timeout is host-owned",
                     "one file per record"):
            with self.subTest(text=text):
                self.assertEqual(self.problems(text), [])

    def test_the_frontmatter_is_outside_the_guard(self):
        self.assertEqual(activation.skill_spike_number_problems("---\ndescription: 120 s\n---\nBody."), [])


class ReferenceLinkTests(unittest.TestCase):
    """The skill's reference links resolve, and no reference is presented as a read before the route."""

    ROOT = Path(__file__).resolve().parents[2]
    REFERENCES = ROOT / activation.SKILL.parent / "references"

    def skill(self):
        return (self.ROOT / activation.SKILL).read_text(encoding="utf-8")

    def test_the_committed_skill_and_references_are_accepted(self):
        self.assertEqual(activation.skill_link_problems(self.skill(), self.REFERENCES), [])
        self.assertEqual(activation.reference_header_problems(self.REFERENCES), [])

    def test_a_broken_link_is_named(self):
        text = self.skill().replace("references/claim-graph.md", "references/no-such-file.md")
        problems = activation.skill_link_problems(text, self.REFERENCES)
        self.assertTrue(any("no-such-file.md" in problem for problem in problems), problems)

    def route_section_with(self, sentence):
        return self.skill().replace("## 6. Invocation, and route before investigating\n",
                                    "## 6. Invocation, and route before investigating\n\n" + sentence + "\n")

    def test_no_reference_link_stands_in_the_route_section(self):
        for sentence in ("Read [the graph rules](references/claim-graph.md) first.",
                         "See [the graph rules](references/claim-graph.md) first.",
                         "Consult [the graph rules](references/claim-graph.md) before routing.",
                         "First open [the governance reference](references/governance.md).",
                         "Do not read [the graph rules](references/claim-graph.md) yet.",
                         "The detail is in [the graph rules](./references/claim-graph.md); it is not a prerequisite read."):
            with self.subTest(sentence=sentence):
                problems = activation.skill_link_problems(self.route_section_with(sentence), self.REFERENCES)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("before investigation is admitted", problems[0])

    def test_the_references_are_linked_after_the_route_section(self):
        text = self.skill()
        start, end = text.index("\n## 6. "), text.index("\n## 7. ")
        self.assertNotIn("](references/", text[start:end])
        self.assertNotIn("](./references/", text[start:end])
        for name in activation.REQUIRED_SKILL_REFERENCES:
            self.assertIn(f"references/{name}", text, name)

    def test_a_punctuated_anchor_is_validated_against_the_headings(self):
        """The whole fragment is read, so punctuation in an anchor cannot hide a mistyped link (a heading's slug has none)."""
        for anchor in ("no.such.heading", "construction.rules", "no/such", "a:b", "x%20y", "no_such_heading!"):
            with self.subTest(anchor=anchor):
                text = self.skill() + f"\n\nSee [rules](references/claim-graph.md#{anchor}).\n"
                problems = activation.skill_link_problems(text, self.REFERENCES)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(f"claim-graph.md#{anchor}", problems[0])

    def test_a_valid_anchor_is_accepted_including_one_to_a_heading_that_has_punctuation(self):
        # "Governed initialization (public contract 6.0.0)": its slug drops the parentheses and the dots
        for target in ("claim-graph.md#construction-rules", "governance.md#governed-initialization-public-contract-600",
                       "governance.md#human-confirmations-are-not-configuration"):
            with self.subTest(target=target):
                text = self.skill() + f"\n\nSee [rules](references/{target}).\n"
                self.assertEqual(activation.skill_link_problems(text, self.REFERENCES), [])

    def test_a_dot_slash_link_to_a_missing_file_is_named(self):
        text = self.skill().replace("## 8. Earn or discard every gating claim",
                                    "## 8. Earn or discard every gating claim\n\nSee [x](./references/missing.md).")
        problems = activation.skill_link_problems(text, self.REFERENCES)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("missing.md", problems[0])

    def test_a_header_that_says_to_read_before_routing_is_named(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            refs = Path(tmp)
            for name in activation.REQUIRED_SKILL_REFERENCES:
                (refs / name).write_text((self.REFERENCES / name).read_text(encoding="utf-8"), encoding="utf-8")
            path = refs / "claim-graph.md"
            path.write_text(path.read_text(encoding="utf-8").replace(
                activation.REFERENCE_LATER_HEADER, "Read this file immediately before seeding or replacing the selected graph."),
                encoding="utf-8")
            problems = activation.reference_header_problems(refs)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("claim-graph.md", problems[0])

    def test_the_three_pre_investigation_references_carry_the_header(self):
        for name in activation.PRE_INVESTIGATION_REFERENCES:
            head = (self.REFERENCES / name).read_text(encoding="utf-8").split("\n\n", 2)
            self.assertEqual(head[1], activation.REFERENCE_LATER_HEADER, name)
        self.assertEqual(activation.REFERENCE_LATER_HEADER,
                         "The rules needed before investigation are in the skill; this reference adds the detail for later.")


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


class DisclosureSourceTests(unittest.TestCase):
    """S8 review F5, F6: both host ranges the frontmatter check requires come from the profiles; the reference says true things."""

    def test_the_claude_range_is_derived_from_its_profile_like_the_pi_range(self):
        document = json.loads(activation.HOST_PROFILES.read_text(encoding="utf-8"))
        claude = activation.claude_profile(document)
        self.assertEqual(activation.host_interval(claude), ">=2.1.278,<2.2.0")
        self.assertIn(activation.host_interval(activation.CLAUDE_PROFILE), activation.REQUIRED_SKILL_DISCLOSURES)
        self.assertIn(activation.pi_interval(activation.PI_PROFILE), activation.REQUIRED_SKILL_DISCLOSURES)
        moved = json.loads(json.dumps(claude))
        moved["compatibility"] = {"minimum": "2.3.0", "maximum_exclusive": "2.4.0"}
        self.assertEqual(activation.host_interval(moved), ">=2.3.0,<2.4.0")
        source = Path(activation.__file__).read_text(encoding="utf-8")
        self.assertNotIn('">=2.1.278,<2.2.0"', source, "no retyped Claude range in the validator")

    def test_exactly_one_claude_profile_is_required(self):
        document = json.loads(activation.HOST_PROFILES.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            activation.claude_profile({"profiles": [p for p in document["profiles"] if p["host_id"] != "claude-code"]})

    def test_the_host_capabilities_reference_names_the_refusals_that_exist(self):
        text = (Path(__file__).resolve().parents[2] / activation.SKILL.parent / "references/host-capabilities.md").read_text(
            encoding="utf-8")
        self.assertIn("The registered host profile defines the required surfaces, and Pi's start refusals and the "
                      "audit-time `host.*` reasons name what is missing.", " ".join(text.split()))
        self.assertNotIn("each refusal reason names", " ".join(text.split()))


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
