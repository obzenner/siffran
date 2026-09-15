// Empirica adapter for Pi (pi.dev) — v2-only, radically simplified (D6-C C3).
//
// The Pi adapter is a *translator* over the empirica/v2 contract: it maps Pi's
// native events into requests and maps the guarded typed decision back onto
// Pi's enforcement/UI. It holds no convergence rules — those live in the
// host-neutral core, reached through the injected `dispatch` seam.
//
// Minimal Pi surfaces (D6 strict boundary):
//   * resources_discover     -> contributes the shared Empirica skill.
//   * /empirica <goal>        -> reject before StartRun because this exact profile cannot progress
//   * empirica_status tool   -> RestoreRun(handle) or ResolveRun(selector)
//        (reports only id/status; a restored handle wins across extension reload)
//   * report_convergence tool -> EvaluateRun(report_convergence) (hard gate:
//        the tool is blocked unless the core returns a guarded Allow; Block,
//        Inert, open or closed Fault, and transport errors all deny)
//   * tool_call interception  -> EvaluateRun(report_convergence)  (the hard gate)
//        + executable subagent launch local fail-closed (D8-owned, no dispatch)
//   * session_before_compact  -> RestoreRun           (only if a real handle)
//
// No audit/ticket/nonce/spawn pipeline, no nudge, no knowledge tool, no v1
// obligations contract. State lives only behind the transport; the only
// per-session state is the active run's opaque handle, held in memory.

import { fileURLToPath } from "node:url";
import * as path from "node:path";
import { createHash, randomUUID } from "node:crypto";

import type { Dispatch, Request, Response, RunSelector } from "./contract.ts";
import { assertResponse } from "./guard.ts";
import type {
  ExtensionAPI,
  ExtensionContext,
  ToolCallEvent,
  ToolCallResult,
} from "./pi-types.ts";
import { createStdioBridgeDispatch, defaultBridgeConfig } from "./stdio-transport.ts";
import {
  REPORT_CONVERGENCE_INTENT,
  REPORT_CONVERGENCE_TOOL,
  convergenceNotice,
  evaluateRunRequest,
  gateFromDecision,
  isExecutableSubagentLaunch,
  resolveRunRequest,
  restoreRunRequest,
  statusNotice,
  subagentUnsupportedReason,
} from "./translate.ts";

// plugins/empirica/adapters/pi/src/index.ts -> plugins/empirica/skills
export const DEFAULT_SKILLS_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
  "skills",
);

/** Resolves the run selector from Pi host context. */
export type SelectorProvider = (ctx: ExtensionContext) => RunSelector;

export interface EmpiricaPiDeps {
  /** Bridge to the host-neutral Empirica core (empirica/v2). */
  dispatch: Dispatch;
  /** Absolute path to the empirica `skills` directory to contribute. */
  skillsDir?: string;
  /** Derive the run selector from Pi context (default: workspace hash + session id). */
  deriveSelector?: SelectorProvider;
  /** Tool names whose call is the convergence report and must be gated. */
  gatedTools?: readonly string[];
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

  return function empiricaExtension(pi: ExtensionAPI): void {
    // The active run's opaque handle, held only in memory for this session.
    let runHandle: string | null = null;

    // Guarded dispatch: every response passes through the central runtime guard
    // before ANY gate or render. A malformed/partial/unknown/mismatched response
    // throws — the hard gate catches and fails closed.
    const dispatch = async (request: Request): Promise<Response> => {
      const response = await Promise.resolve(deps.dispatch(request));
      assertResponse(response, request.request_id);
      return response;
    };

    // (resources) Contribute the shared Empirica skill so Pi discovers the
    // workflow instructions — the same resource Claude Code ships, no per-host fork.
    pi.on("resources_discover", () => ({ skillPaths: [skillsDir] }));

    // (session_start) Restore the run handle from persisted entries.
    pi.on("session_start", async (_event, ctx) => {
      const entries = ctx.sessionManager?.getEntries() ?? [];
      for (const entry of entries) {
        const data = entry.data as { runHandle?: unknown } | undefined;
        if (entry.customType === "empirica.run" && typeof data?.runHandle === "string")
          runHandle = data.runHandle;
      }
    });

    // Tool parameter schemas MUST be JSON-Schema objects with `type: "object"`.
    const EMPTY_PARAMS = { type: "object", properties: {}, additionalProperties: false };
    if (pi.registerTool) {
      pi.registerTool({
        name: REPORT_CONVERGENCE_TOOL,
        label: "Report convergence",
        description: "Ask Empirica to verify convergence.",
        parameters: EMPTY_PARAMS,
        async execute() {
          if (!runHandle)
            return { content: [{ type: "text", text: "No active Empirica run." }] };
          const response = await dispatch(
            evaluateRunRequest(runHandle, REPORT_CONVERGENCE_INTENT, randomUUID()),
          );
          const decision = gateFromDecision(response.result);
          if (decision.kind === "deny")
            throw new Error(`${decision.reason}\nhandle: ${runHandle}`);
          const notice = convergenceNotice(response.result);
          return { content: [{ type: "text", text: notice.text }] };
        },
      });

      pi.registerTool({
        name: "empirica_status",
        label: "Empirica status",
        description: "Show the current run id and status only.",
        parameters: EMPTY_PARAMS,
        async execute(_id, _params, _signal, _onUpdate, ctx) {
          const request = runHandle
            ? restoreRunRequest(runHandle, randomUUID())
            : resolveRunRequest(selectorOf(ctx), randomUUID());
          const response = await dispatch(request);
          const notice = statusNotice(response.result);
          return { content: [{ type: "text", text: notice.text }] };
        },
      });
    }

    pi.registerCommand("empirica", {
      description: "Explain why the current Pi v2 profile cannot run Empirica to convergence.",
      handler: async (_args, ctx) => {
        ctx.ui.notify(
          "Empirica cannot start on pi@0.84.1: this profile exposes status and convergence reporting only; author actions and the bound audit lifecycle are unavailable. No run was created.",
          "error",
        );
      },
    });

    // (tool_call) The hard gate. A `report_convergence` tool call succeeds only
    // when the core returns a guarded Allow; a Block, an Inert (run gone), an
    // open or closed Fault, or a transport error blocks that single call with the
    // reason. Non-gated tools pass untouched.
    //
    // Additionally, an executable `subagent` tool launch while a real Empirica
    // run is active is denied locally (fail-closed) as unsupported — the
    // foreground-only pi@0.84.1 profile has no child-admission (D8) capability.
    // No dispatch, no child protocol/state; read-only management calls and
    // no-handle launches are inert.
    pi.on("tool_call", async (event: ToolCallEvent): Promise<ToolCallResult | void> => {
      // Local fail-closed for executable subagent launches (D8-owned).
      if (runHandle !== null && isExecutableSubagentLaunch(event.toolName, event.input)) {
        return { block: true, reason: subagentUnsupportedReason() };
      }
      if (!gatedTools.has(event.toolName)) return;
      if (runHandle === null) return; // no run to gate against
      try {
        const response = await dispatch(
          evaluateRunRequest(runHandle, REPORT_CONVERGENCE_INTENT, randomUUID()),
        );
        const decision = gateFromDecision(response.result);
        if (decision.kind === "deny")
          return { block: true, reason: `${decision.reason}\nhandle: ${runHandle}` };
        return; // permit
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
