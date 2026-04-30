#!/usr/bin/env python3
"""
WillIFit — OpenStreetMap Overpass import pipeline.

Pulls parking garages, low-clearance tunnels, and low-clearance bridges from
OpenStreetMap for each city in data/index.json and merges them into
data/cities/{slug}.json without clobbering hand-curated entries.

OSM tags we read (garages):
  amenity=parking         → filter
  parking=multi-storey|underground|garage_boxes → keep
  parking=surface         → keep only if we're looking for oversized lots
  name                    → garage name
  operator                → fallback for name
  addr:housenumber/street → address
  maxheight               → clearance (various formats: "3.5", "3.5 m", "11'6\"", "11ft 6in")
  height                  → backup for maxheight
  access                  → skip if 'private' (many employee-only garages)
  fee                     → pay status (logged in notes)
  capacity                → space count (logged in notes)

OSM tags we read (tunnels/bridges):
  highway=*               → must be a drivable road
  tunnel=yes              → tunnel query
  bridge=yes              → bridge query
  maxheight               → REQUIRED. We only import with a verified posted clearance.
  name / ref              → tunnel/bridge name

Usage:
  python3 overpass_import.py --slug las-vegas-nv --dry-run
  python3 overpass_import.py --slug las-vegas-nv            # writes changes
  python3 overpass_import.py --all --dry-run                # preview all cities
  python3 overpass_import.py --all                          # import all cities
  python3 overpass_import.py --all --types garages,tunnels,bridges

By default --types is garages,tunnels,bridges (import everything). Override to
run just one pass: --types tunnels

Rate limiting: Overpass API public endpoint has a rough 10,000 queries/day soft
limit and will 429 if you hammer it. This script sleeps 2s between cities.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib import request, parse, error

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"

OVERPASS_URL = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
# Public mirrors to try in order when the primary returns 504/timeout.
# The primary gets hammered hardest; kumi.systems and maps.mail.ru are well-run
# community mirrors — see https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances
OVERPASS_MIRRORS = [
    OVERPASS_URL,
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
USER_AGENT = "willifit-importer/0.1 (parking-clearance aggregator)"

# Rough radius around city center, in degrees (~0.1 deg lat ≈ 11 km).
# Big metros (NYC, LA) need wider — we scale by zoom.
CITY_RADIUS_DEG = {
    11: 0.25,   # ~28 km (big metros)
    12: 0.15,   # ~17 km
    13: 0.08,   # ~9 km (small tourist towns)
}

# Minimum meters between a new candidate and an existing entry before we
# consider them duplicates.
DEDUPE_METERS = 75.0


MIN_PLAUSIBLE_INCHES = 48   # 4' — below this is almost certainly a parse error (motorcycle-only etc.)
MAX_PLAUSIBLE_INCHES = 240  # 20' — above this is definitely a parse error for a parking garage

def parse_maxheight(value: str) -> Optional[int]:
    """
    OSM maxheight strings are a mess. Return clearance in inches, or None.
    Handles:
      "3.5"         → meters (OSM default) if ≤ 10, else likely feet
      "3.5 m"       → meters
      "11'6\""     → feet/inches
      "11ft 6in"    → feet/inches
      "default"     → None (no restriction posted)
      "none"        → None
    Values producing <4' or >20' are discarded (clearly parse errors or noise).
    """
    if not value:
        return None
    s = str(value).strip().lower()
    if s in ("default", "none", "no_sign", "unknown", ""):
        return None

    inches: Optional[int] = None

    # Feet + inches: 11'6" or 11' 6" or 11ft 6in
    ft_in = re.match(r"(\d+)\s*(?:'|ft|feet)\s*(\d+)?\s*(?:\"|in|inches)?", s)
    if ft_in:
        feet = int(ft_in.group(1))
        extra = int(ft_in.group(2) or 0)
        inches = feet * 12 + extra
    else:
        # Pure inches: 84in
        in_only = re.match(r"(\d+)\s*(?:in|inches)", s)
        if in_only:
            inches = int(in_only.group(1))
        else:
            # Numeric with optional m suffix
            m_match = re.match(r"(\d+(?:\.\d+)?)\s*(m|meters?)?$", s)
            if m_match:
                num = float(m_match.group(1))
                unit = m_match.group(2)
                if unit:
                    # Explicit meters
                    inches = int(round(num * 39.3701))
                else:
                    # Ambiguous bare number. OSM convention says meters, but
                    # in the wild US entries often use bare feet. Disambiguate by range:
                    #   1.5 - 6.0  → assume meters (5-20 ft → sane)
                    #   7 - 20     → assume feet (5-20 ft → sane)
                    #   outside    → discard
                    if 1.5 <= num <= 6.0:
                        inches = int(round(num * 39.3701))
                    elif 7 <= num <= 20:
                        inches = int(round(num * 12))
                    else:
                        return None

    if inches is None:
        return None
    if inches < MIN_PLAUSIBLE_INCHES or inches > MAX_PLAUSIBLE_INCHES:
        return None
    return inches


def inches_to_label(inches: Optional[int]) -> Optional[str]:
    if inches is None:
        return None
    ft = inches // 12
    rem = inches % 12
    return f"{ft}'{rem}\""


def haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    """Distance between two coords in meters."""
    R = 6_371_000.0
    to_r = lambda d: d * math.pi / 180.0
    dlat = to_r(b_lat - a_lat)
    dlng = to_r(b_lng - a_lng)
    s = math.sin(dlat/2)**2 + math.cos(to_r(a_lat)) * math.cos(to_r(b_lat)) * math.sin(dlng/2)**2
    return 2 * R * math.asin(math.sqrt(s))


def _bbox(lat: float, lng: float, radius_deg: float) -> str:
    south, west = lat - radius_deg, lng - radius_deg
    north, east = lat + radius_deg, lng + radius_deg
    return f"{south},{west},{north},{east}"


def build_query(lat: float, lng: float, radius_deg: float) -> str:
    """Overpass QL query for parking garages + surface lots in a bounding box."""
    bbox = _bbox(lat, lng, radius_deg)
    return f"""
[out:json][timeout:90];
(
  node["amenity"="parking"]({bbox});
  way["amenity"="parking"]({bbox});
  relation["amenity"="parking"]({bbox});
);
out center tags;
""".strip()


DRIVABLE_HIGHWAYS = {
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential",
    "motorway_link", "trunk_link", "primary_link",
    "secondary_link", "tertiary_link",
    "living_street", "service",  # service kept — covers some covered service drives
}


def build_tunnel_query(lat: float, lng: float, radius_deg: float) -> str:
    """Tunnels + covered roads with a posted maxheight.

    Kept narrow so Overpass uses the tunnel/covered primary index, not a big
    highway regex scan.  Drivable-road filtering happens client-side.
    """
    bbox = _bbox(lat, lng, radius_deg)
    return f"""
[out:json][timeout:90];
(
  way["tunnel"="yes"]["maxheight"]({bbox});
  way["covered"="yes"]["maxheight"]({bbox});
);
out center tags;
""".strip()


def build_bridge_query(lat: float, lng: float, radius_deg: float) -> str:
    """Roads with a posted maxheight — almost always underpass clearances.

    OSM convention: the clearance UNDER a bridge is tagged on the way running
    underneath.  We filter client-side for drivable roads and skip tunnels
    (they're handled by build_tunnel_query).

    NOTE: we use ["highway"] positive filter — Overpass is fast at indexed
    positive filters, slow at negations on huge indices like ["maxheight"].
    """
    bbox = _bbox(lat, lng, radius_deg)
    return f"""
[out:json][timeout:90];
(
  way["highway"]["maxheight"]({bbox});
);
out center tags;
""".strip()


def fetch_overpass(query: str, per_mirror_timeout: int = 45) -> dict:
    """Try each mirror in order; retry on 429/504/timeout. 45s cap per mirror so
    one slow endpoint can't stall us — failing fast to the next mirror is
    better than hanging on one that's overloaded."""
    data = parse.urlencode({"data": query}).encode("utf-8")
    last_err = None
    for url in OVERPASS_MIRRORS:
        req = request.Request(
            url,
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=per_mirror_timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            last_err = RuntimeError(f"Overpass HTTP {e.code} at {url}: {body}")
            # Most mirror failures are transient or policy-based — try the next mirror.
            # Only bail hard on 4xx auth errors (401) since none of our queries need auth.
            if e.code in (401,):
                raise last_err
            time.sleep(1.0)
            continue
        except Exception as e:
            last_err = e
            time.sleep(1.0)
            continue
    raise last_err or RuntimeError("Overpass: all mirrors failed")


def element_latlng(el: dict) -> Optional[tuple]:
    if el.get("type") == "node":
        return (el["lat"], el["lon"])
    c = el.get("center")
    if c:
        return (c["lat"], c["lon"])
    return None


def normalize_element(el: dict, city_slug: str, verified_only: bool = False, min_capacity: int = 0) -> Optional[dict]:
    """Turn an OSM element into our garage schema. Returns None if we should skip."""
    tags = el.get("tags", {}) or {}

    if tags.get("amenity") != "parking":
        return None
    # Skip private/staff-only/customer-only
    access = (tags.get("access") or "").lower()
    if access in ("private", "no", "permit"):
        return None

    ll = element_latlng(el)
    if not ll:
        return None
    lat, lng = ll

    parking_type = (tags.get("parking") or "surface").lower()
    is_covered = parking_type in (
        "multi-storey", "underground", "garage_boxes",
        "carports", "garage", "rooftop"
    )
    is_surface = parking_type == "surface"

    # Oversized vehicles only fit surface or underground w/ height >= 8'2"
    # We'll tag surface lots with oversized=True when access is public.
    oversized = is_surface

    name = tags.get("name") or tags.get("operator") or tags.get("ref")
    if not name:
        # Generic fallback — don't add un-named non-garage surface lots (too noisy).
        if is_surface:
            return None
        name = "Unnamed parking structure"

    # Address
    parts = [tags.get("addr:housenumber"), tags.get("addr:street")]
    addr = " ".join(p for p in parts if p) or tags.get("addr:full") or ""

    # Clearance.  IMPORTANT: do NOT fall back to OSM's `height` tag here --
    # for parking ways, `height` is the BUILDING'S overall height (e.g. a
    # 6-storey garage tags height=20m), not the vehicle clearance.  Falling
    # back caused 193 garages across 14 cities to be marked with bogus
    # 15'-20' clearances (cleared April 2026).  Only `maxheight` and
    # `maxheight:physical` are clearance tags.
    maxh = tags.get("maxheight") or tags.get("maxheight:physical")
    height_in = parse_maxheight(maxh) if maxh else None
    height_label = inches_to_label(height_in)

    # Quality filters
    if verified_only and height_in is None:
        return None
    if min_capacity > 0:
        try:
            cap = int(tags.get("capacity", "0"))
        except (TypeError, ValueError):
            cap = 0
        if cap and cap < min_capacity:
            return None

    # Notes — bundle capacity, fee, etc.
    note_bits = []
    if tags.get("capacity"):
        note_bits.append(f"{tags['capacity']} spaces")
    if tags.get("fee") == "yes":
        note_bits.append("paid")
    elif tags.get("fee") == "no":
        note_bits.append("free")
    if tags.get("covered") == "yes":
        note_bits.append("covered")
    elif tags.get("covered") == "no":
        note_bits.append("open-air")
    if tags.get("opening_hours"):
        note_bits.append(f"hrs: {tags['opening_hours'][:40]}")

    notes = "; ".join(note_bits) if note_bits else "Imported from OpenStreetMap."

    # ID — stable per OSM element
    osm_type = el.get("type", "node")
    osm_id = el.get("id")
    gid = f"osm-{osm_type[0]}{osm_id}"

    return {
        "id": gid,
        "name": name.strip()[:120],
        "addr": addr.strip()[:200],
        "lat": round(lat, 5),
        "lng": round(lng, 5),
        "height_in": height_in,
        "height_label": height_label,
        "oversized": oversized,
        "notes": notes,
        "source": "OpenStreetMap",
    }


def normalize_tunnel_element(el: dict) -> Optional[dict]:
    """Turn an OSM tunnel way into our tunnel schema. Requires maxheight."""
    tags = el.get("tags", {}) or {}
    # Must be a drivable road
    hwy = (tags.get("highway") or "").lower()
    if hwy not in DRIVABLE_HIGHWAYS:
        return None
    # Skip private
    access = (tags.get("access") or "").lower()
    if access in ("private", "no"):
        return None

    ll = element_latlng(el)
    if not ll:
        return None
    lat, lng = ll

    maxh = tags.get("maxheight") or tags.get("maxheight:physical")
    height_in = parse_maxheight(maxh) if maxh else None
    if height_in is None:
        return None  # tunnels without a verified height are not useful
    height_label = inches_to_label(height_in)

    name = tags.get("name") or tags.get("tunnel:name") or tags.get("ref")
    if not name:
        # Build a generic label from the road
        hwy = tags.get("highway", "road").replace("_", " ")
        name = f"Tunnel ({hwy})"

    # Address — use the ref (I-90) or the road name
    ref = tags.get("ref", "")
    addr = ref or (tags.get("name") or "")

    note_bits = []
    if tags.get("layer"):
        note_bits.append(f"layer {tags['layer']}")
    if tags.get("length"):
        note_bits.append(f"{tags['length']}m long")
    if tags.get("lanes"):
        note_bits.append(f"{tags['lanes']} lanes")
    notes = "; ".join(note_bits) if note_bits else "Imported from OpenStreetMap."

    osm_type = el.get("type", "way")
    osm_id = el.get("id")
    tid = f"osm-{osm_type[0]}{osm_id}"

    return {
        "id": tid,
        "name": name.strip()[:120],
        "addr": addr.strip()[:200],
        "lat": round(lat, 5),
        "lng": round(lng, 5),
        "height_in": height_in,
        "height_label": height_label,
        "notes": notes,
        "source": "OpenStreetMap",
    }


def normalize_bridge_element(el: dict) -> Optional[dict]:
    """Turn an OSM way with a maxheight (underpass) into our bridge schema."""
    tags = el.get("tags", {}) or {}
    hwy = (tags.get("highway") or "").lower()
    if hwy not in DRIVABLE_HIGHWAYS:
        return None
    # Tunnels are handled by normalize_tunnel_element — skip here to avoid dupes
    if (tags.get("tunnel") or "").lower() == "yes":
        return None
    if (tags.get("covered") or "").lower() == "yes":
        return None
    access = (tags.get("access") or "").lower()
    if access in ("private", "no"):
        return None

    ll = element_latlng(el)
    if not ll:
        return None
    lat, lng = ll

    maxh = tags.get("maxheight") or tags.get("maxheight:physical")
    height_in = parse_maxheight(maxh) if maxh else None
    if height_in is None:
        return None
    height_label = inches_to_label(height_in)

    # Under-bridge clearances usually don't have a name — use ref + road
    ref = tags.get("ref") or ""
    road_name = tags.get("name") or ""
    if ref and road_name:
        name = f"Low clearance — {road_name} ({ref})"
    elif road_name:
        name = f"Low clearance — {road_name}"
    elif ref:
        name = f"Low clearance — {ref}"
    else:
        hwy = tags.get("highway", "road").replace("_", " ")
        name = f"Low clearance underpass ({hwy})"

    addr = ref or road_name or ""

    note_bits = ["Underpass / low clearance"]
    if tags.get("bridge:name"):
        note_bits.append(f"Under: {tags['bridge:name']}")
    notes = "; ".join(note_bits)

    osm_type = el.get("type", "way")
    osm_id = el.get("id")
    bid = f"osm-{osm_type[0]}{osm_id}"

    return {
        "id": bid,
        "name": name.strip()[:120],
        "addr": addr.strip()[:200],
        "lat": round(lat, 5),
        "lng": round(lng, 5),
        "height_in": height_in,
        "height_label": height_label,
        "notes": notes,
        "source": "OpenStreetMap",
    }


def merge_into_city(
    city_slug: str,
    candidates: list,
    key: str = "garages",
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """Merge candidates into data[key]. Returns (added, skipped_dupe, existing_count)."""
    city_path = CITIES_DIR / f"{city_slug}.json"
    if city_path.exists():
        data = json.loads(city_path.read_text())
    else:
        data = {"garages": [], "tunnels": [], "bridges": []}

    arr = data.setdefault(key, [])
    existing_ids = {g["id"] for g in arr}
    existing_count = len(arr)

    added = 0
    skipped_dupe = 0

    for cand in candidates:
        if cand["id"] in existing_ids:
            skipped_dupe += 1
            continue
        # Proximity dedupe — don't add if an item within DEDUPE_METERS exists
        dupe = False
        for g in arr:
            if haversine_m(cand["lat"], cand["lng"], g["lat"], g["lng"]) < DEDUPE_METERS:
                dupe = True
                break
        if dupe:
            skipped_dupe += 1
            continue

        arr.append(cand)
        existing_ids.add(cand["id"])
        added += 1

    if not dry_run and added > 0:
        city_path.write_text(json.dumps(data, indent=2) + "\n")
        # Bump index count only for garages (that's the field we track)
        if key == "garages":
            update_index_count(city_slug, len(arr))

    return added, skipped_dupe, existing_count


def update_index_count(slug: str, new_count: int) -> None:
    idx = json.loads(INDEX_PATH.read_text())
    for c in idx:
        if c["slug"] == slug:
            c["garage_count"] = new_count
            break
    INDEX_PATH.write_text(json.dumps(idx, indent=2) + "\n")


def _run_pass(
    slug: str,
    label: str,
    query: str,
    normalize_fn,
    key: str,
    dry_run: bool,
) -> dict:
    """Shared logic for garage/tunnel/bridge passes."""
    print(f"  [{label}] querying Overpass…")
    try:
        result = fetch_overpass(query)
    except Exception as e:
        print(f"    ERROR: {e}")
        return {"added": 0, "dupes": 0, "error": str(e)}

    elements = result.get("elements", [])
    candidates = []
    for el in elements:
        c = normalize_fn(el)
        if c:
            candidates.append(c)
    with_height = sum(1 for c in candidates if c.get("height_in") is not None)
    print(f"    OSM returned {len(elements)}; normalized {len(candidates)} ({with_height} with height)")

    added, dupes, existing = merge_into_city(slug, candidates, key=key, dry_run=dry_run)
    tag = "[DRY-RUN] would add" if dry_run else "Added"
    print(f"    {tag}: {added}  (dupes: {dupes}; existing: {existing})")
    return {"added": added, "dupes": dupes, "existing_before": existing}


def import_city(
    city: dict,
    dry_run: bool,
    verified_only: bool = False,
    min_capacity: int = 0,
    types: Optional[set] = None,
) -> dict:
    if types is None:
        types = {"garages", "tunnels", "bridges"}
    slug = city["slug"]
    radius = CITY_RADIUS_DEG.get(city.get("zoom", 12), 0.15)
    print(f"\n[{slug}] ({city['lat']:.3f},{city['lng']:.3f})  radius={radius}°  types={sorted(types)}")

    summary = {"slug": slug}

    if "garages" in types:
        q = build_query(city["lat"], city["lng"], radius)
        # Garages use the richer normalizer with verified_only / min_capacity
        def gnorm(el):
            return normalize_element(el, slug, verified_only=verified_only, min_capacity=min_capacity)
        res = _run_pass(slug, "garages", q, gnorm, "garages", dry_run)
        summary["garages"] = res
        time.sleep(1.0)

    if "tunnels" in types:
        q = build_tunnel_query(city["lat"], city["lng"], radius)
        res = _run_pass(slug, "tunnels", q, normalize_tunnel_element, "tunnels", dry_run)
        summary["tunnels"] = res
        time.sleep(1.0)

    if "bridges" in types:
        q = build_bridge_query(city["lat"], city["lng"], radius)
        res = _run_pass(slug, "bridges", q, normalize_bridge_element, "bridges", dry_run)
        summary["bridges"] = res

    total_added = sum(summary.get(t, {}).get("added", 0) for t in ("garages", "tunnels", "bridges"))
    summary["added"] = total_added
    summary["dupes"] = sum(summary.get(t, {}).get("dupes", 0) for t in ("garages", "tunnels", "bridges"))
    return summary


def main():
    ap = argparse.ArgumentParser(description="Import OSM parking data into WillIFit.")
    ap.add_argument("--slug", help="Single city slug (e.g. las-vegas-nv)")
    ap.add_argument("--slugs", help="Comma-separated list of slugs (useful for a subset)")
    ap.add_argument("--all", action="store_true", help="Import all live cities")
    ap.add_argument("--dry-run", action="store_true", help="Don't write changes")
    ap.add_argument("--sleep", type=float, default=2.0, help="Seconds between cities")
    ap.add_argument("--verified-only", action="store_true",
                    help="Only import OSM entries that have a maxheight tag (conservative — prevents 'Unverified' noise)")
    ap.add_argument("--min-capacity", type=int, default=0,
                    help="Skip entries whose capacity tag is < N. Named garages with no capacity still pass.")
    ap.add_argument("--types", default="garages,tunnels,bridges",
                    help="Comma-separated: garages, tunnels, bridges. Default: all three.")
    args = ap.parse_args()

    if not args.slug and not args.all and not args.slugs:
        ap.error("Pass --slug SLUG, --slugs s1,s2,... or --all")

    types = {t.strip() for t in args.types.split(",") if t.strip()}
    valid = {"garages", "tunnels", "bridges"}
    if not types.issubset(valid):
        ap.error(f"--types must be subset of {sorted(valid)}, got {sorted(types)}")

    idx = json.loads(INDEX_PATH.read_text())
    live_cities = [c for c in idx if c["status"] == "live"]

    if args.slug:
        cities = [c for c in idx if c["slug"] == args.slug]
        if not cities:
            print(f"Unknown slug: {args.slug}", file=sys.stderr)
            sys.exit(2)
    elif args.slugs:
        wanted = [s.strip() for s in args.slugs.split(",") if s.strip()]
        by_slug = {c["slug"]: c for c in idx}
        cities = []
        for slug in wanted:
            if slug in by_slug:
                cities.append(by_slug[slug])
            else:
                print(f"Warning: unknown slug (skipping): {slug}", file=sys.stderr)
        if not cities:
            ap.error("no cities matched --slugs")
    else:
        cities = live_cities

    print(f"Target: {len(cities)} city/cities  (dry_run={args.dry_run})")

    summary = []
    for i, city in enumerate(cities):
        try:
            summary.append(import_city(
                city,
                dry_run=args.dry_run,
                verified_only=args.verified_only,
                min_capacity=args.min_capacity,
                types=types,
            ))
        except KeyboardInterrupt:
            print("\nInterrupted.")
            break
        if i < len(cities) - 1:
            time.sleep(args.sleep)

    # Tally
    by_type = {"garages": 0, "tunnels": 0, "bridges": 0}
    total_added = 0
    total_dupes = 0
    errors = []
    for s in summary:
        total_added += s.get("added", 0)
        total_dupes += s.get("dupes", 0)
        for t in by_type:
            sub = s.get(t) or {}
            by_type[t] += sub.get("added", 0)
            if sub.get("error"):
                errors.append((s["slug"], t, sub["error"]))

    print("\n=== SUMMARY ===")
    print(f"Cities processed: {len(summary)}")
    print(f"Total added:      {total_added}")
    print(f"  garages: {by_type['garages']}")
    print(f"  tunnels: {by_type['tunnels']}")
    print(f"  bridges: {by_type['bridges']}")
    print(f"Total dupes:      {total_dupes}")
    if errors:
        print(f"Errors:           {len(errors)}")
        for slug, t, err in errors:
            print(f"  - {slug} [{t}]: {err}")


if __name__ == "__main__":
    main()
