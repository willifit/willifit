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

// v6: forces clients to re-fetch HTML after the verified-badge / aerial-fallback
// improvements (commits 5da1adf and earlier).  Without bumping, returning visitors
// keep seeing the old cached index.html and miss the new "Verified" badges.
const CACHE_VERSION = "willifit-v6";
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
    url.hostname === "server.arcgisonline.com" ||  // Esri World Imagery (satellite layer)
    url.hostname === "unpkg.com"
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
    if (fresh.ok) cache.put(req, fresh.clone());
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
    if (fresh.ok) cache.put(req, fresh.clone());
    return fresh;
  }).catch(() => null);
  return cached || (await fetchPromise) || Response.error();
}
