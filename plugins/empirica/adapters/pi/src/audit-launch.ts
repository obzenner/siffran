// The fields the adapter writes onto the canonical auditor's `subagent` call (Empirica 4.1, D3).
//
// A pure function so the complete set of keys is one value: a test proves every key is declared by
// the pinned pi-subagents' own schema, and that no bound pi-subagents dropped (turn budgets) appears.

import { auditLaunchFields, type AuditLaunchPolicy } from "./host-profile.ts";
import {
  AUDIT_LAUNCH_PARAMETERS, launchPlacement, type AuditLaunchParameter, type SubagentInventory,
} from "./subagent-inventory.ts";

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
  readonly context: unknown;
} {
  return { async: request.async, output_mode: request.outputMode, force_top_level_async: request.forceTopLevelAsync,
    context: request.context };
}

/**
 * The host-owned launch fields: the dossier task, the resolved model and scope, a foreground run, the read-only
 * acceptance exemption, an explicit fresh context, and the contract-owned timeout and tool budget. pi-subagents may
 * classify the original author call before the task is replaced, so the acceptance exemption is explicit:
 * a canonical auditor must not be assigned writer evidence gates by extension ordering.
 */
export function auditLaunchInput(decision: AuditLaunchDecision): Record<string, unknown> {
  return {
    task: decision.task,
    model: decision.model,
    agentScope: decision.agentScope,
    async: false,
    // pi-subagents resolves an omitted `context` from the operator's `defaultSubagentContext` (0.64.0 and
    // later), which can be `fork`: the auditor would then inherit the author's transcript. Every reviewed
    // version declares `context`, so the pin is explicit. The author may not send it (canonicalAuditorInputError).
    context: "fresh",
    acceptance: { level: "none",
      reason: "Empirica's bound canonical auditor is read-only and has its own verdict contract." },
    ...auditLaunchFields(decision.policy),
  };
}

/**
 * The fields of `auditLaunchInput`, written where the bound owner's schema declares each one: at the top level of the
 * `subagent` call, or inside its one `options` object (pi-subagents 0.77.0 and later publish the compact schema; a flat
 * package declares everything at the top level and gets the flat fields unchanged).
 *
 * Each field is written once, in exactly one place: pi-subagents rejects a field given in both places with different
 * values, and an `options` key it does not know (`SUBAGENT_OPTION_KEYS`) with the closest valid name, so a field a
 * future package moved or renamed fails loudly instead of being ignored. `agent` stays the author's own top-level key.
 * The flat view of the placed call is `auditLaunchInput`: that is what the launch preflight is asked and what
 * `flattenSubagentToolOptions` lowers the placed call to (matrix check `launch`).
 */
export function placeAuditLaunch(fields: Record<string, unknown>, inventory: SubagentInventory): Record<string, unknown> {
  const placement = launchPlacement(inventory);
  const topLevel: Record<string, unknown> = {};
  const options: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(fields)) {
    if (!(AUDIT_LAUNCH_PARAMETERS as readonly string[]).includes(name))
      throw new Error(`the audit launch sets ${JSON.stringify(name)}, which is not a known audit-launch parameter`);
    (placement[name as AuditLaunchParameter] === "options" ? options : topLevel)[name] = value;
  }
  return Object.keys(options).length === 0 ? topLevel : { ...topLevel, options };
}
