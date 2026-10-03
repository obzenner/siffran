// A decoy operator home for hermeticity tests. The agents in it are invalid on purpose (pi-subagents
// 0.75 refuses a user agent that still carries the removed `fallbackModels` field), so any code path
// that lets the package read `~/.agents` turns an otherwise passing preflight into a refusal.
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const DECOY_AGENT = (name) => `---\nname: ${name}\ndescription: decoy from a home directory that must never be read\nfallbackModels: decoy/model\n---\ndecoy\n`;

/** Create the decoy home and point HOME/USERPROFILE at it; `t.after` restores both. Returns its path. */
export function useDecoyHome(t, names = ["scout", "empirica-auditor", "empirica.empirica-auditor"]) {
  const home = mkdtempSync(path.join(os.tmpdir(), "decoy-home-"));
  mkdirSync(path.join(home, ".agents"));
  for (const name of names) writeFileSync(path.join(home, ".agents", `${name}.md`), DECOY_AGENT(name));
  const previous = { HOME: process.env.HOME, USERPROFILE: process.env.USERPROFILE };
  process.env.HOME = process.env.USERPROFILE = home;
  t.after(() => {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  });
  return home;
}
