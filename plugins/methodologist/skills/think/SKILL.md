---
name: think
description: "Select and execute a formal reasoning methodology for the current task. Explicit bare /think or $think invocations present the complete methodology catalog and wait for the user to choose; an explicit methodology name is the expert shortcut, while implicit activation may select semantically. Use for architectural decisions, debugging, rule enforcement, design tradeoffs, assumption validation, or structured reasoning. In Methodologist Pi, use /think; --simple is the non-interactive automation path."
argument-hint: "[methodology-name | --simple <intent>]"
allowed-tools: [Read, Glob, Grep, Bash, Agent, TaskCreate, TaskUpdate]
---

# Methodologist — Formal Reasoning Router

You are executing a structured reasoning methodology. You are NOT freestyling. Every phase is tracked, every output is structured, every conclusion is traced to its premises.

## Step 0: Adopt the stance, then parse invocation

**First, before anything else**, read `references/evidence-over-recall.md` (next to this file) and emit its stance declaration line verbatim. This is the shared spine of every methodology — parametric knowledge is a hypothesis, every step emits a fabrication-resistant artifact, open questions are resolved before they are surfaced. If that line is absent from your output, you have not run the methodology.

Host invocation arguments, when the host expands them: `$ARGUMENTS`

Choose the host mode before routing:

- **Native simple mode:** Codex activates this skill implicitly in native simple
  mode; Methodologist Pi also uses it for the explicit `--simple <intent>`
  automation path. This is a direct, stateless execution path: semantically select from the registry,
  announce the choice, and execute it without bridge/UI/workflow state. Do not
  invoke a slash command, `methodologist_select`, HumanPort, a widget, or
  persisted task state. The kickoff/current request is already the sole user
  prompt, so do not recursively dispatch Methodologist again.
- **Interactive invocation mode:** use this when the user explicitly invokes
  `/think`, `$think`, or the `think` skill without naming a methodology. Read the
  registry, show the complete catalog, and ask the user to choose. Do not select
  on the user's behalf, read a methodology file, call the bridge, or execute a
  phase until the user replies. Host-native selection UI is preferred when the
  host provides it; otherwise render the catalog in conversation.
- **Structured bridge mode:** enter this mode only after a methodology has been
  chosen, when the user or host kickoff explicitly requests it and a
  `methodologist_select` tool is actually available. The bridge validates the
  chosen registry name and canonical phase plan; it does not choose or execute
  methodology semantics. If the tool is unavailable, use native execution and
  never claim bridge-backed validation.

**If a methodology name was provided** in the host arguments or current request
(e.g., `/think formal-reasoning` in a host that exposes that command):
- Read the methodology file from `methodologies/<name>.md` relative to this skill
- Skip to Step 2

**If no methodology was provided:**
- For an explicit invocation, proceed to the catalog-first interaction in Step 1
  and stop after asking the user to choose.
- For implicit native activation, proceed to Step 1's semantic-selection path.

## Step 1: Present the catalog or select for implicit automation

Read `registry.json` (located next to this SKILL.md). It is the single source of
truth for methodology names and descriptions. **Do not read any methodology
`.md` file before a methodology has been chosen.**

### Explicit invocation without a name: catalog first

Present every registry entry, in registry order, with:

- exact `name`;
- one-line `use_when` guidance;
- the failure mode from `prevents`.

Then ask: `Which methodology should I run?` and stop the turn. Do not recommend,
preselect, narrow to two candidates, call `methodologist_select`, or start phase
work. The user's reply supplies the explicit methodology name; validate it
against the registry, then continue to Step 2. If the host provides a native
picker, use the same complete registry catalog in that picker.

An explicit `/think <methodology-name>` remains an expert shortcut and skips the
catalog. An unknown name must fail closed and show the available names.

### Implicit or explicit automation mode only

When the skill was activated implicitly by task semantics, or the user chose the
explicit `--simple` automation path, semantically compare the current task
against every `use_when` entry:

1. If one methodology clearly addresses the primary uncertainty, select it.
2. If genuinely ambiguous, present the complete catalog and ask the user rather
   than silently choosing.

Announce an automatic selection as:
`Using **<methodology-name>**: <one-line reason>`

**Structured bridge mode only, after selection:** call `methodologist_select`
with the exact chosen registry name and one-line reason. The bridge validates
that name and returns the canonical phase plan. It never chooses for the user.

Then—and only then—read `methodologies/<name>.md` relative to this skill.

## Step 2: Create phase tasks

Every methodology file defines numbered phases. After reading the methodology:

1. In structured bridge mode, if the host actually provides TaskCreate and
   TaskUpdate, create one task per phase, prefixed with the methodology name, and
   set the first task to `in_progress`. If those capabilities are absent (as in
   Codex), use the validated phase plan without claiming host-native tracking. In
   native simple mode, create no task or workflow state.
2. Announce the phase plan to the user in a compact list.

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
4. In structured bridge mode with TaskUpdate available, mark the task complete
5. In structured bridge mode with TaskUpdate available, move the next task to
   `in_progress`; otherwise continue directly without state writes

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
