// Production transport: a JSON stdio bridge to the host-neutral Empirica v2
// core (D6-C C3).
//
// This is the concrete `Dispatch` a host uses in production. It spawns a bridge
// process, writes one `empirica/v2` request as JSON to its stdin, reads the
// single JSON response from its stdout, and guards it through the central
// inbound runtime guard before returning. It is deliberately *only* transport:
// it carries no convergence rules, no run identity, no persistence — it moves
// bytes, parses JSON, and validates the envelope (ADR-30/D6-C). All operational
// state lives wherever the bridge puts it; this file never touches the
// filesystem itself.
//
// The exact host profile is supplied to the bridge via the
// EMPIRICA_HOST_PROFILE_ID environment variable. There is no default: the
// bridge requires an explicit profile and fails closed if it is absent.

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import * as path from "node:path";

import type { Dispatch, Request, Response } from "./contract.ts";
import { assertResponse, GuardError } from "./guard.ts";

/** Exact Pi + pi-subagents profile required for complete foreground audit binding. */
export const HOST_PROFILE_ID = "pi@0.84.1+pi-subagents@0.50.0";

export interface StdioBridgeConfig {
  /** Executable to run (e.g. "python3"). */
  command: string;
  /** Arguments (e.g. the bridge script path). */
  args?: readonly string[];
  /** Working directory for the bridge process. */
  cwd?: string;
  /** Environment for the bridge; defaults to the parent process environment
   * with EMPIRICA_HOST_PROFILE_ID set to the exact Pi profile. */
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

function guardResponse(raw: string, expectedRequestId: string): Response {
  const trimmed = raw.trim();
  if (trimmed.length === 0) {
    throw new GuardError("empirica bridge returned no output");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new GuardError(`empirica bridge returned invalid JSON: ${message}`);
  }
  // The central guard validates protocol, request_id correlation, result
  // discriminator, and minimum safe branch fields. A malformed/partial/
  // unknown/mismatched response throws here — the caller's gate fails closed.
  assertResponse(parsed, expectedRequestId);
  return parsed as Response;
}

/**
 * Build a production `Dispatch` over a JSON stdio bridge process.
 *
 * One process per request (a request is a single round-trip): the request is
 * written to stdin, the child writes one JSON response to stdout and exits 0.
 * A non-zero exit, a spawn failure, a timeout, or unparseable output all reject
 * — the caller (the gate) decides how to fail, and the hard gate fails closed
 * on a rejection. The response is guarded through the central runtime guard
 * before it is returned, so the caller never sees an unvalidated envelope.
 */
export function createStdioBridgeDispatch(config: StdioBridgeConfig): Dispatch {
  return (request: Request): Promise<Response> =>
    new Promise<Response>((resolve, reject) => {
      const env: NodeJS.ProcessEnv = {
        ...(config.env ?? process.env),
        // The exact host profile is supplied to the bridge; no default.
        EMPIRICA_HOST_PROFILE_ID: HOST_PROFILE_ID,
      };

      const child = spawn(config.command, [...(config.args ?? [])], {
        cwd: config.cwd,
        env,
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
                reject(
                  new GuardError(
                    `empirica bridge timed out after ${config.timeoutMs}ms`,
                  ),
                ),
              );
            }, config.timeoutMs)
          : null;

      child.stdout.setEncoding("utf-8");
      child.stderr.setEncoding("utf-8");
      child.stdout.on("data", (chunk: string) => (stdout += chunk));
      child.stderr.on("data", (chunk: string) => (stderr += chunk));

      child.on("error", (error: Error) =>
        finish(() =>
          reject(
            new GuardError(`empirica bridge failed to start: ${error.message}`),
          ),
        ),
      );

      child.on("close", (code: number | null) =>
        finish(() => {
          if (code !== 0) {
            const detail = stderr.trim();
            reject(
              new GuardError(
                `empirica bridge exited with code ${code}${detail ? `: ${detail}` : ""}`,
              ),
            );
            return;
          }
          try {
            resolve(guardResponse(stdout, request.request_id));
          } catch (error) {
            reject(error instanceof Error ? error : new GuardError(String(error)));
          }
        }),
      );

      child.stdin.on("error", () => {
        /* a bridge that exits before reading stdin surfaces via 'close'/'error' */
      });
      child.stdin.end(JSON.stringify(request));
    });
}
