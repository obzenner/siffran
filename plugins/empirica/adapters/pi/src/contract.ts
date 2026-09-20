// Nonauthoritative reduced v2 boundary projection (D6-C C3).
//
// The canonical contract is contracts/empirica/v2/{request,response}.schema.json.
// This file is a *reduced projection* of that canonical schema: it models only the
// outbound command subset the Pi adapter genuinely dispatches (StartRun, ResolveRun,
// EvaluateRun, RestoreRun) and the minimum inbound result fields the central guard
// validates. It carries no domain rules, no convergence judgement, and no host
// policy — those live behind the transport in the host-neutral core (ADR-30/D6-C).
//
// This is NOT the contract; the schema is. If this file drifts, the guard still
// rejects an invalid response, and the conformance suite still catches a bad
// request — but a caller that compiles against these types must not assume they
// are authoritative for any field not listed here.

export const PROTOCOL = "empirica/v2";

// --- request: outbound subset only -----------------------------------------

export interface RunSelector {
  project: string;
  session: string;
}

export interface Budgets {
  max_passes?: number;
  max_spawns?: number;
}

export interface Modes {
  multi_provider?: boolean;
  cli_exec?: boolean;
}

export interface StartRunCommand {
  type: "StartRun";
  selector: RunSelector;
  goal: string;
  budgets?: Budgets;
  modes?: Modes;
}

export interface ResolveRunCommand {
  type: "ResolveRun";
  selector: RunSelector;
}

export type EvaluateIntent = "continue" | "report_convergence" | "stop";

export interface ObserveActionCommand {
  type: "ObserveAction";
  run_id: string;
  action: { kind: string; [key: string]: unknown };
}

export interface GetRunCommand {
  type: "GetRun";
  run_id: string;
}

export interface GetArgumentCommand {
  type: "GetArgument";
  run_id: string;
}

export interface GetContractCommand {
  type: "GetContract";
  target: "index" | "section" | "full";
  section_id?: string;
}

export interface EvaluateRunCommand {
  type: "EvaluateRun";
  run_id: string;
  intent: EvaluateIntent;
  observed_at?: string | null;
}

export interface RestoreRunCommand {
  type: "RestoreRun";
  run_id: string;
}

export type Command =
  | StartRunCommand
  | ResolveRunCommand
  | ObserveActionCommand
  | GetRunCommand
  | GetArgumentCommand
  | GetContractCommand
  | EvaluateRunCommand
  | RestoreRunCommand;

export interface Request {
  protocol: typeof PROTOCOL;
  request_id: string;
  command: Command;
}

// --- response: minimum guard surface ----------------------------------------
//
// The canonical response schema is far richer (RunView carries goal, modes,
// contract identity, obligations, residuals, freshness, children, host). The
// adapter reads only the minimum safe fields the guard asserts before any
// gate or render; everything else is `[key: string]: unknown` passthrough.

export type RunStatus =
  | "active"
  | "converged"
  | "stopped_residual"
  | "stopped_frozen"
  | "stopped_budget";

export type InertReason = "no_run" | "unsupported_host_event";

export type FaultCode =
  | "invalid_request"
  | "unsupported"
  | "conflict"
  | "corrupt_run"
  | "corrupt_artifacts"
  | "unavailable";

export type FailDirection = "open" | "closed";

/** Minimum run fields the guard validates on Allow/Block branches. */
export interface RunSnapshot {
  id: string;
  status: RunStatus;
  [key: string]: unknown;
}

export interface Allow {
  type: "Allow";
  converged: boolean;
  run: RunSnapshot;
  [key: string]: unknown;
}

export interface BlockReason {
  code: string;
  /** Optional human-readable message; the guard asserts it is a string if present. */
  message?: string;
  [key: string]: unknown;
}

export interface Block {
  type: "Block";
  run: RunSnapshot;
  reasons: BlockReason[];
  [key: string]: unknown;
}

export interface Inert {
  type: "Inert";
  reason: InertReason;
}

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
// host-neutral Empirica application service. The production wiring is a JSON
// stdio bridge (see stdio-transport.ts); tests inject a fake. The adapter never
// assumes which transport is in use. See ADR-30 / D6-C.
export type Dispatch = (request: Request) => Promise<Response> | Response;
