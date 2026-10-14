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

/** The bound injected into every foreground audit launch of a host. */
export interface AuditLaunchPolicy {
  readonly timeout_ms: number;
  /** The only tools the auditor child may be given; the preflight's effective allowlist must be a subset. */
  readonly audit_child_tools: readonly string[];
  readonly tool_budget: { readonly soft: number; readonly hard: number; readonly block: readonly string[] };
}

/** The external audit runtime a profile supports: a version range under one named policy (ADR-0069). */
export interface SubagentsCompatibility {
  readonly package: string;
  readonly policy_id: string;
  readonly minimum: string;
  readonly maximum_exclusive: string;
}

/** The half-open interval `[minimum, maximum_exclusive)` of exact host versions a profile supports (ADR-0069). */
export interface VersionRange {
  readonly minimum: string;
  readonly maximum_exclusive: string;
}

export interface HostProfiles {
  readonly thinking_levels: readonly string[];
  readonly host_compatibility: ReadonlyMap<string, VersionRange>;
  readonly audit_launch_policies: ReadonlyMap<string, AuditLaunchPolicy>;
  readonly subagents_compatibility: ReadonlyMap<string, SubagentsCompatibility>;
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
  closed(policy, ["timeout_ms", "audit_child_tools", "tool_budget"], where);
  const budget = record(policy.tool_budget, `${where}.tool_budget`);
  closed(budget, ["soft", "hard", "block"], `${where}.tool_budget`);
  const soft = positiveInteger(budget.soft, `${where}.tool_budget.soft`);
  const hard = positiveInteger(budget.hard, `${where}.tool_budget.hard`);
  if (soft > hard) throw new Error(`host-profiles: ${where}.tool_budget.soft must not exceed hard`);
  const block = budget.block;
  if (!Array.isArray(block) || block.length === 0
      || !block.every((tool) => typeof tool === "string" && tool !== "") || new Set(block).size !== block.length)
    throw new Error(`host-profiles: ${where}.tool_budget.block must be a nonempty list of unique tool names`);
  const child = policy.audit_child_tools;
  if (!Array.isArray(child) || child.length === 0
      || !child.every((tool) => typeof tool === "string" && tool !== "") || new Set(child).size !== child.length)
    throw new Error(`host-profiles: ${where}.audit_child_tools must be a nonempty list of unique tool names`);
  return { timeout_ms: positiveInteger(policy.timeout_ms, `${where}.timeout_ms`, MAX_TIMER_MS),
    audit_child_tools: Object.freeze([...child] as string[]),
    tool_budget: { soft, hard, block: Object.freeze([...block] as string[]) } };
}

export const EXACT_VERSION = /^\d+\.\d+\.\d+$/;

/** Compare two exact MAJOR.MINOR.PATCH versions numerically (negative, zero, positive). */
export function compareVersions(left: string, right: string): number {
  const a = left.split(".").map(Number);
  const b = right.split(".").map(Number);
  return (a[0] - b[0]) || (a[1] - b[1]) || (a[2] - b[2]);
}

/** Whether `version` is an exact version in `[minimum, maximum_exclusive)` of the range. */
export function inRange(range: VersionRange, version: unknown): version is string {
  return typeof version === "string" && EXACT_VERSION.test(version)
    && compareVersions(version, range.minimum) >= 0 && compareVersions(version, range.maximum_exclusive) < 0;
}

/** Whether `version` is an exact version in `[minimum, maximum_exclusive)` of the policy. */
export function inSubagentsRange(policy: SubagentsCompatibility, version: unknown): version is string {
  return inRange(policy, version);
}

/** The range as the contract and the docs spell it: `>=1.0.0,<2.0.0`. */
export function spellRange(range: VersionRange): string {
  return `>=${range.minimum},<${range.maximum_exclusive}`;
}

/** The host version as the reason shows it: the exact version read, else the word for "could not be read". */
export type HostVersionVerdict = { ok: true; version: string } | { ok: false; version: string; range: string };

/**
 * The one check of the host's own version against its profile's range (ADR-0069). A version that is not an exact
 * MAJOR.MINOR.PATCH, or that was not read at all, is outside the range: the run is refused, never started blind.
 */
export function checkHostVersion(range: VersionRange, observed: unknown): HostVersionVerdict {
  return inRange(range, observed) ? { ok: true, version: observed }
    : { ok: false, version: typeof observed === "string" && observed !== "" ? observed : "unreadable", range: spellRange(range) };
}

function versionRange(value: unknown, where: string): VersionRange {
  const range = record(value, where);
  const { minimum, maximum_exclusive: maximum } = range;
  if (typeof minimum !== "string" || typeof maximum !== "string" || !EXACT_VERSION.test(minimum) || !EXACT_VERSION.test(maximum)
      || compareVersions(minimum, maximum) >= 0)
    throw new Error(`host-profiles: ${where} needs exact versions with minimum below maximum_exclusive`);
  return { minimum, maximum_exclusive: maximum };
}

function subagentsCompatibility(value: unknown, where: string): SubagentsCompatibility {
  const policy = record(value, where);
  closed(policy, ["package", "policy_id", "minimum", "maximum_exclusive"], where);
  if (typeof policy.package !== "string" || !/^[a-z0-9][a-z0-9._-]*$/.test(policy.package))
    throw new Error(`host-profiles: ${where}.package must be a lowercase package name`);
  if (typeof policy.policy_id !== "string" || !/^[a-z0-9]+(-[a-z0-9]+)*-v[0-9]+$/.test(policy.policy_id))
    throw new Error(`host-profiles: ${where}.policy_id must look like name-v1`);
  const { minimum, maximum_exclusive: maximum } = policy;
  if (typeof minimum !== "string" || typeof maximum !== "string" || !EXACT_VERSION.test(minimum) || !EXACT_VERSION.test(maximum)
      || compareVersions(minimum, maximum) >= 0)
    throw new Error(`host-profiles: ${where} needs exact versions with minimum below maximum_exclusive`);
  return { package: policy.package, policy_id: policy.policy_id, minimum, maximum_exclusive: maximum };
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
  const compatibilities = new Map<string, SubagentsCompatibility>();
  const hosts = new Map<string, VersionRange>();
  doc.profiles.forEach((row, index) => {
    const profile = record(row, `profiles[${index}]`);
    if (typeof profile.profile_id !== "string" || profile.profile_id === "")
      throw new Error(`host-profiles: profiles[${index}].profile_id must be a nonempty string`);
    if ("compatibility" in profile)
      hosts.set(profile.profile_id, versionRange(profile.compatibility, `${profile.profile_id}.compatibility`));
    if ("audit_launch_policy" in profile)
      policies.set(profile.profile_id, auditLaunchPolicy(profile.audit_launch_policy, `${profile.profile_id}.audit_launch_policy`));
    if ("subagents_compatibility" in profile)
      compatibilities.set(profile.profile_id,
        subagentsCompatibility(profile.subagents_compatibility, `${profile.profile_id}.subagents_compatibility`));
  });
  return { thinking_levels: Object.freeze([...levels] as string[]), host_compatibility: hosts, audit_launch_policies: policies,
    subagents_compatibility: compatibilities };
}

/** The audit launch policy of `profileId`; throws when the profile declares none (fail closed). */
export function auditLaunchPolicyFor(profiles: HostProfiles, profileId: string): AuditLaunchPolicy {
  const policy = profiles.audit_launch_policies.get(profileId);
  if (policy === undefined) throw new Error(`host-profiles: ${profileId} declares no audit_launch_policy`);
  return policy;
}

/** The host version range of `profileId`; throws when the profile declares none (fail closed). */
export function hostCompatibilityFor(profiles: HostProfiles, profileId: string): VersionRange {
  const range = profiles.host_compatibility.get(profileId);
  if (range === undefined) throw new Error(`host-profiles: ${profileId} declares no compatibility`);
  return range;
}

/** The external-runtime policy of `profileId`; throws when the profile declares none (fail closed). */
export function subagentsCompatibilityFor(profiles: HostProfiles, profileId: string): SubagentsCompatibility {
  const policy = profiles.subagents_compatibility.get(profileId);
  if (policy === undefined) throw new Error(`host-profiles: ${profileId} declares no subagents_compatibility`);
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

const DOCUMENT: unknown = JSON.parse(readFileSync(HOST_PROFILES_PATH, "utf8"));
const PROFILES: HostProfiles = parseHostProfiles(DOCUMENT);

/** The id of the one profile of `hostId` in a host-profiles document; throws unless there is exactly one. */
export function profileIdOfHost(document: unknown, hostId: string): string {
  const rows = (record(document, "document").profiles as unknown[]).map((row) => record(row, "profile"))
    .filter((row) => row.host_id === hostId);
  if (rows.length !== 1 || typeof rows[0].profile_id !== "string")
    throw new Error(`host-profiles: expected exactly one profile for host ${hostId}, found ${rows.length}`);
  return rows[0].profile_id;
}

/** This adapter's host profile, read from the contract: Pi plus the pi-subagents foreground-audit policy. */
export const HOST_PROFILE_ID: string = profileIdOfHost(DOCUMENT, "pi");

/** The reasoning-level suffixes pi-subagents appends to a model spelling (contract `thinking_levels`). */
export const THINKING_LEVELS: ReadonlySet<string> = new Set(PROFILES.thinking_levels);

/** The audit bound of this adapter's host profile (contract `audit_launch_policy`). */
export const AUDIT_LAUNCH_POLICY: AuditLaunchPolicy = auditLaunchPolicyFor(PROFILES, HOST_PROFILE_ID);

/** The Pi versions this adapter supports (contract `compatibility` of the Pi profile): enforced at `/empirica` start. */
export const HOST_COMPATIBILITY: VersionRange = hostCompatibilityFor(PROFILES, HOST_PROFILE_ID);

/** The external audit runtime of this adapter's host profile: its version range and the policy id. */
export const SUBAGENTS_COMPATIBILITY: SubagentsCompatibility = subagentsCompatibilityFor(PROFILES, HOST_PROFILE_ID);
