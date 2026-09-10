#!/usr/bin/env python3
"""
WillIFit — per-state hub page generator (SEO/AEO plan, Task 2).

Problem this solves:
  Per-city pages (generate_city_pages.py) give Google 226 unique, indexable
  pages, but a searcher looking for "Nevada bridge clearances" or an AI
  answer engine summarizing statewide coverage has no single page to land
  on or cite — only 5 separate Nevada city pages with no state-level
  rollup.

Fix:
  Generate one real HTML file per state at /state/<xx> (xx = lowercase
  two-letter code).  Each page rolls up every live city in that state:
    - Unique <title>, <meta description>, <link rel=canonical>
    - A "Cities in {State}" table (every live city, garages/tunnels/
      bridges counts, lowest garage clearance, AI-verified count)
    - Statewide "lowest posted clearances" and "lowest garage clearances"
      tables, ranked across every city in the state
    - JSON-LD (CollectionPage + BreadcrumbList + ItemList + FAQPage)
    - Links to every city page, and every city page links back here

Run any time data changes:
    python3 scripts/generate_state_pages.py
"""

from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path

from wf_common import (STATE_NAMES, VEHICLE_CLASSES, MEASURE_NOTE, inches_label,
                       fit_phrase, has_posted_height, clip, import_source_phrase)
from generate_city_pages import (assign_anchors, verification_summary, entry_verification,
                                 fmt_date, safe_jsonld, esc, cat_list, plural_word,
                                 render_faq_section, faqs_to_jsonld)

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"
OUT_DIR = REPO_ROOT / "state"

SITE = "https://willifit.ai"


def build_title(state_full: str) -> str:
    """<title> text with a one-step length fallback -- mirrors
    generate_city_pages.build_title().  Falls back to the shorter form when
    the full descriptive title's entity-decoded length (what actually shows
    in a browser tab / SERP, not the raw '&amp;' HTML) exceeds ~60 chars.
    Every state name fits the short form, so unlike the city generator this
    needs only one fallback tier."""
    full = f"{state_full} Parking Garage &amp; Low Bridge Clearances | WillIFit.ai"
    if len(html.unescape(full)) <= 60:
        return full
    return f"{state_full} Clearance Heights | WillIFit.ai"


def _city_stats_row(c: dict, data_by_slug: dict) -> tuple:
    """One city's contribution to a state rollup: its 'Cities in {State}'
    table row, its raw entries (folded into the state-wide verification
    summary), and its candidate rows for the state-wide lowest-bridge /
    lowest-garage / oversized lists.

    Anchors are assigned exactly like the city page does: assign_anchors()
    is called once over this city's own garages+tunnels+bridges (in that
    order) and sliced by position, so a `/city/<slug>#<anchor>` link built
    from this always lands on the same <li> the city page emits."""
    slug = c["slug"]
    data = data_by_slug.get(slug) or {}
    garages = data.get("garages") or []
    tunnels = data.get("tunnels") or []
    bridges = data.get("bridges") or []
    entries = garages + tunnels + bridges

    anchors = assign_anchors(entries)
    garage_anchors = anchors[:len(garages)]
    other_anchors = anchors[len(garages):]  # tunnels, then bridges

    posted_garages = [g for g in garages if has_posted_height(g)]
    lowest_city_garage = min(posted_garages, key=lambda g: g["height_in"]) if posted_garages else None
    row = {
        "slug": slug, "name": c["name"], "total": len(entries),
        "garages": len(garages), "tunnels": len(tunnels), "bridges": len(bridges),
        "lowest_garage_label": (inches_label(lowest_city_garage["height_in"])
                                if lowest_city_garage else None),
        "ai": sum(1 for e in entries if "AI-verified" in (e.get("source") or "")),
    }

    # Statewide "lowest posted clearances" candidates: bridges + tunnels,
    # same 72-168 plausibility floor generate_bridges_page.py uses.
    bridge_candidates = []
    for e, a in zip(tunnels + bridges, other_anchors):
        h = e.get("height_in")
        if isinstance(h, (int, float)) and 72 <= h <= 168:
            bridge_candidates.append({"h": h, "name": e.get("name") or "Unnamed underpass",
                                      "city": c["name"], "slug": slug, "anchor": a})

    # Statewide "lowest garage clearances" candidates: posted heights only.
    garage_candidates = [
        {"name": e.get("name") or "Unnamed", "height_in": e["height_in"],
         "city": c["name"], "slug": slug, "anchor": a, "entry": e}
        for e, a in zip(garages, garage_anchors) if has_posted_height(e)
    ]

    oversized = [(e.get("name") or "Unnamed", c["name"]) for e in garages if e.get("oversized") is True]

    return row, entries, bridge_candidates, garage_candidates, oversized


def state_stats(code: str, cities: list, data_by_slug: dict) -> dict:
    """Per-state rollup: one pass over every live city in `code` (via
    _city_stats_row), gathering the per-city table rows, the state-wide
    verification summary, and the ranked lowest-bridge / lowest-garage
    candidate lists that the page's tables, quick facts, and FAQ answers
    all read from -- so every number on the page and in its JSON-LD traces
    back to this one testable place."""
    state_full = STATE_NAMES.get(code, code)
    state_cities = [c for c in cities if c.get("state") == code and c.get("status") == "live"]

    city_rows, all_entries = [], []
    lowest_bridges, lowest_garages, oversized = [], [], []
    for c in state_cities:
        row, entries, bridge_cands, garage_cands, ov = _city_stats_row(c, data_by_slug)
        city_rows.append(row)
        all_entries.extend(entries)
        lowest_bridges.extend(bridge_cands)
        lowest_garages.extend(garage_cands)
        oversized.extend(ov)

    city_rows.sort(key=lambda r: r["total"], reverse=True)
    lowest_bridges.sort(key=lambda r: r["h"])
    lowest_garages.sort(key=lambda r: r["height_in"])
    ver = verification_summary(all_entries)

    return {
        "code": code,
        "state_full": state_full,
        "cities": city_rows,
        "total": len(all_entries),
        "n_cities": len(city_rows),
        "ver": ver,
        "lowest_bridge": lowest_bridges[0] if lowest_bridges else None,
        "lowest_garage": lowest_garages[0] if lowest_garages else None,
        "lowest_bridges": lowest_bridges[:10],
        "lowest_garages": lowest_garages[:10],
        "oversized": oversized,
        "oversized_count": len(oversized),
        "latest_verified": ver["latest"],
    }


def render_state_quick_facts(stats: dict) -> str:
    """Quick-facts stat block -- same .qf-card markup as the city page's
    quick facts, different cards (statewide instead of per-city)."""
    cards = [
        '<div class="qf-card">'
        '<div class="qf-label">Cities covered</div>'
        f'<div class="qf-value">{stats["n_cities"]}</div>'
        '</div>'
    ]
    lb = stats["lowest_bridge"]
    if lb:
        cards.append(
            '<div class="qf-card">'
            '<div class="qf-label">Lowest posted bridge</div>'
            f'<div class="qf-value">{esc(inches_label(lb["h"]))}</div>'
            f'<div class="qf-detail">{esc(lb["name"])} · {esc(lb["city"])}</div>'
            '</div>'
        )
    lg = stats["lowest_garage"]
    if lg:
        cards.append(
            '<div class="qf-card">'
            '<div class="qf-label">Lowest garage clearance</div>'
            f'<div class="qf-value">{esc(inches_label(lg["height_in"]))}</div>'
            f'<div class="qf-detail">{esc(lg["name"])} · {esc(lg["city"])}</div>'
            '</div>'
        )
    cards.append(
        '<div class="qf-card">'
        '<div class="qf-label">Oversized-friendly</div>'
        f'<div class="qf-value">{stats["oversized_count"]}</div>'
        '<div class="qf-detail">RV / box-truck OK</div>'
        '</div>'
    )
    return '<section class="quick-facts" aria-label="Quick facts">' + ''.join(cards) + '</section>'


def render_state_cities_table(state_full: str, rows: list) -> str:
    """'Cities in {State}' table -- one row per live city, sorted by total
    locations descending (the order state_stats() already sorted `rows`
    into)."""
    trs = []
    for r in rows:
        trs.append(
            '<tr>'
            f'<td><a href="/city/{r["slug"]}">{esc(r["name"])}</a></td>'
            f'<td>{r["garages"]}</td>'
            f'<td>{r["tunnels"]}</td>'
            f'<td>{r["bridges"]}</td>'
            f'<td>{esc(r["lowest_garage_label"] or "—")}</td>'
            f'<td>{r["ai"]}</td>'
            '</tr>'
        )
    state_e = esc(state_full)
    return (
        f'<h2>Cities in {state_e}</h2>'
        f'<div class="table-wrap" tabindex="0" role="region" aria-label="Cities in {state_e}, scrollable table">'
        '<table>'
        '<thead><tr>'
        '<th scope="col">City</th><th scope="col">Garages</th><th scope="col">Tunnels</th>'
        '<th scope="col">Low bridges</th><th scope="col">Lowest garage clearance</th>'
        '<th scope="col">AI-verified</th>'
        '</tr></thead>'
        f'<tbody>{"".join(trs)}</tbody>'
        '</table>'
        '</div>'
    )


def render_lowest_bridges_section(state_full: str, rows: list) -> str:
    """'Lowest posted clearances in {State}' -- up to 10 bridges/tunnels,
    ascending by height.  Omitted entirely when the state has none."""
    if not rows:
        return ""
    trs = []
    for r in rows:
        trs.append(
            '<tr>'
            f'<td><b>{inches_label(r["h"])}</b></td>'
            f'<td><a href="/city/{r["slug"]}#{r["anchor"]}">{esc(r["name"])}</a></td>'
            f'<td><a href="/city/{r["slug"]}">{esc(r["city"])}</a></td>'
            '</tr>'
        )
    state_e = esc(state_full)
    return (
        f'<h2>Lowest posted clearances in {state_e}</h2>'
        f'<div class="table-wrap" tabindex="0" role="region" aria-label="Lowest posted clearances in {state_e}, scrollable table">'
        '<table>'
        '<thead><tr><th scope="col">Posted</th><th scope="col">Structure</th><th scope="col">City</th></tr></thead>'
        f'<tbody>{"".join(trs)}</tbody>'
        '</table>'
        '</div>'
    )


def render_lowest_garages_section(state_full: str, rows: list) -> str:
    """'Lowest garage clearances in {State}' -- up to 10 posted-height
    garages, ascending.  Omitted entirely when the state has none."""
    if not rows:
        return ""
    trs = []
    for r in rows:
        kind, von = entry_verification(r["entry"])
        if kind == "ai":
            verif = f"AI-verified {fmt_date(von)}" if von else "AI-verified"
        elif kind == "human":
            verif = f"Verified {fmt_date(von)}" if von else "Verified"
        else:
            verif = "Imported"
        trs.append(
            '<tr>'
            f'<td><b>{inches_label(r["height_in"])}</b></td>'
            f'<td><a href="/city/{r["slug"]}#{r["anchor"]}">{esc(r["name"])}</a></td>'
            f'<td><a href="/city/{r["slug"]}">{esc(r["city"])}</a></td>'
            f'<td>{esc(verif)}</td>'
            '</tr>'
        )
    state_e = esc(state_full)
    return (
        f'<h2>Lowest garage clearances in {state_e}</h2>'
        f'<div class="table-wrap" tabindex="0" role="region" aria-label="Lowest garage clearances in {state_e}, scrollable table">'
        '<table>'
        '<thead><tr><th scope="col">Posted</th><th scope="col">Garage</th>'
        '<th scope="col">City</th><th scope="col">Verification</th></tr></thead>'
        f'<tbody>{"".join(trs)}</tbody>'
        '</table>'
        '</div>'
    )


def state_verification_faq(state_full: str, ver: dict) -> dict:
    """Same three-way 'how is this verified' answer as the city generator's
    build_faqs(), with 'on this page' replaced by 'in {State}' -- the
    state-wide rollup has no single page's worth of locations, it has a
    state's worth."""
    if ver["ai"] > 0:
        answer = (
            f"{ver['ai']} of the {ver['total']} locations in {state_full} are AI-verified: the "
            f"posted clearance was read directly from the entrance sign in Google Street View "
            f"using Claude Vision (Anthropic's image AI), and we store the exact Street View "
            f"pano so you can open it and check the sign yourself."
        )
        if ver["human"] > 0:
            answer += (f" Another {ver['human']} were verified against a published source "
                       f"such as the facility's own website.")
        if ver["imported"] > 0:
            answer += (f" The remaining {ver['imported']} are imported from OpenStreetMap and "
                       f"the U.S. National Bridge Inventory and are not individually verified.")
        answer += " Always confirm at the posted sign before you drive."
    elif ver["verified"] > 0:
        answer = (
            f"{ver['verified']} of the {ver['total']} locations in {state_full} were verified "
            f"against a published source such as the facility's own website or operator "
            f"listing, with the verification date recorded on each entry."
        )
        if ver["imported"] > 0:
            answer += (f" The remaining {ver['imported']} are imported from OpenStreetMap and "
                       f"the U.S. National Bridge Inventory and are not individually verified.")
        answer += " Always confirm at the posted sign before you drive."
    else:
        src_phrase = import_source_phrase(ver["has_osm"], ver["has_nbi"])
        answer = (
            f"The {ver['total']} clearances in {state_full} are imported from {src_phrase}. "
            f"They have not yet been individually verified against Street View, so treat them "
            f"as a starting point and always confirm at the posted sign before you drive. "
            f"Other cities on WillIFit.ai include AI-verified readings taken directly from the "
            f"entrance sign."
        )
    return {"q": "How is the clearance data verified?", "a": answer}


def build_state_faqs(state_full: str, stats: dict) -> list:
    """Auto-generate the state page's Q&A pairs from its stats -- visible
    <details> content AND FAQPage JSON-LD, same dual role as the city
    generator's build_faqs()."""
    faqs = []

    lb = stats["lowest_bridge"]
    if lb:
        faqs.append({
            "q": f"What is the lowest bridge clearance in {state_full}?",
            "a": (f"The lowest posted bridge or underpass clearance WillIFit tracks in {state_full} "
                  f"is {inches_label(lb['h'])} ({int(lb['h'])} inches) at {lb['name']} in {lb['city']}. "
                  f"{fit_phrase(lb['h'])} {MEASURE_NOTE}"),
        })

    n = stats["n_cities"]
    parts = [f"{c['name']} ({c['total']} {'location' if c['total'] == 1 else 'locations'})"
             for c in stats["cities"]]
    faqs.append({
        "q": f"Which {state_full} cities does WillIFit cover?",
        "a": (f"WillIFit covers {n} {plural_word(n, 'city', 'cities')} in {state_full}: "
              + ", ".join(parts) + "."),
    })

    k = stats["oversized_count"]
    if k > 0:
        names = [f"{name} in {city}" for name, city in stats["oversized"][:3]]
        faqs.append({
            "q": f"Where can an RV or box truck park in {state_full}?",
            "a": (f"{k} indexed {plural_word(k, 'facility', 'facilities')} in {state_full} "
                  f"{'is' if k == 1 else 'are'} marked oversized-vehicle-friendly, including "
                  f"{', '.join(names)}. See each city page for the full list."),
        })
    else:
        faqs.append({
            "q": f"Where can an RV or box truck park in {state_full}?",
            "a": (f"None of the facilities indexed in {state_full} are explicitly marked "
                  f"oversized-vehicle-friendly. RV and box-truck drivers should call ahead "
                  f"or use surface lots."),
        })

    faqs.append(state_verification_faq(state_full, stats["ver"]))
    return faqs


RELATED_HTML = (
    '<section class="related"><h2>Related</h2><ul>'
    '<li><a href="/lowest-bridges-in-america.html">The lowest bridges in America</a></li>'
    '<li><a href="/parking-garage-clearance-heights.html">Standard parking garage clearance heights</a></li>'
    '<li><a href="/cities.html">All covered cities</a></li>'
    '</ul></section>'
)


def build_state_jsonld(state_full: str, canonical: str, description: str,
                       stats: dict, faqs: list) -> str:
    """CollectionPage + BreadcrumbList + ItemList + FAQPage, in that order."""
    collection = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "@id": canonical,
        "url": canonical,
        "name": f"Clearance heights in {state_full}",
        "description": description,
    }
    if stats["latest_verified"]:
        collection["dateModified"] = stats["latest_verified"]

    breadcrumbs = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": "Cities", "item": f"{SITE}/cities.html"},
            {"@type": "ListItem", "position": 3, "name": state_full, "item": canonical},
        ],
    }

    item_list = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": f"Cities in {state_full}",
        "numberOfItems": stats["n_cities"],
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": f"{c['name']}, {state_full}",
             "url": f"{SITE}/city/{c['slug']}"}
            for i, c in enumerate(stats["cities"], start=1)
        ],
    }

    blocks = [collection, breadcrumbs, item_list]
    if faqs:
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
<meta name="robots" content="{robots}">
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
  /* Skip-link for keyboard users -- same visually-hidden-until-focused
     pattern as the main SPA (index.html .skip-link). */
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
  /* Tap targets: WCAG 2.2 min 24x24, aim 44px on mobile. */
  header a.brand, header a.crumb {{
    display: inline-flex; align-items: center;
    min-height: 24px; padding: 6px 4px; margin: -6px -4px;
  }}
  h1 {{ font-size: 30px; letter-spacing: -0.02em; margin: 8px 0 12px; }}
  h2 {{ font-size: 20px; letter-spacing: -0.01em;
        margin: 40px 0 16px; padding-bottom: 6px;
        border-bottom: 1px solid var(--border); }}
  .lede {{ color: var(--muted); font-size: 16px; max-width: 680px; }}

  /* Table CSS -- copied from generate_bridges_page.py so the statewide
     tables match the site's other data tables. */
  table {{ width: 100%; border-collapse: collapse; margin: 18px 0; font-size: 14px; }}
  th, td {{ text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.04em; }}
  tr:last-child td {{ border-bottom: none; }}
  td b {{ color: var(--bad); font-variant-numeric: tabular-nums; }}
  .table-wrap {{ overflow-x: auto; }}

  /* Quick-facts stat block -- same markup as the city page. */
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

  /* FAQ section -- <details>/<summary> for accessibility, FAQPage JSON-LD
     lives in the page head for AI answer engines. */
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

  /* Related links, closing out the page. */
  .related {{ margin-top: 40px; }}
  .related ul {{ list-style: none; padding: 0; margin: 12px 0 0; }}
  .related li {{ margin: 6px 0; font-size: 14px; }}

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

  /* Tap targets: 44px min height on small/mobile viewports (WCAG 2.2). */
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
    <span class="crumb">{state_full}</span>
  </header>

  <main id="main">
  <h1>Clearance heights in {state_full}</h1>
  <p class="lede">{lede}</p>

  {quick_facts}

  {cities_table}

  {lowest_bridges_section}

  {lowest_garages_section}

  <!-- Auto-generated FAQ from state stats.  Visible <details>/<summary>
       for users; FAQPage JSON-LD in the head for AI answer engines. -->
  {faq_section}

  {related}

  <div class="disclaimer">
    <b>⚠ Always verify at the sign.</b>
    Posted clearances on this page are for planning. The only authoritative number
    is the sign at the garage entrance or bridge approach. Clearances can change
    due to re-paving, renovations, or weather. If you spot an inaccuracy,
    <a href="/">open the map</a> and use the "Report clearance" button.
  </div>
  </main>

  <footer>
    <div>© {year} WillIFit.ai — clearance data for RVs, trucks &amp; oversized vehicles.</div>
    <div>
      <a href="/about.html">About</a> ·
      <a href="/accessibility.html">Accessibility</a> ·
      <a href="/how-ai-verification-works.html">How AI verification works</a> ·
      <a href="/parking-garage-clearance-heights.html">Clearance guide</a> ·
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


def _state_lede(total: int, n: int, state_full: str, ver: dict, src_phrase: str) -> str:
    """The lede embeds a live link ("open the interactive map"), so -- unlike
    _state_description below, which has no markup in it -- it can't be
    esc()'d as one finished blob without breaking the link. Each
    data-derived piece is escaped individually and the anchor tag stays
    literal."""
    prov = []
    if ver["ai"]:
        prov.append(f"{ver['ai']} AI-verified from Street View signage")
    if ver["human"]:
        prov.append(f"{ver['human']} verified against published sources")
    if ver["imported"]:
        prov.append(f"{ver['imported']} imported from {esc(src_phrase)}")
    return (f'{total} {esc(cat_list(total, "low-clearance bridge"))} across {n} covered '
            f'{esc(plural_word(n, "city", "cities"))} in {esc(state_full)}: {"; ".join(prov)}. '
            f'Pick a city below for its full list, or <a href="/">open the interactive map</a>.')


def _state_description(total: int, n: int, state_full: str, ver: dict, src_phrase: str) -> str:
    if ver["ai"]:
        claim = f"{ver['ai']} AI-verified from Street View signage."
    elif ver["verified"]:
        claim = f"{ver['verified']} verified against published sources."
    else:
        claim = f"Imported from {src_phrase}."
    return clip(
        f"Vehicle clearance heights for {total} parking garages, tunnels, and low bridges "
        f"across {n} {plural_word(n, 'city', 'cities')} in {state_full}. {claim} Check before you drive.",
        160)


def generate_state(code: str, cities: list, data_by_slug: dict) -> str:
    stats = state_stats(code, cities, data_by_slug)
    state_full = stats["state_full"]
    total = stats["total"]
    n = stats["n_cities"]
    ver = stats["ver"]
    canonical = f"{SITE}/state/{code.lower()}"
    src_phrase = import_source_phrase(ver["has_osm"], ver["has_nbi"])

    lede = _state_lede(total, n, state_full, ver, src_phrase)
    description_raw = _state_description(total, n, state_full, ver, src_phrase)
    faqs = build_state_faqs(state_full, stats)

    return PAGE_TEMPLATE.format(
        title=f"{state_full} Parking Garage &amp; Low Bridge Clearances | WillIFit.ai",
        title_tag=build_title(state_full),
        description=esc(description_raw),
        robots="noindex,follow" if total == 0 else "index,follow",
        canonical=canonical,
        site=SITE,
        state_full=esc(state_full),
        lede=lede,
        quick_facts=render_state_quick_facts(stats),
        cities_table=render_state_cities_table(state_full, stats["cities"]),
        lowest_bridges_section=render_lowest_bridges_section(state_full, stats["lowest_bridges"]),
        lowest_garages_section=render_lowest_garages_section(state_full, stats["lowest_garages"]),
        faq_section=render_faq_section(faqs),
        related=RELATED_HTML,
        year=date.today().year,
        jsonld=build_state_jsonld(state_full, canonical, description_raw, stats, faqs),
    )


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    idx = json.loads(INDEX_PATH.read_text())
    live = [c for c in idx if c.get("status") == "live"]

    data_by_slug = {}
    for c in live:
        p = CITIES_DIR / f"{c['slug']}.json"
        if p.exists():
            data_by_slug[c["slug"]] = json.loads(p.read_text())

    codes = sorted({c["state"] for c in live})
    generated = 0
    for code in codes:
        page = generate_state(code, live, data_by_slug)
        (OUT_DIR / f"{code.lower()}.html").write_text(page)
        generated += 1

    print(f"Generated: {generated}")


if __name__ == "__main__":
    main()
