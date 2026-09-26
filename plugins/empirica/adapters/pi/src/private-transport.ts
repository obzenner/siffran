import { fileURLToPath } from "node:url";
import * as path from "node:path";

import { runJsonProcess } from "./process-transport.ts";
import { HOST_PROFILE_ID } from "./stdio-transport.ts";

export interface AuditPlanData {
  child_id: string;
  role_profile: string;
  argument: Record<string, unknown>;
  operation_id: string;
  auditor: { provider_id: string; model_id: string };
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

export function createPrivateIngress(
  timeoutMs = 30_000, script = PRIVATE_BRIDGE_SCRIPT,
): PrivateIngress {
  return async (request) => {
    const raw = await runJsonProcess({
      command: process.env.EMPIRICA_PYTHON ?? "python3",
      args: [script],
      env: { ...process.env, EMPIRICA_HOST_PROFILE_ID: HOST_PROFILE_ID },
      timeoutMs,
      label: "private bridge",
    }, JSON.stringify(request));
    return JSON.parse(raw) as Record<string, unknown>;
  };
}
