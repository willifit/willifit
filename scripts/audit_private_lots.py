#!/usr/bin/env python3
"""
audit_private_lots.py — audit existing parking-lot entries across all
city JSONs for institutional / private-access signals that earlier
imports missed.

Why this exists:
  Some OSM-imported and hand-curated entries are actually private or
  permit-required lots (school lots, hospital staff, military bases,
  employee lots).  Our data is supposed to be "where the general public
  can park an RV/truck" -- private lots are misleading inclusions.

Two passes:

  1. OFFLINE pass — scan every entry across data/cities/*.json for
     name / operator / notes / source / addr containing an
     institutional blacklist keyword (school, staff, courthouse, etc.)
     with word-boundary matching.  Zero network calls.

  2. LIVE-OSM pass — for entries with id matching ^osm-[wn]<id>$,
     batch-query Overpass for the current way/node tags and flag if
     access is in (private / permit / no / customers / staff) or the
     operator tag matches the institutional blacklist.  Batched 50
     ways per query, 3s sleep between batches (polite).

Output:
  Markdown-ish report to stdout by default.  --out <file> to write to
  a file.  --json for machine-readable.

Does NOT modify any JSON by default.  Pass --apply to delete flagged
entries in place after writing a backup.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional
from urllib import request, parse, error

REPO_ROOT = Path(__file__).resolve().parent.parent
CITIES_DIR = REPO_ROOT / "data" / "cities"

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
USER_AGENT = "willifit-private-audit/0.1"
REQUEST_TIMEOUT = 60
BATCH_SIZE = 50
SLEEP_BETWEEN_BATCHES = 3.0

# Tightened keyword set — dropped the noisy ones ("district", "base",
# "county", "admin", "administration", "private", "teacher", "academy",
# "administration") that threw false positives on entries like
# "Anaheim Packing District", "Sandia Peak Tramway Base", "Truist Park
# — The Battery Atlanta".  Only kept keywords that are near-certain
# institutional/private markers in a lot NAME.
INSTITUTIONAL_KEYWORDS = (
    # Religious (private gatherings)
    "church", "chapel", "cathedral", "temple", "mosque", "synagogue",
    # Schools (public schools = staff/student use)
    "school", "elementary", "middle school", "high school", "kindergarten",
    # Higher-ed (permit-required lots dominate)
    "university", "college", "campus",
    # Medical (patient + staff lots)
    "hospital", "clinic", "medical center",
    # Government / civic
    "courthouse", "city hall", "municipal", "government", "dmv",
    "police", "fire station",
    "prison", "jail", "correctional", "detention",
    # Military
    "military", "army", "navy", "marine corps", "air force", "space force",
    # Explicit access markers
    "employee", "staff only", "faculty",
)

PRIVATE_ACCESS_TAGS = {
    "private", "permit", "no", "customers", "staff", "employees", "students",
}

_KW_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in INSTITUTIONAL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def find_institutional_keyword(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = _KW_RE.search(text)
    return m.group(1).lower() if m else None


def offline_scan(entry: dict) -> Optional[str]:
    """Return a human-readable reason the entry looks institutional, or None.

    Only the `name` field is scanned -- notes and addr contain too much
    free text ('district nearby', 'Anaheim Packing District', 'Sandia
    Peak Tramway Base') for keyword matching to be reliable.  `source`
    is checked too because operator-credited sources like 'UNLV Parking
    Services' are strong private-access signals."""
    name = entry.get("name") or ""
    # Public-facing visitor garages at hospitals / universities are OK
    # -- if the name explicitly says "Visitor", treat that as an override.
    if re.search(r"\bvisitor(?:'s)?\b", name, re.IGNORECASE):
        return None
    for field in ("name", "source"):
        val = entry.get(field) or ""
        hit = find_institutional_keyword(val)
        if hit:
            return f"{field}: '{hit}'"
    return None


def fetch_overpass(query: str) -> Optional[dict]:
    data = parse.urlencode({"data": query}).encode("ascii")
    last_err: Optional[Exception] = None
    for url in OVERPASS_MIRRORS:
        try:
            req = request.Request(
                url, data=data, headers={"User-Agent": USER_AGENT}, method="POST"
            )
            with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            print(f"    Overpass fail on {url}: {e}", file=sys.stderr)
            time.sleep(2)
    print(f"    Overpass: all mirrors failed -- {last_err}", file=sys.stderr)
    return None


def live_osm_batch(osm_refs: list[tuple[str, int]]) -> dict[tuple[str, int], dict]:
    """osm_refs is [(kind, id), ...] where kind is 'w' or 'n'.
    Returns {(kind, id): {tags...}}."""
    if not osm_refs:
        return {}
    way_ids = [i for (k, i) in osm_refs if k == "w"]
    node_ids = [i for (k, i) in osm_refs if k == "n"]
    parts = []
    if way_ids:
        parts.append(f"way(id:{','.join(str(i) for i in way_ids)});")
    if node_ids:
        parts.append(f"node(id:{','.join(str(i) for i in node_ids)});")
    if not parts:
        return {}
    query = f"[out:json][timeout:60];({''.join(parts)});out tags;"
    resp = fetch_overpass(query)
    out: dict[tuple[str, int], dict] = {}
    if not resp:
        return out
    for el in resp.get("elements", []):
        t = el.get("type")
        kind = "w" if t == "way" else "n" if t == "node" else None
        if not kind:
            continue
        out[(kind, el["id"])] = el.get("tags", {}) or {}
    return out


def live_scan(osm_tags: dict) -> Optional[str]:
    """Given current OSM tags for a way/node, decide if it reads private."""
    access = (osm_tags.get("access") or "").lower()
    if access in PRIVATE_ACCESS_TAGS:
        return f"access={access}"
    for field in ("name", "operator"):
        hit = find_institutional_keyword(osm_tags.get(field))
        if hit:
            return f"OSM {field}: '{hit}'"
    return None


OSM_ID_RE = re.compile(r"^osm-(w|n)(\d+)$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="write report to file (default: stdout)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--offline-only", action="store_true", help="skip OSM queries")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete flagged entries (writes .bak first)")
    args = ap.parse_args()

    out_fh = open(args.out, "w") if args.out else sys.stdout

    # Pass 1: offline scan.
    # Also collect OSM refs for pass 2.
    findings: list[dict] = []
    osm_refs_to_check: list[tuple[str, int, str, dict]] = []  # (kind, id, slug, entry)

    files = sorted(CITIES_DIR.glob("*.json"))
    total_entries = 0
    for f in files:
        slug = f.stem
        data = json.loads(f.read_text())
        # Only scan `garages` for institutional markers.  Tunnels and
        # bridges are infrastructure -- a bridge called "University
        # Boulevard Underpass" is a bridge over a street called
        # "University Blvd", not a university-owned institution.
        for bucket in ("garages",):
            for entry in data.get(bucket, []):
                total_entries += 1
                off_reason = offline_scan(entry)
                if off_reason:
                    findings.append({
                        "city": slug,
                        "bucket": bucket,
                        "id": entry.get("id"),
                        "name": entry.get("name"),
                        "pass": "offline",
                        "reason": off_reason,
                        "source": entry.get("source"),
                    })
                # Queue live check regardless (even institutional-named
                # entries may also have access=private; we want the
                # full picture).
                m = OSM_ID_RE.match(entry.get("id") or "")
                if m and not args.offline_only:
                    osm_refs_to_check.append((m.group(1), int(m.group(2)), slug, entry))

    print(f"Scanned {total_entries} entries across {len(files)} cities.", file=sys.stderr)
    print(f"Offline flags: {len(findings)}", file=sys.stderr)

    # Pass 2: live OSM.  Batched to stay polite.
    if not args.offline_only and osm_refs_to_check:
        print(f"Live OSM check: {len(osm_refs_to_check)} OSM-linked entries, "
              f"batched {BATCH_SIZE}/query, {SLEEP_BETWEEN_BATCHES}s between batches "
              f"(~{len(osm_refs_to_check) * SLEEP_BETWEEN_BATCHES / BATCH_SIZE:.0f}s)",
              file=sys.stderr)
        # Lookup per (kind, id)
        tag_map: dict[tuple[str, int], dict] = {}
        for i in range(0, len(osm_refs_to_check), BATCH_SIZE):
            batch = osm_refs_to_check[i:i + BATCH_SIZE]
            refs = [(k, id_) for (k, id_, _, _) in batch]
            print(f"  batch {i // BATCH_SIZE + 1}/{(len(osm_refs_to_check) + BATCH_SIZE - 1) // BATCH_SIZE}...",
                  file=sys.stderr)
            tag_map.update(live_osm_batch(refs))
            time.sleep(SLEEP_BETWEEN_BATCHES)

        # Apply live filter; dedupe against offline findings.
        seen_ids = {(f["city"], f["id"]) for f in findings}
        for (kind, id_, slug, entry) in osm_refs_to_check:
            tags = tag_map.get((kind, id_))
            if not tags:
                continue
            live_reason = live_scan(tags)
            if not live_reason:
                continue
            key = (slug, entry.get("id"))
            if key in seen_ids:
                # Already flagged offline — just enrich reason.
                for f in findings:
                    if f["city"] == slug and f["id"] == entry.get("id"):
                        f["reason"] += f"; {live_reason}"
                        break
            else:
                findings.append({
                    "city": slug,
                    "bucket": "?",  # not re-walking to avoid O(n^2)
                    "id": entry.get("id"),
                    "name": entry.get("name"),
                    "pass": "live-osm",
                    "reason": live_reason,
                    "source": entry.get("source"),
                })

    # Report.
    if args.json:
        json.dump({"findings": findings, "total_scanned": total_entries}, out_fh, indent=2)
        print(file=out_fh)
    else:
        print(f"# Private-lot audit — {date.today().isoformat()}\n", file=out_fh)
        print(f"Scanned **{total_entries}** entries across **{len(files)}** cities.", file=out_fh)
        print(f"Flagged **{len(findings)}** for likely institutional / private-access status.\n", file=out_fh)
        if findings:
            by_city: dict[str, list[dict]] = {}
            for f in findings:
                by_city.setdefault(f["city"], []).append(f)
            for city in sorted(by_city):
                print(f"## {city}", file=out_fh)
                for f in by_city[city]:
                    print(f"- `{f['id']}` **{f['name']}** — {f['reason']}", file=out_fh)
                print(file=out_fh)

    # --apply: delete flagged entries after writing a backup.
    if args.apply and findings:
        flagged_by_city: dict[str, set[str]] = {}
        for f in findings:
            flagged_by_city.setdefault(f["city"], set()).add(f["id"])
        for slug, ids in flagged_by_city.items():
            path = CITIES_DIR / f"{slug}.json"
            bak = path.with_suffix(".json.bak")
            shutil.copy2(path, bak)
            data = json.loads(path.read_text())
            for bucket in ("garages", "tunnels", "bridges"):
                before = len(data.get(bucket, []))
                data[bucket] = [e for e in data.get(bucket, []) if e.get("id") not in ids]
                after = len(data[bucket])
                if before != after:
                    print(f"[{slug}] {bucket}: removed {before - after} entries", file=sys.stderr)
            path.write_text(json.dumps(data, indent=2) + "\n")
        print(f"\n.bak files saved alongside each touched JSON; delete them after review.",
              file=sys.stderr)

    if out_fh is not sys.stdout:
        out_fh.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
