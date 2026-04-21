#!/usr/bin/env python3
"""
Coords + facility-type audit — for each garage:
  1. Geocode the recorded address and compare to the recorded lat/lng.
     Flag any mismatch >50m (likely wrong building).
  2. Call Google Place Details on the geocoded place_id to read the
     canonical facility name and types.  Flag anything that Google
     clearly labels as a surface lot when we have it as a garage,
     or anything Google doesn't recognize as a parking facility at
     all (so we don't spend Claude credits on non-parking addresses).

Context:
  Reno's National Bowling Stadium had coords 136m off -- Claude
  correctly read "no sign" from the wrong building.  Reno's Atlantis
  is actually open-air parking even though imported as a "garage".
  This script catches both categories of bad data before they waste
  downstream verification budget.

Usage:
  export GOOGLE_MAPS_API_KEY=...
  python3 scripts/geocode_audit.py --slug reno-nv
  python3 scripts/geocode_audit.py --all                    # full DB
  python3 scripts/geocode_audit.py --slug reno-nv --emit-suggestions

Cost:
  Geocoding:     ~$0.005 per entry  ($5 / 1000)
  Place Details: ~$0.017 per entry  ($17 / 1000)  <-- requires Places API
                                                     enabled in your GCP console
  Reno (19 entries): ~$0.42
  Full DB (~2000):   ~$45
  If Places API isn't enabled, the script degrades gracefully --
  geocoding-only pass at 1/4 the cost.  Pass --skip-place-details to
  force the cheap mode.

Safety:
  Never modifies city JSON directly.  At most writes a
  data/cities/<slug>.geocode-suggestions.json file that a human can
  diff and copy-paste good corrections from.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Optional
from urllib import request, parse, error

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
GOOGLE_KEY = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_GEOCODING_KEY")

# Keywords used to classify Google's canonical place name into a facility
# kind.  Run against .lower() of the name.  Structural keywords win over
# surface keywords (so "Stadium Garage" classifies as structure).
FACILITY_STRUCTURE_WORDS = (
    "parking garage", "parking structure", "parking deck",
    "parking ramp", "multi-level", "multi-story", "multistory",
)
FACILITY_SURFACE_WORDS = (
    "parking lot", "surface lot", "open air", "open-air",
    "outdoor parking", "uncovered",
)

# Distance bands (meters) — how far off is "too far"
OK_THRESHOLD = 20.0       # ≤20m is normal geocoder jitter
SUSPECT_THRESHOLD = 50.0  # 20-50m worth watching, might be entrance vs building center
BAD_THRESHOLD = 100.0     # >50m is a wrong-building error


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _http_get(url: str, timeout: int = 15) -> tuple[int, bytes]:
    req = request.Request(url, headers={"User-Agent": "willifit-geocode-audit/0.1"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except error.HTTPError as e:
        return e.code, e.read()


def geocode(address: str) -> Optional[dict]:
    """Return the first geocoding result, or None on error / zero results."""
    q = parse.urlencode({"address": address, "key": GOOGLE_KEY})
    status, body = _http_get(f"{GEOCODE_URL}?{q}")
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except Exception:
        return None
    if data.get("status") != "OK" or not data.get("results"):
        return {"_status": data.get("status", "ZERO_RESULTS")}
    r = data["results"][0]
    loc = r.get("geometry", {}).get("location", {})
    return {
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "formatted": r.get("formatted_address", ""),
        "location_type": r.get("geometry", {}).get("location_type", ""),
        "place_id": r.get("place_id"),
        "types": r.get("types", []),
    }


def place_details(place_id: str) -> Optional[dict]:
    """Fetch Place Details for a given place_id.  We only pull the few
    fields we actually use (keeps the billable SKU to 'Basic Data' +
    'Contact Data' which are the cheapest).  Returns None on API error,
    which the caller should treat as 'degraded' rather than fatal --
    Place Details requires the Places API to be enabled separately in
    Google Cloud Console, and not every project has that enabled."""
    q = parse.urlencode({
        "place_id": place_id,
        "fields": "name,types,formatted_address,business_status",
        "key": GOOGLE_KEY,
    })
    status, body = _http_get(f"{PLACE_DETAILS_URL}?{q}")
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except Exception:
        return None
    if data.get("status") != "OK":
        # Common codes: REQUEST_DENIED (Places API not enabled),
        # INVALID_REQUEST, NOT_FOUND
        return {"_status": data.get("status", "UNKNOWN")}
    r = data.get("result", {}) or {}
    return {
        "name": r.get("name", ""),
        "types": r.get("types", []),
        "formatted_address": r.get("formatted_address", ""),
        "business_status": r.get("business_status", ""),
    }


def classify_distance(distance_m: Optional[float], loc_type: str) -> str:
    """Banding based on distance and Google's own precision signal."""
    if distance_m is None:
        return "no-geocode"
    if distance_m <= OK_THRESHOLD:
        return "ok"
    if distance_m <= SUSPECT_THRESHOLD:
        return "suspect"
    if distance_m <= BAD_THRESHOLD:
        return "bad"
    return "very-bad"


def classify_facility(
    place_name: str, place_types: list, garage_notes: str = ""
) -> str:
    """Based on Google's canonical place name + types, classify the facility.

    Returns one of:
      "structure"     — Google names it as a garage / structure / deck
      "surface_lot"   — Google names it as a lot / surface parking
      "parking"       — Google marks types=[parking] but name is generic
                        (e.g. just the building/casino name)
      "not_parking"   — Google doesn't recognize it as a parking facility
      "unknown"       — no Place Details available (API not enabled)
    """
    if not place_name and not place_types:
        return "unknown"

    n = (place_name or "").lower()
    # Structural wording wins (explicit > ambiguous)
    if any(w in n for w in FACILITY_STRUCTURE_WORDS):
        return "structure"
    if any(w in n for w in FACILITY_SURFACE_WORDS):
        return "surface_lot"

    # Fall back to types
    if "parking" in (place_types or []):
        return "parking"    # generic — neither name nor types disambiguates

    # If name suggests parking but doesn't match structural/surface keywords...
    if "parking" in n:
        return "parking"

    return "not_parking"


def audit_city(slug: str, emit_suggestions: bool, sleep: float, skip_place_details: bool) -> dict:
    city_path = CITIES_DIR / f"{slug}.json"
    if not city_path.exists():
        return {"slug": slug, "error": "no city file"}

    idx = json.loads(INDEX_PATH.read_text())
    city_meta = next((c for c in idx if c["slug"] == slug), None)
    city_name = city_meta.get("name", "") if city_meta else ""
    state = city_meta.get("state", "") if city_meta else ""

    data = json.loads(city_path.read_text())
    garages = data.get("garages", [])

    rows = []
    suggestions = []
    # Disable Place Details after the first REQUEST_DENIED so we don't waste
    # time (and a failed call is still billed in some cases).  This keeps the
    # audit useful even if the user hasn't enabled the Places API.
    place_details_disabled = skip_place_details

    print(f"[{slug}] auditing {len(garages)} garages (city={city_name}, state={state})")
    print(f"{'id':<35}{'dist':<8}{'coord':<8}{'facility':<14}{'google name':<42}{'notes':<25}")
    print("-" * 135)

    for g in garages:
        addr = (g.get("addr") or "").strip()
        if not addr:
            rows.append({"id": g.get("id"), "status": "no-addr"})
            print(f"{g.get('id','?')[:34]:<35}{'':<8}{'no-addr':<8}")
            continue

        # Include city + state so the geocoder disambiguates.
        full_addr = addr
        if city_name and city_name.lower() not in addr.lower():
            full_addr = f"{addr}, {city_name}"
        if state and state.lower() not in full_addr.lower():
            full_addr = f"{full_addr}, {state}"

        gc = geocode(full_addr)
        if not gc or gc.get("_status"):
            rows.append({"id": g.get("id"), "status": "geocode-failed",
                         "geocode_status": (gc or {}).get("_status")})
            print(f"{g.get('id','?')[:34]:<35}{'-':<8}{'FAIL':<8}")
            time.sleep(sleep)
            continue

        cur_lat, cur_lng = g.get("lat"), g.get("lng")
        new_lat, new_lng = gc["lat"], gc["lng"]
        d = haversine_m(cur_lat, cur_lng, new_lat, new_lng) if cur_lat else None
        coord_status = classify_distance(d, gc.get("location_type", ""))

        # Place Details for facility-type classification
        pd = None
        facility_kind = "unknown"
        if not place_details_disabled and gc.get("place_id"):
            pd = place_details(gc["place_id"])
            if pd and pd.get("_status") == "REQUEST_DENIED":
                print(f"  (note: Places API not enabled — running geocode-only from here)")
                place_details_disabled = True
                pd = None
            elif pd and not pd.get("_status"):
                facility_kind = classify_facility(
                    pd.get("name", ""), pd.get("types", []),
                    g.get("notes", ""),
                )
            time.sleep(sleep)

        # Overall row status — worst of (coord, facility) signals the row's
        # concern level; the suggestions file lists anything worth a human look.
        row_concerns = []
        if coord_status in ("bad", "very-bad"):
            row_concerns.append(f"coord-{coord_status}")
        stamped_type = g.get("structure_type")  # what we already have stamped, if anything
        if facility_kind == "surface_lot" and stamped_type != "surface_lot":
            row_concerns.append("should-be-surface-lot")
        if facility_kind == "not_parking":
            row_concerns.append("not-a-parking-facility")

        pn = (pd or {}).get("name", "") if pd else ""
        print(f"{g.get('id','?')[:34]:<35}"
              f"{(f'{d:.0f}m' if d is not None else '-'):<8}"
              f"{coord_status:<8}"
              f"{facility_kind:<14}"
              f"{pn[:40]:<42}"
              f"{(','.join(row_concerns))[:24]:<25}")

        rows.append({
            "id": g.get("id"),
            "name": g.get("name"),
            "addr_queried": full_addr,
            "current_lat": cur_lat, "current_lng": cur_lng,
            "geocoded_lat": new_lat, "geocoded_lng": new_lng,
            "distance_m": round(d, 1) if d is not None else None,
            "coord_status": coord_status,
            "google_location_type": gc.get("location_type"),
            "google_formatted": gc.get("formatted"),
            "google_place_id": gc.get("place_id"),
            "google_place_name": pn,
            "google_place_types": (pd or {}).get("types", []) if pd else [],
            "facility_kind": facility_kind,
            "concerns": row_concerns,
        })

        if row_concerns:
            sug = {
                "id": g.get("id"),
                "name": g.get("name"),
                "concerns": row_concerns,
                "google_name": pn,
                "google_types": (pd or {}).get("types", []) if pd else [],
            }
            if coord_status in ("bad", "very-bad"):
                sug["suggested_coords"] = {
                    "old": [cur_lat, cur_lng],
                    "new": [round(new_lat, 6), round(new_lng, 6)],
                    "distance_m": round(d, 1),
                    "google_formatted": gc.get("formatted"),
                }
            if facility_kind == "surface_lot" and stamped_type != "surface_lot":
                sug["suggested_structure_type"] = "surface_lot"
            if facility_kind == "not_parking":
                sug["suggested_action"] = "review — Google does not recognize this as a parking facility"
            suggestions.append(sug)

        time.sleep(sleep)

    # Summary — tally by coord status AND by facility kind
    coord_counts = {}
    facility_counts = {}
    for r in rows:
        coord_counts[r.get("coord_status", "n/a")] = coord_counts.get(r.get("coord_status", "n/a"), 0) + 1
        facility_counts[r.get("facility_kind", "n/a")] = facility_counts.get(r.get("facility_kind", "n/a"), 0) + 1
    print()
    print(f"[{slug}] coord status:   {dict(coord_counts)}")
    print(f"[{slug}] facility kind:  {dict(facility_counts)}")
    print(f"[{slug}] rows flagged:   {len(suggestions)} (see suggestions file if --emit-suggestions)")

    if emit_suggestions and suggestions:
        out = CITIES_DIR / f"{slug}.geocode-suggestions.json"
        out.write_text(json.dumps({"slug": slug, "generated": time.strftime("%Y-%m-%d"),
                                   "suggestions": suggestions}, indent=2) + "\n")
        print(f"[{slug}] wrote {len(suggestions)} suggestions → {out}")

    return {"slug": slug, "counts": counts, "rows": rows, "suggestions": suggestions}


def main():
    ap = argparse.ArgumentParser(description="Audit garage coords against Google Geocoding.")
    ap.add_argument("--slug", help="Single city slug")
    ap.add_argument("--all", action="store_true", help="All live cities")
    ap.add_argument("--emit-suggestions", action="store_true",
                    help="Write <slug>.geocode-suggestions.json for flagged entries")
    ap.add_argument("--sleep", type=float, default=0.15,
                    help="Seconds between geocoding calls (rate-limit pad)")
    ap.add_argument("--skip-place-details", action="store_true",
                    help="Cheap mode -- geocoding only, no Place Details. "
                         "Use if Places API isn't enabled in your Google Cloud "
                         "console (~1/4 the cost, but no facility-type check).")
    args = ap.parse_args()

    if not (args.slug or args.all):
        ap.error("pass --slug or --all")
    if not GOOGLE_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY not set", file=sys.stderr)
        sys.exit(2)

    idx = json.loads(INDEX_PATH.read_text())
    if args.slug:
        targets = [args.slug]
    else:
        targets = [c["slug"] for c in idx if c.get("status") == "live"]

    all_suggestions = 0
    for slug in targets:
        r = audit_city(slug, args.emit_suggestions, args.sleep,
                       skip_place_details=args.skip_place_details)
        all_suggestions += len(r.get("suggestions", []))

    if args.all:
        print(f"\n=== TOTAL suggestions across all cities: {all_suggestions} ===")


if __name__ == "__main__":
    main()
