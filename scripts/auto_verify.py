#!/usr/bin/env python3
"""
auto_verify.py — autonomous clearance-sign finder.

For each unverified garage, finds the best Google Street View pano that
shows the posted clearance sign, reads the number, and writes the result
back to the city JSON.  No human URL-pasting required.

Pipeline per garage:
  1. Free pre-filter: classify_structure() rejects entries with clear
     surface-lot signals (name/notes/oversized fields).  No API calls.
  2. Discover up to MAX_PANOS Google-outdoor panos near the garage
     (primary lat/lng + 4 offset probes at ±OFFSET_M N/S/E/W).
  3. For each candidate pano, shoot 8 images at 45° heading intervals
     (full 360° coverage — signs can face any direction).
  4. Send each pano's image set to Claude Vision in a single call.
     Prompt asks "is there a clearance sign?  which heading?  what
     number?  confidence?"
  5. Rank candidate panos by Claude's confidence.  Winner's pano_id,
     best heading, and height reading get written to the garage record.
  6. If no pano finds a sign, the garage keeps height_in=null and
     structure_type is left unchanged.  Flagged for manual review in
     the summary.

Output:
  Updates data/cities/<slug>.json in place with:
    height_in, height_label, verified_on
    source = "AI-verified (Street View + Claude Vision — auto-pano)"
    pano_id + pano_heading (frontend uses these to pin the hero view
                            to the exact sign-visible vantage)
    notes (appended) with the AI read + pano id for audit trail

Usage:
  export GOOGLE_MAPS_API_KEY=...
  export ANTHROPIC_API_KEY=...
  python3 scripts/auto_verify.py --slug reno-nv --max-cost 10 --dry-run
  python3 scripts/auto_verify.py --slug reno-nv --max-cost 200

Cost model:
  Google Street View Static:  free (under $200/mo free credit for first
                                    ~28k images/month)
  Google Street View Metadata: free
  Claude Vision (Haiku):       ~$0.003/image × ~8 images/call = ~$0.024/call
                               up to MAX_PANOS_PER_GARAGE calls per garage
                               so ~$0.12 per garage worst case
  Estimated for ~2000 garage DB: ~$150-240 one-time

The --max-cost flag caps cumulative Claude spend.  Script aborts the
current city mid-run if the next garage would push over the cap.
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

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"
LOG_PATH = REPO_ROOT / "data" / "auto_verify.log"

GOOGLE_KEY = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_STREETVIEW_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

DEFAULT_MODEL = "claude-haiku-4-5"
IMG_SIZE = "640x640"
FOV = 75                     # wide enough to catch signs in peripheral
PITCH = 10                   # slight upward tilt
HEADINGS_8 = [0, 45, 90, 135, 180, 225, 270, 315]  # 45° intervals

MAX_PANOS_PER_GARAGE = 4     # budget: up to 4 Claude calls per garage
MAX_PANO_DISTANCE_M = 50.0   # widened from 30 — willing to scan further
OFFSET_PROBE_M = 15.0

# Rough costs in USD (Haiku vision pricing; update if model changes)
COST_PER_CLAUDE_CALL = 0.024

SV_STATIC = "https://maps.googleapis.com/maps/api/streetview"
SV_META   = "https://maps.googleapis.com/maps/api/streetview/metadata"

CONF_RANK = {"low": 0, "medium": 1, "high": 2}

# Reuse the surface-lot heuristic from streetview_verify.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from streetview_verify import classify_structure as _classify_structure
except Exception:
    _classify_structure = None


# ---------------------------------------------------------------------------
# Geometry + HTTP (same as streetview_verify.py, copied for self-containment)
# ---------------------------------------------------------------------------

def haversine_m(lat1, lng1, lat2, lng2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def bearing_deg(from_lat, from_lng, to_lat, to_lng):
    p1, p2 = math.radians(from_lat), math.radians(to_lat)
    dl = math.radians(to_lng - from_lng)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _http_get(url, timeout=30):
    req = request.Request(url, headers={"User-Agent": "willifit-auto-verify/0.1"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except error.HTTPError as e:
        return e.code, e.read()


# ---------------------------------------------------------------------------
# Pano discovery — returns LIST of candidates, not just closest
# ---------------------------------------------------------------------------

def _streetview_meta(lat, lng, **extra):
    q = parse.urlencode({"location": f"{lat},{lng}", "key": GOOGLE_KEY, **extra})
    status, body = _http_get(f"{SV_META}?{q}", timeout=10)
    if status != 200:
        return {"status": "http-error"}
    try:
        return json.loads(body)
    except Exception:
        return {"status": "bad-json"}


def find_candidate_panos(lat, lng, max_panos=MAX_PANOS_PER_GARAGE):
    """Return up to max_panos Google-outdoor panos within MAX_PANO_DISTANCE_M
    of the target, sorted by distance."""
    deg_per_m_lat = 1.0 / 111320.0
    deg_per_m_lng = 1.0 / (111320.0 * max(0.01, math.cos(math.radians(lat))))
    d = OFFSET_PROBE_M
    probes = [
        ("primary", lat, lng),
        ("N", lat + d * deg_per_m_lat, lng),
        ("S", lat - d * deg_per_m_lat, lng),
        ("E", lat, lng + d * deg_per_m_lng),
        ("W", lat, lng - d * deg_per_m_lng),
    ]

    found = {}  # pano_id -> dict (dedupe across probes)
    for label, plat, plng in probes:
        m = _streetview_meta(plat, plng, source="outdoor")
        if m.get("status") != "OK":
            continue
        pano_id = m.get("pano_id", "")
        if not pano_id or pano_id in found:
            continue
        cr = (m.get("copyright") or "").lower()
        if "google" not in cr:
            continue
        ploc = m.get("location") or {}
        plat2, plng2 = ploc.get("lat"), ploc.get("lng")
        if plat2 is None:
            continue
        dist = haversine_m(plat2, plng2, lat, lng)
        if dist > MAX_PANO_DISTANCE_M:
            continue
        found[pano_id] = {
            "pano_id": pano_id,
            "pano_lat": plat2,
            "pano_lng": plng2,
            "distance_m": dist,
            "bearing_to_target": bearing_deg(plat2, plng2, lat, lng),
            "copyright": m.get("copyright") or "",
            "via_probe": label,
        }

    panos = sorted(found.values(), key=lambda x: x["distance_m"])
    return panos[:max_panos]


def fetch_sv_image(pano_id, heading, pitch=PITCH, fov=FOV):
    q = parse.urlencode({
        "size": IMG_SIZE,
        "pano": pano_id,
        "heading": round(heading % 360, 2),
        "pitch": pitch,
        "fov": fov,
        "key": GOOGLE_KEY,
        "return_error_code": "true",
    })
    status, body = _http_get(f"{SV_STATIC}?{q}", timeout=20)
    if status == 200 and body.startswith(b"\xff\xd8"):
        return body
    return None


# ---------------------------------------------------------------------------
# Claude Vision — one call scores all 8 headings from one pano
# ---------------------------------------------------------------------------

PROMPT_SCAN_PANO = """You are looking at 8 Google Street View images captured from a SINGLE fixed
camera location (pano_id: {pano_id}), rotating through 8 compass headings
spaced 45° apart.  Each image is labeled with its heading in degrees (0°=N,
90°=E, 180°=S, 270°=W).

The camera is near a parking garage / structure / tunnel / bridge entrance.
Your job is to find a posted VEHICLE-HEIGHT CLEARANCE sign in any of the 8
images and report:

  * which heading has the clearest view of the sign
  * what the posted clearance number is (converted to inches)
  * how confident you are

What counts as a clearance sign:
  * Height in feet-inches ("11'6\"", "13 FT 6 IN", "CLEARANCE 11' 6\"")
  * Ceiling-mounted clearance bar with a height number
  * Yellow-diamond or orange LOW CLEARANCE warning with a height
  * "MAX HEIGHT" / "VEHICLE HEIGHT LIMIT" markers

What does NOT count:
  * Speed limit, street name, parking rates, business signs, weight limits
  * Structural elements without a posted number

Rules:
  * If the sign appears in multiple images, report the heading with the
    clearest/most readable view.
  * Convert the reading to inches (11'6" = 138, 13'0" = 156, 7'0" = 84).
  * If the sign is present but the number is ambiguous/blurry, use
    height_in=null and confidence=low.
  * If no clearance sign is visible in any of the 8 images, found_sign=false.
  * Do NOT guess based on structure type.  You must SEE a sign to report
    a number.

Return ONLY a JSON object (no prose, no markdown fences):

{{
  "found_sign": boolean,
  "best_heading": number | null,   // which heading showed the sign, or null
  "height_in": integer | null,
  "confidence": "low" | "medium" | "high",
  "raw_text": string,              // exactly what text appears on the sign
  "notes": string
}}
"""


def _anthropic_client():
    try:
        import anthropic  # noqa
    except ImportError:
        print("ERROR: anthropic SDK not installed. pip install anthropic", file=sys.stderr)
        sys.exit(2)
    import anthropic
    if not ANTHROPIC_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(2)
    return anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def scan_pano_with_claude(pano_id, images_by_heading, model):
    """Send 8 labeled images from one pano to Claude, return parsed response."""
    client = _anthropic_client()

    content = []
    for heading, img in images_by_heading:
        content.append({"type": "text", "text": f"Heading {heading}°:"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(img).decode("ascii"),
            },
        })
    content.append({"type": "text", "text": PROMPT_SCAN_PANO.format(pano_id=pano_id)})

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=500,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as e:
        print(f"    Claude API error: {e}", file=sys.stderr)
        return None

    text = ""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text = block.text
            break

    # Strip markdown fences if present
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        print(f"    Claude replied non-JSON: {text[:200]!r}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Per-garage orchestration
# ---------------------------------------------------------------------------

def inches_to_label(inches):
    if inches is None:
        return None
    ft = inches // 12
    rem = inches % 12
    return f"{ft}'{rem}\""


def _log(line):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def verify_garage(g, model):
    """Multi-pano scan.  Returns dict with keys:
        status: "verified" | "no-sign" | "no-pano" | "error"
        If verified: pano_id, pano_heading, height_in, confidence, raw_text, notes
        claude_calls: int  (for cost accounting)
    """
    lat, lng = g.get("lat"), g.get("lng")
    if lat is None:
        return {"status": "error", "reason": "no-coords", "claude_calls": 0}

    panos = find_candidate_panos(lat, lng, max_panos=MAX_PANOS_PER_GARAGE)
    if not panos:
        return {"status": "no-pano", "claude_calls": 0}

    # For each pano, shoot 8 images and score with Claude
    candidates = []
    claude_calls = 0
    for p in panos:
        images_by_heading = []
        for h in HEADINGS_8:
            img = fetch_sv_image(p["pano_id"], h)
            if img:
                images_by_heading.append((h, img))
        if len(images_by_heading) < 4:
            continue

        print(f"    pano {p['pano_id'][:12]}… (d={p['distance_m']:.1f}m, via={p['via_probe']}) → "
              f"{len(images_by_heading)} shots, asking Claude…", flush=True)
        result = scan_pano_with_claude(p["pano_id"], images_by_heading, model)
        claude_calls += 1
        if not result:
            continue

        if result.get("found_sign") and result.get("height_in") is not None:
            height_in = result["height_in"]
            conf = (result.get("confidence") or "low").lower()
            # Sanity bound
            if 48 <= int(height_in) <= 240:
                candidates.append({
                    "pano_id": p["pano_id"],
                    "pano_distance_m": p["distance_m"],
                    "pano_heading": result.get("best_heading", p["bearing_to_target"]),
                    "height_in": int(height_in),
                    "confidence": conf,
                    "conf_rank": CONF_RANK.get(conf, -1),
                    "raw_text": result.get("raw_text", "") or "",
                    "notes": result.get("notes", "") or "",
                })

    if not candidates:
        return {"status": "no-sign", "claude_calls": claude_calls}

    # Rank: highest confidence first, then closest pano as tiebreaker
    candidates.sort(key=lambda c: (-c["conf_rank"], c["pano_distance_m"]))
    winner = candidates[0]

    # Cross-check: if any other candidate found a DIFFERENT height with
    # comparable confidence, demote to medium — discrepancy is a red flag.
    for c in candidates[1:]:
        if c["conf_rank"] >= winner["conf_rank"] and abs(c["height_in"] - winner["height_in"]) > 1:
            if winner["confidence"] == "high":
                winner["confidence"] = "medium"
                winner["notes"] = (winner["notes"] + " | DEMOTED: another pano disagreed on height").strip()
            break

    return {"status": "verified", "claude_calls": claude_calls, **winner}


def process_city(slug, max_cost_usd, min_conf, model, dry_run, running_total):
    city_path = CITIES_DIR / f"{slug}.json"
    if not city_path.exists():
        return {"slug": slug, "error": "no city file", "cost": 0}

    data = json.loads(city_path.read_text())
    garages = data.get("garages", [])
    tunnels = data.get("tunnels", [])
    bridges = data.get("bridges", [])

    # Only process entries that:
    #   - have no recorded height_in, AND
    #   - aren't already stamped as surface_lot, AND
    #   - don't classify as surface_lot via heuristic
    all_structures = garages + tunnels + bridges
    candidates = []
    skipped_lots = 0
    for g in all_structures:
        if g.get("height_in") is not None:
            continue
        if g.get("structure_type") == "surface_lot":
            skipped_lots += 1
            continue
        if _classify_structure:
            kind = _classify_structure(g)
            if kind == "surface_lot":
                skipped_lots += 1
                # Also stamp it so it's recorded
                if not dry_run:
                    g["structure_type"] = "surface_lot"
                _log(f"{date.today()} {slug} {g.get('id','?')} SKIP-SURFACE-LOT")
                continue
        candidates.append(g)

    print(f"[{slug}] {len(candidates)} to verify (skipped {skipped_lots} surface lots)")

    updated = 0
    no_sign = 0
    no_pano = 0
    low_conf = 0
    cost_this_city = 0.0
    aborted_for_cost = False

    for g in candidates:
        # Budget gate — will this garage's worst-case Claude spend push us over?
        worst_case_this_garage = MAX_PANOS_PER_GARAGE * COST_PER_CLAUDE_CALL
        if running_total + cost_this_city + worst_case_this_garage > max_cost_usd:
            print(f"[{slug}] ABORT — next garage ({g.get('id')}) worst-case "
                  f"${worst_case_this_garage:.2f} would exceed cap "
                  f"${max_cost_usd:.2f} (current spend ${running_total + cost_this_city:.2f})")
            aborted_for_cost = True
            break

        tag = f"  {g.get('id','?'):<34} {(g.get('name') or '?')[:40]:<40}"
        print(tag, flush=True)

        res = verify_garage(g, model)
        call_cost = res.get("claude_calls", 0) * COST_PER_CLAUDE_CALL
        cost_this_city += call_cost

        status = res.get("status", "error")

        if status == "no-pano":
            no_pano += 1
            print(f"    → no Google-outdoor pano within {MAX_PANO_DISTANCE_M:.0f}m")
            _log(f"{date.today()} {slug} {g.get('id')} NO-PANO cost=${call_cost:.3f}")
            continue

        if status == "no-sign":
            no_sign += 1
            print(f"    → scanned {res['claude_calls']} pano(s), no sign found (cost=${call_cost:.3f})")
            _log(f"{date.today()} {slug} {g.get('id')} NO-SIGN calls={res['claude_calls']} cost=${call_cost:.3f}")
            continue

        if status == "error":
            print(f"    → error: {res.get('reason')}")
            continue

        # Verified
        conf = res["confidence"]
        if CONF_RANK.get(conf, -1) < CONF_RANK[min_conf]:
            low_conf += 1
            print(f"    → {res['height_in']}in ({inches_to_label(res['height_in'])}) "
                  f"confidence={conf} — below threshold ({min_conf}), NOT written")
            _log(f"{date.today()} {slug} {g.get('id')} LOW-CONF h={res['height_in']} conf={conf} "
                 f"raw={res['raw_text']!r} cost=${call_cost:.3f}")
            continue

        # Write it
        if not dry_run:
            g["height_in"] = int(res["height_in"])
            g["height_label"] = inches_to_label(int(res["height_in"]))
            g["verified_on"] = date.today().isoformat()
            g["pano_id"] = res["pano_id"]
            g["pano_heading"] = round(res["pano_heading"], 1) if res.get("pano_heading") is not None else None
            prev_src = g.get("source", "")
            if "AI-verified" not in prev_src:
                g["source"] = f"AI-verified (Street View + Claude Vision — auto-pano) — was: {prev_src}".strip(" -")
            prior_notes = g.get("notes", "") or ""
            stamp = (
                f'Verified {date.today().isoformat()} from sign reading "{res["raw_text"]}" '
                f'(pano {res["pano_id"]}, heading {res.get("pano_heading","?")}°, '
                f'Claude confidence {conf})'
            )
            g["notes"] = (prior_notes + "\n" + stamp).strip()

        updated += 1
        action = "[DRY] would set" if dry_run else "UPDATED"
        print(f"    → {action}: {res['height_in']}in ({inches_to_label(res['height_in'])}) "
              f"conf={conf} pano={res['pano_id'][:12]}… heading={res.get('pano_heading','?')}° "
              f"(cost=${call_cost:.3f})")
        _log(f"{date.today()} {slug} {g.get('id')} VERIFIED h={res['height_in']} conf={conf} "
             f"pano={res['pano_id']} heading={res.get('pano_heading','?')} "
             f"raw={res['raw_text']!r} cost=${call_cost:.3f}")

    # Persist
    if (updated > 0 or skipped_lots > 0) and not dry_run:
        city_path.write_text(json.dumps(data, indent=2) + "\n")

    print(f"[{slug}] done: verified={updated} no-sign={no_sign} no-pano={no_pano} "
          f"low-conf={low_conf} lots-skipped={skipped_lots} cost=${cost_this_city:.2f}")

    return {
        "slug": slug,
        "updated": updated,
        "no_sign": no_sign,
        "no_pano": no_pano,
        "low_conf": low_conf,
        "skipped_lots": skipped_lots,
        "cost": cost_this_city,
        "aborted": aborted_for_cost,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Autonomous clearance-sign finder via multi-pano Claude Vision.")
    ap.add_argument("--slug", help="Single city slug")
    ap.add_argument("--slugs", help="Comma-separated list")
    ap.add_argument("--all", action="store_true", help="All live cities")
    ap.add_argument("--max-cost", type=float, default=10.0,
                    help="Hard cap on Claude spend in USD (default: $10)")
    ap.add_argument("--confidence", choices=["low", "medium", "high"], default="high",
                    help="Minimum Claude confidence to write a reading (default: high)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true", help="Don't write to city files")
    ap.add_argument("--sleep", type=float, default=0.5,
                    help="Seconds between garages")
    args = ap.parse_args()

    if not (args.slug or args.slugs or args.all):
        ap.error("pass --slug, --slugs, or --all")
    if not GOOGLE_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY not set", file=sys.stderr)
        sys.exit(2)

    idx = json.loads(INDEX_PATH.read_text())
    if args.slug:
        targets = [args.slug]
    elif args.slugs:
        targets = [s.strip() for s in args.slugs.split(",") if s.strip()]
    else:
        targets = [c["slug"] for c in idx if c.get("status") == "live"]

    print(f"auto_verify: {len(targets)} cities, cost cap ${args.max_cost:.2f}, "
          f"confidence >={args.confidence}, model={args.model}, dry_run={args.dry_run}")
    print()

    summaries = []
    running_total = 0.0
    for slug in targets:
        try:
            r = process_city(slug, args.max_cost, args.confidence, args.model,
                             args.dry_run, running_total)
            summaries.append(r)
            running_total += r.get("cost", 0)
            if r.get("aborted"):
                print(f"\nCost cap reached. Stopping.")
                break
        except KeyboardInterrupt:
            print("\nInterrupted.")
            break
        time.sleep(args.sleep)

    print()
    print("=== GRAND SUMMARY ===")
    total_updated = sum(s.get("updated", 0) for s in summaries)
    total_no_sign = sum(s.get("no_sign", 0) for s in summaries)
    total_no_pano = sum(s.get("no_pano", 0) for s in summaries)
    total_low = sum(s.get("low_conf", 0) for s in summaries)
    total_lots = sum(s.get("skipped_lots", 0) for s in summaries)
    print(f"Cities processed:      {len(summaries)}")
    print(f"Verified (written):    {total_updated}")
    print(f"No sign found:         {total_no_sign}")
    print(f"No pano within {MAX_PANO_DISTANCE_M:.0f}m:  {total_no_pano}")
    print(f"Low confidence:        {total_low}")
    print(f"Surface lots skipped:  {total_lots}")
    print(f"Total Claude spend:    ${running_total:.2f}  (cap was ${args.max_cost:.2f})")
    if args.dry_run:
        print("(DRY-RUN — no files written.)")


if __name__ == "__main__":
    main()
