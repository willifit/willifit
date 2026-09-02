// Lock for W1: sw.js's SHELL_FILES precache list must include every
// same-origin <script src> / <link rel=stylesheet href> that index.html's
// <head> and body reference. Before this fix it was missing
// /vendor/leaflet/leaflet.js and leaflet.css entirely -- a first-time
// visitor who went offline and reloaded got "L is not defined" because the
// service worker had precached everything EXCEPT the map library.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.join(__dirname, "..");
const swPath = process.env.SW_JS_PATH || path.join(repoRoot, "sw.js");
const indexHtmlPath =
  process.env.INDEX_HTML_PATH || path.join(repoRoot, "index.html");

function extractShellFiles(swSource) {
  const m = swSource.match(/const SHELL_FILES = (\[[\s\S]*?\n\]);/);
  assert.ok(m, "could not find `const SHELL_FILES = [...];` in " + swPath);
  return vm.runInNewContext("(" + m[1] + ")");
}

// Same-origin script/link references in index.html's <head> and <body>.
// External hosts (https://...) are deliberately excluded -- the shell only
// needs to precache assets this origin actually serves.
function extractSameOriginAssetPaths(html) {
  const paths = new Set();
  const scriptRe = /<script\b[^>]*\bsrc="(\/[^"]+)"/gi;
  const linkRe = /<link\b[^>]*\brel="stylesheet"[^>]*\bhref="(\/[^"]+)"/gi;
  let m;
  while ((m = scriptRe.exec(html))) paths.add(m[1]);
  while ((m = linkRe.exec(html))) paths.add(m[1]);
  // Also catch `<link rel=stylesheet href="...">` (no quotes on rel value).
  const linkRe2 = /<link\b[^>]*\brel=stylesheet[^>]*\bhref="(\/[^"]+)"/gi;
  while ((m = linkRe2.exec(html))) paths.add(m[1]);
  return paths;
}

test("SHELL_FILES precaches every same-origin script/stylesheet index.html references", () => {
  const swSource = readFileSync(swPath, "utf8");
  const shellFiles = extractShellFiles(swSource);
  const html = readFileSync(indexHtmlPath, "utf8");
  const referenced = extractSameOriginAssetPaths(html);

  assert.ok(referenced.size > 0, "sanity: index.html must reference at least one same-origin script/stylesheet");

  const missingFromShell = [...referenced].filter((p) => !shellFiles.includes(p)).sort();
  assert.deepEqual(
    missingFromShell,
    [],
    `SHELL_FILES is missing: ${missingFromShell.join(", ")}`
  );
});

test("every referenced same-origin asset actually exists on disk", () => {
  const html = readFileSync(indexHtmlPath, "utf8");
  const referenced = extractSameOriginAssetPaths(html);
  const missingOnDisk = [...referenced].filter(
    (p) => !existsSync(path.join(repoRoot, p.replace(/^\//, "")))
  );
  assert.deepEqual(missingOnDisk, [], `referenced but missing on disk: ${missingOnDisk.join(", ")}`);
});
