---
name: think
description: "Select and execute a formal reasoning methodology for the current task. Use when facing architectural decisions, debugging, rule enforcement, design tradeoffs, assumption validation, or any situation requiring structured thinking. Trigger phrases: 'think through this', 'reason about', 'which approach', 'analyze this decision', 'first principles', 'what are the assumptions', 'prove this', 'why does this break'. Invoke as /think for auto-detection or /think <methodology-name> to use a specific one."
argument-hint: "[methodology-name]"
allowed-tools: [Read, Glob, Grep, Bash, Agent, TaskCreate, TaskUpdate]
---

# Methodologist — Formal Reasoning Router

You are executing a structured reasoning methodology. You are NOT freestyling. Every phase is tracked, every output is structured, every conclusion is traced to its premises.

## Step 0: Adopt the stance, then parse invocation

**First, before anything else**, read `references/evidence-over-recall.md` (next to this file) and emit its stance declaration line verbatim. This is the shared spine of every methodology — parametric knowledge is a hypothesis, every step emits a fabrication-resistant artifact, open questions are resolved before they are surfaced. If that line is absent from your output, you have not run the methodology.

The user invoked: `$ARGUMENTS`

**If a methodology name was provided** (e.g., `/think formal-reasoning`):
- Read the methodology file from `methodologies/<name>.md` relative to this skill
- Skip to Step 2

**If no methodology was provided** (just `/think`):
- Proceed to Step 1

## Step 1: Select methodology

Read `registry.json` (located next to this SKILL.md). This file contains every available methodology with its `name`, `use_when` trigger description, `lineage`, and what it `prevents`.

**Do NOT read any methodology .md files yet.** The registry has everything you need to select.

Analyze the user's current task context — recent conversation, open files, the task at hand. Match against the `use_when` field of each registry entry.

**Selection rules:**
1. If the task clearly matches one methodology, use it
2. If it could match multiple, pick the one that addresses the PRIMARY uncertainty
3. If genuinely ambiguous, state the top 2 candidates with one-line rationale each and ask the user to pick

Announce your selection: `Using **<methodology-name>**: <one-line reason>`

**Host bridge:** If a `methodologist_select` tool is available, do not open the
methodology file yourself yet. Call that tool with the exact registry `name` and
your one-line semantic reason. If the choice is genuinely ambiguous, call it
with exactly the top two `{name, rationale}` candidates so the host can present
the human choice UI. The bridge validates the named methodology through
`methodologist/v1`, returns the canonical six-phase plan, and renders host-native
phase tracking. Continue below using that returned plan. This is the same named
bridge used by explicit `/think <name>`; never replace it with keyword routing.

Then — and ONLY then — read the methodology file from `methodologies/<name>.md` relative to this skill.

## Step 2: Create phase tasks

Every methodology file defines numbered phases. After reading the methodology:

1. Create one task per phase using TaskCreate, prefixed with the methodology name
2. Set the first task to `in_progress`
3. Announce the phase plan to the user in a compact list

Example:
```
Phases for invariant-analysis:
1. [ ] Identify operation and scope
2. [ ] State preconditions
3. [ ] State postconditions
4. [ ] Identify invariants
5. [ ] Verify or find violation
6. [ ] Produce traced conclusion
```

## Step 3: Execute phases sequentially

For each phase:

1. Read the methodology's instructions for that phase
2. Do the work — read code, analyze, reason, search
3. Produce the phase output in the format the methodology specifies
4. Mark the task complete via TaskUpdate
5. Move to the next phase

**Rules during execution:**
- Do NOT skip phases. If a phase seems unnecessary, say why and still produce minimal output for it.
- Do NOT merge phases. Each gets its own output block.
- If a phase reveals that the methodology selection was wrong, STOP. Say so. Suggest the correct one. Ask the user.
- If you need information you don't have, say what you need and ask — don't fabricate.

## Step 4: Produce final artifact

After all phases complete, produce a structured summary:

```
## Methodology: <name>
## Context: <what was being analyzed>

### Reasoning trace
<One paragraph per phase — what was found, what it implies>

### Conclusion
<The decision/finding, with explicit references to which phase produced the supporting evidence>

### Confidence
<high | medium | low> — <why>

### Open questions
<Per the residual protocol in `references/evidence-over-recall.md` (§3): surface candidates, resolve each against evidence, then list ONLY the blocked residual — each tagged [needs-data | needs-decision | needs-experiment] with what you already tried. The honest default is "None.">
```

**Open-questions gate (apply before writing that section).** Apply §3 of `references/evidence-over-recall.md`: a question is a worklist item to be resolved, not a section to be filled. Attempt resolution first — read the code, search the docs, reason it through, run a command. A question survives to the artifact only if blocked on one of the three tags, and must state what you tried. The honest default is "None."

## Registry

The single source of truth for available methodologies is `registry.json` (next to this file). It is validated by `validate.py` against the actual files in `methodologies/`. Do not hardcode methodology names or descriptions in this skill — always read from the registry.
