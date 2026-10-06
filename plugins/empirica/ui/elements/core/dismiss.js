// Esc dismisses one layer at a time: the innermost thing that is open. Each layer says whether it is open and how to
// close itself; one document listener asks them in priority order and lets exactly the first open one act, so a hint
// over a panel over a selection takes three presses and never two things at once. A press already handled (default
// prevented) or made in a native control (a select's list, a text field) is left to that control.
// Behavioural (E3 exception): the only document-level Esc listener in the UI.
const layers = new Set();
let bound = false;

function onKey(e) {
  if (e.key !== "Escape" || e.defaultPrevented) return;
  if (e.target instanceof Element && e.target.closest("select, input, textarea, [contenteditable]")) return;
  const open = [...layers].filter((l) => l.open()).sort((a, b) => b.priority - a.priority);
  if (!open.length) return;
  e.preventDefault();
  open[0].close();
}

/** Register a layer `{priority, open(), close()}` (higher priority is closed first); returns how to remove it. */
export function layer(l) {
  layers.add(l);
  if (!bound) { document.addEventListener("keydown", onKey); bound = true; }
  return () => { layers.delete(l); };
}

/** The priorities, innermost first. */
export const PRIORITY = { hint: 30, popover: 25, selection: 20, panel: 10 };
