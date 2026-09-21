// Unit tests for the NETLIFY_FORMS_TOKEN expiry tripwire and upstream
// auth-failure detection in netlify/functions/reports.js.
//
// Why this exists: the admin queue reads Netlify Forms with a Personal
// Access Token that CARRIES AN EXPIRY DATE. When it lapses, every Netlify
// API call 401s and the operator's only symptom used to be a generic
// "Error 502: netlify forms list failed" (or, if it lapsed mid-request, an
// opaque 500 "internal error") -- a failure that looks identical to a
// Netlify outage and tells you nothing about the actual cause.
//
// Two behaviours are locked here:
//   1. tokenExpiryWarning() warns BEFORE the lapse, off a date the operator
//      records in NETLIFY_FORMS_TOKEN_EXPIRES when they rotate.
//   2. isNetlifyAuthFailure() recognises a rejected token AFTER the fact so
//      the function can say so in plain words instead of a bare status code.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const reports = await import(
  path.join(__dirname, "..", "netlify", "functions", "reports.js")
);
const mod = reports.default ?? reports;

test("reports.js exposes the token-expiry helpers", () => {
  assert.equal(typeof mod.handler, "function", "exports.handler must remain a function");
  assert.equal(typeof mod._internal.tokenExpiryWarning, "function");
  assert.equal(typeof mod._internal.isNetlifyAuthFailure, "function");
  assert.equal(typeof mod._internal.TOKEN_WARN_DAYS, "number");
});

const { tokenExpiryWarning, isNetlifyAuthFailure, TOKEN_WARN_DAYS } = mod._internal;

const NOW = Date.parse("2026-09-20T12:00:00Z");
const days = (n) => new Date(NOW + n * 86400000).toISOString();

test("tokenExpiryWarning stays silent when the expiry is unset or unparseable", () => {
  // An operator who never sets the env var gets today's behaviour, not a
  // permanent false alarm. Garbage must not warn either -- a warning nobody
  // can act on trains you to ignore the real one.
  assert.equal(tokenExpiryWarning(undefined, NOW), null);
  assert.equal(tokenExpiryWarning(null, NOW), null);
  assert.equal(tokenExpiryWarning("", NOW), null);
  assert.equal(tokenExpiryWarning("   ", NOW), null);
  assert.equal(tokenExpiryWarning("not-a-date", NOW), null);
  assert.equal(tokenExpiryWarning("2026-13-45", NOW), null);
  assert.equal(tokenExpiryWarning(12345, NOW), null);
});

test("tokenExpiryWarning stays silent while the token is comfortably valid", () => {
  assert.equal(tokenExpiryWarning(days(365), NOW), null);
  assert.equal(tokenExpiryWarning(days(90), NOW), null);
  assert.equal(tokenExpiryWarning(days(TOKEN_WARN_DAYS + 1), NOW), null);
});

test("tokenExpiryWarning fires inside the warning window, before anything breaks", () => {
  const w = tokenExpiryWarning(days(10), NOW);
  assert.ok(w, "expected a warning 10 days out");
  assert.equal(w.expired, false);
  assert.equal(w.days, 10);
  assert.match(w.message, /NETLIFY_FORMS_TOKEN/);
  assert.match(w.message, /10 day/);

  // The boundary itself warns -- an off-by-one here costs a silent outage.
  const edge = tokenExpiryWarning(days(TOKEN_WARN_DAYS), NOW);
  assert.ok(edge, `expected a warning exactly ${TOKEN_WARN_DAYS} days out`);
  assert.equal(edge.expired, false);
});

test("tokenExpiryWarning reports an already-lapsed token as expired", () => {
  const w = tokenExpiryWarning(days(-3), NOW);
  assert.ok(w);
  assert.equal(w.expired, true);
  assert.match(w.message, /expired/i);
  assert.match(w.message, /3 day/);

  const today = tokenExpiryWarning(days(0), NOW);
  assert.ok(today, "an expiry of exactly now counts as expired, not as fine");
  assert.equal(today.expired, true);
});

test("isNetlifyAuthFailure distinguishes a rejected token from any other failure", () => {
  // 401/403 from api.netlify.com means the PAT is expired, revoked, or
  // belongs to an account that lost access to the site. Everything else is
  // Netlify having a bad day and must NOT be reported as a token problem --
  // sending the operator to rotate a healthy token wastes the one action
  // that would otherwise fix the real outage.
  assert.equal(isNetlifyAuthFailure(401), true);
  assert.equal(isNetlifyAuthFailure(403), true);
  assert.equal(isNetlifyAuthFailure(200), false);
  assert.equal(isNetlifyAuthFailure(404), false);
  assert.equal(isNetlifyAuthFailure(429), false);
  assert.equal(isNetlifyAuthFailure(500), false);
  assert.equal(isNetlifyAuthFailure(502), false);
  assert.equal(isNetlifyAuthFailure(undefined), false);
});

/* ---------------------------------------------------------------------
   Handler-level wiring. The helpers above can be perfect and still never be
   reached, which is exactly how the old behaviour went unnoticed: a token
   refused DURING the submissions fetch threw, hit the catch-all, and came
   back as 500 "internal error" -- indistinguishable from a bug in this file.
   These drive the real exported handler with a stubbed fetch.
   ------------------------------------------------------------------ */

function withEnv(vars, fn) {
  const saved = {};
  for (const k of Object.keys(vars)) {
    saved[k] = process.env[k];
    if (vars[k] === undefined) delete process.env[k];
    else process.env[k] = vars[k];
  }
  return (async () => {
    try { return await fn(); }
    finally {
      for (const k of Object.keys(saved)) {
        if (saved[k] === undefined) delete process.env[k];
        else process.env[k] = saved[k];
      }
    }
  })();
}

const AUTHED_EVENT = {
  httpMethod: "GET",
  headers: { "x-willifit-admin": "correct-horse", "x-nf-client-connection-ip": "203.0.113.9" },
};

const BASE_ENV = {
  WILLIFIT_ADMIN_PASSWORD: "correct-horse",
  NETLIFY_FORMS_TOKEN: "fake-pat",
  NETLIFY_SITE_ID: "site-123",
};

async function withFetch(impl, fn) {
  const real = globalThis.fetch;
  globalThis.fetch = impl;
  try { return await fn(); }
  finally { globalThis.fetch = real; }
}

const jsonRes = (status, payload) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => payload,
});

test("handler labels a token refused on the forms-list call", async () => {
  await withEnv({ ...BASE_ENV, NETLIFY_FORMS_TOKEN_EXPIRES: "2026-10-05" }, () =>
    withFetch(async () => jsonRes(401, {}), async () => {
      const res = await mod.handler(AUTHED_EVENT);
      assert.equal(res.statusCode, 502);
      const body = JSON.parse(res.body);
      assert.equal(body.code, "netlify_token_rejected");
      assert.equal(body.status, 401);
      assert.match(body.error, /NETLIFY_FORMS_TOKEN/);
      assert.match(body.error, /2026-10-05/, "the recorded expiry belongs in the message");
    })
  );
});

test("handler labels a token refused MID-request, instead of a blank 500", async () => {
  // The regression this locks: forms list succeeds, submissions 401s.
  await withEnv(BASE_ENV, () =>
    withFetch(async (url) => {
      if (String(url).includes("/forms/")) return jsonRes(403, {});
      return jsonRes(200, [{ id: "f1", name: "clearance-report" }]);
    }, async () => {
      const res = await mod.handler(AUTHED_EVENT);
      assert.equal(res.statusCode, 502, "must not collapse into the catch-all 500");
      const body = JSON.parse(res.body);
      assert.equal(body.code, "netlify_token_rejected");
      assert.equal(body.status, 403);
    })
  );
});

test("handler still reports non-auth upstream failures as themselves", async () => {
  await withEnv(BASE_ENV, () =>
    withFetch(async () => jsonRes(500, {}), async () => {
      const res = await mod.handler(AUTHED_EVENT);
      assert.equal(res.statusCode, 502);
      const body = JSON.parse(res.body);
      assert.notEqual(body.code, "netlify_token_rejected", "a Netlify outage is not a token problem");
      assert.equal(body.status, 500);
    })
  );
});

test("handler carries the expiry warning on a healthy response", async () => {
  const soon = new Date(Date.now() + 5 * 86400000).toISOString().slice(0, 10);
  await withEnv({ ...BASE_ENV, NETLIFY_FORMS_TOKEN_EXPIRES: soon }, () =>
    withFetch(async (url) => {
      if (String(url).includes("/forms/")) return jsonRes(200, []);
      return jsonRes(200, [{ id: "f1", name: "clearance-report" }]);
    }, async () => {
      const res = await mod.handler(AUTHED_EVENT);
      assert.equal(res.statusCode, 200);
      const body = JSON.parse(res.body);
      assert.ok(body.token_warning, "expected a warning 5 days out");
      assert.equal(body.token_warning.expired, false);
      assert.match(body.token_warning.message, /expires in/);
      assert.equal(body.total, 0, "the queue payload itself is unchanged");
    })
  );
});

test("handler sends no warning when the expiry is unset (unchanged default)", async () => {
  await withEnv({ ...BASE_ENV, NETLIFY_FORMS_TOKEN_EXPIRES: undefined }, () =>
    withFetch(async (url) => {
      if (String(url).includes("/forms/")) return jsonRes(200, []);
      return jsonRes(200, [{ id: "f1", name: "clearance-report" }]);
    }, async () => {
      const res = await mod.handler(AUTHED_EVENT);
      assert.equal(res.statusCode, 200);
      assert.equal(JSON.parse(res.body).token_warning, null);
    })
  );
});

test("the token-rejected message is only reachable behind the password gate", async () => {
  // It names env vars and the admin runbook. A wrong password must still get
  // the opaque 403 and learn nothing about how this function is configured.
  await withEnv(BASE_ENV, () =>
    withFetch(async () => jsonRes(401, {}), async () => {
      const res = await mod.handler({
        httpMethod: "GET",
        headers: { "x-willifit-admin": "wrong", "x-nf-client-connection-ip": "198.51.100.7" },
      });
      assert.equal(res.statusCode, 403);
      assert.equal(JSON.parse(res.body).error, "forbidden");
      assert.doesNotMatch(res.body, /NETLIFY_FORMS_TOKEN/);
    })
  );
});
