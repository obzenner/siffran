import { test } from "node:test";
import assert from "node:assert/strict";
import { createEmpiricaExtension } from "../src/index.ts";
import { parseModeFlags } from "../src/translate.ts";
import { FakePi, fakeCtx } from "./fakes.ts";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
const contract = { contract_id:"x", revision:1, obligations:[], provenance:[], retired:[], supersedes:[], verdict:{satisfied:[],holds:[],violated:[],residual:[],unwitnessed:[],held:[]} } as any;
const allow = { protocol:"empirica/v1", request_id:"x", result:{type:"Allow", converged:true, run:{id:"h",status:"active",revision:1,contract}} } as any;
const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../..");
const fixtureBlock = JSON.parse(readFileSync(resolve(repo,"contracts/fixtures/empirica-block-audit.json"), "utf8")).expected;
const fixtureRestore = JSON.parse(readFileSync(resolve(repo,"contracts/fixtures/empirica-restore-run.json"), "utf8")).expected;
const block = { protocol:"empirica/v1", request_id:"x", result:{type:"Block", reason:"budget exhausted", run:{id:"h",status:"active",revision:1,contract}} } as any;

function setup(dispatch: any) { const pi = new FakePi(); createEmpiricaExtension({dispatch})(pi); return pi; }
test("parseModeFlags surfaces unknown leading flags", () => assert.deepEqual(parseModeFlags("--cli-exec --wat goal words"), { goal:"goal words", modes:{cli_exec:true}, unknownFlags:["--wat"] }));
test("all three tools register; convergence execute rejects fixture Block", async () => { const pi = setup(async (r:any) => r.command.type === "StartRun" ? fixtureRestore : fixtureBlock); await pi.command("empirica").handler("goal", fakeCtx()); assert.deepEqual([...pi.tools.keys()], ["report_convergence","empirica_status","empirica_knowledge"]); await assert.rejects(() => pi.tools.get("report_convergence")!.execute("x", {}, new AbortController().signal, ()=>{}, fakeCtx()), /independent audit required/); });
test("knowledge rejects unknown action kind before transport", async () => { let calls=0; const pi=setup(async (r:any) => { calls++; return allow; }); await pi.command("empirica").handler("goal", fakeCtx()); await assert.rejects(() => pi.tools.get("empirica_knowledge")!.execute("x", {kind:"audit_verdict"}, new AbortController().signal, ()=>{}, fakeCtx()), /unknown empirica knowledge action/); assert.equal(calls,1); });
test("status explicitly reports no contract when GetRun has no graph", async () => { const pi=setup(async (r:any) => r.command.type === "StartRun" ? allow : { ...allow, result:{...allow.result, run:{id:"h",status:"active",revision:1}}}); await pi.command("empirica").handler("goal", fakeCtx()); const result=await pi.tools.get("empirica_status")!.execute("x",{},new AbortController().signal,()=>{},fakeCtx()); assert.match(result.content[0].text,/no contract yet \(no graph\)/); });

test("appendEntry and session_start reconstruct handle; compaction carries text and JSON details", async () => { const pi=setup(async (_r:any) => allow); await pi.command("empirica").handler("goal", fakeCtx()); const start = pi.handlers.get("session_start") as any; const ctx=fakeCtx("/work", [{customType:"empirica.run",data:{runHandle:"restored"}}]); await start({},ctx); const compact=pi.handlers.get("session_before_compact") as any; const out=await compact({preparation:{firstKeptEntryId:"e",tokensBefore:4}},ctx); assert.match(out.compaction.summary,/Obligation contract/); assert.deepEqual(out.compaction.details.contract,contract); assert.equal(pi.entries.length,1); });
test("subagent is allowed, budget Block denies, and transport failure closes", async () => { let mode="allow"; const pi=setup(async (r:any) => { if (r.command.type === "StartRun") return allow; if (mode === "fail") throw new Error("offline"); return mode === "deny" ? block : allow; }); await pi.command("empirica").handler("goal", fakeCtx()); const gate=pi.toolCall(); assert.equal((await gate({toolName:"subagent",toolCallId:"1",input:{}},fakeCtx())), undefined); mode="deny"; assert.equal((await gate({toolName:"subagent",toolCallId:"2",input:{}},fakeCtx()))!.block,true); mode="fail"; assert.equal((await gate({toolName:"subagent",toolCallId:"3",input:{}},fakeCtx()))!.block,true); });
