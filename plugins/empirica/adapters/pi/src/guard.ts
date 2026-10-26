// Central inbound runtime guard (D6-C C3).
//
// Every response from the transport passes through here BEFORE any gate or
// render. The guard validates the exact protocol, request_id correlation,
// the result discriminator, and the minimum safe branch fields the adapter
// actually reads. A malformed, partial, unknown, or mismatched response
// throws — the hard gate catches the throw and fails CLOSED so an invalid
// response can never permit report_convergence.
//
// This is the single trust-boundary validation point: the stdio transport
// calls it on raw bridge output, and the index.ts dispatch wrapper calls it
// on every dispatch result (including test fakes). The guard is intentionally
// strict and minimal — it checks exactly what the adapter reads, no more.

import {
  PUBLIC_TOOLS, RESPONSE_DEFS, closedKeys, schemaBoolean, schemaBranches, schemaConst, schemaPattern, schemaValues,
} from "./public-tools.ts";

import {
  PROTOCOL,
  type FailDirection,
  type FaultCode,
  type InertReason,
  type Response,
  type RunStatus,
  type SpikeProfile,
} from "./contract.ts";

type Json = Record<string, any>;

// Every contract value below is read from the vendored response schema; none is restated here.
const DEFS = RESPONSE_DEFS;
export const DIGEST = schemaPattern(DEFS.digest256);
export const RUN_STATUSES = schemaValues<RunStatus>(DEFS.runView?.properties?.status);
export const INERT_REASONS = schemaValues<InertReason>(DEFS.inert?.properties?.reason);
export const FAULT_CODES = schemaValues<FaultCode>(DEFS.fault?.properties?.code);
export const FAIL_DIRECTIONS = schemaValues<FailDirection>(DEFS.fault?.properties?.fail_direction);
const CONTRACT = {
  allow: closedKeys(DEFS.allowContractResult),
  indexResult: closedKeys(DEFS.contractResultIndex),
  indexTarget: schemaConst(DEFS.contractResultIndex?.properties?.target),
  sectionResult: closedKeys(DEFS.contractResultSection),
  sectionTarget: schemaConst(DEFS.contractResultSection?.properties?.target),
  index: closedKeys(DEFS.contractIndex),
  id: schemaConst(DEFS.contractIndex?.properties?.id),
  version: schemaConst(DEFS.contractIndex?.properties?.version),
  sectionRow: closedKeys(DEFS.contractIndex?.properties?.sections?.items),
  reasonRow: closedKeys(DEFS.contractIndex?.properties?.reasons?.items),
  actionRow: closedKeys(DEFS.contractIndex?.properties?.next_actions?.items),
  section: closedKeys(DEFS.contractSection),
  clause: closedKeys(DEFS.contractSection?.properties?.clauses?.items),
} as const;

export function objectWithKeys(value: unknown, keys: readonly string[]): value is Json {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join("\0") === [...keys].sort().join("\0");
}

export const text = (value: unknown, nonempty = false): value is string =>
  typeof value === "string" && (!nonempty || value.length > 0);
export const unique = (value: unknown[]): boolean =>
  new Set(value.map((item) => JSON.stringify(item))).size === value.length;
export const rows = (value: unknown, keys: readonly string[], fields: readonly string[]): boolean =>
  Array.isArray(value) && unique(value) && value.every((item) =>
    objectWithKeys(item, keys) && fields.every((field) => text(item[field])));

export function validContractResult(value: unknown): boolean {
  const isIndex = (value as Json | null)?.target === CONTRACT.indexTarget;
  if (!objectWithKeys(value, isIndex ? CONTRACT.indexResult : CONTRACT.sectionResult)) return false;
  if (!text(value.digest) || !DIGEST.test(value.digest)) return false;
  if (isIndex) {
    const index = value.index;
    return objectWithKeys(index, CONTRACT.index)
      && index.id === CONTRACT.id && index.version === CONTRACT.version
      && rows(index.sections, CONTRACT.sectionRow, CONTRACT.sectionRow)
      && Array.isArray(index.reasons) && unique(index.reasons) && index.reasons.every((reason) =>
        objectWithKeys(reason, CONTRACT.reasonRow) && text(reason.code)
        && Array.isArray(reason.sections) && reason.sections.every((section) => text(section)))
      && rows(index.next_actions, CONTRACT.actionRow, CONTRACT.actionRow);
  }
  if (value.target !== CONTRACT.sectionTarget || !text(value.section_id, true)) return false;
  const section = value.section;
  return objectWithKeys(section, CONTRACT.section)
    && text(section.id, true) && section.id === value.section_id && text(section.title, true)
    && text(section.summary) && rows(section.clauses, CONTRACT.clause, CONTRACT.clause)
    && section.clauses.every((clause: Json) => text(clause.id, true) && text(clause.text, true));
}

const START_REFUSAL_REASON_CODES = new Set<string>(PUBLIC_TOOLS.start_refusal_codes);

/** One field check per closed spike-backend property: a digest reference, or a const/enum value set. */
function fieldCheck(schema: Json): (value: unknown) => boolean {
  if (schema.$ref === "#/$defs/digest256") return (value) => typeof value === "string" && DIGEST.test(value);
  const values = schemaValues(schema);
  return (value) => typeof value === "string" && values.has(value);
}

type FieldChecks = readonly (readonly [string, (value: unknown) => boolean])[];

/** The closed spike-backend branches keyed by their `kind` const; non-unique kinds refuse to load. */
export function spikeBackendChecks(schema: Json | undefined): ReadonlyMap<string, FieldChecks> {
  const branches = schemaBranches(schema);
  const checks = new Map(branches.map((branch) => [
    schemaConst(branch.properties?.kind),
    closedKeys(branch).map((name) => [name, fieldCheck(branch.properties[name])] as const),
  ]));
  if (checks.size !== branches.length) throw new Error("contract spikeBackend kind consts are not unique");
  return checks;
}
const SPIKE_BACKENDS = spikeBackendChecks(DEFS.spikeBackend);
export const SPIKE_BACKEND_KINDS: ReadonlySet<string> = new Set(SPIKE_BACKENDS.keys());
export const SPIKE_PROFILE_IDS = schemaValues<SpikeProfile>(schemaBranches(DEFS.spikeBackend)
  .find((branch) => branch.properties?.profile_id !== undefined)?.properties.profile_id);

/** Run statuses each Allow `converged` value admits (schema `allowConverged`/`allowNonConverged`). */
export function allowRunStatuses(defs: Json): ReadonlyMap<boolean, ReadonlySet<string>> {
  const statuses = new Map(["allowConverged", "allowNonConverged"].map((name) => [
    schemaBoolean(defs[name]?.properties?.converged),
    schemaValues(defs[name]?.properties?.run?.allOf?.[1]?.properties?.status),
  ]));
  if (statuses.size !== 2) throw new Error("contract Allow converged consts are not distinct");
  return statuses;
}
const ALLOW_RUN_STATUSES = allowRunStatuses(DEFS);

export class GuardError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GuardError";
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}

function isNonemptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function assertSpikeBackend(value: unknown, branch: string): void {
  if (!isObject(value) || Array.isArray(value))
    throw new GuardError(`${branch}.run.spike_backend must be an object`);
  const fields = typeof value.kind === "string" ? SPIKE_BACKENDS.get(value.kind) : undefined;
  if (fields === undefined || !objectWithKeys(value, fields.map(([name]) => name))
      || !fields.every(([name, admits]) => admits(value[name])))
    throw new GuardError(`${branch}.run.spike_backend shape invalid`);
}

function assertRunSnapshot(run: unknown, branch: string): void {
  if (!isObject(run)) throw new GuardError(`${branch}.run must be an object`);
  if (!isNonemptyString(run.id))
    throw new GuardError(`${branch}.run.id must be a nonempty string`);
  if (
    typeof run.status !== "string" ||
    !RUN_STATUSES.has(run.status as RunStatus)
  )
    throw new GuardError(`${branch}.run.status invalid: ${run.status}`);
  assertSpikeBackend(run.spike_backend, branch);
}

/**
 * Canonical Allow cross invariant (D6-C C3): each `converged` value admits only
 * the run statuses its schema branch names (`allowConverged`/`allowNonConverged`).
 * A mismatched Allow (e.g. converged=true with status=active, or converged=false
 * with status=converged) can never reach the hard gate — the guard rejects it
 * here so the gate fails closed.
 *
 * This does NOT require converged to be true: an Allow with converged=false is a
 * machine-approved non-convergence report and is a valid, guarded Allow.
 */
function assertAllowCrossInvariant(converged: boolean, run: unknown): void {
  if (!isObject(run)) return; // assertRunSnapshot already validated
  const admitted = ALLOW_RUN_STATUSES.get(converged);
  if (admitted === undefined || !admitted.has(String(run.status)))
    throw new GuardError(
      `Allow.converged=${converged} requires run.status in ${[...(admitted ?? [])].join("|")}`,
    );
}

/**
 * Validate that `response` is a well-formed v2 response envelope correlated to
 * `expectedRequestId`. Throws {@link GuardError} on any violation; the caller's
 * hard gate catches and fails closed.
 *
 * Checked invariants (every value set is the vendored response schema's):
 *   - protocol is exactly {@link PROTOCOL}
 *   - request_id equals the request's request_id
 *   - result.type is one of Allow | Block | Inert | Fault
 *   - Allow for GetContract: exactly {type, contract_result} with a closed contract_result
 *   - Allow: converged is boolean; run has id (nonempty string) + status (canonical)
 *     + a closed spike_backend; the status is one the `converged` value's schema
 *     branch admits (cross invariant)
 *   - Block: run has id + status; reasons is a nonempty array; each reason is an
 *     object with a nonempty string code and (if present) a string message — no
 *     deep reason-registry validation
 *   - Inert: reason is a canonical enum value
 *   - Fault: code is a canonical enum value; optional message, if present, is a
 *     string; fail_direction is a canonical enum value
 */
export function assertResponse(
  response: unknown,
  expectedRequestId: string,
  command?: string,
): asserts response is Response {
  if (!isObject(response))
    throw new GuardError("empirica response is not an object");

  if (response.protocol !== PROTOCOL)
    throw new GuardError(
      `empirica response protocol mismatch: expected ${PROTOCOL}`,
    );

  if (response.request_id !== expectedRequestId)
    throw new GuardError("empirica response request_id mismatch");

  const result = response.result;
  if (!isObject(result))
    throw new GuardError("empirica response result is not an object");

  switch (result.type) {
    case "Allow": {
      // GetContract alone answers with the one run-less Allow: exactly a closed contract_result.
      if (command === "GetContract") {
        if (!objectWithKeys(result, CONTRACT.allow)
            || !validContractResult(result.contract_result))
          throw new GuardError("GetContract Allow must be exactly one valid contract_result");
        break;
      }
      if (typeof result.converged !== "boolean")
        throw new GuardError("Allow.converged must be a boolean");
      const converged = result.converged;
      assertRunSnapshot(result.run, "Allow");
      assertAllowCrossInvariant(converged, result.run);
      break;
    }
    case "Block": {
      if (result.run !== undefined) assertRunSnapshot(result.run, "Block");
      if (!Array.isArray(result.reasons) || result.reasons.length === 0)
        throw new GuardError("Block.reasons must be a nonempty array");
      // Each reason is an object with a nonempty string code; an optional
      // message, if present, must be a string. No deep reason-registry
      // validation: the guard checks shape only, not whether code is known.
      for (const reason of result.reasons) {
        if (!isObject(reason))
          throw new GuardError("Block.reasons[] entries must be objects");
        if (!isNonemptyString(reason.code))
          throw new GuardError(
            `Block.reasons[].code must be a nonempty string: ${reason.code}`,
          );
        if (reason.message !== undefined && typeof reason.message !== "string")
          throw new GuardError(
            "Block.reasons[].message must be a string when present",
          );
      }
      if (result.run === undefined && result.reasons.some(
        (reason) => !isObject(reason) || !START_REFUSAL_REASON_CODES.has(String(reason.code))))
        throw new GuardError("Block without run must be a start refusal");
      break;
    }
    case "Inert": {
      if (
        typeof result.reason !== "string" ||
        !INERT_REASONS.has(result.reason as InertReason)
      )
        throw new GuardError(`Inert.reason invalid: ${result.reason}`);
      break;
    }
    case "Fault": {
      if (
        typeof result.code !== "string" ||
        !FAULT_CODES.has(result.code as FaultCode)
      )
        throw new GuardError(`Fault.code invalid: ${result.code}`);
      if (result.message !== undefined && typeof result.message !== "string")
        throw new GuardError("Fault.message must be a string when present");
      if (
        typeof result.fail_direction !== "string" ||
        !FAIL_DIRECTIONS.has(result.fail_direction as FailDirection)
      )
        throw new GuardError(
          `Fault.fail_direction invalid: ${result.fail_direction}`,
        );
      break;
    }
    default:
      throw new GuardError(
        `empirica response result.type unknown: ${result.type}`,
      );
  }
}
