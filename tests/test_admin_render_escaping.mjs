// Regression test for the F1 stored-XSS finding: admin.html renders
// attacker-controlled report fields (submitted via public forms) and must
// escape every one of them before interpolating into HTML/attributes.
//
// This extracts admin.html's inline <script>, evaluates it in a `node:vm`
// context with minimal fake browser globals (just enough for the
// function *definitions* to load -- no fetch is ever triggered), then
// calls every render function with a report where EVERY field is an XSS
// payload and asserts none of it survives unescaped in the output.
//
// Supports ADMIN_HTML_PATH env override so the same test can be pointed at
// a scratch copy of `git show HEAD:admin.html` to prove it FAILS against
// the pre-fix version (see the companion script that does this, or run:
//   ADMIN_HTML_PATH=/path/to/old-admin.html node --test tests/test_admin_render_escaping.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ADMIN_HTML_PATH =
  process.env.ADMIN_HTML_PATH || path.join(__dirname, "..", "admin.html");

const PAYLOAD = '"><img src=x onerror=alert(1)>';

function extractInlineScript(html) {
  // admin.html's own script contains a comment quoting the literal strings
  // "<script>" and "</script>" as prose (see test_admin_csp_hash.mjs for
  // the full explanation) -- a naive non-greedy regex match stops at that
  // quoted "</script>" instead of the real closing tag. Strip HTML
  // comments first, then bound the body by the first real opening tag and
  // the LAST "</script>" in the document (always the real closing tag,
  // since nothing follows it in the file).
  const stripped = html.replace(/<!--[\s\S]*?-->/g, "");
  const openMatch = stripped.match(/<script(?:\s[^>]*)?>/);
  if (!openMatch) throw new Error("no <script> open tag found in " + ADMIN_HTML_PATH);
  const openEnd = openMatch.index + openMatch[0].length;
  const closeIdx = stripped.lastIndexOf("</script>");
  if (closeIdx <= openEnd) throw new Error("no </script> close tag found after the open tag in " + ADMIN_HTML_PATH);
  return stripped.slice(openEnd, closeIdx);
}

function makeFakeElement() {
  const el = {
    style: {},
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {},
    removeEventListener() {},
    closest() { return null; },
    querySelector() { return null; },
    value: "",
    textContent: "",
    innerHTML: "",
  };
  return el;
}

function loadAdminScript() {
  const html = readFileSync(ADMIN_HTML_PATH, "utf8");
  const code = extractInlineScript(html);

  const sessionStorageBacking = {};
  const fakeSessionStorage = {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(sessionStorageBacking, k) ? sessionStorageBacking[k] : null),
    setItem: (k, v) => { sessionStorageBacking[k] = String(v); },
    removeItem: (k) => { delete sessionStorageBacking[k]; },
  };

  const fakeDocument = {
    getElementById: () => makeFakeElement(),
  };

  const fakeFetch = async () => {
    throw new Error("fetch should not be called by this test");
  };

  const sandbox = {
    document: fakeDocument,
    sessionStorage: fakeSessionStorage,
    fetch: fakeFetch,
    navigator: { clipboard: { writeText: async () => {} } },
    alert: () => {},
    console,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  const context = vm.createContext(sandbox);
  // Evaluate the script body. It ends by wiring up addEventListener calls
  // against document.getElementById(...) stubs -- all no-ops here -- so
  // nothing actually fires a network request or touches real DOM.
  vm.runInContext(code, context, { filename: "admin.html-inline-script.js" });
  return context;
}

function assertNoRawInjection(html, label) {
  // A bare substring check for "onerror=" would false-positive on
  // *correctly* escaped output: escapeHtml() only encodes & < > " ' , so
  // the text "onerror=alert(1)" survives as inert visible text once its
  // surrounding angle brackets become &lt;/&gt; -- that's safe, since the
  // browser can't parse an actual element out of it. The only thing that
  // actually matters is whether a REAL (unescaped) '<' ever starts a tag.
  assert.ok(
    !/<img\b/i.test(html),
    `${label}: output contains an unescaped <img> tag:\n${html}`
  );
  // The raw payload's opening break-out sequence (a literal, unescaped
  // '<') must never appear -- that's what would let the payload close the
  // enclosing attribute/element and start a real <img> tag. Note we do
  // NOT bare-string-search for "onerror=" here: escapeHtml() correctly
  // leaves '=' un-encoded, so "onerror=alert(1)" legitimately survives as
  // *inert visible text* once its surrounding angle brackets/quotes become
  // &lt;/&gt;/&quot; entities -- that's safe (no real tag can form around
  // it) and asserting its absence would just penalize correct escaping.
  assert.ok(
    !html.includes('"><img'),
    `${label}: raw unescaped payload break-out sequence found in output`
  );
  // Positive check: the escaped form of the payload must actually be
  // present somewhere -- proves the field was escaped, not silently
  // dropped or nulled out (which would make the negative checks above
  // pass for the wrong reason).
  const escapedPayload = PAYLOAD.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  assert.ok(
    html.includes(escapedPayload),
    `${label}: expected the HTML-escaped payload to appear somewhere in output (proves escaping actually ran):\n${html}`
  );
}

const hostileReport = {
  id: PAYLOAD,
  kind: "clearance-report",
  created_at: "2026-01-01T00:00:00.000Z",
  garage_id: PAYLOAD,
  garage_name: PAYLOAD,
  garage_addr: PAYLOAD,
  city_slug: PAYLOAD,
  city_name: PAYLOAD,
  city_state: PAYLOAD,
  previous_height_in: 84,
  previous_height_label: PAYLOAD,
  reported_height_in: 80,
  no_posted_sign: false,
  oversized_available: PAYLOAD,
  notes: PAYLOAD,
  contact: PAYLOAD,
  correction: PAYLOAD,
  issue_type: PAYLOAD,
  location_name: PAYLOAD,
  location_type: PAYLOAD,
  location_addr: PAYLOAD,
  location_lat: PAYLOAD,
  location_lng: PAYLOAD,
  oversized: PAYLOAD,
};

test("renderReport escapes every field when given a hostile report", () => {
  const ctx = loadAdminScript();
  const html = vm.runInContext("renderReport(REPORT)", Object.assign(ctx, { REPORT: hostileReport }));
  assertNoRawInjection(html, "renderReport");
});

test("renderNewLocation escapes every field, including location_lat/location_lng", () => {
  const ctx = loadAdminScript();
  const html = vm.runInContext("renderNewLocation(REPORT)", Object.assign(ctx, { REPORT: hostileReport }));
  assertNoRawInjection(html, "renderNewLocation");
  // location_lat/location_lng are the F1 finding specifically: a hostile,
  // non-numeric value must never reach the rendered Google Maps href.
  assert.ok(
    !html.includes(PAYLOAD),
    "renderNewLocation: raw location_lat/location_lng payload leaked into output"
  );
});

test("renderIssue escapes every field, including correction", () => {
  const ctx = loadAdminScript();
  const html = vm.runInContext("renderIssue(REPORT)", Object.assign(ctx, { REPORT: hostileReport }));
  assertNoRawInjection(html, "renderIssue");
});
