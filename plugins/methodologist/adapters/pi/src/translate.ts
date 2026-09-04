// Translation between Pi invocations and the methodologist/v1 contract.
//
// This module is pure: it builds Request envelopes from a `/think` invocation
// and renders a Result to the injected ports (TaskTracker / HumanPort) and UI.
// It contains no methodology *judgement* and no phase mechanics — those are the
// core's rules (ADR-30). The adapter only speaks the protocol and drives Pi.

import {
  PROTOCOL,
  type FaultCode,
  type MethodologySelected,
  type PhaseSpec,
  type Request,
  type Result,
} from "./contract.ts";
import type { UiContext } from "./pi-types.ts";
import type { HumanPort, TaskTracker } from "./ports.ts";

// --- Pi invocation -> Request -----------------------------------------------

export interface ParsedInvocation {
  intent: string;
  requestedMethodology: string | null;
}

// A non-empty stand-in when `/think` is invoked with no argument. `intent` is
// required (minLength 1) by the schema, but a slash command only sees its
// argument string; the richer task context is the agent's, supplied when the
// core resolves the selection.
const UNSPECIFIED_INTENT = "(auto-select from current task context)";

/**
 * Parse a `/think` argument string into a selection intent and an optional
 * explicitly-requested methodology.
 *
 * Mirrors SKILL.md: `/think <name>` requests a specific methodology; bare
 * `/think` (or free text) asks the core to select. `knownMethodologies` lets
 * the caller distinguish a methodology name from free-text intent without this
 * module reading the registry.
 */
export function parseThinkInvocation(
  args: string,
  knownMethodologies: readonly string[] = [],
): ParsedInvocation {
  const trimmed = args.trim();
  if (trimmed.length === 0) {
    return { intent: UNSPECIFIED_INTENT, requestedMethodology: null };
  }
  const match = knownMethodologies.find(
    (name) => name.toLowerCase() === trimmed.toLowerCase(),
  );
  if (match !== undefined) {
    return { intent: trimmed, requestedMethodology: match };
  }
  return { intent: trimmed, requestedMethodology: null };
}

export function selectMethodologyRequest(
  intent: string,
  requestedMethodology: string | null,
  requestId: string,
): Request {
  return {
    protocol: PROTOCOL,
    request_id: requestId,
    command: {
      type: "SelectMethodology",
      intent,
      requested_methodology: requestedMethodology,
    },
  };
}

export function completePhaseRequest(
  runId: string,
  phase: number,
  output: string,
  requestId: string,
): Request {
  return {
    protocol: PROTOCOL,
    request_id: requestId,
    command: { type: "CompletePhase", run_id: runId, phase, output },
  };
}

export function produceArtifactRequest(
  runId: string,
  requestId: string,
): Request {
  return {
    protocol: PROTOCOL,
    request_id: requestId,
    command: { type: "ProduceArtifact", run_id: runId },
  };
}

// --- Result -> Pi ------------------------------------------------------------

export interface RenderDeps {
  tracker: TaskTracker;
  human: HumanPort;
  ui: UiContext;
}

export type RenderOutcome =
  | { kind: "selected"; methodology: string }
  | { kind: "choice"; chosen: string }
  | { kind: "advanced"; nextPhase: number | null }
  | { kind: "fault"; code: FaultCode };

const FAULT_MESSAGE: Record<FaultCode, string> = {
  invalid_request: "the invocation could not be understood",
  unknown_methodology: "no methodology by that name exists",
  out_of_order: "phases must complete in order",
  invalid_run: "that reasoning run is not active",
};

function phaseTitle(phase: PhaseSpec, index: number): string {
  if (typeof phase.title === "string" && phase.title.length > 0) {
    return phase.title;
  }
  if (typeof phase.id === "string" && phase.id.length > 0) {
    return phase.id;
  }
  const number = typeof phase.number === "number" ? phase.number : index + 1;
  return `Phase ${number}`;
}

/** Human-readable label + the canonical name to re-dispatch for a candidate. */
function candidateLabelAndName(candidate: unknown): { label: string; name: string } {
  if (typeof candidate === "string") {
    return { label: candidate, name: candidate };
  }
  if (candidate !== null && typeof candidate === "object") {
    const record = candidate as Record<string, unknown>;
    const name = typeof record.name === "string" ? record.name : String(candidate);
    const rationale =
      typeof record.rationale === "string" ? record.rationale : undefined;
    return { label: rationale ? `${name} — ${rationale}` : name, name };
  }
  const text = String(candidate);
  return { label: text, name: text };
}

/**
 * Render one Result to Pi and report what the caller should do next.
 *
 * Single-step by design: on `HumanDecisionRequired` it collects the choice and
 * returns it so the caller re-dispatches a resolved selection — the adapter
 * never invents the follow-up decision itself.
 */
export async function applyResult(
  result: Result,
  deps: RenderDeps,
): Promise<RenderOutcome> {
  switch (result.type) {
    case "MethodologySelected": {
      renderSelected(result, deps.tracker, deps.ui);
      return { kind: "selected", methodology: result.methodology };
    }
    case "HumanDecisionRequired": {
      const labels: string[] = [];
      const nameByLabel = new Map<string, string>();
      for (const candidate of result.candidates) {
        const { label, name } = candidateLabelAndName(candidate);
        labels.push(label);
        nameByLabel.set(label, name);
      }
      const picked = await deps.human.choose(result.question, labels);
      return { kind: "choice", chosen: nameByLabel.get(picked) ?? picked };
    }
    case "PhaseAdvanced": {
      return { kind: "advanced", nextPhase: result.next_phase };
    }
    case "Fault": {
      deps.ui.notify(
        `methodologist: ${FAULT_MESSAGE[result.code]} (${result.code})`,
        "error",
      );
      return { kind: "fault", code: result.code };
    }
  }
}

function renderSelected(
  result: MethodologySelected,
  tracker: TaskTracker,
  ui: UiContext,
): void {
  ui.notify(`Using ${result.methodology}: ${result.reason}`, "info");
  // Mirror core.runner.register_phase_tasks: one task per phase, first started.
  const ids = result.phases.map((phase, index) =>
    tracker.createTask(phaseTitle(phase, index)),
  );
  if (ids.length > 0) {
    tracker.startTask(ids[0]);
  }
}
