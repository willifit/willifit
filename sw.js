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
const CACHE_VERSION = "willifit-v14";
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const DATA_CACHE  = `${CACHE_VERSION}-data`;

const SHELL_FILES = [
  "/index.html",
  "/",
  "/favicon.svg",
  "/manifest.webmanifest",
  "/data/index.json",
  "/data/sponsors.json",
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
    url.hostname.endsWith("tile.openstreetmap.org") ||
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
