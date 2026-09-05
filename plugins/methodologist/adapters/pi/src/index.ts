// Turnkey Methodologist adapter for Pi 0.84.1.
//
// Explicit `/think <name>` requests go straight through methodologist/v1 to the
// host-neutral core bridge. Bare `/think` delegates semantic selection to the
// current model, which enters that bridge through `methodologist_select`.
// `/think --simple <intent>` is deliberately thinner: one user prompt tells the
// model to execute the shared skill directly, without bridge/UI/workflow state.
// No mode contains a keyword router or duplicate methodology instructions.

import { randomUUID } from "node:crypto";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import type { Dispatch, MethodologySelected, Request, Response } from "./contract.ts";
import { PiHumanPort } from "./human-port.ts";
import type { ExtensionAPI, ExtensionContext, ToolResult } from "./pi-types.ts";
import { PiWidgetTaskTracker } from "./task-tracker.ts";
import { createStdioBridgeDispatch, defaultBridgeConfig } from "./stdio-transport.ts";
import { applyResult, parseThinkInvocation, selectMethodologyRequest } from "./translate.ts";

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

interface CandidateInput {
  name: string;
  rationale: string;
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
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

function bareSelectionPrompt(skillDir: string): string {
  const thinkDir = path.join(skillDir, "think");
  return [
    "Run the shared Methodologist selection step for the current task.",
    `Read ${path.join(thinkDir, "SKILL.md")} and first obey its stance requirement.`,
    `Then read ${path.join(thinkDir, "registry.json")} and semantically compare the current task against every use_when entry.`,
    "Do not use keyword matching or invent a methodology.",
    `If one methodology best addresses the primary uncertainty, call ${SELECT_TOOL} with its exact registry name and a one-line reason.`,
    `If genuinely ambiguous, call ${SELECT_TOOL} with exactly two candidates (name + rationale); the tool will ask the human.`,
    "After the tool returns the validated six-phase plan, continue with the selected methodology exactly as the shared skill instructs.",
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

    pi.registerTool({
      name: SELECT_TOOL,
      label: "Methodologist Select",
      description:
        "Enter the validated Methodologist bridge after semantically selecting from the shared registry. Supply one methodology, or exactly two candidates when genuinely ambiguous.",
      parameters: {
        type: "object",
        properties: {
          methodology: { type: "string" },
          reason: { type: "string" },
          candidates: {
            type: "array",
            minItems: 2,
            maxItems: 2,
            items: {
              type: "object",
              required: ["name", "rationale"],
              properties: {
                name: { type: "string" },
                rationale: { type: "string" },
              },
            },
          },
        },
      },
      async execute(_toolCallId, params, _signal, _onUpdate, ctx): Promise<ToolResult> {
        try {
          let methodology =
            typeof params.methodology === "string" ? params.methodology.trim() : "";
          let reason = typeof params.reason === "string" ? params.reason.trim() : "";
          const candidates = Array.isArray(params.candidates)
            ? params.candidates.filter(
                (value): value is CandidateInput =>
                  value !== null &&
                  typeof value === "object" &&
                  typeof (value as CandidateInput).name === "string" &&
                  typeof (value as CandidateInput).rationale === "string",
              )
            : [];

          if (!methodology && candidates.length === 2) {
            const labels = candidates.map(
              (candidate) => `${candidate.name} — ${candidate.rationale}`,
            );
            const picked = await new PiHumanPort(ctx.ui).choose(
              "Two methodologies fit. Which addresses the primary uncertainty?",
              labels,
            );
            const chosen = candidates[labels.indexOf(picked)];
            if (chosen === undefined) throw new Error("the selected candidate was invalid");
            methodology = chosen.name;
            reason = chosen.rationale;
          }
          if (!methodology || !reason) {
            throw new Error("provide methodology + reason, or exactly two candidates");
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
          throw new Error(`methodologist selection failed: ${describe(error)}`);
        }
      },
    });

    pi.registerCommand("think", {
      description: "Select and execute a formal reasoning methodology (methodologist).",
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
        if (parsed.requestedMethodology === null) {
          // Commands bypass skill expansion in Pi. Hand the semantic decision to
          // the model explicitly, while pointing it at the same shared resources.
          pi.sendUserMessage(bareSelectionPrompt(skillsDir));
          return;
        }
        try {
          await runNamed(
            parsed.requestedMethodology,
            "Explicitly requested by the user.",
            ctx,
          );
        } catch (error) {
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
