/**
 * Netlify Function: /.netlify/functions/reports
 *
 * Serves a password-gated list of user-submitted clearance reports from
 * Netlify Forms.  Used by /admin.html to show a queue of submissions the
 * site operator can review.
 *
 * Security model (intentionally simple):
 *   - A shared admin password is stored as an env var: WILLIFIT_ADMIN_PASSWORD
 *   - Client sends the password in the `x-willifit-admin` header
 *   - Function checks the header matches the env var, else 403
 *   - No sessions, no cookies, no tracking -- just "do you know the password"
 *
 * Why not OAuth / real auth?  One admin (you), no need for user management
 * or account recovery.  A strong env-var password over HTTPS is fine at
 * this scale.  If the project ever has multiple admins or untrusted users,
 * upgrade to Netlify Identity or auth0.
 *
 * Required environment variables (set in Netlify dashboard):
 *   - NETLIFY_FORMS_TOKEN        Personal Access Token from Netlify
 *                                (User Settings -> Applications ->
 *                                Personal Access Tokens -> New)
 *   - NETLIFY_SITE_ID            The deploy ID of this site (visible
 *                                under Site configuration -> General ->
 *                                Site information -> Site ID)
 *   - WILLIFIT_ADMIN_PASSWORD    Any strong password you pick
 *
 * How Netlify Forms API works (for reference):
 *   GET /api/v1/sites/:site_id/forms              -- list forms
 *   GET /api/v1/forms/:form_id/submissions        -- list submissions
 *   Authorization: Bearer {NETLIFY_FORMS_TOKEN}
 */

exports.handler = async (event) => {
  // CORS + method guard
  // Shared response headers.  no-store because a successful response carries
  // reporter PII (contact emails submitted with reports) — it must never come
  // to rest in a browser, proxy, or CDN cache.
  //
  // The CORS block below is decorative: /admin.html is served from this same
  // origin and fetches this function same-origin, so no preflight or
  // allow-origin check is ever exercised in the real flow.  It is kept only so
  // that a stray cross-origin XHR is refused by the browser rather than
  // silently allowed.  It is NOT an access control — the password is.
  const cors = {
    "access-control-allow-origin": "https://willifit.ai",
    "access-control-allow-headers": "content-type, x-willifit-admin",
    "access-control-allow-methods": "GET, OPTIONS",
    "cache-control": "no-store",
  };
  if (event.httpMethod === "OPTIONS") {
    return { statusCode: 204, headers: cors, body: "" };
  }
  if (event.httpMethod !== "GET") {
    return { statusCode: 405, headers: cors, body: "Method not allowed" };
  }

  // Password gate — use timing-safe comparison to avoid leaking match
  // length via timing attacks (overkill but cheap).
  // Note: Netlify always lowercases incoming header names before this
  // handler sees them, so only the lowercase form is ever populated — the
  // "X-Willifit-Admin" fallback that used to live here was dead code that
  // could never match anything and has been removed.
  const expected = process.env.WILLIFIT_ADMIN_PASSWORD;
  const supplied = event.headers["x-willifit-admin"] || "";

  // Rate-limit BEFORE evaluating the password, so a blocked client burns no
  // guesses at all.  Every limiter path fails open (see helpers below) — a
  // limiter outage must never lock the operator out of their own queue.
  const ip = clientIp(event);
  const store = await getBlobStore();
  const now = Date.now();
  const rec = normalizeRecord(await readRec(store, ip), now);

  if (rec.blockedUntil > now) {
    const retryAfter = Math.ceil((rec.blockedUntil - now) / 1000);
    return {
      statusCode: 429,
      headers: { ...cors, "retry-after": String(retryAfter) },
      body: JSON.stringify({ error: "too many attempts", retry_after_seconds: retryAfter }),
    };
  }

  // A missing WILLIFIT_ADMIN_PASSWORD is treated as "no one can ever be
  // right" rather than as its own early, pre-rate-limit 500.  Returning a
  // distinct "not configured" response before the rate limiter ran would
  // (a) tell an unauthenticated caller something about server config, and
  // (b) let them probe for that misconfiguration for free, outside the
  // failed-attempt counter.  Instead it falls into the same 403 path as a
  // wrong password, and the real reason is only visible server-side.
  if (!expected) {
    console.error("reports function: WILLIFIT_ADMIN_PASSWORD not configured");
  }
  if (!expected || !constantTimeEqual(supplied, expected)) {
    rec.fails += 1;
    if (rec.fails >= MAX_FAILS) rec.blockedUntil = now + BLOCK_MS;
    await writeRec(store, ip, rec);
    // Delay on top of the counter so even the first few guesses are slow.
    await new Promise((r) => setTimeout(r, 500));
    return {
      statusCode: 403,
      headers: { ...cors, "x-ratelimit-remaining": String(Math.max(0, MAX_FAILS - rec.fails)) },
      body: JSON.stringify({ error: "forbidden" }),
    };
  }

  // Correct password — clear this IP's failure record.
  await clearRec(store, ip);

  const token = process.env.NETLIFY_FORMS_TOKEN;
  const siteId = process.env.NETLIFY_SITE_ID;
  if (!token || !siteId) {
    return {
      statusCode: 500, headers: cors,
      body: JSON.stringify({ error: "NETLIFY_FORMS_TOKEN or NETLIFY_SITE_ID missing" }),
    };
  }

  try {
    // 1. List all forms on the site, then find the two we care about.
    //    "clearance-report"    = user corrections to existing entries
    //    "new-location-report" = user submissions of garages not on the map
    const formsRes = await fetch(
      `https://api.netlify.com/api/v1/sites/${siteId}/forms`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    if (!formsRes.ok) {
      return {
        statusCode: 502, headers: cors,
        body: JSON.stringify({ error: "netlify forms list failed", status: formsRes.status }),
      };
    }
    const forms = await formsRes.json();
    const clearanceForm = forms.find((f) => f.name === "clearance-report");
    const newLocationForm = forms.find((f) => f.name === "new-location-report");
    const issueForm = forms.find((f) => f.name === "location-issue-report");

    // 2. Fetch submissions for each form that exists.
    async function fetchSubmissions(form) {
      if (!form) return [];
      const res = await fetch(
        `https://api.netlify.com/api/v1/forms/${form.id}/submissions?per_page=200`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) throw new Error(`netlify submissions failed (${form.name}): ${res.status}`);
      return res.json();
    }

    const [clearanceSubs, newLocationSubs, issueSubs] = await Promise.all([
      fetchSubmissions(clearanceForm),
      fetchSubmissions(newLocationForm),
      fetchSubmissions(issueForm),
    ]);

    // 3. Massage into client-friendly shapes.  `kind` lets the admin UI
    //    route each submission to the right template without sniffing fields.
    const reports = clearanceSubs.map((s) => {
      const d = s.data || {};
      return {
        kind: "clearance-report",
        id: s.id,
        created_at: s.created_at,
        state: s.state, // 'verified' | 'spam' | 'unknown'
        garage_id: d.garage_id,
        garage_name: d.garage_name,
        garage_addr: d.garage_addr,
        city_slug: d.city_slug,
        city_name: d.city_name,
        city_state: d.city_state,
        previous_height_in: numOrNull(d.previous_height_in),
        previous_height_label: d.previous_height_label,
        reported_height_in: numOrNull(d.reported_height_in),
        no_posted_sign: d.no_posted_sign === "true" || d.no_posted_sign === true,
        oversized_available: d.oversized_available,
        notes: d.notes,
        contact: d.contact,
      };
    });

    const new_locations = newLocationSubs.map((s) => {
      const d = s.data || {};
      return {
        kind: "new-location-report",
        id: s.id,
        created_at: s.created_at,
        state: s.state,
        city_slug: d.city_slug,
        city_name: d.city_name,
        city_state: d.city_state,
        location_name: d.location_name,
        location_type: d.location_type,
        location_addr: d.location_addr,
        location_lat: floatOrNull(d.location_lat, 90),
        location_lng: floatOrNull(d.location_lng, 180),
        reported_height_in: numOrNull(d.reported_height_in),
        no_posted_sign: d.no_posted_sign === "true" || d.no_posted_sign === true,
        oversized: d.oversized,
        notes: d.notes,
        contact: d.contact,
      };
    });

    const issues = issueSubs.map((s) => {
      const d = s.data || {};
      return {
        kind: "location-issue-report",
        id: s.id,
        created_at: s.created_at,
        state: s.state,
        garage_id: d.garage_id,
        garage_name: d.garage_name,
        garage_addr: d.garage_addr,
        city_slug: d.city_slug,
        city_name: d.city_name,
        city_state: d.city_state,
        issue_type: d.issue_type,
        correction: d.correction,
        notes: d.notes,
        contact: d.contact,
      };
    });

    return {
      statusCode: 200,
      headers: { ...cors, "content-type": "application/json" },
      body: JSON.stringify({
        reports,
        new_locations,
        issues,
        total: reports.length + new_locations.length + issues.length,
      }),
    };
  } catch (err) {
    console.error("reports function error:", err);
    return {
      statusCode: 500, headers: cors,
      body: JSON.stringify({ error: "internal error" }),
    };
  }
};

function numOrNull(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : null;
}

// Coerces a form-submitted lat/lng value to a finite number, rejecting
// anything that isn't purely numeric (Number(), not parseFloat() — a
// payload like `1"><img src=x onerror=alert(1)>` must come out as null,
// not as the number 1 with the rest silently dropped) and anything outside
// the given absolute-value bound (90 for latitude, 180 for longitude).
// This is the server-side half of closing the stored-XSS hole in
// admin.html's renderNewLocation(): these fields are attacker-controlled
// (submitted via the public new-location-report form) and used to build a
// Google Maps URL that lands in an `href` attribute, so anything that isn't
// a plain number must never reach the client as a string.
function floatOrNull(v, maxAbs) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  if (typeof maxAbs === "number" && Math.abs(n) > maxAbs) return null;
  return n;
}

/* ---------------------------------------------------------------------
   Brute-force rate limiting.  Two tiers, both best-effort, both fail-open:

     1. Netlify Blobs — shared across containers, so a distributed attack is
        genuinely capped.  Imported under a VARIABLE module name so the
        bundler cannot statically resolve it and fail the build on this
        deliberately dependency-free (no package.json) site.
     2. Per-container memory — only partial coverage since functions scale
        horizontally, but it is free and always available.

   If both are unavailable the endpoint behaves exactly as it did before and
   the password remains the real control.  That is the intended trade: a
   limiter that silently degrades beats one that can lock you out.
   ------------------------------------------------------------------ */
const MAX_FAILS = 8;                  // wrong guesses allowed per window
const WINDOW_MS = 15 * 60 * 1000;     // rolling window
const BLOCK_MS = 15 * 60 * 1000;      // lockout once the window is spent

const memHits = new Map();

// x-nf-client-connection-ip is set by Netlify's edge from the actual TCP
// connection and cannot be spoofed by the client. x-forwarded-for, by
// contrast, is just another request header — a caller can put anything in
// it — so it is deliberately NOT used as a fallback here even though it is
// often present. Trusting it would let a single attacker present a fresh
// "IP" on every request and walk straight around the rate limiter.
function clientIp(event) {
  const raw = event.headers["x-nf-client-connection-ip"] || "";
  return (raw.trim() || "unknown").replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 60);
}

async function getBlobStore() {
  try {
    const mod = "@netlify/blobs";      // variable name defeats static bundling
    const { getStore } = await import(mod);
    return getStore("admin-rate-limit");
  } catch (e) {
    return null;                       // Blobs unavailable — tier 2 only
  }
}

function normalizeRecord(rec, now) {
  if (!rec || typeof rec !== "object") return { fails: 0, first: now, blockedUntil: 0 };
  const out = {
    fails: Number(rec.fails) || 0,
    first: Number(rec.first) || now,
    blockedUntil: Number(rec.blockedUntil) || 0,
  };
  if (out.blockedUntil > now) return out;                       // block in force
  if (now - out.first > WINDOW_MS) return { fails: 0, first: now, blockedUntil: 0 };
  return out;
}

async function readRec(store, ip) {
  if (store) {
    try {
      const v = await store.get(ip, { type: "json" });
      if (v) return v;
    } catch (e) { /* fall through to memory */ }
  }
  return memHits.get(ip) || null;
}

async function writeRec(store, ip, rec) {
  memHits.set(ip, rec);                // local copy always
  if (store) {
    try { await store.setJSON(ip, rec); } catch (e) { /* best effort */ }
  }
}

async function clearRec(store, ip) {
  memHits.delete(ip);
  if (store) {
    try { await store.delete(ip); } catch (e) { /* best effort */ }
  }
}

const crypto = require("crypto");

// Compare via fixed-width SHA-256 digests.  The previous implementation
// returned early when the lengths differed, which leaks the password's
// LENGTH through response timing.  Hashing first makes every comparison
// exactly 32 bytes wide, so timing reveals nothing about the secret.
function constantTimeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const ha = crypto.createHash("sha256").update(a, "utf8").digest();
  const hb = crypto.createHash("sha256").update(b, "utf8").digest();
  return crypto.timingSafeEqual(ha, hb);
}

// Exposed only for unit tests (tests/test_reports_coercion.mjs). Does not
// change the Netlify handler export/contract above.
exports._internal = { numOrNull, floatOrNull, clientIp, constantTimeEqual };
