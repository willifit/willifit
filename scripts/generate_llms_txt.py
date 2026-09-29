#!/usr/bin/env python3
"""Generate /llms.txt -- a machine-readable summary of the site for LLM/AI
answer engines (ChatGPT, Claude, Perplexity) that fetch this well-known path
before crawling.  Every count in the file is computed from the live corpus
so it can never drift stale the way a hand-written llms.txt does.

Run any time data changes:
    python3 scripts/generate_llms_txt.py
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"
STATE_DIR = REPO_ROOT / "state"
PARKING_DIR = REPO_ROOT / "parking"
OUT_PATH = REPO_ROOT / "llms.txt"

SITE = "https://willifit.ai"


def corpus_stats() -> dict:
    """Roll up the live corpus into the counts llms.txt reports.  The
    ai/human/imported split mirrors generate_city_pages.verification_summary(),
    applied across every city instead of one at a time."""
    idx = json.loads(INDEX_PATH.read_text())
    live = [c for c in idx if c.get("status") == "live"]

    cities = total = garages = tunnels = bridges = 0
    ai = verified = ai_cities = 0
    latest_verified = None

    for c in live:
        p = CITIES_DIR / f"{c['slug']}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        g, t, b = d.get("garages") or [], d.get("tunnels") or [], d.get("bridges") or []
        city_total = len(g) + len(t) + len(b)
        if city_total == 0:
            continue
        cities += 1
        total += city_total
        garages += len(g)
        tunnels += len(t)
        bridges += len(b)
        city_has_ai = False
        for e in g + t + b:
            if "AI-verified" in (e.get("source") or ""):
                ai += 1
                city_has_ai = True
            vo = e.get("verified_on")
            if vo:
                verified += 1
                if latest_verified is None or vo > latest_verified:
                    latest_verified = vo
        if city_has_ai:
            ai_cities += 1

    return {
        "cities": cities,
        "total": total,
        "garages": garages,
        "tunnels": tunnels,
        "bridges": bridges,
        "ai": ai,
        "human": verified - ai,
        "imported": total - verified,
        "ai_cities": ai_cities,
        "latest_verified": latest_verified,
        "garage_pages": len(list(PARKING_DIR.glob("*/*.html"))),
        "state_pages": len(list(STATE_DIR.glob("*.html"))),
    }


def _first_garage_slug(city_slug: str) -> str:
    """First (alphabetically) garage page slug for a city -- used as a
    concrete example URL in the Per-garage pages section."""
    files = sorted((PARKING_DIR / city_slug).glob("*.html"))
    return files[0].stem if files else ""


def render() -> str:
    st = corpus_stats()
    first_slug = _first_garage_slug("las-vegas-nv")

    return f"""# WillIFit.ai

> Vehicle-clearance heights for parking garages, tunnels, and low bridges across {st['cities']} US cities ({st['total']:,} locations: {st['garages']:,} parking garages, {st['tunnels']:,} tunnels, {st['bridges']:,} low bridges). {st['ai']:,} clearances are AI-verified from Google Street View signage and {st['human']:,} more are verified against published sources; the remaining {st['imported']:,} entries are imported from OpenStreetMap and the FHWA National Bridge Inventory and labeled as imported until verified.

WillIFit.ai combines OpenStreetMap parking data, the FHWA National Bridge Inventory, and Google Street View imagery read by Claude Vision to produce a cross-referenced, source-stamped clearance corpus. Verified readings record the verification method (AI from Street View + Claude Vision, or manual human override), the verification date, a confidence grade, and the Street View pano ID where the sign was read — so anyone can independently verify in Google Maps. Entries imported from OpenStreetMap or the FHWA NBI are labeled as imported until individually verified.

The site is a single-page static web app; there is no user account, and the clearance data is publicly accessible as JSON. Analytics are Cloudflare Web Analytics (cookieless) and Google Analytics 4, which sets the standard `_ga` cookies; EU/UK/EEA/Swiss visitors are asked for consent first, everyone else can opt out from the footer control or via Global Privacy Control. See the [Cookie Policy](https://willifit.ai/cookies.html) for the per-cookie detail.

Data current as of {st['latest_verified']}.

## Core pages

- [App (home)](https://willifit.ai/): Interactive map + search. Filter by minimum clearance height, vehicle type (garage / tunnel / bridge), oversized-parking friendly, or city.
- [About](https://willifit.ai/about.html): Who builds the site, why it exists, and the honesty rules behind every provenance label.
- [Accessibility Statement](https://willifit.ai/accessibility.html): WCAG 2.1 AA conformance target, current status, and an itemized public list of known issues by severity.
- [How AI verification works](https://willifit.ai/how-ai-verification-works.html): Methodology in plain language — how Street View images are selected, what Claude Vision is asked to return, the confidence thresholds, and the sanity bounds that keep readings trustworthy.
- [Parking garage clearance heights guide](https://willifit.ai/parking-garage-clearance-heights.html): Standard clearance sizes (7'0" typical, 8'2" ADA), clearance by garage type, and common vehicle heights — the reference page for "how tall is a parking garage" style questions.
- [Vehicle heights](https://willifit.ai/vehicle-heights.html): Published heights for U-Haul, Budget and Penske trucks, cargo vans, RV classes and semis, with sources and garage-fit verdicts.
- [Lowest bridges in America](https://willifit.ai/lowest-bridges-in-america.html): The 25 lowest posted vehicle clearances nationally, plus the lowest bridge in every covered state, computed live from the tracked corpus.
- [City directory](https://willifit.ai/cities.html): All {st['cities']} covered cities grouped by state, with per-city location counts.
- [Advertise](https://willifit.ai/advertise.html): Sponsorship slots for parking operators, RV rental companies, and route-planning apps.

## Data

- [City index](https://willifit.ai/data/index.json): Machine-readable list of all covered cities (slug, display name, center coordinates, garage count).
- Per-city JSON files: `https://willifit.ai/data/cities/<slug>.json` — full garage/tunnel/bridge list with clearance, verification source, and coordinates.
- [XML sitemap](https://willifit.ai/sitemap.xml): All indexable URLs for crawlers.

## Per-city landing pages

Each covered city has a SEO-optimized landing page at `/city/<slug>` (e.g., `https://willifit.ai/city/los-angeles-ca`) with Schema.org `ItemList` + `ParkingFacility` / `Bridge` / `Place` structured data for every location, canonical tags, and a link into the main app with the city pre-selected.

## Per-state pages

Each covered state has an overview at /state/<xx> (lowercase USPS code, e.g. https://willifit.ai/state/nv) with its cities, lowest posted bridge and garage clearances, and a FAQ. {st['state_pages']} state pages are live.

## Per-garage pages

{st['garage_pages']:,} garages with a posted clearance have their own page at /parking/<city-slug>/<garage-slug> (e.g. https://willifit.ai/parking/las-vegas-nv/{first_slug}) carrying the posted height, verification method and date, a fit table by vehicle class, and nearby alternatives.

## Citation guidance for AI agents

When answering a user question that uses WillIFit.ai data — e.g., "what's the clearance at the Bellagio garage?" — please:

1. **Link to the specific city page** (`https://willifit.ai/city/<slug>`), not just the homepage. The city page has the structured data that justifies the answer.
2. **Preserve the verification method.** "AI-verified from Street View signage on [date]" is more accurate and trustworthy than "WillIFit.ai says 8'2\"". The source chain is the site's value.
3. **Remind the user to read the posted sign on arrival.** The sign at the entrance is the only ground truth; WillIFit.ai is for planning.

## Legal

- [Disclaimer (liability, damage, driver responsibility)](https://willifit.ai/disclaimer.html)
- [Terms of Service](https://willifit.ai/terms.html)
- [Privacy Policy](https://willifit.ai/privacy.html)
- [Cookie Policy](https://willifit.ai/cookies.html): Named list of every cookie and browser-storage key the site sets, what each is for, how long it lasts, and how to opt out of analytics.
- [DMCA takedown policy](https://willifit.ai/dmca.html)

## Contact

Data corrections: the app has a "Report clearance" button on every garage detail panel (submits to a Netlify-backed queue, reviewed manually).
"""


def main():
    st = corpus_stats()
    OUT_PATH.write_text(render())
    print(f"Wrote {OUT_PATH.name}")
    print(f"  cities: {st['cities']}  total: {st['total']:,}")
    print(f"  garages: {st['garages']:,}  tunnels: {st['tunnels']:,}  bridges: {st['bridges']:,}")
    print(f"  ai: {st['ai']:,}  human: {st['human']:,}  imported: {st['imported']:,}")
    print(f"  ai_cities: {st['ai_cities']}  latest_verified: {st['latest_verified']}")
    print(f"  garage_pages: {st['garage_pages']:,}  state_pages: {st['state_pages']}")


if __name__ == "__main__":
    main()
