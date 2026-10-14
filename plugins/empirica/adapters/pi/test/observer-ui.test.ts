// /empirica-ui's launcher (src/observer-ui.ts): it starts the plugin's own observer server, resolves to its
// loopback address, and stops it; a server that cannot start is an error with the reason, not a hang.
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { OBSERVER_CLI, startObserver } from "../src/observer-ui.ts";

/** Run ``body`` with an empty store as ``EMPIRICA_HOME`` (nothing of the user's is read), then restore it. */
async function withEmptyHome(body: (home: string) => Promise<void>): Promise<void> {
  const home = mkdtempSync(path.join(tmpdir(), "empirica-home-"));
  const before = process.env.EMPIRICA_HOME;
  process.env.EMPIRICA_HOME = home;
  try { await body(home); } finally {
    if (before === undefined) delete process.env.EMPIRICA_HOME; else process.env.EMPIRICA_HOME = before;
    rmSync(home, { recursive: true, force: true });
  }
}

test("the launcher starts the observer, which serves the store summary on 127.0.0.1", () => withEmptyHome(async (home) => {
  const server = await startObserver(home, { open: false });
  try {
    assert.match(server.url, /^http:\/\/empirica\.localhost:\d+\/app\/$/);
    assert.equal(server.reused, false);
    // The test resolves no *.localhost name: it asks the loopback address, with the server's own name as the Host.
    const loopback = new URL("/api/summary", server.url.replace("empirica.localhost", "127.0.0.1"));
    const view = await (await fetch(loopback)).json() as { contract: string; projects: unknown[] };
    assert.deepEqual([view.contract, view.projects], ["empirica-ui-view/v1", []]);
  } finally { server.stop(); }
}));

test("a server that ends is reported as ended, and its stop is harmless after", () => withEmptyHome(async (home) => {
  const server = await startObserver(home, { open: false });
  server.stop();
  await server.exited;
  await assert.rejects(fetch(new URL("/api/summary", server.url.replace("empirica.localhost", "127.0.0.1"))));
  server.stop();
}));

test("the observer the launcher runs is the one shipped in the plugin", () => {
  assert.equal(path.relative(path.resolve(import.meta.dirname, "..", ".."), OBSERVER_CLI), path.join("observer", "cli.py"));
});

test("a silent observer times out with the tail of its stderr, and is stopped", async () => {
  const dir = mkdtempSync(path.join(tmpdir(), "empirica-observer-"));
  const script = path.join(dir, "silent.py");
  writeFileSync(script, "import sys, time\nsys.stderr.write('x' * 5000 + 'still starting')\nsys.stderr.flush()\ntime.sleep(30)\n");
  try {
    const error = await startObserver(dir, { open: false, script, timeoutMs: 1_500 }).then(() => null, (e: Error) => e);
    assert.match(error?.message ?? "", /did not start: x+still starting$/);
    assert.ok((error?.message.length ?? 0) < 2_100, "only the tail of stderr is kept");
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("an observer that exits before serving rejects with its reason", async () => {
  const dir = mkdtempSync(path.join(tmpdir(), "empirica-observer-"));
  const script = path.join(dir, "broken.py");
  writeFileSync(script, "import sys\nsys.stderr.write('no store here')\nsys.exit(3)\n");
  try {
    await assert.rejects(startObserver(dir, { open: false, script }), /exited \(3\): no store here/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("a command that reuses a running observer resolves to it, marked as reused", async () => {
  const dir = mkdtempSync(path.join(tmpdir(), "empirica-observer-"));
  const script = path.join(dir, "reuse.py");  // prints what cli.py serve prints when it reuses, then ends
  writeFileSync(script, 'print("Empirica observer (already running): http://empirica.localhost:8765/app/")\n'
    + 'print("  If your browser does not resolve *.localhost: http://127.0.0.1:8765/app/")\n');
  try {
    for (let i = 0; i < 20; i++) {  // it ends at once: its end must never win over its last line
      const reused = await startObserver(dir, { open: false, script });
      assert.deepEqual([reused.url, reused.reused], ["http://empirica.localhost:8765/app/", true]);
      await reused.exited;
    }
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
