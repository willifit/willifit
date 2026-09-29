// Regression tests for the 2026-09-29 xss.innerhtml fix round on index.html.
//
// Two things are covered, extracted straight out of index.html's inline
// script (no jsdom in this repo -- same "extract the render function" move
// tests/test_state_names.mjs already uses for this file):
//
// 1. renderBrowseTab() -- a genuine stored-XSS gap the mechanical scanner
//    could not see: `container.innerHTML = html` sits on a bare identifier,
//    not a template literal with ${...} in it, so xss.innerhtml's
//    per-interpolation check never reached the `html +=` lines that built
//    it. c.slug/c.name (from data/index.json, city/garage data --
//    attacker-influenced per the same threat model admin.html's report
//    fields use) went in unescaped, and STATE_NAMES[stateCode] || stateCode
//    fell through to the raw, unescaped code for anything outside the
//    fixed whitelist.
//
// 2. safeCoord() -- the numeric guard now used everywhere g.lat/g.lng reach
//    an href="..." attribute (loadDetailHero's Street View/aerial links,
//    renderDetail's directions links). A pure function, tested directly.
//
// INDEX_HTML_PATH env var points this at a different revision (e.g. a
// scratch copy of `git show origin/main:index.html`) to prove it FAILS
// there and passes on this branch.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const INDEX_HTML_PATH =
  process.env.INDEX_HTML_PATH || path.join(__dirname, "..", "index.html");

const PAYLOAD = '"><img src=x onerror=alert(1)>';

function extractBetween(src, startMarker, endMarker, label) {
  const start = src.indexOf(startMarker);
  if (start < 0) throw new Error(`could not find start marker for ${label} in ${INDEX_HTML_PATH}`);
  const end = src.indexOf(endMarker, start + startMarker.length);
  if (end < 0) throw new Error(`could not find end marker for ${label} in ${INDEX_HTML_PATH}`);
  return src.slice(start, end);
}

// escapeHTML/STATE_NAMES/garageWord/garageCountLabel/renderBrowseTab all
// predate this fix round (only what renderBrowseTab does WITH them
// changed), so this half is extracted separately from safeCoord below --
// pointing INDEX_HTML_PATH at a pre-fix revision must fail on the actual
// unescaped-output assertion, not on a missing-helper extraction error.
function buildBrowseTabSource(html) {
  // escapeHTML: fixed 8-line shape, bounded by its own function keyword and
  // closing brace before the next declaration.
  const escapeHTML = extractBetween(
    html,
    "function escapeHTML(s) {",
    "\n}\n",
    "escapeHTML"
  ) + "\n}\n";

  // STATE_NAMES: same regex tests/test_state_names.mjs already relies on.
  const stateNamesMatch = html.match(/const STATE_NAMES = (\{[\s\S]*?\n\});/);
  if (!stateNamesMatch) throw new Error("could not find STATE_NAMES in " + INDEX_HTML_PATH);
  const stateNames = `const STATE_NAMES = ${stateNamesMatch[1]};`;

  // garageWord / garageCountLabel: single-line functions renderBrowseTab calls.
  const garageWordMatch = html.match(/function garageWord\(n\) \{[^\n]*\n?\}/);
  const garageCountLabelMatch = html.match(/function garageCountLabel\(n\) \{[^\n]*\n?\}/);
  if (!garageWordMatch || !garageCountLabelMatch) {
    throw new Error("could not find garageWord/garageCountLabel in " + INDEX_HTML_PATH);
  }

  // renderBrowseTab: bounded by its own signature and the next top-level
  // function declaration (initPickerMap), which follows it directly today.
  const renderBrowseTab = extractBetween(
    html,
    "function renderBrowseTab() {",
    "\nfunction initPickerMap",
    "renderBrowseTab"
  );

  return [
    escapeHTML,
    stateNames,
    garageWordMatch[0],
    garageCountLabelMatch[0],
    renderBrowseTab,
  ].join("\n\n");
}

// safeCoord is new in this fix round -- kept as its own tiny sandbox so a
// pre-fix INDEX_HTML_PATH fails with "function does not exist", which is
// the correct failure for a helper that is the fix itself.
function buildSafeCoordSource(html) {
  return extractBetween(
    html,
    "function safeCoord(n) {",
    "\n}\n",
    "safeCoord"
  ) + "\n}\n";
}

// Minimal fake element: captures whatever gets assigned to .innerHTML and
// no-ops everything renderBrowseTab wires up afterward (event listeners on
// rows we never click in this test).
function makeFakeContainer() {
  return {
    innerHTML: "",
    querySelectorAll() { return []; },
  };
}

function loadBrowseTabHelpers() {
  const html = readFileSync(INDEX_HTML_PATH, "utf8");
  const code = buildBrowseTabSource(html);

  const container = makeFakeContainer();
  const fakeDocument = {
    getElementById: (id) => (id === "browseContent" ? container : makeFakeContainer()),
  };

  const sandbox = {
    document: fakeDocument,
    console,
    CITY_INDEX: [],
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  const context = vm.createContext(sandbox);
  vm.runInContext(code, context, { filename: "index.html-render-helpers.js" });
  return { context, container };
}

function loadSafeCoord() {
  const html = readFileSync(INDEX_HTML_PATH, "utf8");
  const code = buildSafeCoordSource(html);
  const sandbox = { console };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  vm.runInContext(code, context, { filename: "index.html-safecoord.js" });
  return { context };
}

const escapedPayload = PAYLOAD.replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

function assertNoRawInjection(html, label) {
  assert.ok(
    !/<img\b/i.test(html),
    `${label}: output contains an unescaped <img> tag:\n${html}`
  );
  assert.ok(
    !html.includes('"><img'),
    `${label}: raw unescaped payload break-out sequence found in output`
  );
  assert.ok(
    html.includes(escapedPayload),
    `${label}: expected the HTML-escaped payload to appear somewhere in output (proves escaping actually ran):\n${html}`
  );
}

test("renderBrowseTab escapes a hostile city name and slug (data/index.json)", () => {
  const { context, container } = loadBrowseTabHelpers();
  context.CITY_INDEX = [
    {
      region: "West",
      state: "NV",
      status: "live",
      name: PAYLOAD,
      slug: PAYLOAD,
      garage_count: 3,
    },
  ];
  vm.runInContext("renderBrowseTab()", context);
  assertNoRawInjection(container.innerHTML, "renderBrowseTab (city name/slug)");
});

test("renderBrowseTab escapes an unrecognized state code falling through STATE_NAMES", () => {
  const { context, container } = loadBrowseTabHelpers();
  context.CITY_INDEX = [
    {
      region: "West",
      state: PAYLOAD, // not a real postal code -> STATE_NAMES[stateCode] is undefined
      status: "live",
      name: "Somewhere",
      slug: "somewhere-nv",
      garage_count: 1,
    },
  ];
  vm.runInContext("renderBrowseTab()", context);
  assertNoRawInjection(container.innerHTML, "renderBrowseTab (STATE_NAMES fallback)");
});

test("renderBrowseTab still renders normal city data unchanged (no behavior change)", () => {
  const { context, container } = loadBrowseTabHelpers();
  context.CITY_INDEX = [
    { region: "West", state: "NV", status: "live", name: "Las Vegas", slug: "las-vegas-nv", garage_count: 42 },
  ];
  vm.runInContext("renderBrowseTab()", context);
  assert.ok(container.innerHTML.includes("Las Vegas"), "city name should render as normal");
  assert.ok(container.innerHTML.includes('data-slug="las-vegas-nv"'), "slug should render unchanged for normal data");
  assert.ok(container.innerHTML.includes("Nevada"), "STATE_NAMES lookup should still resolve for a real code");
  assert.ok(container.innerHTML.includes("42 garages"), "garage count should still render");
});

test("safeCoord neutralizes non-numeric / hostile lat-lng input", () => {
  const { context } = loadSafeCoord();
  assert.equal(vm.runInContext(`safeCoord(${JSON.stringify(PAYLOAD)})`, context), 0);
  assert.equal(vm.runInContext("safeCoord('javascript:alert(1)')", context), 0);
  assert.equal(vm.runInContext("safeCoord(undefined)", context), 0);
  assert.equal(vm.runInContext("safeCoord(null)", context), 0);
  assert.equal(vm.runInContext("safeCoord(NaN)", context), 0);
  assert.equal(vm.runInContext("safeCoord(Infinity)", context), 0);
});

test("safeCoord passes real coordinates through unchanged (no behavior change)", () => {
  const { context } = loadSafeCoord();
  assert.equal(vm.runInContext("safeCoord(36.1699)", context), 36.1699);
  assert.equal(vm.runInContext("safeCoord(-115.1398)", context), -115.1398);
  assert.equal(vm.runInContext("safeCoord('36.1699')", context), 36.1699);
  assert.equal(vm.runInContext("safeCoord(0)", context), 0);
});
