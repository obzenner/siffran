// Empirica adapter for Pi (pi.dev) — v2-only, radically simplified (D6-C C3).
//
// The Pi adapter is a *translator* over the empirica/v2 contract: it maps Pi's
// native events into requests and maps the guarded typed decision back onto
// Pi's enforcement/UI. It holds no convergence rules — those live in the
// host-neutral core, reached through the injected `dispatch` seam.
//
// Complete exact-profile surfaces:
//   * /empirica -> StartRun and durable opaque-handle context
//   * empirica_observe/read/report_convergence -> canonical public v2 operations
//   * tool_call/tool_result -> bound foreground pi-subagents auditor, synchronous
//     redaction, and adapter-private trusted ingress
//   * session restoration/compaction -> RestoreRun
//
// State and convergence policy remain behind the transport. The adapter retains
// only the session handle and host-native child correlation needed to observe the
// exact foreground result.

import { fileURLToPath } from "node:url";
import * as path from "node:path";
import { lstatSync, readFileSync, realpathSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";

import type { Dispatch, Request, Response, RunSelector } from "./contract.ts";
import { assertResponse } from "./guard.ts";
import type {
  ExtensionAPI,
  ExtensionContext,
  ToolCallEvent,
  ToolCallResult,
  ToolResultEvent,
} from "./pi-types.ts";
import { createPrivateIngress, type AuditPlanData, type PrivateIngress } from "./private-transport.ts";
import {
  lifecycleEvent, redactVerdict, resultDigest, resultText as auditResultText, verdictFromText,
} from "./audit.ts";
import {
  identityFromSessionJsonl, sessionFileFromDetails,
} from "./audit-identity.ts";
import { createStdioBridgeDispatch, defaultBridgeConfig } from "./stdio-transport.ts";
import {
  REPORT_CONVERGENCE_INTENT,
  REPORT_CONVERGENCE_TOOL,
  evaluateRunRequest,
  gateFromDecision,
  getArgumentRequest,
  getContractRequest,
  getRunRequest,
  isExecutableSubagentLaunch,
  observeActionRequest,
  parseModeFlags,
  resolveRunRequest,
  restoreRunRequest,
  startRunRequest,
  startRunNotice,
  type StartRunOptions,
} from "./translate.ts";

const MAX_AUDIT_SESSION_BYTES = 16 * 1024 * 1024;
function readAuditSession(file: string): string | null {
  try {
    const before = lstatSync(file);
    if (!before.isFile() || before.size > MAX_AUDIT_SESSION_BYTES) return null;
    const bytes = readFileSync(file);
    const after = lstatSync(file);
    return before.dev === after.dev && before.ino === after.ino && before.size === after.size
      && bytes.length === before.size ? bytes.toString("utf8") : null;
  } catch { return null; }
}

// plugins/empirica/adapters/pi/src/index.ts -> plugins/empirica/skills
export const DEFAULT_SKILLS_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
  "skills",
);

function canonicalPath(value: string): string {
  try {
    return realpathSync.native(value);
  } catch {
    return path.resolve(value);
  }
}

function sameCanonicalAgentFile(candidate: string, expected: string): boolean {
  if (canonicalPath(candidate) === canonicalPath(expected)) return true;
  const candidateText = readAuditSession(candidate);
  const expectedText = readAuditSession(expected);
  return candidateText !== null && expectedText !== null && candidateText === expectedText;
}

const PUBLIC_TOOLS_PATH = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..", "..",
  "contracts", "empirica", "v2", "public-tools.json");
const PUBLIC_TOOL_SCHEMAS = (JSON.parse(readFileSync(PUBLIC_TOOLS_PATH, "utf8")) as {
  schemas: { host_handle: Record<string, Record<string, unknown>> };
}).schemas.host_handle;
const actionChoices = ((PUBLIC_TOOL_SCHEMAS.empirica_observe.properties as {
  action: { oneOf: Array<{ properties: { kind: { const: string } } }> };
}).action.oneOf);
const AUTHOR_ACTION_KIND_SET = new Set(actionChoices.map(
  (choice) => choice.properties.kind.const));

function skillInvocation(skillsDir: string, args: string): string {
  const source = readFileSync(path.resolve(skillsDir, "empirica", "SKILL.md"), "utf8");
  const body = source.replace(/^---[\s\S]*?---\s*/, "").trim();
  if (!body) throw new Error("canonical Empirica skill is empty");
  return body.replaceAll("$ARGUMENTS", () => args);
}

/** Resolves the run selector from Pi host context. */
export type SelectorProvider = (ctx: ExtensionContext) => RunSelector;

export interface ResolvedAuditContract {
  agentFilePath: string;
  model: string;
}
export type AuditContractResolver = (
  input: Record<string, unknown>, ctx: ExtensionContext,
) => Promise<ResolvedAuditContract>;

async function defaultAuditContractResolver(
  input: Record<string, unknown>, ctx: ExtensionContext,
): Promise<ResolvedAuditContract> {
  const api = await import("pi-subagents/preflight") as {
    resolveSubagentLaunchContract(input: Record<string, unknown>): Promise<
      { ok: true; contract: { agent: { filePath: string }; model?: string; modelCandidates: string[] } }
      | { ok: false; message: string }
    >;
  };
  const result = await api.resolveSubagentLaunchContract({
    agent: String(input.agent),
    task: typeof input.task === "string" ? input.task : undefined,
    context: "fresh",
    model: process.env.EMPIRICA_PI_AUDITOR_MODEL,
    cwd: ctx.cwd ?? process.cwd(),
    availableModels: ctx.modelRegistry?.getAvailable(),
  });
  if (!result.ok) throw new Error(result.message);
  const model = result.contract.modelCandidates[0] ?? result.contract.model;
  if (!model) throw new Error("packaged auditor has no resolved model");
  return { agentFilePath: result.contract.agent.filePath, model };
}

export interface EmpiricaPiDeps {
  /** Bridge to the host-neutral Empirica core (empirica/v2). */
  dispatch: Dispatch;
  /** Absolute path to the empirica `skills` directory to contribute. */
  skillsDir?: string;
  /** Derive the run selector from Pi context (default: workspace hash + session id). */
  deriveSelector?: SelectorProvider;
  /** Tool names whose call is the convergence report and must be gated. */
  gatedTools?: readonly string[];
  /** StartRun options (max_passes, max_spawns, modes). */
  startRunOptions?: StartRunOptions;
  /** Tool name used for the bound auditor lifecycle. */
  subagentToolName?: string;
  /** Adapter-private trusted ingress; production uses the private Python bridge. */
  privateIngress?: PrivateIngress;
  /** Side-effect-free pi-subagents launch-contract resolution. */
  resolveAuditContract?: AuditContractResolver;
}

function defaultSelectorProvider(): SelectorProvider {
  const session = randomUUID();
  return (ctx) => {
    const cwd = ctx.cwd ?? process.cwd();
    const project = createHash("sha256").update(cwd).digest("hex").slice(0, 16);
    return { project, session };
  };
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isInvestigationTool(toolName: string, input: Record<string, unknown>,
                             gatedTools: Set<string>, subagentToolName: string): boolean {
  if (gatedTools.has(toolName) || toolName === "empirica_read") return false;
  if (toolName === subagentToolName)
    return isExecutableSubagentLaunch(toolName, input);
  if (toolName === "empirica_observe") {
    const action = input.action;
    if (!action || typeof action !== "object") return false;
    return new Set(["research", "spike_request"]).has(
      String((action as Record<string, unknown>).kind));
  }
  return true;
}

/**
 * Build the Pi extension function from its dependencies.
 *
 * Tests inject a fake dispatch to prove registration, translation, gating, and
 * guard behaviour without the core, a bridge, or the Pi runtime.
 */
export function createEmpiricaExtension(deps: EmpiricaPiDeps) {
  const skillsDir = deps.skillsDir ?? DEFAULT_SKILLS_DIR;
  const selectorOf = deps.deriveSelector ?? defaultSelectorProvider();
  const gatedTools = new Set(deps.gatedTools ?? [REPORT_CONVERGENCE_TOOL]);
  const startOptions = deps.startRunOptions ?? {};
  const subagentToolName = deps.subagentToolName ?? "subagent";
  const privateIngress = deps.privateIngress ?? createPrivateIngress();
  const resolveAuditContract = deps.resolveAuditContract ?? defaultAuditContractResolver;

  return function empiricaExtension(pi: ExtensionAPI): void {
    // The active run's opaque handle, held only in memory for this session.
    let runHandle: string | null = null;
    type AuditCorrelation = {
      runHandle: string;
      nativeId: string;
      plan: AuditPlanData;
      author: Record<string, unknown>;
      auditor: Record<string, unknown>;
    };
    const audits = new Map<string, AuditCorrelation>();
    const completedAuditCalls = new Set<string>();
    type ChildCorrelation = { runHandle: string; childId: string; nativeId: string };
    const children = new Map<string, ChildCorrelation>();
    const reportEvaluations = new Map<string, { intent: string; response: Response }>();
    const terminalStatus = (response: Response): boolean => {
      const result = response.result;
      if (result.type !== "Allow") return false;
      return new Set(["converged", "stopped_budget", "stopped_frozen", "stopped_residual"])
        .has(String(result.run.status));
    };
    const retireTerminal = (response: Response): void => {
      if (!runHandle || !terminalStatus(response)) return;
      pi.appendEntry?.("empirica.run.done", { runHandle });
      runHandle = null;
    };

    // Guarded dispatch: every response passes through the central runtime guard
    // before ANY gate or render. A malformed/partial/unknown/mismatched response
    // throws — the hard gate catches and fails closed.
    const dispatch = async (request: Request): Promise<Response> => {
      const response = await Promise.resolve(deps.dispatch(request));
      assertResponse(response, request.request_id);
      return response;
    };
    const trusted = async (request: Parameters<PrivateIngress>[0]): Promise<Record<string, unknown>> =>
      privateIngress(request);

    // (resources) Contribute the shared Empirica skill so Pi discovers the
    // workflow instructions — the same resource Claude Code ships, no per-host fork.
    pi.on("resources_discover", () => ({ skillPaths: [skillsDir] }));

    // (session_start) Restore the run handle from persisted entries.
    pi.on("session_start", async (_event, ctx) => {
      const entries = ctx.sessionManager?.getEntries() ?? [];
      const terminalRuns = new Set(entries.filter((entry) => entry.customType === "empirica.run.done")
        .map((entry) => (entry.data as { runHandle?: unknown } | undefined)?.runHandle)
        .filter((value): value is string => typeof value === "string"));
      const completedAudits = new Set(entries.filter((entry) => entry.customType === "empirica.audit.done")
        .map((entry) => (entry.data as { toolCallId?: unknown } | undefined)?.toolCallId)
        .filter((value): value is string => typeof value === "string"));
      completedAuditCalls.clear();
      for (const toolCallId of completedAudits) completedAuditCalls.add(toolCallId);
      for (const entry of entries) {
        const data = entry.data as {
          runHandle?: unknown; toolCallId?: unknown; nativeId?: unknown;
          plan?: unknown; author?: unknown; auditor?: unknown; childId?: unknown;
        } | undefined;
        if (entry.customType === "empirica.run" && typeof data?.runHandle === "string"
            && !terminalRuns.has(data.runHandle))
          runHandle = data.runHandle;
        if (entry.customType === "empirica.audit" && !completedAudits.has(String(data?.toolCallId))
            && typeof data?.runHandle === "string"
            && typeof data.toolCallId === "string" && typeof data.nativeId === "string"
            && data.plan && typeof data.plan === "object"
            && data.author && typeof data.author === "object"
            && data.auditor && typeof data.auditor === "object") {
          audits.set(data.toolCallId, {
            runHandle: data.runHandle, nativeId: data.nativeId,
            plan: data.plan as AuditPlanData,
            author: data.author as Record<string, unknown>,
            auditor: data.auditor as Record<string, unknown>,
          });
        }
        if (entry.customType === "empirica.child" && typeof data?.runHandle === "string"
            && typeof data.toolCallId === "string" && typeof data.nativeId === "string"
            && typeof data.childId === "string") {
          children.set(data.toolCallId, {
            runHandle: data.runHandle, childId: data.childId, nativeId: data.nativeId,
          });
        }
      }
      // Foreground correlations cannot remain live across a restored session boundary.
      for (const [toolCallId, child] of children) {
        try {
          await trusted({ operation: "child_event", run_id: child.runHandle,
            child_id: child.childId, payload: lifecycleEvent("orphaned", child.nativeId) });
          children.delete(toolCallId);
        } catch { /* retain the durable correlation for a later reconciliation attempt */ }
      }
      for (const [toolCallId, audit] of audits) {
        try {
          await trusted({ operation: "audit_failure", run_id: audit.runHandle,
            native_id: audit.nativeId, plan: audit.plan, state: "orphaned" });
          audits.delete(toolCallId);
          completedAuditCalls.add(toolCallId);
          pi.appendEntry?.("empirica.audit.done", { toolCallId });
        } catch { /* retain the durable correlation for a later reconciliation attempt */ }
      }
    });

    // Foreground work should be terminal before shutdown. Any remaining correlation is orphaned;
    // a failed private acknowledgement leaves its durable entry available for the next restore.
    pi.on("session_shutdown", async () => {
      for (const [toolCallId, child] of children) {
        try {
          await trusted({ operation: "child_event", run_id: child.runHandle,
            child_id: child.childId, payload: lifecycleEvent("orphaned", child.nativeId) });
          children.delete(toolCallId);
        } catch { /* retain the durable correlation */ }
      }
      for (const [toolCallId, audit] of audits) {
        try {
          await trusted({ operation: "audit_failure", run_id: audit.runHandle,
            native_id: audit.nativeId, plan: audit.plan, state: "orphaned" });
          audits.delete(toolCallId);
          completedAuditCalls.add(toolCallId);
          pi.appendEntry?.("empirica.audit.done", { toolCallId });
        } catch { /* retain the durable correlation */ }
      }
    });

    // Public schemas are a checked mechanical artifact projected from request.schema.json.
    const EMPTY_PARAMS = PUBLIC_TOOL_SCHEMAS.report_convergence;
    const OBSERVE_PARAMS = PUBLIC_TOOL_SCHEMAS.empirica_observe;
    const READ_PARAMS = PUBLIC_TOOL_SCHEMAS.empirica_read;
    const resultText = (response: Response): string => JSON.stringify(response.result);
    if (pi.registerTool) {
      pi.registerTool({
        name: REPORT_CONVERGENCE_TOOL,
        label: "Report convergence",
        description: "Ask Empirica for guarded convergence or an honest residual stop.",
        parameters: EMPTY_PARAMS,
        async execute(id, raw) {
          if (!runHandle && !reportEvaluations.has(id))
            return { content: [{ type: "text", text: "No active Empirica run." }] };
          const intent = (raw as { intent?: unknown }).intent === "stop"
            ? "stop" : REPORT_CONVERGENCE_INTENT;
          const prepared = reportEvaluations.get(id);
          reportEvaluations.delete(id);
          const response = prepared?.intent === intent ? prepared.response : await dispatch(
            evaluateRunRequest(runHandle!, intent, randomUUID()),
          );
          const decision = gateFromDecision(response.result);
          if (decision.kind === "deny")
            throw new Error(`${decision.reason}\nhandle: ${runHandle ?? "terminal"}`);
          retireTerminal(response);
          return { content: [{ type: "text", text: resultText(response) }], details: response.result };
        },
      });

      pi.registerTool({
        name: "empirica_read",
        label: "Read Empirica",
        description: "Read the current run, audit argument, or public contract.",
        parameters: READ_PARAMS,
        async execute(_id, raw, _signal, _onUpdate, ctx) {
          const params = raw as { operation?: unknown; target?: unknown; section_id?: unknown };
          const operation = params.operation;
          let request: Request;
          if (operation === "GetContract") {
            const target = params.target;
            if (target !== "index" && target !== "section" && target !== "full")
              throw new Error("GetContract requires target=index|section|full");
            if (target === "section" && typeof params.section_id !== "string")
              throw new Error("GetContract(section) requires section_id");
            request = getContractRequest(target, randomUUID(),
              typeof params.section_id === "string" ? params.section_id : undefined);
          } else {
            if (!runHandle) {
              const resolved = await dispatch(resolveRunRequest(selectorOf(ctx), randomUUID()));
              const run = (resolved.result.type === "Allow" || resolved.result.type === "Block")
                ? resolved.result.run : undefined;
              runHandle = run?.id ?? null;
            }
            if (!runHandle)
              return { content: [{ type: "text", text: "No active Empirica run." }] };
            if (operation === "GetRun") request = getRunRequest(runHandle, randomUUID());
            else if (operation === "GetArgument") request = getArgumentRequest(runHandle, randomUUID());
            else if (operation === "RestoreRun") request = restoreRunRequest(runHandle, randomUUID());
            else throw new Error("unknown Empirica read operation");
          }
          const response = await dispatch(request);
          return { content: [{ type: "text", text: resultText(response) }], details: response.result };
        },
      });

      pi.registerTool({
        name: "empirica_observe",
        label: "Observe Empirica action",
        description: "Submit one public Empirica author action for the active run.",
        parameters: OBSERVE_PARAMS,
        async execute(_id, raw) {
          if (!runHandle)
            return { content: [{ type: "text", text: "No active Empirica run." }] };
          const params = raw as { action?: unknown };
          if (!params.action || typeof params.action !== "object")
            throw new Error("action must be an object");
          const action = params.action as { kind?: unknown; [key: string]: unknown };
          if (typeof action.kind !== "string" || !AUTHOR_ACTION_KIND_SET.has(action.kind))
            throw new Error("trusted or unknown Empirica action kind");
          const response = await dispatch(observeActionRequest(
            runHandle, action as { kind: string; [key: string]: unknown }, randomUUID()));
          return { content: [{ type: "text", text: resultText(response) }], details: response.result };
        },
      });
    }

    pi.registerCommand("empirica", {
      description: "Start a complete Empirica v2 convergence run.",
      handler: async (args, ctx) => {
        if (ctx.isIdle?.() === false) {
          ctx.ui.notify("/empirica requires an idle session; retry after the current turn finishes.",
            "warning");
          return;
        }
        const parsed = parseModeFlags(args);
        const goal = parsed.goal || "(goal to be refined from the current task)";
        const modes = { ...startOptions.modes, ...parsed.modes };
        if (parsed.unknownFlags.length)
          ctx.ui.notify(`empirica: unknown mode flags ignored: ${parsed.unknownFlags.join(" ")}`, "warning");
        try {
          // Render the canonical installed skill before creating a run. If the
          // package is incomplete, fail without leaving an active orphan.
          const kickoff = skillInvocation(skillsDir, args);
          const response = await dispatch(startRunRequest(selectorOf(ctx), goal, randomUUID(), {
            ...startOptions, modes,
          }));
          const result = response.result;
          if (result.type === "Allow" || result.type === "Block") {
            runHandle = result.run.id;
            pi.appendEntry?.("empirica.run", { runHandle });
            pi.sendMessage?.({
              customType: "empirica",
              content: `Empirica v2 is active. Opaque run handle: ${runHandle}. Use empirica_observe, empirica_read, and report_convergence.`,
            });
            // Extension-injected slash commands are not passed through Pi's
            // interactive skill expander. Render the canonical SKILL.md itself
            // so no adapter-local workflow copy can drift.
            pi.sendUserMessage(kickoff, { deliverAs: "followUp" });
          }
          const notice = startRunNotice(result);
          ctx.ui.notify(notice.text, notice.type);
        } catch (error) {
          ctx.ui.notify(`/empirica could not start a run: ${describe(error)}`, "error");
        }
      },
    });

    const auditorInstructions = (): string => {
      try {
        return readFileSync(path.resolve(skillsDir, "..", "agents", "empirica-auditor.md"), "utf8")
          .replace(/^---[\s\S]*?---\s*/, "").trim();
      } catch { return "Review the supplied Empirica argument and return one verdict block."; }
    };
    const isCanonicalAuditorInput = (input: Record<string, unknown>): boolean => {
      if (input.agent !== "empirica.empirica-auditor" || typeof input.task !== "string")
        return false;
      return Object.keys(input).every((key) => key === "agent" || key === "task");
    };
    const modelPair = (value: unknown, fallbackProvider: string): [string | null, string | null] => {
      if (typeof value !== "string" || !value) return [null, null];
      const slash = value.indexOf("/");
      return slash > 0 ? [value.slice(0, slash), value.slice(slash + 1)] : [fallbackProvider, value];
    };

    // The verdict is parsed from the exact correlated foreground child result. Redaction happens
    // synchronously before the first await; only adapter-private ingress can admit the candidate.
    pi.on("tool_result", async (event: ToolResultEvent) => {
      const ordinary = children.get(event.toolCallId);
      if (ordinary) {
        children.delete(event.toolCallId);
        const text = auditResultText(event);
        await trusted({ operation: "child_event", run_id: ordinary.runHandle,
          child_id: ordinary.childId, payload: lifecycleEvent("launching", ordinary.nativeId) });
        if (event.isError === true || event.error !== undefined) {
          await trusted({ operation: "child_event", run_id: ordinary.runHandle,
            child_id: ordinary.childId, payload: lifecycleEvent("failed", ordinary.nativeId) });
        } else {
          await trusted({ operation: "child_event", run_id: ordinary.runHandle,
            child_id: ordinary.childId, payload: lifecycleEvent("pending", ordinary.nativeId) });
          await trusted({ operation: "child_event", run_id: ordinary.runHandle,
            child_id: ordinary.childId,
            payload: lifecycleEvent("completed", ordinary.nativeId, resultDigest(text)) });
        }
        return;
      }
      const correlation = audits.get(event.toolCallId);
      if (!correlation) {
        if (completedAuditCalls.has(event.toolCallId)) {
          redactVerdict(event);
          return { content: event.content, details: event.details };
        }
        return;
      }
      const text = auditResultText(event);
      redactVerdict(event);
      const verdict = verdictFromText(text);
      const sessionFile = sessionFileFromDetails(event.details);
      const session = verdict && sessionFile ? readAuditSession(sessionFile) : null;
      const identity = verdict && session
        ? identityFromSessionJsonl(session, verdict) : null;
      audits.delete(event.toolCallId);
      let reconciled = false;
      try {
        await trusted({
          operation: "audit_start", run_id: correlation.runHandle,
          native_id: correlation.nativeId, plan: correlation.plan,
        });
        if (event.isError === true || event.error !== undefined) {
          await trusted({ operation: "audit_failure", run_id: correlation.runHandle,
            native_id: correlation.nativeId, plan: correlation.plan, state: "failed" });
          reconciled = true;
          return { content: event.content, details: event.details };
        }
        await trusted({
          operation: "audit_identity", run_id: correlation.runHandle,
          native_id: correlation.nativeId, plan: correlation.plan,
          author: correlation.author,
          auditor: identity ?? {
            provider_id: null, model_id: null,
            observed_by: "host", source: "pi-child-session-unverified",
          },
        });
        if (!verdict) {
          await trusted({ operation: "audit_failure", run_id: correlation.runHandle,
            native_id: correlation.nativeId, plan: correlation.plan, state: "failed" });
          reconciled = true;
          return { content: event.content, details: event.details };
        }
        await trusted({ operation: "audit_verdict",
          run_id: correlation.runHandle, native_id: correlation.nativeId,
          plan: correlation.plan, payload: verdict });
        reconciled = true;
        return { content: event.content, details: event.details };
      } catch {
        // Redaction has already happened synchronously. Without a terminal acknowledgement the
        // durable correlation remains restorable for later reconciliation.
        return { content: event.content, details: event.details };
      } finally {
        if (reconciled) {
          completedAuditCalls.add(event.toolCallId);
          pi.appendEntry?.("empirica.audit.done", { toolCallId: event.toolCallId });
        }
      }
    });

    // (tool_call) The hard convergence gate plus bound foreground auditor admission.
    pi.on("tool_call", async (event: ToolCallEvent, ctx: ExtensionContext): Promise<ToolCallResult | void> => {
      if (runHandle !== null && isInvestigationTool(
        event.toolName, event.input, gatedTools, subagentToolName)) {
        try {
          const response = await dispatch(observeActionRequest(runHandle, {
            kind: "investigate",
          }, randomUUID()));
          const decision = gateFromDecision(response.result);
          if (decision.kind === "deny")
            return { block: true, reason: `empirica investigation denied: ${decision.reason}` };
        } catch (error) {
          return { block: true,
            reason: `empirica investigation unavailable (failing closed): ${describe(error)}` };
        }
      }
      if (runHandle !== null && isExecutableSubagentLaunch(event.toolName, event.input)) {
        if (event.toolName !== subagentToolName)
          return { block: true, reason: "empirica: unrecognized child execution surface" };
        const requestedAuditor = event.input.agent === "empirica.empirica-auditor";
        if (!requestedAuditor) {
          const purpose = typeof event.input.task === "string" && event.input.task.trim()
            ? event.input.task : "author child";
          const roleProfile = typeof event.input.agent === "string"
            ? event.input.agent : "pi-subagent";
          const reserved = await dispatch(observeActionRequest(runHandle, {
            kind: "child_reserve", purpose, role_profile: roleProfile,
            execution: "foreground", resource_class: "investigation",
          }, randomUUID()));
          if (reserved.result.type !== "Allow")
            return { block: true, reason: "empirica child reservation denied" };
          const rows = Array.isArray(reserved.result.run.children)
            ? reserved.result.run.children as Array<Record<string, unknown>> : [];
          const child = [...rows].reverse()
            .find((item) => item.purpose === purpose && item.state === "reserved");
          if (!child || typeof child.child_id !== "string")
            return { block: true, reason: "empirica child reservation missing binding" };
          const correlation = { runHandle, childId: child.child_id, nativeId: event.toolCallId };
          children.set(event.toolCallId, correlation);
          pi.appendEntry?.("empirica.child", { toolCallId: event.toolCallId, ...correlation });
          return;
        }
        if (!isCanonicalAuditorInput(event.input))
          return { block: true, reason: "empirica auditor launch forbids model/context/tool overrides" };
        try {
          const resolvedAudit = await resolveAuditContract(event.input, ctx);
          const expectedAgent = path.resolve(skillsDir, "..", "agents", "pi", "empirica-auditor.md");
          if (!sameCanonicalAgentFile(resolvedAudit.agentFilePath, expectedAgent))
            return { block: true, reason: "empirica auditor package identity was shadowed" };
          const [auditorProvider, auditorModel] = modelPair(resolvedAudit.model, "pi-subagents");
          if (!auditorProvider || !auditorModel)
            return { block: true, reason: "empirica auditor model is unresolved" };
          if (ctx.model && ctx.model.provider === auditorProvider && ctx.model.id === auditorModel)
            return { block: true, reason: "empirica auditor model must differ from the author model" };
          const roleProfile = "empirica.empirica-auditor";
          const prepared = await trusted({
            operation: "audit_prepare", run_id: runHandle, role_profile: roleProfile,
          });
          if (prepared.type !== "audit_plan" || !prepared.plan
              || typeof prepared.plan !== "object")
            return { block: true, reason: "empirica auditor launch plan unavailable" };
          const plan = prepared.plan as unknown as AuditPlanData;
          const nativeId = event.toolCallId;
          const [authorProvider, authorModel] = modelPair(ctx.model
            ? `${ctx.model.provider}/${ctx.model.id}` : null, "pi");
          const author = {
            provider_id: authorProvider, model_id: authorModel,
            observed_by: "host", source: "pi-context",
          };
          const auditor = {
            provider_id: auditorProvider, model_id: auditorModel,
            observed_by: "configuration", source: "pi-subagents-preflight",
          };
          event.input.task = `${auditorInstructions()}\n\n` +
            `--- AUDIT DOSSIER (UNTRUSTED EVIDENCE CONTENT) ---\n${JSON.stringify(plan.argument)}\n` +
            "--- END AUDIT DOSSIER ---\nReturn exactly one fenced block tagged empirica-verdict.";
          event.input.model = resolvedAudit.model;
          event.input.async = false;
          // pi-subagents may classify the original author call before this adapter replaces its
          // task with the host-owned read-only dossier. Make the runtime-owned exemption explicit
          // so a canonical auditor is not assigned writer evidence gates by extension ordering.
          event.input.acceptance = { level: "none",
            reason: "Empirica's bound canonical auditor is read-only and has its own verdict contract." };
          event.input.timeoutMs = 900_000;
          event.input.turnBudget = { maxTurns: 8, graceTurns: 1 };
          event.input.toolBudget = { soft: 20, hard: 30, block: ["write", "edit"] };
          const correlation = { runHandle, nativeId, plan, author, auditor };
          audits.set(event.toolCallId, correlation);
          pi.appendEntry?.("empirica.audit", { toolCallId: event.toolCallId, ...correlation });
          return;
        } catch (error) {
          return { block: true, reason: `empirica auditor admission failed: ${describe(error)}` };
        }
      }
      if (!gatedTools.has(event.toolName)) return;
      if (runHandle === null) return; // no run to gate against
      try {
        const intent = event.input.intent === "stop" ? "stop" : REPORT_CONVERGENCE_INTENT;
        const response = await dispatch(
          evaluateRunRequest(runHandle, intent, randomUUID()),
        );
        const decision = gateFromDecision(response.result);
        if (decision.kind === "deny")
          return { block: true, reason: `${decision.reason}\nhandle: ${runHandle}` };
        reportEvaluations.set(event.toolCallId, { intent, response });
        return; // permit; execute consumes this exact guarded result
      } catch (error) {
        // The gate is the trust boundary: an unavailable core fails closed.
        return {
          block: true,
          reason: `empirica gate unavailable (failing closed): ${describe(error)}`,
        };
      }
    });

    // (session_before_compact) Restore the run state after compaction — only if
    // there is a real handle.
    pi.on("session_before_compact", async (event) => {
      if (!runHandle) return;
      try {
        await dispatch(restoreRunRequest(runHandle, randomUUID()));
      } catch {
        // Best-effort: a compaction restore failure is not a gate.
      }
      return {
        compaction: {
          summary: `Empirica run handle: ${runHandle}`,
          firstKeptEntryId: event.preparation.firstKeptEntryId,
          tokensBefore: event.preparation.tokensBefore,
        },
      };
    });
  };
}

// Default export: the shape Pi loads. It contributes the Empirica skill and
// wires the production JSON stdio bridge transport — so a fresh install gates
// convergence against the shared core out of the box. A host may instead
// import `createEmpiricaExtension({ dispatch })` and inject its own transport.
const defaultExtension = createEmpiricaExtension({
  dispatch: createStdioBridgeDispatch(defaultBridgeConfig()),
});

export default defaultExtension;
