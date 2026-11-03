// Identifiers (PR 17 §4): one atom for machine values — digests, `er2:` handles, run keys, paths, session ids, source
// URLs — shortened for the eye, the exact value kept in its `title` and on Copy; and sha256 tokens inside prose become
// that atom, formatting only (the text is otherwise unchanged and escaped as it always was). Pure functions, pinned with
// the catalogue's own caps: no other module may shorten an identifier its own way.
import assert from "node:assert/strict";
import { test } from "vitest";

const identifiers = await import("../app/identifiers.js");
const { shortIdentifier, identifierChip, proseHtml } = identifiers;
const catalogue = (await import("../catalogue.json", { with: { type: "json" } })).default;
const IDENT = catalogue.identifier;
const DIGEST = `sha256:${"0a1b2c3d".repeat(8)}`;
const HANDLE = `er2:eyJnIjoxLCJwIjoiczI1Ni05NDQ0NzkwMDk4NTFlMmRmMWY0NDFkNmU5YTcxYjM0YTkwMzI2NmJkYWQ5MzRiZjM4YTk4ZThmMmY3ZDQwM2U1IiwicyI6InMyNTYtZDBlZjZlOTMyZTFjOGEyMDU0NGE1MDIxZjFjZTY4ZTRlMzJkZTZiMGJlNWU5MTE3OTJlZDAxMWNjZGNhYzYzNiJ9:ZvlCb8NjosyRfUpo_MjUyfvaQgvD5RQMuWwDxl_L6gQ`;
const KEY = `s256-0ae90cdacdb49230553a9c64137c22d1b614182a043ffdc6a6ae65af30c9ee17/s256-405b28cee2b1c2980b78f6044c23fda4ec8f9ae0a86e56da7e2b97090f74333b/1`;
const PATH = "/home/amelia.dvn/empirica-projects/p-3f9c2a81b7d4/r-5e0a91c88f2b7e/gen-1/hitl/0a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789.json";
const URL = "https://www.rfc-editor.org/rfc/rfc4180.txt";

test("a long digest shortens to its prefix and its two ends; a value already short is left exactly as it is", () => {
  assert.equal(shortIdentifier(DIGEST),
    `sha256:${DIGEST.slice(7, 7 + IDENT.digest_head)}…${DIGEST.slice(-IDENT.digest_tail)}`,
    "sha256: kept, its head and tail shown, the middle elided");
  assert.equal(shortIdentifier("sha256:0a1b2c3d4e5f"), "sha256:0a1b2c3d4e5f",
    "an identity already its short form is never shortened again");
  assert.equal(shortIdentifier("C0"), "C0", "a claim id is not a long identifier");
  assert.equal(shortIdentifier(""), "", "never a word where nothing was");
});

test("every long machine value keeps the part that says what it is: a handle kept an handle, a key a key, a URL its origin", () => {
  const handle = shortIdentifier(HANDLE);
  assert.ok(handle.startsWith("er2:"), "an er2 handle stays recognisable");
  assert.equal(handle, `${HANDLE.slice(0, IDENT.head)}…${HANDLE.slice(-IDENT.tail)}`, "its head and tail shown");
  assert.equal(shortIdentifier(KEY), `${KEY.slice(0, IDENT.head)}…${KEY.slice(-IDENT.tail)}`, "a run key: head and tail");
  assert.equal(shortIdentifier(PATH), `${PATH.slice(0, IDENT.head)}…${PATH.slice(-IDENT.tail)}`, "a path: head and tail");
  assert.ok(shortIdentifier(URL).length > IDENT.max, "a URL long enough to shorten is shortened");
  assert.ok(shortIdentifier(URL).startsWith("https://www.rfc-editor.org"), "a URL keeps its origin");
  assert.ok(shortIdentifier(URL).endsWith(IDENT.url_tail > 0 ? URL.slice(-IDENT.url_tail) : URL),
    "and its end, so the page it names stays identifiable");
  const shortUrl = "https://example.test/x";
  assert.equal(shortIdentifier(shortUrl), shortUrl, "a URL already short is left exactly");
});

test("identifierChip shows the short form, holds the exact value in its title, and marks the atom", () => {
  const chip = identifierChip(DIGEST);
  assert.match(chip, new RegExp(`class="ev-id[^"]*"`), "one identifier atom class");
  assert.match(chip, new RegExp(`title="${DIGEST}"`), "the exact value is the hover");
  assert.ok(chip.includes(shortIdentifier(DIGEST)), "the short form is what is read");
  const plain = identifierChip("C0");
  assert.ok(plain.includes("C0") && plain.includes('title="C0"'), "a short value needs no shortening, always a title");
});

test("prose escapes the text unchanged and turns sha256 tokens into copyable identifier atoms — full digests and shortened ones, formatting only", () => {
  const finding = `Claim C3 <b>not approved</b>: the artifact ${DIGEST} differs from the pinned sha256:aaaaaaaaaaaa, see ${HANDLE}`;
  const html = proseHtml(finding);
  assert.ok(html.includes("&lt;b&gt;"), "markup in a published string stays text");
  assert.ok(!html.includes("<b>"), "nothing new is introduced as markup of the sheet's own");
  assert.ok(html.includes(HANDLE), "an er2 handle outside the pattern stays the escaped prose it always was, no atom, no copy");
  assert.ok(html.includes("Claim C3 &lt;b&gt;not approved&lt;/b&gt;: the artifact "), "the sentence around the token is burst for burst");
  const atoms = [...html.matchAll(/<ev-copy text="(sha256:[0-9a-f]+)" quiet/g)].map((m) => m[1]);
  assert.equal(atoms.length, 2, "one quiet atom per sha256 token, and only those");
  assert.ok(atoms.includes(DIGEST), "a full digest becomes an atom that carries the exact value");
  assert.ok(atoms.includes("sha256:aaaaaaaaaaaa"), "a shortened token becomes an atom too, its own value carried exactly");
  assert.ok(html.includes("differs from the pinned "), "the words between the two tokens are untouched");
  const alone = proseHtml("nothing machine here");
  assert.equal(alone, "nothing machine here", "prose without a token is escaped only");
});
