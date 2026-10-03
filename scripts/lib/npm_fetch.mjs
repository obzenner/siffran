// Fetch one exact npm package version into a directory the caller names. Injected `run` (a
// `spawnSync`-shaped function) so every failure is testable; nothing here reads the home directory,
// the repository's node_modules, or a global prefix: the npm cache and the destination are explicit.
import { spawnSync } from "node:child_process";
import { mkdirSync, readdirSync } from "node:fs";
import path from "node:path";

const EXACT = /^\d+\.\d+\.\d+$/;

function must(result, what) {
  if (result.error) throw new Error(`${what}: ${result.error.code === "ETIMEDOUT" ? "timed out" : result.error.message}`);
  if (result.status !== 0) throw new Error(`${what} exited ${result.status}: ${String(result.stderr ?? "").trim().split("\n").slice(-3).join(" | ")}`);
  return result;
}

/**
 * `npm install <name>@<version>` into `<dest>` as a private prefix (runtime dependencies resolved,
 * peers and scripts omitted — pi-subagents' peers are host-provided), returning the installed package
 * directory. The version must be an exact release.
 */
export function installPackage({ name, version, dest, cache, timeoutMs = 240_000, run = spawnSync }) {
  if (!EXACT.test(version)) throw new Error(`not an exact release: ${version}`);
  mkdirSync(dest, { recursive: true });
  must(run("npm", ["install", `${name}@${version}`, "--prefix", dest, "--cache", cache, "--ignore-scripts",
    "--omit=peer", "--omit=dev", "--no-audit", "--no-fund", "--no-package-lock", "--loglevel=error"],
  { encoding: "utf8", timeout: timeoutMs }), `npm install ${name}@${version}`);
  return path.join(dest, "node_modules", ...name.split("/"));
}

/** `npm pack <name>@<version>` and unpack it under `<dest>/package` (no dependencies installed). */
export function packPackage({ name, version, dest, cache, timeoutMs = 240_000, run = spawnSync }) {
  if (!EXACT.test(version)) throw new Error(`not an exact release: ${version}`);
  mkdirSync(dest, { recursive: true });
  must(run("npm", ["pack", `${name}@${version}`, "--pack-destination", dest, "--cache", cache, "--ignore-scripts",
    "--loglevel=error"], { encoding: "utf8", timeout: timeoutMs }), `npm pack ${name}@${version}`);
  const tarball = readdirSync(dest).find((file) => file.endsWith(".tgz"));
  if (tarball === undefined) throw new Error(`npm pack ${name}@${version} produced no tarball`);
  must(run("tar", ["-xzf", path.join(dest, tarball), "-C", dest], { encoding: "utf8", timeout: timeoutMs }), "tar -xzf");
  return path.join(dest, "package");
}

/** Every published version of `name` (`npm view <name> versions --json`), as the registry reports it. */
export function registryVersions({ name, cache, timeoutMs = 60_000, run = spawnSync }) {
  const result = must(run("npm", ["view", name, "versions", "--json", "--cache", cache, "--loglevel=error"],
    { encoding: "utf8", timeout: timeoutMs }), `npm view ${name} versions`);
  const parsed = JSON.parse(result.stdout);
  if (!Array.isArray(parsed) || !parsed.every((v) => typeof v === "string")) throw new Error("npm view did not return a list of versions");
  return parsed;
}
