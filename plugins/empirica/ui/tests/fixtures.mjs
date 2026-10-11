// The real-service fixtures the plugin vendors (what `make contract-fixtures` generates through the v2 service): the
// store view, every run's document and the scenario -> run key table. Tests build from these, never from hand-written JSON.
import { readFileSync } from "node:fs";

const dir = new URL("../../vendor/contracts/empirica-ui-view/v1/fixtures/", import.meta.url);
const read = (f) => JSON.parse(readFileSync(new URL(f, dir), "utf8"));
export const view = read("view.json"), details = read("runs.json"), scenarios = read("scenarios.json");
export const rows = view.projects.flatMap((p) => p.runs.map((row) => ({ project: p, row })));
export const byKey = new Map(rows.map(({ row }) => [row.key, row]));
export const projectOf = new Map(rows.map(({ row, project }) => [row.key, project]));
export const row = (scenario) => byKey.get(scenarios[scenario]);
