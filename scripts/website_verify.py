#!/usr/bin/env python3
"""
WillIFit — operator-website clearance verifier.

Why this exists:
  The Street View verifier (streetview_verify.py) only catches clearance
  signs visible from public roads.  That's a small minority of garages —
  most signs are mounted inside the garage, not viewable from the street.

  Operator websites are a much richer source.  A casino, stadium, mall, or
  venue that runs a parking garage often publishes the posted clearance on
  their website ("Self-Parking: 7'0 clearance").  This script fetches the
  operator's homepage + likely parking-related subpaths, hands the text to
  Claude, and extracts the clearance spec if present.

Pipeline for each garage:
  1. If the source field is a domain (e.g. "silverdollarcity.com") we try
     fetching:
       https://{domain}/
       https://{domain}/parking
       https://{domain}/getting-here
       https://{domain}/visit
       https://{domain}/directions
       https://{domain}/parking-info
       https://{domain}/parking.html
  2. Extract readable text from the HTML of each page that returns 200.
  3. Build one combined text blob (~4-8k chars, truncated per page).
  4. Send to Claude with a prompt asking for the vehicle-height clearance
     specific to THIS garage (so we don't pick up truck-route warnings etc).
  5. If Claude returns a confident height, commit it to the city JSON with
     source="AI-verified (operator website + Claude)".

Requires:
  pip install anthropic
  export ANTHROPIC_API_KEY=sk-ant-...

Usage:
  python3 scripts/website_verify.py --slug branson-mo --limit 5 --dry-run
  python3 scripts/website_verify.py --slug branson-mo
  python3 scripts/website_verify.py --all
  python3 scripts/website_verify.py --all --limit 50   # cap per city

Cost estimate:
  ~10-15k input tokens + 150 output tokens per garage at Claude Haiku rates
  (~$0.015/call).  Full pass across 1,850 unverified garages with domain
  sources: ~$28.  Per-city with --limit 5 for sampling: <$0.10.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib import parse, request, error

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"
LOG_PATH = REPO_ROOT / "data" / "website_verify.log"

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

DEFAULT_MODEL = "claude-haiku-4-5"
REQUEST_TIMEOUT = 12
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 willifit-verifier/1.0"
)

# Candidate paths we try on the operator's domain, in priority order.
# Homepage last so parking-specific pages rank higher when combined.
PATHS_TO_TRY = [
    "/parking",
    "/parking.html",
    "/parking-info",
    "/getting-here",
    "/visit",
    "/directions",
    "/visit/parking",
    "/hours-parking",
    "/plan-your-visit",
    "/",
]

MAX_PAGE_CHARS = 3500      # per page after text extraction
MAX_TOTAL_CHARS = 12000    # combined blob sent to Claude
CONF_RANK = {"low": 0, "medium": 1, "high": 2}
MIN_PLAUSIBLE_INCHES = 48
MAX_PLAUSIBLE_INCHES = 240

PROMPT_TEMPLATE = """You are reading the website of the operator of a specific parking garage, and
your job is to find the POSTED VEHICLE-HEIGHT CLEARANCE for that garage.

The garage we care about:
  Name:    {name}
  Address: {addr}
  City:    {city}, {state}

Website content extracted from the operator's domain (multiple pages
concatenated, most relevant first):

---BEGIN CONTENT---
{content}
---END CONTENT---

Task:
  Find the posted vehicle-height clearance for the parking garage above.
  Common patterns:
    * "Parking clearance: 6'8""
    * "Max vehicle height 7 feet"
    * "Clearance: 7'0" (2.13 m)"
    * "Oversized-vehicle parking available up to 12'"
  If the operator has multiple lots with different clearances, pick the
  one that best matches the garage name / address.  If there is an
  oversized/RV lot mentioned separately, note it but give the standard
  clearance as height_in.

IGNORE:
  * Trucking-route signage that's not about the garage ("low bridge
    ahead, 11'6 clearance" on a nearby road is NOT the garage clearance)
  * Weight limits (GVW, tonnage)
  * Navigation/driving directions unless they include the clearance
  * Any clearance that is obviously for a different garage or building

Return ONLY a single JSON object (no markdown fences, no prose before/
after) matching this schema:

{{
  "found_clearance": boolean,
  "height_in": integer | null,
  "confidence": "low" | "medium" | "high",
  "raw_quote": string,
  "oversized_ok": boolean | null,
  "notes": string
}}

- height_in is the standard-garage clearance, converted to inches
  (e.g. 6'8 = 80, 7'0 = 84).
- oversized_ok = true only if the operator explicitly states they accept
  oversized vehicles somewhere on the site.
- raw_quote is the snippet that told you the clearance (80 chars max).
- If the site has no parking clearance info, set found_clearance=false
  and everything else to null / low / "".
"""


# ---------------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------------

SKIP_TAGS = {"script", "style", "noscript", "head", "svg", "iframe"}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            s = data.strip()
            if s:
                self.parts.append(s)

    def get_text(self):
        return " ".join(self.parts)


def extract_text(html: str, limit: int = MAX_PAGE_CHARS) -> str:
    ex = TextExtractor()
    try:
        ex.feed(html)
    except Exception:
        return ""
    text = ex.get_text()
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch_text(url: str) -> Optional[str]:
    """Fetch URL, return decoded HTML string or None on error."""
    req = request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            ct = resp.headers.get("Content-Type", "").lower()
            if "html" not in ct and "text" not in ct:
                return None
            raw = resp.read(500_000)  # cap at 500KB
            try:
                return raw.decode("utf-8", errors="replace")
            except Exception:
                return raw.decode("latin-1", errors="replace")
    except (error.HTTPError, error.URLError, TimeoutError, OSError):
        return None


def normalize_domain(s: str) -> Optional[str]:
    s = (s or "").strip().lower()
    if not s:
        return None
    # Strip leading protocol + trailing slash/path — we want just the bare domain
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0]
    if not re.match(r"^[a-z0-9][a-z0-9\-\.]*\.[a-z]{2,}$", s):
        return None
    return s


# Domain tokens we ignore when extracting from a source string — they're
# provenance stamps (our own scripts) rather than operator websites.
_NON_OPERATOR_DOMAINS = {
    "google.com", "googlemaps.com", "openstreetmap.org", "fhwa.dot.gov",
}


def extract_source_domain(src: str) -> Optional[str]:
    """Find an operator domain in the `source` field.  Handles the common
    'Manually verified ... — was: <domain>' and 'AI-verified ... — was:
    <domain>' chains our other scripts write, walking right-to-left so the
    most-original import source wins (that's the operator website, not
    our own provenance stamp)."""
    if not src:
        return None
    # Split on the em-dash-was / dash-was separator (both variants)
    parts = re.split(r"\s*[—\-]+\s*was:\s*", src)
    # Try most-original first (last element); fall back to earlier segments
    for part in reversed(parts):
        # A segment may itself contain multiple tokens; test each word-ish chunk
        for token in re.split(r"[\s,;|]+", part):
            d = normalize_domain(token)
            if d and d not in _NON_OPERATOR_DOMAINS:
                return d
    return None


def collect_site_text(domain: str) -> tuple[str, list[str]]:
    """Fetch the homepage + a few parking-related subpaths, return a combined
    text blob and the list of URLs we successfully fetched."""
    fetched = []
    chunks = []
    total = 0
    for path in PATHS_TO_TRY:
        if total >= MAX_TOTAL_CHARS:
            break
        url = f"https://{domain}{path}"
        html = fetch_text(url)
        if html is None:
            continue
        text = extract_text(html, MAX_PAGE_CHARS)
        if not text:
            continue
        budget = MAX_TOTAL_CHARS - total
        if budget <= 0:
            break
        chunk_body = text[:budget]
        chunks.append(f"[{url}]\n{chunk_body}")
        total += len(chunk_body)
        fetched.append(url)
    return "\n\n".join(chunks), fetched


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def _anthropic_client():
    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("ERROR: anthropic SDK not installed. Run:  pip install anthropic",
              file=sys.stderr)
        sys.exit(2)
    import anthropic
    if not ANTHROPIC_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(2)
    return anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def verify_with_claude(prompt: str, model: str) -> Optional[dict]:
    client = _anthropic_client()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        print(f"    Claude API error: {e}", file=sys.stderr)
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
        print(f"    Claude replied non-JSON: {text[:200]!r}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Per-garage flow
# ---------------------------------------------------------------------------

def inches_to_label(inches: Optional[int]) -> Optional[str]:
    if inches is None:
        return None
    ft = inches // 12
    rem = inches % 12
    return f"{ft}'{rem}\""


def verify_garage(g: dict, city: dict, model: str) -> Optional[dict]:
    src = g.get("source") or ""
    # Preferred: the `source` field itself is a bare domain (original import
    # format).  Fallback: walk '— was: <domain>' chains to find an operator
    # domain that our own provenance stamps may have buried.
    domain = normalize_domain(src) or extract_source_domain(src)
    if not domain:
        return {"status": "no-domain"}

    content, fetched = collect_site_text(domain)
    if not content:
        return {"status": "site-unreachable", "domain": domain}

    prompt = PROMPT_TEMPLATE.format(
        name=g.get("name", ""),
        addr=g.get("addr", ""),
        city=city.get("name", ""),
        state=city.get("state", ""),
        content=content,
    )

    claude_result = verify_with_claude(prompt, model)
    if not claude_result:
        return {"status": "claude-parse-fail", "domain": domain}

    claude_result["status"] = "ok"
    claude_result["domain"] = domain
    claude_result["urls_fetched"] = fetched
    return claude_result


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
    check_only: bool = False,
) -> dict:
    city_path = CITIES_DIR / f"{slug}.json"
    if not city_path.exists():
        return {"slug": slug, "error": "no city file"}

    data = json.loads(city_path.read_text())
    garages = data.get("garages", [])
    idx = json.loads(INDEX_PATH.read_text())
    city_meta = next((c for c in idx if c["slug"] == slug), {"slug": slug, "name": slug, "state": ""})

    # --check mode inverts the selection: process entries that ALREADY have
    # a stored height_in, cross-reference against the operator's website,
    # NEVER write.  Useful as a 3rd independent source (street view + user
    # eye + operator's own posted spec).
    if check_only:
        candidates = [g for g in garages if g.get("height_in") is not None]
    elif overwrite:
        candidates = list(garages)
    else:
        candidates = [g for g in garages if g.get("height_in") is None]

    if not candidates:
        print(f"[{slug}] no candidates")
        return {"slug": slug, "checked": 0, "updated": 0, "mismatches": []}

    mode_word = "to re-check" if check_only else "candidates"
    print(f"[{slug}] {len(candidates)} {mode_word} (limit={limit})"
          f"{' [CHECK MODE — no writes]' if check_only else ''}")

    checked = updated = no_domain = unreachable = no_clearance = low_conf = 0
    mismatches = []

    for g in candidates:
        if limit is not None and checked >= limit:
            break
        checked += 1
        tag = f"  {g.get('id','?'):<28} {g.get('name','?')[:40]:<40}"
        print(tag, end=" ", flush=True)

        res = verify_garage(g, city_meta, model)
        if res is None or res.get("status") == "no-domain":
            no_domain += 1
            print("no operator domain to cross-ref")
            continue

        if res.get("status") == "site-unreachable":
            unreachable += 1
            print(f"unreachable ({res.get('domain')})")
            _log(f"{date.today()} {slug} {g['id']} UNREACHABLE {res.get('domain')}")
            continue

        if res.get("status") != "ok":
            print(f"error: {res.get('status')}")
            continue

        found = bool(res.get("found_clearance"))
        height_in = res.get("height_in")
        conf = (res.get("confidence") or "low").lower()
        quote = (res.get("raw_quote") or "")[:80]
        domain = res.get("domain")

        if not found or height_in is None:
            no_clearance += 1
            print(f"no clearance found on {domain}")
            _log(f"{date.today()} {slug} {g['id']} NO-CLEARANCE conf={conf} domain={domain}")
            continue

        if CONF_RANK.get(conf, -1) < CONF_RANK[min_conf]:
            low_conf += 1
            print(f"low conf ({conf}): {height_in}in \"{quote}\" on {domain}")
            _log(f"{date.today()} {slug} {g['id']} LOW-CONF conf={conf} h={height_in} quote={quote!r}")
            continue

        if not (MIN_PLAUSIBLE_INCHES <= int(height_in) <= MAX_PLAUSIBLE_INCHES):
            print(f"implausible {height_in}in — discarding")
            continue

        # CHECK-MODE: cross-reference against stored value, don't write.
        if check_only:
            stored_h = g.get("height_in")
            if stored_h is None:
                tag_ = "AI-FOUND-NEW"
                marker = "✨"
            elif abs(int(height_in) - stored_h) == 0:
                tag_ = "MATCH"
                marker = "✓"
            elif abs(int(height_in) - stored_h) <= 1:
                tag_ = "CLOSE"
                marker = "~"
            else:
                tag_ = "MISMATCH"
                marker = "⚠"

            print(f"{marker} {tag_}: stored={stored_h}in site={height_in}in "
                  f"({conf}) on {domain} \"{quote[:40]}\"")
            _log(f"{date.today()} {slug} {g['id']} WEB-{tag_} stored={stored_h} "
                 f"web={height_in} conf={conf} domain={domain} quote={quote!r}")

            if tag_ == "MISMATCH":
                mismatches.append({
                    "id": g.get("id"),
                    "name": g.get("name"),
                    "stored_h": stored_h,
                    "site_h": int(height_in),
                    "site_raw_quote": quote,
                    "site_confidence": conf,
                    "domain": domain,
                })
            continue

        # NORMAL mode — commit the reading
        if not dry_run:
            g["height_in"] = int(height_in)
            g["height_label"] = inches_to_label(int(height_in))
            g["verified_on"] = date.today().isoformat()
            prev_src = g.get("source", "")
            if "AI-verified" not in prev_src:
                g["source"] = f"AI-verified (operator website + Claude) — was: {prev_src}".strip(" -")
            prior_notes = g.get("notes", "")
            stamp = f'Verified {date.today().isoformat()} from {domain}: "{quote}"'
            g["notes"] = (prior_notes + "\n" + stamp).strip() if prior_notes else stamp
            if res.get("oversized_ok") is not None:
                g["oversized"] = bool(res["oversized_ok"])

        updated += 1
        action = "[DRY] would set" if dry_run else "UPDATED"
        print(f"{action}: {height_in}in ({inches_to_label(int(height_in))}) conf={conf} \"{quote}\"")
        _log(f"{date.today()} {slug} {g['id']} VERIFIED h={height_in} conf={conf} domain={domain} quote={quote!r}")

    if updated and not dry_run and not check_only:
        city_path.write_text(json.dumps(data, indent=2) + "\n")

    summary_parts = [
        f"checked={checked}",
        f"matches+writes={updated}" if not check_only else f"findings={updated}",
        f"no-domain={no_domain}",
        f"unreachable={unreachable}",
        f"no-clearance={no_clearance}",
        f"low-conf={low_conf}",
    ]
    if check_only:
        summary_parts.append(f"mismatches={len(mismatches)}")
    print(f"  {slug}: " + " ".join(summary_parts))
    return {
        "slug": slug, "checked": checked, "updated": updated,
        "no_domain": no_domain, "unreachable": unreachable,
        "no_clearance": no_clearance, "low_conf": low_conf,
        "mismatches": mismatches,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Verify garage clearances from operator websites.")
    ap.add_argument("--slug", help="Single city slug")
    ap.add_argument("--slugs", help="Comma-separated slugs")
    ap.add_argument("--all", action="store_true", help="All live cities")
    ap.add_argument("--limit", type=int, default=None, help="Max garages per city")
    ap.add_argument("--confidence", choices=["low", "medium", "high"], default="high",
                    help="Minimum Claude confidence to accept a reading (default: high)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model (default: {DEFAULT_MODEL})")
    ap.add_argument("--overwrite", action="store_true", help="Re-verify ALL garages")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="Cross-reference already-verified entries against the "
                         "operator's website WITHOUT writing.  Reports MATCH / "
                         "CLOSE / MISMATCH for each garage whose source has a "
                         "reachable domain.")
    ap.add_argument("--sleep", type=float, default=0.5, help="Seconds between garages")
    args = ap.parse_args()

    if not (args.slug or args.slugs or args.all):
        ap.error("pass --slug, --slugs, or --all")

    idx = json.loads(INDEX_PATH.read_text())
    if args.slug:
        targets = [args.slug]
    elif args.slugs:
        targets = [s.strip() for s in args.slugs.split(",") if s.strip()]
    else:
        targets = [c["slug"] for c in idx if c.get("status") == "live"]

    mode = "CHECK" if args.check else ("DRY-RUN" if args.dry_run else "LIVE")
    summary = []
    for slug in targets:
        try:
            r = process_city(slug, args.limit, args.confidence, args.model,
                             args.dry_run, args.overwrite, check_only=args.check)
            summary.append(r)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            break
        time.sleep(args.sleep)

    total_checked = sum(s.get("checked", 0) for s in summary)
    total_updated = sum(s.get("updated", 0) for s in summary)
    all_mismatches = [m for s in summary for m in (s.get("mismatches") or [])]

    print(f"\n=== SUMMARY [{mode}] ===")
    print(f"Cities processed: {len(summary)}")
    print(f"Garages checked:  {total_checked}")
    if args.check:
        print(f"MISMATCHES needing review: {len(all_mismatches)}")
    else:
        print(f"Updated:          {total_updated}")

    if all_mismatches:
        print()
        print("=== WEBSITE vs STORED MISMATCHES ===")
        for m in all_mismatches:
            print(f"  {m['id']:<38} stored={m['stored_h']}in  site={m['site_h']}in  "
                  f"({m['site_confidence']}) on {m['domain']}")
            if m.get("site_raw_quote"):
                print(f"    website said: \"{m['site_raw_quote'][:80]}\"")

    if args.dry_run:
        print("(DRY-RUN — no files written.)")


if __name__ == "__main__":
    main()
