// The one place pi-subagents' launch-preflight response is interpreted (Empirica 4.1, D8).
//
// `admitAuditPreflight(descriptor, response)` is a pure function: it turns what the bound runtime's
// `resolveSubagentLaunchContract` returned into either the few facts Empirica consumes or a typed
// refusal. Nothing else reads a preflight response. It is proven against responses captured from
// real packages (test/fixtures/preflight-<version>.json), not against hand-written shapes.
//
// Consumed fields, and what the captured fixtures show per launch-contract version:
//   contract.version                 v2 (0.50.0, 0.64.0) | v3 (0.74.0, 0.75.0); must be one the
//                                    bound inventory records AND one listed in ACCEPTED_LAUNCH_CONTRACT_VERSIONS
//   contract.protocol.packageVersion must equal the bound owner's exact version
//   contract.agent.filePath          must be the packaged canonical auditor file (canonicalised)
//   contract.modelCandidates[0]      v2 only - the resolved launch model, thinking level appended
//   contract.model                   v3 only (v3 drops modelCandidates) - same spelling
// The response does not report async/outputMode/forceTopLevelAsync, so those are checked on the
// launch request the adapter is about to send (`descriptor.launch`), not read from the response.

import { THINKING_LEVELS } from "./host-profile.ts";
import type { SubagentInventory } from "./subagent-inventory.ts";

/** Launch-contract versions proven equivalent, for the consumed fields, by captured fixtures. */
export const ACCEPTED_LAUNCH_CONTRACT_VERSIONS: readonly number[] = [2, 3];

/** `model` without one trailing reasoning level; only the contract's exact levels, never a generic suffix. */
export function withoutThinkingLevel(model: string, levels: ReadonlySet<string> = THINKING_LEVELS): string {
  const colon = model.lastIndexOf(":");
  return colon >= 0 && levels.has(model.slice(colon + 1)) ? model.slice(0, colon) : model;
}

export type AdmissionRefusalCode =
  | "owner-unverified"
  | "preflight-refused"
  | "malformed-response"
  | "launch-contract-version"
  | "owner-version-mismatch"
  | "agent-not-canonical"
  | "model-substituted"
  | "forced-async"
  | "not-foreground"
  | "output-mode-not-inline";

/** The runtime and the launch the adapter is about to perform, as the adapter observed them. */
export interface AuditPreflightDescriptor {
  /** Exact version of the bound owner; `undefined` is a refusal, never a default. */
  readonly version: string | undefined;
  /** The owner's reviewed inventory; `undefined` is a refusal. */
  readonly inventory: SubagentInventory | undefined;
  readonly expected: { readonly agent_file_path: string; readonly model: string };
  /** The launch request's own fields (the preflight response does not echo them). */
  readonly launch: {
    readonly async: unknown;
    readonly output_mode: unknown;
    readonly force_top_level_async: unknown;
  };
  /** Path canonicalisation (production: realpath with resolve fallback), injected. */
  readonly canonical: (file: string) => string;
}

export interface AdmittedAuditContract {
  readonly agent_file_path: string;
  /** The resolved launch model with its reasoning level removed. */
  readonly model: string;
  readonly launch_contract_version: number;
}

export type AuditAdmission =
  | { readonly kind: "accept"; readonly contract: AdmittedAuditContract }
  | { readonly kind: "refuse"; readonly code: AdmissionRefusalCode; readonly detail: string };

const refuse = (code: AdmissionRefusalCode, detail: string): AuditAdmission => ({ kind: "refuse", code, detail });

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Admit (or refuse) one preflight response for the canonical foreground audit. */
export function admitAuditPreflight(descriptor: AuditPreflightDescriptor, response: unknown): AuditAdmission {
  const { version, inventory, launch } = descriptor;
  if (version === undefined || version === "") return refuse("owner-unverified", "the pi-subagents owner has no observed version");
  if (inventory === undefined) return refuse("owner-unverified", `pi-subagents ${version} has no reviewed inventory`);
  if (inventory.version !== version)
    return refuse("owner-unverified", `the inventory describes ${inventory.version}, not the bound owner ${version}`);
  if (launch.force_top_level_async === true)
    return refuse("forced-async", "forceTopLevelAsync forces a background launch; the audit must run in the foreground");
  if (launch.async !== false)
    return refuse("not-foreground", `the audit launch must set async:false (got ${JSON.stringify(launch.async)})`);
  if (launch.output_mode !== undefined && launch.output_mode !== "inline")
    return refuse("output-mode-not-inline",
      `outputMode ${JSON.stringify(launch.output_mode)} would withhold the inline result the verdict is read from`);

  if (!isRecord(response)) return refuse("malformed-response", "the preflight response is not an object");
  if (response.ok === false)
    return refuse("preflight-refused", typeof response.message === "string" ? response.message : "the preflight refused the launch");
  if (response.ok !== true || !isRecord(response.contract))
    return refuse("malformed-response", "the preflight response is neither {ok:false} nor {ok:true, contract}");
  const contract = response.contract;

  const contractVersion = contract.version;
  if (typeof contractVersion !== "number" || !ACCEPTED_LAUNCH_CONTRACT_VERSIONS.includes(contractVersion)
      || contractVersion !== inventory.launch_contract_version)
    return refuse("launch-contract-version",
      `launch contract ${JSON.stringify(contractVersion)} is not the v${inventory.launch_contract_version} that `
      + `pi-subagents ${version} is reviewed to emit (accepted: ${ACCEPTED_LAUNCH_CONTRACT_VERSIONS.join(", ")})`);
  const protocol = contract.protocol;
  if (!isRecord(protocol) || protocol.packageVersion !== version)
    return refuse("owner-version-mismatch",
      `the preflight reports package ${JSON.stringify(isRecord(protocol) ? protocol.packageVersion : undefined)}, `
      + `the bound owner is ${version}`);

  const agent = contract.agent;
  if (!isRecord(agent) || typeof agent.filePath !== "string" || agent.filePath === "")
    return refuse("malformed-response", "contract.agent.filePath is missing");
  if (descriptor.canonical(agent.filePath) !== descriptor.canonical(descriptor.expected.agent_file_path))
    return refuse("agent-not-canonical", `the resolved auditor is ${agent.filePath}, not ${descriptor.expected.agent_file_path}`);

  const resolvedModel = contractVersion === 2
    ? (Array.isArray(contract.modelCandidates) ? contract.modelCandidates[0] : undefined)
    : contract.model;
  if (typeof resolvedModel !== "string" || resolvedModel === "")
    return refuse("malformed-response", `the v${contractVersion} contract carries no resolved launch model`);
  const model = withoutThinkingLevel(resolvedModel);
  if (model !== withoutThinkingLevel(descriptor.expected.model))
    return refuse("model-substituted", `preflight resolved ${resolvedModel}, the configured auditor model is ${descriptor.expected.model}`);
  return { kind: "accept", contract: { agent_file_path: agent.filePath, model, launch_contract_version: contractVersion } };
}
