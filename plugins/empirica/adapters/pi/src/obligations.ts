export type WitnessKind = "test" | "exit_code" | "event" | "artifact" | "predicate" | "judgment";
export type Outcome = "pass" | "fail";
export type Mode = "require" | "forbid";
export type Hold = "blocked" | "deferred";
export interface Witness { kind: WitnessKind; ref: string; expect: Outcome; description: string; }
export interface Obligation { id: string; mode: Mode; must: string; witnesses: Witness[]; because: string[]; hold?: Hold | null; hold_reason?: string | null; severity?: string | null; }
export interface Observation { kind: WitnessKind; ref: string; outcome: Outcome; source: string; at: string; payload?: Record<string, unknown> | null; }
export interface Retirement { obligation: Obligation; reason: string; authority: string; at_revision: number; }
export interface Contract { contract_id: string; revision: number; obligations: Obligation[]; provenance: string[]; parent_revision?: number | null; supersedes: string[]; retired: Retirement[]; }
export interface Verdict { satisfied: string[]; holds: string[]; violated: string[]; residual: string[]; unwitnessed: string[]; held: string[]; }
export interface Preservation { ok: boolean; reasons: string[]; }
export interface ObligationView extends Obligation { status: string; witnesses: Array<Witness & { observed: Outcome | null }>; }
export interface ContractView extends Omit<Contract, "obligations"> { obligations: ObligationView[]; verdict: Verdict; }
const norm = (s: string) => s.replace(/\s+/g, " ").trim();
const semi = (xs: string[]) => xs.length ? xs.join(", ") : "-";
export function renderText(view: ContractView): string {
  const lines = [`Obligation contract ${view.contract_id}@${view.revision}`];
  for (const o of view.obligations) {
    lines.push(`${o.id} [${o.mode}] ${norm(o.must)}`, `  hold: ${o.hold ?? "none"}${o.hold_reason ? ` — ${o.hold_reason}` : ""}`);
    for (const w of o.witnesses) lines.push(`  - ${w.kind}:${w.ref} (${w.expect}) — ${w.description} [observed: ${w.observed ?? "null"}]`);
  }
  for (const r of view.retired) lines.push(`${r.obligation.id} [retired@${r.at_revision}] ${r.reason} — authority: ${r.authority}`);
  const v = view.verdict;
  lines.push(`Verdict: satisfied=${semi(v.satisfied)}; holds=${semi(v.holds)}; violated=${semi(v.violated)}; residual=${semi(v.residual)}; unwitnessed=${semi(v.unwitnessed)}; held=${semi(v.held)}`);
  return lines.join("\n");
}
export function preserved(before: ContractView, after: ContractView): Preservation {
  const reasons: string[] = [];
  const retired = new Map(after.retired.map(r => [r.obligation.id, r]));
  const live = new Map(after.obligations.map(o => [o.id, o]));
  for (const old of before.obligations) {
    const next = live.get(old.id);
    if (!next && !retired.has(old.id)) { reasons.push(`${old.id}: obligation disappeared without retirement`); continue; }
    if (!next) continue;
    if (next.mode !== old.mode) reasons.push(`${old.id}: mode changed`);
    if (norm(next.must) !== norm(old.must)) reasons.push(`${old.id}: must changed`);
    const ws = new Set(next.witnesses.map(w => `${w.kind}\0${w.ref}\0${w.expect}\0${w.description}`));
    for (const w of old.witnesses) if (!ws.has(`${w.kind}\0${w.ref}\0${w.expect}\0${w.description}`)) reasons.push(`${old.id}: witnesses were removed`);
    for (const b of old.because) if (!next.because.includes(b)) reasons.push(`${old.id}: because provenance was removed`);
  }
  return { ok: reasons.length === 0, reasons };
}
export function sameContract(before: ContractView, after: ContractView): Preservation {
  const reasons = [...preserved(before, after).reasons];
  if (before.contract_id !== after.contract_id) reasons.push("contract_id changed");
  if (after.revision < before.revision) reasons.push("revision went backwards");
  const laterRetired = new Map(after.retired.map(r => [r.obligation.id, JSON.stringify(r)]));
  for (const retirement of before.retired) {
    if (laterRetired.get(retirement.obligation.id) !== JSON.stringify(retirement))
      reasons.push(`${retirement.obligation.id}: prior retirement was dropped or changed`);
  }
  const live = new Map(after.obligations.map(o => [o.id, o]));
  const retired = new Set(after.retired.map(r => r.obligation.id));
  for (const old of before.obligations) if (old.hold && !retired.has(old.id)) {
    const next = live.get(old.id);
    if (next && (next.hold !== old.hold || next.hold_reason !== old.hold_reason))
      reasons.push(`${old.id}: hold was silently changed or dropped`);
  }
  return { ok: reasons.length === 0, reasons };
}
