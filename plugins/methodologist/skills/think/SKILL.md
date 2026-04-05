---
name: think
description: "Select and execute a formal reasoning methodology for the current task. Use when facing architectural decisions, debugging, rule enforcement, design tradeoffs, assumption validation, or any situation requiring structured thinking. Trigger phrases: 'think through this', 'reason about', 'which approach', 'analyze this decision', 'first principles', 'what are the assumptions', 'prove this', 'why does this break'. Invoke as /think for auto-detection or /think <methodology-name> to use a specific one."
argument-hint: "[methodology-name]"
allowed-tools: [Read, Glob, Grep, Bash, Agent, TaskCreate, TaskUpdate]
---

# Methodologist — Formal Reasoning Router

You are executing a structured reasoning methodology. You are NOT freestyling. Every phase is tracked, every output is structured, every conclusion is traced to its premises.

## Step 0: Parse invocation

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
<Anything unresolved, tagged: [needs-data], [needs-decision], [needs-experiment]>
```

## Registry

The single source of truth for available methodologies is `registry.json` (next to this file). It is validated by `validate.py` against the actual files in `methodologies/`. Do not hardcode methodology names or descriptions in this skill — always read from the registry.
