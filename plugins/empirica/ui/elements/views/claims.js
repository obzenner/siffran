// A run's claims in the sheet as its support graph (the root, each claim's supporters nested beneath it), each expanding in
// place to its proof (sources and quotes, the human's answer and the decisions on it, spike commands, the audit's coverage),
// and the claims the run no longer stands on. Words only: the
// states, the scope (Required or Deferred) and the assurance are the core's; the blocker's sentence is the registry's.
import catalogue from "../../catalogue.json" with { type: "json" };
import { claimGraph, edgePath, graphBox } from "../../app/geometry.js";
import { shapePath } from "../../app/universe.js";
import { TONE, ago, clip, esc, joined, num, plain, stateOf, t } from "../core/element.js";
import { supportTree } from "../core/tree.js";
import { chip, claimLink, fact, link, list } from "./parts.js";

/** One research record of a claim (ADR-0067), in place: its source and how it stands, and its quote. The word says how the
 * record is verified: checked against the file by the service, confirmed or rejected by the human, or (pending) who it waits
 * on and where. */
function recordHtml(r) {
  const standing = r.status === "pending" && r.wait ? joined(t(`who.${r.wait.who}`), t(`where.${r.wait.where}`)) : "";
  return `<article class="flex flex-col gap-xs border-b border-border py-s last:border-b-0">
    <div class="flex flex-wrap items-center gap-xs">${chip("verification", r.status)}${standing ? `<span class="text-xs text-muted-foreground">${esc(standing)}</span>` : ""}${chip("outcome", r.outcome)}</div>
    <span class="min-w-0 break-all font-mono text-xs text-muted-foreground">${esc(r.source)}</span>
    <blockquote class="border-l-2 border-border pl-s"><ev-more text="${esc(r.quote)}"></ev-more></blockquote>
    <p class="text-xs text-muted-foreground">${t(`record.class.${r.class}`)}</p>
    ${r.spike_unavailable ? `<div class="flex flex-col gap-3xs text-xs"><span class="text-muted-foreground">${t("record.spike")}</span><ev-more text="${esc(r.spike_unavailable)}"></ev-more></div>` : ""}
    ${r.note ? `<div class="flex flex-col gap-3xs text-xs"><span class="text-muted-foreground">${t(r.status === "rejected_by_human" ? "record.note.rejected" : "record.note")}</span><ev-more text="${esc(r.note)}"></ev-more></div>` : ""}
  </article>`;
}

/** One copyable content identity: compact in the disclosure label, exact in its body. */
function identityHtml(label, value) {
  const short = `${value.slice(0, 15)}…${value.slice(-8)}`;
  return `<details class="min-w-0 text-xs"><summary class="cursor-pointer break-all text-muted-foreground">${t(label)} <code class="font-mono">${esc(short)}</code></summary><ev-copy text="${esc(value)}" label="${label}"></ev-copy></details>`;
}

function vectorHtml(label, values) {
  return fact(t(label), `<ol class="flex min-w-0 flex-col gap-3xs">${values.map((value, index) => `<li class="flex min-w-0 gap-xs"><span class="shrink-0 text-muted-foreground">${index}</span><code class="min-w-0 break-all font-mono">${esc(value)}</code></li>`).join("")}</ol>`);
}

/** A profile's words: its own copy key, one per value of the contract's list (enums.json; scripts/audit.mjs checks each). */
function profileLabel(profile) {
  return t(`spike.profile.${profile}`);
}

function backendHtml(backend) {
  return `<div class="flex flex-col gap-3xs" data-spike-authority>
    <span>${joined(t("spike.backend", { backend: backend.backend }), profileLabel(backend.profile_id))}</span>
    ${identityHtml("spike_backend.record", backend.qualification_record_digest)}
    ${identityHtml("spike_backend.execution", backend.execution_binding_digest)}
  </div>`;
}

const RESOURCE_KEYS = {
  cpu_count: "spike.resource.cpu_count", memory_bytes: "spike.resource.memory_bytes",
  memory_swap_bytes: "spike.resource.memory_swap_bytes", pids: "spike.resource.pids",
  open_files: "spike.resource.open_files", max_file_size_bytes: "spike.resource.max_file_size_bytes",
  core_bytes: "spike.resource.core_bytes", workload_timeout_seconds: "spike.resource.workload_timeout_seconds",
  operation_timeout_seconds: "spike.resource.operation_timeout_seconds", stdout_bytes: "spike.resource.stdout_bytes",
  stderr_bytes: "spike.resource.stderr_bytes", staging_files: "spike.resource.staging_files",
  staging_bytes: "spike.resource.staging_bytes", staging_file_bytes: "spike.resource.staging_file_bytes",
  work_tmpfs_bytes: "spike.resource.work_tmpfs_bytes", tmp_tmpfs_bytes: "spike.resource.tmp_tmpfs_bytes",
  shm_bytes: "spike.resource.shm_bytes",
};
const IDENTITY_KEYS = {
  draft_digest: "spike.identity.draft_digest", plan_digest: "spike.identity.plan_digest",
  policy_digest: "spike.identity.policy_digest", snapshot_basis_id: "spike.identity.snapshot_basis_id",
  snapshot_digest: "spike.identity.snapshot_digest", capture_digest: "spike.identity.capture_digest",
  operation_digest: "spike.identity.operation_digest", request_artifact_id: "spike.identity.request_artifact_id",
  qualification_record_digest: "spike.identity.qualification_record_digest",
  execution_binding_digest: "spike.identity.execution_binding_digest", input_digest: "spike.identity.input_digest",
  process_digest: "spike.identity.process_digest", topology_digest: "spike.identity.topology_digest",
  timing_digest: "spike.identity.timing_digest",
};

function resourcesHtml(resources) {
  const fields = Object.keys(RESOURCE_KEYS);
  return `<details class="rounded-m border border-border p-xs" data-spike-limits><summary class="cursor-pointer">${t("spike.resources")}</summary><dl class="ev-facts pt-xs">${fields.map((name) => fact(t(RESOURCE_KEYS[name]), num(resources[name]))).join("")}</dl></details>`;
}

function requestInputsHtml(request) {
  const declarations = request.declared_inputs.length ? list(request.declared_inputs.map((path) => `<code class="break-all font-mono">${esc(path)}</code>`)) : `<span class="text-muted-foreground">${t("spike.none")}</span>`;
  const files = request.inputs.length ? list(request.inputs.map((file) => `<span class="flex min-w-0 flex-col"><code class="break-all font-mono">${esc(file.path)}</code><span>${t("spike.bytes", { count: num(file.bytes) })}</span>${identityHtml("spike.file_digest", file.sha256)}</span>`)) : `<span class="text-muted-foreground">${t("spike.none")}</span>`;
  return `<details class="rounded-m border border-border p-xs" data-spike-input><summary class="cursor-pointer">${t("spike.input_summary", { files: num(request.input_count), bytes: num(request.input_bytes) })}</summary><dl class="ev-facts pt-xs">${fact(t("spike.input.declared"), declarations)}${fact(t("spike.input.bindings"), files)}</dl></details>`;
}

function requestHtml(request) {
  const environment = request.environment_names.length ? list(request.environment_names.map((name) => `<code class="font-mono">${esc(name)}</code>`)) : `<span class="text-muted-foreground">${t("spike.none")}</span>`;
  const image = request.image;
  const ids = [["spike.identity.artifact", request.artifact_id], ["spike.identity.harness_request", request.harness_request_id], ...Object.entries(request.identities).map(([key, value]) => [IDENTITY_KEYS[key], value])];
  return `<section class="flex min-w-0 flex-col gap-xs" data-spike-request aria-label="${t("spike.request")}">
    <p class="text-xs text-muted-foreground">${joined(t("spike.read_only"), t("spike.claim", { claim: request.claim_id }), request.draft_schema, t("spike.profile", { profile: profileLabel(request.profile_id) }), t("spike.timeouts", { workload: request.limits.workload_timeout_seconds, operation: request.limits.operation_timeout_seconds }))}</p>
    <dl class="ev-facts">
      ${fact(t("spike.image.requested"), `<code class="break-all font-mono">${esc(image.requested_reference)}</code>`)}
      ${identityHtml("spike.image.sealed", image.sealed_id)}
      ${fact(t("spike.platform"), `<code class="font-mono">${esc(`${image.raw_os}/${image.raw_architecture}`)}</code> → <code class="font-mono">${esc(`${image.os}/${image.architecture}`)}</code>`)}
      ${vectorHtml("spike.command", request.argv)}
      ${fact(t("spike.environment"), environment)}
      ${fact(t("spike.user"), `<code class="font-mono">${num(request.runtime.uid)}:${num(request.runtime.gid)}</code>`)}
      ${fact(t("spike.workdir"), `<code class="font-mono">${esc(request.runtime.workdir)}</code>`)}
      ${requestInputsHtml(request)}${resourcesHtml(request.limits)}
    </dl>
    <details class="text-xs"><summary class="cursor-pointer text-muted-foreground">${t("spike.identities.request")}</summary><div class="flex flex-col gap-3xs pt-xs">${ids.map(([label, value]) => identityHtml(label, value)).join("")}</div></details>
  </section>`;
}

function streamHtml(name, stream) {
  const label = name === "stdout" ? t("spike.stream.stdout", { bytes: num(stream.bytes_seen) }) : t("spike.stream.stderr", { bytes: num(stream.bytes_seen) });
  return `<span class="flex min-w-0 flex-col"><span>${label}</span>${identityHtml("spike.stream.digest", stream.sha256)}</span>`;
}

function resultHtml(result) {
  if (result === null) return `<p class="text-xs text-muted-foreground" data-spike-no-result>${t("spike.result.none")}</p>`;
  const ids = [["spike.identity.artifact", result.artifact_id], ["spike.identity.harness_request", result.harness_request_id], ...Object.entries(result.identities).map(([key, value]) => [IDENTITY_KEYS[key], value]), ["spike.identity.harness_result", result.harness_result_digest], ["spike.identity.postflight", result.postflight_identity_digest]];
  return `<section class="flex min-w-0 flex-col gap-xs" data-spike-result aria-label="${t("spike.result")}">
    <p class="flex flex-wrap items-center gap-xs"><strong>${esc(result.gate)}</strong><span>${t(`spike.outcome.${result.outcome}`)}</span><span>${t("spike.exit", { code: result.exit_code })}</span><span>${t("spike.exit.natural")}</span><span>${t("spike.cleanup.verified")}</span><span>${t("spike.postflight.verified")}</span></p>
    <p class="text-xs text-muted-foreground">${result.producer_model ? t("spike.producer", { model: result.producer_model }) : t("spike.producer.unknown")}</p>
    ${fact(t("spike.image.realization"), `<ev-copy text="${esc(result.realization_image_digest)}" label="spike.image.realization"></ev-copy>`)}
    <div class="flex flex-col gap-xs">${streamHtml("stdout", result.stdout)}${streamHtml("stderr", result.stderr)}</div>
    <details class="text-xs"><summary class="cursor-pointer text-muted-foreground">${t("spike.identities.result")}</summary><div class="flex flex-col gap-3xs pt-xs">${ids.map(([label, value]) => identityHtml(label, value)).join("")}</div></details>
    <p class="text-xs text-muted-foreground">${t("spike.output.not_retained")}</p>
  </section>`;
}

/** Generic immutable requests and admitted results; disclosures preserve narrow layouts and read-only semantics. */
function spikesHtml(proof) {
  if (!proof.length) return "";
  const one = (item) => `<article class="flex min-w-0 flex-col gap-s" data-spike-proof>
    <p class="text-xs text-muted-foreground">${t(`spike.${item.state}`)}</p>${requestHtml(item.request)}${resultHtml(item.result)}</article>`;
  return fact(t("fact.proof"), list(proof.map(one)));
}

/** A needs-decision claim's decision: where it stands, where it is asked, and the human's answer. */
function decisionHtml(d) {
  return fact(t("fact.decision"), chip("decision", d.status))
    + (d.where ? fact(t("fact.asked"), t(`where.${d.where}`)) : "")
    + (d.answer ? fact(t("fact.answer"), `<ev-more text="${esc(d.answer)}"></ev-more>`) : "");
}

/** The body of an open claim: everything the sheet holds about it, together. */
function proofHtml(c, own, ctx) {
  const { row, href } = ctx;
  if (!own) return `<p class="text-s text-muted-foreground">${t("claim.unavailable")}</p>`;
  const waits = c.blocker_claim && c.blocker_claim !== c.id ? claimLink(row, c.blocker_claim, href) : "";
  return `<div class="flex flex-col gap-s">
    <ev-more text="${esc(c.text)}"></ev-more>
    <dl class="ev-facts flex flex-col gap-xs">
      ${fact(t("fact.kind"), t(`kind.${c.kind}`))}
      ${own.blocker_message ? fact(t("fact.blocker"), esc(own.blocker_message)) : ""}
      ${waits ? fact(t("fact.waits"), waits) : ""}
      ${fact(t("fact.evidence"), chip("evidence", c.evidence))}
      ${fact(t("fact.review"), chip("review", c.review))}
      ${row.audit?.independence ? fact(t("fact.independence"), t(`independence.${row.audit.independence}`)) : ""}
      ${own.decision ? decisionHtml(own.decision) : ""}
      ${own.research > 0 ? fact(t("fact.research.supporting"), String(own.research)) : ""}
      ${spikesHtml(own.proof)}
    </dl>
    ${own.records.length ? `<div class="flex flex-col"><h3 class="text-s font-medium">${t("fact.sources")}</h3>${own.records.map(recordHtml).join("")}</div>` : ""}
  </div>`;
}

/** The decisions a human made on this claim (or on a source it rests on), each its answer and when. */
function claimDecisionsHtml(decisions, id, now) {
  const mine = (decisions ?? []).filter((d) => d.claim === id);
  return mine.length ? `<dl class="ev-facts pt-xs">${fact(t("fact.decisions"), list(mine.map((d) => `<span class="flex flex-wrap items-center gap-xs" data-claim-decision>${t(`ask.${d.asked}`)}${chip("answer", d.answer)}<span class="text-xs text-muted-foreground">${joined(d.source ? esc(d.source) : "", ago(d.at, now))}</span></span>`)))}</dl>` : "";
}

/** One claim as a disclosure, its supporters nested beneath it: the support graph is the list (root first, each claim once,
 * a claim that supports several named again where it recurs). Open: the selected claim (the URL's `claim`). */
function claimHtml(n, parent, ctx, byId) {
  const { detail, claim, href } = ctx;
  const c = byId.get(n.id);
  if (n.again) return `<li class="py-3xs pl-xs text-xs text-muted-foreground" data-again="${esc(n.id)}">${link(href({ claim: n.id }), clip(c.text, 80))} · ${t("support.again")}</li>`;
  const own = detail?.claims.find((x) => x.id === c.id);
  const open = c.id === claim;
  const kids = n.children.length ? `<ul class="ml-s border-l border-border pl-xs">${n.children.map((k) => claimHtml(k, c.id, ctx, byId)).join("")}</ul>` : "";
  return `<li class="border-t border-border first:border-t-0" data-claim-row="${esc(c.id)}" ${parent ? `data-parent="${esc(parent)}"` : ""}>
      <details ${open ? "open" : ""}>
        <summary class="flex cursor-pointer flex-wrap items-center gap-x-xs gap-y-3xs rounded-m px-xs py-2xs text-left hover:bg-muted" data-claim="${esc(c.id)}" data-href="${esc(href({ claim: open ? null : c.id }))}" ${open ? 'aria-current="true"' : ""}>
          <span class="min-w-0 flex-1 text-s font-medium">${esc(clip(c.text, 160))}</span>
          <span class="text-xs text-muted-foreground">${t(c.gating ? "scope.required" : "scope.deferred")}</span>${chip("claim", c.state)}${c.assurance === "not_approved" ? "" : chip("assurance", c.assurance)}
        </summary>
        <div class="px-xs pb-xs">${open ? `${proofHtml(c, own, ctx)}${claimDecisionsHtml(detail?.decisions, c.id, ctx.now)}` : ""}</div>
      </details>${kids}
    </li>`;
}

const G = catalogue.universe;
/** The sheet's graph, drawn small: rings, nodes, the run and the labels (each claim's id), in px. */
const SMALL = { ring: G.sheet_ring, node: G.sheet_node_px, gap: G.claim_gap_px / 2, run: G.sheet_run_px, label_h: G.sheet_label_px, cap: G.loop_cap };

/** The run's claims as the same node-link graph the Universe draws at the run's zoom (app/geometry.js claimGraph, one pure
 * function, two renderers), small, as SVG: the run in the centre in its standing's shape, each claim its state's shape and
 * colour on the ring of its depth labelled by its id, the SupportedBy edges as lines, a needs-decision or needs-experiment
 * claim ringed dashed. Each node is a link to the claim in place. */
export function claimsGraphHtml({ row, claim, href }) {
  const claims = row.claims.slice(0, G.claims_cap);
  const widths = new Map(claims.map((c) => [c.id, c.id.length * G.sheet_char_px + 4]));
  const g = claimGraph(row.root, claims, row.edges, widths, SMALL);
  const v = graphBox(g, SMALL, [], 4), f = (n) => n.toFixed(1);
  const tone = (domain, value) => TONE[stateOf(domain, value).tone]?.fg ?? TONE.neutral.fg;
  const line = (a, b, extra = "") => `<line ${extra} x1="${f(a.x)}" y1="${f(a.y)}" x2="${f(b.x)}" y2="${f(b.y)}" class="stroke-border" stroke-width="1"></line>`;
  const lines = g.edges.map((e) => `<polyline data-edge="${esc(e.from)} ${esc(e.to)}" points="${edgePath(g, e).map((p) => `${f(p.x)},${f(p.y)}`).join(" ")}" fill="none" class="stroke-border" stroke-width="1"></polyline>`).join("") + (g.root ? line({ x: 0, y: 0 }, g.at.get(g.root)) : "");
  const nodes = claims.map((c) => {
    const p = g.at.get(c.id), s = stateOf("claim", c.state), lb = g.labels.get(c.id), sel = c.id === claim, marked = c.kind !== "ordinary";
    return `<a href="${esc(href({ claim: sel ? null : c.id }))}" data-graph-claim="${esc(c.id)}" ${sel ? 'aria-current="true"' : ""}><title>${esc(joined(c.id, clip(c.text, 120), plain(`${s.copy}.label`), marked ? plain(`kind.${c.kind}`) : ""))}</title>
      <path class="${tone("claim", c.state)}" fill="currentColor" fill-rule="evenodd" transform="translate(${f(p.x)} ${f(p.y)})" d="${shapePath(s.shape, SMALL.node)}"></path>
      ${marked ? `<circle class="${tone("claim", c.state)}" cx="${f(p.x)}" cy="${f(p.y)}" r="${SMALL.node + SMALL.gap / 2}" fill="none" stroke="currentColor" stroke-dasharray="${G.shore_dash.join(" ")}"></circle>` : ""}
      ${sel ? `<circle class="stroke-ring" cx="${f(p.x)}" cy="${f(p.y)}" r="${SMALL.node + SMALL.gap}" fill="none" stroke-width="${G.stroke_px}"></circle>` : ""}
      ${lb ? `<text class="fill-muted-foreground text-xs" x="${f(lb.x + 2)}" y="${f(lb.y + lb.h - 2)}" font-size="${SMALL.label_h - 2}">${esc(c.id)}</text>` : ""}</a>`;
  }).join("");
  const run = `<path class="${tone("standing", row.standing)}" fill="currentColor" fill-rule="evenodd" d="${shapePath(stateOf("standing", row.standing).shape, SMALL.run)}"></path>`;
  return `<svg class="mx-auto block max-w-full" width="${f(v.w)}" height="${f(v.h)}" viewBox="${f(v.x)} ${f(v.y)} ${f(v.w)} ${f(v.h)}" role="img" aria-label="${esc(plain("claims.graph"))}" data-claim-graph>${lines}${run}${nodes}</svg>`;
}

/** The claims as the support graph (drawn small, then listed), each a disclosure that opens in place to its proof. */
export function claimsHtml(ctx) {
  const { row } = ctx;
  if (!row.claims.length) return `<p class="text-s text-muted-foreground">${t("claims.none")}</p>`;
  const byId = new Map(row.claims.map((c) => [c.id, c]));
  return `${claimsGraphHtml(ctx)}<ul class="flex flex-col">${supportTree(row.root, row.claims, row.edges ?? []).map((n) => claimHtml(n, null, ctx, byId)).join("")}</ul>`;
}

/** The claims the run no longer stands on, each with the gate's own why (a refuting source, a failed spike, a human's no, or
 * removed in a regraph with nothing against it) and the evidence the gate named. */
export function droppedHtml(dropped, now) {
  if (dropped === null) return `<p class="text-s text-muted-foreground">${t("dropped.unread")}</p>`;
  if (!dropped.length) return `<p class="text-s text-muted-foreground">${t("dropped.none")}</p>`;
  return list(dropped.map((d) => `<span class="flex flex-col gap-3xs" data-dropped="${esc(d.claim)}">
    <span class="flex flex-wrap items-center gap-xs">${chip("why", d.why)}<span class="text-xs text-muted-foreground">${joined(t(`kind.${d.kind}`), d.removed ? t("dropped.regraph") : "", ago(d.at, now))}</span></span>
    <span class="text-s">${esc(clip(d.text, 200))}</span>
    ${d.source ? `<span class="text-xs text-muted-foreground">${t("dropped.source")} <code class="break-all font-mono">${esc(d.source)}</code></span>` : ""}
    ${d.citation ? `<span class="text-xs text-muted-foreground">${esc(d.citation)}</span>` : ""}
    ${d.command ? `<code class="break-all font-mono text-xs">${esc(d.command)}</code>` : ""}
  </span>`));
}
