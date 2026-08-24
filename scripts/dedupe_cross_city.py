#!/usr/bin/env python3
"""
WillIFit — cross-city duplicate cleanup.

The Overpass import dedupes by id + 75m proximity WITHIN a city file only, so
entries in adjacent-metro overlap zones (Portland/Vancouver, NYC/Jersey
City/Newark, Phoenix/Mesa/Scottsdale, …) were imported into multiple
data/cities/*.json files. Duplicate copies waste paid verification passes and
silently diverge when one copy gets edited.

This script finds every (section, id) present in more than one city file and
collapses each group to ONE canonical copy:

  canonical file   = the listing city whose index.json center is nearest
  canonical content = the copy with the newest verification stamp
                      (max of verified_on / sv_checked; ties → the canonical
                      file's copy), then null fields are filled from the
                      other copies so no verification data is lost
  other copies     = deleted

Conflicts (copies that BOTH carry non-null, differing values for a field) are
resolved by that same recency rule and every one is written to the audit log
for human review.

After applying, index.json garage_count is synced to len(garages) for every
city (it had drifted in ~45 cities), and you should regenerate pages:

  python3 scripts/generate_city_pages.py
  python3 scripts/generate_bridges_page.py

Usage:
  python3 scripts/dedupe_cross_city.py                # dry run, prints summary
  python3 scripts/dedupe_cross_city.py --apply        # writes city files + index
  python3 scripts/dedupe_cross_city.py --apply --log docs/dedupe-cross-city.md
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict, Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"

SECTIONS = ("garages", "tunnels", "bridges")

# Fields whose values constitute "verification payload" worth merging/logging.
# name/addr/lat/lng are identity fields — differences there are logged too.
DATE_FIELDS = ("verified_on", "sv_checked")


def haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    R = 6_371_000.0
    to_r = lambda d: d * math.pi / 180.0
    dlat = to_r(b_lat - a_lat)
    dlng = to_r(b_lng - a_lng)
    s = math.sin(dlat / 2) ** 2 + math.cos(to_r(a_lat)) * math.cos(to_r(b_lat)) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(s))


def recency(entry: dict) -> str:
    """Newest ISO date stamped on the entry, or '' if never verified."""
    return max((entry.get(f) or "" for f in DATE_FIELDS), default="")


def merge_group(copies: list[tuple[str, dict]], index: dict) -> tuple[str, dict, list]:
    """copies = [(slug, entry), ...] for one duplicated id.

    Returns (canonical_slug, merged_entry, conflict_rows).
    """
    scored = sorted(
        copies,
        key=lambda se: haversine_m(
            se[1]["lat"], se[1]["lng"], index[se[0]]["lat"], index[se[0]]["lng"]
        ),
    )
    canonical_slug = scored[0][0]

    # Content base: newest verification wins; ties go to the canonical copy.
    # (scored[0] is canonical, so a stable sort keyed on recency alone would
    # not guarantee that — sort explicitly.)
    by_content = sorted(
        scored, key=lambda se: (recency(se[1]), se[0] == canonical_slug), reverse=True
    )
    base_slug, base = by_content[0]
    merged = dict(base)
    field_src = {k: base_slug for k, v in base.items() if v is not None}

    conflicts = []
    # Fill nulls from the remaining copies: canonical copy first, then by recency.
    fill_order = [se for se in by_content if se[1] is not base]
    fill_order.sort(key=lambda se: (se[0] != canonical_slug, recency(se[1])))
    for slug, other in fill_order:
        for k, v in other.items():
            if v is None:
                continue
            if merged.get(k) is None:
                merged[k] = v
                field_src[k] = slug
            elif merged[k] != v:
                conflicts.append(
                    {
                        "field": k,
                        "kept": merged[k],
                        "kept_from": field_src.get(k, base_slug),
                        "dropped": v,
                        "dropped_from": slug,
                    }
                )
    return canonical_slug, merged, conflicts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--log", default=None, help="write a markdown audit log here")
    args = ap.parse_args()

    index = {c["slug"]: c for c in json.loads(INDEX_PATH.read_text())}

    files = {}
    by_id = defaultdict(list)
    for path in sorted(CITIES_DIR.glob("*.json")):
        slug = path.stem
        data = json.loads(path.read_text())
        files[slug] = data
        for section in SECTIONS:
            for entry in data.get(section, []):
                if entry.get("id"):
                    by_id[(section, entry["id"])].append((slug, entry))

    groups = {k: v for k, v in by_id.items() if len({s for s, _ in v}) > 1}

    deleted = Counter()  # (slug, section) -> n
    changed_files = set()
    log_rows = []
    all_conflicts = []

    for (section, eid), copies in sorted(groups.items()):
        canonical_slug, merged, conflicts = merge_group(copies, index)

        for slug, entry in copies:
            arr = files[slug][section]
            if slug == canonical_slug:
                arr[arr.index(entry)] = merged
                if merged != entry:
                    changed_files.add(slug)
            else:
                arr.remove(entry)
                deleted[(slug, section)] += 1
                changed_files.add(slug)

        losers = [s for s, _ in copies if s != canonical_slug]
        log_rows.append((section, eid, merged.get("name"), canonical_slug, losers, conflicts))
        for c in conflicts:
            all_conflicts.append((section, eid, merged.get("name"), c))

    n_del = sum(deleted.values())
    per_section = Counter()
    for (slug, section), n in deleted.items():
        per_section[section] += n
    print(f"Duplicate groups: {len(groups)}")
    print(
        f"Copies to delete: {n_del} "
        f"(garages {per_section['garages']}, tunnels {per_section['tunnels']}, "
        f"bridges {per_section['bridges']})"
    )
    print(f"City files touched: {len(changed_files)}")
    print(f"Field conflicts (both copies non-null, differing): {len(all_conflicts)} "
          f"across {len({(s, e) for s, e, _n, _c in all_conflicts})} entries")

    # garage_count sync — for every city, not just deduped ones (45 had drifted).
    count_fixes = []
    for slug, data in files.items():
        actual = len(data.get("garages", []))
        if index[slug].get("garage_count") != actual:
            count_fixes.append((slug, index[slug].get("garage_count"), actual))
            index[slug]["garage_count"] = actual
    print(f"index.json garage_count corrections: {len(count_fixes)}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to write.")
        return

    for slug in sorted(changed_files):
        path = CITIES_DIR / f"{slug}.json"
        path.write_text(json.dumps(files[slug], indent=2) + "\n")
    INDEX_PATH.write_text(json.dumps(list(index.values()), indent=2) + "\n")
    print(f"\nWrote {len(changed_files)} city files + index.json")

    if args.log:
        out = Path(args.log)
        if not out.is_absolute():
            out = REPO_ROOT / out
        lines = [
            "# Cross-city dedupe audit log",
            "",
            f"{len(groups)} duplicate (section, id) groups collapsed; {n_del} copies "
            f"removed ({per_section['garages']} garages, {per_section['tunnels']} tunnels, "
            f"{per_section['bridges']} bridges). Canonical file = nearest index.json city "
            "center; content = newest verification stamp, nulls filled from other copies.",
            "",
            "## Field conflicts (kept vs dropped — review these)",
            "",
        ]
        for section, eid, name, c in all_conflicts:
            lines.append(
                f"- `[{section}] {eid}` **{name}** `{c['field']}`: "
                f"kept {c['kept_from']} = `{str(c['kept'])[:90]}` / "
                f"dropped {c['dropped_from']} = `{str(c['dropped'])[:90]}`"
            )
        lines += ["", "## All groups", ""]
        for section, eid, name, canonical_slug, losers, _ in log_rows:
            lines.append(
                f"- `[{section}] {eid}` {name} → kept in **{canonical_slug}**, "
                f"removed from {', '.join(losers)}"
            )
        lines += ["", "## garage_count corrections", ""]
        for slug, old, new in sorted(count_fixes):
            lines.append(f"- {slug}: {old} → {new}")
        out.write_text("\n".join(lines) + "\n")
        print(f"Audit log: {out}")


if __name__ == "__main__":
    main()
