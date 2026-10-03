// The fields the adapter writes onto the canonical auditor's `subagent` call (Empirica 4.1, D3).
//
// A pure function so the complete set of keys is one value: a test proves every key is declared by
// the pinned pi-subagents' own schema, and that no bound pi-subagents dropped (turn budgets) appears.

import { auditLaunchFields, type AuditLaunchPolicy } from "./host-profile.ts";

/** What the adapter resolved for this audit: the host-built task, the preflight-vouched model and scope. */
export interface AuditLaunchDecision {
  readonly task: string;
  readonly model: string;
  readonly agentScope: string;
  readonly policy: AuditLaunchPolicy;
}

/**
 * The launch facts the preflight seam checks (`admitAuditPreflight`'s `descriptor.launch`), read off
 * the request object that is actually sent, so the seam judges the real request and not constants.
 */
export function preflightLaunch(request: Record<string, unknown>): {
  readonly async: unknown; readonly output_mode: unknown; readonly force_top_level_async: unknown;
} {
  return { async: request.async, output_mode: request.outputMode, force_top_level_async: request.forceTopLevelAsync };
}

/**
 * The host-owned launch fields: the dossier task, the resolved model and scope, a foreground run,
 * the read-only acceptance exemption, and the contract-owned timeout and tool budget. pi-subagents may
 * classify the original author call before the task is replaced, so the acceptance exemption is explicit:
 * a canonical auditor must not be assigned writer evidence gates by extension ordering.
 */
export function auditLaunchInput(decision: AuditLaunchDecision): Record<string, unknown> {
  return {
    task: decision.task,
    model: decision.model,
    agentScope: decision.agentScope,
    async: false,
    acceptance: { level: "none",
      reason: "Empirica's bound canonical auditor is read-only and has its own verdict contract." },
    ...auditLaunchFields(decision.policy),
  };
}
