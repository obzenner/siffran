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

    def test_the_closing_frontmatter_delimiter_may_have_trailing_whitespace(self):
        # The same rule as scripts/validate_skills.py: a delimiter line is `line.strip() == "---"`.
        text = "---\nname: x\n--- \nbody ANCHOR `$ARGUMENTS`"
        self.assertEqual(activation.rendered_body(text), "body ANCHOR `$ARGUMENTS`")
        self.assertEqual(activation.skill_anchor_problems(text, ["ANCHOR"]), [])
        self.assertEqual(activation.rendered_body("--- \nname: x\n---\nbody"), "body")

    def test_the_ceiling_may_only_be_lowered_and_stays_within_headroom_of_the_skill(self):
        # The bound is the CURRENT ceiling: a PR that lowers SKILL_MAX_CHARS lowers this number in the same edit, so a later
        # raise needs a visible edit here. The cut is a rendered-window size, independent of the file size.
        self.assertLessEqual(activation.SKILL_MAX_CHARS, 17_600)
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
    """S3: the reorder moved lines and changed none; the anchors are unique and before the goal substitution."""

    # sha256 of the sorted paragraphs (blocks between blank lines, lines right-stripped, headings left out) of SKILL.md as it
    # was before the reorder (2020801f). A line moved from one paragraph to another changes two paragraphs and is caught.
    PARAGRAPHS_DIGEST = "aab1b2c44ea4b244e86386286f7766c60c7b0ab0b4ec5b8c764cf03b59e50105"
    # The only paragraphs the reorder added: a lead-in per moved block whose cross-reference would otherwise dangle, and
    # the pointer that tells a first read where to start.
    LEAD_INS = ("Claim discipline; the two folds named here are defined in section 8 below.",
                "At invocation, begin with section 6 (route, graph, configuration, investigate); "
                "sections 2\u20135 apply once investigation is admitted.")
    # The one allowed paragraph split: this original paragraph is now two (the read-before-route rule moved up as its own
    # paragraph; the host-surface sentence stayed with the matrix). The digest sees the original paragraph.
    SPLIT = ("Identify the exact active host surface from the tools and lifecycle already present in context.\n"
             "**Do not read any file, including a skill reference, before the `investigate` witness is admitted\n"
             "(route \u2192 graph \u2192 approved configuration).** Use this inline bootstrap matrix:",
             "Identify the exact active host surface from the tools and lifecycle already present in context.\n"
             "Use this inline bootstrap matrix:",
             "**Do not read any file, including a skill reference, before the `investigate` witness is admitted\n"
             "(route \u2192 graph \u2192 approved configuration).**")
    # Wording changed on purpose, each pinned by its exact new text and mapped back to the original it replaced: the digest
    # is computed on the original (2020801f), so every other paragraph stays proven unchanged. Each states a rule once.
    REWRITES = (("Once confirmations are done (when evidence waits on a human, `report_convergence` presents it)",
                 "Once confirmations are done (report first; the core refuses an audit while one is pending)"),
                ("Call the host's guarded convergence operation when the graph, evidence, and\n"
                 "scope are current. On Claude and Pi this is `report_convergence`: when evidence\n"
                 "waits on a human, `report_convergence` presents it; after the bound audit its\n"
                 "`Allow` is the terminal decision.",
                 "Call the host's guarded convergence operation exactly once after the graph,\n"
                 "evidence, and scope are current. On Claude and Pi this is `report_convergence`\n"
                 "after the bound audit."),
                ("   After dismissal, timeout, or rejection, wait for the human; Claude can settle the turn nonterminally while\n"
                 "   investigation and convergence remain blocked. When they say how to go on, take the view's `next:`\n"
                 "   (`configure_run` again, with the same or human-edited ceilings); record `investigate`\n"
                 "   only once the view says `approved`.",
                 "   After cancellation/timeout, wait for the human; Claude can settle the turn nonterminally while\n"
                 "   investigation and convergence remain blocked. After dismissal, timeout, or rejection, resubmit\n"
                 "   `configure_run` (the same or human-edited ceilings) to reopen the dialog; record `investigate`\n"
                 "   only once the view says `approved`."))
    # The judgment an agent needs before investigation is inline (the references cannot be read until the `investigate`
    # witness is admitted): each wording change below, and each paragraph added in INLINED, is pinned by exact text. The
    # inlined rules are used at invocation, when the whole skill is loaded, so they need not be early anchors.
    REWRITES += (
                ('uncertainty. Write it by the rules in section 7.\n   Take exact action',
                 'uncertainty. Larger graphs must be\n   root-connected DAGs. Take exact action'),
                ('Include a rationale explaining that sizing.',
                 'Include a 1–600 character nonblank rationale explaining that sizing.'),
                ('control mode. Preserve',
                 'control mode. The goal is displayed read-only; the claim graph and reviewer are not approvable. Preserve'),
                ('envelope. Investigation children draw on `max_spawns` and the auditor on `max_audit_spawns`; neither borrows\n   from the other, so size each for its own work. A pass is charged only when the derivation changes.\n   Timeout is dismissal',
                 'envelope.\n   The host-owned timeout and presentation limits are fixed by the contract and displayed read-only.\n   Timeout is dismissal'),
                ('Construct the smallest closed graph whose gating root is the goal and whose gating claims cover every material\nunknown. Write the first graph before approval, from the supplied context, by these rules:',
                 'After approval and investigation admission, read\n[references/claim-graph.md](references/claim-graph.md). Construct the smallest\nclosed graph whose root is the goal and whose gating claims cover every material\nunknown.'),
                ('Submit the graph through the active author-action surface. Once investigation is admitted,\n[references/claim-graph.md](references/claim-graph.md) adds the detail for amending it.',
                 "Submit the graph through the active author-action surface. Never persist a claim's\nstate or confidence: claim state is derived from current evidence on every read.\nA malformed, detached, missing, or corrupt selected graph fails closed."),
                (' and graph shapes from the `empirica_observe`\n   tool input schema, never from contract reads or guesses.',
                 ' and graph shapes from the `empirica_observe`\n   tool input schema, which carries every closed action shape (including the graph\n   payload); do not guess field names or nesting. Contract reads are optional\n   explanation, never the source of a shape.'),
                ('is unsupported;\ndo not continue with an unrecorded substitute.',
                 'is unsupported. The core and\nsupported host adapters fail closed before investigative tools, evidence, child\nbudget, or harness execution; do not continue with an unrecorded substitute.'),
                ('   - absent or stale evidence leaves the claim open.',
                 '   - absent or stale evidence leaves the claim open;\n   - `needs-decision` is answered by the human at the end of the pass (approve or reject); until\n     then it is a human residual.'),
                ('except a `needs-decision` claim**. Record',
                 "except a `needs-decision` claim** (human-owned: it needs the human's answer, not research). Record"),
                ("Stop before investigation and report the exact unsupported capability when one of the three public tools or the auditor launch is absent, when the start refuses the host, or when the host is unknown; never borrow another host's capabilities, imitate a missing operation in prose, or write runtime artifacts by hand. After admission, [references/host-capabilities.md](references/host-capabilities.md) helps diagnose a capability failure.",
                 'If any capability is absent — including a Pi surface without one verifiable external `pi-subagents` owner, or any host missing one of the three public tools — stop before\ninvestigation and report the exact unsupported capability. Do not imitate the missing operation\nin prose or write runtime artifacts by hand.'),
                ('Do not loop on refusal or exhaustion.\n6. Wait',
                 'Do not loop on refusal or exhaustion. The optional\n   [governance reference](references/governance.md) documents host-owned recovery details; it is\n   not a prerequisite read.\n6. Wait'),
                ('("What still needs confirming"). A summary',
                 '("What still needs confirming"); a `needs-decision`\n   claim is human-owned and needs no research. A summary'),
                ('through `configure_run`\n([references/governance.md](references/governance.md) has the host-owned recovery details). Freeze',
                 'through `configure_run`. Freeze'),
                )
    INLINED = ("- One gating claim per material unknown or invariant; scope you want to defer goes in non-gating claims below the root.\n- `needs-experiment` only when a deterministic spike plan can falsify the claim.\n- `needs-decision` only for an irreducible human choice: the human answers it when you report, it needs no research,\n  and where nobody can be asked it stays open and the run can only stop as a residual.\n- Every other claim is `ordinary`.\n- Rewording a claim stales its evidence and audit coverage, so keep claims stable once evidence binds; state no verdict or confidence in a claim's text.",)
    HOST_SENTENCE = "Stop before investigation and report the exact unsupported capability when one of the three public tools or the auditor launch is absent, when the start refuses the host, or when the host is unknown; never borrow another host's capabilities, imitate a missing operation in prose, or write runtime artifacts by hand. After admission, [references/host-capabilities.md](references/host-capabilities.md) helps diagnose a capability failure."
    INLINED_SECTION_1 = ("Do delegated work through Empirica's children: work by an external agent CLI is neither budgeted nor attributed to a producer.",)
    REMOVED = ('External actor CLIs are not metered or attributed (ADR 0061).',
               # S8: the host matrix, deleted; host compatibility comes from the host profiles and the start refusals
               *('Identify the exact active host surface from the tools and lifecycle already present in context.\nUse this inline bootstrap matrix:', "- **Claude Code capability profile (qualified on `2.1.296`, compatible\n  `>=2.1.278,<2.2.0`, documented and not checked: hook payloads carry no version):** continue when the Empirica hooks and\n  public MCP tools are active and trusted; record route first. Canonical audits\n  are host-owned async children; a current pending audit settles the parent turn\n  until Claude's native completion notification resumes it.\n- **Pi capability profile (qualified on `1.1.0`, compatible\n  `>=1.0.0,<2.0.0`, with external `pi-subagents` installed by the user):** continue when `/empirica`\n  injected an opaque handle, `empirica_observe`, `empirica_read`, and `report_convergence` are present,\n  and `/empirica` accepted the host (a Pi outside its range is refused at start) and exactly one active `subagent` owner (its canonical package root, exact\n  version, and that package's own launch preflight). A `pi-subagents` version outside the supported range fails closed\n  (`>=0.74.0,<1.0.0`; the range and the reviewed versions are listed in `doc/hosts.md`); one inside it with no compat record is admitted\n  and shown as not reviewed. Any other state is a `host.subagents_*`\n  refusal.\n- **Non-interactive Pi or Claude (print, JSON, SDK):** no human can approve a configuration or confirm evidence, so a\n  deliberative run is refused. Auto runs only with the operator's `EMPIRICA_AUTO_DELEGATION=1`; without it the start is\n  refused with the reason on stderr and exit code 1.\n- **Codex:** Empirica is not available on Codex.\n- **Unknown, partial, or outside a qualified compatibility range:** stop as unsupported pending\n  qualification rather than borrowing another profile's capabilities. After admission, consult\n[references/host-capabilities.md](references/host-capabilities.md) only to diagnose a capability failure.", 'A runnable convergence workflow requires all of these capabilities:', '1. read the complete public run view, including obligations;\n2. submit author actions for route, graph, research, spike request, and freeze;\n3. obtain the current audit argument through the host-bound workflow;\n4. launch and observe a bound independent auditor;\n5. submit the observed verdict through private host ingress;\n6. request the guarded convergence decision;\n7. obtain exact run-configuration approval through supported host UI (or explicit bounded auto).', "The public RunView separately reports this run's immutable `spike_backend`. `spikes_unavailable` does\nnot disable graph refinement, research, honest residual stopping, reads, or audit of claims that need\nno experiment. Do not submit an unchanged spike request in that state and never fabricate or replace\nspike evidence with ambient host execution. A `qualified` `docker-isolated-v1` or\n`docker-networked-v1` value means this run is bound to the two published digests; it is not a\nclaim that Docker is currently healthy."))
    # The facts a first read and a compacted run must keep in the first screenful, pinned by exact string. A later change
    # may add anchors or reword one only by editing this list, visibly.
    REQUIRED = ("Claude launches `empirica:empirica-auditor`, Pi calls the",
                "`Allow(converged=true)` permits a convergence claim.",
                "call it once with `intent: \"stop\"`",
                "**faulted** \u2014 state, artifacts, transport, or protocol failed closed.",
                "**Do not read any file, including a skill reference, before the",
                "**Experiments go through Empirica only.**",
                "are trusted host ingress")

    def skill(self):
        return (Path(__file__).resolve().parents[2] / activation.SKILL).read_text(encoding="utf-8")

    @staticmethod
    def paragraphs(text):
        blocks = (["\n".join(line.rstrip() for line in block.splitlines() if line.strip())]
                  for block in re.split(r"\n\s*\n", text))
        return [block for (block,) in blocks if block and not (block.startswith("#") and "\n" not in block)]

    def digest(self, text, lead_ins=LEAD_INS):
        import hashlib
        for new, original in self.REWRITES:
            self.assertEqual(text.count(new), 1, new)
            text = text.replace(new, original)
        for added in (*self.INLINED, *self.INLINED_SECTION_1):
            self.assertEqual(text.count(added), 1, added)
            text = text.replace(added + "\n\n", "", 1)
        paragraphs = self.paragraphs(text)
        paragraphs += self.REMOVED
        for lead in lead_ins:
            self.assertEqual(paragraphs.count(lead), 1, lead)
            paragraphs.remove(lead)
        whole, first, second = self.SPLIT
        self.assertEqual((paragraphs.count(first), paragraphs.count(second)), (1, 1), "the split paragraphs")
        paragraphs.remove(first)
        paragraphs.remove(second)
        paragraphs.append(whole)
        return hashlib.sha256("\n\n".join(sorted(paragraphs)).encode()).hexdigest()

    def test_every_paragraph_is_preserved_and_none_added(self):
        text = self.skill()
        self.assertEqual(self.digest(text), self.PARAGRAPHS_DIGEST)
        lines = text.splitlines()
        gone = next(i for i, line in enumerate(lines) if line.startswith("Treat training-weight"))
        deleted = "\n".join(lines[:gone] + lines[gone + 1:])
        self.assertNotEqual(self.digest(deleted), self.PARAGRAPHS_DIGEST, "a deleted line is caught")
        self.assertNotEqual(self.digest(text + "\n\nOne more sentence.\n"), self.PARAGRAPHS_DIGEST, "an added paragraph is caught")
        self.assertNotEqual(self.digest(text.replace("hypothesis", "guess", 1)), self.PARAGRAPHS_DIGEST, "a rewritten line is caught")

    def test_the_pre_investigation_judgment_is_inline_in_sections_6_and_7(self):
        text = self.skill()
        start, end = text.index("\n## 6. "), text.index("\n## 8. ")
        for rule in (*self.INLINED, "Write it by the rules in section 7.",
                     "size each for its own work."):
            self.assertIn(rule, text[start:end], rule)
        for fact in ("`needs-experiment` only when a deterministic spike plan can falsify the claim.",
                     "`needs-decision` only for an irreducible human choice",
                     "One gating claim per material unknown or invariant",
                     "Every other claim is `ordinary`.",
                     "Investigation children draw on `max_spawns` and the auditor on `max_audit_spawns`"):
            self.assertEqual(text.count(fact), 1, fact)

    def test_the_review_fixes_are_pinned_by_exact_text(self):
        text = self.skill()
        s2, s6, s8 = text.index("\n## 2. "), text.index("\n## 6. "), text.index("\n## 8. ")
        external = ("Do delegated work through Empirica's children: work by an external agent CLI is neither budgeted "
                    "nor attributed to a producer.")
        self.assertEqual(text.count(external), 1)
        self.assertLess(text.index(external), s2, "the external-CLI fact is in section 1")
        pass_charge = "A pass is charged only when the derivation changes."
        no_verdict = "state no verdict or confidence in a claim's text"
        for fact in (pass_charge, no_verdict):
            self.assertEqual(text.count(fact), 1, fact)
            self.assertTrue(s6 < text.index(fact) < s8, fact)
        # the needs-decision rule has one carrier (section 7); section 8 only excepts the claim from research
        self.assertEqual(text.count("is answered by the human"), 0)
        self.assertEqual(text.count("human residual"), 0)
        self.assertEqual(text.count("the human answers it when you report"), 1)

    def test_section_9_keeps_only_the_judgment_nothing_enforces(self):
        text = self.skill()
        section = text[text.index("\n## 9. "):]
        self.assertEqual(text.count(self.HOST_SENTENCE), 1)
        self.assertEqual(section.split("\n\n", 1)[1].strip(), self.HOST_SENTENCE, "section 9 is that one paragraph")
        self.assertLess(len(section.strip()), 600)
        # each judgment clause pinned by its exact words
        for clause in ("Stop before investigation and report the exact unsupported capability when one of the three public "
                       "tools or the auditor launch is absent",
                       "when the start refuses the host, or when the host is unknown",
                       "never borrow another host's capabilities, imitate a missing operation in prose, "
                       "or write runtime artifacts by hand",
                       "[references/host-capabilities.md](references/host-capabilities.md) helps diagnose a capability failure"):
            self.assertIn(clause, self.HOST_SENTENCE)
        for gone in ("capability profile", "qualified on", "inline bootstrap matrix", "not available on Codex",
                     "EMPIRICA_AUTO_DELEGATION", "A runnable convergence workflow", "`spikes_unavailable` does",
                     "qualified range", "a needed operation"):
            self.assertNotIn(gone, text)

    def test_the_third_needs_decision_restatement_and_the_optional_link_sentence_are_gone(self):
        text = self.skill()
        self.assertNotIn("is human-owned and needs no research", text)
        self.assertEqual(text.count("needs no research"), 1, "section 7 carries the rule")
        self.assertNotIn("not a prerequisite read", text)
        self.assertNotIn("The optional", text)
        self.assertEqual(text.count("](references/governance.md)"), 1)
        self.assertGreater(text.index("](references/governance.md)"), text.index("\n## 7. "), "linked after section 6")

    def test_the_terminal_call_is_not_called_the_only_call(self):
        text = self.skill()
        self.assertNotIn("exactly once", text)
        self.assertNotIn("report first", text)
        self.assertNotIn("to reopen the dialog", text)

    def test_a_line_moved_between_paragraphs_is_caught(self):
        lines = self.skill().splitlines()
        first = next(i for i, line in enumerate(lines) if line.startswith("**Experiments go through Empirica only.**"))
        end = first + next(i for i, line in enumerate(lines[first:]) if not line.strip())
        # the last line of the experiments paragraph becomes the second line of the paragraph before it
        moved = lines[:first - 1] + [lines[end - 1], ""] + lines[first:end - 1] + lines[end:]
        self.assertEqual(sorted(line for line in moved if line.strip()), sorted(line for line in lines if line.strip()),
                         "the same lines, so a sorted-line digest would pass")
        self.assertNotEqual(self.digest("\n".join(moved)), self.PARAGRAPHS_DIGEST)

    def test_the_pointer_names_the_section_that_starts_the_run(self):
        text = self.skill()
        self.assertIn("\n## 6. Invocation, and route before investigating\n", text)
        for number in "2345":
            self.assertRegex(text, rf"\n## {number}\. ")

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
                       "governance.md#timeout-and-recovery"):
            with self.subTest(target=target):
                text = self.skill() + f"\n\nSee [rules](references/{target}).\n"
                self.assertEqual(activation.skill_link_problems(text, self.REFERENCES), [])

    def test_a_dot_slash_link_to_a_missing_file_is_named(self):
        text = self.skill().replace("## 9. Preflight the host",
                                    "## 9. Preflight the host\n\nSee [x](./references/missing.md).")
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
