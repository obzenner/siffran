import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { createEmpiricaExtension } from "../src/index.ts";
import { FakePi, FakeUi } from "./fakes.ts";

const oldCap = process.env.EMPIRICA_PI_MAX_NUDGES;
afterEach(() => { if (oldCap === undefined) delete process.env.EMPIRICA_PI_MAX_NUDGES; else process.env.EMPIRICA_PI_MAX_NUDGES = oldCap; });

function run(reason: string, revision = 1): any {
  return { type: "Block", reason, converged: false, run: { id: "nudge-run", status: "active", revision } };
}
function setup(responses: any[]) {
  const pi = new FakePi(); let i = 0;
  createEmpiricaExtension({
    dispatch: (request: any) => request.command.type === "StartRun"
      ? { protocol: "empirica/v1", request_id: "start", result: { type: "Allow", converged: false, run: { id: "nudge-run", status: "active", revision: 1 } } }
      : { protocol: "empirica/v1", request_id: "eval", result: responses[Math.min(i++, responses.length - 1)] },
    deriveSelector: () => ({ project: "p", session: "s" }),
  })(pi);
  return pi;
}

// These are intentionally assertions against the first/failing lifecycle shapes:
// no tool/text fields is the shape emitted by an idle model in Pi.
test("empty or aborted settled turns never dispatch a nudge", async () => {
  const pi = setup([run("open")]);
  await pi.command("empirica").handler("goal", { ui: new FakeUi() });
  await pi.agentSettled()({} as any, { ui: new FakeUi() });
  await pi.agentSettled()({ aborted: true } as any, { ui: new FakeUi() });
  await pi.agentSettled()({ toolCalls: [], text: "" } as any, { ui: new FakeUi() });
  assert.equal(pi.sentMessages.length, 0);
});

test("settled nudges deduplicate the same block digest", async () => {
  const pi = setup([run("same"), run("same"), run("different", 2)]);
  await pi.command("empirica").handler("goal", { ui: new FakeUi() });
  await pi.agentSettled()({ text: "productive" } as any, { ui: new FakeUi() });
  await pi.agentSettled()({ text: "productive" } as any, { ui: new FakeUi() });
  await pi.agentSettled()({ text: "productive" } as any, { ui: new FakeUi() });
  assert.equal(pi.sentMessages.length, 2);
  assert.match(pi.sentMessages[0].text, /same/);
  assert.match(pi.sentMessages[1].text, /different/);
});

test("nudge cap emits one final pause message", async () => {
  process.env.EMPIRICA_PI_MAX_NUDGES = "1";
  const pi = setup([run("one"), run("two", 2), run("three", 3)]);
  await pi.command("empirica").handler("goal", { ui: new FakeUi() });
  await pi.agentSettled()({ text: "a" } as any, { ui: new FakeUi() });
  await pi.agentSettled()({ text: "b" } as any, { ui: new FakeUi() });
  await pi.agentSettled()({ text: "c" } as any, { ui: new FakeUi() });
  assert.equal(pi.sentMessages.length, 2);
  assert.match(pi.sentMessages[1].text, /nudge loop paused after the maximum reminders/);
});
