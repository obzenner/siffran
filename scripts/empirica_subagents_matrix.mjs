#!/usr/bin/env node
// The reviewed pi-subagents matrix: prove, for every version that has a checked-in compat record, that the
// checked-in evidence still describes the package the registry serves (Empirica 4.1, D3/D7; ADR-0069).
//
// Per reviewed version (the versions with a `compat/pi-subagents-<version>.json`, each of which must lie in the
// range of `subagents_compatibility` in contracts/empirica/v2/host-profiles.json; the version the repository's
// devDependency pins is used in place, every other one is fetched from the registry into
// <TMPDIR>/empirica-subagents-matrix/<version>/):
//   inventory   `gen_pi_subagents_inventory.mjs --check` — the generated launch/action inventory equals
//               the checked-in compat/pi-subagents-<version>.json
//   schema      every parameter the adapter writes on the audit launch (`auditLaunchInput`) is declared
//               by the package's own `subagent` parameter schema
//   preflight   a live `pi-subagents/preflight` capture equals the checked-in fixture, modulo masked paths
//   classifier  the call classifier, given this version's inventory, admits exactly the reviewed surface:
//               each launch form alone is executable, two are malformed, every action outside the
//               read-only allowlist is unsupported, every allowlisted one present is management
//   retrieval   the retrieval kinds derived from the live preflight's `contract.tools.effectiveAllowlist`
//               through the adapter's one tool->kind table equal the set reviewed for every version (ADR-0067)
// One JSON line per version on stdout; exit 1 when any version fails, times out, or cannot be fetched.
//
// Usage: node scripts/empirica_subagents_matrix.mjs [--root DIR]
//        node scripts/empirica_subagents_matrix.mjs --update   list registry versions newer than the newest reviewed
// (The hidden `--cell` mode is the per-version child; each cell runs in its own process so a hang is a timeout.)
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { installPackage, registryVersions } from "./lib/npm_fetch.mjs";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SELF = fileURLToPath(import.meta.url);
export const PACKAGE = "pi-subagents";
export const CELL_TIMEOUT_MS = 180_000;
const EXACT = /^\d+\.\d+\.\d+$/;

// --- pure -----------------------------------------------------------------------------------------

/** `[major, minor, patch]` of an exact release; throws for anything else (ranges, prereleases). */
export function parseVersion(version) {
  if (!EXACT.test(version)) throw new Error(`not an exact release: ${version}`);
  return version.split(".").map(Number);
}

export function compareVersions(a, b) {
  const [x, y] = [parseVersion(a), parseVersion(b)];
  for (let i = 0; i < 3; i += 1) if (x[i] !== y[i]) return x[i] - y[i];
  return 0;
}

export const COMPAT_DIR = path.join(repo, "plugins", "empirica", "adapters", "pi", "compat");

/**
 * The reviewed versions (those with a checked-in compat record, ascending) and the policy id of the Pi profile.
 * A record outside the profile's range is dead evidence and an error.
 */
export function reviewedFrom(document, compatDir = COMPAT_DIR) {
  const found = document.profiles.filter((profile) => profile.subagents_compatibility !== undefined);
  if (found.length !== 1) throw new Error(`expected exactly one profile with subagents_compatibility, found ${found.length}`);
  const policy = found[0].subagents_compatibility;
  if (policy.package !== PACKAGE) throw new Error(`subagents_compatibility is for ${policy.package}, not ${PACKAGE}`);
  const versions = readdirSync(compatDir).map((file) => new RegExp(`^${PACKAGE}-(.+)\\.json$`).exec(file)?.[1])
    .filter((version) => version !== undefined).sort(compareVersions);
  const outside = versions.filter((version) => compareVersions(version, policy.minimum) < 0
    || compareVersions(version, policy.maximum_exclusive) >= 0);
  if (outside.length > 0)
    throw new Error(`compat records outside the supported range [${policy.minimum}, ${policy.maximum_exclusive}): ${outside.join(", ")}`);
  return { versions, policy_id: policy.policy_id };
}

/** One cell per reviewed version: the devDependency's own version runs in place, the rest are fetched. */
export function planCells(reviewed, localVersion) {
  return reviewed.map((version) => ({ version, source: version === localVersion ? "local" : "registry" }));
}

/** Registry releases strictly newer than the newest reviewed (exact releases only, ascending). */
export function newerThanReviewed(registry, reviewed) {
  const newest = [...reviewed].sort(compareVersions).at(-1);
  return registry.filter((version) => EXACT.test(version) && compareVersions(version, newest) > 0).sort(compareVersions);
}

/** The commands an operator runs to review `version`; nothing is promoted automatically. */
export function reviewCommands(version, root) {
  const dir = `${root}/${version}`;
  return [
    `npm install ${PACKAGE}@${version} --prefix ${dir}/install --ignore-scripts --omit=peer --omit=dev --no-package-lock --cache ${root}/npm-cache`,
    `make pi-subagents-inventory PACKAGE_ROOT=${dir}/install/node_modules/${PACKAGE}`,
    `make pi-preflight-fixture PACKAGE_ROOT=${dir}/install/node_modules/${PACKAGE}`,
    `# review the two generated files and add the classifier cases; "${version}" is reviewed once its compat record is checked in (it must lie in the profile's range)`,
    "make contract-schemas vendor-contracts contract-check && make empirica-subagents-matrix",
  ];
}

/**
 * The four checks of one version, from evidence already read. Pure: every input is data.
 * `expected` is the checked-in evidence, `observed` what the package serves now.
 */
export function evaluateCell({ version, request, properties, inventory, liveFixture, checkedFixture, classify, managementActions, retrieval }) {
  const checks = {};
  const missing = Object.keys(request).filter((key) => !properties.includes(key));
  checks.schema = missing.length === 0 ? { ok: true } : { ok: false, detail: `parameters not declared by the package: ${missing.join(", ")}` };
  const same = JSON.stringify(maskMachineDigests(liveFixture)) === JSON.stringify(maskMachineDigests(checkedFixture));
  checks.preflight = same ? { ok: true } : { ok: false, detail: "live preflight differs from the checked-in fixture" };
  checks.classifier = classifierVerdict({ version, inventory, classify, managementActions });
  if (retrieval !== undefined) checks.retrieval = retrievalVerdict(version, retrieval);
  return checks;
}

/** The retrieval kinds every reviewed pi-subagents version is pinned to give the canonical auditor: it reads files, never the web. */
export const REVIEWED_RETRIEVAL = ["local", "workspace"];

/** `observed` is derived from the live preflight by the adapter's own table; it must equal the reviewed set. */
export function retrievalVerdict(version, { observed, reviewed = REVIEWED_RETRIEVAL }) {
  return JSON.stringify(observed) === JSON.stringify(reviewed) ? { ok: true }
    : { ok: false, detail: `${version}: the auditor's retrieval is ${JSON.stringify(observed)}, reviewed ${JSON.stringify(reviewed)}` };
}

/** The launch-contract members that are hashes over path-bearing content (verified: the same package
 *  installed in two directories yields two values, and nothing in the adapter reads either). */
export const PATH_BEARING_DIGESTS = ["digest", "launchContractDigest"];

/**
 * A contract digest hashes the launch contract with its machine-specific paths, so it differs between
 * install directories by construction. It is masked only when it is a sha256 hex string; anything else
 * (a missing or malformed digest) still differs from the checked-in fixture.
 */
export function maskMachineDigests(value) {
  if (Array.isArray(value)) return value.map(maskMachineDigests);
  if (value === null || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).map(([key, member]) => [key,
    PATH_BEARING_DIGESTS.includes(key) && typeof member === "string" && /^[0-9a-f]{64}$/.test(member)
      ? "<path-bearing digest>" : maskMachineDigests(member)]));
}

/** Classifier property cases over one inventory; the first violation is reported. */
export function classifierVerdict({ version, inventory, classify, managementActions }) {
  const expect = (label, input, kind) => {
    const got = classify(input, inventory).kind;
    return got === kind ? undefined : `${label}: expected ${kind}, got ${got}`;
  };
  const problems = [];
  for (const form of inventory.launch_forms) problems.push(expect(`launch form ${form}`, { [form]: "x" }, "executable"));
  if (inventory.launch_forms.length >= 2)
    problems.push(expect("two launch forms", { [inventory.launch_forms[0]]: "x", [inventory.launch_forms[1]]: "x" }, "malformed"));
  problems.push(expect("no action and no form", {}, "malformed"));
  problems.push(expect("an unknown action", { action: "__not_an_action__" }, "unsupported"));
  for (const action of inventory.actions) {
    if (!managementActions.has(action)) problems.push(expect(`action ${action} outside the read-only allowlist`, { action }, "unsupported"));
    else if (action === "validate" && !inventory.supports.validateOffline) problems.push(expect("validate without the offline guarantee", { action }, "unsupported"));
    else problems.push(expect(`allowlisted action ${action}`, { action, ...(managementActions.get(action).agentTarget ? { agent: "x" } : {}) }, "management"));
  }
  const failed = problems.find((problem) => problem !== undefined);
  return failed === undefined ? { ok: true, cases: problems.length } : { ok: false, detail: `${version}: ${failed}` };
}

/** `{ok, detail?}` of a finished child process (`spawnSync`-shaped); a timeout or signal is a failure. */
export function processVerdict(result, what) {
  if (result.error) return { ok: false, detail: `${what}: ${result.error.code === "ETIMEDOUT" ? "timed out" : result.error.message}` };
  if (result.signal) return { ok: false, detail: `${what}: killed by ${result.signal}` };
  if (result.status !== 0) return { ok: false, detail: `${what}: exit ${result.status}: ${String(result.stderr ?? "").trim().split("\n").slice(-2).join(" | ")}` };
  return { ok: true };
}

/**
 * Run every cell: `prepare(cell)` returns the package root (or throws), `inspect(cell, root)` returns
 * the checks map. A thrown error fails that cell only; no cell can mask another.
 */
export async function runMatrix({ cells, prepare, inspect, emit, now = Date.now }) {
  const results = [];
  for (const cell of cells) {
    const started = now();
    let checks;
    try {
      checks = await inspect(cell, await prepare(cell));
    } catch (error) {
      checks = { prepare: { ok: false, detail: String(error.message ?? error) } };
    }
    const result = { version: cell.version, source: cell.source, ok: Object.values(checks).every((check) => check.ok), ms: now() - started, checks };
    emit(result);
    results.push(result);
  }
  return results;
}

// --- I/O ---------------------------------------------------------------------------------------------

function matrixRoot(argv) {
  const at = argv.indexOf("--root");
  return at >= 0 ? path.resolve(argv[at + 1]) : path.join(process.env.TMPDIR ?? "/tmp", "empirica-subagents-matrix");
}

function readJson(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}

const HOST_PROFILES = path.join(repo, "contracts", "empirica", "v2", "host-profiles.json");

function localVersion() {
  const pinned = readJson(path.join(repo, "package.json")).devDependencies?.[PACKAGE];
  const installed = path.join(repo, "node_modules", PACKAGE);
  return pinned !== undefined && EXACT.test(pinned) && existsSync(installed) ? readJson(path.join(installed, "package.json")).version : undefined;
}

/** The per-version child: gather the evidence for one package and print the checks as JSON. */
async function cellMain(argv) {
  const { generateInventory } = await import("./gen_pi_subagents_inventory.mjs");
  const { captureFixture, fixturePath, FIXTURE_DIR } = await import("./capture_pi_preflight_fixture.mjs");
  const { subagentParamProperties } = await import("./gen_pi_subagents_inventory.mjs");
  const { auditLaunchInput } = await import("../plugins/empirica/adapters/pi/src/audit-launch.ts");
  const { AUDIT_LAUNCH_POLICY } = await import("../plugins/empirica/adapters/pi/src/host-profile.ts");
  const { classifySubagentCall, MANAGEMENT_ACTIONS } = await import("../plugins/empirica/adapters/pi/src/translate.ts");
  const { loadInventory } = await import("../plugins/empirica/adapters/pi/src/subagent-inventory.ts");
  const value = (flag) => argv[argv.indexOf(flag) + 1];
  const [version, packageRoot] = [value("--version"), value("--package-root")];
  const generated = await generateInventory(packageRoot);
  if (generated.version !== version) throw new Error(`the package at ${packageRoot} is ${generated.version}, not ${version}`);
  const inventory = loadInventory(version);
  if (inventory === undefined) throw new Error(`no checked-in inventory for ${version}`);
  const { auditRetrievalFrom } = await import("../plugins/empirica/adapters/pi/src/audit-retrieval.ts");
  const liveFixture = await captureFixture(packageRoot);
  const allowlist = liveFixture.cases.canonical_auditor?.response?.contract?.tools?.effectiveAllowlist;
  const request = { agent: "empirica.empirica-auditor", ...auditLaunchInput({ task: "t", model: "audit/reviewer", agentScope: "user", policy: AUDIT_LAUNCH_POLICY }) };
  const checks = evaluateCell({
    version, request, properties: await subagentParamProperties(packageRoot), inventory,
    liveFixture, checkedFixture: readJson(fixturePath(FIXTURE_DIR, version)),
    classify: classifySubagentCall, managementActions: MANAGEMENT_ACTIONS,
    // A preflight that reports no allowlist derives nothing, which never equals the reviewed set.
    retrieval: { observed: Array.isArray(allowlist) ? auditRetrievalFrom(allowlist) : null },
  });
  process.stdout.write(`${JSON.stringify(checks)}\n`);
}

/** Run the two per-version checks in child processes (`run` is `spawnSync`-shaped), each under a timeout. */
export function inspectInChildren(cell, packageRoot, run = spawnSync) {
  const generator = run("node", [path.join(repo, "scripts", "gen_pi_subagents_inventory.mjs"), "--package-root", packageRoot, "--check"],
    { encoding: "utf8", timeout: CELL_TIMEOUT_MS, cwd: repo });
  const checks = { inventory: processVerdict(generator, "gen_pi_subagents_inventory --check") };
  const child = run("node", [SELF, "--cell", "--version", cell.version, "--package-root", packageRoot],
    { encoding: "utf8", timeout: CELL_TIMEOUT_MS, cwd: repo });
  const verdict = processVerdict(child, "cell checks");
  if (!verdict.ok) return { ...checks, cell: verdict };
  return { ...checks, ...JSON.parse(child.stdout) };
}

function prepareCell(cell, root, cache) {
  if (cell.source === "local") return path.join(repo, "node_modules", PACKAGE);
  const dest = path.join(root, cell.version);
  if (path.dirname(dest) !== root) throw new Error(`refusing to clear ${dest}`);
  rmSync(dest, { recursive: true, force: true });
  return installPackage({ name: PACKAGE, version: cell.version, dest: path.join(dest, "install"), cache });
}

async function main(argv) {
  if (argv.includes("--cell")) { await cellMain(argv); return 0; }
  const root = matrixRoot(argv);
  const reviewed = reviewedFrom(readJson(HOST_PROFILES));
  const cache = path.join(root, "npm-cache");
  mkdirSync(cache, { recursive: true });
  if (argv.includes("--update")) {
    const newer = newerThanReviewed(registryVersions({ name: PACKAGE, cache }), reviewed.versions);
    if (newer.length === 0) console.log(`no ${PACKAGE} release is newer than ${reviewed.versions.at(-1)} (the newest reviewed)`);
    for (const version of newer) {
      console.log(`${PACKAGE}@${version} has no compat record; inside the supported range the adapter admits it by observation, outside it refuses it. To review it:`);
      for (const command of reviewCommands(version, root)) console.log(`  ${command}`);
    }
    return 0;
  }
  const results = await runMatrix({
    cells: planCells(reviewed.versions, localVersion()), prepare: (cell) => prepareCell(cell, root, cache),
    inspect: (cell, packageRoot) => inspectInChildren(cell, packageRoot), emit: (result) => console.log(JSON.stringify(result)),
  });
  return results.every((result) => result.ok) ? 0 : 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === SELF)
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; }, (error) => {
    console.error(String(error.message ?? error)); process.exitCode = 2;
  });
