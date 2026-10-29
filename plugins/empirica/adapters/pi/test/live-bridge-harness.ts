// Shared live-bridge Python service harness (PR 09).
//
// The live-bridge files drive the real stdio bridge (`bridge.py`) and the real private bridge
// against a fresh on-disk machine home and git repo. The rejection flows moved to their own
// file (`live-bridge-rejections.test.ts`) so every test file stays inside node --test's 30 s
// per-file cap with clear margin; this module is the one shared setup those files use: the
// machine home each file isolates its runs under, the stdio dispatch factory, and the
// scripted rejection dialog. The governance-bridge check (`make empirica-governance-bridge-check`)
// keeps the approve flow in `live-bridge.test.ts`; both files run under `make check-pi`.

import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { createStdioBridgeDispatch } from "../src/stdio-transport.ts";
import { recordedInvocation } from "./owner-fixture.ts";
import type { Response } from "../src/contract.ts";

/** The recorded Pi invocation the live-bridge files start runs with. */
export const INVOCATION = recordedInvocation();
export const BRIDGE_TIMEOUT_MS = 15_000;

/** One real machine layout per importing file: a temp repo (cwd for the bridge) with its own git
 * state and a real `EMPIRICA_HOME` — the home every mediating test isolates under, so nothing
 * ever reaches the developer's real ~/.empirica-plugin. */
export function liveBridgeDirectory(label: string) {
  const testRepo = mkdtempSync(join(tmpdir(), `empirica-pi-live-${label}-`));
  const testHome = join(testRepo, "home");
  mkdirSync(testHome);
  execFileSync("git", ["init", "-q"], { cwd: testRepo });
  return { testRepo, testHome };
}

/** The stdio dispatch over the real `bridge.py`, bound to one repo and its machine home. */
export function liveBridgeDispatch(testRepo: string, testHome: string) {
  const py = process.env.EMPIRICA_PYTHON ?? "python3";
  const bridgeScript = join(process.cwd(), "bridge.py");
  return createStdioBridgeDispatch({
    command: py,
    args: [bridgeScript],
    cwd: testRepo,
    env: { ...process.env, EMPIRICA_HOME: testHome, EMPIRICA_REPO_DIR: testRepo },
    timeoutMs: BRIDGE_TIMEOUT_MS,
  });
}

/** The structured-content assertion helper every live-bridge flow uses. */
export function runOfAllow(response: Response): {
  id: string;
  governance: { state: string; approved_digest: string | null; proposal_digest: string;
    budgets: Record<string, number> };
  obligations: { active: Array<{ id: string; status: string }> };
} {
  if (response.result.type !== "Allow" || !("run" in response.result) || !response.result.run)
    throw new Error(`expected Allow with a run, got ${JSON.stringify(response.result)}`);
  return response.result.run as never;
}
