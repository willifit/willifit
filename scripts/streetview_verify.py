#!/usr/bin/env python3
"""
WillIFit — Street View clearance-sign verifier.

For every parking garage in our DB that has NO posted clearance (height_in is
null), this script:
  1. Checks whether Google Street View has imagery for that lat/lng (free call).
  2. Fetches 4 Street View images at different headings (N/E/S/W).
  3. Sends all 4 images + a structured prompt to Claude Vision.
  4. Parses Claude's JSON reply.
  5. If confidence is high enough, writes the new height_in / height_label /
     verified_on / source fields back to the city JSON.

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
LOG_PATH = REPO_ROOT / "data" / "streetview_verify.log"

GOOGLE_KEY = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_STREETVIEW_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

DEFAULT_MODEL = "claude-haiku-4-5"   # cheap + fast; upgrade to sonnet for tougher signs
IMG_SIZE = "640x640"                 # max size on free Static tier
HEADINGS = [0, 90, 180, 270]         # N, E, S, W — 4 cardinal angles
PITCH = 10                           # slight upward tilt to catch signage above entrance
FOV = 90                             # wide enough to see signage + entrance

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


def streetview_has_imagery(lat: float, lng: float) -> bool:
    """Free metadata check — returns True if Google has imagery at this point.
    Avoids paying for images that would return a grey 'no imagery' placeholder."""
    q = parse.urlencode({"location": f"{lat},{lng}", "key": GOOGLE_KEY})
    status, body, _ = _http_get(f"{SV_META}?{q}", timeout=10)
    if status != 200:
        return False
    try:
        data = json.loads(body)
        return data.get("status") == "OK"
    except Exception:
        return False


def fetch_streetview_image(lat: float, lng: float, heading: int) -> Optional[bytes]:
    """Pull one Street View Static image. Returns raw JPEG bytes or None on error."""
    q = parse.urlencode({
        "size": IMG_SIZE,
        "location": f"{lat},{lng}",
        "heading": heading,
        "pitch": PITCH,
        "fov": FOV,
        "key": GOOGLE_KEY,
        "return_error_code": "true",  # so we see 4xx instead of a grey placeholder
    })
    status, body, _ = _http_get(f"{SV_STATIC}?{q}", timeout=20)
    if status == 200 and body.startswith(b"\xff\xd8"):  # JPEG magic
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

    # Free metadata check first so we don't burn budget on lat/lngs with no imagery
    if not streetview_has_imagery(lat, lng):
        return {"sv_status": "no-imagery"}

    images = []
    for h in HEADINGS:
        img = fetch_streetview_image(lat, lng, h)
        if img:
            images.append(img)

    if len(images) < 2:
        return {"sv_status": "too-few-images", "images_fetched": len(images)}

    result = verify_with_claude(images, model)
    if not result:
        return {"sv_status": "claude-parse-fail"}

    result["sv_status"] = "ok"
    result["images_used"] = len(images)
    return result


def _log(line: str):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def process_city(
    slug: str,
    limit: Optional[int],
    min_conf: str,
    model: str,
    dry_run: bool,
    overwrite: bool,
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

    print(f"[{slug}] {len(candidates)} candidates (limit={limit})")

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

        if res.get("sv_status") == "no-imagery":
            no_imagery += 1
            print("no Street View imagery")
            _log(f"{date.today()} {slug} {g['id']} NO-IMAGERY")
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

        if not found or height_in is None:
            no_sign += 1
            print(f"no sign ({conf})")
            _log(f"{date.today()} {slug} {g['id']} NO-SIGN conf={conf} notes={notes!r}")
            continue

        if CONF_RANK.get(conf, -1) < CONF_RANK[min_conf]:
            low_conf += 1
            print(f"low confidence: {conf} — {height_in}in {raw!r}")
            _log(f"{date.today()} {slug} {g['id']} LOW-CONF conf={conf} h={height_in} raw={raw!r}")
            continue

        # Sanity bound
        if not (48 <= int(height_in) <= 240):
            print(f"implausible height {height_in}in — discarding")
            _log(f"{date.today()} {slug} {g['id']} IMPLAUSIBLE h={height_in}")
            continue

        # Record the verified reading
        if not dry_run:
            g["height_in"] = int(height_in)
            g["height_label"] = inches_to_label(int(height_in))
            g["verified_on"] = date.today().isoformat()
            prev_src = g.get("source", "")
            if "AI-verified" not in prev_src:
                g["source"] = f"AI-verified (Street View + Claude Vision) — was: {prev_src}".strip(" -")
            # Append to notes (keep existing)
            prior_notes = g.get("notes", "")
            stamp = f'Verified {date.today().isoformat()} from sign reading: "{raw}"'
            g["notes"] = (prior_notes + "\n" + stamp).strip() if prior_notes else stamp

        updated += 1
        action = "[DRY] would set" if dry_run else "UPDATED"
        print(f"{action}: {height_in}in ({inches_to_label(int(height_in))}) conf={conf}")
        _log(f"{date.today()} {slug} {g['id']} VERIFIED h={height_in} conf={conf} raw={raw!r}")

    if updated and not dry_run:
        city_path.write_text(json.dumps(data, indent=2) + "\n")

    print(f"  {slug}: checked={checked} updated={updated} "
          f"no-imagery={no_imagery} no-sign={no_sign} low-conf={low_conf}")
    return {
        "slug": slug, "checked": checked, "updated": updated,
        "no_imagery": no_imagery, "no_sign": no_sign, "low_conf": low_conf,
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
                             args.dry_run, args.overwrite)
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

    print("\n=== SUMMARY ===")
    print(f"Cities processed:   {len(summary)}")
    print(f"Garages checked:    {total_checked}")
    print(f"Updated:            {total_updated}")
    print(f"No Street View:     {total_no_img}")
    print(f"No clearance sign:  {total_no_sign}")
    print(f"Low confidence:     {total_low}")
    if args.dry_run:
        print("(DRY-RUN — no files written.)")


if __name__ == "__main__":
    main()
