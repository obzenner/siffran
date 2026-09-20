// Guard mutation matrix (D6-C C3). Proves that every malformed, partial,
// unknown, or mismatched response is rejected by the central inbound runtime
// guard — so a malformed Allow/Inert/Block/Fault can never permit the
// report_convergence hard gate. The guard is the trust boundary: anything it
// does not validate throws, and the hard gate catches and fails closed.

import { test } from "node:test";
import assert from "node:assert/strict";

import { assertResponse, GuardError } from "../src/guard.ts";
import type { Response, RunStatus } from "../src/contract.ts";

const RID = "rid-1";

function ok(result: Response["result"]): Response {
  return { protocol: "empirica/v2", request_id: RID, result };
}

function run(status: RunStatus = "active"): { id: string; status: RunStatus } {
  return { id: "run-1", status };
}

function expectThrow(label: string, response: unknown, rid = RID): void {
  test(`guard rejects: ${label}`, () => {
    assert.throws(
      () => assertResponse(response, rid),
      (e: unknown) => e instanceof GuardError,
    );
  });
}

// --- protocol / request_id / structure ---------------------------------------

expectThrow("not an object", null);
expectThrow("not an object (string)", "hello");
expectThrow("wrong protocol", { ...ok({ type: "Inert", reason: "no_run" }), protocol: "empirica/v3" });
expectThrow("missing protocol", { request_id: RID, result: { type: "Inert", reason: "no_run" } });
expectThrow("request_id mismatch", ok({ type: "Inert", reason: "no_run" }), "other-rid");
expectThrow("missing request_id", { protocol: "empirica/v2", result: { type: "Inert", reason: "no_run" } });
expectThrow("result not an object", { protocol: "empirica/v2", request_id: RID, result: null });
expectThrow("missing result", { protocol: "empirica/v2", request_id: RID });
expectThrow("unknown result.type", ok({ type: "Wat" } as never));

// --- Allow mutations ---------------------------------------------------------

expectThrow("Allow missing converged", ok({ type: "Allow", run: run() } as never));
expectThrow("Allow converged not boolean", ok({ type: "Allow", converged: "true", run: run() } as never));
expectThrow("Allow missing run", ok({ type: "Allow", converged: true } as never));
expectThrow("Allow run not object", ok({ type: "Allow", converged: true, run: null } as never));
expectThrow("Allow run.id missing", ok({ type: "Allow", converged: true, run: { status: "active" } } as never));
expectThrow("Allow run.id empty", ok({ type: "Allow", converged: true, run: { id: "", status: "active" } } as never));
expectThrow("Allow run.status missing", ok({ type: "Allow", converged: true, run: { id: "r" } } as never));
expectThrow("Allow run.status invalid", ok({ type: "Allow", converged: true, run: { id: "r", status: "unknown" } as never }));

// --- Allow cross invariant mutations (D6-C C3) ------------------------------
// converged true iff status converged; converged false only non-converged statuses.

expectThrow("Allow converged=true with status=active", ok({ type: "Allow", converged: true, run: run("active") } as never));
expectThrow("Allow converged=true with status=stopped_budget", ok({ type: "Allow", converged: true, run: run("stopped_budget") } as never));
expectThrow("Allow converged=false with status=converged", ok({ type: "Allow", converged: false, run: run("converged") } as never));

// --- Block mutations ---------------------------------------------------------

expectThrow("Block missing run", ok({ type: "Block", reasons: [{ code: "x" }] } as never));
expectThrow("Block run.id missing", ok({ type: "Block", run: { status: "active" }, reasons: [{ code: "x" }] } as never));
expectThrow("Block run.status invalid", ok({ type: "Block", run: { id: "r", status: "bad" }, reasons: [{ code: "x" }] } as never));
expectThrow("Block missing reasons", ok({ type: "Block", run: run() } as never));
expectThrow("Block reasons empty array", ok({ type: "Block", run: run(), reasons: [] } as never));
expectThrow("Block reasons not array", ok({ type: "Block", run: run(), reasons: "x" } as never));
expectThrow("Block reason null entry", ok({ type: "Block", run: run(), reasons: [null] } as never));
expectThrow("Block reason empty object", ok({ type: "Block", run: run(), reasons: [{}] } as never));
expectThrow("Block reason numeric code", ok({ type: "Block", run: run(), reasons: [{ code: 42 }] } as never));
expectThrow("Block reason empty code", ok({ type: "Block", run: run(), reasons: [{ code: "" }] } as never));
expectThrow("Block reason numeric message", ok({ type: "Block", run: run(), reasons: [{ code: "x", message: 42 }] } as never));

// --- Inert mutations ---------------------------------------------------------

expectThrow("Inert missing reason", ok({ type: "Inert" } as never));
expectThrow("Inert reason invalid", ok({ type: "Inert", reason: "bogus" } as never));
expectThrow("Inert reason not string", ok({ type: "Inert", reason: 42 } as never));

// --- Fault mutations ---------------------------------------------------------

expectThrow("Fault missing code", ok({ type: "Fault", fail_direction: "closed" } as never));
expectThrow("Fault code invalid", ok({ type: "Fault", code: "bogus", fail_direction: "closed" } as never));
expectThrow("Fault missing fail_direction", ok({ type: "Fault", code: "unavailable" } as never));
expectThrow("Fault fail_direction invalid", ok({ type: "Fault", code: "unavailable", fail_direction: "sideways" } as never));
expectThrow("Fault numeric message", ok({ type: "Fault", code: "unavailable", message: 42, fail_direction: "closed" } as never));

// --- valid responses pass ----------------------------------------------------

test("guard accepts valid Allow converged", () => {
  assertResponse(ok({ type: "Allow", converged: true, run: run("converged") }), RID);
});

test("guard accepts valid Allow not converged (all non-converged statuses)", () => {
  for (const status of ["active", "stopped_residual", "stopped_frozen", "stopped_budget"] as const) {
    assertResponse(ok({ type: "Allow", converged: false, run: run(status) }), RID);
  }
});

test("guard accepts valid Block", () => {
  assertResponse(ok({ type: "Block", run: run(), reasons: [{ code: "claim.research_missing" }] }), RID);
});

test("guard accepts valid Block with a string message", () => {
  assertResponse(ok({ type: "Block", run: run(), reasons: [{ code: "claim.research_missing", message: "3 claims lack evidence" }] }), RID);
});

test("guard accepts valid Inert", () => {
  assertResponse(ok({ type: "Inert", reason: "no_run" }), RID);
  assertResponse(ok({ type: "Inert", reason: "unsupported_host_event" }), RID);
});

test("guard accepts valid Fault", () => {
  assertResponse(ok({ type: "Fault", code: "unavailable", fail_direction: "closed" }), RID);
  assertResponse(ok({ type: "Fault", code: "corrupt_run", message: "x", fail_direction: "open" }), RID);
});
