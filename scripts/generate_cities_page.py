#!/usr/bin/env python3
"""Generate /cities.html -- the full city directory grouped by state.

Problem this solves:
  cities.html used to be hand-maintained.  Per-city counts drifted stale
  (e.g. Las Vegas showed "(147)" against 204 real locations) and the page's
  own headline claim ("25,000+ locations") went stale the moment the August
  cross-city dedupe landed (the corpus holds 23,472 entries, not 25,000+).

Fix:
  Render the whole page from data/index.json + data/cities/*.json every
  time, the same way city/state/parking pages already are.  Cities with no
  data file or zero locations are omitted -- they're noindexed on their own
  page, so they don't belong in the directory either.

Run any time data changes:
    python3 scripts/generate_cities_page.py
"""
from __future__ import annotations

import json
from pathlib import Path

from wf_common import STATE_NAMES, compose_description
from generate_city_pages import esc, safe_jsonld

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"
OUT_PATH = REPO_ROOT / "cities.html"

SITE = "https://willifit.ai"

# The page's own head/consent/analytics/CSS is copied verbatim from the
# hand-authored cities.html this generator replaces -- none of it depends
# on data, so it lives here as a plain string (no .format()/f-string
# brace-escaping needed) rather than being rebuilt every render().
CONSENT_ANALYTICS = """<script src="/js/consent.js"></script>
<!-- Cloudflare Web Analytics -->
<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon='{"token": "162b93f801fa42499a0b840c50d3f772"}'></script>
<!-- End Cloudflare Web Analytics -->
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-SH191K5NGS"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-SH191K5NGS');
</script>
<!-- End Google tag -->"""

CSS = """<style>
  :root {
    --bg:#0e1116; --panel:#171b23; --panel-2:#1d2330; --text:#e6eaf0;
    --muted:#8a95a6; --border:#2a3140; --accent:#0ea5e9; --ok:#3ecf8e;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         line-height:1.5; }
  a { color:var(--accent); text-decoration:none; }
  a:hover { text-decoration:underline; }
  main a { text-decoration: underline; text-underline-offset: 2px; text-decoration-color: rgba(14,165,233,0.4); }
  .page { max-width:1100px; margin:0 auto; padding:36px 24px 80px; }
  header { border-bottom:1px solid var(--border); padding-bottom:20px; margin-bottom:28px;
           display:flex; flex-wrap:wrap; gap:8px; align-items:baseline; }
  header .brand { font-weight:800; color:var(--text); letter-spacing:-0.01em; font-size:16px; }
  header .brand .tld { color:var(--accent); }
  header .crumb { color:var(--muted); font-size:14px; }
  h1 { margin:12px 0 8px; font-size:30px; letter-spacing:-0.02em; }
  .lede { color:var(--muted); font-size:15px; max-width:640px; }
  .stats { display:flex; flex-wrap:wrap; gap:8px; margin:16px 0 4px; font-size:12px; color:var(--muted); }
  .stat { padding:4px 10px; background:var(--panel); border:1px solid var(--border);
          border-radius:999px; font-family:'SF Mono',monospace; }
  .stat b { color:var(--text); font-weight:700; }
  .toc { margin:20px 0 30px; padding:14px 16px; background:var(--panel); border:1px solid var(--border);
          border-radius:8px; font-size:13px; line-height:2; }
  .toc a { margin-right:2px; }
  .state-block { margin-bottom:28px; }
  .state-block h2 { font-size:18px; letter-spacing:-0.01em; color:var(--text);
                    margin:0 0 10px; padding-bottom:6px; border-bottom:1px solid var(--border); }
  ul.city-list { list-style:none; padding:0; margin:0;
                  display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:4px 20px; }
  ul.city-list li { padding:3px 0; font-size:14px; }
  ul.city-list a { display:inline-block; padding:4px 2px; line-height:1.4; }
  @media (max-width: 600px) { ul.city-list a { padding:13px 2px; } }
  ul.city-list .count { color:var(--muted); font-family:'SF Mono',monospace; font-size:12px; }
  .skip-link {
    position: absolute; top: -40px; left: 0;
    background: #3ecf8e; color: #0a0f18;
    padding: 8px 12px; z-index: 9999;
    font-family: 'SF Mono',monospace; font-size: 12px;
    text-decoration: none; font-weight: 700;
  }
  .skip-link:focus { top: 0; }
  footer { margin-top:40px; padding-top:20px; border-top:1px solid var(--border);
           font-size:12px; color:var(--muted); display:flex; justify-content:space-between;
           flex-wrap:wrap; gap:10px; }
  footer a { color:var(--muted); } footer a:hover { color:var(--accent); }
</style>"""

HEADER = """  <header>
    <a href="/" class="brand">Will<span class="tld">I</span>Fit<span class="tld">.ai</span></a>
    <span class="crumb">›</span>
    <span class="crumb">Cities</span>
  </header>"""

LEDE = ('  <p class="lede">Parking-garage, tunnel, and low-bridge clearance heights — including '
        'AI-verified readings, grouped by state. Each state also has an overview page with its '
        'lowest bridges and garages.\n  Click a city to see its full list, or '
        '<a href="/">open the interactive map</a>.</p>')

TOC = """  <nav class="toc" aria-label="Jump to state">
    Jump: <a href="#al">Alabama</a> · <a href="#ak">Alaska</a> · <a href="#az">Arizona</a> · <a href="#ar">Arkansas</a> · <a href="#ca">California</a> · <a href="#co">Colorado</a> · <a href="#ct">Connecticut</a> · <a href="#de">Delaware</a> · <a href="#dc">District of Columbia</a> · <a href="#fl">Florida</a> · <a href="#ga">Georgia</a> · <a href="#hi">Hawaii</a> · <a href="#id">Idaho</a> · <a href="#il">Illinois</a> · <a href="#in">Indiana</a> · <a href="#ia">Iowa</a> · <a href="#ks">Kansas</a> · <a href="#ky">Kentucky</a> · <a href="#la">Louisiana</a> · <a href="#me">Maine</a> · <a href="#md">Maryland</a> · <a href="#ma">Massachusetts</a> · <a href="#mi">Michigan</a> · <a href="#mn">Minnesota</a> · <a href="#ms">Mississippi</a> · <a href="#mo">Missouri</a> · <a href="#mt">Montana</a> · <a href="#ne">Nebraska</a> · <a href="#nv">Nevada</a> · <a href="#nh">New Hampshire</a> · <a href="#nj">New Jersey</a> · <a href="#nm">New Mexico</a> · <a href="#ny">New York</a> · <a href="#nc">North Carolina</a> · <a href="#nd">North Dakota</a> · <a href="#oh">Ohio</a> · <a href="#ok">Oklahoma</a> · <a href="#or">Oregon</a> · <a href="#pa">Pennsylvania</a> · <a href="#pr">Puerto Rico</a> · <a href="#ri">Rhode Island</a> · <a href="#sc">South Carolina</a> · <a href="#sd">South Dakota</a> · <a href="#tn">Tennessee</a> · <a href="#tx">Texas</a> · <a href="#ut">Utah</a> · <a href="#vt">Vermont</a> · <a href="#va">Virginia</a> · <a href="#wa">Washington</a> · <a href="#wv">West Virginia</a> · <a href="#wi">Wisconsin</a> · <a href="#wy">Wyoming</a>
  </nav>"""

FOOTER = """  <footer>
    <div>© 2026 WillIFit.ai — clearance data for RVs, trucks &amp; oversized vehicles.</div>
    <div>
      <a href="/about.html">About</a> ·
      <a href="/accessibility.html">Accessibility</a> ·
      <a href="/how-ai-verification-works.html">How AI verification works</a> ·
      <a href="/parking-garage-clearance-heights.html">Clearance guide</a> ·
      <a href="/vehicle-heights.html">Vehicle heights</a> ·
      <a href="/lowest-bridges-in-america.html">Lowest bridges</a> ·
      <a href="/advertise.html">Advertise</a> ·
      <a href="/disclaimer.html">Disclaimer</a> ·
      <a href="/terms.html">Terms</a> ·
      <a href="/privacy.html">Privacy</a> ·
      <a href="/dmca.html">DMCA</a> ·
      <a href="/cookies.html">Cookies</a> ·
      <button type="button" data-wf-consent-open class="wf-consent-btn">Cookie preferences</button> ·
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">© OpenStreetMap</a> ·
      <a href="https://www.fhwa.dot.gov/bridge/nbi/" target="_blank" rel="noopener">FHWA NBI</a>
    </div>
  </footer>"""


def _live_cities_by_state() -> dict:
    """State code -> [(name, slug, total_locations), ...] for every live
    city that has a data file and at least one location.  Matches the
    noindex rule generate_city_pages.py applies to 0-location cities."""
    idx = json.loads(INDEX_PATH.read_text())
    live = [c for c in idx if c.get("status") == "live"]
    by_state = {}
    for c in live:
        p = CITIES_DIR / f"{c['slug']}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        total = (len(d.get("garages") or []) + len(d.get("tunnels") or [])
                 + len(d.get("bridges") or []))
        if total == 0:
            continue
        by_state.setdefault(c["state"], []).append((c["name"], c["slug"], total))
    return by_state


def render_state_blocks(by_state: dict) -> str:
    blocks = []
    for code in sorted(by_state, key=lambda xx: STATE_NAMES.get(xx, xx)):
        cities = sorted(by_state[code], key=lambda t: t[0])
        xx = code.lower()
        state_full = STATE_NAMES.get(code, code)
        items = "\n".join(
            f'      <li><a href="/city/{esc(slug)}">{esc(name)}</a> <span class="count">({total})</span></li>'
            for name, slug, total in cities
        )
        blocks.append(
            f'  <section class="state-block">\n'
            f'    <h2 id="{xx}"><a href="/state/{xx}">{esc(state_full)}</a> ({len(cities)})</h2>\n'
            f'    <ul class="city-list">\n'
            f'{items}\n'
            f'    </ul>\n'
            f'  </section>'
        )
    return "\n".join(blocks)


def render() -> str:
    by_state = _live_cities_by_state()
    n_cities = sum(len(v) for v in by_state.values())
    total = sum(t for v in by_state.values() for _, _, t in v)
    codes = set(by_state)
    n_states = len(codes)
    if "DC" in codes and "PR" in codes:
        states_stat = f'<b>{n_states - 2}</b> states + DC + PR'
    else:
        states_stat = f'<b>{n_states}</b> states'

    # Two sentences (not one 161-char run-on) so compose_description has a
    # trailing clause it can drop instead of clipping mid-word once the
    # corpus total reaches five digits (GC15: never end mid-sentence).
    description = compose_description(
        [f"All {n_cities} US cities with parking-garage, tunnel, and low-bridge clearance heights.",
         f"{total:,} locations for RV, truck, and oversized-vehicle drivers, grouped by state."], 160)
    og_description = (f"{n_cities} US cities, {total:,} parking/tunnel/bridge clearance "
                       f"heights — including AI-verified readings.")
    twitter_description = f"{n_cities} US cities, {total:,} locations."
    collection_description = (f"Parking clearance data for {n_cities} US cities — {total:,} "
                               f"garages, tunnels, and low-clearance bridges, including "
                               f"AI-verified readings.")

    jsonld = safe_jsonld([
        {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": "WillIFit.ai — all covered US cities",
            "url": f"{SITE}/cities.html",
            "description": collection_description,
            "dateModified": "2026-09-10",
            "isPartOf": {"@type": "WebSite", "name": "WillIFit.ai", "url": f"{SITE}/"},
        },
        {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{SITE}/"},
                {"@type": "ListItem", "position": 2, "name": "Cities", "item": f"{SITE}/cities.html"},
            ],
        },
    ])

    state_blocks = render_state_blocks(by_state)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0e1116">
<meta name="color-scheme" content="dark">
<title>Covered US Cities by State: Clearance Heights | WillIFit.ai</title>
<meta name="description" content="{esc(description)}">
<meta name="robots" content="index,follow">
<link rel="canonical" href="{SITE}/cities.html">
<meta property="og:title" content="All covered US cities — WillIFit.ai">
<meta property="og:description" content="{esc(og_description)}">
<meta property="og:type" content="website">
<meta property="og:image" content="{SITE}/og-image.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:url" content="{SITE}/cities.html">
<meta property="og:site_name" content="WillIFit.ai">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="All covered US cities — WillIFit.ai">
<meta name="twitter:description" content="{esc(twitter_description)}">
<meta name="twitter:image" content="{SITE}/og-image.png">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">

<script type="application/ld+json">{jsonld}</script>

{CONSENT_ANALYTICS}

{CSS}
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<div class="page">
{HEADER}

  <main id="main">
  <h1>All covered US cities</h1>
{LEDE}

  <div class="stats">
    <div class="stat"><b>{n_cities}</b> cities</div>
    <div class="stat"><b>{total:,}</b> locations</div>
    <div class="stat">{states_stat}</div>
  </div>

{TOC}

{state_blocks}
  </main>

{FOOTER}
</div>
</body>
</html>
"""


def main():
    by_state = _live_cities_by_state()
    n_cities = sum(len(v) for v in by_state.values())
    total = sum(t for v in by_state.values() for _, _, t in v)
    OUT_PATH.write_text(render())
    print(f"Wrote {OUT_PATH.name}: {n_cities} cities, {total:,} locations, {len(by_state)} state codes")


if __name__ == "__main__":
    main()
