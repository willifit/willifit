// Lock for U3: STATE_NAMES (index.html) must have an entry for every state
// code that actually appears in data/index.json. It was previously missing
// DE, MS, MT, ND, NH, PR, SD, WV -- those cities would render with a bare
// two-letter code ("MS") instead of a state name in the city picker.
//
// INDEX_HTML_PATH env var lets this be pointed at an old revision (e.g. a
// worktree checkout of a prior commit) to prove the test fails there.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const indexHtmlPath =
  process.env.INDEX_HTML_PATH || path.join(__dirname, "..", "index.html");
const indexDataPath =
  process.env.INDEX_JSON_PATH || path.join(__dirname, "..", "data", "index.json");

function extractStateNames(html) {
  const m = html.match(/const STATE_NAMES = (\{[\s\S]*?\n\});/);
  assert.ok(m, "could not find `const STATE_NAMES = {...};` in " + indexHtmlPath);
  const obj = vm.runInNewContext("(" + m[1] + ")");
  return obj;
}

function distinctStatesInData(json) {
  const arr = Array.isArray(json) ? json : json.cities || Object.values(json);
  const states = new Set();
  for (const c of arr) {
    if (c && c.state) states.add(c.state);
  }
  return states;
}

test("STATE_NAMES has an entry for every state used in data/index.json", () => {
  const html = readFileSync(indexHtmlPath, "utf8");
  const stateNames = extractStateNames(html);
  const dataJson = JSON.parse(readFileSync(indexDataPath, "utf8"));
  const usedStates = distinctStatesInData(dataJson);

  assert.ok(usedStates.size > 0, "sanity: data/index.json must reference at least one state");

  const missing = [...usedStates].filter((code) => !(code in stateNames)).sort();
  assert.deepEqual(
    missing,
    [],
    `STATE_NAMES is missing entries for: ${missing.join(", ")}`
  );
});
