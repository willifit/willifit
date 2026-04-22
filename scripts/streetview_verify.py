#!/usr/bin/env python3
"""
Willifit — Street View clearance-sign verifier (v2: targeted-bearing + nearby-pano search).

For every parking garage in our DB that has NO posted clearance (height_in is
null), this script:
  1. Finds the best Google-captured Street View pano near the garage
     (primary lat/lng first, then ±10m offsets N/S/E/W as fallback).
  2. Computes the bearing from the pano to the garage and shoots a tight
     3-image cone (bearing ±30°) plus one up-tilted frame to catch signs
     mounted above the entrance.
  3. Sends all images + a structured prompt to Claude Vision.
  4. Parses Claude's JSON reply.
  5. If confidence is high enough, writes the new height_in / height_label /
     verified_on / source fields back to the city JSON.

Improvements over v1 (the original N/E/S/W approach):
  - source=outdoor filter skips indoor + non-Google panos automatically.
  - Targeted bearing: the sign is in ONE direction from the pano, not all
    four — so we stop sending Claude 75% useless imagery.
  - Narrower FOV (60° vs 90°) makes distant signs sharper / more readable.
  - Nearby-pano fallback rescues garages whose recorded lat/lng falls on a
    pano-less spot (roof, interior, awning, etc.) but where the street out
    front has coverage.
  - Skips garages where the closest usable pano is >30m from the target —
    beyond that distance even a clearance sign is too small to read.

Why 4 images in a single call?
  One API call is cheaper and lets Claude reason across views (the entrance
  sign is usually visible from at most one or two angles).  ~1¢ per garage
  with Haiku, plus Street View Static is free for the first ~28k images/month
  on Google's $200/mo free credit.

Costs (rough, as of 2026):
  Haiku vision     ~$0.003 / image → ~$0.012 per 4-image call
  Street View      $7 / 1000 images (first ~28k/mo free)
  Full 1,100-unverified pass: ~$13 in Claude fees, $0 in Google fees

Requires:
  pip install anthropic
  export ANTHROPIC_API_KEY=sk-ant-...
  export GOOGLE_MAPS_API_KEY=AIza...         (Street View Static)

Usage:
  python3 streetview_verify.py --slug las-vegas-nv --limit 5 --dry-run
  python3 streetview_verify.py --slug las-vegas-nv             # commit changes
  python3 streetview_verify.py --all --limit 100               # first 100 across all cities
  python3 streetview_verify.py --all                           # full pass
  python3 streetview_verify.py --slug las-vegas-nv --overwrite # re-verify every garage

Safety notes:
  * We only overwrite height_in when Claude's confidence is "high" by default.
    Use --confidence medium to be more permissive.
  * Any write records source="AI-verified (Street View + Claude Vision)" so
    the provenance is visible in the app.  The user-facing detail panel shows
    the source string.
  * verified_on is set to today's ISO date so the "data may be stale" banner
    in the app will track freshness.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional
from urllib import request, parse, error

# Auto-load .env — see scripts/_env.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"
LOG_PATH = REPO_ROOT / "data" / "streetview_verify.log"

GOOGLE_KEY = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_STREETVIEW_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

DEFAULT_MODEL = "claude-haiku-4-5"   # cheap + fast; upgrade to sonnet for tougher signs
IMG_SIZE = "640x640"                 # max size on free Static tier
FOV = 60                             # tighter than v1's 90° — sharper distant-sign reads
PITCH_LEVEL = 10                     # slight upward tilt to catch signage above entrance
PITCH_HIGH = 25                      # steeper shot for signs mounted high on the awning
HEADING_CONE_DEG = 30                # three shots at bearing-30, bearing, bearing+30
MAX_PANO_DISTANCE_M = 30.0           # beyond this, signs are unreadable
OFFSET_SEARCH_M = 10.0               # Tier 2: nearby-offset probe radius

SV_STATIC = "https://maps.googleapis.com/maps/api/streetview"
SV_META   = "https://maps.googleapis.com/maps/api/streetview/metadata"

# Confidence ordering for threshold comparisons
CONF_RANK = {"low": 0, "medium": 1, "high": 2}

PROMPT_TEMPLATE = """You are analyzing {n} Google Street View images of the SAME parking-garage or
parking-structure entrance, taken from {n} different angles at the same lat/lng.

Your job: find any posted vehicle-height CLEARANCE sign across these images
and report the reading.

Things that COUNT as a clearance sign:
  * A sign showing a height in feet-inches ("11'6\"", "13 FT 6 IN", "CLEARANCE 11' 6\"")
  * A ceiling-mounted clearance bar with a height number
  * A yellow-diamond or orange LOW CLEARANCE warning with a height
  * A posted "MAX HEIGHT" or "VEHICLE HEIGHT LIMIT" marker

Things that do NOT count (ignore them):
  * Speed-limit signs
  * Street-name signs
  * Parking-rate signs / hours signs
  * Billboards, storefront signs, ads
  * Weight limits ("GVW 30,000 LBS")
  * Signs for other (non-parking) businesses

Rules:
  * If you see a sign in MULTIPLE images, report the clearest reading and
    use the others to corroborate.
  * Convert the reading to inches (e.g. 11'6" = 138, 13'0" = 156).
  * If the sign is there but reading is ambiguous/blurry, use height_in=null
    and confidence=low with a note explaining.
  * If no clearance sign is visible in any image, found_sign=false.
  * Do NOT guess based on the structure type.  If no sign is posted in the
    images, say so.

Return ONLY a single JSON object (no markdown fences, no prose before/after)
matching this schema:

{{
  "found_sign": boolean,
  "height_in": integer | null,
  "confidence": "low" | "medium" | "high",
  "raw_text": string,
  "notes": string
}}
"""


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = 30) -> tuple[int, bytes, dict]:
    req = request.Request(url, headers={"User-Agent": "willifit-sv-verify/0.1"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})


# ---------------------------------------------------------------------------
# Geometry helpers (no API calls — pure math)
# ---------------------------------------------------------------------------

def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters."""
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def bearing_deg(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> float:
    """Forward bearing (compass degrees 0-360) from point 1 to point 2."""
    p1, p2 = math.radians(from_lat), math.radians(to_lat)
    dl = math.radians(to_lng - from_lng)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


# ---------------------------------------------------------------------------
# Pano discovery
# ---------------------------------------------------------------------------

def _streetview_meta(lat: float, lng: float, **extra) -> dict:
    """Raw metadata call. Caller interprets status + copyright."""
    q = parse.urlencode({"location": f"{lat},{lng}", "key": GOOGLE_KEY, **extra})
    status, body, _ = _http_get(f"{SV_META}?{q}", timeout=10)
    if status != 200:
        return {"status": "http-error"}
    try:
        return json.loads(body)
    except Exception:
        return {"status": "bad-json"}


def find_best_pano(lat: float, lng: float) -> dict:
    """Locate the best Google-captured outdoor pano near (lat, lng).

    Strategy:
      1. Query metadata at the primary point with source=outdoor (filters out
         indoor + user-contributed panos in one shot).
      2. If that fails or returns a pano farther than MAX_PANO_DISTANCE_M,
         probe 4 offset points (±OFFSET_SEARCH_M N/S/E/W) and pick the
         Google-official pano closest to the target.

    Returns:
      {ok: True, pano_id, pano_lat, pano_lng, distance_m, bearing_to_target,
       copyright, tier}
      or {ok: False, reason: str}
    """
    deg_per_m_lat = 1.0 / 111320.0
    deg_per_m_lng = 1.0 / (111320.0 * max(0.01, math.cos(math.radians(lat))))
    d = OFFSET_SEARCH_M

    # Primary first, then offsets. We check primary separately so we can tag
    # the result with tier=1 (primary) vs tier=2 (fallback offset).
    probes = [
        ("primary", lat, lng),
        ("offset-N", lat + d * deg_per_m_lat, lng),
        ("offset-S", lat - d * deg_per_m_lat, lng),
        ("offset-E", lat, lng + d * deg_per_m_lng),
        ("offset-W", lat, lng - d * deg_per_m_lng),
    ]

    best = None  # (distance_m, info_dict)
    seen_panos = set()
    for label, plat, plng in probes:
        m = _streetview_meta(plat, plng, source="outdoor")
        if m.get("status") != "OK":
            continue
        pano_id = m.get("pano_id") or ""
        if not pano_id or pano_id in seen_panos:
            continue
        seen_panos.add(pano_id)
        cr = (m.get("copyright") or "").lower()
        if "google" not in cr:
            continue  # skip user panos (indoor uploads etc.)
        ploc = m.get("location") or {}
        plat2, plng2 = ploc.get("lat"), ploc.get("lng")
        if plat2 is None:
            continue
        dist = haversine_m(plat2, plng2, lat, lng)
        if dist > MAX_PANO_DISTANCE_M:
            continue
        info = {
            "pano_id": pano_id,
            "pano_lat": plat2,
            "pano_lng": plng2,
            "distance_m": dist,
            "bearing_to_target": bearing_deg(plat2, plng2, lat, lng),
            "copyright": m.get("copyright") or "",
            "tier": 1 if label == "primary" else 2,
            "probe": label,
        }
        if best is None or dist < best[0]:
            best = (dist, info)

    if best is None:
        return {"ok": False, "reason": "no-pano-within-range"}
    return {"ok": True, **best[1]}


def fetch_streetview_image_by_pano(
    pano_id: str, heading: float, pitch: float = PITCH_LEVEL, fov: int = FOV
) -> Optional[bytes]:
    """Pull a Street View Static image for a specific pano_id at a given heading.
    Using pano_id (rather than lat/lng) guarantees we shoot the same pano the
    metadata call resolved — otherwise Google occasionally returns a different
    nearby pano for the image endpoint."""
    q = parse.urlencode({
        "size": IMG_SIZE,
        "pano": pano_id,
        "heading": round(heading % 360, 2),
        "pitch": pitch,
        "fov": fov,
        "key": GOOGLE_KEY,
        "return_error_code": "true",
    })
    status, body, _ = _http_get(f"{SV_STATIC}?{q}", timeout=20)
    if status == 200 and body.startswith(b"\xff\xd8"):
        return body
    return None


# ---------------------------------------------------------------------------
# Claude Vision call
# ---------------------------------------------------------------------------

def _anthropic_client():
    """Import anthropic lazily so `--help` works without the SDK installed."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("ERROR: anthropic SDK not installed.  Run:  pip install anthropic",
              file=sys.stderr)
        sys.exit(2)
    import anthropic
    if not ANTHROPIC_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(2)
    return anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def verify_with_claude(images: list[bytes], model: str) -> Optional[dict]:
    """Send images to Claude, return parsed JSON reply or None on parse failure."""
    client = _anthropic_client()

    content: list = []
    for img in images:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(img).decode("ascii"),
            },
        })
    content.append({"type": "text", "text": PROMPT_TEMPLATE.format(n=len(images))})

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as e:
        print(f"    Claude API error: {e}", file=sys.stderr)
        return None

    # Claude returns a list of content blocks; grab the text from the first
    text = ""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text = block.text
            break

    # Strip any accidental markdown fences
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: find first {...} block
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        print(f"    Claude replied non-JSON: {text[:200]!r}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Core per-garage flow
# ---------------------------------------------------------------------------

def inches_to_label(inches: Optional[int]) -> Optional[str]:
    if inches is None:
        return None
    ft = inches // 12
    rem = inches % 12
    return f"{ft}'{rem}\""


def verify_garage(g: dict, model: str) -> Optional[dict]:
    """Returns a dict of fields to merge into the garage record, or None on fail.
    Caller decides whether to write based on confidence threshold."""
    lat, lng = g.get("lat"), g.get("lng")
    if lat is None or lng is None:
        return None

    # 1. Locate the best Google-outdoor pano near the garage. Free metadata
    #    calls only — no image bytes yet, no Claude call yet.
    pano = find_best_pano(lat, lng)
    if not pano.get("ok"):
        return {"sv_status": pano.get("reason", "no-pano-within-range")}

    # 2. Compute a tight heading cone aimed AT the garage from the pano
    #    location, plus one up-tilted shot for high-mounted entrance signs.
    b = pano["bearing_to_target"]
    shots = [
        (b - HEADING_CONE_DEG, PITCH_LEVEL),   # left of bearing
        (b,                    PITCH_LEVEL),   # straight at entrance
        (b + HEADING_CONE_DEG, PITCH_LEVEL),   # right of bearing
        (b,                    PITCH_HIGH),    # same heading, higher pitch for awning-mounted signs
    ]

    images = []
    for h, p in shots:
        img = fetch_streetview_image_by_pano(pano["pano_id"], h, pitch=p)
        if img:
            images.append(img)

    if len(images) < 2:
        return {"sv_status": "too-few-images", "images_fetched": len(images)}

    result = verify_with_claude(images, model)
    if not result:
        return {"sv_status": "claude-parse-fail"}

    result["sv_status"] = "ok"
    result["images_used"] = len(images)
    result["pano_id"] = pano["pano_id"]
    result["pano_distance_m"] = round(pano["distance_m"], 1)
    result["pano_tier"] = pano["tier"]        # 1=primary, 2=offset-search fallback
    result["pano_probe"] = pano["probe"]      # primary / offset-N / offset-S / offset-E / offset-W
    return result


def _log(line: str):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Structure-type pre-filter (free — no API calls)
# ---------------------------------------------------------------------------

# Words that, when present in name OR notes, confirm the entry is a real
# parking STRUCTURE (multi-level, covered).  These override surface-lot
# signals below — e.g. "National Bowling Stadium Garage" contains both
# "Stadium" (surface signal) and "Garage" (structural signal) — the
# structural signal wins because you can't have "garage" as a lot.
STRUCTURAL_MARKERS = (
    "garage", "parking structure", "parking deck", "multi-level",
    "multi-story", "multistory", "parking ramp",
)

# Words that strongly indicate an open-air / surface lot (no overhead
# clearance to measure).  Checked in name + notes.
SURFACE_PHRASE_MARKERS = (
    "surface lot", "surface lots",
    "open-air", "open air",
    "uncovered",
    "rv-friendly lot", "rv-friendly surface", "rv friendly lot",
)

# Name-only patterns — whole-word match in the name field.  "Park" alone
# is excluded (too ambiguous — "Park Place Garage" is a real structure).
# We only match when these appear as standalone tokens in the name.
SURFACE_NAME_WORDS = re.compile(
    r"\b(lot|lots|stadium|ballpark|field|economy)\b",
    re.IGNORECASE,
)


def classify_structure(g: dict) -> Optional[str]:
    """Free heuristic: classify a parking entry as a structure, a surface lot,
    or unknown based ONLY on existing name/notes/oversized fields.

    Returns:
        "surface_lot"  — confident it's a flat / open-air lot, skip verification
        "structure"    — confident it's a multi-level garage, attempt verification
        None           — ambiguous, attempt verification anyway

    Ordering matters: structural markers trump everything else (explicit is
    best), so "National Bowling Stadium Garage" is a structure even though
    the name contains "Stadium".
    """
    name = (g.get("name") or "").lower()
    notes = (g.get("notes") or "").lower()
    blob = f"{name} {notes}"

    # 1. Explicit structural keyword anywhere -> structure
    if any(m in blob for m in STRUCTURAL_MARKERS):
        return "structure"

    # 2. Explicit surface-lot phrase -> surface lot
    if any(m in blob for m in SURFACE_PHRASE_MARKERS):
        return "surface_lot"

    # 3. oversized=True + height_in=None is a strong surface-lot signal.
    #    Real height-limited garages get oversized=True only when they have
    #    a tall (≥8') clearance, which means height_in should be populated.
    #    Claiming "oversized-friendly" without a height on file almost
    #    always means it's a flat lot.
    if g.get("oversized") is True and g.get("height_in") is None:
        return "surface_lot"

    # 4. Name alone strongly suggests outdoor venue
    if SURFACE_NAME_WORDS.search(name):
        return "surface_lot"

    return None


def process_city(
    slug: str,
    limit: Optional[int],
    min_conf: str,
    model: str,
    dry_run: bool,
    overwrite: bool,
    force_lots: bool = False,
) -> dict:
    city_path = CITIES_DIR / f"{slug}.json"
    if not city_path.exists():
        return {"slug": slug, "error": "no city file"}

    data = json.loads(city_path.read_text())
    garages = data.get("garages", [])

    if overwrite:
        candidates = list(garages)
    else:
        candidates = [g for g in garages if g.get("height_in") is None]

    if not candidates:
        print(f"[{slug}] no unverified garages")
        return {"slug": slug, "checked": 0, "updated": 0}

    # Free pre-filter: classify each candidate as structure / surface_lot /
    # unknown based on existing name/notes/oversized fields.  Surface lots
    # get stamped as such and skipped — no Claude call, no Street View
    # fetch.  --force-lots bypasses this (useful when you suspect the
    # heuristic is misclassifying a real garage).
    skipped_lots = 0
    filtered_candidates = []
    lot_writes_pending = False
    for g in candidates:
        kind = classify_structure(g)
        if kind == "surface_lot" and not force_lots:
            # Stamp it in the JSON so next run doesn't reconsider it, and so
            # the app can render "open-air parking" instead of "unverified".
            if g.get("structure_type") != "surface_lot":
                if not dry_run:
                    g["structure_type"] = "surface_lot"
                    lot_writes_pending = True
            skipped_lots += 1
            print(f"  {g.get('id','?'):<28} {g.get('name','?')[:40]:<40} SKIP surface_lot (heuristic)")
            _log(f"{date.today()} {slug} {g['id']} SKIP-SURFACE-LOT name={g.get('name','')!r} notes={(g.get('notes') or '')[:80]!r}")
            continue
        filtered_candidates.append(g)

    # Persist surface-lot stamps immediately, even if the pipeline is interrupted later.
    if lot_writes_pending and not dry_run:
        city_path.write_text(json.dumps(data, indent=2) + "\n")

    print(f"[{slug}] {len(filtered_candidates)} candidates after lot filter "
          f"(skipped {skipped_lots} surface lots), limit={limit}")

    candidates = filtered_candidates

    checked = 0
    updated = 0
    no_imagery = 0
    no_sign = 0
    low_conf = 0

    for g in candidates:
        if limit is not None and checked >= limit:
            break
        checked += 1

        tag = f"  {g.get('id','?'):<28} {g.get('name','?')[:40]:<40}"
        print(tag, end=" ", flush=True)

        res = verify_garage(g, model)
        if res is None:
            print("SKIP (bad coords)")
            continue

        if res.get("sv_status") == "no-pano-within-range":
            no_imagery += 1
            print("no usable SV pano (none within 30m of target)")
            _log(f"{date.today()} {slug} {g['id']} NO-PANO-IN-RANGE")
            continue

        if res.get("sv_status") != "ok":
            print(f"error: {res.get('sv_status')}")
            _log(f"{date.today()} {slug} {g['id']} ERR {res.get('sv_status')}")
            continue

        found = bool(res.get("found_sign"))
        height_in = res.get("height_in")
        conf = (res.get("confidence") or "low").lower()
        raw = (res.get("raw_text") or "")[:80]
        notes = (res.get("notes") or "")[:200]

        pano_info = (
            f"tier={res.get('pano_tier','?')} "
            f"probe={res.get('pano_probe','?')} "
            f"d={res.get('pano_distance_m','?')}m"
        )

        if not found or height_in is None:
            no_sign += 1
            print(f"no sign ({conf}) [{pano_info}]")
            _log(f"{date.today()} {slug} {g['id']} NO-SIGN conf={conf} {pano_info} notes={notes!r}")
            continue

        if CONF_RANK.get(conf, -1) < CONF_RANK[min_conf]:
            low_conf += 1
            print(f"low confidence: {conf} — {height_in}in {raw!r} [{pano_info}]")
            _log(f"{date.today()} {slug} {g['id']} LOW-CONF conf={conf} h={height_in} {pano_info} raw={raw!r}")
            continue

        # Sanity bound
        if not (48 <= int(height_in) <= 240):
            print(f"implausible height {height_in}in — discarding")
            _log(f"{date.today()} {slug} {g['id']} IMPLAUSIBLE h={height_in}")
            continue

        # Record the verified reading.  The "AI-verified (Street View + Claude
        # Vision)" source string is what the frontend pattern-matches on to
        # render the green "AI-verified" badge in both the sidebar list and
        # the detail panel (see willifit.html: `source.includes('AI-verified')`).
        if not dry_run:
            g["height_in"] = int(height_in)
            g["height_label"] = inches_to_label(int(height_in))
            g["verified_on"] = date.today().isoformat()
            prev_src = g.get("source", "")
            if "AI-verified" not in prev_src:
                g["source"] = f"AI-verified (Street View + Claude Vision) — was: {prev_src}".strip(" -")
            # Append to notes (keep existing) — include pano diagnostics so a
            # human reviewer can reproduce the shot that produced this reading.
            prior_notes = g.get("notes", "")
            stamp = (
                f'Verified {date.today().isoformat()} from sign reading: "{raw}" '
                f'(pano {res.get("pano_id","?")}, {pano_info})'
            )
            g["notes"] = (prior_notes + "\n" + stamp).strip() if prior_notes else stamp

        updated += 1
        action = "[DRY] would set" if dry_run else "UPDATED"
        print(f"{action}: {height_in}in ({inches_to_label(int(height_in))}) conf={conf} [{pano_info}]")
        _log(f"{date.today()} {slug} {g['id']} VERIFIED h={height_in} conf={conf} {pano_info} raw={raw!r}")

    if updated and not dry_run:
        city_path.write_text(json.dumps(data, indent=2) + "\n")

    print(f"  {slug}: checked={checked} updated={updated} "
          f"no-imagery={no_imagery} no-sign={no_sign} low-conf={low_conf} "
          f"lots-skipped={skipped_lots}")
    return {
        "slug": slug, "checked": checked, "updated": updated,
        "no_imagery": no_imagery, "no_sign": no_sign, "low_conf": low_conf,
        "skipped_lots": skipped_lots,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Verify parking-garage clearance heights via Google Street View + Claude Vision.")
    ap.add_argument("--slug", help="Single city slug")
    ap.add_argument("--slugs", help="Comma-separated list of slugs")
    ap.add_argument("--all", action="store_true", help="All live cities")
    ap.add_argument("--limit", type=int, default=None,
                    help="Stop after N garages per city (useful for budget control)")
    ap.add_argument("--confidence", choices=["low", "medium", "high"], default="high",
                    help="Minimum Claude confidence to accept a reading (default: high)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model (default: {DEFAULT_MODEL})")
    ap.add_argument("--overwrite", action="store_true",
                    help="Re-verify ALL garages, including already-verified ones")
    ap.add_argument("--dry-run", action="store_true", help="Don't write to city files")
    ap.add_argument("--sleep", type=float, default=0.5,
                    help="Seconds between garages (rate-limit pad)")
    ap.add_argument("--force-lots", action="store_true",
                    help="Skip the free surface-lot pre-filter and attempt verification "
                         "on every candidate, including ones our heuristic flags as flat lots. "
                         "Use if you suspect the heuristic is wrong about a specific city.")
    args = ap.parse_args()

    if not (args.slug or args.slugs or args.all):
        ap.error("pass --slug, --slugs, or --all")

    if not GOOGLE_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY not set in env", file=sys.stderr)
        sys.exit(2)

    idx = json.loads(INDEX_PATH.read_text())
    if args.slug:
        targets = [args.slug]
    elif args.slugs:
        targets = [s.strip() for s in args.slugs.split(",") if s.strip()]
    else:
        targets = [c["slug"] for c in idx if c.get("status") == "live"]

    summary = []
    for slug in targets:
        try:
            r = process_city(slug, args.limit, args.confidence, args.model,
                             args.dry_run, args.overwrite,
                             force_lots=args.force_lots)
            summary.append(r)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            break
        time.sleep(args.sleep)

    # Tally
    total_checked = sum(s.get("checked", 0) for s in summary)
    total_updated = sum(s.get("updated", 0) for s in summary)
    total_no_img  = sum(s.get("no_imagery", 0) for s in summary)
    total_no_sign = sum(s.get("no_sign", 0) for s in summary)
    total_low     = sum(s.get("low_conf", 0) for s in summary)
    total_lots    = sum(s.get("skipped_lots", 0) for s in summary)

    print("\n=== SUMMARY ===")
    print(f"Cities processed:    {len(summary)}")
    print(f"Garages checked:     {total_checked}")
    print(f"Updated:             {total_updated}")
    print(f"No Street View:      {total_no_img}")
    print(f"No clearance sign:   {total_no_sign}")
    print(f"Low confidence:      {total_low}")
    print(f"Surface-lots skipped:{total_lots}  (saved ~${total_lots * 0.012:.2f} in Claude fees)")
    if args.dry_run:
        print("(DRY-RUN — no files written.)")


if __name__ == "__main__":
    main()
