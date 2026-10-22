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

import { PUBLIC_TOOLS } from "./public-tools.ts";

import {
  PROTOCOL,
  type FailDirection,
  type FaultCode,
  type InertReason,
  type Response,
  type RunStatus,
} from "./contract.ts";

type Json = Record<string, any>;

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
  if (!objectWithKeys(value, (value as Json | null)?.target === "index"
    ? ["target", "digest", "index"] : ["target", "digest", "section_id", "section"])) return false;
  if (!text(value.digest) || !/^sha256:[0-9a-f]{64}$/.test(value.digest)) return false;
  if (value.target === "index") {
    const index = value.index;
    return objectWithKeys(index, ["id", "version", "sections", "reasons", "next_actions"])
      && index.id === "empirica/public" && index.version === "6.0.0"
      && rows(index.sections, ["id", "title"], ["id", "title"])
      && Array.isArray(index.reasons) && unique(index.reasons) && index.reasons.every((reason) =>
        objectWithKeys(reason, ["code", "sections"]) && text(reason.code)
        && Array.isArray(reason.sections) && reason.sections.every((section) => text(section)))
      && rows(index.next_actions, ["id", "description"], ["id", "description"]);
  }
  if (value.target !== "section" || !text(value.section_id, true)) return false;
  const section = value.section;
  return objectWithKeys(section, ["id", "title", "summary", "clauses"])
    && text(section.id, true) && section.id === value.section_id && text(section.title, true)
    && text(section.summary) && rows(section.clauses, ["id", "text"], ["id", "text"])
    && section.clauses.every((clause: Json) => text(clause.id, true) && text(clause.text, true));
}

const START_REFUSAL_REASON_CODES = new Set<string>(PUBLIC_TOOLS.start_refusal_codes);
const RUN_STATUSES: ReadonlySet<RunStatus> = new Set([
  "active",
  "converged",
  "stopped_residual",
  "stopped_frozen",
  "stopped_budget",
]);

const INERT_REASONS: ReadonlySet<InertReason> = new Set([
  "no_run",
  "unsupported_host_event",
]);

const FAULT_CODES: ReadonlySet<FaultCode> = new Set([
  "invalid_request",
  "unsupported",
  "conflict",
  "corrupt_run",
  "corrupt_artifacts",
  "unavailable",
]);

const FAIL_DIRECTIONS: ReadonlySet<FailDirection> = new Set(["open", "closed"]);

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

const DIGEST = /^sha256:[0-9a-f]{64}$/;

function assertSpikeBackend(value: unknown, branch: string): void {
  if (!isObject(value) || Array.isArray(value))
    throw new GuardError(`${branch}.run.spike_backend must be an object`);
  const keys = Object.keys(value).sort().join("\0");
  if (value.kind === "spikes_unavailable") {
    if (keys !== "kind") throw new GuardError(`${branch}.run.spike_backend unavailable shape invalid`);
    return;
  }
  const qualified = ["backend", "execution_binding_digest", "kind", "profile_id", "qualification_record_digest"]
    .sort().join("\0");
  if (value.kind !== "qualified" || keys !== qualified || value.backend !== "docker"
      || !new Set(["docker-isolated-v1", "docker-networked-v1"]).has(String(value.profile_id))
      || typeof value.qualification_record_digest !== "string"
      || !DIGEST.test(value.qualification_record_digest)
      || typeof value.execution_binding_digest !== "string"
      || !DIGEST.test(value.execution_binding_digest))
    throw new GuardError(`${branch}.run.spike_backend qualified shape invalid`);
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
 * Canonical Allow cross invariant (D6-C C3): converged is true *iff* the run
 * status is `converged`, and false only for the active/stopped_* statuses. A
 * mismatched Allow (e.g. converged=true with status=active, or converged=false
 * with status=converged) can never reach the hard gate — the guard rejects it
 * here so the gate fails closed.
 *
 * This does NOT require converged to be true: an Allow with converged=false is a
 * machine-approved non-convergence report and is a valid, guarded Allow.
 */
function assertAllowCrossInvariant(converged: boolean, run: unknown): void {
  if (!isObject(run)) return; // assertRunSnapshot already validated
  const status = run.status;
  if (converged && status !== "converged")
    throw new GuardError(
      "Allow.converged=true requires run.status=converged",
    );
  if (!converged && status === "converged")
    throw new GuardError(
      "Allow.converged=false requires run.status != converged",
    );
}

/**
 * Validate that `response` is a well-formed v2 response envelope correlated to
 * `expectedRequestId`. Throws {@link GuardError} on any violation; the caller's
 * hard gate catches and fails closed.
 *
 * Checked invariants:
 *   - protocol is exactly `empirica/v2`
 *   - request_id equals the request's request_id
 *   - result.type is one of Allow | Block | Inert | Fault
 *   - Allow for GetContract: exactly {type, contract_result} with a closed contract_result
 *   - Allow: converged is boolean; run has id (nonempty string) + status (canonical);
 *     converged true iff status is `converged`; converged false only for
 *     active/stopped_residual/stopped_frozen/stopped_budget (cross invariant)
 *   - Block: run has id + status; reasons is a nonempty array; each reason is an
 *     object with a nonempty string code and (if present) a string message — no
 *     deep reason-registry validation
 *   - Inert: reason is a canonical enum value
 *   - Fault: code is a canonical enum value; optional message, if present, is a
 *     string; fail_direction is open | closed
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
        if (!objectWithKeys(result, ["type", "contract_result"])
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
