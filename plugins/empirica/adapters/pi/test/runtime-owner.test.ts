// Active pi-subagents owner resolution (Empirica 4.1, D2): the single extension that registered
// `subagent` is identified from Pi's tool inventory, never searched for. The resolver is pure over
// injected I/O; the real-file-system cases use throwaway packages (test/owner-fixture.ts).

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, realpathSync, symlinkSync, writeFileSync } from "node:fs";
import * as path from "node:path";

import type { SlashCommandInfo, ToolInfo } from "../src/pi-types.ts";
import {
  nodeOwnerIo, nodePreflightImporter, resolveOwnerInventory, resolveOwnerPreflight, resolveSubagentOwner, sameOwner,
  subagentsProvenance, type OwnerIo, type OwnerRefusalCode, type PreflightImporter,
} from "../src/runtime-owner.ts";
import { launchUnsupportedReason, OWNER_REFUSAL_REASON, ownerRefusalBlock, ownerRefusalReason } from "../src/owner-refusal.ts";
import { loadInventory } from "../src/subagent-inventory.ts";
import { PUBLIC_TOOLS } from "../src/public-tools.ts";
import { makeSubagentsPackage, tempParent } from "./owner-fixture.ts";

const NO_ENV = {};

/** ``resolveSubagentOwner`` with the slash-command inventory last (default: none). */
const resolve = (tools: readonly ToolInfo[], io: OwnerIo, env: { PI_SUBAGENT_CHILD?: string },
                 commands: readonly SlashCommandInfo[] = []) => resolveSubagentOwner(tools, commands, io, env);

/** The extension slash command a loaded pi-subagents copy registers (`subagents-doctor`). */
const doctorCommand = (tool: ToolInfo, source: SlashCommandInfo["source"] = "extension"): SlashCommandInfo =>
  ({ name: "subagents-doctor", source, sourceInfo: tool.sourceInfo });

function refusal(resolution: ReturnType<typeof resolveSubagentOwner>): OwnerRefusalCode {
  assert.equal(resolution.ok, false, "expected a refusal");
  return (resolution as { code: OwnerRefusalCode }).code;
}

// --- one owner -------------------------------------------------------------------------------

test("exactly one subagent tool resolves to its canonical owner, package root, and exact version", () => {
  const pkg = makeSubagentsPackage(tempParent(), { version: "0.74.0" });
  const resolved = resolve([pkg.tool(), { name: "read", sourceInfo: pkg.tool().sourceInfo }],
    nodeOwnerIo, NO_ENV);
  assert.deepEqual(resolved, {
    ok: true, owner_path: realpathSync(pkg.entry), package_root: realpathSync(pkg.root),
    version: "0.74.0", source: "npm:pi-subagents",
  });
});

test("prerelease and build semver versions are exact-read; non-semver versions are unobservable", () => {
  for (const version of ["1.0.0-rc.1", "0.75.0+build.5"]) {
    const pkg = makeSubagentsPackage(tempParent(), { version });
    const resolved = resolve([pkg.tool()], nodeOwnerIo, NO_ENV);
    assert.ok(resolved.ok && resolved.version === version, version);
  }
  for (const version of ["latest", "1.2", "01.2.3", "v1.2.3", "1.2.3.4", "^0.74.0", ""]) {
    const pkg = makeSubagentsPackage(tempParent(), { version });
    assert.equal(refusal(resolve([pkg.tool()], nodeOwnerIo, NO_ENV)), "version-unobservable", version);
  }
  const absent = makeSubagentsPackage(tempParent(), { version: null });
  assert.equal(refusal(resolve([absent.tool()], nodeOwnerIo, NO_ENV)), "version-unobservable");
});

test("a non-string version is unobservable, not coerced", () => {
  const io: OwnerIo = { realpath: (p) => p, readPackageJson: () => ({ name: "pi-subagents", version: 74 }) };
  assert.equal(refusal(resolve(
    [{ name: "subagent", sourceInfo: { path: "/x/pi-subagents/index.ts", source: "s", scope: "user", origin: "package" } }],
    io, NO_ENV)), "version-unobservable");
});

// --- zero / several owners -------------------------------------------------------------------

test("no subagent tool, or only other tools, is missing-tool", () => {
  const pkg = makeSubagentsPackage(tempParent());
  assert.equal(refusal(resolve([], nodeOwnerIo, NO_ENV)), "missing-tool");
  const other: ToolInfo = { name: "read", sourceInfo: pkg.tool().sourceInfo };
  assert.equal(refusal(resolve([other], nodeOwnerIo, NO_ENV)), "missing-tool");
});

test("two subagent tool registrations are refused whether distinct packages or the same package and version", () => {
  // Pi itself never reports two (getAllTools is first-wins); the check is defensive for other hosts.
  const a = makeSubagentsPackage(tempParent(), { version: "0.74.0" });
  const b = makeSubagentsPackage(tempParent(), { version: "0.64.0" });
  assert.equal(refusal(resolve([a.tool(), b.tool()], nodeOwnerIo, NO_ENV)), "multiple-owners");
  const twin = makeSubagentsPackage(tempParent(), { version: "0.74.0" });
  assert.equal(refusal(resolve([a.tool(), twin.tool()], nodeOwnerIo, NO_ENV)), "multiple-owners");
  assert.equal(refusal(resolve([a.tool(), a.tool()], nodeOwnerIo, NO_ENV)), "multiple-owners");
});

test("a second loaded pi-subagents copy is seen through its commands although one subagent tool is reported", () => {
  const a = makeSubagentsPackage(tempParent(), { version: "0.74.0" });
  const b = makeSubagentsPackage(tempParent(), { version: "0.64.0" });
  const twin = makeSubagentsPackage(tempParent(), { version: "0.74.0" });
  for (const second of [b, twin]) {
    const refused = resolve([a.tool()], nodeOwnerIo, NO_ENV, [doctorCommand(a.tool()), doctorCommand(second.tool())]);
    assert.equal(refusal(refused), "multiple-owners");
    const message = (refused as { message: string }).message;
    assert.ok(message.includes(realpathSync(a.root)) && message.includes(realpathSync(second.root)), message);
  }
  // The tool owner's own root counts even when only the other copy contributed commands.
  assert.equal(refusal(resolve([a.tool()], nodeOwnerIo, NO_ENV, [doctorCommand(b.tool())])), "multiple-owners");
});

test("commands from the owner's own package, however many or reached by symlink, are not a second owner", () => {
  const a = makeSubagentsPackage(tempParent(), { version: "0.74.0" });
  const linkParent = tempParent();
  symlinkSync(a.root, path.join(linkParent, "pi-subagents"), "dir");
  const viaLink = a.tool({ path: path.join(linkParent, "pi-subagents", "index.ts") });
  const resolved = resolve([a.tool()], nodeOwnerIo, NO_ENV,
    [doctorCommand(a.tool()), doctorCommand(a.tool()), doctorCommand(viaLink), { ...doctorCommand(a.tool()), name: "run" }]);
  assert.ok(resolved.ok, JSON.stringify(resolved));
  assert.equal(resolved.package_root, realpathSync(a.root));
});

test("only extension commands demonstrably inside a pi-subagents package count toward the owner census", () => {
  const a = makeSubagentsPackage(tempParent(), { version: "0.74.0" });
  const b = makeSubagentsPackage(tempParent(), { version: "0.64.0" });
  const unrelated = makeSubagentsPackage(tempParent(), { name: "some-other-extension" });
  const broken = makeSubagentsPackage(tempParent());
  writeFileSync(path.join(broken.root, "package.json"), "{ not json");
  const noise: SlashCommandInfo[] = [
    doctorCommand(b.tool(), "prompt"), doctorCommand(b.tool(), "skill"),
    doctorCommand(unrelated.tool()),
    doctorCommand(a.tool({ path: "<inline:1>" })), doctorCommand(a.tool({ path: path.join(a.root, "gone.ts") })),
    doctorCommand(broken.tool()),
    { name: "x", source: "extension", sourceInfo: undefined as never },
    { name: "y", source: "extension", sourceInfo: { path: 7 } as never },
  ];
  const resolved = resolve([a.tool()], nodeOwnerIo, NO_ENV, [doctorCommand(a.tool()), ...noise]);
  assert.ok(resolved.ok, JSON.stringify(resolved));
});

test("a malformed tool or command inventory is owner-unobservable, never a crash or a pass", () => {
  const a = makeSubagentsPackage(tempParent());
  const cases: Array<[string, unknown, unknown]> = [
    ["tools not an array", undefined, []], ["tools a string", "subagent", []],
    ["null tool", [null, a.tool()], []], ["primitive tool", [7], []],
    ["commands not an array", [a.tool()], undefined], ["commands an object", [a.tool()], {}],
    ["null command", [a.tool()], [null]], ["string command", [a.tool()], ["subagents-doctor"]],
  ];
  for (const [name, tools, commands] of cases)
    assert.equal(refusal(resolveSubagentOwner(tools as never, commands as never, nodeOwnerIo, NO_ENV)),
      "owner-unobservable", name);
  // The child-process refusal still wins over a malformed inventory.
  assert.equal(refusal(resolveSubagentOwner(null as never, null as never, nodeOwnerIo, { PI_SUBAGENT_CHILD: "1" })),
    "child-process");
});

// --- canonicalisation ------------------------------------------------------------------------

test("a symlinked registration path is canonicalised to the real package", () => {
  const real = makeSubagentsPackage(tempParent());
  const linkParent = tempParent();
  symlinkSync(real.root, path.join(linkParent, "pi-subagents"), "dir");
  const viaLink = path.join(linkParent, "pi-subagents", "index.ts");
  const resolved = resolve([real.tool({ path: viaLink })], nodeOwnerIo, NO_ENV);
  assert.ok(resolved.ok);
  assert.equal(resolved.owner_path, realpathSync(real.entry));
  assert.equal(resolved.package_root, realpathSync(real.root));
});

test("sourceInfo.baseDir is a hint that never widens or redirects the owner", () => {
  const owner = makeSubagentsPackage(tempParent(), { version: "0.74.0" });
  const decoy = makeSubagentsPackage(tempParent(), { version: "9.9.9" });
  const resolved = resolve([owner.tool({ baseDir: decoy.root })], nodeOwnerIo, NO_ENV);
  assert.ok(resolved.ok);
  assert.equal(resolved.version, "0.74.0");
  assert.equal(resolved.package_root, realpathSync(owner.root));
});

// --- unobservable / wrong package ------------------------------------------------------------

test("a registration without an absolute source path or source identity is owner-unobservable", () => {
  const pkg = makeSubagentsPackage(tempParent());
  const cases: ToolInfo[] = [
    pkg.tool({ path: "" }), pkg.tool({ path: "index.ts" }), pkg.tool({ path: undefined as never }),
    pkg.tool({ path: 7 as never }), pkg.tool({ source: "" }), pkg.tool({ source: undefined as never }),
    { name: "subagent", sourceInfo: undefined as never },
    { name: "subagent", sourceInfo: null as never },
  ];
  for (const tool of cases)
    assert.equal(refusal(resolve([tool], nodeOwnerIo, NO_ENV)), "owner-unobservable", JSON.stringify(tool));
});

test("a source path that does not exist cannot be canonicalised", () => {
  const pkg = makeSubagentsPackage(tempParent());
  const missing = path.join(pkg.root, "gone.ts");
  assert.equal(refusal(resolve([pkg.tool({ path: missing })], nodeOwnerIo, NO_ENV)), "owner-unobservable");
});

test("an unreadable or invalid package.json is owner-unobservable; a missing one keeps climbing", () => {
  const broken = makeSubagentsPackage(tempParent());
  writeFileSync(path.join(broken.root, "package.json"), "{ not json");
  assert.equal(refusal(resolve([broken.tool()], nodeOwnerIo, NO_ENV)), "owner-unobservable");

  // No package.json anywhere above the file (injected): the walk reaches the root and refuses.
  const io: OwnerIo = { realpath: (p) => p, readPackageJson: () => undefined };
  const tool: ToolInfo = { name: "subagent",
    sourceInfo: { path: "/a/b/c/index.ts", source: "s", scope: "user", origin: "package" } };
  assert.equal(refusal(resolve([tool], io, NO_ENV)), "owner-unobservable");

  // The manifest two levels up is the owner: the walk climbs past directories without a package.json.
  const seen: string[] = [];
  const climbing: OwnerIo = { realpath: (p) => p, readPackageJson: (dir) => {
    seen.push(dir);
    return dir === "/a" ? { name: "pi-subagents", version: "1.0.0" } : undefined;
  } };
  const found = resolve([tool], climbing, NO_ENV);
  assert.ok(found.ok);
  assert.equal(found.package_root, "/a");
  assert.deepEqual(seen, ["/a/b/c", "/a/b", "/a"]);
});

test("the owning package must be named pi-subagents, and the nearest package.json decides", () => {
  const wrong = makeSubagentsPackage(tempParent(), { name: "some-other-package" });
  assert.equal(refusal(resolve([wrong.tool()], nodeOwnerIo, NO_ENV)), "package-not-pi-subagents");

  // A nested package of another name inside a pi-subagents tree is a different owner.
  const outer = makeSubagentsPackage(tempParent());
  const nested = path.join(outer.root, "vendor", "inner");
  mkdirSync(nested, { recursive: true });
  writeFileSync(path.join(nested, "package.json"), JSON.stringify({ name: "inner", version: "1.0.0" }));
  writeFileSync(path.join(nested, "ext.ts"), "export default function () {}\n");
  assert.equal(refusal(resolve([outer.tool({ path: path.join(nested, "ext.ts") })], nodeOwnerIo, NO_ENV)),
    "package-not-pi-subagents");

  const nameless: OwnerIo = { realpath: (p) => p, readPackageJson: () => ({ version: "1.0.0" }) };
  assert.equal(refusal(resolve([outer.tool()], nameless, NO_ENV)), "package-not-pi-subagents");
  const notObject: OwnerIo = { realpath: (p) => p, readPackageJson: () => "pi-subagents" };
  assert.equal(refusal(resolve([outer.tool()], notObject, NO_ENV)), "package-not-pi-subagents");
});

// --- child process ---------------------------------------------------------------------------

test("PI_SUBAGENT_CHILD=1 refuses even with a perfectly valid owner; other values do not", () => {
  const pkg = makeSubagentsPackage(tempParent());
  assert.equal(refusal(resolve([pkg.tool()], nodeOwnerIo, { PI_SUBAGENT_CHILD: "1" })), "child-process");
  for (const env of [{}, { PI_SUBAGENT_CHILD: "0" }, { PI_SUBAGENT_CHILD: "" }, { PI_SUBAGENT_CHILD: undefined }])
    assert.ok(resolve([pkg.tool()], nodeOwnerIo, env).ok, JSON.stringify(env));
  // The child flag wins over a missing tool: a child is never a parent owner.
  assert.equal(refusal(resolve([], nodeOwnerIo, { PI_SUBAGENT_CHILD: "1" })), "child-process");
});

// --- identity --------------------------------------------------------------------------------

test("sameOwner compares every identity field", () => {
  const base = { owner_path: "/a/index.ts", package_root: "/a", version: "1.0.0", source: "npm:pi-subagents" };
  assert.equal(sameOwner(base, { ...base }), true);
  for (const field of Object.keys(base) as Array<keyof typeof base>)
    assert.equal(sameOwner(base, { ...base, [field]: `${base[field]}x` }), false, field);
});

// --- owner-anchored preflight ----------------------------------------------------------------

test("the preflight is imported from the registered owner's package, not from repository resolution", async () => {
  const a = makeSubagentsPackage(tempParent(), { sentinel: "owner-A" });
  const b = makeSubagentsPackage(tempParent(), { sentinel: "owner-B" });
  for (const [pkg, sentinel] of [[a, "owner-A"], [b, "owner-B"]] as const) {
    const owner = resolve([pkg.tool()], nodeOwnerIo, NO_ENV);
    assert.ok(owner.ok);
    const bound = await resolveOwnerPreflight(owner, nodePreflightImporter, nodeOwnerIo);
    assert.ok(bound.ok, JSON.stringify(bound));
    assert.equal(bound.preflight_path, realpathSync(path.join(pkg.root, "src", "api", "preflight.ts")));
    assert.deepEqual(await bound.api.resolveSubagentLaunchContract({}), { ok: false, message: `preflight:${sentinel}` });
  }
});

test("the bare specifier is resolved from the owner, never from the adapter's own location", async () => {
  const owner = resolve([makeSubagentsPackage(tempParent(), { sentinel: "S" }).tool()], nodeOwnerIo, NO_ENV);
  assert.ok(owner.ok);
  const asked: Array<[string, string]> = [];
  const importer: PreflightImporter = {
    resolve: (specifier, from) => { asked.push([specifier, from]); return nodePreflightImporter.resolve(specifier, from); },
    load: nodePreflightImporter.load,
  };
  assert.ok((await resolveOwnerPreflight(owner, importer, nodeOwnerIo)).ok);
  assert.deepEqual(asked, [["pi-subagents/preflight", owner.owner_path]]);
});

test("an owner whose preflight is absent, unresolvable, broken, or lacks the export is preflight-unavailable", async () => {
  const cases: Array<[string, { preflight?: string | null }]> = [
    ["no module", { preflight: null }],
    ["syntax error", { preflight: "export const = ;" }],
    ["no export", { preflight: "export const other = 1;\n" }],
    ["not callable", { preflight: "export const resolveSubagentLaunchContract = 7;\n" }],
  ];
  for (const [name, options] of cases) {
    const owner = resolve([makeSubagentsPackage(tempParent(), options).tool()], nodeOwnerIo, NO_ENV);
    assert.ok(owner.ok, name);
    const bound = await resolveOwnerPreflight(owner, nodePreflightImporter, nodeOwnerIo);
    assert.equal(bound.ok, false, name);
    assert.equal((bound as { code: string }).code, "preflight-unavailable", name);
  }
});

test("a preflight that resolves outside the owner's package is refused", async () => {
  const owner = resolve([makeSubagentsPackage(tempParent()).tool()], nodeOwnerIo, NO_ENV);
  assert.ok(owner.ok);
  const outside = makeSubagentsPackage(tempParent(), { sentinel: "elsewhere" });
  const importer: PreflightImporter = {
    resolve: () => path.join(outside.root, "src", "api", "preflight.ts"), load: nodePreflightImporter.load };
  const bound = await resolveOwnerPreflight(owner, importer, nodeOwnerIo);
  assert.equal(bound.ok, false);
  assert.match((bound as { message: string }).message, /outside the owner package/);
});

test("a preflight resolved into a nested pi-subagents copy inside the owner's tree is refused", async () => {
  const pkg = makeSubagentsPackage(tempParent(), { sentinel: "owner" });
  const owner = resolve([pkg.tool()], nodeOwnerIo, NO_ENV);
  assert.ok(owner.ok);
  // <owner>/node_modules/pi-subagents/src/api/preflight.ts: inside package_root, but another package.
  const nested = makeSubagentsPackage(path.join(pkg.root, "node_modules"), { sentinel: "nested" });
  const loaded: string[] = [];
  const importer: PreflightImporter = {
    resolve: () => path.join(nested.root, "src", "api", "preflight.ts"),
    load: (file) => { loaded.push(file); return nodePreflightImporter.load(file); },
  };
  const bound = await resolveOwnerPreflight(owner, importer, nodeOwnerIo);
  assert.equal(bound.ok, false);
  assert.equal((bound as { code: string }).code, "preflight-unavailable");
  assert.match((bound as { message: string }).message, /not the owner package/);
  assert.deepEqual(loaded, [], "a nested copy must never be loaded");
  // The owner's own preflight is still accepted.
  const own: PreflightImporter = { ...importer, resolve: () => path.join(pkg.root, "src", "api", "preflight.ts") };
  assert.ok((await resolveOwnerPreflight(owner, own, nodeOwnerIo)).ok);
});

test("an unreadable package.json above the resolved preflight is preflight-unavailable", async () => {
  const pkg = makeSubagentsPackage(tempParent());
  const owner = resolve([pkg.tool()], nodeOwnerIo, NO_ENV);
  assert.ok(owner.ok);
  const io: Pick<OwnerIo, "realpath" | "readPackageJson"> = { realpath: nodeOwnerIo.realpath,
    readPackageJson: () => { throw new Error("EACCES"); } };
  const importer: PreflightImporter = { resolve: nodePreflightImporter.resolve, load: nodePreflightImporter.load };
  const bound = await resolveOwnerPreflight(owner, importer, io);
  assert.equal((bound as { code?: string }).code, "preflight-unavailable");
});

test("provenance records exactly what was observed, with the loaded preflight path", async () => {
  const pkg = makeSubagentsPackage(tempParent(), { version: "0.74.0" });
  const owner = resolve([pkg.tool()], nodeOwnerIo, NO_ENV);
  assert.ok(owner.ok);
  const bound = await resolveOwnerPreflight(owner, nodePreflightImporter, nodeOwnerIo);
  assert.ok(bound.ok);
  assert.deepEqual(subagentsProvenance(owner, bound), {
    package: "pi-subagents", version: "0.74.0", owner_path: realpathSync(pkg.entry),
    package_root: realpathSync(pkg.root), preflight_path: bound.preflight_path, source: "npm:pi-subagents",
  });
});

// --- refusal → contract reason ---------------------------------------------------------------

test("every refusal code maps to a host.subagents_* reason the contract carries, with its own message", () => {
  const codes = Object.keys(OWNER_REFUSAL_REASON) as OwnerRefusalCode[];
  assert.deepEqual([...codes].sort(), ["child-process", "missing-tool", "multiple-owners", "owner-changed",
    "owner-unobservable", "package-not-pi-subagents", "preflight-unavailable", "tool-inactive", "version-unobservable",
    "version-unreviewed"]);
  for (const code of codes) {
    const reason = ownerRefusalReason(code);
    assert.match(reason.code, /^host\.subagents_/, code);
    assert.equal(reason.message, PUBLIC_TOOLS.recovery[reason.code].message, code);
    assert.ok(reason.message.length > 0, code);
  }
  assert.equal(ownerRefusalReason("missing-tool").code, "host.subagents_missing");
  assert.equal(ownerRefusalReason("multiple-owners").code, "host.subagents_duplicate_owner");
  assert.equal(ownerRefusalReason("child-process").code, "host.subagents_owner_unverified");
  assert.equal(ownerRefusalReason("preflight-unavailable").code, "host.subagents_version_unsupported");
  assert.equal(ownerRefusalReason("version-unreviewed").code, "host.subagents_version_unsupported");
  assert.equal(ownerRefusalReason("tool-inactive").code, "host.subagents_tool_inactive");
});

test("a registered-but-inactive tool has its own guidance, distinct from a missing install", () => {
  const inactive = String(ownerRefusalReason("tool-inactive").message);
  assert.notEqual(inactive, String(ownerRefusalReason("missing-tool").message));
  assert.match(inactive, /Call `subagents_enable` and retry, or set pi-subagents `toolActivation: eager` and reload the host/);
  assert.match(inactive, /a restart alone does not change this/);
  assert.doesNotMatch(inactive, /install/i);
});

test("the launch-unsupported reason is read from the contract projection", () => {
  const reason = launchUnsupportedReason();
  assert.equal(reason.code, "host.subagents_launch_unsupported");
  assert.equal(reason.message, PUBLIC_TOOLS.recovery["host.subagents_launch_unsupported"].message);
});

test("a refusal block carries the reason, and the run only when one exists", () => {
  const run = { id: "r", status: "active" as never };
  assert.deepEqual(ownerRefusalBlock("missing-tool").reasons.map((r) => r.code), ["host.subagents_missing"]);
  assert.equal("run" in ownerRefusalBlock("missing-tool"), false);
  assert.equal(ownerRefusalBlock("missing-tool", run).run, run);
});

test("the contract defines every host.subagents_* reason once, each offering residual.accept", () => {
  const codes = Object.keys(PUBLIC_TOOLS.recovery).filter((code) => code.startsWith("host.subagents_")).sort();
  assert.deepEqual(codes, ["host.subagents_duplicate_owner", "host.subagents_launch_unsupported",
    "host.subagents_missing", "host.subagents_owner_unverified", "host.subagents_provenance_missing",
    "host.subagents_tool_inactive", "host.subagents_version_unsupported"]);
  for (const code of codes) assert.deepEqual(PUBLIC_TOOLS.recovery[code].next_actions, ["residual.accept"], code);
});

// --- reviewed inventory binding ---------------------------------------------------------------

const ownerAt = (version: string) => ({ ok: true as const, tool: "subagent", package_root: "/p", package_name: "pi-subagents",
  version, source: "x" } as never);

test("resolveOwnerInventory selects the inventory of the exact version", () => {
  for (const version of ["0.50.0", "0.64.0", "0.74.0", "0.75.0"]) {
    const bound = resolveOwnerInventory(ownerAt(version), (v) => loadInventory(v));
    assert.equal(bound.ok, true, version);
    if (bound.ok) assert.equal(bound.inventory.version, version);
  }
});

test("resolveOwnerInventory refuses an unreviewed version, a wrong inventory, and a version that cannot carry the bound", () => {
  const refused = (owner: never, lookup: Parameters<typeof resolveOwnerInventory>[1]) => {
    const result = resolveOwnerInventory(owner, lookup);
    return result.ok ? "ok" : result.code;
  };
  assert.equal(refused(ownerAt("9.9.9"), (v) => loadInventory(v)), "version-unreviewed");
  assert.equal(refused(ownerAt("0.75.0-beta.1"), (v) => loadInventory(v)), "version-unreviewed", "non-exact versions are not approximated");
  assert.equal(refused(ownerAt("0.75.0"), () => loadInventory("0.74.0")), "version-unreviewed");
  const noTimeout = { ...loadInventory("0.75.0")!, supports: { ...loadInventory("0.75.0")!.supports, timeoutMs: false } };
  assert.equal(refused(ownerAt("0.75.0"), () => noTimeout), "version-unreviewed");
});

// Pi 1.0 reports built-in tools and extensions with a `builtin:<name>` path (source-info.d.ts
// BUILTIN_PATH_PREFIX); it names no file, so it can never identify a pi-subagents package.
test("a Pi 1.0 built-in `subagent` tool (builtin:<name> path) is not a package owner", () => {
  const result = resolve([{ name: "subagent", sourceInfo: { path: "builtin:subagent", source: "builtin", scope: "user",
    origin: "top-level" } }], nodeOwnerIo, NO_ENV);
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.code, "owner-unobservable");
    assert.match(result.message, /no absolute source path/);
  }
});
