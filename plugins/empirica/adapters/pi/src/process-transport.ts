import { spawn } from "node:child_process";

export interface JsonProcessConfig {
  command: string;
  args?: readonly string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  timeoutMs: number;
  label: string;
  error?: (message: string) => Error;
}

/** One bounded JSON stdio round trip; callers own parsing and schema validation. */
export function runJsonProcess(config: JsonProcessConfig, input: string): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const failure = config.error ?? ((message: string) => new Error(message));
    const child = spawn(config.command, [...(config.args ?? [])], {
      cwd: config.cwd, env: config.env, stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      fn();
    };
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish(() => reject(failure(`${config.label} timed out after ${config.timeoutMs}ms`)));
    }, config.timeoutMs);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => (stdout += chunk));
    child.stderr.on("data", (chunk: string) => (stderr += chunk));
    child.on("error", (error: Error) =>
      finish(() => reject(failure(`${config.label} failed to start: ${error.message}`))));
    child.on("close", (code: number | null) => finish(() => {
      if (code !== 0) {
        const detail = stderr.trim();
        reject(failure(`${config.label} exited with code ${code}${detail ? `: ${detail}` : ""}`));
      } else resolve(stdout);
    }));
    child.stdin.on("error", () => { /* close/error owns settlement */ });
    child.stdin.end(input);
  });
}
