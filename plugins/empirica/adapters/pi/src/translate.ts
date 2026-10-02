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
  type InvocationProvenance,
  type Request,
  type Result,
  type RunSelector,
} from "./contract.ts";
import type { SubagentInventory } from "./subagent-inventory.ts";

// The convergence gate's intent and the tool/command name it guards. A run may
// report convergence only through EvaluateRun(report_convergence) (ADR-32).
export const REPORT_CONVERGENCE_INTENT: EvaluateIntent = "report_convergence";
export const REPORT_CONVERGENCE_TOOL = "report_convergence";

export interface ParsedInvocationFlags {
  goal: string;
  unknownFlags: string[];
  controlMode?: "auto";
}

export function splitLeadingFlags(args: string): { flags: string[]; goal: string } {
  const matches = [...args.matchAll(/\S+/g)];
  let index = 0;
  while (index < matches.length && matches[index][0].startsWith("--")) index += 1;
  return {
    flags: matches.slice(0, index).map((match) => match[0]),
    goal: index === 0 ? args : (index < matches.length ? args.slice(matches[index].index) : ""),
  };
}

/** Consume only --auto; every other leading flag is surfaced as unknown. */
export function parseInvocationFlags(args: string): ParsedInvocationFlags {
  const { flags, goal } = splitLeadingFlags(args);
  const unknownFlags = flags.filter((flag) => flag !== "--auto");
  const controlMode = flags.includes("--auto") ? "auto" : undefined;
  return { goal, unknownFlags, ...(controlMode ? { controlMode } : {}) };
}

// --- Pi invocation -> Request -----------------------------------------------

export interface StartRunOptions {
  controlMode?: "auto" | "deliberative";
  maxPasses?: number;
  maxSpawns?: number;
  maxAuditSpawns?: number;
}

export function startRunRequest(
  selector: RunSelector,
  goal: string,
  requestId: string,
  invocation: InvocationProvenance,
  options: StartRunOptions = {},
): Request {
  const command: Extract<Request["command"], { type: "StartRun" }> = {
    type: "StartRun",
    selector,
    goal,
    invocation,
    control_mode: options.controlMode ?? "deliberative",
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
  target: "index" | "section",
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
      if (!run) return { type: "warning", text: "empirica: run unavailable." };
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
// Every `subagent` tool call is exactly one of four things, decided against the reviewed inventory
// of the pi-subagents version that owns the tool (compat/pi-subagents-<version>.json):
//   executable  - starts exactly one child through one launch form; enters investigation + binding
//   management  - a reviewed read-only action; inert
//   unsupported - any other action (control, mutation, schedule, mission, worktree, unknown)
//   malformed   - ambiguous: zero or several launch forms, or a launch form beside an action
// Only `executable` and `management` may proceed; the other two are refused while a run is active.

/** The Pi subagent tool name. */
export const SUBAGENT_TOOL = "subagent";

export type SubagentCallKind = "management" | "executable" | "unsupported" | "malformed";

export interface SubagentCallClassification {
  readonly kind: SubagentCallKind;
  /** Why, for the operator and the refusal text (names the launch form or action involved). */
  readonly detail: string;
}

/**
 * The reviewed read-only management actions (L2 brief; PLAN D4). An action is management only if it
 * is listed here AND present in the version's inventory - a release can add or drop actions without
 * widening this set. ``agentTarget``: the action takes an ``agent`` argument as its target rather
 * than as a launch.
 */
export const MANAGEMENT_ACTIONS: ReadonlyMap<string, { readonly agentTarget: boolean }> = new Map([
  ["list", { agentTarget: false }],
  ["status", { agentTarget: false }],
  ["models", { agentTarget: false }],
  ["guide", { agentTarget: false }],
  ["doctor", { agentTarget: false }],
  ["children.list", { agentTarget: false }],
  ["project.status", { agentTarget: false }],
  ["lane.status", { agentTarget: false }],
  ["watchdog.status", { agentTarget: false }],
  ["inspector.status", { agentTarget: false }],
  ["refine.show", { agentTarget: true }],
  ["validate", { agentTarget: false }],
]);

/** The launch forms ``validate`` takes as the workflow it statically checks (it launches nothing). */
const VALIDATE_WORKFLOW_FORMS: readonly string[] = ["workflow", "workflowScript", "workflowScriptPath"];

/**
 * Classify a `subagent` call's input against the owner version's inventory.
 *
 * A launch form counts when present and non-null. With no `action`, exactly one launch form is an
 * executable launch. With an `action`, the action must be in the version's inventory and in the
 * reviewed management allowlist (``validate`` additionally only where the version documents it as
 * offline), and must not be accompanied by a launch form it does not take (``agent`` for a
 * target-taking action, the workflow forms for ``validate``).
 */
export function classifySubagentCall(input: unknown, inventory: SubagentInventory): SubagentCallClassification {
  if (input === null || typeof input !== "object" || Array.isArray(input))
    return { kind: "malformed", detail: "the call input is not an object" };
  const record = input as Record<string, unknown>;
  const present = inventory.launch_forms.filter((form) => record[form] != null);
  const action = record.action;
  if (action == null) {
    if (present.length === 1) return { kind: "executable", detail: `launch form ${present[0]}` };
    return present.length === 0
      ? { kind: "malformed", detail: `no action and no launch form (${inventory.launch_forms.join(", ")})` }
      : { kind: "malformed", detail: `more than one launch form: ${present.join(", ")}` };
  }
  if (typeof action !== "string" || action.trim() === "")
    return { kind: "malformed", detail: "action must be a nonempty string" };
  const name = action.trim();
  if (!inventory.actions.includes(name))
    return { kind: "unsupported", detail: `action ${JSON.stringify(name)} is not in the pi-subagents ${inventory.version} inventory` };
  const management = MANAGEMENT_ACTIONS.get(name);
  if (management === undefined)
    return { kind: "unsupported", detail: `action ${JSON.stringify(name)} is not in the reviewed read-only allowlist` };
  if (name === "validate" && !inventory.supports.validateOffline)
    return { kind: "unsupported", detail: `pi-subagents ${inventory.version} does not document validate as offline` };
  const stray = present.filter((form) => !(name === "validate" ? VALIDATE_WORKFLOW_FORMS.includes(form)
    : form === "agent" && management.agentTarget));
  if (stray.length > 0)
    return { kind: "malformed", detail: `launch form ${stray.join(", ")} alongside action ${JSON.stringify(name)}` };
  return { kind: "management", detail: `action ${name}` };
}
