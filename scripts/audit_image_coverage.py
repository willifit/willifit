#!/usr/bin/env python3
"""
audit_image_coverage.py — find entries where the detail panel will
show nothing useful.

Why this exists:
  The app's detail-panel hero auto-aims a Google Street View pano at
  each entry's lat/lng.  If Google has no nearby coverage (common for
  bridges over rail, tunnels under buildings, or coords that drifted
  during a data import) the hero renders a generic empty-state instead
  of the actual location.  `lv-tropicana-rail` is the canonical case.

  This audit sweeps every entry across data/cities/*.json and classifies
  it by Street View coverage using Google's free metadata endpoint
  (metadata calls don't count against the image-tile quota).

Classifications:
  OK             - Street View has a pano within `radius` of the coords
                   (the hero will load fine even with no explicit pano_id)
  OK_PANNED      - Coords lack nearby coverage, but an explicit pano_id
                   IS set in the entry and that pano resolves.  Safe.
  ZERO_RESULTS   - Coords have no Street View coverage within `radius`.
                   Hero will show the empty state.
  STALE_PANO     - pano_id is set but Google no longer serves it
                   (panos get retired when streets are re-imaged).
  NO_COORDS      - Entry has no usable lat/lng.
  OUT_OF_BOUNDS  - Coords are obviously bogus (0,0, outside North
                   America, etc.).
  API_ERROR      - Metadata request failed.  Not flagged as a data
                   problem; just means we couldn't check.

Requires:
  export GOOGLE_MAPS_API_KEY_CLI=AIza...     # a key WITHOUT referrer
                                             # restrictions (the public
                                             # frontend key is locked to
                                             # willifit.ai origins and
                                             # won't work from CLI)

Usage:
  python3 scripts/audit_image_coverage.py --slug las-vegas-nv --dry-run
  python3 scripts/audit_image_coverage.py --all --out /tmp/image-audit.md
  python3 scripts/audit_image_coverage.py --all --json > audit.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional
from urllib import request, parse, error

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"

# Auto-load .env so user doesn't have to re-export keys every session.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _env  # noqa: F401
except ImportError:
    pass

API_KEY = (
    os.environ.get("GOOGLE_MAPS_API_KEY_CLI")
    or os.environ.get("GOOGLE_MAPS_API_KEY")
    or ""
)

METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
REQUEST_TIMEOUT = 10
DEFAULT_RADIUS_M = 75  # meters — matches app's auto-aim tolerance
SLEEP_BETWEEN = 0.05   # 50ms between calls => ~20 req/s, well under Google's limit

# Rough continental-US bounding box + Alaska + Hawaii + Puerto Rico.
# Anything outside these has almost certainly drifted during an import.
VALID_LAT_RANGES = [(17.5, 72.0)]
VALID_LNG_RANGES = [(-180.0, -64.0)]


def in_valid_bounds(lat: float, lng: float) -> bool:
    if any(a <= lat <= b for (a, b) in VALID_LAT_RANGES) and any(
        a <= lng <= b for (a, b) in VALID_LNG_RANGES
    ):
        return True
    return False


def _http_get_json(url: str) -> Optional[dict]:
    try:
        req = request.Request(url, headers={"User-Agent": "willifit-image-audit/0.1"})
        with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        return {"status": "HTTP_ERROR", "code": e.code}
    except Exception:
        return None


def check_by_location(lat: float, lng: float, radius: int = DEFAULT_RADIUS_M) -> dict:
    q = parse.urlencode(
        {"location": f"{lat},{lng}", "radius": radius, "key": API_KEY}
    )
    out = _http_get_json(f"{METADATA_URL}?{q}")
    return out or {"status": "API_ERROR"}


def check_by_pano(pano_id: str) -> dict:
    q = parse.urlencode({"pano": pano_id, "key": API_KEY})
    out = _http_get_json(f"{METADATA_URL}?{q}")
    return out or {"status": "API_ERROR"}


def classify(entry: dict) -> tuple[str, str]:
    """Returns (classification, detail)."""
    lat = entry.get("lat")
    lng = entry.get("lng")
    if lat is None or lng is None:
        return ("NO_COORDS", "")
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return ("NO_COORDS", "not numeric")
    if not in_valid_bounds(lat, lng):
        return ("OUT_OF_BOUNDS", f"{lat},{lng}")

    pano = entry.get("pano_id")
    # If an explicit pano is set, check it first -- that's what the app uses.
    if pano:
        pano_meta = check_by_pano(pano)
        time.sleep(SLEEP_BETWEEN)
        status = pano_meta.get("status")
        if status == "OK":
            return ("OK_PANNED", f"pano {pano} ok")
        else:
            # Pano is stale -- but coords might still auto-aim to something
            # else.  Fall through to the location check and mark it.
            loc = check_by_location(lat, lng)
            time.sleep(SLEEP_BETWEEN)
            lstatus = loc.get("status")
            if lstatus == "OK":
                return ("STALE_PANO", f"pano {pano} -> {status}, but coords have coverage")
            return ("STALE_PANO", f"pano {pano} -> {status}, AND coords have no coverage")

    # No explicit pano -- does auto-aim have anything to work with?
    loc = check_by_location(lat, lng)
    time.sleep(SLEEP_BETWEEN)
    status = loc.get("status")
    if status == "OK":
        return ("OK", "")
    if status == "ZERO_RESULTS":
        return ("ZERO_RESULTS", "no pano within radius")
    return ("API_ERROR", str(status))


def process(cities: list[dict], args) -> dict:
    report = {
        "NO_COORDS": [],
        "OUT_OF_BOUNDS": [],
        "ZERO_RESULTS": [],
        "STALE_PANO": [],
        "API_ERROR": [],
        # OK / OK_PANNED are not tracked (no-op success)
    }
    scanned = 0
    ok = 0
    for city in cities:
        slug = city["slug"]
        path = CITIES_DIR / f"{slug}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        buckets = ("garages", "tunnels", "bridges")
        city_hits = 0
        for bucket in buckets:
            for entry in data.get(bucket, []):
                scanned += 1
                cls, detail = classify(entry)
                if cls in ("OK", "OK_PANNED"):
                    ok += 1
                    continue
                report[cls].append({
                    "city": slug,
                    "bucket": bucket,
                    "id": entry.get("id"),
                    "name": entry.get("name"),
                    "lat": entry.get("lat"),
                    "lng": entry.get("lng"),
                    "pano_id": entry.get("pano_id"),
                    "detail": detail,
                })
                city_hits += 1
        flagged_so_far = sum(len(v) for v in report.values())
        print(
            f"[{slug:<22}] {city_hits:>3} flagged "
            f"(cumulative scanned={scanned}, ok={ok}, flagged={flagged_so_far})",
            file=sys.stderr,
        )
    return {"report": report, "scanned": scanned, "ok": ok}


def write_markdown(result: dict, out):
    report = result["report"]
    total_flagged = sum(len(v) for v in report.values())
    print(f"# Image-coverage audit — {date.today().isoformat()}\n", file=out)
    print(f"Scanned **{result['scanned']}** entries, **{result['ok']}** clean, **{total_flagged}** flagged.\n", file=out)
    order = [
        ("STALE_PANO", "Pano ID set but Google no longer serves it — clear `pano_id` + re-verify"),
        ("ZERO_RESULTS", "Coords have no Street View coverage — hero will show empty state"),
        ("OUT_OF_BOUNDS", "Coords outside North America — almost certainly bogus"),
        ("NO_COORDS", "Entry has no usable lat/lng"),
        ("API_ERROR", "Metadata request failed (network / key / rate)"),
    ]
    for key, desc in order:
        items = report[key]
        if not items:
            continue
        print(f"\n## {key} — {len(items)} flagged\n", file=out)
        print(f"_{desc}_\n", file=out)
        by_city: dict[str, list[dict]] = {}
        for it in items:
            by_city.setdefault(it["city"], []).append(it)
        for city in sorted(by_city):
            print(f"### {city} ({len(by_city[city])})", file=out)
            for it in by_city[city]:
                tail = f" — {it['detail']}" if it.get("detail") else ""
                print(f"- `{it['id']}` **{it['name']}** (lat {it['lat']}, lng {it['lng']}){tail}", file=out)
            print(file=out)


def main():
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--slug", help="One city")
    group.add_argument("--slugs", help="Comma-separated slugs")
    group.add_argument("--all", action="store_true", help="All live cities")
    ap.add_argument("--out", help="Markdown report path (default: stdout)")
    ap.add_argument("--json", action="store_true", help="Write raw JSON instead of markdown")
    args = ap.parse_args()

    if not API_KEY:
        print(
            "ERROR: set GOOGLE_MAPS_API_KEY_CLI (or GOOGLE_MAPS_API_KEY) to a\n"
            "Google Maps key WITHOUT referrer restrictions.  The public\n"
            "frontend key in index.html is locked to willifit.ai origins and\n"
            "won't work from the CLI.",
            file=sys.stderr,
        )
        return 2

    idx = json.loads(INDEX_PATH.read_text())
    live = [c for c in idx if c.get("status") == "live"]
    if args.slug:
        cities = [c for c in live if c["slug"] == args.slug]
    elif args.slugs:
        wanted = {s.strip() for s in args.slugs.split(",") if s.strip()}
        cities = [c for c in live if c["slug"] in wanted]
    else:
        cities = live
    if not cities:
        print("No cities matched.", file=sys.stderr)
        return 1

    print(f"Auditing {len(cities)} cities...\n", file=sys.stderr)
    result = process(cities, args)

    out_fh = open(args.out, "w") if args.out else sys.stdout
    if args.json:
        json.dump(result, out_fh, indent=2)
        print(file=out_fh)
    else:
        write_markdown(result, out_fh)
    if out_fh is not sys.stdout:
        out_fh.close()
        print(f"\nReport: {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
