#!/usr/bin/env python3
"""
WillIFit — FHWA National Bridge Inventory (NBI) import pipeline.

The NBI is a free, public-domain U.S. government dataset of ~620,000 bridges
with posted vertical clearances, published annually by the Federal Highway
Administration.  It's the single largest source of verified low-clearance
data in the country.

  Source: https://www.fhwa.dot.gov/bridge/nbi/ascii.cfm

The 2023 (and earlier) files ship as one comma-delimited `.txt` per state
with ~120 fields per row.  Field definitions live in the NBI Coding Guide:

  https://www.fhwa.dot.gov/bridge/mtguide.pdf

Fields we care about:
  STATE_CODE_001         - FIPS state
  STRUCTURE_NUMBER_008   - unique bridge ID (per state)
  LAT_016                - latitude  as DDMMSS.SS (8 chars, right-aligned)
  LONG_017               - longitude as DDDMMSS.SS (9 chars, west-positive!)
  FACILITY_CARRIED_007   - what the bridge carries ("I-95", "Main St")
  FEATURES_DESC_006A     - what it crosses ("Hudson River", "US-1")
  MIN_VERT_CLR_054       - coded clearance: two digits A + four digits B
                           054A = reference (N=highway under, R=rail)
                           054B = clearance in meters, XX.XX (stored as 0450)
  YEAR_BUILT_027         - build year

Usage:
  # 1) Download a state file (or all of them)
  python3 nbi_import.py --download --state CA --year 2023

  # 2) Parse + assign to nearest city + merge
  python3 nbi_import.py --parse --state CA --max-clearance-ft 15 --dry-run

  # One-shot (download + parse + merge)
  python3 nbi_import.py --all-states --year 2023 --max-clearance-ft 15

Only bridges with a verified clearance under --max-clearance-ft are imported.
Anything 15'0" or lower is a real concern for standard U.S. moving trucks
(typical 11'-13'6"), RVs (11'-13'6"), and box trucks (typically 13'6").
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path
from typing import Optional
from urllib import request, error

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"
NBI_CACHE = REPO_ROOT / "data" / "nbi_cache"

# One-shot URL format. FHWA sometimes renames years — adjust if needed.
NBI_URL_TEMPLATE = "https://www.fhwa.dot.gov/bridge/nbi/{year}/delimited/{state}{yy}.txt"

USER_AGENT = "willifit-nbi-importer/0.1"

# Assign a bridge to a city if it is within this many km of the city centroid.
CITY_RADIUS_KM = 30.0

# Default: anything <=15'0" is a real hazard.
DEFAULT_MAX_CLEARANCE_FT = 15.0

# Proximity dedupe against existing entries (km).
DEDUPE_M = 75.0

# All US state 2-letter codes (50 states + DC + PR).
US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR",
]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_ddmmss(value: str, is_long: bool = False) -> Optional[float]:
    """NBI lat/lng format (2023 CSV export):
      LAT_016  = 8-digit DDMMSSSS where the last 2 digits are hundredths of a second
                 e.g.  39460947  → 39°46'09.47"
      LONG_017 = 9-digit DDDMMSSSS (west-positive, we negate)
                 e.g.  075343612 → 75°34'36.12"

    Leading zeros are significant — treat input as a fixed-width string first,
    not a float (stripping leading zeros would shift digits).
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Strip any quoting/whitespace
    s = s.strip("'\"")
    if not s or not s.isdigit():
        return None
    if set(s) == {"0"}:
        return None

    if is_long:
        # Expect 9 chars: DDD MM SS SS (seconds scaled by 100)
        s = s.zfill(9)
        deg = int(s[0:3])
        mm = int(s[3:5])
        ss = int(s[5:9]) / 100.0
    else:
        s = s.zfill(8)
        deg = int(s[0:2])
        mm = int(s[2:4])
        ss = int(s[4:8]) / 100.0

    if mm >= 60 or ss >= 60:
        return None

    decimal = deg + mm / 60.0 + ss / 3600.0
    if is_long:
        decimal = -decimal

    # Sanity — continental US + AK + HI + PR
    if is_long and not (-180 <= decimal <= -60):
        return None
    if not is_long and not (15 <= decimal <= 72):
        return None
    return round(decimal, 5)


def parse_clearance_meters(value: str) -> Optional[float]:
    """NBI vertical clearance fields.

    Modern (2017+) CSV format stores as decimal meters already, e.g. "4.28".
    Sentinel values:
      "99.99"   → no limit / not applicable
      "0"       → not measured / not applicable
      ""        → missing
    """
    if value is None:
        return None
    s = str(value).strip().strip("'\"")
    if s in ("", "N", "0", "0.0", "0.00", "9999", "99.99", "99.9"):
        return None
    try:
        num = float(s)
    except ValueError:
        return None
    # Legacy fixed-width encoding (no decimal, all digits) → divide by 100
    if "." not in s and num > 50:
        num = num / 100.0
    if num <= 0 or num > 10:  # >10m = ~32'10" = effectively no limit
        return None
    return num


def meters_to_inches(m: float) -> int:
    return int(round(m * 39.3701))


def inches_to_label(inches: Optional[int]) -> Optional[str]:
    if inches is None:
        return None
    ft = inches // 12
    rem = inches % 12
    return f"{ft}'{rem}\""


def haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    R = 6_371_000.0
    to_r = lambda d: d * math.pi / 180.0
    dlat = to_r(b_lat - a_lat)
    dlng = to_r(b_lng - a_lng)
    s = math.sin(dlat / 2) ** 2 + math.cos(to_r(a_lat)) * math.cos(to_r(b_lat)) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(s))


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_state(state: str, year: int) -> Path:
    NBI_CACHE.mkdir(parents=True, exist_ok=True)
    yy = f"{year % 100:02d}"
    url = NBI_URL_TEMPLATE.format(year=year, state=state.upper(), yy=yy)
    out = NBI_CACHE / f"{state.upper()}{yy}.txt"
    if out.exists() and out.stat().st_size > 1000:
        print(f"  [{state}] cached ({out.stat().st_size // 1024} KB)")
        return out
    print(f"  [{state}] downloading {url}")
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    except error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}") from e
    out.write_bytes(data)
    print(f"  [{state}] saved {len(data) // 1024} KB → {out.name}")
    return out


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

# The NBI delimited file has a header row naming each column.  Column names
# we care about (2017+ format):
#   STATE_CODE_001, STRUCTURE_NUMBER_008, LAT_016, LONG_017,
#   FACILITY_CARRIED_007, FEATURES_DESC_006A, MIN_VERT_CLR_UNDR_054B,
#   YEAR_BUILT_027, MIN_VERT_CLR_010 (alt).

# Some older files use different names — try alternates if missing.
COL_ALIASES = {
    "state": ["STATE_CODE_001", "STATE_001"],
    "id":    ["STRUCTURE_NUMBER_008", "STRUCTURE_NUMBER"],
    "lat":   ["LAT_016"],
    "lng":   ["LONG_017"],
    "carries": ["FACILITY_CARRIED_007", "FACILITY_CARRIED"],
    "crosses": ["FEATURES_DESC_006A", "FEATURES_DESCRIPTION"],
    "clr_under_m": ["VERT_CLR_UND_054B", "MIN_VERT_CLR_UNDR_054B", "MIN_VERT_UNDR_054B"],
    "clr_under_ref": ["VERT_CLR_UND_REF_054A"],  # N=highway under, R=rail under
    "clr_on_m":  ["MIN_VERT_CLR_010"],  # on-bridge (covered deck / tunnel approach)
    "year_built": ["YEAR_BUILT_027"],
    "county": ["COUNTY_CODE_003"],
}


def _lookup(row: dict, aliases: list[str]) -> str:
    for a in aliases:
        if a in row and row[a]:
            return row[a]
    return ""


def _clean(s: str) -> str:
    """NBI CSVs often wrap strings in single quotes: 'RISING SUN LANE'"""
    if s is None:
        return ""
    return s.strip().strip("'\"").strip()


def parse_nbi_file(path: Path, max_clearance_ft: float) -> list[dict]:
    """Return list of low-clearance bridge dicts.

    We emit an entry whenever EITHER the on-bridge clearance (item 010) or the
    under-bridge clearance (item 054B) is ≤ the threshold.  We prefer the lower
    of the two as `height_in`, and record which in the notes.
    """
    max_clearance_m = max_clearance_ft * 0.3048
    out = []
    with path.open("r", encoding="latin-1", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lat = parse_ddmmss(_clean(_lookup(row, COL_ALIASES["lat"])), is_long=False)
            lng = parse_ddmmss(_clean(_lookup(row, COL_ALIASES["lng"])), is_long=True)
            if lat is None or lng is None:
                continue

            clr_under = parse_clearance_meters(_clean(_lookup(row, COL_ALIASES["clr_under_m"])))
            clr_on = parse_clearance_meters(_clean(_lookup(row, COL_ALIASES["clr_on_m"])))
            under_ref = _clean(_lookup(row, COL_ALIASES["clr_under_ref"]))
            # 054A = "N" means a highway passes underneath — that's the case drivers hit.
            # "R" means rail underneath (not our concern). "H" = parallel highway (skip).
            if clr_under is not None and under_ref and under_ref.upper() not in ("N", ""):
                clr_under = None

            # Which clearance is the concern?
            candidates = []
            if clr_under is not None:
                candidates.append(("under", clr_under))
            if clr_on is not None:
                candidates.append(("on", clr_on))
            # Filter to below threshold
            candidates = [(t, m) for t, m in candidates if m <= max_clearance_m]
            if not candidates:
                continue

            # Use the lower of the two as our posted height
            kind, clr_m = min(candidates, key=lambda x: x[1])
            height_in = meters_to_inches(clr_m)
            if height_in < 60 or height_in > 240:
                continue

            struct_num = _clean(_lookup(row, COL_ALIASES["id"]))
            state = _clean(_lookup(row, COL_ALIASES["state"]))
            carries = _clean(_lookup(row, COL_ALIASES["carries"]))
            crosses = _clean(_lookup(row, COL_ALIASES["crosses"]))
            year = _clean(_lookup(row, COL_ALIASES["year_built"]))

            # Build display name — this is how users see it on the map
            if kind == "under":
                # Low UNDER-clearance: truck passes under "crosses" (the feature below)
                # and the bridge above carries "carries".
                if carries and crosses:
                    name = f"Low clearance: {crosses} under {carries}"
                elif carries:
                    name = f"Underpass: {carries}"
                else:
                    name = f"Low clearance bridge #{struct_num}"
            else:
                # Low ON-bridge clearance (covered/tunnel-like structure)
                if carries:
                    name = f"Low on-bridge clearance: {carries}"
                else:
                    name = f"Low clearance bridge #{struct_num}"

            note_bits = [
                "FHWA NBI",
                f"{'under-bridge' if kind == 'under' else 'on-bridge'} clearance"
            ]
            if year and year not in ("0", "0000"):
                note_bits.append(f"built {year}")
            if kind == "under" and carries:
                note_bits.append(f"bridge carries {carries}")
            notes = "; ".join(note_bits)

            # Display address: for underpasses, the road-under ("crosses") is what
            # the driver is on.  For on-bridge, show the road carried.
            addr_parts = [crosses if kind == "under" else carries]
            addr = " ".join(p for p in addr_parts if p)[:200]

            bid = f"nbi-{state}-{struct_num.replace(' ', '')}"

            out.append({
                "id": bid,
                "name": name[:120],
                "addr": addr,
                "lat": lat,
                "lng": lng,
                "height_in": height_in,
                "height_label": inches_to_label(height_in),
                "notes": notes,
                "source": "FHWA National Bridge Inventory",
            })
    return out


# ---------------------------------------------------------------------------
# City assignment + merge
# ---------------------------------------------------------------------------

def load_cities() -> list[dict]:
    idx = json.loads(INDEX_PATH.read_text())
    return [c for c in idx if c.get("status") == "live"]


def nearest_city(lat: float, lng: float, cities: list[dict], max_km: float) -> Optional[dict]:
    best = None
    best_d = max_km * 1000.0
    for c in cities:
        d = haversine_m(lat, lng, c["lat"], c["lng"])
        if d < best_d:
            best_d = d
            best = c
    return best


def merge_bridges(slug: str, bridges: list[dict], dry_run: bool) -> tuple[int, int]:
    """Add bridges to a city JSON. Returns (added, skipped_dupe)."""
    city_path = CITIES_DIR / f"{slug}.json"
    if city_path.exists():
        data = json.loads(city_path.read_text())
    else:
        data = {"garages": [], "tunnels": [], "bridges": []}

    arr = data.setdefault("bridges", [])
    existing_ids = {b["id"] for b in arr}

    added = 0
    skipped = 0
    for b in bridges:
        if b["id"] in existing_ids:
            skipped += 1
            continue
        dupe = False
        for e in arr:
            if haversine_m(b["lat"], b["lng"], e["lat"], e["lng"]) < DEDUPE_M:
                dupe = True
                break
        if dupe:
            skipped += 1
            continue
        arr.append(b)
        existing_ids.add(b["id"])
        added += 1

    if not dry_run and added > 0:
        city_path.write_text(json.dumps(data, indent=2) + "\n")

    return added, skipped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_state(state: str, year: int, max_clearance_ft: float, dry_run: bool,
                  do_download: bool, cities: list[dict]) -> dict:
    yy = f"{year % 100:02d}"
    path = NBI_CACHE / f"{state.upper()}{yy}.txt"
    if do_download or not path.exists():
        try:
            path = download_state(state, year)
        except Exception as e:
            return {"state": state, "error": str(e)}

    if not path.exists():
        return {"state": state, "error": "no file"}

    print(f"[{state}] parsing {path.name}")
    try:
        bridges = parse_nbi_file(path, max_clearance_ft)
    except Exception as e:
        return {"state": state, "error": f"parse: {e}"}
    print(f"  found {len(bridges)} bridges with clearance <= {max_clearance_ft}ft")

    # Assign to cities
    by_city: dict[str, list] = {}
    unassigned = 0
    for b in bridges:
        c = nearest_city(b["lat"], b["lng"], cities, CITY_RADIUS_KM)
        if c is None:
            unassigned += 1
            continue
        by_city.setdefault(c["slug"], []).append(b)

    total_added = 0
    total_dupes = 0
    for slug, brs in by_city.items():
        added, dupes = merge_bridges(slug, brs, dry_run=dry_run)
        label = "[DRY]" if dry_run else ""
        print(f"  {label} {slug}: +{added} new (dupes {dupes}; candidates {len(brs)})")
        total_added += added
        total_dupes += dupes

    return {
        "state": state,
        "found": len(bridges),
        "added": total_added,
        "dupes": total_dupes,
        "unassigned": unassigned,
    }


def main():
    ap = argparse.ArgumentParser(description="FHWA NBI low-clearance bridge importer.")
    ap.add_argument("--state", help="Single state code (e.g. CA)")
    ap.add_argument("--all-states", action="store_true", help="Process all 50 states + DC + PR")
    ap.add_argument("--year", type=int, default=2023, help="NBI year (default 2023)")
    ap.add_argument("--max-clearance-ft", type=float, default=DEFAULT_MAX_CLEARANCE_FT,
                    help="Max clearance in feet to import (default 15)")
    ap.add_argument("--dry-run", action="store_true", help="Don't write to city files")
    ap.add_argument("--download-only", action="store_true",
                    help="Only download state files; don't parse/merge")
    ap.add_argument("--download", action="store_true", default=True,
                    help="Download missing state files (on by default)")
    ap.add_argument("--no-download", dest="download", action="store_false",
                    help="Skip download step (use only cached files)")
    args = ap.parse_args()

    if not args.state and not args.all_states:
        ap.error("Pass --state XX or --all-states")

    states = [args.state.upper()] if args.state else US_STATES
    cities = load_cities()
    print(f"Loaded {len(cities)} live cities. Processing {len(states)} state(s)…")

    summary = []
    for i, st in enumerate(states):
        if args.download_only:
            try:
                download_state(st, args.year)
            except Exception as e:
                print(f"  [{st}] ERROR: {e}")
            if i < len(states) - 1:
                time.sleep(1.0)
            continue
        r = process_state(st, args.year, args.max_clearance_ft, args.dry_run,
                          args.download, cities)
        summary.append(r)
        if i < len(states) - 1:
            time.sleep(1.0)

    if args.download_only:
        return

    total_found = sum(s.get("found", 0) for s in summary)
    total_added = sum(s.get("added", 0) for s in summary)
    total_dupes = sum(s.get("dupes", 0) for s in summary)
    total_unassigned = sum(s.get("unassigned", 0) for s in summary)
    errors = [s for s in summary if s.get("error")]

    print("\n=== SUMMARY ===")
    print(f"States processed: {len(summary)}")
    print(f"Low-clearance bridges found: {total_found}")
    print(f"Added (within {CITY_RADIUS_KM}km of a city): {total_added}")
    print(f"Dupes skipped:                              {total_dupes}")
    print(f"Outside any tracked city:                   {total_unassigned}")
    if errors:
        print(f"Errors: {len(errors)}")
        for e in errors:
            print(f"  - {e['state']}: {e['error']}")


if __name__ == "__main__":
    main()
