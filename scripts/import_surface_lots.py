#!/usr/bin/env python3
"""
WillIFit — curated surface-lot importer.

Why this exists:
  Our target audience (RVs, box trucks, oversized vehicles) often literally
  cannot use covered garages -- anything over 7'-ish is locked out.  Surface
  lots (open-air, no overhead) are the only real answer for those drivers,
  but our database has been biased toward garages because that's what our
  other importers + auto_verify focus on.  This script closes the gap.

What it does:
  For each live city, query OSM Overpass for `amenity=parking` entries in
  the city's bbox and keep only the ones most likely to matter to an
  oversized-vehicle driver:

    - Named lots (name or operator tag present)
    - Large capacity (>= 50 spaces)
    - Bus/HGV/motorhome-tagged
    - RV parks (tourism=caravan_site)

  Each kept entry is added to the city JSON as:
    structure_type: "surface_lot"
    height_in:       null
    height_label:    "No limit (uncovered)"
    oversized:       true
    source:          "OpenStreetMap (surface lot)"

  Dedupe against existing entries by:
    - osm-w<way_id> / osm-n<node_id> ID match
    - any existing entry within 75m

Cost:
  Zero.  Overpass is free.  Script sleeps 2s between cities per Overpass
  community etiquette (they 429 you if you hammer the public endpoints).

Usage:
  python3 import_surface_lots.py --slug las-vegas-nv --dry-run
  python3 import_surface_lots.py --slug las-vegas-nv
  python3 import_surface_lots.py --all --dry-run
  python3 import_surface_lots.py --all
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib import request, parse, error

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
USER_AGENT = "willifit-surface-lots/0.1 (RV/oversized parking aggregator)"
REQUEST_TIMEOUT = 60
# 3s/city: earlier 2s runs got 429'd by the primary Overpass endpoint
# partway through a 226-city sweep.  3s is more polite; the mirror
# fallback chain still handles transient 504s / 429s on individual
# cities without aborting.
SLEEP_BETWEEN_CITIES = 3.0

# Radius (in lat/lng degrees) around city center per OSM zoom level.  These
# match the other importer (scripts/overpass_import.py) so the two passes
# cover the same ground.  Roughly:  0.25 -> 28km, 0.15 -> 17km, 0.08 -> 9km.
CITY_RADIUS_DEG = {
    11: 0.25,
    12: 0.15,
    13: 0.08,
}

# A candidate lot within this many meters of an existing entry is treated as
# the same physical location and not re-added.
DEDUPE_METERS = 75.0

# Minimum capacity for "big lot" auto-inclusion when no RV-specific tags
# are set.  Below this, the lot must have another qualifying tag.
MIN_CAPACITY = 50

# Keywords in the lot name that signal oversized-vehicle relevance even
# without formal tagging.
OVERSIZED_NAME_KEYWORDS = (
    "rv", "truck", "semi", "bus", "motorhome", "coach",
    "oversize", "fairground", "stadium",
)

# Keywords in the lot name that indicate institutional or private-use-only
# parking.  Matched case-insensitively with word boundaries.  Anything that
# hits one of these is skipped even if other signals would've qualified it.
INSTITUTIONAL_NAME_KEYWORDS = (
    # schools
    "school", "elementary", "middle school", "high school", "college",
    "university", "academy", "kindergarten", "district", "student", "faculty",
    # government / civic
    "courthouse", "city hall", "county", "municipal", "government", "dmv",
    "police", "fire station", "correctional", "prison", "jail",
    "military", "army", "navy", "marine", "air force", "base",
    # private / employee-only
    "staff", "employee", "teacher", "administration", "admin",
    # religious
    "church", "temple", "mosque", "synagogue",
    # healthcare (patients only — adjacent not oversized-friendly)
    "hospital", "clinic",
)

# OSM amenity and landuse values that identify "this is institutional land,
# any parking within it is private/limited".  A candidate within
# INSTITUTIONAL_PROXIMITY_M of an element tagged like this is rejected.
INSTITUTIONAL_AMENITIES = {
    "school", "college", "university", "kindergarten",
    "hospital", "clinic",
    "prison", "police", "fire_station", "courthouse",
    "townhall", "post_office",
    "place_of_worship",
}
INSTITUTIONAL_LANDUSES = {
    "education", "military",
}

# Distance in meters; lots closer than this to the centroid of an
# institutional polygon are skipped.  200m is generous -- a campus
# parking lot can easily sit 150m+ from the school building itself.
INSTITUTIONAL_PROXIMITY_M = 200.0


def build_query(lat: float, lng: float, radius_deg: float) -> str:
    """Overpass QL for parking + RV-park candidates AND institutional
    polygons (schools / hospitals / military / etc.) in a single bbox call.

    Candidates and institutional areas come back in the same element list;
    Python splits them afterward so each candidate can be tested for
    proximity to an institution.

    We over-query here (everything amenity=parking with any of: name,
    capacity, bus, hgv, motorhome) and let Python filter -- bbox queries
    are cheap; the filter logic changes more often than the bbox does."""
    south = lat - radius_deg
    west = lng - radius_deg
    north = lat + radius_deg
    east = lng + radius_deg
    bbox = f"{south},{west},{north},{east}"
    amen_alt = "|".join(sorted(INSTITUTIONAL_AMENITIES))
    land_alt = "|".join(sorted(INSTITUTIONAL_LANDUSES))
    # Previously ran 5 tag-specific parking subqueries over the same
    # bbox (name / capacity / bus / hgv / motorhome) which hammered
    # the Overpass index 5x per city and started 429'ing on 226-city
    # sweeps.  Now one parking subquery; qualifies() re-applies the
    # same tag filters client-side.  Slightly bigger download per
    # city (~1MB vs ~200KB) but ~3x fewer Overpass queries.
    return f"""
[out:json][timeout:60];
(
  way["amenity"="parking"]({bbox});
  way["tourism"="caravan_site"]({bbox});
  node["tourism"="caravan_site"]({bbox});
  way["amenity"~"^({amen_alt})$"]({bbox});
  way["landuse"~"^({land_alt})$"]({bbox});
);
out center tags;
"""


def fetch_overpass(query: str) -> dict:
    """Try each Overpass mirror in turn; raise on total failure."""
    data = parse.urlencode({"data": query}).encode("ascii")
    last_err: Optional[Exception] = None
    for url in OVERPASS_MIRRORS:
        try:
            req = request.Request(
                url,
                data=data,
                headers={"User-Agent": USER_AGENT},
                method="POST",
            )
            with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            print(f"    Overpass fail on {url}: {e}", file=sys.stderr)
            time.sleep(1)
    raise last_err if last_err else RuntimeError("Overpass: all mirrors failed")


def element_latlng(el: dict) -> Optional[tuple[float, float]]:
    if el.get("type") == "node":
        return (el["lat"], el["lon"])
    c = el.get("center")
    if c:
        return (c["lat"], c["lon"])
    return None


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _name_hits_institutional_blacklist(name: str) -> Optional[str]:
    """Return the offending keyword if the name reads as institutional /
    private-use, else None."""
    nl = (name or "").lower()
    if not nl:
        return None
    for kw in INSTITUTIONAL_NAME_KEYWORDS:
        # Word-boundary so "district" doesn't match "districted" (there's
        # no such word but you get the idea) and "coach" (good) doesn't
        # collide with "Coach USA" (fine -- still bus operator).
        if re.search(r"\b" + re.escape(kw) + r"\b", nl):
            return kw
    return None


def qualifies(tags: dict) -> Optional[str]:
    """Return a human-readable reason if this tag set qualifies as
    oversized-friendly, or None to skip.

    Institutional-name blacklist runs first -- a "Bus Parking" lot owned
    by a school district still reads as bus-friendly via the keyword path,
    but the name check here kills it.  We check both the `name` and
    `operator` tags so a generically-named lot ("Lot U") with an
    institutional operator ("University of Nevada Las Vegas") is skipped."""
    name = tags.get("name") or ""
    operator = tags.get("operator") or ""
    if _name_hits_institutional_blacklist(name):
        return None
    if _name_hits_institutional_blacklist(operator):
        return None

    if tags.get("tourism") == "caravan_site":
        return "RV park (OSM tourism=caravan_site)"
    if tags.get("motorhome") == "yes":
        return "motorhome-friendly (OSM motorhome=yes)"
    if tags.get("bus") == "yes":
        return "bus-friendly (OSM bus=yes)"
    if tags.get("hgv") == "yes":
        return "HGV-friendly (OSM hgv=yes)"

    try:
        cap = int(tags.get("capacity", "0"))
    except (TypeError, ValueError):
        cap = 0
    if cap >= MIN_CAPACITY:
        return f"large capacity ({cap} spaces)"

    # Word-boundary match so we don't pick up "rv" inside "obseRVation" etc.
    nl = name.lower()
    for kw in OVERSIZED_NAME_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", nl):
            return f"name signals oversized use ('{kw}')"

    return None


def normalize_to_entry(el: dict, reason: str) -> Optional[dict]:
    tags = el.get("tags", {}) or {}

    # parking type -- surface-only pass (multi-storey/underground are out
    # of scope here; they're handled by auto_verify.py's height pipeline).
    # caravan_site entries don't have a parking= tag, so we accept those too.
    parking_type = (tags.get("parking") or "").lower()
    is_caravan = tags.get("tourism") == "caravan_site"
    if not is_caravan and parking_type and parking_type not in ("surface", ""):
        return None

    access = (tags.get("access") or "").lower()
    if access in ("private", "no", "permit", "customers"):
        return None

    ll = element_latlng(el)
    if not ll:
        return None
    lat, lng = ll

    name = tags.get("name") or tags.get("operator")
    if not name:
        if is_caravan:
            name = "RV Park (unnamed)"
        else:
            return None  # require a name for non-RV lots
    # Skip degenerate names (pure digits like "115" are almost always lot
    # numbers in OSM, not useful to a driver reading a list).
    name_str = str(name).strip()
    if len(name_str) < 3 or name_str.isdigit():
        return None

    osm_id = el.get("id")
    osm_type = el.get("type", "way")
    if not osm_id:
        return None
    entry_id = f"osm-{osm_type[0]}{osm_id}"  # osm-w123 or osm-n123

    addr_parts = [tags.get("addr:housenumber"), tags.get("addr:street")]
    addr = " ".join(p for p in addr_parts if p).strip() or tags.get("addr:full") or ""

    note_bits = [f"Imported 2026 from OSM as an oversized-vehicle-friendly surface lot: {reason}."]
    if tags.get("capacity"):
        note_bits.append(f"{tags['capacity']} spaces.")
    if tags.get("fee") == "yes":
        note_bits.append("Paid parking.")
    elif tags.get("fee") == "no":
        note_bits.append("Free parking.")
    if tags.get("operator"):
        note_bits.append(f"Operator: {tags['operator']}.")

    return {
        "id": entry_id,
        "name": name,
        "addr": addr,
        "lat": round(lat, 5),
        "lng": round(lng, 5),
        "height_in": None,
        "height_label": "No limit (uncovered)",
        "oversized": True,
        "structure_type": "surface_lot",
        "notes": " ".join(note_bits),
        "source": "OpenStreetMap (surface lot)",
    }


def process_city(slug: str, city_meta: dict, dry_run: bool = False) -> int:
    data_path = CITIES_DIR / f"{slug}.json"
    if not data_path.exists():
        print(f"[{slug}] no data file, skip")
        return 0

    data = json.loads(data_path.read_text())
    garages = data.get("garages", [])

    existing_ids = {g.get("id") for g in garages if g.get("id")}
    existing_coords: list[tuple[float, float]] = [
        (g["lat"], g["lng"]) for g in garages if g.get("lat") is not None and g.get("lng") is not None
    ]

    zoom = int(city_meta.get("zoom", 12))
    radius = CITY_RADIUS_DEG.get(zoom, 0.15)
    lat = city_meta["lat"]
    lng = city_meta["lng"]

    try:
        resp = fetch_overpass(build_query(lat, lng, radius))
    except Exception as e:
        print(f"[{slug}] Overpass fetch failed: {e}")
        return 0

    elements = resp.get("elements", [])

    # Split the response: candidate parking lots vs institutional polygons.
    # A single element can technically be both (a parking lot tagged with
    # amenity=parking inside an element also tagged landuse=education),
    # but Overpass returns them as separate elements here.
    institutional_centers: list[tuple[float, float]] = []
    candidates: list[dict] = []
    for el in elements:
        tags = el.get("tags", {}) or {}
        amen = tags.get("amenity")
        land = tags.get("landuse")
        if amen in INSTITUTIONAL_AMENITIES or land in INSTITUTIONAL_LANDUSES:
            ll = element_latlng(el)
            if ll:
                institutional_centers.append(ll)
            continue
        if amen == "parking" or tags.get("tourism") == "caravan_site":
            candidates.append(el)

    def _inside_institution(lat: float, lng: float) -> bool:
        for (ilat, ilng) in institutional_centers:
            if haversine_m(lat, lng, ilat, ilng) < INSTITUTIONAL_PROXIMITY_M:
                return True
        return False

    added: list[dict] = []
    skipped_filter = 0
    skipped_inst = 0
    skipped_dup = 0
    for el in candidates:
        tags = el.get("tags", {}) or {}
        reason = qualifies(tags)
        if not reason:
            skipped_filter += 1
            continue
        entry = normalize_to_entry(el, reason)
        if not entry:
            skipped_filter += 1
            continue
        # caravan_site is a strong, explicit "this is an RV park" signal.
        # Skip the institutional-proximity check in that case -- downtown
        # casino RV parks (e.g., Main Street Station in Vegas) often sit
        # within 200m of a courthouse or police station, but they're
        # still real RV destinations and shouldn't be filtered out.
        if tags.get("tourism") != "caravan_site":
            if _inside_institution(entry["lat"], entry["lng"]):
                skipped_inst += 1
                continue
        if entry["id"] in existing_ids:
            skipped_dup += 1
            continue
        too_close = False
        for (elat, elng) in existing_coords:
            if haversine_m(entry["lat"], entry["lng"], elat, elng) < DEDUPE_METERS:
                too_close = True
                break
        if too_close:
            skipped_dup += 1
            continue
        added.append(entry)
        existing_ids.add(entry["id"])
        existing_coords.append((entry["lat"], entry["lng"]))

    if added and not dry_run:
        data["garages"].extend(added)
        data_path.write_text(json.dumps(data, indent=2) + "\n")

    action = "would add" if dry_run else "added"
    print(
        f"[{slug}] {action}: {len(added)} surface lots "
        f"(skipped: {skipped_filter} filter, {skipped_inst} institutional, {skipped_dup} dedup)"
    )
    for entry in added[:6]:
        print(f"  + {entry['id']:<24} {entry['name']}")
    if len(added) > 6:
        print(f"  ... and {len(added) - 6} more")

    return len(added)


def main():
    ap = argparse.ArgumentParser(description="Curated RV-friendly surface-lot importer (OSM)")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--slug", help="Run on one city by slug")
    group.add_argument("--slugs", help="Comma-separated list of slugs")
    group.add_argument("--all", action="store_true", help="Run on every live city")
    ap.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = ap.parse_args()

    idx = json.loads(INDEX_PATH.read_text())
    live = [c for c in idx if c.get("status") == "live"]

    if args.slug:
        cities = [c for c in live if c["slug"] == args.slug]
        if not cities:
            print(f"No live city with slug={args.slug}", file=sys.stderr)
            return 1
    elif args.slugs:
        wanted = {s.strip() for s in args.slugs.split(",") if s.strip()}
        cities = [c for c in live if c["slug"] in wanted]
        missing = wanted - {c["slug"] for c in cities}
        if missing:
            print(f"Unknown slug(s): {sorted(missing)}", file=sys.stderr)
            return 1
    else:
        cities = live

    print(f"{'Dry-run' if args.dry_run else 'Importing'} across {len(cities)} cities...\n")
    total = 0
    for i, city in enumerate(cities, 1):
        n = process_city(city["slug"], city, dry_run=args.dry_run)
        total += n
        if i < len(cities):
            time.sleep(SLEEP_BETWEEN_CITIES)

    action = "Would add" if args.dry_run else "Added"
    print(f"\n{action}: {total} surface lots across {len(cities)} cities.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
