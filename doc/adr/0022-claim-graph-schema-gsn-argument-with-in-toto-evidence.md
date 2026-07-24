---
number: 22
title: "Claim-graph schema: a GSN assurance argument with in-toto evidence records"
status: accepted
date: 2026-07-24
tags:
  - architecture
  - state
  - standards
  - verification
links:
  - target: 15
    kind: Supersedes
  - target: 20
    kind: Depends on
  - target: 18
    kind: relatesto
  - target: 7
    kind: relatesto
  - target: 19
    kind: relatesto
---

# Claim-graph schema: a GSN assurance argument with in-toto evidence records

## Context and Problem Statement

ADR-15 adopted the GitHub **spec-kit** artifact set (`spec.md`, `plan.md`, `tasks.md`,
`research.md`) as empirica's state substrate, with "unknowns live as open items in the living
spec." That choice fit the workflow empirica *was* — draft a spec, converge it, hand it off.
It does not fit the workflow empirica now *is*. ADR-20 redefined a run as the adjudication of a
**claim graph**: nodes the agent proposes, validates with two-fold evidence, then approves,
blocks, or discards, until every claim on the path to the goal is terminal.

spec-kit is a **document model** — prose files a human authors and reviews. A claim graph is a
**typed directed graph with evidence bindings**. Forcing the graph into markdown documents is
the impedance mismatch behind this project's worst failures: "unknowns live in `spec.md`" is
what made the working memory look like a deliverable, land at the repository root, and carry
confidence as a free-text HTML comment a model types at will. The data structure was fighting
the model.

The question: what schema represents empirica's claim graph and its evidence — without
inventing a bespoke one, and without carrying spec-kit's document baggage?

## Decision Drivers

* Standards over invention (the standing rule): the field has a mature standard for
  "structured claims decomposed into sub-claims and terminated in evidence" — assurance cases.
  Inventing a `Claim`/`Evidence` JSON schema when a formal metamodel exists is reinvention.
* The schema must match the model: a graph of claims with an argument structure and evidence
  leaves, not a set of prose documents.
* Evidence records (ADR-20 Fold 1 research citations, Fold 2 spike results) need a verifiable,
  content-addressed shape — a solved problem in supply-chain provenance.
* The one genuinely novel piece — confidence scoring + θ convergence over the claim graph
  (ADR-7/9) — has no standard and is kept as empirica's layer on top.
* Reference standards; do not vendor or hard-depend (consistent with ADR-3/15's own driver).

## Considered Options

* **Keep spec-kit (status quo, ADR-15).** Rejected: document model vs claim-graph model
  mismatch; it is the direct cause of the spec-as-deliverable failure. spec-kit remains a fine
  standard for its purpose — human-authored specs — which is no longer empirica's substrate.
* **Invent a bespoke claim/evidence JSON schema.** Rejected: reinvents assurance cases, a
  mature formal standard; a hand-rolled schema drifts and carries no tooling or shared meaning.
* **GSN / SACM assurance argument + in-toto evidence (chosen).** Represent the claim graph as a
  Goal Structuring Notation argument (Goal → Strategy → Solution), optionally serialisable to
  the OMG SACM interchange metamodel; represent each evidence leaf as an in-toto attestation
  Statement. Confidence/θ is empirica's additive layer.
* **Toulmin argumentation model.** Considered: claim/grounds/warrant/rebuttal is the right
  shape conceptually, but it is an argumentation *theory*, not a machine-readable interchange
  standard with tooling. Kept as intellectual lineage for the "warrant" idea (why evidence
  supports a claim), not as the schema.

## Decision Outcome

Chosen: **the claim graph is a GSN assurance argument; evidence leaves are in-toto attestation
Statements; confidence/θ is empirica's layer on top.** Verified against sources (2026-07-24):
GSN is an open, Creative-Commons standard maintained by the SCSC Assurance Case Working Group;
its machine-readable metamodel is OMG **SACM v2.3** (formal, adopted Oct 2023, XML
interchange); in-toto attestation **Statement** (`_type`, `subject[].digest`, `predicateType`,
`predicate`) is the supply-chain-provenance standard for binding a claim to a content-addressed
subject.

**The mapping (empirica ⟷ GSN):**

| empirica concept | GSN element | Notes |
|---|---|---|
| the intent | top **Goal** | the claim the whole run must establish |
| an unknown / sub-claim | **Goal** (sub-goal) | a node the agent adjudicates |
| how a claim is broken down | **Strategy** | e.g. "resolve by routing into needs-data / needs-experiment / needs-decision" |
| a claim's scope / inputs | **Context** | the repo, the harness, pinned versions |
| a `needs-decision` residual | **Assumption** / away-Goal | surfaced to the human; not agent-resolvable |
| Fold-1 research evidence | **Solution** (evidence leaf) | citation to a non-training-data source |
| Fold-2 spike result | **Solution** (evidence leaf) | deterministic check outcome |
| an open (sub-θ) claim | **undeveloped** Goal | GSN's own "not yet supported by evidence" marker |
| why evidence supports the claim | **Justification** (Toulmin *warrant*) | recorded, auditor-checkable |

**Evidence-leaf shape (in-toto Statement, adapted):**
```
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject":       [{ "name": "<claim_id>", "digest": { "sha256": "<claim_text_hash>" } }],
  "predicateType": "empirica/research/v1" | "empirica/spike/v1",
  "predicate": {
     "kind":   "docs|code|runtime|web"        // Fold 1
             | "spike",                        // Fold 2
     "citation|command": "...",
     "result": "supports|refutes" | "gate: pass|fail",
     "hashes": { "files": "...", "result": "..." },   // Fold 2 tamper-evidence
     "ts": "<caller-stamped>"
  }
}
```
The evidence record binds `claim_id ↔ evidence` exactly as ADR-20 P3/ADR-21 Mechanism 2
require; content-addressing (the digest) is what makes it tamper-evident.

**Confidence layer (empirica's own, no standard):** each Goal carries `confidence ∈ [0,1]`; a
Goal is **developed** (supported) when confidence ≥ θ with valid evidence leaves, **undeveloped**
below θ, **blocked** when an Assumption/human residual, **discarded** when evidence refutes it
(the node and its sub-goals are pruned — ADR-20). Convergence = every Goal on the path to the
top Goal is developed, blocked, or discarded, and the audit passed (ADR-20 P7).

**Serialisation and storage.** The graph lives in the run directory
(`.claude/empirica/<run_id>/`, ADR-19) as transient run memory (ADR-14). The on-disk form is a
compact JSON claim graph (GSN element types as the node schema) plus one in-toto Statement per
evidence leaf; SACM XML export is available when a run must be handed to an external assurance
tool, but is not the working format. **No spec-kit files, no repository-root `spec.md`.**

**What is dropped.** spec-kit templates, the pinned-commit `WebFetch`, and the
`spec/plan/tasks/research.md` file set leave empirica entirely. ADR-15 is superseded on the
substrate question; its still-valid principle — reference standards, do not vendor — is carried
forward here (GSN/SACM/in-toto are referenced, not vendored).

### Consequences

* Good, because the schema now matches the model: a claim graph is stored as a claim graph, so
  the spec-as-deliverable class of failure cannot recur — there is no document to mistake for
  output.
* Good, because empirica speaks a real assurance-case vocabulary (Goal/Strategy/Solution) with a
  formal metamodel (SACM) and can export to external assurance tooling, and its evidence records
  are in-toto attestations rather than a bespoke shape.
* Good, because the argument structure makes the auditor's job (ADR-20 P6) concrete: walk the
  Goal→Strategy→Solution tree and check every developed Goal has valid Solution leaves.
* Bad, because GSN/SACM originate in safety-critical systems and carry concepts empirica does
  not need (full SACM is heavy); empirica uses the *element vocabulary and structure*, not the
  entire metamodel — a deliberately partial adoption that must be documented so it is not
  mistaken for full SACM conformance.
* Bad, because this supersedes an accepted ADR (15) and discards shipped assumptions; the SKILL
  and any spec-kit references must be rewritten (tracked as build work, ADR-16 binding updated).
* Bad, because "which subset of SACM, and whether to emit SACM XML at all" is a real open
  question (below) — adopted as vocabulary now, exact conformance deferred to build with a
  live spec read.

### Confirmation

Fitness function: (1) a run's state is a JSON claim graph in the run directory whose node types
are GSN elements — no `spec.md`/`plan.md`/`tasks.md` anywhere, none at repo root; (2) every
evidence leaf validates as an in-toto Statement with a `subject.digest` binding it to its claim;
(3) an undeveloped (sub-θ) Goal on the path to the top Goal blocks convergence (ADR-20 P7);
(4) a refuted Goal is discarded and its sub-goals pruned, not parked; (5) a grep for spec-kit
template URLs or vendored templates returns nothing; (6) the graph optionally exports to SACM
XML for an external tool without loss of the claim/evidence structure.

## More Information

Supersedes ADR-15 on the state substrate (claim graph replaces spec-kit documents); the
"reference-don't-vendor" principle is retained. Depends on ADR-20 (the claim-graph run protocol
this schema serves) and relates to ADR-18 (evidence binding — now an in-toto Statement), ADR-7
(confidence/θ, empirica's layer over GSN), ADR-19 (the run directory the graph lives in).
Standards verified 2026-07-24 against live sources: GSN (scsc.uk/gsn, CC-licensed, SCSC ACWG),
OMG SACM v2.3 (omg.org/spec/SACM, formal Oct 2023, XML interchange), in-toto attestation
Statement v1 (github.com/in-toto/attestation). Intellectual lineage: Toulmin's argument model
(claim/grounds/warrant/rebuttal) for the Justification/warrant concept. Open item: the exact
SACM subset empirica conforms to (vocabulary-only vs XML-exportable) is a build-time decision
requiring a full read of the SACM v2.3 spec — recorded, not guessed.
