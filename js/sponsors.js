/* WillIFit — shared sponsor targeting + rendering.
 *
 * Used by:
 *   - per-city landing pages at /city/<slug>.html   (one city_hero slot)
 *   - the main app index.html                       (currently uses its own
 *                                                    inline copy; migrating
 *                                                    to this file is a
 *                                                    follow-on task)
 *
 * Loaded from /data/sponsors.json.  Scoped by city and (optional) lat/lng
 * radius around a specific garage.  Audience filters respect user intent
 * (e.g. only show RV-park ads to users with the oversized toggle on).
 * Impressions & clicks recorded in localStorage for v1 reporting.
 *
 * Public API (attached to window.WillIFitSponsors):
 *   load()                       -> Promise<Array> of sponsor records
 *   pickSponsor(slot, ctx)       -> one record, or null
 *   renderSponsor(slot, ctx, opts) -> HTML string, or ""
 *   renderInto(el, slot, ctx, opts) -> awaits load, sets el.innerHTML,
 *                                     wires the click-tracking listener
 */
(function () {
  'use strict';

  const SPONSORS_URL = '/data/sponsors.json';
  let SPONSORS = [];
  let _loadPromise = null;

  function escapeHTML(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function load() {
    if (_loadPromise) return _loadPromise;
    _loadPromise = fetch(SPONSORS_URL)
      .then((r) => (r.ok ? r.json() : []))
      .then((arr) => {
        SPONSORS = Array.isArray(arr) ? arr : [];
        return SPONSORS;
      })
      .catch(() => {
        SPONSORS = [];
        return SPONSORS;
      });
    return _loadPromise;
  }

  function _haversineMi(aLat, aLng, bLat, bLng) {
    const toR = (d) => (d * Math.PI) / 180;
    const R = 3958.8; // earth radius in miles
    const dLat = toR(bLat - aLat);
    const dLng = toR(bLng - aLng);
    const s =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toR(aLat)) * Math.cos(toR(bLat)) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(s));
  }

  function pickSponsor(slot, ctx) {
    if (!SPONSORS.length) return null;
    ctx = ctx || {};
    const candidates = SPONSORS.filter((s) => {
      if (!s.tier || !s.tier.includes(slot)) return false;
      const cities = (s.scope && s.scope.cities) || ['*'];
      if (!cities.includes('*') && (!ctx.city || !cities.includes(ctx.city))) return false;
      if (s.audience && s.audience.oversized_only && !ctx.oversized_filter) return false;
      if (
        s.audience &&
        s.audience.min_height_in != null &&
        ctx.min_height_in != null &&
        ctx.min_height_in < s.audience.min_height_in
      )
        return false;
      if (s.scope && s.scope.near && ctx.garage) {
        const d = _haversineMi(
          s.scope.near.lat,
          s.scope.near.lng,
          ctx.garage.lat,
          ctx.garage.lng
        );
        if (d > (s.scope.near.radius_mi || 20)) return false;
      }
      return true;
    });
    if (!candidates.length) return null;
    if (ctx.garage) {
      candidates.sort((a, b) => {
        const da =
          a.scope && a.scope.near
            ? _haversineMi(a.scope.near.lat, a.scope.near.lng, ctx.garage.lat, ctx.garage.lng)
            : 9999;
        const db =
          b.scope && b.scope.near
            ? _haversineMi(b.scope.near.lat, b.scope.near.lng, ctx.garage.lat, ctx.garage.lng)
            : 9999;
        return da - db;
      });
    }
    const pool = ctx.garage ? candidates.slice(0, 3) : candidates;
    const total = pool.reduce((a, s) => a + (s.weight || 1), 0);
    let r = Math.random() * total;
    for (const s of pool) {
      r -= s.weight || 1;
      if (r <= 0) return s;
    }
    return pool[0];
  }

  function _adEventKey(kind) {
    return `willifit_ad_${kind}`;
  }

  function recordAdEvent(kind, sponsorId, slot, city) {
    try {
      const key = _adEventKey(kind);
      const log = JSON.parse(localStorage.getItem(key) || '[]');
      log.push({ id: sponsorId, slot: slot, ts: Date.now(), city: city || null });
      if (log.length > 500) log.splice(0, log.length - 500); // keep last 500
      localStorage.setItem(key, JSON.stringify(log));
    } catch (e) {
      /* localStorage disabled / quota full — swallow */
    }
  }

  function renderSponsor(slot, ctx, opts) {
    const s = pickSponsor(slot, ctx);
    if (!s) return '';
    recordAdEvent('impression', s.id, slot, ctx && ctx.city);
    const compact = opts && opts.compact;
    const isHouse = (s.id || '').startsWith('house-');
    const isExternal = /^https?:\/\//.test(s.url);
    const linkAttrs = isExternal
      ? `href="${s.url}" target="_blank" rel="noopener sponsored"`
      : `href="${s.url}"`;
    return `
      <div class="sponsor-card ${compact ? 'compact' : ''} ${isHouse ? 'house' : ''}" data-slot="${slot}" data-id="${escapeHTML(s.id)}">
        <div class="sponsor-label">${escapeHTML(s.label || 'Sponsored')}</div>
        <div class="sponsor-title">${escapeHTML(s.title)}</div>
        ${compact ? '' : `<div class="sponsor-desc">${escapeHTML(s.desc)}</div>`}
        <a class="sponsor-cta" ${linkAttrs} data-sponsor-click="${escapeHTML(s.id)}" data-sponsor-slot="${escapeHTML(slot)}">${escapeHTML(s.cta)}</a>
      </div>`;
  }

  async function renderInto(el, slot, ctx, opts) {
    await load();
    if (!el) return;
    const html = renderSponsor(slot, ctx, opts);
    if (!html) return;
    el.innerHTML = html;
    // Attach click tracker to the CTA.  Done here (not via inline onclick)
    // so this module works under strict CSP when callers adopt it.
    const cta = el.querySelector('.sponsor-cta[data-sponsor-click]');
    if (cta) {
      cta.addEventListener('click', function () {
        recordAdEvent(
          'click',
          cta.dataset.sponsorClick,
          cta.dataset.sponsorSlot,
          ctx && ctx.city
        );
      });
    }
  }

  window.WillIFitSponsors = {
    load: load,
    pickSponsor: pickSponsor,
    renderSponsor: renderSponsor,
    renderInto: renderInto,
    recordAdEvent: recordAdEvent,
  };
})();
