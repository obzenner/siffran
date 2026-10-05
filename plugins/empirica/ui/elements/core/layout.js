// Layered (Sugiyama-style) DAG layout: longest-path layering, dummy nodes for long edges,
// barycentre crossing reduction, separation-respecting coordinate relaxation. Pure, no DOM.
// layout(nodes:[{id}], edges:[{from,to}], {w,h,gapX,gapY,dummyW,sweeps,root}) -> {nodes,edges,width,height,crossings,reversed}
export function layout(nodeList, edgeList, o = {}) {
  const W = o.w ?? 224, H = o.h ?? 96, GX = o.gapX ?? 24, GY = o.gapY ?? 56, DW = o.dummyW ?? 16;
  const sweeps = o.sweeps ?? 12;
  const ids = nodeList.map((n) => n.id), known = new Set(ids);
  const seen = new Set();
  let edges = [];
  for (const e of edgeList) {
    const k = e.from + "\u0000" + e.to;
    if (e.from === e.to || !known.has(e.from) || !known.has(e.to) || seen.has(k)) continue;
    seen.add(k); edges.push({ from: e.from, to: e.to });
  }
  // 1. break cycles: DFS from root (then the rest, in input order); back edges are reversed for layering only
  const out = new Map(ids.map((i) => [i, []]));
  edges.forEach((e, i) => out.get(e.from).push(i));
  const state = new Map(), back = new Set();
  const dfs = (v) => {
    state.set(v, 1);
    for (const i of out.get(v)) {
      const t = edges[i].to, s = state.get(t);
      if (s === 1) back.add(i); else if (!s) dfs(t);
    }
    state.set(v, 2);
  };
  const order0 = o.root && known.has(o.root) ? [o.root, ...ids.filter((i) => i !== o.root)] : ids;
  for (const v of order0) if (!state.get(v)) dfs(v);
  const dag = edges.map((e, i) => (back.has(i) ? { from: e.to, to: e.from, orig: i } : { ...e, orig: i }));
  // 2. longest-path layering
  const indeg = new Map(ids.map((i) => [i, 0])), succ = new Map(ids.map((i) => [i, []]));
  for (const e of dag) { indeg.set(e.to, indeg.get(e.to) + 1); succ.get(e.from).push(e.to); }
  const layer = new Map(ids.map((i) => [i, 0])), q = ids.filter((i) => !indeg.get(i));
  for (let k = 0; k < q.length; k++) {
    const v = q[k];
    for (const t of succ.get(v)) {
      layer.set(t, Math.max(layer.get(t), layer.get(v) + 1));
      indeg.set(t, indeg.get(t) - 1);
      if (!indeg.get(t)) q.push(t);
    }
  }
  // 3. dummy nodes so every edge spans exactly one layer
  const kind = new Map(ids.map((i) => [i, "real"])), chains = [];
  const segs = []; // [upper, lower]
  dag.forEach((e) => {
    const chain = [e.from];
    for (let l = layer.get(e.from) + 1; l < layer.get(e.to); l++) {
      const d = `~d${e.orig}_${l}`; layer.set(d, l); kind.set(d, "dummy"); chain.push(d);
    }
    chain.push(e.to);
    for (let i = 0; i + 1 < chain.length; i++) segs.push([chain[i], chain[i + 1]]);
    chains.push({ orig: e.orig, chain });
  });
  const maxL = Math.max(0, ...layer.values());
  const layers = Array.from({ length: maxL + 1 }, () => []);
  const insertion = [...ids, ...[...layer.keys()].filter((k) => kind.get(k) === "dummy")];
  for (const v of insertion) layers[layer.get(v)].push(v);
  const up = new Map(), down = new Map();
  for (const v of layer.keys()) { up.set(v, []); down.set(v, []); }
  for (const [a, b] of segs) { down.get(a).push(b); up.get(b).push(a); }
  // 4. crossing reduction
  const pos = new Map();
  const index = () => layers.forEach((L) => L.forEach((v, i) => pos.set(v, i)));
  const crossings = () => {
    index(); let c = 0;
    for (let l = 0; l < maxL; l++) {
      const es = [];
      for (const a of layers[l]) for (const b of down.get(a)) es.push([pos.get(a), pos.get(b)]);
      for (let i = 0; i < es.length; i++) for (let j = i + 1; j < es.length; j++)
        if ((es[i][0] - es[j][0]) * (es[i][1] - es[j][1]) < 0) c++;
    }
    return c;
  };
  const naive = crossings();
  let best = layers.map((L) => L.slice()), bestC = naive;
  const bary = (L, nb) => {
    const key = new Map(L.map((v, i) => {
      const ns = nb.get(v); return [v, ns.length ? ns.reduce((s, n) => s + pos.get(n), 0) / ns.length : i];
    }));
    const idx = new Map(L.map((v, i) => [v, i]));
    L.sort((a, b) => key.get(a) - key.get(b) || idx.get(a) - idx.get(b));
  };
  for (let s = 0; s < sweeps && bestC > 0; s++) {
    index();
    if (s % 2 === 0) for (let l = 1; l <= maxL; l++) { bary(layers[l], up); layers[l].forEach((v, i) => pos.set(v, i)); }
    else for (let l = maxL - 1; l >= 0; l--) { bary(layers[l], down); layers[l].forEach((v, i) => pos.set(v, i)); }
    const c = crossings();
    if (c < bestC) { bestC = c; best = layers.map((L) => L.slice()); }
  }
  best.forEach((L, i) => (layers[i] = L));
  index();
  // 5. coordinates: start packed/centred, then relax towards neighbour means without violating separation
  const width = (v) => (kind.get(v) === "dummy" ? DW : W), sep = (a, b) => (width(a) + width(b)) / 2 + GX;
  const x = new Map();
  layers.forEach((L) => {
    let cur = 0; L.forEach((v, i) => { if (i) cur += sep(L[i - 1], v); x.set(v, cur); });
    const mid = cur / 2; L.forEach((v) => x.set(v, x.get(v) - mid));
  });
  const settle = (L, want) => {
    const f = [], b = [];
    L.forEach((v, i) => f[i] = Math.max(want.get(v), i ? f[i - 1] + sep(L[i - 1], v) : -Infinity));
    for (let i = L.length - 1; i >= 0; i--) b[i] = Math.min(want.get(L[i]), i < L.length - 1 ? b[i + 1] - sep(L[i], L[i + 1]) : Infinity);
    L.forEach((v, i) => x.set(v, (f[i] + b[i]) / 2));
  };
  const mean = (v, nb) => { const ns = nb.get(v); return ns.length ? ns.reduce((s, n) => s + x.get(n), 0) / ns.length : x.get(v); };
  for (let it = 0; it < 8; it++) {
    for (let l = 1; l <= maxL; l++) settle(layers[l], new Map(layers[l].map((v) => [v, mean(v, up)])));
    for (let l = maxL - 1; l >= 0; l--) settle(layers[l], new Map(layers[l].map((v) => [v, (mean(v, down) + x.get(v)) / 2])));
  }
  let minX = Infinity, maxX = -Infinity;
  for (const v of ids) { minX = Math.min(minX, x.get(v) - W / 2); maxX = Math.max(maxX, x.get(v) + W / 2); }
  if (!ids.length) { minX = 0; maxX = 0; }
  const PAD = 16, dx = PAD - minX;
  const nodes = {};
  for (const v of ids) nodes[v] = { x: x.get(v) + dx - W / 2, y: PAD + layer.get(v) * (H + GY), layer: layer.get(v), order: pos.get(v), w: W, h: H };
  const cx = (v) => x.get(v) + dx, ty = (v) => PAD + layer.get(v) * (H + GY);
  const outEdges = chains.map(({ orig, chain }) => {
    const e = edges[orig], pts = chain.map((v, i) => {
      if (i === 0) return { x: cx(v), y: ty(v) + H };
      if (i === chain.length - 1) return { x: cx(v), y: ty(v) };
      return { x: cx(v), y: ty(v) + H / 2 };
    });
    // reversed (cycle-closing) edges were laid out child→parent; flip so the path still runs from→to
    return { from: e.from, to: e.to, reversed: back.has(orig), points: back.has(orig) ? pts.reverse() : pts };
  });
  return { nodes, edges: outEdges, width: maxX - minX + 2 * PAD, height: PAD * 2 + (maxL + 1) * H + maxL * GY,
    crossings: bestC, naiveCrossings: naive, reversed: [...back].map((i) => edges[i]), layers: layers.map((L) => L.filter((v) => kind.get(v) === "real")) };
}

export function overlaps(res, gap = 0) {
  const ns = Object.entries(res.nodes), bad = [];
  for (let i = 0; i < ns.length; i++) for (let j = i + 1; j < ns.length; j++) {
    const [a, A] = ns[i], [b, B] = ns[j];
    if (A.x < B.x + B.w + gap && B.x < A.x + A.w + gap && A.y < B.y + B.h + gap && B.y < A.y + A.h + gap) bad.push([a, b]);
  }
  return bad;
}

// SVG path through edge points (vertical-tangent cubic segments)
export function edgePath(points) {
  if (points.length < 2) return "";
  let d = `M${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i], m = (a.y + b.y) / 2;
    d += ` C${a.x},${m} ${b.x},${m} ${b.x},${b.y}`;
  }
  return d;
}
