// Production transport: a JSON stdio bridge to the host-neutral Empirica core.
//
// This is the concrete `Dispatch` a host uses in production. It spawns a bridge
// process, writes one `empirica/v1` request as JSON to its stdin, and reads the
// single JSON response from its stdout. It is deliberately *only* transport: it
// carries no convergence rules, no run identity, no persistence — it moves bytes
// and parses JSON (ADR-30: the domain lives behind the contract, in the core).
// All operational state therefore lives wherever the bridge puts it (the shared
// `~/.empirica-plugin` home); this file never touches the filesystem itself.

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import * as path from "node:path";

import type { Dispatch, Request, Response } from "./contract.ts";

export interface StdioBridgeConfig {
  /** Executable to run (e.g. "python3"). */
  command: string;
  /** Arguments (e.g. the bridge script path). */
  args?: readonly string[];
  /** Working directory for the bridge process. */
  cwd?: string;
  /** Environment for the bridge; defaults to the parent process environment. */
  env?: NodeJS.ProcessEnv;
  /** Hard timeout in milliseconds; the child is killed and the dispatch rejects. */
  timeoutMs?: number;
}

// plugins/empirica/adapters/pi/src/stdio-transport.ts -> plugins/empirica/adapters/pi/bridge.py
const HERE = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULT_BRIDGE_SCRIPT = path.resolve(HERE, "..", "bridge.py");

/** The default bridge invocation: the Python stdio bridge under this package. */
export function defaultBridgeConfig(): StdioBridgeConfig {
  return {
    command: process.env.EMPIRICA_PYTHON ?? "python3",
    args: [DEFAULT_BRIDGE_SCRIPT],
    timeoutMs: 30_000,
  };
}

function parseResponse(raw: string): Response {
  const trimmed = raw.trim();
  if (trimmed.length === 0) {
    throw new Error("empirica bridge returned no output");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`empirica bridge returned invalid JSON: ${message}`);
  }
  // Structural transport check only — the *meaning* of the result is the core's.
  if (
    parsed === null ||
    typeof parsed !== "object" ||
    !("result" in parsed) ||
    (parsed as { result: unknown }).result === null ||
    typeof (parsed as { result: unknown }).result !== "object"
  ) {
    throw new Error("empirica bridge response is not a well-formed envelope");
  }
  return parsed as Response;
}

/**
 * Build a production `Dispatch` over a JSON stdio bridge process.
 *
 * One process per request (a request is a single round-trip): the request is
 * written to stdin, the child is expected to write one JSON response to stdout
 * and exit 0. A non-zero exit, a spawn failure, a timeout, or unparseable output
 * all reject — the caller (the gate) decides how to fail, and the hard gate fails
 * closed on a rejection (see translate.gateFromDecision / index.ts).
 */
export function createStdioBridgeDispatch(config: StdioBridgeConfig): Dispatch {
  return (request: Request): Promise<Response> =>
    new Promise<Response>((resolve, reject) => {
      const child = spawn(config.command, [...(config.args ?? [])], {
        cwd: config.cwd,
        env: config.env ?? process.env,
        stdio: ["pipe", "pipe", "pipe"],
      });

      let stdout = "";
      let stderr = "";
      let settled = false;
      const finish = (fn: () => void) => {
        if (settled) return;
        settled = true;
        if (timer !== null) clearTimeout(timer);
        fn();
      };

      const timer =
        config.timeoutMs && config.timeoutMs > 0
          ? setTimeout(() => {
              child.kill("SIGKILL");
              finish(() =>
                reject(new Error(`empirica bridge timed out after ${config.timeoutMs}ms`)),
              );
            }, config.timeoutMs)
          : null;

      child.stdout.setEncoding("utf-8");
      child.stderr.setEncoding("utf-8");
      child.stdout.on("data", (chunk: string) => (stdout += chunk));
      child.stderr.on("data", (chunk: string) => (stderr += chunk));

      child.on("error", (error: Error) =>
        finish(() => reject(new Error(`empirica bridge failed to start: ${error.message}`))),
      );

      child.on("close", (code: number | null) =>
        finish(() => {
          if (code !== 0) {
            const detail = stderr.trim();
            reject(
              new Error(
                `empirica bridge exited with code ${code}${detail ? `: ${detail}` : ""}`,
              ),
            );
            return;
          }
          try {
            resolve(parseResponse(stdout));
          } catch (error) {
            reject(error instanceof Error ? error : new Error(String(error)));
          }
        }),
      );

      child.stdin.on("error", () => {
        /* a bridge that exits before reading stdin surfaces via 'close'/'error' */
      });
      child.stdin.end(JSON.stringify(request));
    });
}
