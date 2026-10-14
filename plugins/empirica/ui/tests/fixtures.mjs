// The real-service fixtures the plugin vendors (what `make contract-fixtures` generates through the real service): the
// summary, every run's row, every run's sheet and the scenario -> run key table. Tests build from these, never from
// hand-written JSON.
import { readFileSync } from "node:fs";
import { URL } from "node:url";  // Node's, as in paths.mjs
import { FIXTURES as dir } from "./paths.mjs";

const read = (f) => JSON.parse(readFileSync(new URL(f, dir), "utf8"));
export const summary = read("summary.json"), rows = read("rows.json"), sheets = read("runs.json"), scenarios = read("scenarios.json");
export const byKey = new Map(rows.map((r) => [r.key, r]));
export const row = (scenario) => byKey.get(scenarios[scenario]);
export const sheet = (scenario) => sheets[scenarios[scenario]];
/** A runs-page line, as /api/runs gives it (the fields the row schema calls a line). */
export const line = (r) => ({ key: r.key, id: r.id, goal: r.goal, standing: r.standing, ask: r.ask, asked_at: r.asked_at, project: r.project,
  project_label: summary.projects.find((p) => p.id === r.project)?.label ?? r.project, host: r.session?.host ?? null, session: r.session?.label ?? null,
  updated: r.updated, generation: r.generation });
