// TypeScript view of the `methodologist/v1` domain contract.
//
// These types mirror contracts/methodologist/v1/{request,response}.schema.json
// (the shared, host-neutral envelope from ADR-30). They carry no Pi concept:
// the adapter translates Pi invocations *into* a Request and renders a Response
// *out* to Pi capabilities; the meaning of each shape is owned by the core, not
// by this file.

export const PROTOCOL = "methodologist/v1";

export interface SelectMethodologyCommand {
  type: "SelectMethodology";
  intent: string;
  requested_methodology?: string | null;
}

export interface CompletePhaseCommand {
  type: "CompletePhase";
  run_id: string;
  phase: number;
  output: string;
}

export interface ProduceArtifactCommand {
  type: "ProduceArtifact";
  run_id: string;
}

export type Command =
  | SelectMethodologyCommand
  | CompletePhaseCommand
  | ProduceArtifactCommand;

export interface Request {
  protocol: typeof PROTOCOL;
  request_id: string;
  command: Command;
}

// A phase as the core reports it. The response schema pins only that `phases`
// is a non-empty array (additionalProperties allowed), so we read titles
// defensively and fall back to the phase index for display.
export interface PhaseSpec {
  id?: string;
  title?: string;
  number?: number;
  [key: string]: unknown;
}

export interface MethodologySelected {
  type: "MethodologySelected";
  methodology: string;
  reason: string;
  phases: PhaseSpec[];
  [key: string]: unknown;
}

export interface HumanDecisionRequired {
  type: "HumanDecisionRequired";
  // Schema: exactly two candidates. Shape is open; we read `name`/`rationale`
  // when present and fall back to the raw value.
  candidates: unknown[];
  question: string;
}

export interface PhaseAdvanced {
  type: "PhaseAdvanced";
  run_id: string;
  next_phase: number | null;
  [key: string]: unknown;
}

export type FaultCode =
  | "invalid_request"
  | "unknown_methodology"
  | "out_of_order"
  | "invalid_run";

export interface Fault {
  type: "Fault";
  code: FaultCode;
}

export type Result =
  | MethodologySelected
  | HumanDecisionRequired
  | PhaseAdvanced
  | Fault;

export interface Response {
  protocol: typeof PROTOCOL;
  request_id: string;
  result: Result;
}

// The seam to the core. A host wires this to whatever transport reaches the
// host-neutral Methodologist core (in-process, subprocess, RPC). The adapter
// never assumes which. See ADR-30 / ADR-32.
export type Dispatch = (request: Request) => Promise<Response> | Response;
