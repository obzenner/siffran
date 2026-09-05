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

import type { Dispatch, Request, RunSelector } from "./contract.ts";
import type {
  ExtensionAPI,
  ExtensionContext,
  ToolCallEvent,
  ToolCallResult,
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
  statusNotice,
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
  const startOptions = deps.startRunOptions ?? {};

  return function empiricaExtension(pi: ExtensionAPI): void {
    // The active run's opaque handle, held only in memory for this session. It is
    // set by StartRun and read by every later command; nothing is persisted here.
    let runHandle: string | null = null;

    const dispatch = (request: Request) => Promise.resolve(deps.dispatch(request));

    // (resources) Contribute the shared Empirica skill so Pi discovers the
    // workflow instructions — the same resource Claude Code ships, no per-host fork.
    pi.on("resources_discover", () => ({ skillPaths: [skillsDir] }));

    // (/empirica) Open or resume a run for this session.
    pi.registerCommand("empirica", {
      description: "Start an empirical-convergence run for the current goal (empirica).",
      handler: async (args, ctx) => {
        const goal = args.trim() || "(goal to be refined from the current task)";
        try {
          const result = (
            await dispatch(startRunRequest(selectorOf(ctx), goal, randomUUID(), startOptions))
          ).result;
          if (result.type === "Allow" || result.type === "Block") {
            runHandle = result.run.id; // remember the opaque handle for this session
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

    // (tool_call) The hard gate. A `report_convergence` tool call succeeds only
    // when the core returns Allow; a Block (or a closed-fault / transport failure)
    // blocks that single call with the reason. Non-gated tools pass untouched — the
    // adapter never round-trips the core for calls it does not gate.
    pi.on("tool_call", async (event: ToolCallEvent): Promise<ToolCallResult | void> => {
      if (!gatedTools.has(event.toolName)) return;
      if (runHandle === null) return; // no run to gate against
      try {
        const response = await dispatch(
          evaluateRunRequest(runHandle, REPORT_CONVERGENCE_INTENT, randomUUID()),
        );
        const decision = gateFromDecision(response.result);
        if (decision.kind === "deny") {
          return { block: true, reason: decision.reason };
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

    // (agent_settled) Observational only (ADR-32): Pi cannot veto completion here.
    // We evaluate the run and, if it is active with outstanding work, enqueue a
    // best-effort follow-up nudge. It never blocks and never throws.
    pi.on("agent_settled", async (_event, _ctx: ExtensionContext) => {
      if (runHandle === null) return;
      try {
        const response = await dispatch(
          evaluateRunRequest(runHandle, CONTINUE_INTENT, randomUUID()),
        );
        const nudge = settledFollowUp(response.result);
        if (nudge !== null && typeof pi.sendUserMessage === "function") {
          pi.sendUserMessage(nudge, { deliverAs: "followUp" });
        }
      } catch {
        // Best-effort: a settled-time evaluation failure is not a gate and is
        // swallowed rather than surfaced as an error the user cannot act on.
      }
    });
  };
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
