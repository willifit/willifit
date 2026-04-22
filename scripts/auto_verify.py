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

# Auto-load .env from repo root so ANTHROPIC_API_KEY / GOOGLE_MAPS_API_KEY
# don't have to be re-exported in every new shell.  Does nothing if .env
# isn't present; never overwrites vars the user exported explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: F401

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


def _streetview_meta_by_pano(pano_id):
    """Lookup pano metadata by ID (to recover its lat/lng for recorded panos)."""
    q = parse.urlencode({"pano": pano_id, "key": GOOGLE_KEY})
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
Your job is to find EVERY distinct posted vehicle-height CLEARANCE sign
visible in any of the 8 images.  Some facilities have multiple gates with
different clearances (airports with short-term + long-term, casinos with
car + RV lanes, etc.) -- those deserve SEPARATE entries in the "signs"
array.

What counts as a clearance sign:
  * A sign showing a vehicle height in feet-inches: "11'6\"", "13 FT 6 IN",
    "CLEARANCE 11' 6\"", "MAX HEIGHT 7'0""
  * A ceiling-mounted clearance bar with a posted height number
  * A yellow-diamond or orange LOW CLEARANCE warning with a height number
  * The sign must SHOW DIGITS representing a height

What does NOT count (do not invent heights from these):
  * "SHORT TERM PARKING" / "LONG TERM" / "VALET" / "RESERVED" / "EXIT" --
    those identify a lane but are not themselves clearance numbers.  Use
    them as the "label" field on a nearby clearance sign if the lane
    context is clear.
  * Speed limit, street name, parking rate, business, weight limit signs
  * Building addresses, suite numbers, floor numbers
  * Any sign without an explicit height-in-feet-or-inches number

CRITICAL ANTI-HALLUCINATION RULES:
  * raw_text MUST be the EXACT height text read on the sign,
    character-for-character.  If you cannot quote the height verbatim,
    do NOT include that sign in the signs array.
  * The height digits in raw_text MUST match height_in.  If raw_text
    says "7'0\"" then height_in must be 84.  If no height number is
    readable, skip that sign.
  * If the only "sign" you see is a lane label ("SHORT TERM PARKING")
    with no height number, that is NOT a clearance sign.  Skip it.

Deduping:
  * If the SAME clearance sign appears in multiple images, include it
    ONCE (pick the heading with the clearest view).
  * If two signs show the same height, treat them as one sign and
    include once (unless they clearly have different lane labels).
  * If two signs show DIFFERENT heights, include BOTH as separate
    entries -- that's a multi-section facility.

Conversion examples:  7'0" = 84,  7'6" = 90,  11'6" = 138,  13'0" = 156.

Per-sign fields:
  heading    (number)  which of the 8 headings showed this sign best
  height_in  (integer) posted clearance in inches
  label      (string)  lane identifier IF the sign or its context makes
                       it obvious ("Short-term", "Long-term", "RV",
                       "Compact") -- otherwise ""
  confidence "low" | "medium" | "high"
  raw_text   (string)  exact height text from the sign

Keep "notes" to a SHORT fragment (<=20 words).  The JSON output MUST
fit well under 500 tokens total.

Return ONLY a JSON object (no prose, no markdown fences):

{{
  "signs": [
    {{ "heading": number, "height_in": integer, "label": string,
       "confidence": "low" | "medium" | "high", "raw_text": string }}
  ],
  "notes": string
}}

If no clearance signs are visible in any image, return {{"signs": [], "notes": "..."}}.
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
            max_tokens=2500,  # generous; actual JSON is <300 tokens, buffer is for model reasoning
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

    # Happy path: the response is complete and parses as JSON.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # Fallback: the response got truncated mid-JSON (max_tokens cap, or
    # the model decided to stop early with a long trailing notes field).
    # Walk the signs array by balanced braces and recover whatever
    # complete sign-objects are in there.
    partial = _extract_partial_fields(text)
    if partial.get("signs"):
        stop_reason = getattr(resp, "stop_reason", "?")
        print(f"    (Claude response truncated [stop_reason={stop_reason}], "
              f"recovered {len(partial['signs'])} sign(s) by regex)", file=sys.stderr)
        return partial

    print(f"    Claude replied non-JSON: {text[:200]!r}", file=sys.stderr)
    return None


def _extract_partial_fields(text):
    """Recover the fields of interest from a truncated or malformed JSON
    response.  Used when the model stops mid-object (typically during a
    long `notes` value).  Walks each well-formed "{...}" sign object
    within the "signs": [ ... ] array and returns whatever parses.

    Returns a dict with a `signs` list (possibly empty) and an optional
    `notes` string, matching the schema the caller expects."""
    out = {"signs": []}

    # Find the "signs": [ region and walk balanced-brace objects inside
    signs_match = re.search(r'"signs"\s*:\s*\[', text)
    if signs_match:
        tail = text[signs_match.end():]
        depth = 0
        start = None
        for i, ch in enumerate(tail):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    blob = tail[start:i+1]
                    sign = _parse_one_sign(blob)
                    if sign:
                        out["signs"].append(sign)
                    start = None
            elif ch == "]" and depth == 0:
                break

    # Top-level notes field (may also be truncated)
    nm = re.search(r'"notes"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if nm:
        out["notes"] = nm.group(1)
    return out


def _parse_one_sign(blob):
    """Parse a single sign object fragment from a possibly-truncated JSON.
    Uses the same per-field regex approach as the flat partial-extractor."""
    patterns = {
        "heading":    (r'"heading"\s*:\s*(-?\d+(?:\.\d+)?|null)',  lambda v: None if v == "null" else float(v)),
        "height_in":  (r'"height_in"\s*:\s*(-?\d+|null)',          lambda v: None if v == "null" else int(v)),
        "label":      (r'"label"\s*:\s*"((?:[^"\\]|\\.)*)"',       lambda v: v),
        "confidence": (r'"confidence"\s*:\s*"(low|medium|high)"',  lambda v: v),
        "raw_text":   (r'"raw_text"\s*:\s*"((?:[^"\\]|\\.)*)"',    lambda v: v),
    }
    out = {}
    for key, (patt, coerce) in patterns.items():
        m = re.search(patt, blob)
        if m:
            try:
                out[key] = coerce(m.group(1))
            except (ValueError, TypeError):
                pass
    # A sign needs at minimum height_in and raw_text to be useful
    if "height_in" in out and out["height_in"] is not None and "raw_text" in out:
        out.setdefault("label", "")
        out.setdefault("confidence", "low")
        out.setdefault("heading", None)
        return out
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


def _height_matches_raw(height_in, raw_text):
    """Defense against Claude hallucinating a height when raw_text has none.
    We require raw_text to contain DIGITS that plausibly encode the stated
    height.  Accepts either feet-inches form ("7'0"", "13 FT 6 IN") or a
    bare inch number.  Returns True if plausible, False if the model
    invented digits."""
    if not raw_text or height_in is None:
        return False
    digits = re.findall(r"\d+", raw_text)
    if not digits:
        return False
    ft = height_in // 12
    rem = height_in % 12
    # Any of these numeric combos is a plausible match:
    candidates = {str(ft), str(rem), str(height_in), f"{ft}{rem:02d}"}
    return any(d in candidates for d in digits)


def verify_garage(g, model):
    """Multi-pano scan.  Aggregates every readable clearance sign found
    across up to MAX_PANOS_PER_GARAGE panos.  Dedupes by height (within
    a 1-inch tolerance) and returns:

        status: "verified" | "no-sign" | "no-pano" | "error"
        claude_calls: int
        if status==verified: {
            pano_id, pano_heading, height_in, height_label, confidence,
            raw_text, notes,
            sections: list of {label, height_in, height_label}  (2+ only,
                      omitted when only one distinct height was found)
        }
    """
    lat, lng = g.get("lat"), g.get("lng")
    if lat is None:
        return {"status": "error", "reason": "no-coords", "claude_calls": 0}

    panos = find_candidate_panos(lat, lng, max_panos=MAX_PANOS_PER_GARAGE)

    # Always scan a previously-recorded pano first if the record has one.
    recorded = g.get("pano_id")
    if recorded and not any(p["pano_id"] == recorded for p in panos):
        rec_meta = _streetview_meta_by_pano(recorded)
        if rec_meta and rec_meta.get("status") == "OK":
            rloc = rec_meta.get("location") or {}
            if rloc.get("lat") is not None:
                panos.insert(0, {
                    "pano_id": recorded,
                    "pano_lat": rloc["lat"],
                    "pano_lng": rloc["lng"],
                    "distance_m": haversine_m(rloc["lat"], rloc["lng"], lat, lng),
                    "bearing_to_target": bearing_deg(rloc["lat"], rloc["lng"], lat, lng),
                    "copyright": rec_meta.get("copyright") or "",
                    "via_probe": "recorded",
                })
                panos = panos[:MAX_PANOS_PER_GARAGE]

    if not panos:
        return {"status": "no-pano", "claude_calls": 0}

    # Scan each pano.  Flatten all returned signs across all panos.
    all_signs = []  # list of dicts, each augmented with pano info
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

        for s in (result.get("signs") or []):
            height_in = s.get("height_in")
            raw_text = s.get("raw_text", "") or ""
            conf = (s.get("confidence") or "low").lower()
            if height_in is None:
                continue
            if not (48 <= int(height_in) <= 240):
                print(f"    ⚠ rejecting implausible {height_in}in (raw={raw_text!r})")
                _log(f"{date.today()} {g.get('id')} REJECT-IMPLAUSIBLE pano={p['pano_id']} h={height_in}")
                continue
            if not _height_matches_raw(int(height_in), raw_text):
                print(f"    ⚠ rejecting hallucinated {height_in}in — raw_text={raw_text!r}")
                _log(f"{date.today()} {g.get('id')} REJECT-HALLUCINATION pano={p['pano_id']} "
                     f"h={height_in} raw={raw_text!r}")
                continue
            all_signs.append({
                "pano_id": p["pano_id"],
                "pano_distance_m": p["distance_m"],
                "heading": s.get("heading") if s.get("heading") is not None else p["bearing_to_target"],
                "height_in": int(height_in),
                "label": s.get("label") or "",
                "confidence": conf,
                "conf_rank": CONF_RANK.get(conf, -1),
                "raw_text": raw_text,
            })

    if not all_signs:
        return {"status": "no-sign", "claude_calls": claude_calls}

    # Dedupe by approximate height (within 1 inch = same physical sign).
    # Within a group, pick the highest-confidence / closest-pano instance.
    # Preserve the most informative non-empty `label` across duplicates.
    grouped = {}
    for s in all_signs:
        # Bucket key: round to nearest inch
        key = int(round(s["height_in"]))
        existing = grouped.get(key)
        if not existing:
            grouped[key] = s
        else:
            # Prefer higher confidence, break ties by closer pano
            if (s["conf_rank"], -s["pano_distance_m"]) > (existing["conf_rank"], -existing["pano_distance_m"]):
                # Keep the better one but inherit a non-empty label
                if existing.get("label") and not s.get("label"):
                    s["label"] = existing["label"]
                grouped[key] = s
            elif not existing.get("label") and s.get("label"):
                existing["label"] = s["label"]

    distinct = sorted(grouped.values(), key=lambda x: x["height_in"])
    primary = distinct[0]  # strictest (lowest) height becomes the primary reading

    result = {
        "status": "verified",
        "claude_calls": claude_calls,
        "pano_id": primary["pano_id"],
        "pano_heading": primary["heading"],
        "pano_distance_m": primary["pano_distance_m"],
        "height_in": primary["height_in"],
        "height_label": inches_to_label(primary["height_in"]),
        "confidence": primary["confidence"],
        "raw_text": primary["raw_text"],
        "notes": "",
    }

    # Multi-section output only when we actually found 2+ distinct heights.
    if len(distinct) >= 2:
        result["sections"] = [
            {
                "label": d.get("label") or f"Lane {i+1}",
                "height_in": d["height_in"],
                "height_label": inches_to_label(d["height_in"]),
            }
            for i, d in enumerate(distinct)
        ]

    return result


def process_city(slug, max_cost_usd, min_conf, model, dry_run, running_total, check_only=False):
    city_path = CITIES_DIR / f"{slug}.json"
    if not city_path.exists():
        return {"slug": slug, "error": "no city file", "cost": 0}

    data = json.loads(city_path.read_text())
    garages = data.get("garages", [])
    tunnels = data.get("tunnels", [])
    bridges = data.get("bridges", [])

    # --check mode inverts the selection: instead of finding unverified
    # entries, it re-runs the pipeline against entries that ALREADY have
    # a stored height_in, so we can catch drift or manual entry errors.
    # Surface lots are skipped in either mode (no clearance to check).
    all_structures = garages + tunnels + bridges
    candidates = []
    skipped_lots = 0
    for g in all_structures:
        if g.get("structure_type") == "surface_lot":
            skipped_lots += 1
            continue
        if check_only:
            if g.get("height_in") is None:
                continue  # only re-check entries that have a value to check
        else:
            if g.get("height_in") is not None:
                continue  # only fill entries that need a value
            if _classify_structure:
                kind = _classify_structure(g)
                if kind == "surface_lot":
                    skipped_lots += 1
                    if not dry_run:
                        g["structure_type"] = "surface_lot"
                    _log(f"{date.today()} {slug} {g.get('id','?')} SKIP-SURFACE-LOT")
                    continue
        candidates.append(g)

    mode_word = "to re-check" if check_only else "to verify"
    print(f"[{slug}] {len(candidates)} {mode_word} (skipped {skipped_lots} surface lots)"
          f"{' [CHECK MODE -- no writes]' if check_only else ''}")

    updated = 0
    no_sign = 0
    no_pano = 0
    low_conf = 0
    cost_this_city = 0.0
    aborted_for_cost = False
    mismatches = []  # populated by --check mode when AI disagrees with stored

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

        # Verified — AI has a reading
        conf = res["confidence"]
        ai_height = int(res["height_in"])
        ai_sections = res.get("sections")  # list of {label, height_in, height_label} or None
        sections_tag = f" [+{len(ai_sections)-1} more section(s)]" if ai_sections else ""

        if CONF_RANK.get(conf, -1) < CONF_RANK[min_conf]:
            low_conf += 1
            print(f"    → {ai_height}in ({inches_to_label(ai_height)}){sections_tag} "
                  f"confidence={conf} — below threshold ({min_conf}), NOT written")
            _log(f"{date.today()} {slug} {g.get('id')} LOW-CONF h={ai_height} conf={conf} "
                 f"raw={res['raw_text']!r} cost=${call_cost:.3f}")
            continue

        # CHECK-MODE: compare AI reading to stored value and REPORT without
        # touching the JSON.  Flags: exact match, height within 1" drift,
        # meaningful disagreement (>1"), missing-section discrepancy.
        if check_only:
            stored_h = g.get("height_in")
            stored_sections = g.get("sections")
            diff_h = ai_height - (stored_h or 0) if stored_h is not None else None

            tag = "OK"
            color = ""
            if stored_h is None:
                tag = "AI-FOUND-NEW"
                color = "✨"
            elif abs(ai_height - stored_h) == 0:
                tag = "MATCH"
                color = "✓"
            elif abs(ai_height - stored_h) <= 1:
                tag = "CLOSE"
                color = "~"
            else:
                tag = "MISMATCH"
                color = "⚠"

            # Section-level discrepancy
            sec_tag = ""
            stored_set = {s.get("height_in") for s in (stored_sections or [])} if isinstance(stored_sections, list) else set()
            ai_set = {s.get("height_in") for s in (ai_sections or [])} if isinstance(ai_sections, list) else set()
            if stored_set or ai_set:
                if stored_set != ai_set:
                    sec_tag = f" · SECTIONS DIFFER stored={sorted(stored_set)} ai={sorted(ai_set)}"

            print(f"    → {color} {tag}: stored={stored_h}in ai={ai_height}in "
                  f"(conf={conf}){sections_tag}{sec_tag} raw={res['raw_text']!r} "
                  f"(cost=${call_cost:.3f})")
            _log(f"{date.today()} {slug} {g.get('id')} CHECK-{tag} "
                 f"stored={stored_h} ai={ai_height} conf={conf} raw={res['raw_text']!r}"
                 f"{sec_tag} cost=${call_cost:.3f}")

            if tag == "MISMATCH":
                mismatches.append({
                    "id": g.get("id"), "name": g.get("name"),
                    "stored_h": stored_h, "ai_h": ai_height,
                    "ai_raw": res["raw_text"], "ai_confidence": conf,
                    "ai_pano_id": res["pano_id"], "ai_pano_heading": res.get("pano_heading"),
                    "stored_sections": stored_sections, "ai_sections": ai_sections,
                })
            continue

        # NORMAL (write) mode
        if not dry_run:
            g["height_in"] = ai_height
            g["height_label"] = inches_to_label(ai_height)
            g["verified_on"] = date.today().isoformat()
            g["pano_id"] = res["pano_id"]
            g["pano_heading"] = round(res["pano_heading"], 1) if res.get("pano_heading") is not None else None
            if ai_sections and len(ai_sections) >= 2:
                g["sections"] = ai_sections
            prev_src = g.get("source", "")
            if "AI-verified" not in prev_src:
                g["source"] = f"AI-verified (Street View + Claude Vision — auto-pano) — was: {prev_src}".strip(" -")
            prior_notes = g.get("notes", "") or ""
            stamp = (
                f'Verified {date.today().isoformat()} from sign reading "{res["raw_text"]}" '
                f'(pano {res["pano_id"]}, heading {res.get("pano_heading","?")}°, '
                f'Claude confidence {conf}{sections_tag})'
            )
            g["notes"] = (prior_notes + "\n" + stamp).strip()

        updated += 1
        action = "[DRY] would set" if dry_run else "UPDATED"
        print(f"    → {action}: {ai_height}in ({inches_to_label(ai_height)}){sections_tag} "
              f"conf={conf} pano={res['pano_id'][:12]}… heading={res.get('pano_heading','?')}° "
              f"(cost=${call_cost:.3f})")
        _log(f"{date.today()} {slug} {g.get('id')} VERIFIED h={ai_height} conf={conf} "
             f"sections={len(ai_sections) if ai_sections else 1} "
             f"pano={res['pano_id']} heading={res.get('pano_heading','?')} "
             f"raw={res['raw_text']!r} cost=${call_cost:.3f}")

    # Persist — only in normal mode.  --check never writes, even on "new"
    # findings (those should be re-verified in a separate normal run).
    if not check_only and (updated > 0 or skipped_lots > 0) and not dry_run:
        city_path.write_text(json.dumps(data, indent=2) + "\n")

    if check_only:
        print(f"[{slug}] check done: mismatches={len(mismatches)} "
              f"no-sign={no_sign} no-pano={no_pano} low-conf={low_conf} "
              f"cost=${cost_this_city:.2f}")
    else:
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
        "mismatches": mismatches,
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
    ap.add_argument("--check", action="store_true",
                    help="Re-verify already-verified entries WITHOUT writing. "
                         "Reports per-garage MATCH / CLOSE / MISMATCH + section "
                         "discrepancies so a human can review.")
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

    mode = "CHECK" if args.check else ("DRY-RUN" if args.dry_run else "LIVE")
    print(f"auto_verify [{mode}]: {len(targets)} cities, cost cap ${args.max_cost:.2f}, "
          f"confidence >={args.confidence}, model={args.model}")
    print()

    summaries = []
    running_total = 0.0
    for slug in targets:
        try:
            r = process_city(slug, args.max_cost, args.confidence, args.model,
                             args.dry_run, running_total, check_only=args.check)
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
    all_mismatches = [m for s in summaries for m in (s.get("mismatches") or [])]

    print(f"Mode:                   {mode}")
    print(f"Cities processed:       {len(summaries)}")
    if args.check:
        print(f"Entries re-checked:     {total_updated + total_no_sign + total_no_pano + total_low + len(all_mismatches)}")
        print(f"MISMATCHES needing review: {len(all_mismatches)}")
    else:
        print(f"Verified (written):     {total_updated}")
    print(f"No sign found:          {total_no_sign}")
    print(f"No pano within {MAX_PANO_DISTANCE_M:.0f}m:   {total_no_pano}")
    print(f"Low confidence:         {total_low}")
    if not args.check:
        print(f"Surface lots skipped:   {total_lots}")
    print(f"Total Claude spend:     ${running_total:.2f}  (cap was ${args.max_cost:.2f})")

    if all_mismatches:
        print()
        print("=== MISMATCHES (stored vs AI disagree by >1 inch) ===")
        for m in all_mismatches:
            print(f"  {m['id']:<38} stored={m['stored_h']}in  ai={m['ai_h']}in  "
                  f"conf={m['ai_confidence']}  raw={m['ai_raw']!r}")
            if m.get("ai_sections"):
                for s in m["ai_sections"]:
                    print(f"    • AI saw: {s.get('label','?')} = {s['height_label']}")

    if args.dry_run:
        print("(DRY-RUN — no files written.)")


if __name__ == "__main__":
    main()
