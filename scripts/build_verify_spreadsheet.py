#!/usr/bin/env python3
"""
build_verify_spreadsheet.py — generate a Google Sheets-compatible CSV of
every parking entry across all cities that still needs manual verification.

Why:
  After running find_entrances.py + auto_verify.py, a chunk of garages
  remain unverified because their signs live inside the ramp (invisible
  to Street View) or OSM didn't tag their entrance.  The practical fix is
  a human opening Google Maps, panning to the sign, pasting the URL + the
  clearance number into a spreadsheet, then running bulk_update.py.

  This script builds that spreadsheet — one row per garage needing review,
  sorted by city then name, with a click-through maps_search_url that
  drops you right into Google Maps for each one.

Filters out:
  - Already-verified entries (height_in is populated)
  - Anything stamped structure_type=surface_lot
  - Anything the surface-lot heuristic flags (if streetview_verify.py's
    classify_structure is importable)

Output:
  data/manual_verify_todo.csv  (default)

Column layout (matches bulk_update.py's TSV expectations when re-saved
as TSV; commas auto-convert when you paste into Google Sheets):

  city         — city slug  (e.g. "las-vegas-nv")
  garage_id    — internal id the JSON uses
  name         — human-readable name  (what to search in Google Maps)
  addr         — street address
  maps_search_url  — click-through URL that searches Google Maps for
                     "name addr city state"
  url          — EMPTY (you paste the pano URL here after verifying)
  height       — EMPTY (you type "7'6\"", "surface", "skip", etc.)
  notes        — EMPTY (optional extra context)
  status       — why this row is in the list (unverified / no-entrance)

Usage:
  python3 scripts/build_verify_spreadsheet.py
  python3 scripts/build_verify_spreadsheet.py --slug las-vegas-nv
  python3 scripts/build_verify_spreadsheet.py --out my_list.csv

Safe to re-run any time: it just re-scans the DB and rewrites the file.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from urllib.parse import quote_plus

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"
DEFAULT_OUT = REPO_ROOT / "data" / "manual_verify_todo.csv"

# Reuse the surface-lot heuristic from streetview_verify.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from streetview_verify import classify_structure as _classify_structure
except Exception:
    _classify_structure = None


def google_maps_search_url(name: str, addr: str, city_name: str, state: str) -> str:
    """Build a google.com/maps URL that searches for the place.  When the
    user clicks this in the spreadsheet, Google Maps opens pre-focused on
    the garage so they can enter Street View."""
    parts = [p for p in [name, addr, city_name, state] if p]
    q = quote_plus(" ".join(parts))
    return f"https://www.google.com/maps/search/?api=1&query={q}"


def should_include(g: dict) -> tuple[bool, str]:
    """Return (include, status_label).  Reasons:
       - unverified     : height_in is null and not classified as surface
       - no-entrance    : has height but no pano_id (verifiable but pinless)
       Excluded:
       - verified       : height_in is set
       - surface_lot    : stamped or heuristically classified
    """
    if g.get("structure_type") == "surface_lot":
        return (False, "")

    if _classify_structure is not None:
        kind = _classify_structure(g)
        if kind == "surface_lot":
            return (False, "")

    if g.get("height_in") is None:
        return (True, "unverified")

    # Has a height but no pano_id pinned — still worth letting user
    # attach a pano for the frontend to show the right view.
    if not g.get("pano_id"):
        return (True, "height-ok-needs-pano")

    return (False, "")


def rows_for_city(slug: str, city_name: str, state: str, data: dict) -> list:
    out = []
    for bucket in ("garages", "tunnels", "bridges"):
        for g in data.get(bucket, []):
            keep, status = should_include(g)
            if not keep:
                continue
            out.append({
                "city": slug,
                "garage_id": g.get("id", ""),
                "name": g.get("name", ""),
                "addr": g.get("addr", ""),
                "maps_search_url": google_maps_search_url(
                    g.get("name", ""), g.get("addr", ""),
                    city_name, state
                ),
                "url": "",
                "height": "",
                "notes": "",
                "status": status,
            })
    return out


def main():
    ap = argparse.ArgumentParser(description="Generate a spreadsheet of garages needing manual verification.")
    ap.add_argument("--slug", help="Single city slug")
    ap.add_argument("--slugs", help="Comma-separated slugs")
    ap.add_argument("--all", action="store_true", help="All live cities (default)")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"Output CSV path (default: {DEFAULT_OUT})")
    ap.add_argument("--include-pano-only", action="store_true",
                    help="Also include garages that have a verified height but no "
                         "pano_id pinned (they'd benefit from a user-verified pano "
                         "for the hero view, but the heights themselves are fine). "
                         "Default: skip those -- focus on genuinely missing heights.")
    args = ap.parse_args()

    idx = json.loads(INDEX_PATH.read_text())

    if args.slug:
        targets = [args.slug]
    elif args.slugs:
        targets = [s.strip() for s in args.slugs.split(",") if s.strip()]
    else:
        targets = [c["slug"] for c in idx if c.get("status") == "live"]

    # Index by slug for city_name + state lookup
    by_slug = {c["slug"]: c for c in idx}

    all_rows = []
    cities_scanned = 0
    cities_empty = 0

    for slug in targets:
        city_path = CITIES_DIR / f"{slug}.json"
        if not city_path.exists():
            continue
        cities_scanned += 1
        meta = by_slug.get(slug, {})
        city_name = meta.get("name", slug)
        state = meta.get("state", "")
        try:
            data = json.loads(city_path.read_text())
        except Exception:
            continue
        rows = rows_for_city(slug, city_name, state, data)
        if not args.include_pano_only:
            rows = [r for r in rows if r["status"] == "unverified"]
        if not rows:
            cities_empty += 1
            continue
        all_rows.extend(rows)

    # Sort by city then name for easy batch-per-city workflow
    all_rows.sort(key=lambda r: (r["city"], r["name"].lower()))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="") as f:
        fieldnames = ["city", "garage_id", "name", "addr",
                      "maps_search_url", "url", "height", "notes", "status"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    # Summary
    by_city = {}
    by_status = {}
    for r in all_rows:
        by_city[r["city"]] = by_city.get(r["city"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    print(f"Wrote {len(all_rows)} row(s) to {out_path}")
    print(f"Cities scanned:     {cities_scanned}")
    print(f"Cities with nothing left to verify: {cities_empty}")
    print(f"Status breakdown:   {dict(by_status)}")
    if len(by_city) <= 20:
        print("Rows per city:")
        for city, count in sorted(by_city.items(), key=lambda kv: -kv[1]):
            print(f"  {city:<30} {count}")
    else:
        print(f"Rows spread across {len(by_city)} cities — top 10:")
        for city, count in sorted(by_city.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {city:<30} {count}")


if __name__ == "__main__":
    main()
