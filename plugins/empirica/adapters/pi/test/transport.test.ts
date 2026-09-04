// Proves the production JSON stdio transport actually round-trips a request over a
// child process, and rejects on the failure modes the gate relies on to fail
// closed. The "bridge" here is a trivial `node -e` script — deterministic, no
// network, no Python, no Pi runtime.

import { test } from "node:test";
import assert from "node:assert/strict";

import { createStdioBridgeDispatch } from "../src/stdio-transport.ts";
import { getRunRequest } from "../src/translate.ts";

// A stand-in bridge: read the whole request, echo request_id, return Inert(no_run).
const ECHO_BRIDGE =
  "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{" +
  "const r=JSON.parse(d);" +
  "process.stdout.write(JSON.stringify({protocol:'empirica/v1',request_id:r.request_id," +
  "result:{type:'Inert',reason:'no_run'}}));});";

function nodeBridge(script: string, timeoutMs = 10_000) {
  return createStdioBridgeDispatch({
    command: process.execPath,
    args: ["-e", script],
    timeoutMs,
  });
}

test("round-trips a request and parses the response envelope", async () => {
  const dispatch = nodeBridge(ECHO_BRIDGE);
  const response = await dispatch(getRunRequest("handle-9", "rid-42"));
  assert.equal(response.request_id, "rid-42"); // the bridge saw and echoed our request
  assert.equal(response.result.type, "Inert");
});

test("rejects when the bridge exits non-zero (so the gate can fail closed)", async () => {
  const dispatch = nodeBridge("process.stderr.write('boom');process.exit(3);");
  await assert.rejects(
    () => Promise.resolve(dispatch(getRunRequest("h", "r"))),
    /exited with code 3.*boom/s,
  );
});

test("rejects on unparseable bridge output", async () => {
  const dispatch = nodeBridge("process.stdout.write('not json at all');");
  await assert.rejects(
    () => Promise.resolve(dispatch(getRunRequest("h", "r"))),
    /invalid JSON/,
  );
});

test("rejects when the bridge output is not a well-formed envelope", async () => {
  const dispatch = nodeBridge("process.stdout.write(JSON.stringify({nope:true}));");
  await assert.rejects(
    () => Promise.resolve(dispatch(getRunRequest("h", "r"))),
    /well-formed envelope/,
  );
});

test("rejects when the bridge command cannot be spawned", async () => {
  const dispatch = createStdioBridgeDispatch({
    command: "definitely-not-a-real-binary-xyzzy",
    args: [],
    timeoutMs: 10_000,
  });
  await assert.rejects(
    () => Promise.resolve(dispatch(getRunRequest("h", "r"))),
    /failed to start/,
  );
});
