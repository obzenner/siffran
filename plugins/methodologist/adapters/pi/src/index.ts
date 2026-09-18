// Turnkey Methodologist adapter for Pi 0.84.1.
//
// Bare `/think` retrieves the complete registry catalog through
// methodologist/v1, asks the human to choose with Pi's native selector, and then
// starts the selected shared methodology. Explicit `/think <name>` remains a
// shortcut. `/think --simple <intent>` is the deliberate non-interactive path:
// one user prompt asks the model to select and execute directly from the shared
// skill and registry. No mode contains a keyword router or duplicate methodology
// instructions.

import { randomUUID } from "node:crypto";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import type {
  Dispatch,
  MethodologyCatalogEntry,
  MethodologySelected,
  Request,
  Response,
} from "./contract.ts";
import { PiHumanPort } from "./human-port.ts";
import type { ExtensionAPI, ExtensionContext, ToolResult } from "./pi-types.ts";
import { PiWidgetTaskTracker } from "./task-tracker.ts";
import { createStdioBridgeDispatch, defaultBridgeConfig } from "./stdio-transport.ts";
import {
  applyResult,
  listMethodologiesRequest,
  parseThinkInvocation,
  selectMethodologyRequest,
} from "./translate.ts";

export const DEFAULT_SKILLS_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
  "skills",
);

const SELECT_TOOL = "methodologist_select";

export interface MethodologistPiDeps {
  /** Test/host seam. The default package supplies the real stdio bridge. */
  dispatch: Dispatch;
  skillsDir?: string;
  /** Optional canonicalisation aid retained for embedding hosts/tests. */
  knownMethodologies?: readonly string[];
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// The shipped bridge is stateless: nothing advances or clears a phase widget
// after a turn. Clear any stale one on teardown, on hot-reload, and on error so
// an interrupted (or pre-fix) run cannot strand progress below the editor.
function clearPhaseWidget(ctx: ExtensionContext): void {
  new PiWidgetTaskTracker(ctx.ui).clear();
}

function phasePlan(result: MethodologySelected): string {
  const lines = result.phases.map((phase, index) => {
    const number = typeof phase.number === "number" ? phase.number : index + 1;
    const title =
      typeof phase.title === "string" && phase.title.length > 0
        ? phase.title
        : `Phase ${number}`;
    return `${number}. ${title}`;
  });
  return `Using **${result.methodology}**: ${result.reason}\n\nPhases:\n${lines.join("\n")}`;
}

function executionPrompt(skillDir: string, result: MethodologySelected): string {
  const thinkDir = path.join(skillDir, "think");
  const phaseLines = result.phases.map((phase, index) =>
    `${typeof phase.number === "number" ? phase.number : index + 1}. ${phase.title ?? `Phase ${index + 1}`}`,
  );
  return [
    `Run the user-selected Methodologist methodology: ${result.methodology}.`,
    `Read ${path.join(thinkDir, "SKILL.md")} and obey its stance and execution rules.`,
    `Then read ${path.join(thinkDir, "methodologies", `${result.methodology}.md`)} and execute all phases for the current task context.`,
    "The selection is already complete. Do not invoke /think, show the catalog again, auto-select, or call methodologist_select.",
    "Validated phase plan:",
    ...phaseLines,
  ].join("\n");
}

function simpleModePrompt(skillDir: string, intent: string): string {
  const thinkDir = path.join(skillDir, "think");
  return [
    "Handle this as a Methodologist simple-mode request directly in the current agent turn.",
    "Do not invoke any slash command or Methodologist tool, and do not create or persist workflow/task state or host UI.",
    `Read and follow the shared skill at ${path.join(thinkDir, "SKILL.md")}, including its simple-mode rules.`,
    `Use the shared registry at ${path.join(thinkDir, "registry.json")} to select semantically, never by keyword routing.`,
    "Load the selected shared methodology file and execute it exactly as the skill directs; do not invent or reproduce methodology instructions from this prompt.",
    "The user's intent is:",
    intent,
  ].join("\n");
}

export function createMethodologistExtension(deps: MethodologistPiDeps) {
  const skillsDir = deps.skillsDir ?? DEFAULT_SKILLS_DIR;
  const known = deps.knownMethodologies ?? [];

  return function methodologistExtension(pi: ExtensionAPI): void {
    const dispatch = (request: Request): Promise<Response> =>
      Promise.resolve(deps.dispatch(request));

    pi.on("resources_discover", () => ({ skillPaths: [skillsDir] }));

    // Clear any lingering phase widget when the session ends or hot-reloads, so a
    // widget left by an interrupted or pre-fix run does not persist (ADR-32).
    pi.on("session_shutdown", (_event, ctx) => clearPhaseWidget(ctx));
    pi.on("session_start", (event, ctx) => {
      if ((event as { reason?: string } | null)?.reason === "reload") {
        clearPhaseWidget(ctx);
      }
    });

    const runNamed = async (
      methodology: string,
      reason: string,
      ctx: ExtensionContext,
    ): Promise<Response["result"]> => {
      const tracker = new PiWidgetTaskTracker(ctx.ui);
      const human = new PiHumanPort(ctx.ui);
      const renderDeps = { tracker, human, ui: ctx.ui };
      let response = await dispatch(
        selectMethodologyRequest(reason, methodology, randomUUID()),
      );
      let outcome = await applyResult(response.result, renderDeps);

      // Preserve the contract-owned ambiguity path for injected/custom cores.
      if (outcome.kind === "choice") {
        response = await dispatch(
          selectMethodologyRequest(reason, outcome.chosen, randomUUID()),
        );
        await applyResult(response.result, renderDeps);
      }
      return response.result;
    };

    const chooseFromCatalog = async (ctx: ExtensionContext): Promise<MethodologyCatalogEntry> => {
      const response = await dispatch(listMethodologiesRequest(randomUUID()));
      if (response.result.type !== "MethodologyCatalog") {
        throw new Error(`core returned ${response.result.type} instead of MethodologyCatalog`);
      }
      const entries = response.result.methodologies;
      const labels = entries.map(
        (entry) => `${entry.name} — Use when: ${entry.use_when} — Prevents: ${entry.prevents}`,
      );
      const picked = await new PiHumanPort(ctx.ui).choose(
        "Which formal methodology should I run?",
        labels,
      );
      const chosen = entries[labels.indexOf(picked)];
      if (chosen === undefined) throw new Error("the selected methodology was invalid");
      return chosen;
    };

    pi.registerTool({
      name: SELECT_TOOL,
      label: "Methodologist Select",
      description:
        "Validate a Methodologist methodology already chosen from the shared registry and return its canonical phase plan.",
      parameters: {
        type: "object",
        required: ["methodology", "reason"],
        properties: {
          methodology: { type: "string" },
          reason: { type: "string" },
        },
      },
      async execute(_toolCallId, params, _signal, _onUpdate, ctx): Promise<ToolResult> {
        try {
          const methodology =
            typeof params.methodology === "string" ? params.methodology.trim() : "";
          const reason = typeof params.reason === "string" ? params.reason.trim() : "";
          if (!methodology || !reason) {
            throw new Error("provide the methodology chosen by the user and a reason");
          }

          const result = await runNamed(methodology, reason, ctx);
          if (result.type !== "MethodologySelected") {
            throw new Error(`core returned ${result.type}`);
          }
          return {
            content: [{ type: "text", text: phasePlan(result) }],
            details: { methodology: result.methodology, phases: result.phases },
          };
        } catch (error) {
          clearPhaseWidget(ctx);
          throw new Error(`methodologist selection failed: ${describe(error)}`);
        }
      },
    });

    pi.registerCommand("think", {
      description: "Browse formal methodologies, choose one, and run it (methodologist).",
      handler: async (args, ctx) => {
        const simple = args.match(/^\s*--simple(?:\s+([\s\S]*?))?\s*$/);
        if (simple !== null) {
          const intent = simple[1]?.trim() ?? "";
          if (intent.length === 0) {
            ctx.ui.notify("Usage: /think --simple <intent>", "warning");
            return;
          }
          pi.sendUserMessage(simpleModePrompt(skillsDir, intent));
          return;
        }

        const parsed = parseThinkInvocation(args, known);
        try {
          const methodology = parsed.requestedMethodology === null
            ? (await chooseFromCatalog(ctx)).name
            : parsed.requestedMethodology;
          const result = await runNamed(
            methodology,
            parsed.requestedMethodology === null
              ? "Selected by the user from the complete methodology catalog."
              : "Explicitly requested by the user.",
            ctx,
          );
          if (result.type !== "MethodologySelected") {
            throw new Error(`core returned ${result.type}`);
          }
          pi.sendUserMessage(executionPrompt(skillsDir, result));
        } catch (error) {
          clearPhaseWidget(ctx);
          ctx.ui.notify(`/think could not run: ${describe(error)}`, "error");
        }
      },
    });
  };
}

// Turnkey default: production stdio transport to the shared host-neutral core.
const defaultExtension = createMethodologistExtension({
  dispatch: createStdioBridgeDispatch(defaultBridgeConfig()),
});

export default defaultExtension;
