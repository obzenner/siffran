import { fileURLToPath } from "node:url";
import * as path from "node:path";

import type { Response } from "./contract.ts";
import { runJsonProcess } from "./process-transport.ts";
import { HOST_PROFILE_ID } from "./stdio-transport.ts";

export interface AuditPlanData {
  child_id: string;
  role_profile: string;
  argument: Record<string, unknown>;
  operation_id: string;
}

export type PrivateOperation =
  | "classify_identity"
  | "governance_context"
  | "governance_decision"
  | "audit_prepare"
  | "audit_reject"
  | "audit_start"
  | "audit_identity"
  | "audit_failure"
  | "audit_verdict"
  | "child_event"
  | "attribution"
  | "evidence_leaf"
  | "human_surface"
  | "evidence_presentation"
  | "evidence_confirmation";

export interface PrivateIngressRequest<Op extends PrivateOperation = PrivateOperation> {
  operation: Op;
  run_id?: string;
  child_id?: string;
  role_profile?: string;
  native_id?: string;
  state?: string;
  plan?: AuditPlanData;
  author?: Record<string, unknown>;
  auditor?: Record<string, unknown>;
  payload?: Record<string, unknown>;
}

type IdentityResponse = Readonly<{
  identity: string;
  provider_id: string | null;
  model_id: string | null;
}> | null;
type AuditPlanResponse = Readonly<{ type: "audit_plan"; plan: AuditPlanData }>;
/**
 * `audit_prepare` refused before any reservation: the runtime the adapter observes now is not the one
 * recorded at StartRun (a reload or resume put another pi-subagents behind the `subagent` tool).
 */
type AuditRefusedResponse = Readonly<{ type: "audit_refused"; reason: "runtime_changed" }>;
/** One thing the human is asked about (built once, in adapters/confirmation.py). */
export type EvidenceItem =
  | { subject: "research"; id: string; claim: string; source_kind: string; source_ref: string;
      quote: string; class: string; reason?: string }
  | { subject: "claim"; id: string; digest: string; text: string };
export interface EvidenceAnswer { subject: "research" | "claim"; id: string; choice: string; note: string | null }
/** The presentation is recorded (and has spent one interaction) or the core refused it. */
type EvidencePresentationResponse =
  | Readonly<{ type: "evidence_presentation"; receipt_id: string; items: EvidenceItem[] }>
  | Readonly<{ type: "evidence_refused"; result: Record<string, unknown> }>;
/** The receipt was admitted (`evidence_receipt`), or the answers granted nothing (`evidence_none`). */
type EvidenceConfirmationResponse =
  | Readonly<{ type: "evidence_receipt"; result: Record<string, unknown> }>
  | Readonly<{ type: "evidence_none" }>;
type AuditVerdictResponse = Readonly<{ type: "audit_verdict"; admitted: boolean }>;
type AuditStartedResponse = Readonly<{ type: "audit_started" }>;
type AuditIdentityResponse = Readonly<{ type: "audit_identity" }>;
type AuditTerminalResponse = Readonly<{ type: "audit_terminal" }>;

export interface PrivateResponses {
  classify_identity: IdentityResponse;
  governance_context: Response;
  governance_decision: Response;
  audit_prepare: AuditPlanResponse | AuditRefusedResponse;
  audit_reject: AuditTerminalResponse;
  audit_start: AuditStartedResponse;
  audit_identity: AuditIdentityResponse;
  audit_failure: AuditTerminalResponse;
  audit_verdict: AuditVerdictResponse;
  child_event: Response;
  attribution: Response;
  evidence_leaf: Response;
  human_surface: Response;
  evidence_presentation: EvidencePresentationResponse;
  evidence_confirmation: EvidenceConfirmationResponse;
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

const string = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const typed = (type: string) => (value: unknown) => object(value) && value.type === type;
const envelope = (value: unknown) => object(value) && value.protocol === "empirica/v2"
  && string(value.request_id) && object(value.result);

/** Shape predicate per private operation; this table is the sole private-response validator. */
const PRIVATE_SHAPES: { readonly [Op in PrivateOperation]: (value: unknown) => boolean } = {
  classify_identity: (value) => value === null || (object(value) && string(value.identity)),
  governance_context: envelope,
  governance_decision: envelope,
  audit_prepare: (value) => object(value) && (
    (value.type === "audit_refused" && value.reason === "runtime_changed")
    || (value.type === "audit_plan" && object(value.plan)
      && string(value.plan.child_id) && string(value.plan.role_profile)
      && string(value.plan.operation_id) && object(value.plan.argument))),
  audit_verdict: (value) => object(value) && value.type === "audit_verdict"
    && typeof value.admitted === "boolean",
  audit_start: typed("audit_started"),
  audit_identity: typed("audit_identity"),
  audit_reject: typed("audit_terminal"),
  audit_failure: typed("audit_terminal"),
  child_event: envelope,
  attribution: envelope,
  evidence_leaf: envelope,
  human_surface: envelope,
  evidence_presentation: (value) => object(value) && (
    (value.type === "evidence_presentation" && string(value.receipt_id) && Array.isArray(value.items)
      && value.items.every((item) => object(item) && string(item.id)
        && (item.subject === "research" || item.subject === "claim")))
    || (value.type === "evidence_refused" && object(value.result))),
  evidence_confirmation: (value) => object(value)
    && (value.type === "evidence_none" || (value.type === "evidence_receipt" && object(value.result))),
};

/** Assert one adapter-private response at the sole private ingress boundary. */
export function assertPrivateResponse<Op extends PrivateOperation>(
  operation: Op, value: unknown,
): PrivateResponses[Op] {
  if (!PRIVATE_SHAPES[operation](value))
    throw new Error(`private ${operation} response is invalid`);
  return value as PrivateResponses[Op];
}

export type PrivateIngress = (
  request: PrivateIngressRequest,
) => Promise<unknown>;

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const PRIVATE_BRIDGE_SCRIPT = path.resolve(HERE, "..", "private_bridge.py");

export function createPrivateIngress(
  timeoutMs = 30_000, script = PRIVATE_BRIDGE_SCRIPT,
): PrivateIngress {
  return async (request: PrivateIngressRequest) => {
    const raw = await runJsonProcess({
      command: process.env.EMPIRICA_PYTHON ?? "python3",
      args: [script],
      env: { ...process.env, EMPIRICA_HOST_PROFILE_ID: HOST_PROFILE_ID },
      timeoutMs,
      label: "private bridge",
    }, JSON.stringify(request));
    return assertPrivateResponse(request.operation, JSON.parse(raw));
  };
}
