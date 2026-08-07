---
number: 23
title: "Harness-aware model and agent routing by claim kind"
status: superseded
date: 2026-07-24
tags:
  - architecture
  - harness
  - routing
  - cost
links:
  - target: 24
    kind: Superseded by
  - target: 21
    kind: Depends on
  - target: 20
    kind: Depends on
  - target: 12
    kind: relatesto
  - target: 17
    kind: relatesto
  - target: 13
    kind: relatesto
---

# Harness-aware model and agent routing by claim kind

## Context and Problem Statement

empirica runs on more than one harness (Claude Code, PI — ADR-21) and adjudicates claims of
different kinds (`needs-data`, `needs-experiment`, `needs-decision`, plus the audit pass —
ADR-20). Today it treats every claim and every harness the same: one model, whatever the
session started with, for research, spikes, and audit alike. That is both wasteful and weaker
than the harness allows:

* A `needs-data` fetch-and-cite is a cheap, mechanical task; running it on a frontier reasoning
  model burns budget for no gain.
* A `needs-experiment` spike design and a high-stakes decision need a capable model.
* The **audit** (ADR-20 P6) is strongest when performed by a *different* principal — ideally a
  different model tier — so it is not the same weights grading their own reasoning.
* Each harness exposes model/agent selection empirica does not use. Verified 2026-07-24: PI
  (`pi --list-models`) offers a full tier ladder on `amazon-bedrock` (nova-micro / haiku-4.5 →
  sonnet-5 → opus-4.8 → fable-5) and its subagent extension takes a per-agent `--model`; Claude
  Code selects a subagent's model via agent-definition `model:` frontmatter and routes
  reasoning depth through the `/think` methodology router (ADR-12).

empirica should **understand which harness it is on and use that harness's models and agents
intelligently per claim** — not run everything on one undifferentiated model.

## Decision Drivers

* Cost: cheap claims on cheap models, expensive reasoning only where it pays (ADR-17's spirit —
  spend where it earns; moai-adk tiers its agents by cost for the same reason).
* Audit independence: the auditor should differ from the author in principal and preferably in
  model, or "independent audit" is weaker than claimed (ADR-20 P6; self-preferential bias,
  ADR-13).
* Capability match: route the hard claims (`needs-experiment` design, high-stakes decisions) to
  capable models; route mechanical claims (`needs-data`) to fast ones.
* Harness honesty: routing must use each harness's *real* selection surface, and degrade
  explicitly where a harness offers none — never pretend a capability exists (the ADR-21 rule).
* No hard-coded model IDs in the workflow: models change; the policy is by **tier and role**,
  resolved to concrete IDs by harness config, not baked into the skill.

## Considered Options

* **One model for everything (status quo).** Rejected: wasteful on cheap claims, no audit
  independence, ignores the harness's ladder.
* **Fixed model per claim kind, hard-coded.** Rejected: model IDs churn; hard-coding them in
  the skill is the stale-cache failure. Also ignores harness differences.
* **Tier/role routing policy, resolved per harness (chosen).** The workflow declares a claim's
  required *tier* (fast | capable | frontier) and *role* (author | spike-runner | auditor); an
  adapter per harness maps tier+role to a concrete model/agent using that harness's selection
  surface. Unmapped tiers degrade to the session default with a logged note.

## Decision Outcome

Chosen: **routing by (claim-kind → tier + role), resolved to concrete models by a per-harness
adapter.** empirica never names a model in its workflow logic; it names a tier and a role, and
the adapter for the active harness binds it.

**Routing policy (harness-independent):**

| Claim kind / step | Tier | Role | Rationale |
|---|---|---|---|
| `needs-data` (Fold-1 fetch + cite) | fast | author | mechanical retrieval; cheap model suffices |
| `needs-experiment` (spike design + Fold-2) | capable | spike-runner | designing a real check + reading its verdict |
| `needs-decision` | — | human | not model-resolvable; surfaced (ADR-20) |
| Assessor pass / claim derivation | capable | author | specialize-only derivation is reasoning |
| `/think` escalation (stall / high-stakes) | frontier | author | the expensive tier, by trigger (ADR-12/13) |
| **independent audit (P6)** | capable+ | **auditor** | **must differ in principal from the author, and SHOULD differ in model tier** so it is not the author's weights re-grading themselves |

**Per-harness adapter (uses each harness's verified selection surface):**

* **Claude Code.** Role → subagent with a `model:` set in its agent definition; the auditor is a
  spawned subagent (distinct principal, ADR-20 P6) whose definition pins a capable tier. Reasoning
  depth for the author routes through `/think` (ADR-12). Spawns are charged to the spawn budget
  (ADR-17). Tier→model IDs live in the plugin's agent definitions, not the skill prose.
* **PI.** Role → a subagent invoked via the subagent extension with `--model` (verified: the
  extension passes a per-agent model; the catalog spans nova-micro → opus-4.8 → fable-5). The
  orchestrator (ADR-21) picks the tier per claim and passes the concrete model. The auditor is a
  separate `pi` process on a capable-tier model.
* **Any harness with no model selection** (e.g. a bare interactive session): routing degrades to
  the session's single model, logged as "tier routing unavailable on this harness" — the ADR-21
  no-overclaim rule. Audit independence then rests on *principal* separation alone (a distinct
  spawn/process), not model difference, and the docs say so.

**Tier is abstract; IDs are config.** "fast / capable / frontier" resolve to concrete models in
harness config (Claude Code agent definitions; PI's `models.json` / orchestrator config), so a
model rename or a new tier never touches empirica's workflow logic. This is the ADR-15
"reference, don't vendor" principle applied to models: name the role, resolve the identity
outside the logic.

### Consequences

* Good, because cost tracks value: mechanical claims run fast/cheap, reasoning-heavy claims run
  capable, the frontier tier is reserved for `/think` by trigger (ADR-12/13).
* Good, because the audit gains real independence — a different principal on a different model
  tier — strengthening ADR-20 P6 beyond "a second call to the same weights."
* Good, because empirica finally uses each harness's actual capability (PI's model ladder,
  Claude Code's subagent model + `/think`) instead of one undifferentiated model.
* Good, because tier-not-ID routing keeps model churn out of the workflow logic (no stale
  hard-coded model names).
* Bad, because it adds a per-harness routing adapter to build and maintain, and a policy table
  that must be tuned (wrong tiering wastes budget or under-powers a claim).
* Bad, because on a harness with no model selection the cost/independence benefits collapse to
  principal-separation only — a real limitation, documented not hidden.
* Bad, because "capable+" for the auditor is a judgment the operator may need to override per
  domain (a security audit may want frontier); the policy is a default, not a law.

### Confirmation

Fitness function: (1) a `needs-data` claim resolves on the fast tier, not the frontier model —
observable in the harness's per-subagent model record; (2) the auditor runs on a different
principal and, where the harness supports it, a different model tier than the author; (3) no
concrete model ID appears in the skill/workflow logic — a grep finds model IDs only in harness
config / agent definitions; (4) on a harness without model selection, the run logs "tier routing
unavailable" and makes no model-independence claim for the audit; (5) tier→model changes require
editing only config, never the workflow.

## More Information

Depends on ADR-21 (the harness adapters this routing extends) and ADR-20 (the claim kinds and
the P6 audit it routes); relates to ADR-12 (`/think` as the frontier reasoning tier), ADR-17
(spawn budget bounding the routed spawns), ADR-13 (audit as a distinct-principal sensor).
Harness facts verified 2026-07-24 against the running `pi` v0.80.6 (`--list-models` tier ladder;
subagent extension `--model` passthrough at `@earendil-works/pi-coding-agent`) and Claude Code
subagent `model:` frontmatter — not recalled. Cost-tiered agents precedent: `modu-ai/moai-adk`.
