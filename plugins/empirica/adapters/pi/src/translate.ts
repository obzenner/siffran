// Translation between Pi events and the empirica/v1 contract.
//
// This module is pure: it builds Request envelopes from Pi invocations and maps a
// typed Result to a host-neutral gate/notification outcome. It contains no
// convergence *judgement* — whether a run has converged, what is gated, why a
// report is blocked — those are the core's rules, reached over the transport
// (ADR-30). The adapter only speaks the protocol and obeys the returned decision.

import {
  PROTOCOL,
  type EvaluateIntent,
  type FaultCode,
  type Request,
  type Result,
  type RunModes,
  type RunSelector,
} from "./contract.ts";

// The convergence gate's intent and the tool/command name it guards. A run may
// report convergence only through EvaluateRun(report_convergence) (ADR-32).
export const REPORT_CONVERGENCE_INTENT: EvaluateIntent = "report_convergence";
export const CONTINUE_INTENT: EvaluateIntent = "continue";
export const REPORT_CONVERGENCE_TOOL = "report_convergence";

// --- Pi invocation -> Request -----------------------------------------------

export interface StartRunOptions {
  maxPasses?: number;
  maxSpawns?: number | null;
  modes?: RunModes;
}

export function startRunRequest(
  selector: RunSelector,
  goal: string,
  requestId: string,
  options: StartRunOptions = {},
): Request {
  const command: Request["command"] = { type: "StartRun", selector, goal };
  if (options.maxPasses !== undefined) command.max_passes = options.maxPasses;
  if (options.maxSpawns !== undefined) command.max_spawns = options.maxSpawns;
  if (options.modes !== undefined) command.modes = options.modes;
  return { protocol: PROTOCOL, request_id: requestId, command };
}

export function getRunRequest(runId: string, requestId: string): Request {
  return {
    protocol: PROTOCOL,
    request_id: requestId,
    command: { type: "GetRun", run_id: runId },
  };
}

export function evaluateRunRequest(
  runId: string,
  intent: EvaluateIntent,
  requestId: string,
  // Epoch SECONDS (float), not milliseconds — Date.now() is ms, so divide by
  // 1000. The core reads this to enforce a wall-clock stall deadline on a
  // blocking run. Defaulted here (the sole clock read the adapter needs) so the
  // requests dispatched from index.ts carry it; a caller may pass an explicit
  // value to keep the builder deterministic under test.
  observedAt: number = Date.now() / 1000,
): Request {
  return {
    protocol: PROTOCOL,
    request_id: requestId,
    command: { type: "EvaluateRun", run_id: runId, intent, observed_at: observedAt },
  };
}

// --- Result -> host-neutral outcomes ----------------------------------------

/** A gate decision, independent of Pi's own return shape (mapped in index.ts). */
export type GateDecision =
  | { kind: "permit" }
  | { kind: "deny"; reason: string };

const FAULT_MESSAGE: Record<FaultCode, string> = {
  invalid_request: "the request was rejected as malformed",
  unsupported: "the operation is not supported by the core",
  conflict: "the run state conflicts with this operation",
  corrupt_run: "the run's operational state is unreadable",
  corrupt_artifacts: "the run's knowledge artifacts are unreadable",
  unavailable: "the empirica core is unavailable",
};

function faultReason(code: FaultCode, message?: string): string {
  return message && message.length > 0 ? message : FAULT_MESSAGE[code];
}

/**
 * Map a decision to a gate outcome for a *hard-gated* operation (the convergence
 * report). This is the trust boundary, so it fails **closed**: an explicit Block
 * denies, and a Fault denies unless the core explicitly says `fail_direction:
 * "open"`. An `Allow` or `Inert` (no active run — nothing to gate) permits.
 */
export function gateFromDecision(result: Result): GateDecision {
  switch (result.type) {
    case "Allow":
      return { kind: "permit" };
    case "Block":
      return { kind: "deny", reason: result.reason };
    case "Inert":
      // No active run (or an event the core does not act on) — not gated.
      return { kind: "permit" };
    case "Fault":
      return result.fail_direction === "open"
        ? { kind: "permit" }
        : { kind: "deny", reason: faultReason(result.code, result.message) };
  }
}

export interface Notice {
  type: "info" | "warning" | "error";
  text: string;
}

/** A user-facing notice describing a convergence-report decision (command path). */
export function convergenceNotice(result: Result): Notice {
  switch (result.type) {
    case "Allow":
      return result.converged
        ? { type: "info", text: "empirica: run converged — convergence report allowed." }
        : {
            type: "warning",
            text: "empirica: allowed, but the run is not marked converged.",
          };
    case "Block":
      return {
        type: "error",
        text: `empirica: convergence report blocked — ${result.reason}`,
      };
    case "Inert":
      return {
        type: "info",
        text: "empirica: no active run — nothing to report.",
      };
    case "Fault":
      return {
        type: "error",
        text: `empirica: cannot evaluate convergence — ${faultReason(result.code, result.message)}`,
      };
  }
}

/** A user-facing notice describing a run snapshot (status command path). */
export function statusNotice(result: Result): Notice {
  switch (result.type) {
    case "Allow":
    case "Block": {
      const run = result.run;
      const converged = result.type === "Allow" && result.converged;
      return {
        type: "info",
        text:
          `empirica run ${run.id}: status=${run.status}, revision=${run.revision}` +
          (converged ? " (converged)" : ""),
      };
    }
    case "Inert":
      return { type: "info", text: "empirica: no active run in this session." };
    case "Fault":
      return {
        type: "error",
        text: `empirica: cannot read run — ${faultReason(result.code, result.message)}`,
      };
  }
}

/**
 * Build the best-effort follow-up nudge for `agent_settled`, or `null` when there
 * is nothing to say. This is explicitly **not** a completion gate (ADR-32): Pi's
 * settled lifecycle is observational and cannot be vetoed, so the text names
 * itself a reminder, and the caller enqueues it without blocking anything.
 */
export function settledFollowUp(result: Result): string | null {
  // Only nudge while a run is genuinely active and unconverged. Everything else —
  // converged, terminal, no run, or a fault — means there is nothing to prompt.
  if (result.type !== "Block") return null;
  return (
    `empirica (reminder, not a gate): this run has outstanding work before it can ` +
    `report convergence — ${result.reason}. ` +
    `Continue, or call the ${REPORT_CONVERGENCE_TOOL} tool once the evidence is in.`
  );
}
