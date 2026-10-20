// Nonauthoritative reduced v2 boundary projection (D6-C C3).
//
// The canonical contract is contracts/empirica/v2/{request,response}.schema.json.
// This file is a *reduced projection* of that canonical schema: it models only the
// outbound commands the Pi adapter dispatches and the minimum inbound result fields
// the central guard validates.
//
// This is NOT the contract; the schema is. If this file drifts, the guard still
// rejects an invalid response, and the conformance suite still catches a bad
// request — but a caller that compiles against these types must not assume they
// are authoritative for any field not listed here.

import type { RetrievalKind } from "./audit-retrieval.ts";

export const PROTOCOL = "empirica/v2";

// --- request: outbound subset only -----------------------------------------

export interface RunSelector {
  project: string;
  session: string;
}

export interface Budgets {
  max_passes?: number;
  max_spawns?: number;
  max_audit_spawns?: number;
}

export interface InvocationProvenance {
  host: string;
  interactive: boolean | null;
  signal: string;
  delegation: boolean;
  /** The retrieval kinds the audit child is observed to cover (ADR-0067); recorded once, re-observed at audit launch. */
  audit_retrieval: readonly RetrievalKind[];
  /** Whether the host can show a dialog to a human now (Pi: `canPresent(ctx)`, a UI with `select` and `input`); sent again at every report_convergence. */
  human_surface: "human" | "unavailable" | "unobserved";
  /** Where the session can be found again, as the session manager reported it (Pi: the session id, and its file when it has one); absent when it reported nothing. */
  session?: { label: string; locator: string };
  /** Host-recorded audit-runtime provenance (host-runtime.ts); persisted with the run, shown only when not reviewed. */
  host_runtime?: {
    policy_id: string;
    subagents: {
      package: string; version: string; owner_path: string; package_root: string;
      preflight_path: string; source: string;
      /** Whether this exact version has a checked-in compat record (false: admitted by observation, ADR-0069). */
      reviewed: boolean;
    };
  };
}

export type SpikeProfile = "docker-isolated-v1" | "docker-networked-v1";

export interface StartRunCommand {
  type: "StartRun";
  control_mode: "auto" | "deliberative";
  selector: RunSelector;
  goal: string;
  invocation: InvocationProvenance;
  budgets?: Budgets;
  /** Optional host-launch authority selected before StartRun; never author-controlled. */
  spike_profile?: SpikeProfile;
}

export interface SpikePlanDraft {
  schema: "empirica.spike-plan-draft/2";
  image: string;
  argv: string[];
  inputs: string[];
  environment: Record<string, string>;
}

export interface SpikeRequestAction {
  kind: "spike_request";
  claim_id: string;
  plan: SpikePlanDraft;
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
  target: "index" | "section";
  section_id?: string;
}

/** What a host reports when it could not do the mediation a report asks of it (nobody was asked); never stored. */
export type HostFailure = "surface_unrecorded" | "presentation_failed";

export interface EvaluateRunCommand {
  type: "EvaluateRun";
  run_id: string;
  intent: EvaluateIntent;
  observed_at?: string | null;
  host_failure?: HostFailure;
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
// The canonical response schema is far richer (RunView carries goal,
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

export interface ConfirmationBatch {
  key: string;
  record_ids: string[];
  claim_ids: string[];
  /** Only the terminal step of a deliberative run: the one item asks whether to end the run as `status`. */
  terminal?: { status: "stopped_frozen" | "stopped_residual" | "stopped_budget"; digest: string };
  records: { claim_id: string; source_kind: string; source_ref: string }[];
  prompt_error: null | "unavailable" | "unobserved" | "already_presented" | "interaction_limit" | HostFailure;
}

/** Whether a confirmation batch is the terminal step of a deliberative run (ADR-0063): one item that asks whether to end
 * the run as `terminal.status`. The one predicate every caller uses (Python: `core.confirmation.is_terminal`). */
export function isTerminalBatch(batch: unknown): batch is ConfirmationBatch & { terminal: NonNullable<ConfirmationBatch["terminal"]> } {
  return typeof batch === "object" && batch !== null && typeof (batch as ConfirmationBatch).terminal === "object"
    && (batch as ConfirmationBatch).terminal !== null;
}

/** The run's immutable spike authority selected once at StartRun; qualified is binding, not live health. */
export type SpikeBackend =
  | { kind: "spikes_unavailable" }
  | {
      kind: "qualified";
      backend: "docker";
      profile_id: SpikeProfile;
      qualification_record_digest: string;
      execution_binding_digest: string;
    };

/** Minimum run fields the guard validates on Allow/Block branches. */
export interface RunSnapshot {
  id: string;
  status: RunStatus;
  spike_backend: SpikeBackend;
  /** The core's progress basis (a digest; `core.evaluation.progress_basis`): the one thing a host compares to know whether the run moved. */
  basis?: string;
  /** What a human would be shown now, or null (the core's projection; the host presents when `prompt_error` is null). */
  confirmation_batch?: ConfirmationBatch | null;
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
  run?: RunSnapshot;
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
