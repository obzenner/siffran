// Tiny Chrome DevTools driver (Node 24 global WebSocket/fetch; no deps), used by browser.journeys.mjs (withPage) and
// for screenshots. Unlike --virtual-time-budget it works with open streams. Every Chrome is kill-guarded.
// From plugins/empirica/ui/:
//   node tests/cdp.mjs shot URL OUT.png [WxH] [waitMs=600] [js evaluated before the shot]
//   node tests/cdp.mjs eval URL 'js expression' [waitMs]
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Chrome keeps the real HOME; the mock keychain and basic password store stop it from prompting for the login keychain.
export const chromeArgs = (dir, w, h) => ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--use-mock-keychain", "--password-store=basic", `--window-size=${w},${h}`, "--remote-debugging-port=0", `--user-data-dir=${dir}`, "about:blank"];

export async function withPage(url, size, fn) {
  const dir = mkdtempSync(join(tmpdir(), "cdp-"));
  const [w, h] = (size || "1500x950").split(/[x,]/).map(Number);
  const p = spawn(CHROME, chromeArgs(dir, w, h), { stdio: ["ignore", "ignore", "pipe"] });
  const guard = setTimeout(() => p.kill("SIGKILL"), 90000);
  try {
    const ws = await new Promise((res, rej) => { let buf = ""; p.stderr.on("data", (d) => { buf += d; const m = /ws:\/\/[^\s]+/.exec(buf); if (m) res(m[0]); }); setTimeout(() => rej(new Error("no devtools")), 15000); });
    const port = new URL(ws).port;
    const tgt = await (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(url)}`, { method: "PUT" })).json();
    const sock = new WebSocket(tgt.webSocketDebuggerUrl);
    await new Promise((r) => (sock.onopen = r));
    let id = 0; const pend = new Map();
    const errors = [];  // uncaught page exceptions, so a check can assert there were none
    const handlers = new Map();  // CDP event method -> handler (e.g. Fetch.requestPaused, to answer requests)
    sock.onmessage = (m) => {
      const d = JSON.parse(m.data);
      if (d.id && pend.has(d.id)) { pend.get(d.id)(d); pend.delete(d.id); }
      if (d.method === "Runtime.exceptionThrown") errors.push(d.params.exceptionDetails.exception?.description ?? d.params.exceptionDetails.text);
      if (d.method && handlers.has(d.method)) handlers.get(d.method)(d.params);
    };
    const on = (method, fn) => handlers.set(method, fn);
    const send = (method, params = {}) => new Promise((r) => { pend.set(++id, r); sock.send(JSON.stringify({ id, method, params })); });
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
    await send("Runtime.enable");
    const evalJs = async (expr) => { const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true }); if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails.exception?.description || r.result.exceptionDetails)); return r.result?.result?.value; };
    const waitReady = async (ms = 20000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await evalJs("document.body && document.body.dataset.ready")) return; await sleep(100); } throw new Error(`app never became ready${errors.length ? `: ${errors.join(" | ")}` : ""}`); };
    const key = async (k, code = k) => { for (const type of ["keyDown", "keyUp"]) await send("Input.dispatchKeyEvent", { type, key: k, code, windowsVirtualKeyCode: { ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37, ArrowRight: 39, Enter: 13, Escape: 27, Tab: 9 }[k] || 0, ...(k === "Enter" && type === "keyDown" ? { text: "\r" } : {}) }); };
    const shot = async (out) => { const r = await send("Page.captureScreenshot", { format: "png" }); writeFileSync(out, Buffer.from(r.result.data, "base64")); };
    return await fn({ send, on, evalJs, waitReady, key, shot, sleep, errors });
  } finally { clearTimeout(guard); p.kill("SIGKILL"); await sleep(200); try { rmSync(dir, { recursive: true, force: true }); } catch {} }
}

const [, , cmd, url, a3, a4, a5] = process.argv;
if (cmd && process.argv[1]?.endsWith("cdp.mjs")) {
  const r = await withPage(url, cmd === "shot" ? a4 : null, async (P) => {
    await P.waitReady();
    if (cmd === "shot") { if (process.argv[7]) await P.evalJs(process.argv[7]); await P.sleep(+a5 || 600); await P.shot(a3); return "ok"; }
    if (cmd === "eval") { await P.sleep(+a4 || 300); return await P.evalJs(a3); }
  });
  console.log(typeof r === "string" ? r : JSON.stringify(r));
  process.exit(0);
}
