// Empirica adapter for Pi (pi.dev).
//
// Per ADR-30/ADR-32 the Pi adapter is a *translator* over the empirica/v1
// contract: it maps Pi's native events into requests and maps the typed decision
// back onto Pi's enforcement/UI. It holds no convergence rules — those live in
// the host-neutral core, reached through the injected `dispatch` seam.
//
// What it wires (ADR-32):
//   * /empirica <goal>       -> StartRun            (opens/resumes a run)
//   * /empirica-status       -> GetRun              (reports the session's run)
//   * /report-convergence    -> EvaluateRun(report_convergence)  (command gate)
//   * tool_call interception -> EvaluateRun(report_convergence)  (the hard gate:
//        the `report_convergence` tool is blocked unless the core returns Allow)
//   * agent_settled          -> EvaluateRun(continue) + a best-effort follow-up
//        nudge. Pi's settled lifecycle is observational and cannot veto
//        completion, so this is explicitly *not* a hard gate.
//   * resources_discover     -> contributes the shared Empirica skill.
//
// State lives only behind the transport (the shared `~/.empirica-plugin` home via
// the JSON stdio bridge). This module writes nothing under .pi/.claude/repo; the
// only per-session state is the active run's opaque handle, held in memory.

import { fileURLToPath } from "node:url";
import * as path from "node:path";
import { createHash, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";

import type { Dispatch, Request, RunSelector } from "./contract.ts";
import { renderText } from "./obligations.ts";

export const KNOWLEDGE_ACTION_KINDS = new Set(["graph", "route", "evidence_leaf", "attribution", "freeze"]);


import type {
  ExtensionAPI,
  ExtensionContext,
  ToolCallEvent,
  ToolCallResult,
  ToolResultEvent,
} from "./pi-types.ts";
import { createStdioBridgeDispatch, defaultBridgeConfig } from "./stdio-transport.ts";
import {
  CONTINUE_INTENT,
  REPORT_CONVERGENCE_INTENT,
  REPORT_CONVERGENCE_TOOL,
  convergenceNotice,
  evaluateRunRequest,
  gateFromDecision,
  getRunRequest,
  settledFollowUp,
  startRunRequest,
  restoreRunRequest,
  statusNotice,
  parseModeFlags,
  type StartRunOptions,
} from "./translate.ts";

// plugins/empirica/adapters/pi/src/index.ts -> plugins/empirica/skills
export const DEFAULT_SKILLS_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
  "skills",
);

/** Resolves the run selector from Pi host context. Identity is host-shaped, so
 * this is a swappable seam; the default is a stable-per-workspace project id and
 * a per-session id, both filesystem-safe (the state store rejects separators). */
export type SelectorProvider = (ctx: ExtensionContext) => RunSelector;

export interface EmpiricaPiDeps {
  /** Bridge to the host-neutral Empirica core (empirica/v1). */
  dispatch: Dispatch;
  /** Absolute path to the empirica `skills` directory to contribute. */
  skillsDir?: string;
  /** Derive the run selector from Pi context (default: workspace hash + session id). */
  deriveSelector?: SelectorProvider;
  /** Tool names whose call is the convergence report and must be gated. */
  gatedTools?: readonly string[];
  /** Tool name for subagent spawn interception; undefined disables this hook. */
  subagentToolName?: string;
  /** StartRun options (max_passes, max_spawns, modes). */
  startRunOptions?: StartRunOptions;
}

function defaultSelectorProvider(): SelectorProvider {
  // One run per Pi extension load: project groups by workspace, session is unique
  // per load. Both are safe path segments so the machine-local state store accepts
  // them without the adapter reimplementing the core's identity rules.
  const session = randomUUID();
  return (ctx) => {
    const cwd = ctx.cwd ?? process.cwd();
    const project = createHash("sha256").update(cwd).digest("hex").slice(0, 16);
    return { project, session };
  };
}

/**
 * Build the Pi extension function from its dependencies.
 *
 * This is the real entry point for a host that wires the core: it injects a
 * `dispatch`. Tests inject a fake dispatch to prove registration, translation,
 * gating, and follow-up honesty without the core, a bridge, or the Pi runtime.
 */
export function createEmpiricaExtension(deps: EmpiricaPiDeps) {
  const skillsDir = deps.skillsDir ?? DEFAULT_SKILLS_DIR;
  const selectorOf = deps.deriveSelector ?? defaultSelectorProvider();
  const gatedTools = new Set(deps.gatedTools ?? [REPORT_CONVERGENCE_TOOL]);
  const subagentToolName = deps.subagentToolName ?? "subagent";
  const startOptions = deps.startRunOptions ?? {};

  return function empiricaExtension(pi: ExtensionAPI): void {
    // The active run's opaque handle, held only in memory for this session. It is
    // set by StartRun and read by every later command; nothing is persisted here.
    let runHandle: string | null = null;

    const dispatch = (request: Request) => Promise.resolve(deps.dispatch(request));

    // (resources) Contribute the shared Empirica skill so Pi discovers the
    // workflow instructions — the same resource Claude Code ships, no per-host fork.
    pi.on("resources_discover", () => ({ skillPaths: [skillsDir] }));

    pi.on("session_start", async (_event, ctx) => {
      const entries = ctx.sessionManager?.getEntries() ?? [];
      for (const entry of [...entries].reverse()) {
        const data = entry.data as { runHandle?: unknown } | undefined;
        if (entry.customType === "empirica.run" && typeof data?.runHandle === "string") { runHandle = data.runHandle; break; }
      }
    });

    const textResult = (result: { type: string; run?: { id?: string; goal?: string; modes?: unknown; contract?: import("./obligations.ts").ContractView } }, handle?: string) => {
      const view = result.run?.contract;
      const prefix = `${result.run?.goal ? `Goal: ${result.run.goal}\n` : ""}${result.run?.modes ? `Modes: ${JSON.stringify(result.run.modes)}\n` : ""}`;
      if (!view) return { content: [{ type: "text" as const, text: `${prefix}Empirica run ${handle ?? result.run?.id ?? "(unknown)"}: no contract yet (no graph).` }], details: result };
      return { content: [{ type: "text" as const, text: `${prefix}Empirica run handle: ${handle ?? result.run?.id ?? "(unknown)"}\n${renderText(view)}` }], details: result };
    };
    // Tool parameter schemas MUST be JSON-Schema objects with `type: "object"`: providers (Bedrock
    // rejects `{}` at request validation) require it. Found live while dogfooding; the fake-host
    // tests cannot see provider validation, so keep these explicit.
    const EMPTY_PARAMS = { type: "object", properties: {}, additionalProperties: false };
    const KNOWLEDGE_PARAMS = {
      type: "object",
      required: ["kind"],
      properties: { kind: { type: "string", enum: [...KNOWLEDGE_ACTION_KINDS].sort() } },
      additionalProperties: true,
    };
    if (pi.registerTool) {
      pi.registerTool({ name: REPORT_CONVERGENCE_TOOL, label: "Report convergence", description: "Ask Empirica to verify convergence.", parameters: EMPTY_PARAMS, async execute() {
        if (!runHandle) return { content: [{ type: "text", text: "No active Empirica run." }] };
        const response = await dispatch(evaluateRunRequest(runHandle, REPORT_CONVERGENCE_INTENT, randomUUID()));
        const decision = gateFromDecision(response.result);
        if (decision.kind === "deny") {
          const contract = decision.contract ? `\n${renderText(decision.contract)}` : "";
          throw new Error(`${decision.reason}${contract}\nhandle: ${runHandle}`);
        }
        return textResult(response.result);
      }});
      pi.registerTool({ name: "empirica_status", label: "Empirica status", description: "Show the current run handle and obligation contract.", parameters: EMPTY_PARAMS, async execute() {
        if (!runHandle) return { content: [{ type: "text", text: "No active Empirica run." }] };
        const response = await dispatch(getRunRequest(runHandle, randomUUID()));
        return textResult(response.result, runHandle);
      }});
      pi.registerTool({ name: "empirica_knowledge", label: "Empirica knowledge", description: "Submit an Empirica ObserveAction payload (kind + the same fields the Claude knowledge builders emit).", parameters: KNOWLEDGE_PARAMS, async execute(_id, params) {
        if (!runHandle) return { content: [{ type: "text", text: "No active Empirica run." }] };
        const action = params as Record<string, unknown>;
        const kind = String(action.kind ?? "graph");
        if (!KNOWLEDGE_ACTION_KINDS.has(kind)) throw new Error(`unknown empirica knowledge action: ${kind}`);
        const response = await dispatch({ protocol: "empirica/v1", request_id: randomUUID(), command: { type: "ObserveAction", run_id: runHandle, action: { kind, ...action } } });
        return textResult(response.result);
      }});
    }

    pi.registerCommand("empirica", {
      description: "Start an empirical-convergence run for the current goal (empirica).",
      handler: async (args, ctx) => {
        const parsed = parseModeFlags(args);
        const goal = parsed.goal || "(goal to be refined from the current task)";
        const modes = { ...startOptions.modes, ...parsed.modes };
        if (parsed.unknownFlags.length) ctx.ui.notify(`empirica: unknown mode flags ignored: ${parsed.unknownFlags.join(" ")}`, "warning");
        try {
          const result = (
            await dispatch(startRunRequest(selectorOf(ctx), goal, randomUUID(), { ...startOptions, modes, actor: authorActor(ctx) }))
          ).result;
          if (result.type === "Allow" || result.type === "Block") {
            runHandle = result.run.id; // remember the opaque handle for this session
            pi.appendEntry?.("empirica.run", { runHandle });
            const modeText = Object.keys(modes).length ? JSON.stringify(modes) : "{}";
            const contractText = result.run.contract ? `\n${renderText(result.run.contract)}` : "\nno contract yet (no graph).";
            // This is deliberately unconditional: the model needs the invocation even before a graph exists.
            pi.sendMessage?.({ customType: "empirica", content: `Empirica run handle: ${runHandle}\nGoal: ${goal}\nModes: ${modeText}\nUnknown flags: ${parsed.unknownFlags.length ? parsed.unknownFlags.join(" ") : "none"}\nFollow the empirica skill from Step 1.${contractText}` });
          }
          const notice = statusNotice(result);
          ctx.ui.notify(notice.text, notice.type);
        } catch (error) {
          ctx.ui.notify(`/empirica could not start a run: ${describe(error)}`, "error");
        }
      },
    });

    // (/empirica-status) Report the session's run.
    pi.registerCommand("empirica-status", {
      description: "Show the status of this session's empirica run.",
      handler: async (_args, ctx) => {
        if (runHandle === null) {
          ctx.ui.notify(
            "empirica: no active run in this session — start one with /empirica <goal>.",
            "info",
          );
          return;
        }
        try {
          const response = await dispatch(getRunRequest(runHandle, randomUUID()));
          const notice = statusNotice(response.result);
          ctx.ui.notify(notice.text, notice.type);
        } catch (error) {
          ctx.ui.notify(`/empirica-status failed: ${describe(error)}`, "error");
        }
      },
    });

    // (/report-convergence) The command form of the convergence gate.
    pi.registerCommand("report-convergence", {
      description:
        "Ask empirica whether this run may report convergence (gated by evidence).",
      handler: async (_args, ctx) => {
        if (runHandle === null) {
          ctx.ui.notify(
            "empirica: no active run in this session — nothing to report.",
            "info",
          );
          return;
        }
        try {
          const response = await dispatch(
            evaluateRunRequest(runHandle, REPORT_CONVERGENCE_INTENT, randomUUID()),
          );
          const notice = convergenceNotice(response.result);
          ctx.ui.notify(notice.text, notice.type);
        } catch (error) {
          // The command surfaces the failure; the enforced denial is the tool gate.
          ctx.ui.notify(`empirica: convergence check failed — ${describe(error)}`, "error");
        }
      },
    });

    // Child tickets are correlated by Pi's toolCallId, never shown to the author.
    const auditTickets = new Map<string, string>();
    const AUDIT_CONTRACT = "```empirica-verdict {\n  {verdict, nonce, argument_digest, claims_reviewed:[{claim_id, claim_digest, evidence_digest}], findings, ts}\n}```";
    const auditorInstructions = (): string => {
      const file = path.resolve(skillsDir, "..", "agents", "empirica-auditor.md");
      try {
        const raw = readFileSync(file, "utf8");
        return raw.replace(/^---[\s\S]*?---\s*/, "").trim();
      } catch { return "Review the supplied argument and return the required verdict block."; }
    };
    const verdictBlock = (value: unknown): Record<string, unknown> | null => {
      const text = typeof value === "string" ? value : JSON.stringify(value ?? "");
      const match = text.match(/```empirica-verdict\s*\n?([\s\S]*?)```/i);
      if (!match) return null;
      try { const parsed = JSON.parse(match[1].trim()); return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : null; } catch { return null; }
    };
    const resultText = (event: ToolResultEvent): string => {
      const value = event.content ?? event.result ?? event.error ?? "";
      if (typeof value === "string") return value;
      if (Array.isArray(value)) return value.map((x) => typeof x === "object" && x && "text" in x ? String((x as Record<string, unknown>).text) : String(x)).join("\n");
      return JSON.stringify(value);
    };
    const VERDICT_BLOCK = /```empirica-verdict\s*\n?[\s\S]*?```/gi;
    const redactVerdict = (event: ToolResultEvent): void => {
      const marker = "[empirica-verdict block recorded by the host]";
      if (Array.isArray(event.content)) {
        for (const part of event.content) {
          if (part && typeof part === "object" && typeof (part as { text?: unknown }).text === "string") {
            (part as { text: string }).text = (part as { text: string }).text.replace(VERDICT_BLOCK, marker);
          }
        }
        return;
      }
      if (typeof event.content === "string") event.content = event.content.replace(VERDICT_BLOCK, marker);
    };
    const appendResult = (event: ToolResultEvent, line: string): void => {
      if (Array.isArray(event.content)) { event.content.push({ type: "text", text: line }); return; }
      if (typeof event.content === "string") { event.content += `\n${line}`; return; }
      event.content = `${resultText(event)}\n${line}`;
    };
    pi.on("tool_result", async (event: ToolResultEvent) => {
      const nonce = auditTickets.get(event.toolCallId);
      if (!nonce || runHandle === null) return;
      auditTickets.delete(event.toolCallId);
      if (event.isError || event.error) {
        // The child never ran: give the reservation back and void the nonce (P-8).
        await dispatch({ protocol: "empirica/v1", request_id: randomUUID(), command: { type: "ObserveAction", run_id: runHandle, action: { kind: "void_spawn", nonce } } });
        appendResult(event, "Empirica: auditor launch failed — spawn reservation released, ticket voided.");
        return;
      }
      const parsed = verdictBlock(resultText(event));
      if (!parsed || parsed.nonce !== nonce) {
        // The child ran (budget is spent) but produced no usable verdict. The ticket simply never
        // gets a matching verdict, so it cannot satisfy coverage; the obligation stays open.
        appendResult(event, "Empirica: the auditor returned no valid empirica-verdict block; the audit obligation remains open. Spawn another auditor if budget remains.");
        return;
      }
      const response = await dispatch({ protocol: "empirica/v1", request_id: randomUUID(), command: { type: "ObserveAction", run_id: runHandle, action: { kind: "audit_verdict", ...parsed } } });
      redactVerdict(event); // the author never holds the nonce, not even after the fact
      const run = (response.result as { run?: { contract?: import("./obligations.ts").ContractView } }).run;
      const findings = Array.isArray(parsed.findings) && parsed.findings.length ? `\nFindings:\n- ${parsed.findings.join("\n- ")}` : "";
      const head = response.result.type === "Allow"
        ? `Empirica: host recorded the auditor's verdict (${parsed.verdict}).${findings}`
        : `Empirica: the auditor's verdict was not accepted — ${(response.result as { reason?: string; message?: string }).reason ?? (response.result as { message?: string }).message ?? response.result.type}.`;
      appendResult(event, run?.contract ? `${head}\n${renderText(run.contract)}` : head);
    });

    // (tool_call) The hard gate. A `report_convergence` tool call succeeds only
    // when the core returns Allow; a Block (or a closed-fault / transport failure)
    // blocks that single call with the reason. Non-gated tools pass untouched — the
    // adapter never round-trips the core for calls it does not gate.
    pi.on("tool_call", async (event: ToolCallEvent): Promise<ToolCallResult | void> => {
      if (event.toolName === subagentToolName && runHandle !== null && isExecutableSpawn(event.input)) {
        try {
          const reservation = await dispatch({ protocol: "empirica/v1", request_id: randomUUID(), command: { type: "ObserveAction", run_id: runHandle, action: { kind: "reserve_spawn" } } });
          if (reservation.result.type === "Block") return { block: true, reason: `${reservation.result.reason}${reservation.result.run.contract ? `\n${renderText(reservation.result.run.contract)}` : ""}` };
          if (reservation.result.type === "Fault") {
            if (reservation.result.code === "invalid_request") throw new Error(`Empirica reserve_spawn request bug: ${reservation.result.message ?? "invalid request"}`);
            if (reservation.result.fail_direction !== "open") return { block: true, reason: `empirica spawn gate unavailable (failing closed): ${reservation.result.message ?? "core fault"}` };
          }
          if (isAuditorSpawn(event.input)) {
            const model = actorModel(event.input);
            const ticket = await dispatch({ protocol: "empirica/v1", request_id: randomUUID(), command: { type: "ObserveAction", run_id: runHandle, action: { kind: "audit_ticket", actor: { model, harness: "pi", provider: "pi", source_type: "LLM_JUDGE", attribution: "declared" }, witnessed: false } } });
            if (ticket.result.type === "Fault" && ticket.result.code === "invalid_request") throw new Error(`Empirica audit_ticket request bug: ${ticket.result.message ?? "invalid request"}`);
            if (ticket.result.type === "Fault") return { block: true, reason: `empirica audit ticket unavailable: ${ticket.result.message ?? "core fault"}` };
            if (ticket.result.type === "Block") return { block: true, reason: ticket.result.reason };
            const nonce = (ticket.result as { run?: { ticket?: { nonce?: string } } }).run?.ticket?.nonce;
            if (nonce) {
              const argumentResponse = await dispatch({ protocol: "empirica/v1", request_id: randomUUID(), command: { type: "GetArgument", run_id: runHandle } });
              if (argumentResponse.result.type === "Fault" || argumentResponse.result.type === "Block") {
                await dispatch({ protocol: "empirica/v1", request_id: randomUUID(), command: { type: "ObserveAction", run_id: runHandle, action: { kind: "void_spawn", nonce } } });
                return { block: true, reason: "empirica audit argument unavailable" };
              }
              const argument = (argumentResponse.result as { run?: { argument?: { text?: string } } }).run?.argument;
              const text = typeof event.input.task === "string" ? event.input.task : "";
              event.input.task = `${auditorInstructions()}\n\n${argument?.text ?? ""}\n\nYour nonce: ${nonce}\n\nReturn exactly one fenced block tagged empirica-verdict containing JSON with verdict, nonce, argument_digest, claims_reviewed, findings, and ts.\n${AUDIT_CONTRACT}`.trim();
              // The verdict must come back in THIS tool result: the host decides the launch is
              // foreground. A detached run would return only an id here and the verdict would
              // surface as a notification the host cannot attribute to the ticket.
              event.input.async = false;
              auditTickets.set(event.toolCallId, nonce);
              pi.sendMessage?.({ customType: "empirica", content: "Auditor spawned; verdict is recorded by the host" });
            }
          }
        } catch (error) { return { block: true, reason: `empirica spawn gate unavailable (failing closed): ${describe(error)}` }; }
      }
      if (!gatedTools.has(event.toolName)) return;
      if (runHandle === null) return; // no run to gate against
      try {
        const response = await dispatch(
          evaluateRunRequest(runHandle, REPORT_CONVERGENCE_INTENT, randomUUID()),
        );
        const decision = gateFromDecision(response.result);
        if (decision.kind === "deny") {
          if (decision.kind === "deny") {
            const contract = decision.contract ? `\n${renderText(decision.contract)}` : "";
            return { block: true, reason: `${decision.reason}${contract}${decision.contract ? `\nhandle: ${runHandle}` : ""}` };
          }
        }
        return; // permit
      } catch (error) {
        // The gate is the trust boundary: an unavailable core fails closed.
        return {
          block: true,
          reason: `empirica gate unavailable (failing closed): ${describe(error)}`,
        };
      }
    });

    pi.on("session_before_compact", async (event) => {
      if (!runHandle) return;
      try {
        const response = await dispatch(restoreRunRequest(runHandle, randomUUID()));
        const run = response.result.type === "Allow" || response.result.type === "Block" ? response.result.run : undefined;
        if (!run?.contract) return;
        return { compaction: { summary: `Empirica run.goal: ${run.goal ?? "(unknown)"}\nEmpirica obligations (deterministic):\n${renderText(run.contract)}`, firstKeptEntryId: event.preparation.firstKeptEntryId, tokensBefore: event.preparation.tokensBefore, details: { runHandle, contract: run.contract } } };
      } catch { return; }
    });

    // (agent_settled) Observational only (ADR-32): Pi cannot veto completion here.
    // We evaluate the run and, if it is active with outstanding work, enqueue a
    // best-effort follow-up nudge. It never blocks and never throws.
    let lastNudgeKey: string | null = null;
    let nudgeCount = 0;
    let pausedNoticeSent = false;
    const maxNudges = Number.parseInt(process.env.EMPIRICA_PI_MAX_NUDGES ?? "3", 10) || 3;
    pi.on("agent_settled", async (event, _ctx: ExtensionContext) => {
      if (runHandle === null || isEmptySettledTurn(event)) return;
      try {
        const response = await dispatch(
          evaluateRunRequest(runHandle, CONTINUE_INTENT, randomUUID()),
        );
        const nudge = settledFollowUp(response.result);
        if (nudge !== null && typeof pi.sendUserMessage === "function") {
          const blocked = response.result as Extract<import("./contract.ts").Result, { type: "Block" }>;
          const key = `${blocked.type}:${blocked.reason}:${blocked.run.revision}:${"converged" in blocked ? blocked.converged : false}`;
          if (key === lastNudgeKey) return;
          lastNudgeKey = key;
          nudgeCount += 1;
          if (nudgeCount > maxNudges) {
            if (!pausedNoticeSent) { pausedNoticeSent = true; pi.sendUserMessage("empirica: nudge loop paused after the maximum reminders. Resume by continuing the run or calling report_convergence.", { deliverAs: "followUp" }); }
          } else pi.sendUserMessage(nudge, { deliverAs: "followUp" });
        }
      } catch {
        // Best-effort: a settled-time evaluation failure is not a gate and is
        // swallowed rather than surfaced as an error the user cannot act on.
      }
    });
  };
}

// The author's actor for ADR-24 decorrelation: sent only when a model is actually known, so the
// application never has to reject a StartRun over optional bookkeeping.
function authorActor(ctx: unknown): { model: string; harness: string; provider?: string } | undefined {
  const model = process.env.PI_MODEL || (ctx as { model?: { id?: string } }).model?.id;
  if (!model) return undefined;
  return { model, harness: "pi", provider: process.env.PI_PROVIDER || undefined };
}

function isExecutableSpawn(input: Record<string, unknown>): boolean {
  const keys = ["agent", "workflowScript", "resume"].filter((key) => input[key] !== undefined && input[key] !== null);
  return keys.length === 1;
}
function isAuditorSpawn(input: Record<string, unknown>): boolean {
  return JSON.stringify(input).toLowerCase().includes("empirica-auditor");
}
function actorModel(input: Record<string, unknown>): string {
  const model = input.model;
  if (typeof model === "string" && model.trim()) return model.trim();
  const agent = input.agent;
  if (typeof agent === "string" && agent.trim()) return agent.trim();
  if (agent && typeof agent === "object" && typeof (agent as Record<string, unknown>).model === "string") return String((agent as Record<string, unknown>).model);
  const definition = input.agentDefinition;
  if (definition && typeof definition === "object" && typeof (definition as Record<string, unknown>).model === "string") return String((definition as Record<string, unknown>).model);
  return "empirica-auditor";
}
function isEmptySettledTurn(event: unknown): boolean {
  if (!event || typeof event !== "object") return false;
  const value = event as Record<string, unknown>;
  if (value.aborted === true || value.cancelled === true) return true;
  const calls = value.toolCalls ?? value.tool_calls;
  // Pi may omit all turn fields for an idle/aborted model turn. Treat that
  // shape exactly like an explicit empty calls/text payload: it must not
  // manufacture a reminder (the lifecycle is observational, not a gate).
  if (calls === undefined && value.text === undefined && value.content === undefined) return true;
  if (Array.isArray(calls) && calls.length === 0 && !value.text && !value.content) return true;
  if ((typeof value.text === "string" && value.text.length === 0) && Array.isArray(calls) && calls.length === 0) return true;
  return false;
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// Default export: the shape Pi loads. It contributes the Empirica skill and wires
// the production JSON stdio bridge transport (ADR-32) — so a fresh install gates
// convergence against the shared `~/.empirica-plugin` home out of the box. A host
// may instead import `createEmpiricaExtension({ dispatch })` and inject its own
// transport (in-process, RPC). See README "Wiring the core".
const defaultExtension = createEmpiricaExtension({
  dispatch: createStdioBridgeDispatch(defaultBridgeConfig()),
});

export default defaultExtension;
