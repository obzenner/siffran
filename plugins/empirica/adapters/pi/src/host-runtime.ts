// The host-recorded runtime provenance of a StartRun (Empirica 4.1, D7): `invocation.host_runtime`.
//
// The adapter observed which pi-subagents owns the `subagent` tool (runtime-owner.ts); this records
// that once with the run, under the profile's policy id, so receipts can be derived from persisted
// state instead of operator flags. `hostRuntimeRejection` is the adapter's copy of the rule the Python
// application applies at the bridge (`application/host_runtime.py`): both are driven by the same
// fixture file, plugins/empirica/tests/fixtures/host-runtime-cases.json. Pure; no I/O.

import * as path from "node:path";

import type { InvocationProvenance } from "./contract.ts";
import type { SubagentsCompatibility } from "./host-profile.ts";

/** The recorded object (closed: exactly the members the request schema allows). */
export type HostRuntime = NonNullable<InvocationProvenance["host_runtime"]>;

export type HostRuntimeRejection = "unexpected" | "missing" | "malformed" | "policy" | "package" | "version" | "paths";

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/**
 * Whether `file` is strictly below the absolute directory `root` (lexical, like the Python mirror).
 * Neither path may contain a `..` segment: `path.posix.relative` would normalise one away, where the
 * Python copies compare path parts verbatim and so refuse it.
 */
function inside(file: unknown, root: unknown): boolean {
  if (typeof file !== "string" || typeof root !== "string") return false;
  if (!path.posix.isAbsolute(file) || !path.posix.isAbsolute(root)) return false;
  if (file.split("/").includes("..") || root.split("/").includes("..")) return false;
  const relative = path.posix.relative(root, file);
  return relative !== "" && !relative.startsWith("..") && !path.posix.isAbsolute(relative);
}

/**
 * A stable code when `hostRuntime` does not satisfy the profile's policy, else `undefined`.
 * `policy` is `undefined` for a profile with no external runtime.
 */
export function hostRuntimeRejection(policy: SubagentsCompatibility | undefined, hostRuntime: unknown): HostRuntimeRejection | undefined {
  if (policy === undefined) return hostRuntime === undefined || hostRuntime === null ? undefined : "unexpected";
  if (hostRuntime === undefined || hostRuntime === null) return "missing";
  if (!isRecord(hostRuntime) || !isRecord(hostRuntime.subagents)) return "malformed";
  const subagents = hostRuntime.subagents;
  if (hostRuntime.policy_id !== policy.policy_id) return "policy";
  if (subagents.package !== policy.package) return "package";
  if (typeof subagents.version !== "string" || !policy.reviewed_versions.includes(subagents.version)) return "version";
  if (!inside(subagents.owner_path, subagents.package_root) || !inside(subagents.preflight_path, subagents.package_root)) return "paths";
  return undefined;
}

/** The object to record for `provenance` under `policy`; throws if it would not be accepted (fail closed). */
export function buildHostRuntime(policy: SubagentsCompatibility, provenance: HostRuntime["subagents"]): HostRuntime {
  const hostRuntime: HostRuntime = { policy_id: policy.policy_id, subagents: { ...provenance } };
  const rejection = hostRuntimeRejection(policy, hostRuntime);
  if (rejection !== undefined) throw new Error(`the observed pi-subagents runtime cannot be recorded (${rejection})`);
  return hostRuntime;
}
