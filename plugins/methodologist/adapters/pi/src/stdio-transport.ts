// Production transport for methodologist/v1: one JSON request per subprocess.
// It moves bytes only; selection and phase validation remain behind the bridge.

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import * as path from "node:path";

import type { Dispatch, Request, Response } from "./contract.ts";

export interface StdioBridgeConfig {
  command: string;
  args?: readonly string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  timeoutMs?: number;
}

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULT_BRIDGE_SCRIPT = path.resolve(HERE, "..", "bridge.py");

export function defaultBridgeConfig(): StdioBridgeConfig {
  return {
    command: process.env.METHODOLOGIST_PYTHON ?? "python3",
    args: [DEFAULT_BRIDGE_SCRIPT],
    timeoutMs: 30_000,
  };
}

function parseResponse(raw: string): Response {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw.trim());
  } catch (error) {
    throw new Error(
      `methodologist bridge returned invalid JSON: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  if (
    parsed === null ||
    typeof parsed !== "object" ||
    !("protocol" in parsed) ||
    !("request_id" in parsed) ||
    !("result" in parsed)
  ) {
    throw new Error("methodologist bridge response is not a well-formed envelope");
  }
  return parsed as Response;
}

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
                reject(
                  new Error(`methodologist bridge timed out after ${config.timeoutMs}ms`),
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
          reject(new Error(`methodologist bridge failed to start: ${error.message}`)),
        ),
      );
      child.on("close", (code: number | null) =>
        finish(() => {
          if (code !== 0) {
            reject(
              new Error(
                `methodologist bridge exited with code ${code}${stderr.trim() ? `: ${stderr.trim()}` : ""}`,
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
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify(request));
    });
}
