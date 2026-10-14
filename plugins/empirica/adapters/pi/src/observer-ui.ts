/**
 * ``/empirica-ui``: the observer UI for this machine's Empirica store, served by the plugin's own observer
 * (``adapters/observer/cli.py serve``) on this machine (127.0.0.1, named empirica.localhost) and opened in the default browser. One server per
 * extension instance: a second ``/empirica-ui`` reports the running one; ``session_shutdown`` stops it. It is
 * started with ``--exit-with-stdin`` on a pipe this process holds, so it also ends when Pi does, however Pi ends.
 * Nothing here reads or judges runs; the observer does, read-only.
 */
import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
/** The observer's command line, shipped beside this adapter in the plugin. */
export const OBSERVER_CLI = path.resolve(HERE, "..", "..", "observer", "cli.py");
/** The first address the CLI prints (`empirica.localhost`); the next line, the loopback fallback, is not read. */
const URL_PATTERN = /http:\/\/empirica\.localhost:\d+\/app\/\S*/;

export interface ObserverServer {
  readonly url: string;
  /** Whether the command found an observer of the same store already running and opened that one. */
  readonly reused: boolean;
  /** Settles when the server process has ended, for whatever reason. */
  readonly exited: Promise<void>;
  stop(): void;
}

const STDERR_TAIL = 2_000;  // characters of the server's stderr kept for a startup failure

/** Start the observer server for ``cwd`` (its repository's history is read) and resolve to its address: the
 * first stdout line that names it. Settles once; a server that exits, cannot run or stays silent rejects with the
 * tail of its stderr. */
export function startObserver(cwd: string, options: { open?: boolean; timeoutMs?: number;
  command?: string; script?: string } = {}): Promise<ObserverServer> {
  const command = options.command ?? process.env.EMPIRICA_PYTHON ?? "python3";
  const args = [options.script ?? OBSERVER_CLI, "serve", "--exit-with-stdin", ...(options.open === false ? [] : ["--open"])];
  const child: ChildProcess = spawn(command, args, { cwd, stdio: ["pipe", "pipe", "pipe"] });
  const exited = new Promise<void>((done) => { child.once("exit", () => done()); });
  let err = "";
  const keep = (d: Buffer) => { err = (err + d.toString()).slice(-STDERR_TAIL); };
  child.stderr?.on("data", keep);
  const lines = createInterface({ input: child.stdout! });
  return new Promise((resolve, reject) => {
    let settled = false;
    const settle = (finish: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      lines.close();
      child.stderr?.off("data", keep);
      // Keep draining both pipes: a server whose pipe filled would block on its next log line.
      child.stdout?.resume();
      child.stderr?.resume();
      finish();
    };
    const why = (what: string) => new Error(`${what}${err.trim() ? `: ${err.trim()}` : ""}`);
    const fail = (what: string) => settle(() => { child.kill(); reject(why(what)); });
    const timer = setTimeout(() => fail("the observer did not start"), options.timeoutMs ?? 15_000);
    lines.on("line", (line) => {
      const url = URL_PATTERN.exec(line)?.[0];
      const reused = line.includes("(already running)");
      if (url) settle(() => resolve({ url, reused, exited, stop: () => { child.stdin?.end(); child.kill(); } }));
    });
    child.once("error", (e) => fail(`the observer could not run: ${e.message}`));
    // "close", not "exit": it follows the last stdout data, so a command that prints its address
    // and ends at once (reusing a running observer) is never reported as a failure.
    child.once("close", (code) => fail(`the observer exited (${code})`));
  });
}
