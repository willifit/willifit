#!/usr/bin/env python3
"""
WillIFit — per-garage SEO/AEO page generator (SEO/AEO plan, Task 3).

Problem this solves:
  The per-city pages (generate_city_pages.py) list every garage in one long
  page, but a searcher asking "what's the clearance at Aria Resort & Casino
  parking garage" or an AI answer engine looking for one specific location
  has no dedicated, citable URL to land on -- just an anchor inside a much
  bigger city page.

Fix:
  Generate one real HTML file per eligible garage at
  /parking/<city-slug>/<garage-slug>: a focused page with a direct answer
  paragraph, a "will it fit" table for common vehicle classes, a details
  list, links back to the map/city page, nearby alternatives, and three
  FAQs (U-Haul, RV, verification) with their own FAQPage JSON-LD.

  Only garages with a posted height AND a meaningful (non-generic) name get
  a page -- see eligible() below.  City pages link straight to these pages
  for eligible garages (see generate_city_pages.render_entry's `path` arg).

Run any time data changes:
    python3 scripts/generate_location_pages.py
    python3 scripts/generate_location_pages.py --city las-vegas-nv
    python3 scripts/generate_location_pages.py --verified-only
    python3 scripts/generate_location_pages.py --dry-run
"""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import date
from pathlib import Path

from wf_common import (STATE_NAMES, VEHICLE_CLASSES, MEASURE_NOTE, inches_label,
                       fit_phrase, has_posted_height, slugify, clip, compose_description)
from generate_city_pages import (assign_anchors, verification_sentence, entry_verification,
                                 streetview_url, safe_jsonld, esc, haversine_miles,
                                 render_faq_section, faqs_to_jsonld)

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"
OUT_DIR = REPO_ROOT / "parking"

SITE = "https://willifit.ai"

# Generic / meaningless names never earn their own page -- "Parking Deck",
# "Lot 26", "Unnamed parking structure" tell a searcher nothing a city page
# doesn't already say, and a page full of these would read as thin content.
GENERIC_NAME_RE = re.compile(
    r"^(parking|parking (lot|garage|deck|structure)|garage|deck|lot( ?\d+)?|"
    r"unnamed.*|truck parking|parking structure|car park|multi-storey car park)$",
    re.IGNORECASE,
)


def eligible(e: dict) -> bool:
    """A garage earns its own page iff it has a posted height, numeric
    coordinates, AND a meaningful name (not a generic placeholder like
    "Parking Deck").  Coordinates are required because render_details,
    render_links_row, and nearby() all assume a real lat/lng -- without
    this check here, a future un-geocoded record would crash generation
    for the whole city (nearby() feeds raw lat/lng into haversine_miles,
    which raises TypeError on None)."""
    if not has_posted_height(e):
        return False
    if not isinstance(e.get("lat"), (int, float)) or not isinstance(e.get("lng"), (int, float)):
        return False
    name = (e.get("name") or "").strip()
    if not name:
        return False
    return not GENERIC_NAME_RE.match(name)


def location_paths(city_slug: str, garages: list) -> list:
    """URL path per garage, aligned with `garages` (None for ineligible
    entries).  Slugs are assigned deterministically: eligible garages are
    sorted by (slugify(name), str(id)) so duplicate-name collisions always
    number the same way regardless of the garages' order in the data file --
    the first in that sort keeps the bare slug, later duplicates get -2,
    -3, ...  Paths are then returned in the ORIGINAL list order."""
    order = sorted((i for i, g in enumerate(garages) if eligible(g)),
                   key=lambda i: (slugify(garages[i].get("name") or ""), str(garages[i].get("id"))))
    counts, slug_for = {}, {}
    for i in order:
        base = slugify(garages[i].get("name") or "")
        counts[base] = counts.get(base, 0) + 1
        slug_for[i] = base if counts[base] == 1 else f"{base}-{counts[base]}"
    return [f"/parking/{city_slug}/{slug_for[i]}" if i in slug_for else None
            for i in range(len(garages))]


def build_title(name: str, label: str) -> tuple:
    """(<title> text, og:title text) -- og:title always uses the full
    descriptive form; <title> falls back through shorter forms until the
    DECODED (browser-rendered) length is <= 60 chars, same policy as
    generate_city_pages.build_title."""
    esc_name, esc_label = esc(name), esc(label)
    forms = [
        f"{esc_name} Parking Clearance Height: {esc_label} | WillIFit.ai",
        f"{esc_name} Clearance: {esc_label} | WillIFit.ai",
        f"{esc_name} clearance: {esc_label}",
        f"{esc(clip(name, 40))} clearance: {esc_label}",
    ]
    og_title = forms[0]
    for f in forms:
        if len(html.unescape(f)) <= 60:
            return f, og_title
    return forms[-1], og_title


def render_answer(name: str, addr: str, city_name: str, state_full: str,
                  label: str, h: int, e: dict) -> str:
    where = f"{esc(name)}, {esc(addr)}" if addr else esc(name)
    return (
        f'<p class="answer" id="answer">The posted vehicle clearance at {where} '
        f'in {esc(city_name)}, {esc(state_full)} is {esc(label)} ({h} inches). '
        f'{esc(verification_sentence(e))} {esc(fit_phrase(h))} {MEASURE_NOTE}</p>'
    )


def render_fit_table(h: int, label: str) -> str:
    rows = []
    for cname, need in VEHICLE_CLASSES:
        cap = cname[0].upper() + cname[1:]
        fits = need <= h
        cls = "fit-yes" if fits else "fit-no"
        word = "Yes" if fits else "No"
        rows.append(f'<tr><td>{esc(cap)}</td><td>{esc(inches_label(need))}</td>'
                    f'<td class="{cls}">{word}</td></tr>')
    return (
        '<h2>Will it fit?</h2>'
        f'<div class="table-wrap" tabindex="0" role="region" '
        f'aria-label="Will it fit at {esc(label)}?, scrollable table">'
        '<table>'
        '<caption>Typical heights by vehicle class; the sign at the entrance is the only authoritative number.</caption>'
        '<thead><tr><th scope="col">Vehicle</th><th scope="col">Typical height</th>'
        f'<th scope="col">Fits {esc(label)}?</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
        '</div>'
    )


def render_details(e: dict) -> str:
    addr = e.get("addr") or ""
    # No "or 0" fallback: eligible() guarantees numeric lat/lng for every
    # garage this is called on, so a None here means eligible() was bypassed
    # and this should fail loudly rather than silently print 0.00000, 0.00000.
    lat, lng = e.get("lat"), e.get("lng")
    # esc() first, then clip -- same order as generate_city_pages.render_entry's
    # notes handling, so a 300-char cut lands on the same boundary either way.
    notes = esc(e.get("notes") or "")[:300]
    website = e.get("website") or ""

    items = []
    if addr:
        items.append(f"<dt>Address</dt><dd>{esc(addr)}</dd>")
    items.append(f"<dt>Coordinates</dt><dd>{lat:.5f}, {lng:.5f}</dd>")
    oversized_txt = ("Marked oversized-vehicle-friendly" if e.get("oversized") is True
                     else "Not marked oversized-vehicle-friendly")
    items.append(f"<dt>Oversized vehicles</dt><dd>{oversized_txt}</dd>")
    if notes:
        items.append(f"<dt>Notes</dt><dd>{notes}</dd>")
    if website.startswith("http"):
        host = website.split("//", 1)[1].split("/", 1)[0]
        items.append(f'<dt>Website</dt><dd><a href="{esc(website)}" rel="noopener" '
                     f'target="_blank">{esc(host)}</a></dd>')
    items.append(f"<dt>Source</dt><dd>{esc(e.get('source') or '')}</dd>")
    return '<h2>Details</h2><dl class="details">' + "".join(items) + "</dl>"


def render_links_row(e: dict, city: dict, anchor: str) -> str:
    links = []
    sv = streetview_url(e)
    if sv:
        links.append(f'<a class="cta" href="{esc(sv)}" target="_blank" rel="noopener">'
                     f'See the sign in Street View</a>')
    links.append(f'<a class="cta" href="/#{esc(city["slug"])}">Open in the interactive map</a>')
    directions = f'https://www.google.com/maps/dir/?api=1&destination={e.get("lat")},{e.get("lng")}'
    links.append(f'<a class="cta" href="{esc(directions)}" target="_blank" rel="noopener">Directions</a>')
    links.append(f'<a class="cta" href="/city/{esc(city["slug"])}#{esc(anchor)}">'
                 f'All {esc(city["name"])} clearances</a>')
    return '<div class="cta-row">' + "".join(links) + '</div>'


def nearby(garage: dict, all_garages: list, all_paths: list, anchors: list) -> list:
    """Up to 6 other garages with a posted height AND numeric coordinates in
    the same city, nearest first.  Prefers garages within 1.5 mi; if that
    yields fewer than 3, falls back to the 6 nearest overall so a page in a
    sparse city still gets a nearby section.

    Candidates come from `all_garages` (has_posted_height only, not full
    eligible()), so a coordinate-less garage can still reach this loop --
    skip it explicitly rather than handing None to haversine_miles, which
    raises TypeError and would abort the whole run."""
    lat, lng = garage.get("lat"), garage.get("lng")
    cands = []
    for g, p, a in zip(all_garages, all_paths, anchors):
        if g is garage or not has_posted_height(g):
            continue
        glat, glng = g.get("lat"), g.get("lng")
        if not isinstance(glat, (int, float)) or not isinstance(glng, (int, float)):
            continue
        d = haversine_miles(lat, lng, glat, glng)
        cands.append({"garage": g, "path": p, "anchor": a, "dist": d})
    within = sorted((c for c in cands if c["dist"] <= 1.5), key=lambda c: c["dist"])
    chosen = within if len(within) >= 3 else sorted(cands, key=lambda c: c["dist"])
    return chosen[:6]


def render_nearby_section(name: str, h: int, candidates: list, city_slug: str) -> str:
    if not candidates:
        return ""
    items = []
    for c in candidates:
        g = c["garage"]
        their_h = int(round(float(g["height_in"])))
        if their_h > h:
            cmp_word = "taller"
        elif their_h < h:
            cmp_word = "lower"
        else:
            cmp_word = "same height"
        href = c["path"] or f'/city/{city_slug}#{c["anchor"]}'
        items.append(
            f'<li><a href="{esc(href)}">{esc(g.get("name") or "Unnamed")}</a>'
            f' · {esc(inches_label(their_h))} · {c["dist"]:.1f} mi · {cmp_word}</li>'
        )
    return (
        f'<h2>Other garages with a posted clearance near {esc(name)}</h2>'
        f'<ul class="nearby-list">{"".join(items)}</ul>'
    )


def _uhaul_faq(name: str, h: int, label: str) -> dict:
    fits10, fits15, fits26 = h >= 108, h >= 132, h >= 144
    if fits26:
        verdict, sizes = "Yes", f"All three sizes fit at {label}."
    elif fits15:
        verdict = "Only the smaller trucks"
        sizes = f"At {label}, the 10 ft and 15–20 ft trucks fit; the 26 ft truck does not."
    elif fits10:
        verdict, sizes = "Only the smaller trucks", f"At {label}, only the 10 ft truck fits."
    else:
        verdict, sizes = "No", f"None of them fits under {label}."
    answer = (f"{verdict}. The posted clearance is {label}. U-Haul lists a clearance height of "
             f"9'0\" for its 10 ft truck, 11'0\" for its 15, 17, and 20 ft trucks, and 12'0\" for "
             f"its 26 ft truck. {sizes} Budget and Penske publish different figures; see the "
             f"vehicle heights guide at willifit.ai/vehicle-heights.html.")
    return {"q": f"Will a U-Haul fit at {name}?", "a": answer}


def _rv_faq(name: str, h: int, label: str) -> dict:
    if h >= 144:
        rv = f"Both classes clear {label} even with a rooftop air conditioner in most cases; measure your own rig."
    elif h >= 132:
        rv = "Both classes fit at their published heights, but a rooftop air conditioner or vent may not; measure first."
    elif h >= 102:
        rv = f"A Class B camper van fits only if it measures under {label} including roof equipment; a Class C motorhome does not."
    else:
        rv = f"Neither a Class B camper van nor a Class C motorhome fits under {label}."
    answer = (f"Class B camper vans are typically 8.5 to 11 ft tall and Class C motorhomes 10 to 11 "
             f"ft, not counting roof-mounted air conditioners and vents. {rv} Class A motorhomes "
             f"and fifth wheels run 11 to 13.5 ft and exceed most garage clearances; use a surface lot.")
    return {"q": f"Will an RV fit at {name}?", "a": answer}


def _verify_faq(e: dict) -> dict:
    parts = [verification_sentence(e)]
    if streetview_url(e):
        parts.append("Open the Street View link above to read the sign yourself.")
    parts.append("Clearances change with re-paving and renovations; confirm at the sign before you enter.")
    return {"q": "How was this clearance verified?", "a": " ".join(parts)}


def build_faqs(name: str, h: int, label: str, e: dict) -> list:
    return [_uhaul_faq(name, h, label), _rv_faq(name, h, label), _verify_faq(e)]


def build_location_jsonld(city: dict, garage: dict, canonical: str, label: str,
                          h: int, faqs: list) -> str:
    state_full = STATE_NAMES.get(city["state"], city["state"])
    address = {"@type": "PostalAddress"}
    if garage.get("addr"):
        address["streetAddress"] = garage["addr"]
    address["addressLocality"] = city["name"]
    address["addressRegion"] = city["state"]
    address["addressCountry"] = "US"
    name = garage.get("name") or "Unnamed"

    facility = {
        "@context": "https://schema.org",
        "@type": "ParkingFacility",
        "@id": canonical,
        "url": canonical,
        "name": name,
        "address": address,
        "geo": {"@type": "GeoCoordinates", "latitude": garage.get("lat"), "longitude": garage.get("lng")},
        "description": f"Posted vehicle clearance: {label} ({h} inches).",
        "additionalProperty": [{
            "@type": "PropertyValue",
            "name": "Vehicle clearance height",
            "value": h,
            "unitCode": "INH",
            "unitText": "inches",
        }],
    }
    if garage.get("website"):
        facility["sameAs"] = [garage["website"]]

    breadcrumbs = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": "Cities", "item": f"{SITE}/cities.html"},
            {"@type": "ListItem", "position": 3, "name": state_full, "item": f"{SITE}/state/{city['state'].lower()}"},
            {"@type": "ListItem", "position": 4, "name": f"{city['name']}, {state_full}",
             "item": f"{SITE}/city/{city['slug']}"},
            {"@type": "ListItem", "position": 5, "name": name, "item": canonical},
        ],
    }

    blocks = [facility, breadcrumbs]
    if garage.get("verified_on"):
        blocks.append({
            "@context": "https://schema.org",
            "@type": "WebPage",
            "@id": canonical,
            "url": canonical,
            "name": f"{name} parking clearance: {label}",
            "dateModified": garage["verified_on"],
        })
    blocks.append(faqs_to_jsonld(faqs))
    return safe_jsonld(blocks)


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0e1116">
<meta name="color-scheme" content="dark">

<title>{title_tag}</title>
<meta name="description" content="{description}">
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

<script src="/js/consent.js"></script>
<!-- Cloudflare Web Analytics -->
<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon='{{"token": "162b93f801fa42499a0b840c50d3f772"}}'></script>
<!-- End Cloudflare Web Analytics -->
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-SH191K5NGS"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', 'G-SH191K5NGS');
</script>
<!-- End Google tag -->

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
  .skip-link {{
    position: absolute; top: -40px; left: 0;
    background: var(--ok); color: #0a0f18;
    padding: 8px 12px; z-index: 9999;
    font-family: 'SF Mono', monospace; font-size: 12px;
    text-decoration: none; font-weight: 700;
  }}
  .skip-link:focus {{ top: 0; }}
  a {{ color: var(--accent); text-decoration: none; }}
  main a {{ text-decoration: underline; text-underline-offset: 2px; text-decoration-color: rgba(14,165,233,0.4); }}
  a:hover {{ text-decoration: underline; }}
  .page {{ max-width: 960px; margin: 0 auto; padding: 32px 20px 60px; }}
  header {{ display: flex; align-items: center; gap: 12px;
            padding-bottom: 16px; margin-bottom: 24px;
            border-bottom: 1px solid var(--border); font-size: 14px; }}
  header .crumb {{ color: var(--muted); }}
  header .brand {{ font-weight: 800; color: var(--text); letter-spacing: -0.01em; }}
  header .brand .tld {{ color: var(--accent); }}
  header a.brand, header a.crumb {{
    display: inline-flex; align-items: center;
    min-height: 24px; padding: 6px 4px; margin: -6px -4px;
  }}
  h1 {{ font-size: 30px; letter-spacing: -0.02em; margin: 8px 0 12px; }}
  h2 {{ font-size: 20px; letter-spacing: -0.01em;
        margin: 40px 0 16px; padding-bottom: 6px;
        border-bottom: 1px solid var(--border); }}

  /* Answer paragraph -- the featured-snippet target. */
  .answer {{ font-size: 17px; line-height: 1.7; max-width: 720px; }}

  .cta-row {{ margin: 24px 0 8px; display: flex; flex-wrap: wrap; gap: 10px; }}
  .cta {{
    display: inline-block; padding: 12px 20px;
    background: var(--accent); color: #001018;
    font-weight: 700; border-radius: 8px; font-size: 15px;
  }}
  .cta:hover {{ filter: brightness(1.1); text-decoration: none; }}

  /* Fit table -- table CSS copied from generate_bridges_page.py. */
  table {{ width: 100%; border-collapse: collapse; margin: 18px 0; font-size: 14px; }}
  th, td {{ text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.04em; }}
  tr:last-child td {{ border-bottom: none; }}
  td b {{ color: var(--bad); font-variant-numeric: tabular-nums; }}
  .table-wrap {{ overflow-x: auto; }}
  table caption {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
                   overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }}
  .fit-yes {{ color: var(--ok); font-weight: 700 }}
  .fit-no {{ color: var(--bad); font-weight: 700 }}

  /* Details definition list. */
  dl.details {{ display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px }}
  dl.details dt {{ color: var(--muted) }}
  dl.details dd {{ margin: 0; }}

  /* Nearby-garages list -- no equivalent block in the city template, so
     this small rule is the page's own addition. */
  ul.nearby-list {{ list-style: none; padding: 0; margin: 12px 0 0; display: grid; gap: 8px; }}
  ul.nearby-list li {{ font-size: 14px; color: var(--muted); }}
  ul.nearby-list a {{ color: var(--accent); font-weight: 600; }}

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
  .faq-q h3 {{ display: inline; margin: 0; font-size: inherit; font-weight: inherit; letter-spacing: inherit; border: 0; padding: 0; }}
  .faq-q::-webkit-details-marker {{ display: none; }}
  .faq-q::before {{
    content: '+'; display: inline-block; width: 20px;
    color: var(--accent); font-weight: 800;
  }}
  details[open] .faq-q::before {{ content: '−'; }}
  .faq-a {{
    padding: 0 16px 14px 36px; font-size: 14px; color: var(--muted); line-height: 1.55;
  }}

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

  @media (max-width: 560px) {{
    h1 {{ font-size: 24px; }}
  }}
  @media (max-width: 600px) {{
    header a.brand, header a.crumb {{
      min-height: 44px; padding: 12px 4px; margin: -12px -4px;
    }}
  }}
</style>
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<div class="page">
  <header>
    <a href="/" class="brand">Will<span class="tld">I</span>Fit<span class="tld">.ai</span></a>
    <span class="crumb">›</span>
    <a href="/cities.html" class="crumb">Cities</a>
    <span class="crumb">›</span>
    <a href="/state/{state_lower}" class="crumb">{state_full}</a>
    <span class="crumb">›</span>
    <a href="/city/{city_slug}" class="crumb">{city_name}, {state_code}</a>
    <span class="crumb">›</span>
    <span class="crumb">{name}</span>
  </header>

  <main id="main">
  <h1>{h1}</h1>
  {answer}

  {fit_table}

  {details}

  {links_row}

  {nearby_section}

  {faq_section}

  <div class="disclaimer">
    <b>⚠ Always verify at the sign.</b>
    Posted clearances on this page are for planning. The only authoritative number
    is the sign at the garage entrance or bridge approach. Clearances can change
    due to re-paving, renovations, or weather. If you spot an inaccuracy,
    <a href="/#{city_slug}">open the map</a> and use the "Report clearance" button.
  </div>
  </main>

  <footer>
    <div>© {year} WillIFit.ai — clearance data for RVs, trucks &amp; oversized vehicles.</div>
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
  </footer>
</div>
</body>
</html>
"""


def generate_location(city: dict, garage: dict, path: str, all_garages: list,
                      all_paths: list, anchor: str) -> str:
    name = garage.get("name") or "Unnamed"
    addr = garage.get("addr") or ""
    h = int(round(float(garage["height_in"])))
    label = inches_label(h)
    city_name, state_code = city["name"], city["state"]
    state_full = STATE_NAMES.get(state_code, state_code)
    canonical = f"{SITE}{path}"

    title_tag, og_title = build_title(name, label)
    raw_desc = compose_description(
        [f"{name} in {city_name}, {state_full} has a posted vehicle clearance of {label} ({h} inches).",
         verification_sentence(garage), fit_phrase(h)], 160)

    faqs = build_faqs(name, h, label, garage)
    all_anchors = assign_anchors(all_garages)
    candidates = nearby(garage, all_garages, all_paths, all_anchors)

    return PAGE_TEMPLATE.format(
        title_tag=title_tag,
        title=og_title,
        description=esc(raw_desc),
        canonical=canonical,
        site=SITE,
        state_lower=state_code.lower(),
        state_full=esc(state_full),
        state_code=esc(state_code),
        city_slug=city["slug"],
        city_name=esc(city_name),
        name=esc(name),
        h1=f"{esc(name)} parking clearance: {esc(label)}",
        answer=render_answer(name, addr, city_name, state_full, label, h, garage),
        fit_table=render_fit_table(h, label),
        details=render_details(garage),
        links_row=render_links_row(garage, city, anchor),
        nearby_section=render_nearby_section(name, h, candidates, city["slug"]),
        faq_section=render_faq_section(faqs),
        jsonld=build_location_jsonld(city, garage, canonical, label, h, faqs),
        year=date.today().year,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate per-garage clearance pages.")
    parser.add_argument("--city", help="Only regenerate this city slug.")
    parser.add_argument("--verified-only", action="store_true",
                        help="Skip entries that are still unverified bulk imports; pages already "
                             "on disk for the garages this excludes are then deleted by the "
                             "stale-file pass below, since they're no longer in keep_names.")
    parser.add_argument("--dry-run", action="store_true", help="Print counts; write nothing.")
    args = parser.parse_args(argv)

    idx = json.loads(INDEX_PATH.read_text())
    live = [c for c in idx if c.get("status") == "live"]
    if args.city:
        live = [c for c in live if c["slug"] == args.city]

    total_pages = total_deleted = cities_touched = 0

    for city in live:
        data_path = CITIES_DIR / f"{city['slug']}.json"
        if not data_path.exists():
            continue
        data = json.loads(data_path.read_text())
        garages = data.get("garages") or []
        paths = location_paths(city["slug"], garages)
        anchors = assign_anchors(garages)

        keep_names = set()
        n_written = 0
        out_dir = OUT_DIR / city["slug"]
        for g, path, anchor in zip(garages, paths, anchors):
            if path is None:
                continue
            if args.verified_only and entry_verification(g)[0] == "import":
                continue
            slug_name = path.rsplit("/", 1)[1]
            keep_names.add(f"{slug_name}.html")
            n_written += 1
            if not args.dry_run:
                page = generate_location(city, g, path, garages, paths, anchor)
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"{slug_name}.html").write_text(page)

        n_deleted = 0
        if not args.dry_run and out_dir.exists():
            for f in out_dir.glob("*.html"):
                if f.name not in keep_names:
                    f.unlink()
                    n_deleted += 1
            if not any(out_dir.iterdir()):
                out_dir.rmdir()

        total_pages += n_written
        total_deleted += n_deleted
        if n_written or n_deleted:
            cities_touched += 1

    print(f"Generated: {total_pages} pages in {cities_touched} cities (deleted {total_deleted} stale)")


if __name__ == "__main__":
    main()
