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
import math
import os
import re
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


RV_NAME_RE = re.compile(r'\b(rv park|rv resort|caravan|kampground|koa)\b', re.IGNORECASE)


def compute_quick_facts(garages: list, tunnels: list, bridges: list) -> dict:
    """Per-city stat block used both for the visible 'Quick facts' card
    and as the source for FAQPage answers below.

    Lowest/highest are computed from GARAGES only -- the FAQs ask about
    parking garages specifically, and pulling bridge/tunnel heights into
    the answer would be misleading (a "Low clearance underpass" tagged
    at 18'7" is not Vegas's tallest garage).  Tunnels + bridges still
    appear in their own page sections below."""
    verified = [g for g in garages
                if isinstance(g.get("height_in"), (int, float)) and g["height_in"] > 0]
    lowest = min(verified, key=lambda g: g["height_in"]) if verified else None
    highest = max(verified, key=lambda g: g["height_in"]) if verified else None
    oversized = [g for g in garages if g.get("oversized")]
    rv_parks = [g for g in garages
                if RV_NAME_RE.search(g.get("name") or "")
                or "caravan_site" in (g.get("source") or "").lower()]
    return {
        "verified": verified,
        "lowest": lowest,
        "highest": highest,
        "oversized": oversized,
        "rv_parks": rv_parks,
        "verified_count": len(verified),
        "oversized_count": len(oversized),
        "rv_park_count": len(rv_parks),
    }


def render_quick_facts(facts: dict) -> str:
    """Featured-snippet-friendly stat block.  Renders right under the H1
    so Google's 'Answer' / 'Featured snippet' selector can lift it
    verbatim, and so AI Overview / ChatGPT cite it directly."""
    cards = []
    if facts["lowest"]:
        e = facts["lowest"]
        cards.append(
            '<div class="qf-card">'
            '<div class="qf-label">Lowest clearance</div>'
            f'<div class="qf-value">{esc(e.get("height_label") or "")}</div>'
            f'<div class="qf-detail">{esc(e.get("name") or "")}</div>'
            '</div>'
        )
    if facts["highest"]:
        e = facts["highest"]
        cards.append(
            '<div class="qf-card">'
            '<div class="qf-label">Highest clearance</div>'
            f'<div class="qf-value">{esc(e.get("height_label") or "")}</div>'
            f'<div class="qf-detail">{esc(e.get("name") or "")}</div>'
            '</div>'
        )
    cards.append(
        '<div class="qf-card">'
        '<div class="qf-label">Oversized-friendly</div>'
        f'<div class="qf-value">{facts["oversized_count"]}</div>'
        '<div class="qf-detail">RV / box-truck OK</div>'
        '</div>'
    )
    cards.append(
        '<div class="qf-card">'
        '<div class="qf-label">RV parks</div>'
        f'<div class="qf-value">{facts["rv_park_count"]}</div>'
        '<div class="qf-detail">indexed</div>'
        '</div>'
    )
    return '<section class="quick-facts" aria-label="Quick facts">' + ''.join(cards) + '</section>'


def build_faqs(city_meta: dict, facts: dict) -> list:
    """Auto-generate 4-5 Q&A pairs per city from its stats.  Used both
    as visible content (an HTML <details> list) AND as FAQPage JSON-LD,
    which is what ChatGPT / Perplexity / AI Overviews cite verbatim."""
    name = city_meta["name"]
    state_full = STATE_NAMES.get(city_meta["state"], city_meta["state"])
    slug = city_meta["slug"]
    faqs = []

    if facts["lowest"]:
        e = facts["lowest"]
        faqs.append({
            "q": f"What's the lowest-clearance parking garage in {name}, {state_full}?",
            "a": (f"The lowest verified clearance in {name} is {e.get('height_label')} "
                  f"({int(e.get('height_in'))} inches) at {e.get('name')}. "
                  f"Standard cars and small SUVs fit, but vans, RVs, and box trucks "
                  f"should look elsewhere."),
        })

    if facts["highest"]:
        e = facts["highest"]
        faqs.append({
            "q": f"What's the highest-clearance parking option in {name}?",
            "a": (f"{e.get('name')} has the tallest verified clearance in {name} at "
                  f"{e.get('height_label')} ({int(e.get('height_in'))} inches), which "
                  f"accommodates most box trucks and smaller RVs."),
        })

    if facts["oversized_count"] > 0:
        names = [g.get("name") or "Unnamed" for g in facts["oversized"][:3]]
        joined = ", ".join(names)
        more = f" and {facts['oversized_count'] - len(names)} more" if facts['oversized_count'] > len(names) else ""
        faqs.append({
            "q": f"Which parking facilities in {name} accept RVs or oversized vehicles?",
            "a": (f"{facts['oversized_count']} facilities in {name} are explicitly marked as "
                  f"oversized-vehicle-friendly: {joined}{more}. "
                  f"See the full list on the interactive map at willifit.ai/#{slug}."),
        })
    else:
        faqs.append({
            "q": f"Which parking facilities in {name} accept RVs or oversized vehicles?",
            "a": (f"None of the parking facilities currently indexed in {name} are explicitly "
                  f"marked as oversized-vehicle-friendly. RV and box-truck drivers should "
                  f"call ahead, look for surface lots, or check nearby cities on willifit.ai."),
        })

    if facts["rv_park_count"] > 0:
        rv_names = [g.get("name") or "Unnamed" for g in facts["rv_parks"][:3]]
        joined = ", ".join(rv_names)
        more = f" and {facts['rv_park_count'] - len(rv_names)} more" if facts['rv_park_count'] > len(rv_names) else ""
        faqs.append({
            "q": f"Are there RV parks in {name}?",
            "a": (f"Yes. {facts['rv_park_count']} RV park(s) are indexed in {name}, "
                  f"including {joined}{more}. RV parks have no overhead clearance — any "
                  f"vehicle size fits."),
        })

    faqs.append({
        "q": "How is the clearance data verified?",
        "a": ("Each posted clearance is read directly from the sign at the garage entrance "
              "using Google Street View imagery and Claude Vision (Anthropic's image AI). "
              "Only readings with high confidence are committed; we record the exact "
              "Street View pano ID and the verification date so anyone can independently "
              "confirm in Google Maps."),
    })

    return faqs


def render_faq_section(faqs: list) -> str:
    if not faqs:
        return ""
    items = []
    for f in faqs:
        items.append(
            '<details class="faq-item">'
            f'<summary class="faq-q">{esc(f["q"])}</summary>'
            f'<div class="faq-a">{esc(f["a"])}</div>'
            '</details>'
        )
    return (
        '<section class="faq-section" aria-label="Frequently asked questions">'
        '<h2>Frequently asked</h2>'
        + ''.join(items)
        + '</section>'
    )


def faqs_to_jsonld(faqs: list) -> dict:
    """Returns a FAQPage Schema.org dict suitable for embedding in
    the page's combined JSON-LD array."""
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": f["q"],
                "acceptedAnswer": {"@type": "Answer", "text": f["a"]},
            }
            for f in faqs
        ],
    }


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 3958.8  # earth radius in miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def compute_nearby_cities(this_city: dict, all_cities: list, max_miles: float = 60.0,
                          max_results: int = 6) -> list:
    """List of (city, distance_mi) tuples for cities within max_miles of
    the current city's centroid, sorted by distance.  Drives the
    'Nearby cities' cross-link block (internal-link equity) and the
    'plan a route' use case (Vegas -> Henderson -> Boulder City)."""
    out = []
    for c in all_cities:
        if c.get("slug") == this_city.get("slug"):
            continue
        if c.get("status") != "live":
            continue
        d = haversine_miles(this_city["lat"], this_city["lng"], c["lat"], c["lng"])
        if d <= max_miles:
            out.append((c, d))
    out.sort(key=lambda x: x[1])
    return out[:max_results]


def render_nearby_cities(nearby: list) -> str:
    if not nearby:
        return ""
    items = []
    for (c, d) in nearby:
        slug = c["slug"]
        name = esc(c["name"])
        state = esc(c["state"])
        items.append(
            f'<li><a href="/city/{slug}.html">{name}, {state}</a>'
            f' <span class="nc-dist">{int(round(d))} mi</span></li>'
        )
    return (
        '<section class="nearby-cities" aria-label="Nearby cities">'
        '<h2>Nearby cities</h2>'
        '<ul class="nc-list">' + ''.join(items) + '</ul>'
        '</section>'
    )


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


def build_jsonld(city: dict, garages: list, tunnels: list, bridges: list,
                 faqs: list = None) -> str:
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

    item_list = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": f"Parking clearance heights in {name}, {state_full}",
        "description": f"{total} parking garages, tunnels, and low-clearance bridges "
                       f"with AI-verified clearance heights in {name}, {state_full}.",
        "itemListElement": items,
        "numberOfItems": total,
    }
    # BreadcrumbList — signals page hierarchy to Google (Home > Cities > <City>),
    # and is what earns the "> crumb > crumb" format in search results.
    breadcrumbs = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home",
             "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": "Cities",
             "item": f"{SITE}/cities.html"},
            {"@type": "ListItem", "position": 3, "name": f"{name}, {state_full}",
             "item": f"{SITE}/city/{city['slug']}.html"},
        ],
    }
    blocks = [item_list, breadcrumbs]
    if faqs:
        blocks.append(faqs_to_jsonld(faqs))
    return json.dumps(blocks, separators=(",", ":"))


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

  /* Quick-facts stat block.  Big, definitive numbers in a 4-up grid
     that AI Overview / featured-snippet pickers can lift verbatim. */
  .quick-facts {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 10px;
    margin: 24px 0 8px;
  }}
  .qf-card {{
    background: var(--panel); border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 6px; padding: 12px 14px;
  }}
  .qf-label {{
    font-family: 'SF Mono', monospace; font-size: 10px;
    text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted);
  }}
  .qf-value {{
    font-size: 22px; font-weight: 700; color: var(--text);
    margin: 4px 0 2px; letter-spacing: -0.01em;
  }}
  .qf-detail {{ font-size: 12px; color: var(--muted); }}

  /* FAQ section -- <details>/<summary> for accessibility, FAQPage
     JSON-LD lives in the page head for AI answer engines. */
  .faq-section {{ margin-top: 40px; }}
  .faq-item {{
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 6px; padding: 0; margin: 8px 0; overflow: hidden;
  }}
  .faq-q {{
    padding: 14px 16px; font-weight: 600; font-size: 15px; cursor: pointer;
    color: var(--text); list-style: none;
  }}
  .faq-q::-webkit-details-marker {{ display: none; }}
  .faq-q::before {{
    content: '+'; display: inline-block; width: 20px;
    color: var(--accent); font-weight: 800;
  }}
  details[open] .faq-q::before {{ content: '−'; }}
  .faq-a {{
    padding: 0 16px 14px 36px; font-size: 14px; color: var(--muted); line-height: 1.55;
  }}

  /* Nearby cities -- internal-link equity + plan-a-route. */
  .nearby-cities {{ margin-top: 40px; }}
  .nc-list {{
    list-style: none; padding: 0; margin: 12px 0 0;
    display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 6px 16px;
  }}
  .nc-list li {{ font-size: 14px; }}
  .nc-dist {{ color: var(--muted); font-family: 'SF Mono', monospace; font-size: 11px; }}

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
    <a href="/cities.html" class="crumb">Cities</a>
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

  <!-- Quick-facts: featured-snippet bait.  Renders right under the CTA so
       it's the first content block AI Overview / ChatGPT / Perplexity
       see when summarizing the page. -->
  {quick_facts}

  <!-- City-hero sponsor slot.  Populated by /js/sponsors.js from sponsors.json.
       Kept above-the-fold so high-intent visitors (someone researching {city}
       parking) see geo-targeted inventory before they scroll into the list. -->
  <div id="cityPageSponsor" class="sponsor-slot-city"></div>

  {garages_section}
  {tunnels_section}
  {bridges_section}

  <!-- Auto-generated FAQ from city stats.  Visible <details>/<summary>
       for users; FAQPage JSON-LD in the head for AI answer engines. -->
  {faq_section}

  <!-- Nearby-cities cross-link block.  Internal-link equity + helps
       users plan multi-city routes. -->
  {nearby_cities}

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


def generate_city(city: dict, all_cities: list = None) -> str:
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

    # Quick-facts stat block + auto-generated FAQs feed both visible content
    # and the FAQPage JSON-LD (cited by ChatGPT / Perplexity / AI Overviews).
    facts = compute_quick_facts(garages, tunnels, bridges)
    faqs = build_faqs(city, facts)
    nearby = compute_nearby_cities(city, all_cities or [city]) if all_cities else []

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
        quick_facts=render_quick_facts(facts),
        faq_section=render_faq_section(faqs),
        nearby_cities=render_nearby_cities(nearby),
        year=date.today().year,
        jsonld=build_jsonld(city, garages, tunnels, bridges, faqs=faqs),
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
        html_str = generate_city(city, all_cities=live)
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
