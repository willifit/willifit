#!/usr/bin/env python3
"""
WillIFit — per-city SEO page generator.

Problem this solves:
  Our main app is a single-file SPA using hash-based routing (#las-vegas-nv).
  Google Search sees every "city" as the same HTML page because the content
  is loaded via JS fetch.  That makes 226 cities look like 1 page to crawlers.

Fix:
  Generate a real HTML file per city at /city/{slug}.  Each page has:
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

from wf_common import (STATE_NAMES, VEHICLE_CLASSES, MEASURE_NOTE, inches_label,
                       fit_phrase, has_posted_height, slugify, compose_description,
                       import_source_phrase, is_http_url)

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"
OUT_DIR = REPO_ROOT / "city"

SITE = "https://willifit.ai"


def esc(s):
    """HTML-escape, safely handling None."""
    return html.escape(str(s) if s is not None else "", quote=True)


def plural_word(n, word, plural_form=None):
    """'garage' -> 'garage' for n==1, 'garages' otherwise.  Used everywhere
    a count precedes a noun so a city with exactly 1 of something doesn't
    render '1 garages' / '1 have been AI-verified'."""
    return word if n == 1 else (plural_form or word + "s")


def cat_list(total, bridge_word="low bridge"):
    """Grammatical category summary for the combined garage/tunnel/bridge
    count used in the meta description and JSON-LD ItemList description.
    Singular phrasing when the whole city only has one indexed location;
    otherwise the original plural wording, unchanged (callers pass
    bridge_word to match each call site's existing noun choice)."""
    if total == 1:
        return f"parking garage, tunnel, or {bridge_word}"
    return f"parking garages, tunnels, and {bridge_word}s"


def safe_jsonld(obj) -> str:
    """Serialize to compact JSON, then neutralize characters that could
    break out of the <script type="application/ld+json"> block a garage
    name pulled from OSM contains something like '</script>'.  The escapes
    are valid inside a JSON string and inert in HTML, so this round-trips
    through json.loads() back to an identical object -- asserted below on
    every call so a future edit that reorders things can't silently
    reintroduce the injection."""
    raw = json.dumps(obj, separators=(",", ":"))
    escaped = raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    assert "<" not in escaped and ">" not in escaped, \
        "JSON-LD escaping failed to remove angle brackets"
    assert json.loads(escaped) == obj, \
        "JSON-LD escaping altered the payload"
    return escaped


def build_title(name: str, state: str) -> str:
    """<title> text with a length fallback.  Search engines truncate titles
    past ~60 chars (measured on the rendered, entity-decoded text -- what
    actually shows in a browser tab / SERP, not the raw HTML with '&amp;'),
    and the full descriptive form exceeds that for most city names.  Falls
    back to a shorter form, then drops the site suffix, rather than letting
    Google truncate mid-word.  og:title / twitter:title keep the full form
    unchanged -- only the <title> tag uses this."""
    full = f"{name}, {state} Parking &amp; Bridge Clearance Heights | WillIFit.ai"
    if len(html.unescape(full)) <= 60:
        return full
    short = f"{name}, {state} Clearance Heights | WillIFit.ai"
    if len(short) <= 60:
        return short
    return f"{name}, {state} Clearance Heights"


RV_NAME_RE = re.compile(r'\b(rv park|rv resort|caravan|kampground|koa)\b', re.IGNORECASE)

MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_date(iso: str) -> str:
    """'2026-04-23' -> 'Apr 23, 2026'.  Absolute, not relative: a static page
    is cached and crawled long after build, so 'verified 2 years ago' would
    rot while an absolute date stays correct."""
    try:
        y, m, d = iso.split("-")
        return f"{MONTHS[int(m)]} {int(d)}, {y}"
    except Exception:
        return iso or ""


def origin_source(src: str) -> str:
    """Human-meaningful origin of a verified entry.  Verification source
    strings look like 'Web-verified (medium confidence) - was: albuquerquecc.com'
    or 'AI-verified (Street View + Claude Vision — auto-pano) — was: kimotickets.com';
    we want the part after the last 'was:'."""
    if not src:
        return ""
    i = src.lower().rfind("was:")
    return src[i + 4:].strip() if i != -1 else src.strip()


def streetview_url(e: dict) -> str:
    """Google Maps pano URL so anyone can open the exact Street View the
    clearance was read from and check the sign themselves."""
    pano = e.get("pano_id")
    if not pano:
        return ""
    url = f"https://www.google.com/maps/@?api=1&map_action=pano&pano={pano}"
    if isinstance(e.get("pano_heading"), (int, float)):
        url += f"&heading={int(e['pano_heading'])}"
    return url


def entry_verification(e: dict) -> tuple:
    """(kind, verified_on) where kind is 'ai' | 'human' | 'import'.  Mirrors
    the SPA: AI when the source mentions 'AI-verified'; otherwise 'human' when
    it carries a verification date; otherwise an unverified bulk import."""
    src = e.get("source") or ""
    von = e.get("verified_on")
    if "AI-verified" in src:
        return "ai", von
    if von:
        return "human", von
    return "import", None


def verification_sentence(e: dict) -> str:
    """One true sentence about where this entry's number comes from."""
    src = e.get("source") or ""
    kind, von = entry_verification(e)
    if kind == "ai":
        return (f"It was AI-verified from Street View signage on {fmt_date(von)}."
                if von else "It was AI-verified from Street View signage.")
    if kind == "human":
        # The "was: X" suffix in these source strings is the PRIOR source the
        # verification replaced, not what it was checked against -- so it is
        # only usable as an origin when it names a real publisher.
        if src.startswith("Manually verified from Google Street View"):
            return f"It was verified by a person from Google Street View imagery on {fmt_date(von)}."
        if src.startswith("Verified in person"):
            return f"It was verified in person on {fmt_date(von)}."
        if src.startswith("User-observed"):
            return f"It was reported by a user on {fmt_date(von)} and has not been independently verified."
        if src.startswith("Web-verified"):
            origin = verification_origin(e)
            return f"It was verified against {origin or 'a published source'} on {fmt_date(von)}."
        return f"It was verified on {fmt_date(von)}; source: {src.strip() or 'unrecorded'}."
    if src.startswith("Needs verification"):
        return "This figure is unverified and may not reflect the posted sign."
    if "OpenStreetMap" in src:
        return "It is imported from OpenStreetMap and not yet individually verified."
    if "FHWA" in src or "National Bridge" in src:
        return "It is imported from the FHWA National Bridge Inventory and not yet individually verified."
    if src.strip():
        return f"The figure comes from {src.strip()} and has not been individually re-verified."
    return "This figure has no recorded source and has not been verified."


def verification_origin(e: dict) -> str:
    """Publisher a web-verified entry was checked against: the host of
    `source_url` when present, else the 'was: X' origin when X is a real
    publisher (not a placeholder), else ''."""
    url = e.get("source_url") or ""
    if is_http_url(url):
        host = url.split("//", 1)[1].split("/", 1)[0]
        return host[4:] if host.startswith("www.") else host
    origin = origin_source(e.get("source") or "")
    if origin and origin.lower() not in ("needs verification", "openstreetmap") \
            and origin != (e.get("source") or "").strip():
        return origin
    return ""


def verification_summary(entries: list) -> dict:
    """City-level rollup that drives the page's headline pill, lede, meta
    description, the 'how is this verified' FAQ, and the JSON-LD
    dateModified -- so every truth-claim on the page reflects the real data
    instead of a blanket 'AI-verified' that was false for import-only cities."""
    ai = sum(1 for e in entries if "AI-verified" in (e.get("source") or ""))
    verified = sum(1 for e in entries if e.get("verified_on"))
    latest = max((e["verified_on"] for e in entries if e.get("verified_on")),
                 default=None)
    srcs = " ".join((e.get("source") or "") for e in entries)
    return {
        "ai": ai,
        "human": verified - ai,
        "verified": verified,
        "imported": len(entries) - verified,
        "total": len(entries),
        "latest": latest,
        "has_osm": "OpenStreetMap" in srcs,
        "has_nbi": ("FHWA" in srcs or "National Bridge" in srcs),
    }


def provenance_label(ver: dict) -> str:
    """Short, honest provenance string for a city with no verified entries."""
    if ver["has_osm"] and ver["has_nbi"]:
        return "OSM + FHWA NBI data"
    if ver["has_nbi"]:
        return "FHWA NBI data"
    if ver["has_osm"]:
        return "OpenStreetMap data"
    return "Imported data"


def compute_quick_facts(garages: list, tunnels: list, bridges: list) -> dict:
    """Per-city stat block used both for the visible 'Quick facts' card
    and as the source for FAQPage answers below.

    Lowest/highest are computed from GARAGES only -- the FAQs ask about
    parking garages specifically, and pulling bridge/tunnel heights into
    the answer would be misleading (a "Low clearance underpass" tagged
    at 18'7" is not Vegas's tallest garage).  Tunnels + bridges still
    appear in their own page sections below."""
    verified = [g for g in garages if has_posted_height(g)]
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
        "posted_count": len(verified),
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
    if facts["posted_count"] >= 2 and facts["highest"] is not facts["lowest"]:
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


def build_city_intro(name: str, state_full: str, facts: dict, ver: dict,
                     garages: list, tunnels: list, bridges: list) -> str:
    """A unique, data-driven prose paragraph per city — every page gets
    different numbers and different landmark names, which is exactly what
    separates 226 real pages from 226 near-duplicate templates (for both
    search engines and AI answer engines quoting a sentence)."""
    total = len(garages) + len(tunnels) + len(bridges)
    if total == 0:
        return ""
    bits = []
    if facts.get("lowest"):
        e = facts["lowest"]
        bits.append(f"The tightest garage on file is {esc(e.get('name') or 'an unnamed structure')}, "
                    f"posting {esc(e.get('height_label') or '?')}")
        if facts.get("highest") and facts["highest"] is not facts["lowest"]:
            h = facts["highest"]
            bits[-1] += (f", while {esc(h.get('name') or 'another garage')} offers the most room "
                         f"at {esc(h.get('height_label') or '?')}")
        bits[-1] += "."
    lb = [b for b in bridges if isinstance(b.get("height_in"), (int, float)) and b["height_in"] > 0]
    if lb:
        low_b = min(lb, key=lambda b: b["height_in"])
        bits.append(f"The lowest posted bridge clearance in the area is "
                    f"{esc(low_b.get('height_label') or '?')} at {esc((low_b.get('name') or 'an unnamed underpass')[:70])}.")
    if ver.get("ai"):
        have_word = "has" if ver["ai"] == 1 else "have"
        bits.append(f"{ver['ai']} of these clearances {have_word} been AI-verified directly against "
                    f"the posted sign in Google Street View.")
    if facts.get("oversized_count"):
        bits.append(f"{facts['oversized_count']} location{'s' if facts['oversized_count'] != 1 else ''} "
                    f"{'are' if facts['oversized_count'] != 1 else 'is'} flagged oversized-vehicle-friendly.")
    if not bits:
        return ""
    return '<p class="city-intro">' + " ".join(bits) + "</p>"


def build_faqs(city_meta: dict, facts: dict, ver: dict) -> list:
    """Auto-generate 4-5 Q&A pairs per city from its stats.  Used both
    as visible content (an HTML <details> list) AND as FAQPage JSON-LD,
    which is what ChatGPT / Perplexity / AI Overviews cite verbatim.

    `ver` (verification_summary) lets the 'how is this verified' answer tell
    the truth per city: an AI-verified answer for cities with Street-View reads,
    a source-verified answer for web-verified-only cities, and an honest
    'imported, not yet verified' answer for OSM/NBI-only cities -- instead of
    one blanket AI claim that was false for import-only cities."""
    name = city_meta["name"]
    state_full = STATE_NAMES.get(city_meta["state"], city_meta["state"])
    slug = city_meta["slug"]
    faqs = []

    if facts["lowest"]:
        e = facts["lowest"]
        faqs.append({
            "q": f"What's the lowest-clearance parking garage in {name}, {state_full}?",
            "a": (f"The lowest clearance on file for {name} garages is {e.get('height_label')} "
                  f"({int(e.get('height_in'))} inches) at {e.get('name')}. "
                  f"{verification_sentence(e)} {fit_phrase(e['height_in'])} {MEASURE_NOTE}"),
        })
    if facts["posted_count"] >= 2 and facts["highest"] is not facts["lowest"]:
        e = facts["highest"]
        faqs.append({
            "q": f"What's the highest-clearance parking garage in {name}?",
            "a": (f"The highest clearance on file for {name} garages is {e.get('height_label')} "
                  f"({int(e.get('height_in'))} inches) at {e.get('name')}. "
                  f"{verification_sentence(e)} {fit_phrase(e['height_in'])} {MEASURE_NOTE}"),
        })

    if facts["oversized_count"] > 0:
        names = [g.get("name") or "Unnamed" for g in facts["oversized"][:3]]
        joined = ", ".join(names)
        more = f" and {facts['oversized_count'] - len(names)} more" if facts['oversized_count'] > len(names) else ""
        fac_noun = "facility" if facts["oversized_count"] == 1 else "facilities"
        fac_verb = "is" if facts["oversized_count"] == 1 else "are"
        faqs.append({
            "q": f"Which parking facilities in {name} accept RVs or oversized vehicles?",
            "a": (f"{facts['oversized_count']} {fac_noun} in {name} {fac_verb} explicitly marked as "
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
        rv_word = "RV park is" if facts["rv_park_count"] == 1 else "RV parks are"
        faqs.append({
            "q": f"Are there RV parks in {name}?",
            "a": (f"Yes. {facts['rv_park_count']} {rv_word} indexed in {name}, "
                  f"including {joined}{more}. RV parks are open-air sites without a garage "
                  f"ceiling; check each park's own length and height limits."),
        })

    src_phrase = import_source_phrase(ver["has_osm"], ver["has_nbi"])
    if ver["ai"] > 0:
        ai_be = "is" if ver["ai"] == 1 else "are"
        answer = (
            f"{ver['ai']} of the {ver['total']} locations on this page {ai_be} AI-verified: the "
            f"posted clearance was read directly from the entrance sign in Google Street View "
            f"using Claude Vision (Anthropic's image AI), and we store the exact Street View "
            f"pano so you can open it and check the sign yourself."
        )
        if ver["human"] > 0:
            human_be = "was" if ver["human"] == 1 else "were"
            answer += (f" Another {ver['human']} {human_be} verified against a published source "
                       f"such as the facility's own website.")
        if ver["imported"] > 0:
            imp_be = "is" if ver["imported"] == 1 else "are"
            answer += (f" The remaining {ver['imported']} {imp_be} imported from {src_phrase} "
                       f"and {imp_be} not individually verified.")
        answer += " Always confirm at the posted sign before you drive."
    elif ver["verified"] > 0:
        verified_be = "was" if ver["verified"] == 1 else "were"
        answer = (
            f"{ver['verified']} of the {ver['total']} locations on this page {verified_be} verified "
            f"against a published source such as the facility's own website or operator "
            f"listing, with the verification date recorded on each entry."
        )
        if ver["imported"] > 0:
            imp_be = "is" if ver["imported"] == 1 else "are"
            answer += (f" The remaining {ver['imported']} {imp_be} imported from {src_phrase} "
                       f"and {imp_be} not individually verified.")
        answer += " Always confirm at the posted sign before you drive."
    else:
        # Import-only city: be honest -- no Street View / Vision pass here yet.
        clearance_word = plural_word(ver["total"], "clearance", "clearances")
        total_be = "is" if ver["total"] == 1 else "are"
        answer = (
            f"The {ver['total']} {clearance_word} on this page {total_be} imported from {src_phrase}. "
            f"They have not yet been individually verified against Street View, so treat them "
            f"as a starting point and always confirm at the posted sign before you drive. "
            f"Other cities on WillIFit.ai include AI-verified readings taken directly from the "
            f"entrance sign."
        )
    faqs.append({
        "q": "How is the clearance data verified?",
        "a": answer,
    })

    return faqs


def render_faq_section(faqs: list) -> str:
    if not faqs:
        return ""
    items = []
    for f in faqs:
        items.append(
            '<details class="faq-item">'
            f'<summary class="faq-q"><h3>{esc(f["q"])}</h3></summary>'
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
            f'<li><a href="/city/{slug}">{name}, {state}</a>'
            f' <span class="nc-dist">{int(round(d))} mi</span></li>'
        )
    return (
        '<section class="nearby-cities" aria-label="Nearby cities">'
        '<h2>Nearby cities</h2>'
        '<ul class="nc-list">' + ''.join(items) + '</ul>'
        '</section>'
    )


def compute_state_cities(this_city: dict, all_cities: list) -> list:
    """(city, total_locations) tuples for every OTHER live city in the same
    state as this_city, sorted by name.  Complements the (much narrower,
    distance-capped) 'Nearby cities' block above it -- a Las Vegas page
    should link every other live Nevada city, not just the ones within 60
    miles, so 'More cities in Nevada' covers the full state regardless of
    geography."""
    state = this_city.get("state")
    out = []
    for c in all_cities:
        if c.get("slug") == this_city.get("slug"):
            continue
        if c.get("status") != "live" or c.get("state") != state:
            continue
        data_path = CITIES_DIR / f"{c['slug']}.json"
        if not data_path.exists():
            continue
        data = json.loads(data_path.read_text())
        total = (len(data.get("garages") or []) + len(data.get("tunnels") or [])
                + len(data.get("bridges") or []))
        out.append((c, total))
    out.sort(key=lambda x: x[0]["name"])
    return out


def render_state_cities(state_full: str, state_lower: str, cities: list) -> str:
    """'More cities in {State}' block, right after nearby-cities.  When this
    city is the only one indexed in its state, the section still renders --
    just the link to the state overview, no empty list."""
    label = f"More cities in {esc(state_full)}"
    link = f'<p><a href="/state/{state_lower}">All {esc(state_full)} clearance data →</a></p>'
    if not cities:
        return (f'<section class="state-cities" aria-label="{label}">'
                f'<h2>{label}</h2>{link}</section>')
    items = "".join(
        f'<li><a href="/city/{c["slug"]}">{esc(c["name"])}</a>'
        f' <span class="nc-dist">{total} locations</span></li>'
        for c, total in cities
    )
    return (f'<section class="state-cities" aria-label="{label}">'
            f'<h2>{label}</h2>'
            f'<ul class="nc-list">{items}</ul>'
            f'{link}</section>')


def assign_anchors(entries: list) -> list[str]:
    """Stable, unique fragment ids (loc-<id>) for garages+tunnels+bridges in
    page order.  Duplicate ids get -2, -3 ... so the page never has two
    elements with the same id."""
    used, out = set(), []
    for e in entries:
        base = slugify(str(e.get("id") or e.get("name") or "loc"), 60) or "loc"
        anchor = f"loc-{base}"
        n = 2
        while anchor in used:
            anchor = f"loc-{base}-{n}"
            n += 1
        used.add(anchor)
        out.append(anchor)
    return out


def render_entry(e: dict, kind: str, anchor: str, path: str = None) -> str:
    """Render one garage/tunnel/bridge as an HTML <li>.

    Provenance is per-entry, not per-city: a single city page can mix an
    AI-verified garage (blue, links to the exact Street View we read the sign
    from), a human/web-verified entry (green, dated, links to its source), and
    raw OSM/NBI imports (plain 'Source:' line).  This is what lets the page
    tell the truth instead of stamping every row 'AI-verified'.

    `path` is the entry's own /parking/<city>/<slug> page (Task 3), when it
    has one -- the name then links straight there instead of staying plain
    text."""
    name = esc(e.get("name", "Unnamed"))
    addr = esc(e.get("addr", ""))
    height_label = e.get("height_label")
    height_in = e.get("height_in")
    height_str = esc(height_label or "Unverified")
    height_class = "height-verified" if height_in else "height-unverified"
    source = esc(e.get("source", ""))
    notes = esc(e.get("notes", "")[:300])
    oversized = e.get("oversized")
    vkind, von = entry_verification(e)

    tag_parts = []
    if oversized is True:
        tag_parts.append('<span class="tag tag-oversized">Oversized OK</span>')
    if vkind == "ai":
        tag_parts.append('<span class="tag tag-ai">✦ AI-verified</span>')
    elif vkind == "human":
        tag_parts.append('<span class="tag tag-verified">✓ Verified</span>')
    tags = "".join(tag_parts)

    addr_html = f'<div class="entry-addr">{addr}</div>' if addr else ""
    notes_html = f'<div class="entry-notes">{notes}</div>' if notes else ""

    # Verification line replaces the raw "Source:" line for verified entries,
    # which would otherwise just repeat the machine string ("AI-verified
    # (Street View ...) — was: x").  Imports keep the plain source line.
    if vkind == "ai":
        date_txt = f" on {esc(fmt_date(von))}" if von else ""
        sv = streetview_url(e)
        see = (f' · <a href="{esc(sv)}" target="_blank" rel="noopener">see the sign</a>'
               if sv else "")
        verify_html = (
            '<div class="entry-verify entry-verify-ai">'
            'AI-verified from Google Street View'
            f'{date_txt}{see}'
            ' · <a href="/how-ai-verification-works.html">how we verify this</a></div>'
        )
    elif vkind == "human":
        date_txt = f"Verified on {esc(fmt_date(von))}" if von else "Verified"
        origin = verification_origin(e)
        src_url = e.get("source_url")
        if origin and src_url:
            origin_html = (f' · source: <a href="{esc(src_url)}" target="_blank" '
                           f'rel="noopener">{esc(origin)}</a>')
        elif origin:
            origin_html = f' · source: {esc(origin)}'
        else:
            origin_html = ""
        verify_html = f'<div class="entry-verify">{date_txt}{origin_html}</div>'
    else:
        verify_html = f'<div class="entry-source">Source: {source}</div>'

    name_html = f'<a href="{esc(path)}">{name}</a>' if path else name

    return (
        f'<li class="entry entry-{kind}" id="{esc(anchor)}">'
        f'<div class="entry-head">'
        f'<h3 class="entry-name">{name_html}</h3>'
        f'<div class="entry-height {height_class}">{height_str}</div>'
        f'</div>'
        f'{addr_html}'
        f'<div class="entry-tags">{tags}</div>'
        f'{notes_html}'
        f'{verify_html}'
        f'</li>'
    )


def build_jsonld(city: dict, garages: list, tunnels: list, bridges: list, anchors: list,
                 faqs: list = None, latest_verified: str = None, paths: list = None) -> str:
    """Build JSON-LD structured data for the city + entries.
    Gives Google enough detail to render rich snippets.

    `anchors` is the full, aligned list of per-entry fragment ids from
    `assign_anchors(garages + tunnels + bridges)` -- each ListItem's url
    deep-links straight to its <li> on the page instead of just the city.

    `paths` (Task 3), when given, is the same-length aligned list of each
    entry's own /parking/<city>/<slug> page or None -- a garage with a page
    gets that as its ListItem url instead of the in-page anchor.

    `latest_verified` (max verified_on across the city's entries) becomes a
    WebPage.dateModified.  It's emitted ONLY when there's a real verification
    date -- if we stamped dateModified on every build it would churn on every
    regen and train crawlers to ignore it, defeating the honest lastmod signal
    sitemap.xml already provides."""
    name = city["name"]
    state = city["state"]
    state_full = STATE_NAMES.get(state, state)
    slug = city["slug"]
    total = len(garages) + len(tunnels) + len(bridges)

    items = []
    rank = 1
    all_entries = ([(e, "ParkingFacility") for e in garages]
                   + [(e, "Place") for e in tunnels]
                   + [(e, "Bridge") for e in bridges])
    paths = paths if paths is not None else [None] * len(all_entries)
    for (e, kind), anchor, path in zip(all_entries, anchors, paths):  # every entry -- no cap
        address = {"@type": "PostalAddress"}
        if e.get("addr"):
            address["streetAddress"] = e["addr"]
        address["addressLocality"] = name
        address["addressRegion"] = state
        address["addressCountry"] = "US"
        item = {
            "@type": "ListItem",
            "position": rank,
            "item": {
                "@type": kind,
                "name": e.get("name", "Unnamed"),
                "url": f"{SITE}{path}" if path else f"{SITE}/city/{slug}#{anchor}",
                "address": address,
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
        "description": f"{total} {cat_list(total, 'low-clearance bridge')} "
                       f"with posted vehicle clearance heights in {name}, {state_full}.",
        "itemListElement": items,
        "numberOfItems": len(items),
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
            {"@type": "ListItem", "position": 3, "name": state_full,
             "item": f"{SITE}/state/{state.lower()}"},
            {"@type": "ListItem", "position": 4, "name": f"{name}, {state_full}",
             "item": f"{SITE}/city/{city['slug']}"},
        ],
    }
    blocks = [item_list, breadcrumbs]
    if latest_verified:
        blocks.append({
            "@context": "https://schema.org",
            "@type": "WebPage",
            "@id": f"{SITE}/city/{city['slug']}",
            "url": f"{SITE}/city/{city['slug']}",
            "name": f"Parking clearance heights in {name}, {state_full}",
            "dateModified": latest_verified,
        })
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
<meta name="keywords" content="parking clearance {city}, {city} garage heights, low bridges {city}, RV parking {city}, truck clearance {city}, oversized vehicle parking">
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
  /* Tap targets: WCAG 2.2 min 24x24, aim 44px on mobile.  Padding + flex
     keeps the hit area generous without visually enlarging the small text. */
  header a.brand, header a.crumb {{
    display: inline-flex; align-items: center;
    min-height: 24px; padding: 6px 4px; margin: -6px -4px;
  }}
  h1 {{ font-size: 30px; letter-spacing: -0.02em; margin: 8px 0 12px; }}
  h2 {{ font-size: 20px; letter-spacing: -0.01em;
        margin: 40px 0 16px; padding-bottom: 6px;
        border-bottom: 1px solid var(--border); }}
  .lede {{ color: var(--muted); font-size: 16px; max-width: 640px; }}
  .city-intro {{ color: var(--text); font-size: 15px; max-width: 720px;
                 margin: 18px 0 6px; line-height: 1.7; }}
  .ai-pill {{
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    background: rgba(14,165,233,0.15); border: 1px solid rgba(14,165,233,0.35);
    color: #7dd3fc; font-size: 10px; font-weight: 700;
    letter-spacing: 0.06em; text-transform: uppercase; vertical-align: 2px;
  }}
  /* Provenance-pill variants: green for source-verified-only cities, muted
     for import-only cities.  The base .ai-pill (blue) stays for AI cities. */
  .ai-pill.verified {{
    background: rgba(62,207,142,0.15); border-color: rgba(62,207,142,0.35);
    color: #6ee7b7;
  }}
  .ai-pill.imported {{
    background: rgba(138,149,166,0.12); border-color: rgba(138,149,166,0.3);
    color: var(--muted);
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
  .tag-verified {{ background: rgba(62,207,142,0.12); color: var(--ok);
                   border: 1px solid rgba(62,207,142,0.3); }}
  .entry-notes {{ color: var(--muted); font-size: 13px; margin: 8px 0 0; }}
  .entry-source {{ color: var(--muted); font-size: 11px; margin: 8px 0 0; font-style: italic; }}
  /* Per-entry verification line: green for human/web-verified, blue (-ai)
     for Street-View+Vision reads.  Links inherit the line colour so the
     'see the sign' / source links don't fight the accent palette. */
  .entry-verify {{ color: var(--ok); font-size: 12px; margin: 8px 0 0; }}
  .entry-verify a {{ color: inherit; text-decoration: underline; }}
  .entry-verify-ai {{ color: var(--accent); }}
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
    display: inline-flex; align-items: center;
    min-height: 24px; padding: 4px 2px; margin: -4px -2px;
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

  /* Tap targets: 44px min height on small/mobile viewports (WCAG 2.2). */
  @media (max-width: 600px) {{
    header a.brand, header a.crumb {{
      min-height: 44px; padding: 12px 4px; margin: -12px -4px;
    }}
    .sponsor-cta {{ min-height: 44px; padding: 10px 2px; margin: -10px -2px; }}
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
    <a href="/#{slug}" class="crumb">{city}, {state}</a>
  </header>

  <main id="main">
  {pill}
  <h1>Parking clearance heights in {city}, {state_full}</h1>
  <p class="lede">{lede}</p>

  <div class="stats">
    <div class="stat"><b>{garage_count}</b> {garage_word}</div>
    <div class="stat"><b>{tunnel_count}</b> {tunnel_word}</div>
    <div class="stat"><b>{bridge_count}</b> {bridge_word}</div>
  </div>

  {intro}

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

  <!-- More-cities-in-state cross-link block.  Unlike nearby-cities (60 mi
       radius cap), this links every other live city in the same state. -->
  {state_cities}

  <div class="disclaimer">
    <b>⚠ Always verify at the sign.</b>
    Posted clearances on this page are for planning. The only authoritative number
    is the sign at the garage entrance or bridge approach. Clearances can change
    due to re-paving, renovations, or weather. If you spot an inaccuracy,
    <a href="/#{slug}">open the map</a> and use the "Report clearance" button.
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


def render_section(title: str, entries: list, kind: str, anchors: list, paths: list = None) -> str:
    if not entries:
        return f'<h2>{title} (0)</h2><div class="empty">None indexed in this city yet.</div>'
    paths = paths if paths is not None else [None] * len(entries)
    items_html = "\n".join(render_entry(e, kind, a, p) for e, a, p in zip(entries, anchors, paths))
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

    # Stable per-entry fragment ids, computed once in page order (garages,
    # then tunnels, then bridges) and sliced for each render_section() call
    # below so every <li> and its JSON-LD ListItem share the same anchor.
    all_entries = garages + tunnels + bridges
    anchors = assign_anchors(all_entries)

    # Task 3: per-garage /parking/<city>/<slug> pages.  Imported inside this
    # function, not at module level -- generate_location_pages imports
    # assign_anchors etc. FROM this module, so a top-level import here would
    # be circular.  Only garages get their own page; tunnels/bridges pad with
    # None so build_jsonld's paths list stays aligned with all_entries.
    from generate_location_pages import location_paths
    garage_paths = location_paths(slug, garages)

    # Per-city verification rollup drives every truth-claim on the page: the
    # headline pill, the lede, the meta description, the 'how is this verified'
    # FAQ, and the JSON-LD dateModified.  Without it the page stamped a blanket
    # 'AI-verified' even on import-only cities (e.g. Akron: all OSM/NBI, zero
    # Street-View reads).
    ver = verification_summary(all_entries)

    # Quick-facts stat block + auto-generated FAQs feed both visible content
    # and the FAQPage JSON-LD (cited by ChatGPT / Perplexity / AI Overviews).
    facts = compute_quick_facts(garages, tunnels, bridges)
    faqs = build_faqs(city, facts, ver)
    nearby = compute_nearby_cities(city, all_cities or [city]) if all_cities else []
    state_cities_list = compute_state_cities(city, all_cities or [])

    # Build a short paragraph describing what's on the page, for meta + lede.
    # plural_word keeps this from reading "1 parking garages" in cities with
    # exactly one of a given type.
    parts = []
    if garages:
        parts.append(f"{len(garages)} {plural_word(len(garages), 'parking garage')}")
    if tunnels:
        parts.append(f"{len(tunnels)} {plural_word(len(tunnels), 'tunnel')}")
    if bridges:
        parts.append(f"{len(bridges)} {plural_word(len(bridges), 'low-clearance bridge')}")
    locations = ", ".join(parts) if parts else "parking garages, tunnels, and low bridges"

    # Per-entry-truthful lede + description: state exactly how many entries
    # are AI-verified / source-verified / imported instead of one blanket
    # adjective, so a page never claims "verified" for data that is only
    # an unreviewed OSM/NBI import.
    src_phrase = import_source_phrase(ver["has_osm"], ver["has_nbi"])
    prov = []
    if ver["ai"]:
        prov.append(f"{ver['ai']} AI-verified from Street View signage")
    if ver["human"]:
        prov.append(f"{ver['human']} verified against published sources")
    if ver["imported"]:
        prov.append(f"{ver['imported']} imported from {src_phrase}")
    lede = (f"{locations} in {name}, {state_full}: {'; '.join(prov)}. "
            f"Enter your vehicle height on the interactive map to see what fits.")
    if not prov:
        lede = (f"No indexed locations in {name}, {state_full} yet. "
                f"Enter your vehicle height on the interactive map to see what fits.")
    if ver["ai"]:
        claim = f"{ver['ai']} AI-verified from Street View signage."
    elif ver["verified"]:
        claim = f"{ver['verified']} verified against published sources."
    else:
        claim = f"Imported from {src_phrase}."
    description = compose_description(
        [f"Clearance heights for {total} {cat_list(total)} in {name}, {state_full}.",
         claim, "Check before you drive."], 160)
    # Front-load the city name and keep ~60 chars so SERPs show the whole
    # thing (the old form ran 77-93 chars and truncated mid-title).  og:title
    # / twitter:title keep this full form; <title> uses title_tag below,
    # which falls back to shorter forms past 60 chars (151/226 cities
    # exceeded it with this one).
    title = f"{name}, {state} Parking &amp; Bridge Clearance Heights | WillIFit.ai"
    title_tag = build_title(name, state)

    # Headline provenance pill: blue AI badge, green verified badge, or a muted
    # source label -- never a blanket 'AI-verified' on import-only data.
    if total == 0:
        pill = ""
    elif ver["ai"] > 0:
        pill = f'<span class="ai-pill">✦ {ver["ai"]} AI-verified</span>'
    elif ver["verified"] > 0:
        pill = f'<span class="ai-pill verified">✓ {ver["verified"]} verified</span>'
    else:
        pill = f'<span class="ai-pill imported">{esc(provenance_label(ver))}</span>'

    # A city with zero indexed locations is thin content -- noindex it so it
    # can't dilute the site's quality signal, but keep "follow" so the
    # nearby-cities links still pass equity.  generate_sitemap.py drops these
    # same pages from sitemap.xml, so the two stay consistent.
    robots = "noindex,follow" if total == 0 else "index,follow"

    page = PAGE_TEMPLATE.format(
        title=title,
        title_tag=title_tag,
        description=esc(description),
        robots=robots,
        canonical=f"{SITE}/city/{slug}",
        site=SITE,
        slug=slug,
        city=esc(name),
        state=esc(state),
        state_full=esc(state_full),
        state_lower=state.lower(),
        pill=pill,
        lede=esc(lede),
        intro=build_city_intro(name, state_full, facts, ver, garages, tunnels, bridges),
        garage_count=len(garages),
        tunnel_count=len(tunnels),
        bridge_count=len(bridges),
        garage_word=plural_word(len(garages), "parking garage"),
        tunnel_word=plural_word(len(tunnels), "tunnel"),
        bridge_word=plural_word(len(bridges), "low bridge"),
        garages_section=render_section("Parking garages", garages, "garage",
                                       anchors[:len(garages)], garage_paths),
        tunnels_section=render_section("Tunnels", tunnels, "tunnel",
                                       anchors[len(garages):len(garages) + len(tunnels)]),
        bridges_section=render_section("Low-clearance bridges", bridges, "bridge",
                                       anchors[len(garages) + len(tunnels):]),
        quick_facts=render_quick_facts(facts),
        faq_section=render_faq_section(faqs),
        nearby_cities=render_nearby_cities(nearby),
        state_cities=render_state_cities(state_full, state.lower(), state_cities_list),
        year=date.today().year,
        jsonld=build_jsonld(city, garages, tunnels, bridges, anchors, faqs=faqs,
                            latest_verified=ver["latest"],
                            paths=garage_paths + [None] * (len(tunnels) + len(bridges))),
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
