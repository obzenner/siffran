# Governed initialization (public contract 6.0.0)

The rules needed before investigation are in the skill; this reference adds the detail for later.

## Prepare without investigating

The default control mode is **deliberative**. Route, then propose a root-connected claim DAG from supplied context. The claim graph defines the work but is not part of human approval. Size the run from that graph and the task: count gating claims, estimate how many need deterministic experiments, and budget the expected audit rounds (normally 2: an initial audit and one retry). The author agent chooses the ceilings; adapters and core only transport, display, and enforce them. The approval binds the immutable goal as read-only context plus run configuration: all three budget ceilings, control mode, and the exact rationale.

Submit `configure_run` with **all three** proposed ceilings and a nonblank 1–600 character `rationale`. Explain why the graph and task need that size; do not write a command, authority claim, evidence claim, or approvable conclusion. This requests host review and never approves itself. The schema rejects missing ceilings and reviewer fields because reviewer selection belongs to host configuration. Only the exact current approved configuration permits `investigate`.

The ordinary host review is a short form: it displays the goal's first line read-only, the three budget ceilings, and a core-rendered document that holds the sizing rationale under its **Agent sizing rationale — unverified** label. The human accepts to approve the displayed values, declines to reject, or dismisses to decide later; editing any value and accepting submits a ceiling-only amendment. Amendments immediately open one locked confirmation in the same call. It shows the amended values beside their **(was …)** priors and warns **Written for the original proposal; not regenerated for these human-edited values**. It is read-only: approving it approves exactly that revision, while declining or dismissing retains the edits without consent. No author action runs between amendment and confirmation. A decision whose document no longer matches the run's state is refused (`governance.document_mismatch`) with nothing spent.

Cancel, timeout, invalid content, stale or conflicting replies, and missing UI never grant authority. After dismissal, timeout, or rejection the human decides next; when they say how to go on the author takes the view's `next:` (`configure_run` again, same or human-edited ceilings) and records `investigate` only once the view says `approved`. A configuration is shown at most three times and a run at most 128 times. An exact replay of an answered decision is inert; a conflicting replay blocks.

## Modes

Auto is opt-in through explicit `/empirica --auto <goal>`. It is bounded automatic acceptance, not human consent: it never increases budgets without a human (a raise is the human's to approve in interactive auto) and needs an interactive invocation or an operator-recorded delegation. Graph changes keep configuration authority but invalidate stale audit coverage ([audit guide](audit.md)).

- **Deliberative:** every material configuration change returns to the human for review and locked confirmation when amended.
- **Interactive auto:** the initial complete sized proposal has one human approval episode before investigation. Dismissal, timeout, cancellation, rejection, a declined confirmation, or unavailable UI grants nothing and never falls back to delegation. After the first successful approval, non-raising changes are accepted automatically without another dialog; a proposal that raises a ceiling above the approved one (`governance.raises` is true) is shown to the human, who approves or rejects it. A rejected raise changes nothing: continue within the approved ceilings, or stop. When the budget is exhausted and the core offers `budget.raise`, propose a raise with `configure_run` for the human to approve; where it offers none, the host settles the turn with a notice instead of blocking, and you stop honestly with `report_convergence` `intent: "stop"`.
- **Delegated auto:** with no human present and an operator-recorded delegation, proposals may be accepted only inside the fixed 8/1/2 envelope. Run budgets may narrow but never enlarge it. A raise is refused (`governance.auto_ceiling`) and the core never offers `budget.raise`: oversize work must reduce scope, start a fresh run with a larger approved size, or move to an approval-capable host interactively.

**Ending a turn.** One table decides how a turn ends, by control mode and run state, the same on Claude and Pi. A pause or a settlement never approves, confirms or terminalizes anything, and in deliberative mode a step that would end the run without converging (`stopped_*`) asks the human first (`governance.terminal_stop_pending`).

Reviewer configuration comes from the host, never from the author, and observed audit independence remains required for convergence: unavailable or same-identity-class reviewers block before launch, and audit identity classification and its fail-closed cases are owned by the [audit guide](audit.md#independence-reporting). Empirica does not run on Codex. The operator-side setup is in `doc/governance-operations.md`.

## Human confirmations are not configuration

Run configuration is the only thing the configuration dialog approves. Confirming research or answering a
`needs-decision` claim is a separate human decision at the end of a pass: the host first records a
presentation of the pending set (it spends one human interaction, counted together with configuration
presentations against the 128-per-run allowance), then one receipt carries one decision per item, so it can never be
mistaken for a configuration approval, and an identical replay is inert while a conflicting one blocks. With no approval
ingress nothing is asked and the requirement stays open. A submit the service refused
(a Block, a Fault, or one that never landed) admitted nobody's answer: the run reads `host_failed` and is
recovered only by showing the batch again — never as the human's decline.

## Timeout

The host decision timeout is host-owned and shown in the dialog header; it is never approvable. A timeout is dismissal, not rejection.
