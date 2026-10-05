---
number: 66
title: Check workspace citations verbatim when research is submitted
status: accepted
date: 2026-10-06
tags:
- empirica
- evidence
- verification
links:
- target: 20
  kind: Amends
---

# Check workspace citations verbatim when research is submitted

## Context and Problem Statement

Fold 1 requires every research record to carry a verbatim `citation` from the source its `source_ref` names (ADR-20; the skill's `evidence.md`). The service checked only that both fields were non-empty. Whether the citation was real was left to the independent audit, which "independently retrieves every citation".

A dogfood run (Empirica 4.2, Pi, an OpenAI-family author) recorded six research records whose "citations" were 600–750-character summaries of what several files said, each `source_ref` listing many files and line ranges. All five ordinary claims were approved on that research before any audit. The audit then never ran, so nothing caught it, and the observer showed the claims as settled.

The audit remains the gate. But a summary that is not in the file is mechanically detectable, and finding it out only at audit time costs the whole run.

## Decision Drivers

* The rule already exists; this changes when it is enforced, not what it says.
* The core decides deterministically from captured bytes (ADR-13): no judgement, no network.
* Relevance and correctness stay with the auditor; the service only answers "is this text in that file?".
* A refusal must say why and record nothing, so the author can recover in the same pass.

## Considered Options

* **Leave it to the audit.** Rejected: the author learns of a paraphrase only after the run is spent, and only if an audit runs.
* **Require `observed_content_digest`.** Rejected: it binds bytes, not text; a summary can carry the digest of the file it summarises.
* **Check the citation against the cited workspace file when the record is submitted.** Chosen.

## Decision Outcome

Chosen option: check the citation against the cited workspace file when the record is submitted, because it enforces the existing rule as early as it can be checked without judgement.

### The check

For a `code` or `docs` research record whose `source_ref` is a workspace path, the application captures that one file through the workspace port, and the core checks:

* `source_ref` is one repo-relative file, optionally with line ranges (`path`, `path:40`, `path:40-63`, `path:40-63,70-72`);
* the file is present and readable, and the cited lines exist;
* the citation appears within those lines verbatim. Whitespace runs compare as one space, and excerpts joined by an ellipsis (`…`, or three full stops) must each appear, in order.

Otherwise the record is refused with the new reason `research.citation_unverified` (`problem`: `source_ref_form`, `file_unavailable`, `lines_out_of_range` or `text_not_found`; next action `research.record`) and nothing is recorded. A URL or an absolute path, and the `runtime` and `web` kinds, are not checked here: the auditor retrieves them.

### Consequences

* Good, because a summary presented as a citation is refused at once, with a recoverable reason.
* Good, because the check is pure and reuses the workspace port that spikes use.
* Bad, because each research submission reads one more file. That read is not part of the observation basis: spikes alone bind bytes.
* Neutral: an author can still cite a real line that does not support the claim. Judging that remains the audit's job.
