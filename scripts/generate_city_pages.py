#!/usr/bin/env python3
"""
WillIFit — per-city SEO page generator.

Problem this solves:
  Our main app is a single-file SPA using hash-based routing (#las-vegas-nv).
  Google Search sees every "city" as the same HTML page because the content
  is loaded via JS fetch.  That makes 226 cities look like 1 page to crawlers.

Fix:
  Generate a real HTML file per city at /city/{slug}.html.  Each page has:
    - Unique <title>, <meta description>, <link rel=canonical>
    - Real HTML content listing every garage / tunnel / bridge with address,
      clearance height, and notes (indexable text, not JS-fetched)
    - JSON-LD structured data (Place + ItemList) for rich search snippets
    - A prominent CTA linking back to the interactive map at /#{slug}

This way Google indexes 226 unique pages full of clearance data.  The
interactive app is unchanged — users who click links land on the static
page, and can open the map with one click.

Run any time data changes:
    python3 scripts/generate_city_pages.py
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from datetime import date

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"
OUT_DIR = REPO_ROOT / "city"

SITE = "https://willifit.ai"

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia", "PR": "Puerto Rico",
}


def esc(s):
    """HTML-escape, safely handling None."""
    return html.escape(str(s) if s is not None else "", quote=True)


def render_entry(e: dict, kind: str) -> str:
    """Render one garage/tunnel/bridge as an HTML <li>."""
    name = esc(e.get("name", "Unnamed"))
    addr = esc(e.get("addr", ""))
    height_label = e.get("height_label")
    height_in = e.get("height_in")
    height_str = esc(height_label or "Unverified")
    height_class = "height-verified" if height_in else "height-unverified"
    source = esc(e.get("source", ""))
    notes = esc(e.get("notes", ""))[:300]
    oversized = e.get("oversized")
    ai = "AI-verified" in (e.get("source") or "")

    tag_parts = []
    if oversized is True:
        tag_parts.append('<span class="tag tag-oversized">Oversized OK</span>')
    if ai:
        tag_parts.append('<span class="tag tag-ai">✦ AI-verified</span>')
    tags = "".join(tag_parts)

    addr_html = f'<div class="entry-addr">{addr}</div>' if addr else ""
    notes_html = f'<div class="entry-notes">{notes}</div>' if notes else ""

    return (
        f'<li class="entry entry-{kind}">'
        f'<div class="entry-head">'
        f'<h3 class="entry-name">{name}</h3>'
        f'<div class="entry-height {height_class}">{height_str}</div>'
        f'</div>'
        f'{addr_html}'
        f'<div class="entry-tags">{tags}</div>'
        f'{notes_html}'
        f'<div class="entry-source">Source: {source}</div>'
        f'</li>'
    )


def build_jsonld(city: dict, garages: list, tunnels: list, bridges: list) -> str:
    """Build JSON-LD structured data for the city + entries.
    Gives Google enough detail to render rich snippets."""
    name = city["name"]
    state = city["state"]
    state_full = STATE_NAMES.get(state, state)
    total = len(garages) + len(tunnels) + len(bridges)

    items = []
    rank = 1
    for src_list, kind in [(garages, "ParkingFacility"), (tunnels, "Place"), (bridges, "Bridge")]:
        for e in src_list[:20]:  # cap at 20 per type to keep JSON-LD small
            item = {
                "@type": "ListItem",
                "position": rank,
                "item": {
                    "@type": kind,
                    "name": e.get("name", "Unnamed"),
                    "address": {
                        "@type": "PostalAddress",
                        "streetAddress": e.get("addr", ""),
                        "addressLocality": name,
                        "addressRegion": state,
                        "addressCountry": "US",
                    },
                    "geo": {
                        "@type": "GeoCoordinates",
                        "latitude": e.get("lat"),
                        "longitude": e.get("lng"),
                    },
                },
            }
            if e.get("height_label"):
                item["item"]["description"] = f"Posted vehicle clearance: {e['height_label']}"
            items.append(item)
            rank += 1

    data = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": f"Parking clearance heights in {name}, {state_full}",
        "description": f"{total} parking garages, tunnels, and low-clearance bridges "
                       f"with AI-verified clearance heights in {name}, {state_full}.",
        "itemListElement": items,
        "numberOfItems": total,
    }
    return json.dumps(data, separators=(",", ":"))


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0e1116">
<meta name="color-scheme" content="dark">

<title>{title}</title>
<meta name="description" content="{description}">
<meta name="keywords" content="parking clearance {city}, {city} garage heights, low bridges {city}, RV parking {city}, truck clearance {city}, oversized vehicle parking">
<meta name="robots" content="index,follow">
<link rel="canonical" href="{canonical}">

<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:image" content="{site}/og-image.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:url" content="{canonical}">
<meta property="og:site_name" content="WillIFit.ai">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{site}/og-image.png">

<link rel="icon" type="image/svg+xml" href="/favicon.svg">

<!-- Privacy-friendly analytics by Plausible -->
<script async src="https://plausible.io/js/pa-vw06rzB0tQ574fL9diwDM.js"></script>
<script>
  window.plausible=window.plausible||function(){{(plausible.q=plausible.q||[]).push(arguments)}},plausible.init=plausible.init||function(i){{plausible.o=i||{{}}}};
  plausible.init()
</script>

<script type="application/ld+json">{jsonld}</script>

<style>
  :root {{
    --bg: #0e1116; --panel: #171b23; --panel-2: #1d2330;
    --text: #e6eaf0; --muted: #8a95a6; --border: #2a3140;
    --accent: #0ea5e9; --ok: #3ecf8e; --warn: #f5a623; --bad: #e5484d;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    line-height: 1.6;
  }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .page {{ max-width: 960px; margin: 0 auto; padding: 32px 20px 60px; }}
  header {{ display: flex; align-items: center; gap: 12px;
            padding-bottom: 16px; margin-bottom: 24px;
            border-bottom: 1px solid var(--border); font-size: 14px; }}
  header .crumb {{ color: var(--muted); }}
  header .brand {{ font-weight: 800; color: var(--text); letter-spacing: -0.01em; }}
  header .brand .tld {{ color: var(--accent); }}
  h1 {{ font-size: 30px; letter-spacing: -0.02em; margin: 8px 0 12px; }}
  h2 {{ font-size: 20px; letter-spacing: -0.01em;
        margin: 40px 0 16px; padding-bottom: 6px;
        border-bottom: 1px solid var(--border); }}
  .lede {{ color: var(--muted); font-size: 16px; max-width: 640px; }}
  .ai-pill {{
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    background: rgba(14,165,233,0.15); border: 1px solid rgba(14,165,233,0.35);
    color: #7dd3fc; font-size: 10px; font-weight: 700;
    letter-spacing: 0.06em; text-transform: uppercase; vertical-align: 2px;
  }}
  .cta-row {{ margin: 24px 0 8px; }}
  .cta {{
    display: inline-block; padding: 12px 20px;
    background: var(--accent); color: #001018;
    font-weight: 700; border-radius: 8px; font-size: 15px;
  }}
  .cta:hover {{ filter: brightness(1.1); text-decoration: none; }}
  .stats {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 20px 0 0; font-size: 12px; color: var(--muted); }}
  .stat {{ padding: 4px 10px; background: var(--panel); border: 1px solid var(--border);
           border-radius: 999px; font-family: 'SF Mono', monospace; }}
  .stat b {{ color: var(--text); font-weight: 700; }}
  ul.entries {{ list-style: none; padding: 0; margin: 16px 0 0; display: grid; gap: 12px; }}
  .entry {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
            padding: 16px 18px; }}
  .entry-head {{ display: flex; justify-content: space-between; align-items: start; gap: 12px; }}
  .entry-name {{ font-size: 15px; font-weight: 700; margin: 0; color: var(--text); }}
  .entry-height {{ font-family: 'SF Mono', monospace; font-weight: 700;
                   font-size: 14px; white-space: nowrap;
                   padding: 2px 8px; border-radius: 4px; }}
  .height-verified {{ background: rgba(62,207,142,0.15); color: var(--ok); }}
  .height-unverified {{ background: rgba(138,149,166,0.15); color: var(--muted); }}
  .entry-addr {{ color: var(--muted); font-size: 13px; margin: 6px 0 0; }}
  .entry-tags {{ margin: 6px 0 0; display: flex; gap: 6px; flex-wrap: wrap; }}
  .tag {{ font-size: 10px; padding: 2px 7px; border-radius: 999px; font-weight: 600;
          letter-spacing: 0.04em; text-transform: uppercase; }}
  .tag-oversized {{ background: rgba(62,207,142,0.12); color: var(--ok);
                    border: 1px solid rgba(62,207,142,0.3); }}
  .tag-ai {{ background: rgba(14,165,233,0.12); color: var(--accent);
             border: 1px solid rgba(14,165,233,0.3); }}
  .entry-notes {{ color: var(--muted); font-size: 13px; margin: 8px 0 0; }}
  .entry-source {{ color: var(--muted); font-size: 11px; margin: 8px 0 0; font-style: italic; }}
  .disclaimer {{ margin-top: 40px; padding: 14px 16px;
                 background: rgba(245,166,35,0.06);
                 border: 1px solid rgba(245,166,35,0.25);
                 border-left: 3px solid var(--warn);
                 border-radius: 6px;
                 color: var(--muted); font-size: 13px; }}
  .disclaimer b {{ color: var(--warn); }}
  footer {{ margin-top: 60px; padding-top: 20px; border-top: 1px solid var(--border);
            display: flex; justify-content: space-between; flex-wrap: wrap;
            gap: 10px; font-size: 12px; color: var(--muted); }}
  footer a {{ color: var(--muted); }} footer a:hover {{ color: var(--accent); }}
  .empty {{ color: var(--muted); font-style: italic; padding: 16px 0; }}

  /* Sponsor card — fed by /js/sponsors.js from /data/sponsors.json.
     Styling matches the card used in the main app so visitors see one
     consistent visual language for ad inventory across the site. */
  .sponsor-slot-city {{ margin: 20px 0 8px; }}
  .sponsor-card {{
    padding: 14px 16px;
    background: linear-gradient(180deg, rgba(245,166,35,0.04), rgba(62,207,142,0.04));
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 6px;
  }}
  .sponsor-card.house {{
    border-left: 3px dashed var(--accent);
    background: linear-gradient(180deg, rgba(14,165,233,0.05), transparent 80%);
  }}
  .sponsor-label {{
    font-family: 'SF Mono', monospace; font-size: 9px;
    text-transform: uppercase; letter-spacing: 0.1em;
    color: var(--muted); margin-bottom: 4px;
  }}
  .sponsor-title {{ font-weight: 700; font-size: 15px; color: var(--text); margin-bottom: 4px; }}
  .sponsor-desc {{ font-size: 13px; color: var(--muted); line-height: 1.45; margin-bottom: 8px; }}
  .sponsor-cta {{
    display: inline-block;
    color: var(--ok);
    font-family: 'SF Mono', monospace; font-size: 12px;
    text-transform: uppercase; letter-spacing: 0.08em;
    text-decoration: none;
  }}
  .sponsor-cta:hover {{ color: var(--accent); text-decoration: underline; }}

  @media (max-width: 560px) {{
    h1 {{ font-size: 24px; }}
    .entry-head {{ flex-direction: column; gap: 4px; }}
  }}
</style>
</head>
<body>
<div class="page">
  <header>
    <a href="/" class="brand">Will<span class="tld">I</span>Fit<span class="tld">.ai</span></a>
    <span class="crumb">›</span>
    <a href="/#{slug}" class="crumb">{city}, {state}</a>
  </header>

  <span class="ai-pill">AI-verified clearance data</span>
  <h1>Parking clearance heights in {city}, {state_full}</h1>
  <p class="lede">{lede}</p>

  <div class="stats">
    <div class="stat"><b>{garage_count}</b> parking garages</div>
    <div class="stat"><b>{tunnel_count}</b> tunnels</div>
    <div class="stat"><b>{bridge_count}</b> low bridges</div>
  </div>

  <div class="cta-row">
    <a class="cta" href="/#{slug}">Open interactive map →</a>
  </div>

  <!-- City-hero sponsor slot.  Populated by /js/sponsors.js from sponsors.json.
       Kept above-the-fold so high-intent visitors (someone researching {city}
       parking) see geo-targeted inventory before they scroll into the list. -->
  <div id="cityPageSponsor" class="sponsor-slot-city"></div>

  {garages_section}
  {tunnels_section}
  {bridges_section}

  <div class="disclaimer">
    <b>⚠ Always verify at the sign.</b>
    Posted clearances on this page are for planning. The only authoritative number
    is the sign at the garage entrance or bridge approach. Clearances can change
    due to re-paving, renovations, or weather. If you spot an inaccuracy,
    <a href="/#{slug}">open the map</a> and use the "Report clearance" button.
  </div>

  <footer>
    <div>© {year} WillIFit.ai — clearance data for RVs, trucks &amp; oversized vehicles.</div>
    <div>
      <a href="/">Home</a> · <a href="/advertise.html">Advertise</a> ·
      <a href="/how-ai-verification-works.html">AI verification</a> ·
      <a href="/terms.html">Terms</a> · <a href="/privacy.html">Privacy</a>
    </div>
  </footer>
</div>

<!-- Sponsor renderer.  Shared module used across all per-city pages. -->
<script src="/js/sponsors.js"></script>
<script>
  (function () {{
    var el = document.getElementById('cityPageSponsor');
    if (el && window.WillIFitSponsors) {{
      window.WillIFitSponsors.renderInto(el, 'city_hero', {{ city: '{slug}' }});
    }}
  }})();
</script>
</body>
</html>
"""


def render_section(title: str, entries: list, kind: str) -> str:
    if not entries:
        return f'<h2>{title} (0)</h2><div class="empty">None indexed in this city yet.</div>'
    items_html = "\n".join(render_entry(e, kind) for e in entries)
    return f'<h2>{title} ({len(entries)})</h2><ul class="entries">{items_html}</ul>'


def generate_city(city: dict) -> str:
    slug = city["slug"]
    name = city["name"]
    state = city["state"]
    state_full = STATE_NAMES.get(state, state)

    data_path = CITIES_DIR / f"{slug}.json"
    if not data_path.exists():
        return None

    data = json.loads(data_path.read_text())
    garages = data.get("garages") or []
    tunnels = data.get("tunnels") or []
    bridges = data.get("bridges") or []
    total = len(garages) + len(tunnels) + len(bridges)

    # Build a short paragraph describing what's on the page, for meta + lede.
    parts = []
    if garages:
        parts.append(f"{len(garages)} parking garages")
    if tunnels:
        parts.append(f"{len(tunnels)} tunnels")
    if bridges:
        parts.append(f"{len(bridges)} low-clearance bridges")
    lede = (
        f"AI-verified clearance heights for {', '.join(parts)} in {name}, {state_full}. "
        f"Enter your vehicle height on the interactive map to see what fits."
    )

    description = (
        f"Vehicle clearance heights for {total} parking garages, tunnels, and low bridges "
        f"in {name}, {state_full}. AI-verified data for RVs, trucks, and oversized vehicles."
    )[:160]
    title = f"Parking &amp; Bridge Clearance Heights in {name}, {state_full} ({total} locations) | WillIFit.ai"

    page = PAGE_TEMPLATE.format(
        title=title,
        description=esc(description),
        canonical=f"{SITE}/city/{slug}.html",
        site=SITE,
        slug=slug,
        city=esc(name),
        state=esc(state),
        state_full=esc(state_full),
        lede=esc(lede),
        garage_count=len(garages),
        tunnel_count=len(tunnels),
        bridge_count=len(bridges),
        garages_section=render_section("Parking garages", garages, "garage"),
        tunnels_section=render_section("Tunnels", tunnels, "tunnel"),
        bridges_section=render_section("Low-clearance bridges", bridges, "bridge"),
        year=date.today().year,
        jsonld=build_jsonld(city, garages, tunnels, bridges),
    )
    return page


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    idx = json.loads(INDEX_PATH.read_text())
    live = [c for c in idx if c.get("status") == "live"]
    print(f"Generating {len(live)} per-city pages…")

    generated = 0
    skipped = 0
    for city in live:
        html_str = generate_city(city)
        if html_str is None:
            skipped += 1
            continue
        out_path = OUT_DIR / f"{city['slug']}.html"
        out_path.write_text(html_str)
        generated += 1

    print(f"\nGenerated: {generated}")
    print(f"Skipped (no data file): {skipped}")
    print(f"Output directory: {OUT_DIR}")


if __name__ == "__main__":
    main()
