// Owner and launch refusals as public-contract reasons (Empirica 4.1, D6).
//
// The resolver in runtime-owner.ts reports why no audit runtime can be bound as a closed code; this
// module is the one table that maps each code onto the contract's `host.subagents_*` reason. The
// author-facing message, sections, and next actions are read from the contract projection
// (`public-tools.json` `recovery`), never written here.

import type { Block, RunSnapshot } from "./contract.ts";
import type { OwnerRefusalCode } from "./runtime-owner.ts";
import { PUBLIC_TOOLS } from "./public-tools.ts";

/** Every resolver/binding refusal code and the contract reason that reports it. */
export const OWNER_REFUSAL_REASON: Readonly<Record<OwnerRefusalCode, string>> = {
  "missing-tool": "host.subagents_missing",
  "multiple-owners": "host.subagents_duplicate_owner",
  "owner-unobservable": "host.subagents_owner_unverified",
  "package-not-pi-subagents": "host.subagents_owner_unverified",
  "version-unobservable": "host.subagents_owner_unverified",
  "child-process": "host.subagents_owner_unverified",
  "preflight-unavailable": "host.subagents_version_unsupported",
  "version-unreviewed": "host.subagents_version_unsupported",
  "tool-inactive": "host.subagents_tool_inactive",
  "owner-changed": "host.subagents_owner_unverified",
};

/** The `host.subagents_launch_unsupported` reason: a `subagent` call Empirica cannot classify or correlate. */
export const LAUNCH_UNSUPPORTED_REASON = "host.subagents_launch_unsupported";

function contractReason(reason: string, what: string): Block["reasons"][number] {
  const row = PUBLIC_TOOLS.recovery[reason];
  if (row === undefined) throw new Error(`contract carries no recovery reason for ${what}`);
  return { code: reason, parameters: {}, ...row };
}

/** The contract reason for a refusal code; throws if the contract projection does not carry it. */
export function ownerRefusalReason(code: OwnerRefusalCode): Block["reasons"][number] {
  return contractReason(OWNER_REFUSAL_REASON[code], `owner refusal ${code}`);
}

/** A public Block carrying the refusal's contract reason (attach `run` when a run exists). */
export function ownerRefusalBlock(code: OwnerRefusalCode, run?: RunSnapshot): Block {
  return run === undefined
    ? { type: "Block", reasons: [ownerRefusalReason(code)] }
    : { type: "Block", run, reasons: [ownerRefusalReason(code)] };
}

/** The contract reason reported when a `subagent` call is `unsupported` or `malformed` during a run. */
export function launchUnsupportedReason(): Block["reasons"][number] {
  return contractReason(LAUNCH_UNSUPPORTED_REASON, "an unsupported subagent launch");
}
