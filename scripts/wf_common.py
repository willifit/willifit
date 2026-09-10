#!/usr/bin/env python3
"""Shared helpers for the WillIFit page generators (city, state, parking,
bridges, llms.txt).  One definition of the vehicle classes and the state
names so every page tells the same story."""
from __future__ import annotations

import html
import re
import unicodedata

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

# (display name, height in inches).  Order matters: ascending height.  Every
# figure below traces to a sourced row in vehicle-heights.html (data-in /
# data-in-max); see tests/test_vehicle_heights_page.py for the guard that
# keeps these >= the tallest sourced vehicle in each class.
VEHICLE_CLASSES = [
    ("a typical sedan", 60),
    ("a stock pickup or SUV", 78),
    ("a low-roof cargo van", 93),             # Transit 83, Express 85, ProMaster 93 -> tallest
    ("a mid-roof cargo van", 100),
    ("a 10–12 ft rental truck", 108),        # U-Haul 10' and Budget 12': 9'0" clearance height
    ("a high-roof Sprinter or Transit", 110),  # Transit 110, Sprinter 107 -> tallest
    ("a 15–20 ft rental truck", 132),        # U-Haul 15'/17'/20' and Budget 16': 11'0"
    ("a Class B camper van", 132),            # sourced range 8.5-11 ft -> up to 132 in
    ("a Class C RV", 138),
    ("a 26 ft rental truck", 162),           # U-Haul 12'0", Budget 13'0", Penske 13'6" -> largest
    ("a semi trailer", 162),
]

MEASURE_NOTE = "Measure your own vehicle; racks, AC units, and lifts add inches."


def esc(s) -> str:
    return html.escape(str(s) if s is not None else "", quote=True)


def is_http_url(value) -> bool:
    """True only for a real http(s) URL, not a bare hostname like
    "harryreidairport.com" or a malformed string that merely starts with
    "http" (e.g. "http:example.com").  Guards every place that later does
    `value.split("//", 1)[1]` -- without the "//" check that indexing would
    raise IndexError on a malformed source_url/website field."""
    return bool(re.match(r"^https?://", str(value or "")))


def inches_label(height_in) -> str:
    h = int(round(float(height_in)))
    return f"{h // 12}'{h % 12}\""


def has_posted_height(e: dict) -> bool:
    h = e.get("height_in")
    src = e.get("source") or ""
    return isinstance(h, (int, float)) and h > 0 and not src.startswith("Needs verification")


def fit_phrase(height_in) -> str:
    """One sentence that is true for every clearance value.  Never says a
    class fits unless its height is <= the clearance."""
    h = int(round(float(height_in)))
    fits = [(n, need) for (n, need) in VEHICLE_CLASSES if need <= h]
    blocks = [(n, need) for (n, need) in VEHICLE_CLASSES if need > h]
    if not fits:
        return ("That is below the 5'0\" of a typical sedan; treat it as "
                "unsuitable for anything taller than a compact car.")
    if not blocks:
        return "That clears every common vehicle class, including a 13'6\" semi trailer."
    top_name, top_h = fits[-1]
    nxt_name, nxt_h = blocks[0]
    shorter = " and anything shorter" if len(fits) > 1 else ""
    taller = " or anything taller" if len(blocks) > 1 else ""
    return (f"That clears {top_name} ({inches_label(top_h)}){shorter}, "
            f"but not {nxt_name} ({inches_label(nxt_h)}){taller}.")


def slugify(text: str, max_len: int = 60) -> str:
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    t = t.replace("&", " and ").lower()
    t = re.sub(r"['’.]", "", t)          # Binion's -> binions, St. -> st (no dangling "-s")
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    t = t[:max_len].rstrip("-")
    return t or "garage"


def clip(text: str, limit: int) -> str:
    """Cut at a word boundary so meta descriptions never end mid-word.

    Only backs off to the previous space when the plain `text[:limit]` cut
    actually lands inside a word (the character right after the cut is not
    itself a space/end-of-string) -- otherwise a cut that already lands
    exactly on a word boundary would still drop a whole trailing word for
    no reason."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if text[limit] != " " and " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,;:-")


def compose_description(sentences, limit=160):
    """Join whole sentences into a meta description that never ends
    mid-sentence.  Drops trailing sentences (never the first) until what's
    left fits `limit`, then clips at a word boundary as a last resort."""
    parts = [s for s in sentences if s]
    while len(" ".join(parts)) > limit and len(parts) > 1:
        parts.pop()
    return clip(" ".join(parts), limit)


def import_source_phrase(has_osm: bool, has_nbi: bool) -> str:
    if has_osm and has_nbi:
        return "OpenStreetMap and the FHWA National Bridge Inventory"
    if has_nbi:
        return "the FHWA National Bridge Inventory"
    if has_osm:
        return "OpenStreetMap"
    return "public datasets"
