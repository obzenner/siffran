// Methodologist adapter for Pi (pi.dev).
//
// Per ADR-32 the Pi adapter: (1) registers `/think`, (2) contributes the
// host-neutral methodology resources via `resources_discover`, and (3) renders
// phase progress and human choice through Pi capabilities rather than Claude
// task tools. It translates a Pi invocation into a `methodologist/v1` Request
// and renders the Response; the methodology *rules* stay in the core, reached
// through the injected `dispatch` seam (ADR-30).

import { fileURLToPath } from "node:url";
import * as path from "node:path";
import { randomUUID } from "node:crypto";

import type { Dispatch } from "./contract.ts";
import type { ExtensionAPI } from "./pi-types.ts";
import { PiHumanPort } from "./human-port.ts";
import { PiWidgetTaskTracker } from "./task-tracker.ts";
import {
  applyResult,
  parseThinkInvocation,
  selectMethodologyRequest,
} from "./translate.ts";

// plugins/methodologist/adapters/pi/src/index.ts -> plugins/methodologist/skills
export const DEFAULT_SKILLS_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
  "skills",
);

export interface MethodologistPiDeps {
  /** Bridge to the host-neutral Methodologist core. */
  dispatch: Dispatch;
  /** Absolute path to the methodologist `skills` directory to contribute. */
  skillsDir?: string;
  /** Known methodology names, so `/think <name>` is parsed as a request. */
  knownMethodologies?: readonly string[];
}

/**
 * Build the Pi extension function from its dependencies.
 *
 * This is the real entry point for a host that wires the core: it injects a
 * `dispatch`. Tests inject a fake dispatch to prove registration and
 * translation without the core or the Pi runtime.
 */
export function createMethodologistExtension(deps: MethodologistPiDeps) {
  const skillsDir = deps.skillsDir ?? DEFAULT_SKILLS_DIR;
  const known = deps.knownMethodologies ?? [];

  return function methodologistExtension(pi: ExtensionAPI): void {
    // (2) Contribute the shared methodology skill so Pi discovers `/think`'s
    // instructions and the methodology files — the same resources Claude Code
    // ships, with no per-host fork.
    pi.on("resources_discover", () => ({ skillPaths: [skillsDir] }));

    // (1) Register the command.
    pi.registerCommand("think", {
      description:
        "Select and execute a formal reasoning methodology (methodologist).",
      handler: async (args, ctx) => {
        const tracker = new PiWidgetTaskTracker(ctx.ui);
        const human = new PiHumanPort(ctx.ui);
        const renderDeps = { tracker, human, ui: ctx.ui };
        const { intent, requestedMethodology } = parseThinkInvocation(args, known);

        try {
          const first = await deps.dispatch(
            selectMethodologyRequest(intent, requestedMethodology, randomUUID()),
          );
          let outcome = await applyResult(first.result, renderDeps);

          // (3) On ambiguity, the human picks, then we re-dispatch a resolved
          // selection. The adapter never invents the decision.
          if (outcome.kind === "choice") {
            const resolved = await deps.dispatch(
              selectMethodologyRequest(intent, outcome.chosen, randomUUID()),
            );
            outcome = await applyResult(resolved.result, renderDeps);
          }
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          ctx.ui.notify(`/think could not run: ${message}`, "error");
        }
      },
    });
  };
}

// Default export: the shape Pi loads. A host must configure `dispatch` before
// selection/phase progression can run; until then the extension still
// contributes the methodology resources (so the `/think` skill is available to
// the agent) and reports the unconfigured core honestly rather than faking a
// decision. See README "Wiring the core".
const defaultExtension = createMethodologistExtension({
  dispatch: () => {
    throw new Error(
      "methodologist-pi: no core dispatch configured — import " +
        "createMethodologistExtension({ dispatch }) and wire it to the " +
        "host-neutral Methodologist core (see README).",
    );
  },
});

export default defaultExtension;
