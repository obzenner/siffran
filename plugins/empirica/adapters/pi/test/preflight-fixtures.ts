// Real pi-subagents preflight responses (written by scripts/capture_pi_preflight_fixture.mjs) bound
// to this checkout: `{{AUDITOR_FILE}}` becomes the packaged auditor path.

import { readdirSync, readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = path.resolve(HERE, "..", "..", "..");

/** The packaged, canonical Pi auditor file. */
export const AUDITOR = path.join(PLUGIN, "agents", "pi", "empirica-auditor.md");

export interface Captured { input: Record<string, unknown>; response?: Record<string, any>; threw?: string }
export interface PreflightFixture {
  version: string;
  cases: Record<"canonical_auditor" | "unknown_agent" | "unservable_model", Captured>;
}

export const FIXTURES: PreflightFixture[] = readdirSync(path.join(HERE, "fixtures"))
  .filter((name) => /^preflight-.*\.json$/.test(name)).sort()
  .map((name) => JSON.parse(readFileSync(path.join(HERE, "fixtures", name), "utf8")) as PreflightFixture);

export function fixtureFor(version: string): PreflightFixture {
  const found = FIXTURES.find((fixture) => fixture.version === version);
  if (found === undefined) throw new Error(`no preflight fixture for ${version}`);
  return found;
}

/** A fresh copy of a captured response with its placeholders bound to this checkout. */
export function bound(captured: Captured): Record<string, any> {
  return JSON.parse(JSON.stringify(captured.response).replaceAll("{{AUDITOR_FILE}}", AUDITOR));
}
