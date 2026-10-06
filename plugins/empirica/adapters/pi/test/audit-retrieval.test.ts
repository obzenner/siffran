// The auditor's observed retrieval (ADR-0067): one reviewed tool -> kind table, applied to the
// `contract.tools.effectiveAllowlist` of the captured launch preflight of every reviewed pi-subagents version.

import { test } from "node:test";
import assert from "node:assert/strict";
import * as path from "node:path";

import { auditRetrievalFrom, TOOL_RETRIEVAL } from "../src/audit-retrieval.ts";
import { admitAuditPreflight, type AuditPreflightDescriptor } from "../src/preflight-seam.ts";
import { loadInventory } from "../src/subagent-inventory.ts";
import { AUDITOR, bound, FIXTURES } from "./preflight-fixtures.ts";

function descriptor(version: string): AuditPreflightDescriptor {
  return { version, inventory: loadInventory(version),
    expected: { agent_file_path: AUDITOR, model: "audit/reviewer" },
    launch: { async: false, output_mode: undefined, force_top_level_async: undefined },
    canonical: (file) => path.resolve(file) };
}

const retrieval = (version: string, allowlist: unknown) => {
  const response = bound(FIXTURES.find((f) => f.version === version)!.cases.canonical_auditor);
  response.contract.tools.effectiveAllowlist = allowlist;
  return admitAuditPreflight(descriptor(version), response);
};

test("every reviewed pi-subagents version lets the canonical auditor read files and never fetch the web", () => {
  assert.deepEqual(FIXTURES.map((f) => f.version), ["0.50.0", "0.64.0", "0.74.0", "0.75.0"]);
  for (const fixture of FIXTURES) {
    const response = bound(fixture.cases.canonical_auditor);
    assert.ok(response.contract.tools.effectiveAllowlist.includes("read"), fixture.version);
    const admission = admitAuditPreflight(descriptor(fixture.version), response);
    assert.equal(admission.kind, "accept", fixture.version);
    if (admission.kind === "accept")
      assert.deepEqual(admission.contract.audit_retrieval, ["local", "workspace"], fixture.version);
  }
});

test("the table credits only what a reviewed tool is known to do; an unknown tool adds nothing", () => {
  assert.deepEqual(TOOL_RETRIEVAL, { read: ["local", "workspace"] });
  assert.deepEqual(auditRetrievalFrom(["read", "grep", "find", "ls", "contact_supervisor"]), ["local", "workspace"]);
  assert.deepEqual(auditRetrievalFrom(["grep", "find", "ls"]), []);          // locating files is not reading them
  assert.deepEqual(auditRetrievalFrom(["web_fetch", "webfetch", "WebFetch", "fetch_url", "bash"]), []);
  assert.deepEqual(auditRetrievalFrom(["constructor", "toString", "__proto__", "hasOwnProperty"]), []);
  assert.deepEqual(auditRetrievalFrom([]), []);
  assert.deepEqual(auditRetrievalFrom(["read", "read"]), ["local", "workspace"]);
});

test("a changed allowlist changes the observed set, so a divergence at launch is visible", () => {
  const without = retrieval("0.75.0", ["grep", "find", "ls"]);
  const withRead = retrieval("0.75.0", ["read"]);
  assert.equal(without.kind === "accept" && without.contract.audit_retrieval.length, 0);
  assert.equal(withRead.kind === "accept" && withRead.contract.audit_retrieval.join(), "local,workspace");
});

test("a preflight without a usable tool allowlist is malformed, never an empty or default set", () => {
  for (const allowlist of [undefined, null, "read", { 0: "read" }, ["read", 7]]) {
    const admission = retrieval("0.75.0", allowlist);
    assert.equal(admission.kind, "refuse", JSON.stringify(allowlist));
    if (admission.kind === "refuse") assert.equal(admission.code, "malformed-response");
  }
  const response = bound(FIXTURES[0].cases.canonical_auditor);
  delete response.contract.tools;
  assert.equal(admitAuditPreflight(descriptor(FIXTURES[0].version), response).kind, "refuse");
});
