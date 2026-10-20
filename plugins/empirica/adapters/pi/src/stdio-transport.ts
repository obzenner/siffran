// Production transport: a JSON stdio bridge to the host-neutral Empirica v2
// core (D6-C C3).
//
// This is the concrete `Dispatch` a host uses in production. It spawns a bridge
// process, writes one `empirica/v2` request as JSON to its stdin, reads the
// single JSON response from its stdout, and guards it through the central
// inbound runtime guard before returning. It is deliberately *only* transport:
// it carries no convergence rules, no run identity, no persistence — it moves
// bytes, parses JSON, and validates the envelope (ADR-30/D6-C). All operational
// state lives wherever the bridge puts it; this file never touches the
// filesystem itself.
//
// The exact host profile is supplied to the bridge via the
// EMPIRICA_HOST_PROFILE_ID environment variable. There is no default: the
// bridge requires an explicit profile and fails closed if it is absent.

import { fileURLToPath } from "node:url";
import * as path from "node:path";

import type { Dispatch, Request, Response } from "./contract.ts";
import { assertResponse, GuardError } from "./guard.ts";
import { HOST_PROFILE_ID } from "./host-profile.ts";
import { runJsonProcess, type JsonProcessConfig } from "./process-transport.ts";

/** Ordinary public bridge calls must finish within this outer transport bound. */
export const ORDINARY_BRIDGE_TIMEOUT_MS = 30_000;
/**
 * An exact public spike request gets this outer transport margin. It is not a
 * workload budget: the Docker backend keeps its 120 s workload hard limit and
 * 210 s whole-operation bound, which reserves cleanup and postflight time.
 */
export const SPIKE_BRIDGE_TIMEOUT_MS = 300_000;

/** Canonical request-schema members mirrored only to classify the transport deadline. */
export const ROOT_REQUIRED_KEYS = ["protocol", "request_id", "command"] as const;
export const ROOT_OPTIONAL_KEYS: readonly string[] = [];
export const OBSERVE_ACTION_REQUIRED_KEYS = ["type", "run_id", "action"] as const;
export const OBSERVE_ACTION_OPTIONAL_KEYS = ["observed_at"] as const;
export const SPIKE_ACTION_REQUIRED_KEYS = [
  "kind", "claim_id", "plan",
] as const;
export const SPIKE_ACTION_OPTIONAL_KEYS: readonly string[] = [];
export const CLAIM_ID_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}(?![\\s\\S])";
const CLAIM_ID = new RegExp(CLAIM_ID_PATTERN);

export interface StdioBridgeConfig {
  /** Executable to run (e.g. "python3"). */
  command: string;
  /** Arguments (e.g. the bridge script path). */
  args?: readonly string[];
  /** Working directory for the bridge process. */
  cwd?: string;
  /** Environment for the bridge; defaults to the parent process environment
   * with EMPIRICA_HOST_PROFILE_ID set to the exact Pi profile. */
  env?: NodeJS.ProcessEnv;
  /** Test-only ordinary-call override; a positive integer no greater than 30 s. */
  timeoutMs?: number;
  /** Test-only exact-spike override; a positive integer no greater than 300 s. */
  spikeTimeoutMs?: number;
}

// plugins/empirica/adapters/pi/src/stdio-transport.ts -> plugins/empirica/adapters/pi/bridge.py
const HERE = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULT_BRIDGE_SCRIPT = path.resolve(HERE, "..", "bridge.py");

/** The default bridge invocation: the Python stdio bridge under this package. */
export function defaultBridgeConfig(): StdioBridgeConfig {
  return {
    command: process.env.EMPIRICA_PYTHON ?? "python3",
    args: [DEFAULT_BRIDGE_SCRIPT],
    timeoutMs: ORDINARY_BRIDGE_TIMEOUT_MS,
    spikeTimeoutMs: SPIKE_BRIDGE_TIMEOUT_MS,
  };
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []): boolean {
  const keys = Object.keys(value).sort();
  const allowed = [...required, ...optional].sort();
  return required.every((key) => Object.hasOwn(value, key))
    && keys.every((key) => allowed.includes(key));
}

/** Pure, closed classifier for the one request shape allowed the long outer margin. */
export function isExactSpikeRequest(value: unknown): boolean {
  if (!object(value) || !exactKeys(value, ROOT_REQUIRED_KEYS, ROOT_OPTIONAL_KEYS)
      || value.protocol !== "empirica/v2"
      || typeof value.request_id !== "string" || value.request_id.length === 0
      || !object(value.command)) return false;
  const command = value.command;
  if (!exactKeys(command, OBSERVE_ACTION_REQUIRED_KEYS, OBSERVE_ACTION_OPTIONAL_KEYS)
      || command.type !== "ObserveAction"
      || typeof command.run_id !== "string" || command.run_id.length === 0
      || (Object.hasOwn(command, "observed_at")
        && command.observed_at !== null && typeof command.observed_at !== "string")
      || !object(command.action)) return false;
  const action = command.action;
  if (!exactKeys(action, SPIKE_ACTION_REQUIRED_KEYS, SPIKE_ACTION_OPTIONAL_KEYS)
      || action.kind !== "spike_request"
      || typeof action.claim_id !== "string"
      || !CLAIM_ID.test(action.claim_id)
      || !object(action.plan)
      || action.plan.schema !== "empirica.spike-plan-draft/2") return false;
  return true;
}

function admittedOverride(value: number | undefined, maximum: number, name: string): number {
  if (value === undefined) return maximum;
  if (!Number.isSafeInteger(value) || value <= 0 || value > maximum)
    throw new GuardError(`${name} must be a positive integer no greater than ${maximum}ms`);
  return value;
}

/** Select only an outer process deadline; backend workload policy is unchanged. */
export function bridgeTimeoutMs(request: unknown, config: StdioBridgeConfig): number {
  const ordinary = admittedOverride(config.timeoutMs, ORDINARY_BRIDGE_TIMEOUT_MS, "timeoutMs");
  const spike = admittedOverride(config.spikeTimeoutMs, SPIKE_BRIDGE_TIMEOUT_MS, "spikeTimeoutMs");
  return isExactSpikeRequest(request) ? spike : ordinary;
}

/** Pure process config used by the dispatch and inspected by transport regressions. */
export function bridgeProcessConfig(config: StdioBridgeConfig, request: unknown): JsonProcessConfig {
  return {
    command: config.command,
    args: config.args,
    cwd: config.cwd,
    env: {
      ...(config.env ?? process.env),
      EMPIRICA_HOST_PROFILE_ID: HOST_PROFILE_ID,
    },
    timeoutMs: bridgeTimeoutMs(request, config),
    label: "empirica bridge",
    error: (message) => new GuardError(message),
  };
}

function guardResponse(raw: string, expectedRequestId: string): Response {
  const trimmed = raw.trim();
  if (trimmed.length === 0) {
    throw new GuardError("empirica bridge returned no output");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new GuardError(`empirica bridge returned invalid JSON: ${message}`);
  }
  // The central guard validates protocol, request_id correlation, result
  // discriminator, and minimum safe branch fields. A malformed/partial/
  // unknown/mismatched response throws here — the caller's gate fails closed.
  assertResponse(parsed, expectedRequestId);
  return parsed as Response;
}

/**
 * Build a production `Dispatch` over a JSON stdio bridge process.
 *
 * One process per request (a request is a single round-trip): the request is
 * written to stdin, the child writes one JSON response to stdout and exits 0.
 * A non-zero exit, a spawn failure, a timeout, or unparseable output all reject
 * — the caller (the gate) decides how to fail, and the hard gate fails closed
 * on a rejection. The response is guarded through the central runtime guard
 * before it is returned, so the caller never sees an unvalidated envelope.
 */
export function createStdioBridgeDispatch(config: StdioBridgeConfig): Dispatch {
  return async (request: Request): Promise<Response> => {
    const raw = await runJsonProcess(
      bridgeProcessConfig(config, request), JSON.stringify(request));
    return guardResponse(raw, request.request_id);
  };
}
