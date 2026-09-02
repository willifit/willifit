// Willifit service worker — minimal offline shell cache.
//
// Strategy:
//   - Precache the app shell on install (for offline-first cold starts)
//   - For HTML: NETWORK-FIRST with cache fallback.  This means users always
//     get the latest deploy on each visit (no more "stuck on old HTML until
//     hard refresh" bugs), while still loading offline from cache when the
//     network is unreachable.  Previous versions used cache-first here,
//     which caused returning users to run stale JS for days.
//   - For city JSON: stale-while-revalidate (use cache immediately, refresh
//     in the background so data stays fresh without blocking the UI)
//   - For map tiles and third-party CDNs: network-first, fall back to cache
//   - Nothing else is intercepted
//
// Bump CACHE_VERSION when you ship a breaking change to the precached shell.

// v13: completes the share-card checklist for FB/iMessage/Slack/LinkedIn/
// Twitter/Discord/Telegram/WhatsApp.  Added og:locale=en_US to the
// homepage <head> (FB recommends it, AI agents read it for
// regionalisation), plus 11 new explicit Allow blocks in robots.txt
// for Slackbot, Slackbot-LinkExpanding, WhatsApp, Discordbot,
// TelegramBot, Applebot (plain, in addition to existing
// Applebot-Extended), SkypeUriPreview, Embedly, redditbot, Pinterest.
// All 11 follow the same pattern as the existing facebookexternalhit
// block -- needed because each crawler does strict UA matching and
// ignores the "User-agent: *" wildcard.
//
// v14: bug-fix sweep -- escape garage name/label in map popups + the report
// modal (defensive XSS hardening), fix the clearance-report form field name
// (id -> report_id) and add its honeypot, and guard the caches against being
// poisoned by a 200 HTML maintenance/error page (content-type checks below).
// Also an accessibility modal pass: every dialog overlay (city picker, report,
// issue, new-location) plus the welcome screen now has a focus trap,
// Escape-to-close, and focus restoration to the element that opened it.
//
// v15: cookie consent.  /js/consent.js is a render-blocking <head> script that
// carries the site's Consent Mode policy, so it must never be served stale.
// Under the generic same-origin rule below it would have landed in
// cache-first alongside the favicon and been pinned until the next
// CACHE_VERSION bump -- meaning a visitor whose browser cached the file the
// day it shipped could keep running a superseded consent policy for months,
// which is the same class of bug the v13->v14 note above describes for HTML.
// It now gets its own network-first route (fresh whenever online, cached copy
// only as an offline fallback).  The version bump is what purges any copy
// that a v14 service worker already cache-first'ed during the window between
// the HTML rollout and this file shipping.
//
// v16 (W1 fix): SHELL_FILES was missing /vendor/leaflet/leaflet.js and
// leaflet.css entirely -- a first-time visitor whose install precache ran
// would have every same-origin asset EXCEPT the map library cached, so
// going offline and reloading threw "L is not defined" and the whole app
// failed to boot. Added Leaflet + the new Leaflet.markercluster vendor
// files (U4) here. Version bumped so returning visitors' old v15 caches
// (which never had these files and can't retroactively gain them) get
// evicted in `activate` instead of serving a shell that's permanently
// missing the map.
const CACHE_VERSION = "willifit-v16";
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const DATA_CACHE  = `${CACHE_VERSION}-data`;

const SHELL_FILES = [
  "/index.html",
  "/",
  "/favicon.svg",
  "/manifest.webmanifest",
  "/data/index.json",
  "/data/sponsors.json",
  // Precached here too (in addition to its own network-first fetch route
  // below) purely to seed an offline fallback copy at install time -- a
  // visitor whose very first visit is offline would otherwise have no
  // cached copy at all, since networkFirst() only populates the cache
  // after a successful online fetch. Runtime freshness is unaffected: the
  // fetch handler's dedicated network-first route for this path still wins
  // on every online load.
  "/js/consent.js",
  "/vendor/leaflet/leaflet.js",
  "/vendor/leaflet/leaflet.css",
  "/vendor/leaflet.markercluster/leaflet.markercluster.js",
  "/vendor/leaflet.markercluster/MarkerCluster.css",
  "/vendor/leaflet.markercluster/MarkerCluster.Default.css",
];

// ---- install: precache the shell ----------------------------------------
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) =>
      // addAll fails atomically — if one file errors (e.g. index.html missing
      // during dev), we swallow it and precache best-effort.
      Promise.all(SHELL_FILES.map((url) =>
        cache.add(url).catch(() => null)
      ))
    ).then(() => self.skipWaiting())
  );
});

// ---- activate: drop old caches ------------------------------------------
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => !k.startsWith(CACHE_VERSION))
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ---- fetch router -------------------------------------------------------
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Same-origin: app shell + city JSON
  if (url.origin === self.location.origin) {
    // City JSON → stale-while-revalidate
    if (url.pathname.startsWith("/data/cities/")) {
      event.respondWith(staleWhileRevalidate(req, DATA_CACHE));
      return;
    }
    // Consent engine → network-first.  Deliberately carved out of the
    // cache-first catch-all further down: this file decides what Google is
    // allowed to store, and a stale copy would silently keep enforcing an
    // old policy long after the current one shipped.  Correctness beats the
    // few milliseconds cache-first would save.  Cache fallback is retained so
    // the script still loads offline (without it, an offline page load would
    // drop the Consent Mode defaults entirely).
    if (url.pathname === "/js/consent.js") {
      event.respondWith(networkFirst(req, SHELL_CACHE));
      return;
    }
    // HTML navigations → network-first (cache fallback for offline)
    const isHtml =
      req.mode === "navigate" ||
      url.pathname === "/" ||
      url.pathname.endsWith(".html");
    if (isHtml) {
      event.respondWith(networkFirst(req, SHELL_CACHE));
      return;
    }
    // Everything else same-origin (favicon, manifest, /data/index.json,
    // /data/sponsors.json, etc.) → cache-first for speed
    event.respondWith(cacheFirst(req, SHELL_CACHE));
    return;
  }

  // Third-party tiles / CDNs → network-first, cache fallback
  if (
    url.hostname.endsWith("basemaps.cartocdn.com") ||
    url.hostname === "server.arcgisonline.com"  // Esri World Imagery (satellite layer)
  ) {
    event.respondWith(networkFirst(req, DATA_CACHE));
    return;
  }

  // Everything else — pass through
});

// ---- strategies ---------------------------------------------------------
async function cacheFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  if (cached) return cached;
  try {
    const fresh = await fetch(req);
    // Don't cache an HTML error/maintenance page in place of a static asset.
    if (fresh.ok && !(fresh.headers.get("content-type") || "").includes("text/html")) {
      cache.put(req, fresh.clone());
    }
    return fresh;
  } catch (e) {
    return cached || Response.error();
  }
}

async function networkFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const fresh = await fetch(req);
    if (fresh.ok) cache.put(req, fresh.clone());
    return fresh;
  } catch (e) {
    const cached = await cache.match(req);
    return cached || Response.error();
  }
}

async function staleWhileRevalidate(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  const fetchPromise = fetch(req).then((fresh) => {
    // Only cache real JSON -- guards against a 200 HTML maintenance/error
    // page being poisoned into DATA_CACHE and served as city data forever.
    if (fresh.ok && (fresh.headers.get("content-type") || "").includes("json")) {
      cache.put(req, fresh.clone());
    }
    return fresh;
  }).catch(() => null);
  return cached || (await fetchPromise) || Response.error();
}
