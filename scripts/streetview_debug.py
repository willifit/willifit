#!/usr/bin/env python3
"""
Street View debug helper — fetch the exact 4 images our pipeline would send
to Claude for a given garage, save them to disk, and emit a Google Maps
Street View URL so you can compare.

Usage:
  export GOOGLE_MAPS_API_KEY=...
  python3 scripts/streetview_debug.py reno-nv rno-national-bowling-stadium

This does NOT call Claude.  It only fetches Street View Static images, which
are free under Google's $200/mo credit (first ~28k/month).

Output:
  scripts/debug-images/<slug>__<garage_id>__<N>_bearingDEG_pitchDEG.jpg
  scripts/debug-images/<slug>__<garage_id>__info.txt   (URLs to share back)
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import streetview_verify as sv  # noqa

REPO = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "debug-images"


def debug_garage(slug: str, gid: str):
    city = json.loads((REPO / "data" / "cities" / f"{slug}.json").read_text())
    g = next((x for x in city.get("garages", []) if x.get("id") == gid), None)
    if not g:
        print(f"garage {gid!r} not found in {slug}")
        return
    print(f"Target: {g['name']}  ({g['lat']},{g['lng']})")

    pano = sv.find_best_pano(g["lat"], g["lng"])
    if not pano.get("ok"):
        print(f"No pano: {pano.get('reason')}")
        return
    print(f"Pano:   {pano['pano_id']}")
    print(f"Camera: {pano['pano_lat']:.6f},{pano['pano_lng']:.6f}")
    print(f"Dist:   {pano['distance_m']:.1f}m from target")
    print(f"Bearing to target: {pano['bearing_to_target']:.0f}°")
    print(f"Probe:  {pano['probe']} (tier {pano['tier']})")

    b = pano["bearing_to_target"]
    shots = [
        ("cone-left",  b - sv.HEADING_CONE_DEG, sv.PITCH_LEVEL),
        ("cone-center",b,                       sv.PITCH_LEVEL),
        ("cone-right", b + sv.HEADING_CONE_DEG, sv.PITCH_LEVEL),
        ("high-tilt",  b,                       sv.PITCH_HIGH),
    ]

    OUT.mkdir(exist_ok=True)
    for label, h, p in shots:
        img = sv.fetch_streetview_image_by_pano(pano["pano_id"], h, pitch=p)
        fn = OUT / f"{slug}__{gid}__{label}_{int(h%360)}deg_pitch{int(p)}.jpg"
        if img:
            fn.write_bytes(img)
            print(f"  saved {fn.name} ({len(img)//1024}KB)")
        else:
            print(f"  FAILED {label}")

    # Clickable Google Maps URL of our pipeline's chosen pano + bearing.
    # User can click this to compare against what THEY saw.
    our_url = (
        f"https://www.google.com/maps/@{pano['pano_lat']},{pano['pano_lng']},"
        f"3a,75y,{b:.0f}h,90t/data=!3m6!1e1!3m4!1s{pano['pano_id']}!2e0!7i16384!8i8192"
    )

    info = OUT / f"{slug}__{gid}__info.txt"
    info.write_text(
        f"Garage: {g['name']}  ({g['lat']},{g['lng']})\n"
        f"Pano:   {pano['pano_id']}\n"
        f"Camera: {pano['pano_lat']},{pano['pano_lng']}\n"
        f"Dist:   {pano['distance_m']:.1f}m\n"
        f"Bearing: {pano['bearing_to_target']:.0f}°\n"
        f"\n"
        f"What our pipeline sees:\n{our_url}\n"
        f"\n"
        f"Where YOU saw the sign — please paste the URL from the address bar\n"
        f"after positioning Street View on the clearance sign, here:\n"
        f"  <PASTE URL HERE>\n"
    )
    print(f"\nOur pipeline's vantage: {our_url}")
    print(f"Info + URL written to: {info}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: streetview_debug.py <slug> <garage_id>", file=sys.stderr)
        sys.exit(2)
    if not sv.GOOGLE_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY not set", file=sys.stderr)
        sys.exit(2)
    debug_garage(sys.argv[1], sys.argv[2])
