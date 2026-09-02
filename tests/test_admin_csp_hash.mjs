// Guards against CSP hash drift: admin.html's CSP meta tag allow-lists its
// single inline <script> by the SHA-256 (base64) hash of the exact bytes
// between <script> and </script>. If someone edits that script without
// recomputing the hash, the browser silently refuses to run it in
// production (no console error a casual glance would catch) -- so this
// test recomputes the hash from the live file and asserts the CSP meta
// still contains it, failing loudly in CI/dev instead.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ADMIN_HTML_PATH =
  process.env.ADMIN_HTML_PATH || path.join(__dirname, "..", "admin.html");

function readAdminHtml() {
  return readFileSync(ADMIN_HTML_PATH, "utf8");
}

function extractInlineScriptBody(html) {
  // Mirror the HTML parser exactly: the script element's body runs from the
  // end of the FIRST real <script> opening tag to the FIRST "</script>" that
  // follows it -- no matter where that "</script>" sits. A literal closing
  // tag inside a JS comment or string TERMINATES the element in every
  // browser (this happened once: a warning comment that said "between
  // <script> and </script>" cut the script to three lines, Chrome hashed
  // the stub, and the rest of the JS rendered as page text). HTML comments
  // in <head> are stripped first only so prose there cannot be mistaken for
  // the opening tag.
  const stripped = html.replace(/<!--[\s\S]*?-->/g, "");
  const openMatch = stripped.match(/<script(?:\s[^>]*)?>/);
  assert.ok(openMatch, "expected an inline <script> opening tag");
  assert.equal(
    (openMatch[0].match(/<script(\s[^>]*)?>/) || [])[1] || "",
    "",
    "inline <script> tag must have no attributes (e.g. no src=)"
  );
  const openEnd = openMatch.index + openMatch[0].length;
  const closeIdx = stripped.indexOf("</script>", openEnd);
  assert.ok(closeIdx > openEnd, "expected a closing </script> tag after the opening tag");
  const body = stripped.slice(openEnd, closeIdx);
  // The body must be the WHOLE program. If a "</script" appears anywhere
  // later in the file, the element ended early and the page is broken.
  const later = stripped.slice(closeIdx + "</script>".length);
  assert.ok(
    !/<\/script/i.test(later),
    "a second </script> appears after the inline script's closing tag -- " +
      "the script body probably contains a literal closing tag (in a comment " +
      "or string) that terminates the element early in the browser"
  );
  assert.ok(
    body.length > 1000,
    `inline script body is only ${body.length} bytes -- it was almost certainly cut short by an embedded </script>`
  );
  return body;
}

function extractCspContent(html) {
  const m = html.match(
    /<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]*)"/i
  );
  assert.ok(m, "expected a Content-Security-Policy meta tag");
  return m[1];
}

test("CSP meta script-src hash matches the live inline <script> content", () => {
  const html = readAdminHtml();
  const body = extractInlineScriptBody(html);
  const hash = createHash("sha256").update(body, "utf8").digest("base64");
  const expectedToken = `'sha256-${hash}'`;

  const csp = extractCspContent(html);
  assert.ok(
    csp.includes(expectedToken),
    `CSP script-src does not contain ${expectedToken} (recomputed from the current inline script). ` +
      `If you just edited the <script> block, recompute the hash and update the CSP meta tag.`
  );
});

test("CSP script-src does not allow 'unsafe-inline' or a wildcard", () => {
  const html = readAdminHtml();
  const csp = extractCspContent(html);
  const scriptSrcMatch = csp.match(/script-src\s+([^;]*)/i);
  assert.ok(scriptSrcMatch, "expected a script-src directive");
  const scriptSrc = scriptSrcMatch[1];
  assert.ok(!scriptSrc.includes("unsafe-inline"), "script-src must not include 'unsafe-inline'");
  assert.ok(!scriptSrc.includes("*"), "script-src must not include a wildcard source");
  assert.ok(!/nonce-/.test(scriptSrc), "script-src should use the hash allow-list, not a nonce");
});
