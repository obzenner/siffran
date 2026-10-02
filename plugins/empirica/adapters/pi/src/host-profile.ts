// Contract-owned host facts the Pi adapter consumes (Empirica 4.1, D3/D8): the audit launch bound
// and the model-spelling thinking levels. Both live once, in the host-profiles contract
// (`contracts/empirica/v2/host-profiles.json`, read here from the plugin-local vendored copy —
// byte-identical to the SSOT), and Python reads the same file. The adapter never holds a copy.
//
// `parseHostProfiles` validates the parsed document at the boundary and throws on any deviation;
// nothing downstream re-checks it.

import { readFileSync } from "node:fs";
import * as path from "node:path";

import { VENDORED_CONTRACT_DIR } from "./public-tools.ts";
import { HOST_PROFILE_ID } from "./stdio-transport.ts";

/** The bound injected into every foreground audit launch of a host. */
export interface AuditLaunchPolicy {
  readonly timeout_ms: number;
  readonly tool_budget: { readonly soft: number; readonly hard: number; readonly block: readonly string[] };
}

export interface HostProfiles {
  readonly thinking_levels: readonly string[];
  readonly audit_launch_policies: ReadonlyMap<string, AuditLaunchPolicy>;
}

const MAX_TIMER_MS = 2_147_483_647;

function record(value: unknown, where: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    throw new Error(`host-profiles: ${where} must be an object`);
  return value as Record<string, unknown>;
}

function closed(value: Record<string, unknown>, keys: readonly string[], where: string): void {
  for (const key of Object.keys(value))
    if (!keys.includes(key)) throw new Error(`host-profiles: ${where} has unknown field ${JSON.stringify(key)}`);
  for (const key of keys)
    if (!(key in value)) throw new Error(`host-profiles: ${where} is missing ${JSON.stringify(key)}`);
}

function positiveInteger(value: unknown, where: string, maximum = Number.MAX_SAFE_INTEGER): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value > maximum)
    throw new Error(`host-profiles: ${where} must be an integer in 1..${maximum}`);
  return value;
}

function auditLaunchPolicy(value: unknown, where: string): AuditLaunchPolicy {
  const policy = record(value, where);
  closed(policy, ["timeout_ms", "tool_budget"], where);
  const budget = record(policy.tool_budget, `${where}.tool_budget`);
  closed(budget, ["soft", "hard", "block"], `${where}.tool_budget`);
  const soft = positiveInteger(budget.soft, `${where}.tool_budget.soft`);
  const hard = positiveInteger(budget.hard, `${where}.tool_budget.hard`);
  if (soft > hard) throw new Error(`host-profiles: ${where}.tool_budget.soft must not exceed hard`);
  const block = budget.block;
  if (!Array.isArray(block) || block.length === 0
      || !block.every((tool) => typeof tool === "string" && tool !== "") || new Set(block).size !== block.length)
    throw new Error(`host-profiles: ${where}.tool_budget.block must be a nonempty list of unique tool names`);
  return { timeout_ms: positiveInteger(policy.timeout_ms, `${where}.timeout_ms`, MAX_TIMER_MS),
    tool_budget: { soft, hard, block: Object.freeze([...block] as string[]) } };
}

/** Validate a parsed host-profiles document; throws on the first deviation. */
export function parseHostProfiles(document: unknown): HostProfiles {
  const doc = record(document, "document");
  const levels = doc.thinking_levels;
  if (!Array.isArray(levels) || levels.length === 0
      || !levels.every((level) => typeof level === "string" && /^[a-z]+$/.test(level))
      || new Set(levels).size !== levels.length)
    throw new Error("host-profiles: thinking_levels must be a nonempty list of unique lowercase words");
  if (!Array.isArray(doc.profiles)) throw new Error("host-profiles: profiles must be a list");
  const policies = new Map<string, AuditLaunchPolicy>();
  doc.profiles.forEach((row, index) => {
    const profile = record(row, `profiles[${index}]`);
    if (typeof profile.profile_id !== "string" || profile.profile_id === "")
      throw new Error(`host-profiles: profiles[${index}].profile_id must be a nonempty string`);
    if ("audit_launch_policy" in profile)
      policies.set(profile.profile_id, auditLaunchPolicy(profile.audit_launch_policy, `${profile.profile_id}.audit_launch_policy`));
  });
  return { thinking_levels: Object.freeze([...levels] as string[]), audit_launch_policies: policies };
}

/** The audit launch policy of `profileId`; throws when the profile declares none (fail closed). */
export function auditLaunchPolicyFor(profiles: HostProfiles, profileId: string): AuditLaunchPolicy {
  const policy = profiles.audit_launch_policies.get(profileId);
  if (policy === undefined) throw new Error(`host-profiles: ${profileId} declares no audit_launch_policy`);
  return policy;
}

/**
 * The exact `subagent` launch fields the policy sets. Deliberately no turn bound: pi-subagents
 * removed turn budgets in 0.59, so a `turnBudget` key would be silently ignored, not enforced.
 */
export function auditLaunchFields(policy: AuditLaunchPolicy): {
  timeoutMs: number; toolBudget: { soft: number; hard: number; block: string[] };
} {
  return { timeoutMs: policy.timeout_ms,
    toolBudget: { soft: policy.tool_budget.soft, hard: policy.tool_budget.hard, block: [...policy.tool_budget.block] } };
}

export const HOST_PROFILES_PATH = path.join(VENDORED_CONTRACT_DIR, "host-profiles.json");

const PROFILES: HostProfiles = parseHostProfiles(JSON.parse(readFileSync(HOST_PROFILES_PATH, "utf8")));

/** The reasoning-level suffixes pi-subagents appends to a model spelling (contract `thinking_levels`). */
export const THINKING_LEVELS: ReadonlySet<string> = new Set(PROFILES.thinking_levels);

/** The audit bound of this adapter's host profile (contract `audit_launch_policy`). */
export const AUDIT_LAUNCH_POLICY: AuditLaunchPolicy = auditLaunchPolicyFor(PROFILES, HOST_PROFILE_ID);
