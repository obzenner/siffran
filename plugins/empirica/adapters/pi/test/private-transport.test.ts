import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import * as path from "node:path";
import test from "node:test";

import { createPrivateIngress } from "../src/private-transport.ts";

test("private ingress kills and rejects a bridge that exceeds its deadline", async () => {
  const directory = mkdtempSync(path.join(tmpdir(), "empirica-private-timeout-"));
  const script = path.join(directory, "hang.py");
  writeFileSync(script, "import time\ntime.sleep(60)\n", "utf8");
  try {
    const ingress = createPrivateIngress(25, script);
    await assert.rejects(
      ingress({ operation: "audit_failure", run_id: "run", child_id: "child" }),
      /private bridge timed out after 25ms/,
    );
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
