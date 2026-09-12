# Issue: Make semantic obligations a first-class agent-facing contract in Empirica

(Text extracted from empirica_agent_semantic_contracts_issue.docx)


Make semantic obligations a first-class agent-facing contract in Empirica
Summary
Empirica should treat the AI agent consuming its output as a first-class software consumer.
Today Empirica has strong internal machinery for evidence, convergence, audit, persistence, and deterministic gates. However, the semantic information required by the *next agent* is not preserved as a stable interface. Internal state can be correct while the consuming agent is forced to reconstruct what it is expected to do from prose, counts, history, or context.
This is an agent-UX correctness bug.
The proposed change is deliberately small: introduce one first-class abstraction, an obligation, and require every Empirica-to-agent boundary to preserve the current set of obligations and their discharge conditions.
The central invariant should be:
No agent may be required to infer an outstanding obligation from hidden internal state, conversation history, aggregate counts, or generic prose diagnostics. Every agent-facing boundary must explicitly project the obligations that remain and the observations that would discharge them.
The inverse should also hold:
No obligation may exist only in model context.
This turns Empirica from a system that merely establishes and stores knowledge into a system that compiles ambiguous intent into a durable semantic contract another agent can execute and deterministically verify.

Why this is the right abstraction
Empirica is not primarily human-facing software. Its effective customer is another AI agent.
That changes the interface requirement.
A human can often recover meaning from partial diagnostics, inspect the repository, remember earlier context, infer which claim a count refers to, or ask a follow-up question. An agent may resume after compaction, cross a host adapter, enter a new session, or receive only a hook response. If the expected behavior is not explicit at that boundary, the system has lost part of its contract even when its internal state remains intact.
For an agent-facing system, correctness therefore has two parts:
1.  Internal correctness: Empirica correctly represents evidence, claims, audit state, and convergence.
2.  Semantic projection correctness: the consuming agent can recover what is required, prohibited, unresolved, and sufficient to finish the task from the interface Empirica emits.
The second property is currently not first-class.

Regression hypothesis
The original Empirica design was comparatively simple, but its working state was also closer to an agent-readable semantic artifact. The initial plugin described a living spec, unknowns, deterministic spikes, a fixed-point loop, and an eventual handoff to implementation. The termination ADR even proposed a fitness test requiring derived unknowns to become strictly more concrete than their parents.
As Empirica evolved, it correctly strengthened internal assurance: claim graphs, two-fold evidence, audit independence, shadow refs, protocol boundaries, pass budgets, and stronger persistence. But the durable semantic handoff to another agent became weaker.
The architectural regression is therefore not simply "the convergence algorithm got worse." It is:
Empirica improved its internal epistemic machinery while making the agent-facing projection of that state increasingly lossy.
The system can know what is wrong without telling the next actor what it must do about it.
Relevant history:
Initial Empirica plugin: https://github.com/obzenner/siffran/commit/9fd3faa
Claim-graph / gated-run rewrite: https://github.com/obzenner/siffran/commit/2a394d6
Later pass-budget correction: https://github.com/obzenner/siffran/commit/93b2234
The pass-budget regression was real, but it is a separate class of bug. This issue is about preserving semantic obligations across the AI-facing protocol.

Current repository evidence
1. The skill promises resumable semantic state after compaction
plugins/empirica/skills/empirica/SKILL.md says that the Stop hook tells the agent which evidence fold each open claim still owes, and that SessionStart:compact re-injects the graph "including the missing folds" so the loop is durable-resumable.
Source: https://github.com/obzenner/siffran/blob/main/plugins/empirica/skills/empirica/SKILL.md
That is the correct agent-facing semantic promise.
2. RestoreRun currently projects the graph to counts
plugins/empirica/application/service.py::_restore_graph_view derives the gating, open, blocked, and deferred claim sets, but returns only their lengths:
{    "gating": len(gating),    "open": len(open_claims),    "blocked": len(blocked),    "deferred": len(deferred),}

Source: https://github.com/obzenner/siffran/blob/main/plugins/empirica/application/service.py
The application still knows which claims are open. The agent-facing restore projection does not.
3. The Claude restore adapter asks the agent to act on information it was not given
plugins/empirica/adapters/claude/restore.py serializes the application snapshot and tells the model to "continue resolving the application-reported open work."
Source: https://github.com/obzenner/siffran/blob/main/plugins/empirica/adapters/claude/restore.py
But the graph snapshot contains counts, not the identities, meanings, missing evidence, or discharge conditions of that open work.
The program has state. The agent has lost the meaning of the state.
4. The convergence core computes actionable remediation and the application discards it
plugins/empirica/core/convergence.py::_blocked_converging intentionally constructs per-claim ClaimReason values with:
claim ID,
claim text,
confidence,
the specific reason/evidence fold still missing.
The implementation comment explicitly recognizes the agent-UX principle: a generic confidence failure is not actionable, while a specific missing evidence condition is.
Source: https://github.com/obzenner/siffran/blob/main/plugins/empirica/core/convergence.py
However, plugins/empirica/application/service.py::_finalize_block calls:
wire.block(decision.reason, self._run_view(...))

and plugins/empirica/application/wire.py::block exposes only:
{"type": "Block", "reason": reason, "run": run}

Sources: https://github.com/obzenner/siffran/blob/main/plugins/empirica/application/service.py https://github.com/obzenner/siffran/blob/main/plugins/empirica/application/wire.py
So Empirica's core computes the information the agent needs and the protocol boundary collapses it back to generic prose.
5. Final handoff does not define a durable semantic contract
The current skill says the committable result is the resolved goal output (code, document, review, design, etc.), plus ADRs when appropriate and tests for code. The claim graph and evidence machinery remain internal.
Source: https://github.com/obzenner/siffran/blob/main/plugins/empirica/skills/empirica/SKILL.md
Keeping internal evidence machinery internal is reasonable. The missing step is a projection from that machinery into a durable, agent-consumable statement of what must be true.
The next agent should not have to understand GSN, replay the claim graph, or infer behavioral requirements from tests and prose. Empirica should compile its evidence-backed reasoning into an explicit semantic contract.

Design principle: agent UX is a runtime correctness property
This should not be solved as a documentation project.
"Make the prompt clearer" is insufficient because prompts compact, sessions end, adapters change, and downstream agents may not receive the same context.
Instead, define a protocol invariant:
At every Empirica -> agent boundary:semantic obligations before        ==semantic obligations visible afterunless a trusted observation explicitly changed their state.

A boundary includes at least:
Stop / Block feedback,
compaction and resume,
host-adapter translation,
final handoff,
a subsequent implementation or execution session.
A test should fail if an obligation disappears, becomes less specific, loses its discharge condition, or becomes recoverable only by model inference.

One new primitive: Obligation
Do not introduce a large general-purpose eval framework.
Introduce one stable semantic primitive: Obligation.
An obligation is the smallest durable unit of "what another actor is expected to make true."
Conceptually:
id: O17must:  transient failures are retriedmust_not:  authentication failures are retriedwitnesses:  - test: test_transient_retry  - test: test_auth_not_retriedstate: openbecause:  - claim: G7  - claim: G12

The exact schema can change, but every obligation needs enough information to answer:
1.  What must become true?
2.  What must not become true, when relevant?
3.  What trusted observation would prove/disprove it?
4.  Is it currently open, partially discharged, discharged, blocked, or deferred?
5.  Why does it exist / what evidence established it?
The obligation is not an implementation plan. It should avoid prescribing trajectory unless trajectory itself is semantically required.

Claims, obligations, and events are different layers
These concepts should stay separate.
Claim graph / evidence"What do we know, and why may we believe it?"            |            vSemantic obligations"Given that knowledge, what must the next actor cause,preserve, avoid, and demonstrate?"            |            vTrusted events / observations"What actually happened?"            |            vDeterministic verifier"Which obligations remain?"

Empirica's claim graph establishes why an obligation is justified.
The obligation is the interface presented to the next agent.
Events, tests, state deltas, subprocess exit codes, API responses, CI results, or other trusted observations are witnesses that discharge obligations.
The LLM should not be authoritative over whether a machine-observable effect occurred.

The closed loop
The desired loop is:
user intent    |    vEmpirica establishes evidence-backed obligations    |    vagent acts    |    vtrusted events / observations    |    vdeterministic contract verifier    |    vresidual obligations    +---------------------> next agent action

For an observable task, completion becomes ordinary software logic:
required:  O1  refund created                PASS  O2  ticket closed                 PASS  O3  confirmation delivered        FAIL (no witness)forbidden:  F1  subscription cancelled        PASS (not observed)residual = { O3 }

The next hook response is not "approximately 80% correct." It is:
O3 remains unsatisfied:confirmation delivery has no accepted witness.

This is deterministic, repairable, and regression-testable.

Minimal implementation proposal
1. Add an obligation representation
Add a versioned application/core representation containing at minimum:
stable ID,
semantic requirement,
optional prohibition/invariant,
accepted witness/discharge description,
current state,
provenance to claims/evidence.
Do not require a repository-wide rewrite. Obligations can initially be a projection of the existing claim/evidence model.
2. Preserve obligations in Block
Conceptually change:
Block(reason, run)

to:
Block(reason, obligations=[...], run=...)

reason is explanatory prose.
obligations are operational state.
The current rich ClaimReason values are an obvious source for the first projection. Do not compute them and then discard them.
3. Make RestoreRun semantically lossless
Counts may remain as telemetry, but RestoreRun must also expose the actual outstanding obligations.
Bad resume surface:
{"open": 3}

Required resume surface:
O3: obtain Fold 1 citation for claim G4O8: run deterministic spike for claim G9O11: await/obtain the required audit result

The resumed agent should not need the previous conversation to know what remains.
4. Compile final evidence into a semantic handoff
At convergence, Empirica should produce:
resolved deliverable+semantic obligations / invariants / prohibitions+accepted witnesses or verification hooks+explicit residuals, if any

Do not hand the implementation agent the entire GSN graph and expect it to reconstruct the task contract.
The claim graph is internal epistemic machinery. Compile it into the consumer interface.
5. Freeze/version the contract once established
Once Empirica establishes an obligation contract, downstream execution should not silently reinterpret it to match what happened.
If new evidence genuinely changes the contract, create a new revision with provenance.
This prevents an executing model from moving the goalposts after discovering what was convenient to implement.

Deterministic tests that should define the feature
These tests are more important than unit tests of serialization details.
A. Compaction preserves semantic obligations
Given:  O1, O2, O3 are open before compactionWhen:  SessionStart:compact -> RestoreRun -> host adapterThen:  the receiving agent-facing projection contains O1, O2, O3  with equivalent semantic meaning and discharge conditions.

This should fail if only open: 3 survives.
B. Stop feedback is actionable
Given:  claim G7 is open because Fold 2 evidence is missingWhen:  the agent attempts to stopThen:  the agent-visible response identifies:    - the obligation / claim,    - its meaning,    - the missing witness,    - the condition that would discharge it.

C. Witnesses discharge obligations
Given:  O7 requires a deterministic test witnessWhen:  the trusted test event records successThen:  O7 is no longer present in residual obligations.

D. Forbidden effects fail deterministically
Given:  F1 prohibits retrying authentication failuresWhen:  a trusted observation proves an auth failure was retriedThen:  the contract is violated regardless of any model judgment.

E. Exact trajectory is not required unless semantic
Two agents may take different tool-call or implementation paths and still satisfy the same contract.
Tests should assert required effects, forbidden effects, invariants, and necessary ordering constraints - not one arbitrary trajectory.
F. Final handoff is sufficient from a cold start
A particularly important integration test:
Given:  an Empirica run has convergedWhen:  a fresh agent with no conversation history receives only the final handoffThen:  every action-relevant semantic obligation and its verification condition  is recoverable from that handoff without inspecting Empirica internal state.

This is the agent equivalent of an API consumer conformance test.

Acceptance criteria
This issue is complete when all of the following are true:
☐ Outstanding obligations have a first-class structured representation.
☐ Block responses preserve actionable per-obligation remediation instead of only generic prose.
☐ RestoreRun exposes semantic obligations, not only aggregate graph counts.
☐ Host adapters preserve those obligations without requiring conversation history.
☐ Final Empirica handoff contains the semantic contract needed by a fresh downstream agent.
☐ Obligations can name deterministic witnesses (tests, events, state predicates, exit codes, etc.).
☐ A deterministic verifier can compute residual obligations from trusted observations where the effect is machine-observable.
☐ Contract revisions are explicit rather than silently changing downstream expectations.
☐ Integration tests prove semantic preservation across Stop, compaction/restore, adapter translation, and final handoff.
☐ Tests do not require an LLM judge for properties that are already machine-observable.

Non-goals
This proposal does not require:
replacing the claim graph,
replacing GSN,
removing the auditor,
making every semantic statement deterministically decidable,
prescribing one exact agent trajectory,
exposing all internal evidence machinery to downstream agents,
building a generic industry-wide eval platform.
There will remain intrinsically semantic questions for which an LLM or human judgment is useful, especially while compiling ambiguous user intent into obligations.
The boundary is simple:
Once an expected effect is machine-observable, model judgment should not decide whether it happened.

Proposed mental model for Empirica
Current framing:
Empirica is an evidence-convergence workflow around a claim graph.
Proposed product framing:
Empirica compiles ambiguous intent into evidence-backed obligations that other agents can execute and deterministically discharge.
Under this framing:
the claim graph is an implementation mechanism,
GSN is an implementation mechanism,
research and spikes establish evidence,
auditing establishes independence,
hooks enforce boundaries,
the semantic obligation contract is the product interface.

Why this matters beyond Empirica
Most software engineering conventions assume either a human user or a deterministic program at the other side of an interface. Agentic systems introduce a third consumer: a probabilistic actor that can reason well but is vulnerable to lost context, implicit state, semantic compression, ambiguous prose, and reconstruction errors.
For that consumer, a schema is not enough. A valid JSON object can still fail to communicate what behavior is expected next.
Agent-facing systems therefore need a discipline analogous to API design, but for semantics:
make expectations explicit,
make them durable,
version them,
project them at every boundary,
attach machine-checkable witnesses when possible,
compute residual obligations deterministically,
never rely on the model to remember an expectation that the system itself already knows.
This is "AI UX" in the strongest sense: not presentation polish, but designing software interfaces for an AI actor so the actor can reliably continue the system's intent.

Smallest useful first PR
Do not attempt the entire model at once.
The smallest PR that would validate the architecture is:
1.  Preserve the existing ClaimReason / open-claim remediation through the application protocol instead of discarding it.
2.  Add those obligations to Block responses.
3.  Make RestoreRun return the same actionable open obligations rather than only counts.
4.  Add an end-to-end regression test proving an obligation survives:
core decision -> application -> wire -> host adapter -> compaction restore.
5.  Update SKILL.md so its documented resume contract exactly matches what is emitted.
If this materially improves real Empirica runs, generalize the projection into a versioned obligation contract and final handoff in a second PR. This keeps the change empirical: make the semantic boundary lossless first, then extend the abstraction only after the behavior is proven.
