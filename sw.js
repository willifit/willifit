// WillIFit service worker — minimal offline shell cache.
//
// Strategy:
//   - Precache the app shell (HTML + index.json + favicon) on install
//   - For city JSON: stale-while-revalidate (use cache immediately, refresh
//     in the background so data stays fresh without blocking the UI)
//   - For map tiles and third-party CDNs: network-first, fall back to cache
//   - Nothing else is intercepted
//
// Bump CACHE_VERSION when you ship a breaking HTML change so returning users
// pick up the new shell.

const CACHE_VERSION = "willifit-v2";
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const DATA_CACHE  = `${CACHE_VERSION}-data`;

const SHELL_FILES = [
  "/willifit.html",
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
    // App shell (HTML, manifest, favicon) → cache-first
    event.respondWith(cacheFirst(req, SHELL_CACHE));
    return;
  }

  // Third-party tiles / CDNs → network-first, cache fallback
  if (
    url.hostname.endsWith("tile.openstreetmap.org") ||
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
