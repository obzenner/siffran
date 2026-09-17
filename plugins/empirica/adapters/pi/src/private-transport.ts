import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import * as path from "node:path";

import { HOST_PROFILE_ID } from "./stdio-transport.ts";

export interface AuditPlanData {
  child_id: string;
  role_profile: string;
  argument: Record<string, unknown>;
  operation_id: string;
}

export interface PrivateIngressRequest {
  operation: string;
  run_id: string;
  child_id?: string;
  role_profile?: string;
  native_id?: string;
  state?: string;
  plan?: AuditPlanData;
  author?: Record<string, unknown>;
  auditor?: Record<string, unknown>;
  payload?: Record<string, unknown>;
}

export type PrivateIngress = (request: PrivateIngressRequest) => Promise<Record<string, unknown>>;

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const PRIVATE_BRIDGE_SCRIPT = path.resolve(HERE, "..", "private_bridge.py");

export function createPrivateIngress(): PrivateIngress {
  return (request) => new Promise<Record<string, unknown>>((resolve, reject) => {
    const child = spawn(process.env.EMPIRICA_PYTHON ?? "python3", [PRIVATE_BRIDGE_SCRIPT], {
      env: { ...process.env, EMPIRICA_HOST_PROFILE_ID: HOST_PROFILE_ID },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => (stdout += chunk));
    child.stderr.on("data", (chunk: string) => (stderr += chunk));
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(stderr.trim() || `private bridge exited ${code}`));
        return;
      }
      try { resolve(JSON.parse(stdout) as Record<string, unknown>); }
      catch (error) { reject(error); }
    });
    child.stdin.end(JSON.stringify(request));
  });
}
