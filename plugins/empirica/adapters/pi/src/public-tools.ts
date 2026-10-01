import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import * as path from "node:path";

export interface DecisionControls {
  actions: Record<string, string>;
  budgets: Record<string, { label: string; maximum: number }>;
}

export interface PublicToolsProjection {
  definitions: Record<string, { title: string; description: string }>;
  schemas: { host_handle: Record<string, Record<string, unknown>> };
  host_profiles: Record<string, { delegation_env: string }>;
  start_refusal_codes: string[];
  recovery: Record<string, { message: string; sections: string[]; next_actions: string[] }>;
  governance_decisions: {
    controls: DecisionControls;
    confirmation: { title: string; actions: string[] };
    human_wait_notice: string;
  };
}

const PUBLIC_TOOLS_PATH = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..", "..",
  "contracts", "empirica", "v2", "public-tools.json",
);

export const PUBLIC_TOOLS = JSON.parse(
  readFileSync(PUBLIC_TOOLS_PATH, "utf8"),
) as PublicToolsProjection;
