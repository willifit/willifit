// Unit tests for the numeric-coercion and IP-extraction helpers in
// netlify/functions/reports.js. These are the server-side half of closing
// the stored-XSS hole in admin.html's renderNewLocation(): location_lat/
// location_lng are attacker-controlled (submitted via the public
// new-location-report form) and must come out as a real finite number in
// range, or null -- never as a string carrying markup.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const reports = await import(
  path.join(__dirname, "..", "netlify", "functions", "reports.js")
);
const mod = reports.default ?? reports;

test("reports.js exposes _internal helpers alongside the unchanged handler export", () => {
  assert.equal(typeof mod.handler, "function", "exports.handler must remain a function");
  assert.ok(mod._internal, "exports._internal must exist");
  assert.equal(typeof mod._internal.numOrNull, "function");
  assert.equal(typeof mod._internal.floatOrNull, "function");
  assert.equal(typeof mod._internal.clientIp, "function");
});

const { numOrNull, floatOrNull, clientIp } = mod._internal;

test("floatOrNull rejects hostile / non-numeric lat-lng payloads", () => {
  assert.equal(floatOrNull('1"><img src=x onerror=alert(1)>', 90), null);
  assert.equal(floatOrNull("<script>alert(1)</script>", 180), null);
  assert.equal(floatOrNull("javascript:alert(1)", 180), null);
  assert.equal(floatOrNull(undefined, 90), null);
  assert.equal(floatOrNull(null, 90), null);
  assert.equal(floatOrNull("", 90), null);
  assert.equal(floatOrNull("NaN", 90), null);
  assert.equal(floatOrNull("Infinity", 90), null);
});

test("floatOrNull accepts valid in-range numbers (string or numeric)", () => {
  assert.equal(floatOrNull("36.1699", 90), 36.1699);
  assert.equal(floatOrNull(-115.1398, 180), -115.1398);
  assert.equal(floatOrNull("0", 90), 0);
  assert.equal(floatOrNull("90", 90), 90);
  assert.equal(floatOrNull("-180", 180), -180);
});

test("floatOrNull rejects out-of-range values", () => {
  assert.equal(floatOrNull("91", 90), null);
  assert.equal(floatOrNull("-91", 90), null);
  assert.equal(floatOrNull("181", 180), null);
  assert.equal(floatOrNull("-181", 180), null);
  assert.equal(floatOrNull("999999999999999999999", 180), null);
});

test("floatOrNull does not silently truncate a hostile payload to a leading number", () => {
  // parseFloat("1abc") === 1 -- that behavior would let an attacker-chosen
  // numeric prefix through while dropping the rest silently. Number()
  // must reject the whole string instead.
  const result = floatOrNull("1<script>", 180);
  assert.equal(result, null, "a payload with a numeric prefix must not coerce to that number");
});

test("numOrNull still parses plain integers and rejects garbage", () => {
  assert.equal(numOrNull("84"), 84);
  assert.equal(numOrNull(84), 84);
  assert.equal(numOrNull(""), null);
  assert.equal(numOrNull(null), null);
  assert.equal(numOrNull(undefined), null);
  assert.equal(numOrNull('84"><img src=x onerror=alert(1)>'), 84, "numOrNull uses parseInt and intentionally keeps a leading-integer prefix for height fields");
});

test("clientIp trusts only x-nf-client-connection-ip, never x-forwarded-for", () => {
  assert.equal(
    clientIp({ headers: { "x-forwarded-for": "1.2.3.4" } }),
    "unknown",
    "x-forwarded-for must never be used as a fallback -- it is attacker-controlled and would let a client spoof a fresh IP on every request to bypass rate limiting"
  );
  assert.equal(
    clientIp({ headers: { "x-nf-client-connection-ip": "5.6.7.8", "x-forwarded-for": "1.2.3.4" } }),
    "5.6.7.8"
  );
  assert.equal(clientIp({ headers: {} }), "unknown");
});

test("clientIp sanitizes and length-limits its output", () => {
  assert.equal(
    clientIp({ headers: { "x-nf-client-connection-ip": "1.2.3.4; rm -rf /" } }),
    "1.2.3.4__rm_-rf__"
  );
  const long = "a".repeat(200);
  assert.equal(clientIp({ headers: { "x-nf-client-connection-ip": long } }).length, 60);
});
