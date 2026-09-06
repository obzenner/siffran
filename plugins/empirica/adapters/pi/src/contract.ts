// TypeScript view of the `empirica/v1` domain contract.
//
// These types mirror contracts/empirica/v1/{request,response}.schema.json (the
// shared, host-neutral envelope from ADR-30). They carry no Pi concept: the
// adapter translates Pi events *into* a Request and maps a Result *out* to Pi
// enforcement/UI. The meaning of each shape — what converges, what is gated — is
// owned by the core behind the transport, never by this file (ADR-30: "Core
// decisions do not contain hook names, Pi event names, paths, … or exit codes").

export const PROTOCOL = "empirica/v1";

// --- request ---------------------------------------------------------------

/** Identifies the run a StartRun opens; every later command uses the returned handle. */
export interface RunSelector {
  project: string;
  session: string;
}

export interface RunModes {
  multi_provider?: boolean;
  cli_exec?: boolean;
}

export interface StartRunCommand {
  type: "StartRun";
  selector: RunSelector;
  goal: string;
  max_passes?: number;
  max_spawns?: number | null;
  modes?: RunModes;
}

export interface ObserveActionCommand {
  type: "ObserveAction";
  run_id: string;
  action: { kind: string; [key: string]: unknown };
  observed_at?: string | null;
}

export type EvaluateIntent = "continue" | "report_convergence" | "stop";

export interface EvaluateRunCommand {
  type: "EvaluateRun";
  run_id: string;
  intent: EvaluateIntent;
  // Wall-clock observation time in epoch SECONDS (float). The core uses it to
  // enforce a "time since last progress" stall deadline on a blocking run.
  // Optional for backward compatibility: an older core ignores it, and an older
  // adapter that omits it still validates against the request schema.
  observed_at?: number;
}

export interface GetRunCommand {
  type: "GetRun";
  run_id: string;
}

export type Command =
  | StartRunCommand
  | ObserveActionCommand
  | EvaluateRunCommand
  | GetRunCommand;

export interface Request {
  protocol: typeof PROTOCOL;
  request_id: string;
  command: Command;
}

// --- response --------------------------------------------------------------

export type RunStatus =
  | "active"
  | "converged"
  | "stopped_residual"
  | "stopped_frozen"
  | "stopped_budget";

// The response schema pins only id/status/revision on `run` and allows further
// advisory reporting fields (note, deferred, blocked, audit, …), so we read it
// as an open record.
export interface RunSnapshot {
  id: string;
  status: RunStatus;
  revision: number;
  [key: string]: unknown;
}

export interface Allow {
  type: "Allow";
  converged: boolean;
  run: RunSnapshot;
  [key: string]: unknown;
}

export interface Block {
  type: "Block";
  reason: string;
  run: RunSnapshot;
  [key: string]: unknown;
}

export type InertReason = "no_run" | "unsupported_host_event";

export interface Inert {
  type: "Inert";
  reason: InertReason;
}

export type FaultCode =
  | "invalid_request"
  | "unsupported"
  | "conflict"
  | "corrupt_run"
  | "corrupt_artifacts"
  | "unavailable";

/** `fail_direction` tells an adapter how to behave when it cannot get a clean
 * decision: "closed" means refuse the gated operation, "open" means permit it. */
export type FailDirection = "open" | "closed";

export interface Fault {
  type: "Fault";
  code: FaultCode;
  message?: string;
  fail_direction: FailDirection;
}

export type Result = Allow | Block | Inert | Fault;

export interface Response {
  protocol: typeof PROTOCOL;
  request_id: string;
  result: Result;
}

// The seam to the core. A host wires this to whatever transport reaches the
// host-neutral Empirica application service (in-process, subprocess, RPC). The
// production wiring is a JSON stdio bridge (see stdio-transport.ts); the adapter
// never assumes which transport is in use. See ADR-30 / ADR-32.
export type Dispatch = (request: Request) => Promise<Response> | Response;
