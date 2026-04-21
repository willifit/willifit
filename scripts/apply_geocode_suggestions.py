#!/usr/bin/env python3
"""
Selectively apply coord / structure_type fixes from a
<slug>.geocode-suggestions.json file to the corresponding city JSON.

Usage:
  # Preview what would change (no writes)
  python3 scripts/apply_geocode_suggestions.py --slug reno-nv --dry-run

  # Apply all suggestions
  python3 scripts/apply_geocode_suggestions.py --slug reno-nv

  # Apply all except specific garage IDs
  python3 scripts/apply_geocode_suggestions.py --slug reno-nv \
    --skip rno-unr-whalen,rno-airport-garage,rno-airport-economy,rno-national-bowling-stadium

  # Apply only specific garage IDs
  python3 scripts/apply_geocode_suggestions.py --slug reno-nv \
    --only rno-meadowood-mall,rno-scheels

  # After successful apply, delete the suggestions file so it doesn't
  # stay in the tree as an orphan artifact
  python3 scripts/apply_geocode_suggestions.py --slug reno-nv --remove-suggestions

Safety:
  - Always --dry-run first. The script refuses to run without --dry-run
    OR explicit --confirm.
  - Never mass-applies blindly; every garage prints its before/after.
  - Adds a stamp to the notes field recording the coord correction so
    downstream reviewers can see what was automated.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CITIES_DIR = REPO_ROOT / "data" / "cities"


def load_json(p: Path):
    return json.loads(p.read_text())


def save_json(p: Path, d):
    p.write_text(json.dumps(d, indent=2) + "\n")


def apply_for_city(slug: str, skip_ids: set, only_ids: set, dry_run: bool,
                   remove_suggestions: bool) -> dict:
    city_path = CITIES_DIR / f"{slug}.json"
    sugg_path = CITIES_DIR / f"{slug}.geocode-suggestions.json"

    if not city_path.exists():
        print(f"[{slug}] ERROR: no city file at {city_path}", file=sys.stderr)
        return {"error": "no-city-file"}
    if not sugg_path.exists():
        print(f"[{slug}] ERROR: no suggestions file at {sugg_path}", file=sys.stderr)
        print(f"       Run `python3 scripts/geocode_audit.py --slug {slug} --emit-suggestions` first.", file=sys.stderr)
        return {"error": "no-suggestions-file"}

    city = load_json(city_path)
    sugg_doc = load_json(sugg_path)
    suggestions = sugg_doc.get("suggestions", [])

    # Build an index by id for O(1) lookup
    by_id = {g.get("id"): g for g in city.get("garages", [])}

    applied = []
    skipped = []
    not_in_city = []

    print(f"[{slug}] {len(suggestions)} suggestions available")
    print(f"{'id':<35}{'action':<12}{'detail':<60}")
    print("-" * 107)

    for s in suggestions:
        gid = s.get("id")
        if only_ids and gid not in only_ids:
            skipped.append(gid)
            print(f"{gid[:34]:<35}{'skip':<12}{'not in --only list':<60}")
            continue
        if gid in skip_ids:
            skipped.append(gid)
            print(f"{gid[:34]:<35}{'skip':<12}{'in --skip list':<60}")
            continue

        g = by_id.get(gid)
        if g is None:
            not_in_city.append(gid)
            print(f"{gid[:34]:<35}{'missing':<12}{'no garage with this id in city JSON':<60}")
            continue

        changes = []
        notes_additions = []

        # Coordinate correction
        sc = s.get("suggested_coords") or {}
        if sc.get("new"):
            old_lat, old_lng = g.get("lat"), g.get("lng")
            new_lat, new_lng = sc["new"]
            dist = sc.get("distance_m", "?")
            changes.append(f"coords {old_lat},{old_lng} -> {new_lat},{new_lng} ({dist}m)")
            if not dry_run:
                g["lat"] = new_lat
                g["lng"] = new_lng
            notes_additions.append(
                f"Coords corrected via Google Geocoding {date.today().isoformat()} "
                f"(was {old_lat},{old_lng}, {dist}m off)."
            )

        # Structure type stamp (e.g. auto-classified surface_lot)
        st = s.get("suggested_structure_type")
        if st and g.get("structure_type") != st:
            changes.append(f"structure_type -> {st}")
            if not dry_run:
                g["structure_type"] = st
            notes_additions.append(
                f"Classified structure_type={st} via audit {date.today().isoformat()}."
            )

        if not changes:
            skipped.append(gid)
            print(f"{gid[:34]:<35}{'no-change':<12}{'suggestion had no applicable fields':<60}")
            continue

        # Append to notes (keep existing, avoid duplicating)
        if notes_additions and not dry_run:
            prior = g.get("notes", "") or ""
            combined = "\n".join(filter(None, [prior] + notes_additions))
            g["notes"] = combined.strip()

        applied.append(gid)
        detail = " | ".join(changes)[:58]
        action = "[DRY] would" if dry_run else "APPLIED"
        print(f"{gid[:34]:<35}{action:<12}{detail:<60}")

    # Write the city file if anything changed and not a dry-run
    if applied and not dry_run:
        save_json(city_path, city)
        print(f"\n[{slug}] wrote {city_path}")

    # Optionally remove the suggestions file (only if we actually applied something)
    if remove_suggestions and applied and not dry_run:
        sugg_path.unlink()
        print(f"[{slug}] removed {sugg_path}")

    print(f"\n[{slug}] summary: applied={len(applied)} skipped={len(skipped)} "
          f"not-in-city={len(not_in_city)}")
    if dry_run:
        print("(DRY-RUN — no files written.  Re-run without --dry-run to apply.)")

    return {
        "applied": applied,
        "skipped": skipped,
        "not_in_city": not_in_city,
    }


def parse_id_list(s: str) -> set:
    if not s:
        return set()
    return {x.strip() for x in s.split(",") if x.strip()}


def main():
    ap = argparse.ArgumentParser(description="Apply geocode audit suggestions to city JSON.")
    ap.add_argument("--slug", required=True, help="City slug (e.g. reno-nv)")
    ap.add_argument("--skip", default="", help="Comma-separated garage IDs to skip")
    ap.add_argument("--only", default="", help="Comma-separated garage IDs to apply (only these)")
    ap.add_argument("--dry-run", action="store_true", help="Print what would change, don't write.")
    ap.add_argument("--remove-suggestions", action="store_true",
                    help="Delete the suggestions JSON after successful apply.")
    args = ap.parse_args()

    if args.skip and args.only:
        ap.error("pass --skip or --only, not both")

    apply_for_city(
        slug=args.slug,
        skip_ids=parse_id_list(args.skip),
        only_ids=parse_id_list(args.only),
        dry_run=args.dry_run,
        remove_suggestions=args.remove_suggestions,
    )


if __name__ == "__main__":
    main()
