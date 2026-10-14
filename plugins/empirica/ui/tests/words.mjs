// Words a person reads on a screen, counted the same way everywhere (the word budget, tests/views.test.mjs and the journeys):
// visible text only (no screen-reader-only text, no titles), each run of letters or digits one word.
/** The visible text of an HTML string: tags, screen-reader-only spans and attributes dropped; an `<ev-more text>` counts. */
export const visible = (html) => html.replace(/<span class="sr-only">[^<]*<\/span>/g, " ").replace(/ (title|aria-label)="[^"]*"/g, "")
  .replace(/ text="([^"]*)"/g, ">$1<").replace(/<[^>]+>/g, " ").replace(/&#39;/g, "'").replace(/&amp;/g, "&").replace(/&quot;/g, '"')
  .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ");
/** How many words a string of visible text holds. */
export const wordsIn = (text) => text.split(" ").filter((w) => /[A-Za-z0-9]/.test(w)).length;
