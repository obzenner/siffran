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
  PROTOCOL,
  type FailDirection,
  type FaultCode,
  type InertReason,
  type Response,
  type RunStatus,
} from "./contract.ts";

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

function assertRunSnapshot(run: unknown, branch: string): void {
  if (!isObject(run)) throw new GuardError(`${branch}.run must be an object`);
  if (!isNonemptyString(run.id))
    throw new GuardError(`${branch}.run.id must be a nonempty string`);
  if (
    typeof run.status !== "string" ||
    !RUN_STATUSES.has(run.status as RunStatus)
  )
    throw new GuardError(`${branch}.run.status invalid: ${run.status}`);
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
      if (typeof result.converged !== "boolean")
        throw new GuardError("Allow.converged must be a boolean");
      const converged = result.converged;
      assertRunSnapshot(result.run, "Allow");
      assertAllowCrossInvariant(converged, result.run);
      break;
    }
    case "Block": {
      assertRunSnapshot(result.run, "Block");
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
