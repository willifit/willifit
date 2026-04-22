#!/usr/bin/env python3
"""
bulk_update.py — fill the gaps the automated pipelines miss, using
Google Maps Street View URLs the user pastes into a plain TSV file.

Why this exists:
  Even after find_entrances.py + auto_verify.py + website_verify.py have
  done their thing, a chunk of garages remain unverified because:
    - OSM didn't tag the entrance
    - Street View Static picked panos facing the wrong way
    - The clearance sign is inside the ramp, not on the street face
  For these, a human opens Google Maps, pans to the sign, and just
  tells us what they see.  This script automates the busywork of
  turning that URL + number into a proper JSON update.

Input file format (TSV — tab-separated values):

  # Lines starting with # are ignored. First non-comment row is the header.
  city              garage_id          url                                height      notes
  reno-nv           rno-citycenter     https://www.google.com/maps/...    7'6"        downtown
  las-vegas-nv      unlv-tropicana     https://www.google.com/maps/...    8'0"
  portland-or       pdx-smartpark-x    https://www.google.com/maps/...    6'8"        3rd floor

  # Special values for "height":
  #   surface       -> mark as surface_lot, no clearance
  #   7'7"/8'2"     -> multi-section (slash separates distinct readings)
  #   skip          -> leave this row untouched (useful for re-runs)

Pano data we extract from the URL (no API cost):
  - pano_id (the !1s... segment)
  - camera heading (the ...h segment)
  - camera lat/lng (the @lat,lng segment)

We don't make any API calls unless --verify-ai is passed.  If you do
pass --verify-ai, we fetch the pano image for each row and ask Claude
"does this sign actually say {height}?" as a typo-catch.  Costs about
$0.003 per row (Haiku reading one image).

Usage:
  python3 scripts/bulk_update.py data/manual_updates.tsv
  python3 scripts/bulk_update.py data/manual_updates.tsv --dry-run
  python3 scripts/bulk_update.py data/manual_updates.tsv --verify-ai
  python3 scripts/bulk_update.py data/manual_updates.tsv --commit --push
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Optional
from urllib import parse, request, error

REPO_ROOT = Path(__file__).resolve().parent.parent
CITIES_DIR = REPO_ROOT / "data" / "cities"

# Auto-load .env
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: F401

GOOGLE_KEY = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_STREETVIEW_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

SV_STATIC = "https://maps.googleapis.com/maps/api/streetview"
HAIKU = "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

# Google Maps URL patterns:
#   /@lat,lng,ZOOMy,HEADINGh,PITCHt/data=!3m7!1e1!3m5!1sPANOID!2e0!...
# Example pano IDs follow !1s and go to the next !
PANO_ID_RE   = re.compile(r"!1s([A-Za-z0-9_\-]+)")
HEADING_RE   = re.compile(r",([0-9]+\.?[0-9]*)h")
PITCH_RE     = re.compile(r",([0-9]+\.?[0-9]*)t")
LATLNG_RE    = re.compile(r"@(-?[0-9]+\.[0-9]+),(-?[0-9]+\.[0-9]+)")


def parse_maps_url(url: str) -> dict:
    """Extract pano_id, camera heading/pitch, and camera lat/lng from a
    Google Maps Street View URL.  Returns a dict with whatever we could
    extract; missing fields are None."""
    out = {"pano_id": None, "heading": None, "pitch": None, "cam_lat": None, "cam_lng": None}

    m = PANO_ID_RE.search(url)
    if m:
        out["pano_id"] = m.group(1)

    m = HEADING_RE.search(url)
    if m:
        try:
            out["heading"] = float(m.group(1))
        except ValueError:
            pass

    m = PITCH_RE.search(url)
    if m:
        try:
            out["pitch"] = float(m.group(1))
        except ValueError:
            pass

    m = LATLNG_RE.search(url)
    if m:
        try:
            out["cam_lat"] = float(m.group(1))
            out["cam_lng"] = float(m.group(2))
        except ValueError:
            pass

    return out


# ---------------------------------------------------------------------------
# Height string -> inches
# ---------------------------------------------------------------------------

# Matches "7'6"", "7'6", "7' 6"", "7ft 6in", "84"", "84 inches", etc.
FT_IN_RE = re.compile(r"(\d+)\s*(?:'|ft|feet|foot)\s*(\d+)?", re.IGNORECASE)
JUST_IN_RE = re.compile(r'^(\d+)\s*(?:"|in|inch|inches)?\s*$', re.IGNORECASE)


def height_to_inches(s: str) -> Optional[int]:
    """Parse a height string like 7'6" into inches.  Returns None if
    unparseable.  Accepts bare inches too (e.g. "84" -> 84)."""
    s = s.strip().rstrip('"').strip()
    if not s:
        return None
    # Try "7'6" or "7' 6"" or "7ft 6in" first
    m = FT_IN_RE.search(s)
    if m:
        ft = int(m.group(1))
        inches = int(m.group(2) or 0)
        return ft * 12 + inches
    # Bare inches, like "84" or "84in"
    m = JUST_IN_RE.match(s)
    if m:
        return int(m.group(1))
    return None


def inches_to_label(n: int) -> str:
    ft, rem = n // 12, n % 12
    return f"{ft}'{rem}\""


def parse_height_field(s: str) -> dict:
    """Parse the TSV 'height' column.  Returns one of:
      {"kind": "skip"}
      {"kind": "surface"}
      {"kind": "remove"}                                (DELETE the entry)
      {"kind": "single", "height_in": N}
      {"kind": "sections", "sections": [{"height_in": N, "label": ""} ...]}
      {"kind": "error", "message": "..."}
    """
    s = s.strip()
    if not s or s.lower() == "skip":
        return {"kind": "skip"}
    # Remove the whole entry — there is no parking at this location, the
    # business closed, the import was wrong, or similar.  Destructive;
    # user can git-revert if they change their mind.
    if s.lower() in ("remove", "delete", "no-parking", "no parking", "closed", "none"):
        return {"kind": "remove"}
    if s.lower() in ("surface", "surface_lot", "open-air", "uncovered"):
        return {"kind": "surface"}
    # Multi-section support: "7'7"/8'2"" or "7'7\" / 8'2\""
    if "/" in s:
        parts = [p.strip() for p in s.split("/")]
        sections = []
        for p in parts:
            h = height_to_inches(p)
            if h is None:
                return {"kind": "error", "message": f"couldn't parse section {p!r}"}
            sections.append({"height_in": h, "label": ""})
        if len(sections) < 2:
            return {"kind": "error", "message": "< 2 sections after split"}
        return {"kind": "sections", "sections": sections}
    # Single height
    h = height_to_inches(s)
    if h is None:
        return {"kind": "error", "message": f"couldn't parse height {s!r}"}
    if not (48 <= h <= 240):
        return {"kind": "error", "message": f"height {h}in out of plausible range"}
    return {"kind": "single", "height_in": h}


# ---------------------------------------------------------------------------
# Optional AI verification of the typed height
# ---------------------------------------------------------------------------

def fetch_pano_image(pano_id: str, heading: float, size: str = "640x640", fov: int = 40) -> Optional[bytes]:
    """Fetch a Street View Static image for a given pano+heading. Returns JPEG bytes or None."""
    if not GOOGLE_KEY:
        return None
    q = parse.urlencode({
        "size": size, "pano": pano_id,
        "heading": round(heading or 0, 2),
        "pitch": 0, "fov": fov,
        "key": GOOGLE_KEY, "return_error_code": "true",
    })
    try:
        req = request.Request(f"{SV_STATIC}?{q}", headers={"User-Agent": "willifit-bulk-update/0.1"})
        with request.urlopen(req, timeout=20) as resp:
            if resp.status != 200:
                return None
            body = resp.read()
            if body.startswith(b"\xff\xd8"):
                return body
    except Exception:
        pass
    return None


AI_VERIFY_PROMPT = """I'm looking at one Google Street View image from a specific pano at a known
heading.  The image should show the entrance of a parking garage and its
posted clearance sign.

A human viewer reported the clearance as: {claimed}

Please answer:
  - Is there a vehicle-clearance sign visible in this image? (yes/no)
  - If yes, what height does it show? (in inches)
  - Does that match the claimed height (within 1 inch)?

Return JSON:
{{
  "sign_visible": boolean,
  "read_in": integer | null,
  "raw_text": string,
  "matches_claim": boolean
}}
"""


def ai_verify_height(image_bytes: bytes, claimed_label: str, model: str = HAIKU) -> Optional[dict]:
    if not ANTHROPIC_KEY:
        return None
    try:
        import anthropic
    except ImportError:
        print("  (anthropic SDK not installed; install with: pip install anthropic)", file=sys.stderr)
        return None
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    try:
        resp = client.messages.create(
            model=model, max_tokens=300,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                              "data": base64.standard_b64encode(image_bytes).decode("ascii")}},
                {"type": "text", "text": AI_VERIFY_PROMPT.format(claimed=claimed_label)},
            ]}],
        )
    except Exception as e:
        print(f"  (Claude error: {e})", file=sys.stderr)
        return None
    text = ""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text = block.text
            break
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
    return None


# ---------------------------------------------------------------------------
# TSV reader
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = {"city", "garage_id", "url", "height"}
OPTIONAL_COLUMNS = {"notes"}


def read_tsv(path: Path) -> list:
    """Read TSV or CSV.  Auto-detects the delimiter by sampling the first
    non-comment line for tabs vs commas.  Google Sheets exports CSV by
    default; the build_verify_spreadsheet.py generator emits CSV too; but
    TSV is easier to hand-edit in a text editor.  Both work here."""
    import csv as _csv

    # First pass: find the first non-blank / non-comment line, sniff delimiter
    sample_line = None
    with path.open() as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            sample_line = line
            break
    if sample_line is None:
        print(f"ERROR: {path} is empty or all comments", file=sys.stderr)
        sys.exit(2)

    delimiter = "\t" if sample_line.count("\t") > sample_line.count(",") else ","

    rows = []
    with path.open() as f:
        reader = _csv.reader(f, delimiter=delimiter)
        header = None
        for lineno, parts in enumerate(reader, start=1):
            if not parts:
                continue
            if all(not p.strip() for p in parts):
                continue
            # Skip comment lines (those starting with # in the first column)
            if parts[0].lstrip().startswith("#"):
                continue
            parts = [p.strip() for p in parts]
            if header is None:
                header = [c.lower() for c in parts]
                missing = REQUIRED_COLUMNS - set(header)
                if missing:
                    print(f"ERROR: input file missing required columns: {missing}",
                          file=sys.stderr)
                    sys.exit(2)
                continue
            if len(parts) < len(header):
                parts += [""] * (len(header) - len(parts))
            row = dict(zip(header, parts))
            row["_lineno"] = lineno
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Per-row apply
# ---------------------------------------------------------------------------

def find_garage(data: dict, gid: str) -> Optional[tuple[str, dict]]:
    """Find an entry by id.  Returns ('garages'|'tunnels'|'bridges', garage_dict)
    or None."""
    for bucket in ("garages", "tunnels", "bridges"):
        for g in data.get(bucket, []):
            if g.get("id") == gid:
                return (bucket, g)
    return None


def apply_row(row: dict, dry_run: bool, verify_ai: bool) -> dict:
    city = row.get("city", "").strip()
    gid = row.get("garage_id", "").strip()
    url = row.get("url", "").strip()
    height_field = row.get("height", "").strip()
    extra_notes = (row.get("notes") or "").strip()

    prefix = f"  L{row.get('_lineno')} {city}:{gid:<32}"

    if not city or not gid:
        print(f"{prefix} skip -- missing city or garage_id")
        return {"status": "skipped", "reason": "missing-fields"}

    parsed = parse_height_field(height_field)
    if parsed["kind"] == "skip":
        print(f"{prefix} skip row")
        return {"status": "skipped", "reason": "skip"}
    if parsed["kind"] == "error":
        print(f"{prefix} ERROR: {parsed['message']}")
        return {"status": "error", "reason": parsed["message"]}

    city_path = CITIES_DIR / f"{city}.json"
    if not city_path.exists():
        print(f"{prefix} ERROR: no city file {city}.json")
        return {"status": "error", "reason": "no-city-file"}

    data = json.loads(city_path.read_text())
    hit = find_garage(data, gid)
    if hit is None:
        print(f"{prefix} ERROR: garage_id not found in {city}")
        return {"status": "error", "reason": "id-not-found"}
    bucket, g = hit

    # Surface lot case — no URL/height needed
    if parsed["kind"] == "surface":
        g["structure_type"] = "surface_lot"
        g.setdefault("oversized", True)
        g["height_in"] = None
        g["height_label"] = "No limit (open-air)"
        existing = g.get("notes", "") or ""
        stamp = f"Manually stamped surface_lot on {date.today().isoformat()}."
        if stamp not in existing:
            g["notes"] = (existing + "\n" + stamp).strip()
        print(f"{prefix} surface_lot stamped")
        if not dry_run:
            city_path.write_text(json.dumps(data, indent=2) + "\n")
        return {"status": "applied", "kind": "surface", "city_path": city_path}

    # Remove case — delete the entry entirely.  Used when the location
    # has no parking at all (business closed, import was wrong, pin
    # in the wrong spot, etc.).  Git history preserves the data.
    if parsed["kind"] == "remove":
        removed = False
        for arr_name in ("garages", "tunnels", "bridges"):
            arr = data.get(arr_name, [])
            before = len(arr)
            data[arr_name] = [x for x in arr if x.get("id") != gid]
            if len(data[arr_name]) < before:
                removed = True
                break
        if removed:
            print(f"{prefix} REMOVED (no parking at this location)")
            if not dry_run:
                city_path.write_text(json.dumps(data, indent=2) + "\n")
            return {"status": "applied", "kind": "remove", "city_path": city_path}
        else:
            print(f"{prefix} ERROR: couldn't remove — id not found")
            return {"status": "error", "reason": "remove-id-not-found"}

    # Height cases — require URL
    if not url:
        print(f"{prefix} ERROR: URL required for height row")
        return {"status": "error", "reason": "no-url"}

    info = parse_maps_url(url)
    if not info["pano_id"]:
        print(f"{prefix} ERROR: couldn't extract pano_id from URL")
        return {"status": "error", "reason": "no-pano-in-url"}

    # Optional AI typo-catch
    ai_note = ""
    if verify_ai and parsed["kind"] == "single":
        img = fetch_pano_image(info["pano_id"], info["heading"] or 0)
        if img:
            claim_label = inches_to_label(parsed["height_in"])
            res = ai_verify_height(img, claim_label)
            if res and not res.get("matches_claim", False):
                ai_note = (f" ⚠ AI disagrees: read={res.get('read_in')}in "
                           f"raw={res.get('raw_text','')!r}")
                # Don't block the write -- human wins -- but flag it.
            elif res and res.get("matches_claim"):
                ai_note = " (AI ✓)"

    # Compute entrance coords from camera position + heading (push 10m in
    # the heading direction so bearing math works right downstream).
    entrance_lat = entrance_lng = None
    if info["cam_lat"] and info["cam_lng"] and info["heading"] is not None:
        import math
        h = math.radians(info["heading"])
        dlat = 10 * math.cos(h) / 111320.0
        dlng = 10 * math.sin(h) / (111320.0 * max(0.01, math.cos(math.radians(info["cam_lat"]))))
        entrance_lat = round(info["cam_lat"] + dlat, 6)
        entrance_lng = round(info["cam_lng"] + dlng, 6)

    today = date.today().isoformat()
    g["pano_id"] = info["pano_id"]
    g["pano_heading"] = round(info["heading"], 1) if info["heading"] is not None else None
    g["verified_on"] = today

    if entrance_lat is not None:
        g["entrance_lat"] = entrance_lat
        g["entrance_lng"] = entrance_lng
        g["entrance_source"] = "Manual (Google Maps URL)"

    if parsed["kind"] == "single":
        h_in = parsed["height_in"]
        g["height_in"] = h_in
        g["height_label"] = inches_to_label(h_in)
        g.pop("sections", None)
    elif parsed["kind"] == "sections":
        secs = parsed["sections"]
        # Sort so lowest is primary (strictest)
        secs_sorted = sorted(secs, key=lambda s: s["height_in"])
        g["height_in"] = secs_sorted[0]["height_in"]
        g["height_label"] = inches_to_label(secs_sorted[0]["height_in"])
        g["sections"] = [
            {"label": s.get("label") or f"Lane {i+1}",
             "height_in": s["height_in"],
             "height_label": inches_to_label(s["height_in"])}
            for i, s in enumerate(secs_sorted)
        ]

    prev_src = g.get("source", "") or ""
    if "Manually verified" not in prev_src:
        g["source"] = f"Manually verified from Google Street View \u2014 was: {prev_src}".strip(" -")

    prior_notes = g.get("notes", "") or ""
    stamp_parts = [
        f"Verified {today} from pano {info['pano_id']} @ {g['pano_heading']}°"
    ]
    if extra_notes:
        stamp_parts.append(extra_notes)
    stamp = " — ".join(stamp_parts)
    g["notes"] = (prior_notes + "\n" + stamp).strip()

    label_out = g["height_label"]
    secs_out = f" +{len(g.get('sections', []))-1} more section(s)" if g.get("sections") else ""
    print(f"{prefix} set {label_out}{secs_out} pano={info['pano_id'][:12]}…{ai_note}")

    if not dry_run:
        city_path.write_text(json.dumps(data, indent=2) + "\n")
    return {"status": "applied", "kind": parsed["kind"], "city_path": city_path}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Apply a TSV of Google Maps URL + height rows to city JSONs.")
    ap.add_argument("tsv", help="Path to the TSV file")
    ap.add_argument("--dry-run", action="store_true", help="Don't write JSON")
    ap.add_argument("--verify-ai", action="store_true",
                    help="Have Claude Vision cross-check each typed height against "
                         "the URL's pano image (~$0.003/row, catches typos)")
    ap.add_argument("--commit", action="store_true",
                    help="git add + commit modified city files after applying")
    ap.add_argument("--push", action="store_true", help="git push (implies --commit)")
    args = ap.parse_args()

    tsv_path = Path(args.tsv)
    if not tsv_path.exists():
        print(f"ERROR: {tsv_path} not found", file=sys.stderr)
        sys.exit(2)

    rows = read_tsv(tsv_path)
    print(f"bulk_update: {len(rows)} rows from {tsv_path}"
          f"{' [DRY-RUN]' if args.dry_run else ''}"
          f"{' [+AI cross-check]' if args.verify_ai else ''}")
    print()

    touched_paths = set()
    counts = {"applied": 0, "skipped": 0, "error": 0}
    for row in rows:
        r = apply_row(row, args.dry_run, args.verify_ai)
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        p = r.get("city_path")
        if p and r["status"] == "applied":
            touched_paths.add(p)

    print()
    print("=== SUMMARY ===")
    print(f"Rows applied:  {counts['applied']}")
    print(f"Rows skipped:  {counts['skipped']}")
    print(f"Rows errored:  {counts['error']}")
    print(f"City files touched: {len(touched_paths)}")
    if args.dry_run:
        print("(DRY-RUN — nothing written.)")
        return

    if (args.commit or args.push) and touched_paths:
        relative = [str(p.relative_to(REPO_ROOT)) for p in touched_paths]
        print()
        print(f"Committing {len(relative)} file(s)...")
        subprocess.run(["git", "-C", str(REPO_ROOT), "add", *relative], check=True)
        msg = (f"bulk_update: {counts['applied']} row(s) applied from "
               f"{tsv_path.name} on {date.today().isoformat()}")
        subprocess.run(["git", "-C", str(REPO_ROOT), "commit", "-m", msg], check=True)
        if args.push:
            print("Pushing...")
            subprocess.run(["git", "-C", str(REPO_ROOT), "push"], check=True)


if __name__ == "__main__":
    main()
