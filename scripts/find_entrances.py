#!/usr/bin/env python3
"""
find_entrances.py — locate the physical garage entrance for each parking
structure via OpenStreetMap's `amenity=parking_entrance` nodes.

FREE — no API keys needed, no Claude calls, just the public Overpass API.

Why this exists:
  The core verification pipeline (auto_verify.py) probes Street View around
  a garage's stored lat/lng.  Those coords usually come from address
  geocoding, which points at building centers -- often 50-150m from where
  the driveway curb cut actually is.  Street View has no coverage inside
  building footprints, so probing the building center misses the entrance
  pano every time.

  OSM mappers tag the driveway points of many garages as
  `amenity=parking_entrance` nodes -- exactly the lat/lng we want to probe
  Street View at.  This script copies those node coords into new
  `entrance_lat` / `entrance_lng` fields on each garage record.

  Downstream:
    * auto_verify.py prefers entrance_lat/lng over lat/lng when probing
      for panos, dramatically improving hit rate.
    * The frontend detail hero also prefers entrance_lat/lng -- so even
      unverified garages open with the Street View pano actually pointing
      at the driveway instead of at a random spot near the building.

Data preserved:
  The main `lat`/`lng` stays put (it's still the building / address, which
  is what the map pin wants to show).  Only the new entrance_* fields are
  populated, non-destructively.

Usage:
  python3 scripts/find_entrances.py --slug reno-nv
  python3 scripts/find_entrances.py --slug portland-or --dry-run
  python3 scripts/find_entrances.py --all --radius 250
  python3 scripts/find_entrances.py --slug las-vegas-nv --refresh
      # --refresh re-queries even garages that already have entrance_lat
      # set (in case OSM got a better tag since the first pass).

Respect:
  The free Overpass API is community-run.  Default --sleep is 1s/garage
  to stay well below the published rate limit.  Don't run --all twice
  in an hour unless you've been tweaking the script.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Optional
from urllib import parse, request, error

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"
LOG_PATH = REPO_ROOT / "data" / "find_entrances.log"

# Public Overpass API mirrors, in preference order.  overpass-api.de is the
# canonical one; others are community-run fallbacks with the same data.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

DEFAULT_RADIUS_M = 200
USER_AGENT = "willifit-find-entrances/0.1 (https://willifit.ai)"


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _log(line: str):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def overpass_query(q: str, timeout: int = 30) -> Optional[dict]:
    """Try each Overpass mirror until one succeeds; return parsed JSON."""
    body = parse.urlencode({"data": q}).encode("utf-8")
    for mirror in OVERPASS_MIRRORS:
        try:
            req = request.Request(mirror, data=body, headers={
                "User-Agent": USER_AGENT,
            })
            with request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except (error.HTTPError, error.URLError, TimeoutError, OSError):
            continue
        except json.JSONDecodeError:
            continue
    return None


def find_entrance(lat: float, lng: float, radius_m: int = DEFAULT_RADIUS_M) -> Optional[dict]:
    """Look for amenity=parking_entrance nodes within `radius_m` of the target
    coord; return the closest one, or None."""
    q = f"""
    [out:json][timeout:20];
    (
      node["amenity"="parking_entrance"](around:{radius_m},{lat},{lng});
    );
    out body;
    """
    result = overpass_query(q)
    if not result:
        return None

    cands = []
    for el in (result.get("elements") or []):
        if el.get("type") != "node":
            continue
        elat, elng = el.get("lat"), el.get("lon")
        if elat is None or elng is None:
            continue
        dist = haversine_m(lat, lng, elat, elng)
        cands.append({
            "lat": elat,
            "lng": elng,
            "distance_m": dist,
            "osm_id": el.get("id"),
            "tags": el.get("tags", {}),
        })

    if not cands:
        return None

    cands.sort(key=lambda x: x["distance_m"])
    return cands[0]


def process_city(slug: str, radius_m: int, dry_run: bool, refresh: bool,
                 sleep_sec: float) -> dict:
    city_path = CITIES_DIR / f"{slug}.json"
    if not city_path.exists():
        print(f"[{slug}] no city file")
        return {"slug": slug, "error": "no-file", "checked": 0, "found": 0}

    data = json.loads(city_path.read_text())
    garages = data.get("garages", [])

    # Who should we check?
    #   Skip surface lots (no drive-up entrance to locate).
    #   Skip entries that already have entrance_lat (unless --refresh).
    candidates = []
    for g in garages:
        if g.get("structure_type") == "surface_lot":
            continue
        if g.get("entrance_lat") is not None and not refresh:
            continue
        candidates.append(g)

    if not candidates:
        print(f"[{slug}] no candidates needing entrance lookup")
        return {"slug": slug, "checked": 0, "found": 0, "not_found": 0}

    print(f"[{slug}] {len(candidates)} candidates (radius={radius_m}m, "
          f"sleep={sleep_sec}s)")

    found = not_found = 0
    for g in candidates:
        lat, lng = g.get("lat"), g.get("lng")
        if lat is None or lng is None:
            continue

        ent = find_entrance(lat, lng, radius_m)
        if ent:
            found += 1
            print(f"  {g.get('id','?'):<34} -> entrance at {ent['lat']:.6f},{ent['lng']:.6f} "
                  f"(OSM node {ent['osm_id']}, {ent['distance_m']:.0f}m from stored coord)")
            _log(f"{slug} {g.get('id')} FOUND osm={ent['osm_id']} "
                 f"ent_lat={ent['lat']} ent_lng={ent['lng']} dist={ent['distance_m']:.1f}")
            if not dry_run:
                g["entrance_lat"] = ent["lat"]
                g["entrance_lng"] = ent["lng"]
                g["entrance_source"] = f"OSM parking_entrance node {ent['osm_id']}"
        else:
            not_found += 1
            print(f"  {g.get('id','?'):<34} no OSM parking_entrance within {radius_m}m")
            _log(f"{slug} {g.get('id')} NOT-FOUND radius={radius_m}")

        time.sleep(sleep_sec)

    if found > 0 and not dry_run:
        city_path.write_text(json.dumps(data, indent=2) + "\n")

    print(f"[{slug}] done: found={found} not_found={not_found}"
          f"{' [DRY-RUN]' if dry_run else ''}")

    return {
        "slug": slug,
        "checked": len(candidates),
        "found": found,
        "not_found": not_found,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Find garage entrances from OSM parking_entrance nodes (free).")
    ap.add_argument("--slug", help="Single city slug")
    ap.add_argument("--slugs", help="Comma-separated slugs")
    ap.add_argument("--all", action="store_true", help="All live cities")
    ap.add_argument("--radius", type=int, default=DEFAULT_RADIUS_M,
                    help=f"Overpass search radius in meters (default: {DEFAULT_RADIUS_M})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would change; don't write JSON")
    ap.add_argument("--refresh", action="store_true",
                    help="Re-query garages that already have entrance_lat set")
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="Seconds between Overpass calls (default: 1.0 — be "
                         "polite to the free public API)")
    args = ap.parse_args()

    if not (args.slug or args.slugs or args.all):
        ap.error("pass --slug, --slugs, or --all")

    idx = json.loads(INDEX_PATH.read_text())
    if args.slug:
        targets = [args.slug]
    elif args.slugs:
        targets = [s.strip() for s in args.slugs.split(",") if s.strip()]
    else:
        targets = [c["slug"] for c in idx if c.get("status") == "live"]

    print(f"find_entrances: {len(targets)} cities, radius={args.radius}m, "
          f"dry_run={args.dry_run}, refresh={args.refresh}")
    print()

    summary = []
    for slug in targets:
        try:
            r = process_city(slug, args.radius, args.dry_run, args.refresh,
                             args.sleep)
            summary.append(r)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            break

    print()
    print("=== SUMMARY ===")
    total_checked = sum(s.get("checked", 0) for s in summary)
    total_found = sum(s.get("found", 0) for s in summary)
    total_not_found = sum(s.get("not_found", 0) for s in summary)
    print(f"Cities processed:     {len(summary)}")
    print(f"Garages checked:      {total_checked}")
    print(f"Entrances found:      {total_found}")
    print(f"No OSM data:          {total_not_found}")
    if total_checked:
        pct = 100.0 * total_found / total_checked
        print(f"Hit rate:             {pct:.0f}%")
    if args.dry_run:
        print("(DRY-RUN — no files written.)")


if __name__ == "__main__":
    main()
