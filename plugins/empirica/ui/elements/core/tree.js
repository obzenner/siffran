// A run's support graph as an indented list: from the root, each claim's supporters beneath it. A claim that supports
// several is listed once in full and named again where it recurs; claims no edge reaches are listed after. Layout only:
// the edges are the observer's. Pure.

/** @returns {{id:string, again:boolean, children:object[]}[]} the roots of the list */
export function supportTree(root, claims, edges) {
  const below = new Map(claims.map((c) => [c.id, []]));
  for (const e of edges) below.get(e.from)?.push(e.to);
  const seen = new Set();
  const node = (id) => {
    if (seen.has(id)) return { id, again: true, children: [] };
    seen.add(id);
    return { id, again: false, children: (below.get(id) ?? []).filter((x) => below.has(x)).map(node) };
  };
  const tree = root && below.has(root) ? [node(root)] : [];
  for (const c of claims) if (!seen.has(c.id)) tree.push(node(c.id));
  return tree;
}
