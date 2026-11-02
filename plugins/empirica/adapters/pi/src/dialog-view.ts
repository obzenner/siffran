import { Key, matchesKey, truncateToWidth, visibleWidth, wrapTextWithAnsi } from "@earendil-works/pi-tui";

export interface BudgetControl {
  key: string; label: string; short: string; help: string;
  value: number; used: number; minimum: number; maximum: number;
}
export interface Dialog {
  epoch: number; control_mode: string; state: string;
  reviews_left: { proposal: number; total: number };
  goal: string;
  rationale: string | null;
  rationale_label: string;
  amendment_warning: string;
  invocation: { host: string; interactive: boolean | null; signal: string; delegation: boolean } | null;
  budgets: BudgetControl[];
}
export interface DialogState {
  focus: number;
  values: Record<string, number | string>;
  editing: string | null;
  approve: boolean;
}
export type Approve = { type: "approve"; configuration: { budgets: Record<string, number> } };
export type Reject = { type: "reject" };
export type Dismiss = { type: "dismiss" };
export type DialogDecision = Approve | Reject | Dismiss;
export type Reduction = { state: DialogState } | { done: DialogDecision };
export interface DialogTheme {
  accent(text: string): string;
  muted(text: string): string;
  warning(text: string): string;
}

/** Return immutable initial values focused on the first editable row. */
export function initialState(dialog: Dialog, confirmation = false): DialogState {
  return { focus: confirmation ? dialog.budgets.length : 0,
    values: Object.fromEntries(dialog.budgets.map(row => [row.key, row.value])),
    editing: null, approve: true };
}

function configuration(dialog: Dialog, state: DialogState): Approve["configuration"] {
  return {
    budgets: Object.fromEntries(dialog.budgets.map(row => [row.key, state.values[row.key] as number])),
  };
}

export interface RangeError { row: BudgetControl; message: string }

/** Return the first invalid budget value, independent of input history. */
export function rangeError(dialog: Dialog, values: DialogState["values"]): RangeError | null {
  const inRange = (row: BudgetControl, value: unknown) =>
    Number.isInteger(value) && (value as number) >= row.minimum && (value as number) <= row.maximum;
  const row = dialog.budgets.find(item => !inRange(item, values[item.key]));
  return row ? { row, message: `Must be an integer between ${row.minimum} and ${row.maximum}` } : null;
}

function edited(state: DialogState, row: BudgetControl, raw: string): DialogState {
  const value: number | string = /^[0-9]+$/.test(raw) ? Number(raw) : raw;
  return { ...state, values: { ...state.values, [row.key]: value }, editing: raw };
}

/** Reduce one native key into a new state or a typed terminal decision. */
export function reduce(state: DialogState, key: string, dialog: Dialog,
                       confirmation = false): Reduction {
  const fields = dialog.budgets.length;
  if (matchesKey(key, Key.escape)) return { done: { type: "dismiss" } };
  if (matchesKey(key, Key.up)) return { state: { ...state, focus: Math.max(0, state.focus - 1), editing: null } };
  if (matchesKey(key, Key.down)) return { state: { ...state, focus: Math.min(fields, state.focus + 1), editing: null } };
  if (state.focus === fields) {
    if (matchesKey(key, Key.left)) return { state: { ...state, approve: true } };
    if (matchesKey(key, Key.right)) return { state: { ...state, approve: false } };
    if (matchesKey(key, Key.enter)) {
      if (state.approve) {
        if (rangeError(dialog, state.values)) return { state };
        return { done: { type: "approve", configuration: configuration(dialog, state) } };
      }
      return { done: confirmation ? { type: "dismiss" } : { type: "reject" } };
    }
    return { state };
  }
  if (confirmation) return { state };
  if (/^[0-9]$/.test(key)) {
    const row = dialog.budgets[state.focus];
    return { state: edited(state, row, state.editing === null ? key : state.editing + key) };
  }
  if (matchesKey(key, Key.backspace) && state.editing !== null) {
    const row = dialog.budgets[state.focus];
    return { state: edited(state, row, state.editing.slice(0, -1)) };
  }
  if (matchesKey(key, Key.enter) && !rangeError(dialog, state.values))
    return { state: { ...state, focus: fields, editing: null } };
  return { state };
}

function truncate(text: string, width: number): string {
  return visibleWidth(text) <= width ? text : truncateToWidth(text, width, "…");
}

function pad(text: string, width: number): string {
  return text + " ".repeat(Math.max(0, width - visibleWidth(text)));
}

/** Format a remaining duration, rounded up so the display never overstates the time left. */
export function duration(timeoutMs: number): string {
  const seconds = Math.ceil(Math.max(0, timeoutMs) / 1000);
  return seconds >= 60 ? `${Math.ceil(seconds / 60)} min` : `${seconds} sec`;
}

const INDENT = "      ";
const SHORT_BODY = 12; // the ADR-0072 Q4 short digest: `sha256:` plus twelve hex

/** The bound document the Python private bridge wrote (ADR-0072, PR 09): its full digest, the
 * file's absolute path — the one copy-paste surface — the core-owned amendment priors the
 * confirm form's ``(was …)`` values come from (never recomputed by the host), and the parsed
 * document's core-derived ``question`` (M1, review 2) that alone chooses which dialog is
 * shown. No text crosses the Pi transport; the human opens the file. */
export interface BoundDocument {
  digest: string;
  path: string;
  priors: Record<string, number> | null;
  question: "approve" | "confirm_amendment";
}
export interface RenderOptions { confirmation: boolean; timeoutMs: number; document?: BoundDocument }

/** Title on the left and run facts on the right; the facts move to their own line if narrow. */
function header(dialog: Dialog, options: RenderOptions, width: number, theme: DialogTheme): string[] {
  const title = options.confirmation
    ? "Empirica · confirm edited configuration" : "Empirica · approve run configuration";
  const reviews = dialog.reviews_left.proposal;
  const facts = [`epoch ${dialog.epoch}`,
    ...(options.confirmation ? [] : [`${reviews} ${reviews === 1 ? "review" : "reviews"} left`]),
    duration(options.timeoutMs)].join(" · ");
  const gap = width - visibleWidth(title) - visibleWidth(facts);
  return gap >= 2 ? [title + " ".repeat(gap) + theme.muted(facts)] : [title, theme.muted(facts)];
}

/** The goal's first line, quoted and bounded so the controls always stay on screen (ADR-0072
 * "What the forms keep": the full detail lives in the document). */
function goalLines(dialog: Dialog, width: number): string[] {
  const first = `"${dialog.goal.split("\n")[0]}"`;
  const gap = width - "Goal  ".length;
  const shown = visibleWidth(first) <= gap ? first : truncateToWidth(first, gap - 1, "…") + "\"";
  return [`Goal  ${shown}`];
}

/** The one line under the contract-owned label that names where the rationale actually is. */
function pointerLines(dialog: Dialog, width: number): string[] {
  return wrapTextWithAnsi(`${dialog.rationale_label} and full detail: in the document`, Math.max(10, width));
}

/** ADR-0072 Q4's bound lines: the short digest, then the document's absolute path. Wider than
the screen the path wraps — never truncated or cut — because a cut path is a corrupted paste.
M3 (review 2): the realistic sidecar path is ~276 characters (`<home>/projects/s256-<64 hex>/
runs/s256-<64 hex>/gen-<n>/hitl/<64 hex>.json`), so it wraps at every realistic width, and it
breaks **only at "/" boundaries**: every rendered fragment is a valid path piece and the joins
rebuild the path exactly, the 69-character file name owns the last line whole so the digest's
hex is never split, and no segment is ever cut while it fits the width. A single segment wider
than the dialog width (the 70-column `s256-<64 hex>` directories below width 70) is cut by
`renderLines`' global width rule — chosen over overflow, because every rendered line fits the
width (at the widths the dialog tests pin, 80 and above, nothing is cut at all). */
function documentLines(document: BoundDocument | undefined, width: number): string[] {
  if (!document) return [];
  const short = `Document: ${document.digest.slice(0, "sha256:".length + SHORT_BODY)}`;
  const head = wrapTextWithAnsi(short, Math.max(10, width));
  const segments = document.path.match(/\/[^/]*/g) ?? [];
  const path: string[] = [];
  let line = "";
  for (let index = 0; index < segments.length; index++) {
    const segment = segments[index] as string;
    const isFileName = index === segments.length - 1;
    if (line !== "" && (isFileName || visibleWidth(line) + visibleWidth(segment) > width)) {
      path.push(line);
      line = "";
    }
    line += segment;
  }
  if (line !== "") path.push(line);
  return [...head, ...path];
}

/** The amendment warning, in full, in the confirm view only. */
function warningLines(dialog: Dialog, options: RenderOptions, width: number,
                      theme: DialogTheme): string[] {
  if (!options.confirmation) return [];
  return wrapTextWithAnsi(dialog.amendment_warning, Math.max(10, width))
    .map(line => theme.warning(line));
}

type Control = BudgetControl;

function valueText(value: DialogState["values"][string]): string {
  return String(value);
}

function detailText(control: Control): string {
  return `used ${control.used} · allowed ${control.minimum}–${control.maximum}`;
}

/** One row per control: aligned label and value; details inline when all fit, else all beneath. */
function controlLines(dialog: Dialog, state: DialogState, options: RenderOptions,
                      width: number, theme: DialogTheme): string[] {
  const controls: Control[] = dialog.budgets;
  const priors = options.confirmation ? options.document?.priors ?? null : null;
  const shown = controls.map(control => {
    const value = state.values[control.key], prior = priors ? priors[control.key] : undefined;
    const was = prior !== undefined && prior !== value ? ` (was ${valueText(prior)})` : "";
    return valueText(value) + was;
  });
  const labelWidth = Math.max(...controls.map(control => visibleWidth(control.label))) + 2;
  const valueWidth = Math.max(...shown.map(visibleWidth)) + 2;
  const error = rangeError(dialog, state.values);
  const rowWidth = 2 + labelWidth + valueWidth;
  const inline = controls.every(control => rowWidth + visibleWidth(detailText(control)) <= width);
  return controls.flatMap((control, index) => {
    const pointer = state.focus === index ? theme.accent("❯") : " ";
    const row = `${pointer} ${pad(control.label, labelWidth)}${pad(shown[index], valueWidth)}`;
    const detail = detailText(control);
    return [
      inline ? row + theme.muted(detail) : row.trimEnd(),
      ...(inline ? [] : [INDENT.slice(2) + theme.muted(detail)]),
      ...(error?.row.key === control.key ? [theme.warning(`  ⚠ ${error.message}`)] : []),
    ];
  });
}

/** The two buttons; the pointer marks the chosen one while the button row has focus. */
function buttonLine(dialog: Dialog, state: DialogState, options: RenderOptions, theme: DialogTheme): string {
  const focused = state.focus === dialog.budgets.length;
  const labels = options.confirmation ? ["Approve", "Keep pending"] : ["Approve", "Reject"];
  const button = (label: string, chosen: boolean) =>
    chosen ? `${focused ? theme.accent("❯") : " "} ${theme.accent(label)}` : `  ${label}`;
  return `  ${button(labels[0], state.approve)}   ${button(labels[1], !state.approve)}`;
}

/** The key legend, keeping as many hints as fit the width (most important first). */
function legendLine(options: RenderOptions, width: number, theme: DialogTheme): string {
  const hints = options.confirmation
    ? ["enter confirm", "←→ choose", "esc keep pending"]
    : ["↑↓ move", "type to edit", "←→ choose", "enter confirm", "esc later"];
  const fitting = hints.filter((_, index) =>
    visibleWidth(hints.slice(0, index + 1).join(" · ")) <= width);
  return theme.muted(fitting.join(" · "));
}

/** Render the whole dialog frame for the supplied terminal width; every line fits the width.
 * Both hosts keep the same content and order (PR 09 mirrors PR 08's Claude forms): header, the
 * goal's first line (review only), the pointer line, the amendment warning (confirm only), the
 * document digest line and the document's absolute path, the editable rows, buttons, hints. */
export function renderLines(dialog: Dialog, state: DialogState, width: number, theme: DialogTheme,
                            options: RenderOptions): string[] {
  return [
    ...header(dialog, options, width, theme),
    ...(options.confirmation ? [] : goalLines(dialog, width)),
    ...pointerLines(dialog, width),
    ...warningLines(dialog, options, width, theme),
    ...documentLines(options.document, width),
    "",
    ...controlLines(dialog, state, options, width, theme),
    "",
    buttonLine(dialog, state, options, theme),
    legendLine(options, width, theme),
  ].map(line => truncate(line, width));
}
