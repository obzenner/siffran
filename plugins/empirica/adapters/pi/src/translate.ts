// Translation between Pi events and the empirica/v2 contract (D6-C C3).
//
// This module is pure: it builds Request envelopes from Pi invocations and maps
// a guarded Result to a gate/notification outcome. It contains no convergence
// judgement — whether a run has converged, what is gated, why a report is
// blocked — those are the core's rules, reached through the transport. The
// adapter only speaks the protocol and obeys the returned decision.

import {
  PROTOCOL,
  type Budgets,
  type EvaluateIntent,
  type FaultCode,
  type Modes,
  type Request,
  type Result,
  type RunSelector,
} from "./contract.ts";

// The convergence gate's intent and the tool/command name it guards. A run may
// report convergence only through EvaluateRun(report_convergence) (ADR-32).
export const REPORT_CONVERGENCE_INTENT: EvaluateIntent = "report_convergence";
export const REPORT_CONVERGENCE_TOOL = "report_convergence";

export interface ParsedModeFlags {
  goal: string;
  modes: Modes;
  unknownFlags: string[];
  controlMode?: "auto";
}

/** Consume only leading recognized flags; unknown flags are surfaced, never enabled. */
export function parseModeFlags(args: string): ParsedModeFlags {
  const tokens = args.trim().split(/\s+/).filter(Boolean);
  const modes: Modes = {};
  const unknownFlags: string[] = [];
  let controlMode: "auto" | undefined;
  let i = 0;
  while (i < tokens.length && tokens[i].startsWith("--")) {
    const flag = tokens[i++];
    if (flag === "--auto") controlMode = "auto";
    else if (flag === "--cli-exec") modes.cli_exec = true;
    else if (flag === "--no-cli-exec") modes.cli_exec = false;
    else if (flag === "--multi-provider") modes.multi_provider = true;
    else if (flag === "--no-multi-provider") modes.multi_provider = false;
    else unknownFlags.push(flag);
  }
  return { goal: tokens.slice(i).join(" "), modes, unknownFlags, ...(controlMode ? { controlMode } : {}) };
}

// --- Pi invocation -> Request -----------------------------------------------

export interface StartRunOptions {
  controlMode?: "auto" | "deliberative";
  maxPasses?: number;
  maxSpawns?: number;
  maxAuditSpawns?: number;
  modes?: Modes;
}

export function startRunRequest(
  selector: RunSelector,
  goal: string,
  requestId: string,
  options: StartRunOptions = {},
): Request {
  const command: Extract<Request["command"], { type: "StartRun" }> = {
    type: "StartRun",
    selector,
    goal,
  };
  if (options.maxPasses !== undefined || options.maxSpawns !== undefined
      || options.maxAuditSpawns !== undefined) {
    const budgets: Budgets = {};
    if (options.maxPasses !== undefined) budgets.max_passes = options.maxPasses;
    if (options.maxSpawns !== undefined) budgets.max_spawns = options.maxSpawns;
    if (options.maxAuditSpawns !== undefined)
      budgets.max_audit_spawns = options.maxAuditSpawns;
    command.budgets = budgets;
  }
  if (options.modes !== undefined) command.modes = options.modes;
  if (options.controlMode !== undefined) command.control_mode = options.controlMode;
  return { protocol: PROTOCOL, request_id: requestId, command };
}

export function resolveRunRequest(
  selector: RunSelector,
  requestId: string,
): Request {
  return {
    protocol: PROTOCOL,
    request_id: requestId,
    command: { type: "ResolveRun", selector },
  };
}

export function observeActionRequest(
  runId: string,
  action: { kind: string; [key: string]: unknown },
  requestId: string,
): Request {
  return {
    protocol: PROTOCOL,
    request_id: requestId,
    command: { type: "ObserveAction", run_id: runId, action },
  };
}

export function getRunRequest(runId: string, requestId: string): Request {
  return { protocol: PROTOCOL, request_id: requestId,
           command: { type: "GetRun", run_id: runId } };
}

export function getArgumentRequest(runId: string, requestId: string): Request {
  return { protocol: PROTOCOL, request_id: requestId,
           command: { type: "GetArgument", run_id: runId } };
}

export function getContractRequest(
  target: "index" | "section" | "full",
  requestId: string,
  sectionId?: string,
): Request {
  const command: Extract<Request["command"], { type: "GetContract" }> = {
    type: "GetContract", target,
  };
  if (sectionId !== undefined) command.section_id = sectionId;
  return { protocol: PROTOCOL, request_id: requestId, command };
}

export function evaluateRunRequest(
  runId: string,
  intent: EvaluateIntent,
  requestId: string,
): Request {
  return {
    protocol: PROTOCOL,
    request_id: requestId,
    command: { type: "EvaluateRun", run_id: runId, intent },
  };
}

export function restoreRunRequest(runId: string, requestId: string): Request {
  return {
    protocol: PROTOCOL,
    request_id: requestId,
    command: { type: "RestoreRun", run_id: runId },
  };
}

// --- Result -> gate / notice ------------------------------------------------

/** A gate decision for a hard-gated operation (the convergence report). */
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

/** Extract a human-readable denial reason from a v2 Block's reasons array. */
function blockReason(result: Extract<Result, { type: "Block" }>): string {
  const first = result.reasons[0];
  if (first && typeof first.message === "string" && first.message.length > 0)
    return first.message;
  return first?.code ?? "blocked";
}

/**
 * Map a guarded decision to a gate outcome for a *hard-gated* operation (the
 * convergence report). This is the trust boundary, so it fails **closed**: with
 * a nonnull run handle, ONLY a guarded Allow permits — a Block, an Inert (the
 * run is gone but a handle exists), an open *or* closed Fault, and a transport
 * error all deny. An Allow with converged=false is still a valid, guarded
 * Allow: it is a machine-approved non-convergence report, not a denial.
 */
export function gateFromDecision(result: Result): GateDecision {
  switch (result.type) {
    case "Allow":
      return { kind: "permit" };
    case "Block":
      return { kind: "deny", reason: blockReason(result) };
    case "Inert":
      return { kind: "deny", reason: "no active run to report" };
    case "Fault":
      return { kind: "deny", reason: faultReason(result.code, result.message) };
  }
}

export interface Notice {
  type: "info" | "warning" | "error";
  text: string;
}

/** A user-facing notice describing a StartRun attempt (D6). Truthful about the
 * attempt and any failure — never relabels a start as a status read. */
export function startRunNotice(result: Result): Notice {
  switch (result.type) {
    case "Allow":
      return {
        type: "info",
        text: result.converged
          ? `empirica: D6 StartRun attempt — run ${result.run.id} already converged.`
          : `empirica: D6 StartRun attempt — run ${result.run.id} active.`,
      };
    case "Block":
      return { type: "warning", text: `empirica: D6 StartRun attempt blocked — ${blockReason(result)}` };
    case "Inert":
      return { type: "warning", text: "empirica: D6 StartRun attempt — no run created." };
    case "Fault":
      return { type: "error", text: `empirica: D6 StartRun attempt could not start — ${result.code}: ${faultReason(result.code, result.message)}` };
  }
}

/** A user-facing notice describing a convergence-report decision (command path). */
export function convergenceNotice(result: Result): Notice {
  switch (result.type) {
    case "Allow":
      return result.converged
        ? { type: "info", text: "empirica: run converged — convergence report allowed." }
        : { type: "warning", text: "empirica: allowed, but the run is not marked converged." };
    case "Block":
      return { type: "error", text: `empirica: convergence report blocked — ${blockReason(result)}` };
    case "Inert":
      return { type: "info", text: "empirica: no active run — nothing to report." };
    case "Fault":
      return { type: "error", text: `empirica: cannot evaluate convergence — ${faultReason(result.code, result.message)}` };
  }
}

/** A user-facing notice describing a run snapshot (status path). */
export function statusNotice(result: Result): Notice {
  switch (result.type) {
    case "Allow":
    case "Block": {
      const run = result.run;
      const converged = result.type === "Allow" && result.converged;
      return {
        type: "info",
        text: `empirica run ${run.id}: status=${run.status}${converged ? " (converged)" : ""}`,
      };
    }
    case "Inert":
      return { type: "info", text: "empirica: no active run in this session." };
    case "Fault":
      return { type: "error", text: `empirica: cannot read run — ${faultReason(result.code, result.message)}` };
  }
}

// --- native subagent launch classification ----------------------------------
//
// Only structured executions enter the bound-auditor adapter path. Management
// calls and malformed multi-key requests are inert.

/** The Pi subagent tool name. */
export const SUBAGENT_TOOL = "subagent";

const LAUNCH_KEYS = ["agent", "workflowScript", "resume"] as const;

/**
 * Classify a Pi `subagent` tool call. Returns true only for a *fresh, structured
 * executable launch* — exactly one of `agent`, `workflowScript`, or `resume` is
 * present and non-null. Management calls (list/status) and malformed
 * multi-key launches return false (they are inert, never denied).
 */
export function isExecutableSubagentLaunch(
  toolName: string,
  input: Record<string, unknown> | undefined,
): boolean {
  if (toolName !== SUBAGENT_TOOL) return false;
  const inp = input ?? {};
  const present = LAUNCH_KEYS.filter((k) => k in inp && inp[k] != null);
  return present.length === 1;
}
