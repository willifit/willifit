#!/usr/bin/env python3
"""IndexNow submitter for willifit.ai.

What it does:
  Pings the IndexNow API to tell Bing, Yandex, Naver, Seznam, and
  Yep about new or updated URLs.  IndexNow is a write-only push
  protocol (no auth, no account, no quota) -- the only thing it
  requires is a key file at the site root proving ownership.

How ownership works:
  The key (a 32-char hex string) is committed to the repo as
  /<key>.txt at the site root.  When IndexNow gets a submission,
  it fetches that file; if the contents match the submitted key,
  the submission is accepted.  See IndexNow spec section 2.4:
  https://www.indexnow.org/documentation

Usage:
  # Single URL
  python3 scripts/indexnow.py https://willifit.ai/city/las-vegas-nv

  # Multiple URLs (up to 10,000 per call)
  python3 scripts/indexnow.py url1 url2 url3 ...

  # All URLs in sitemap.xml
  python3 scripts/indexnow.py --sitemap

  # Just the homepage + city pages (skip legal/about)
  python3 scripts/indexnow.py --cities-only

When to call this:
  After meaningful data updates -- height verifications, new entries,
  schema changes, or page-level edits.  IndexNow's official guidance
  is "submit on every notable change"; spamming is fine because the
  protocol expects you to push, not pull.

Endpoint chosen:
  api.indexnow.org -- the multi-engine endpoint.  Submitting to the
  Bing or Yandex single-engine endpoints would limit the broadcast.
  api.indexnow.org fans out to every member engine.
"""
import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error

KEY = "c2c39e082b8e29e05a38e1524ae86b70"
HOST = "willifit.ai"
KEY_LOCATION = f"https://{HOST}/{KEY}.txt"
ENDPOINT = "https://api.indexnow.org/IndexNow"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITEMAP = os.path.join(ROOT, "sitemap.xml")


def submit(urls: list[str]) -> tuple[int, str]:
    """Submit a batch of URLs to IndexNow.  Returns (status_code, body)."""
    if not urls:
        return 200, "no urls"
    if len(urls) > 10000:
        raise ValueError("IndexNow caps batches at 10,000 URLs")

    payload = {
        "host": HOST,
        "key": KEY,
        "keyLocation": KEY_LOCATION,
        "urlList": urls,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def parse_sitemap() -> list[str]:
    if not os.path.exists(SITEMAP):
        print(f"sitemap not found at {SITEMAP}", file=sys.stderr)
        sys.exit(1)
    with open(SITEMAP) as f:
        text = f.read()
    return re.findall(r"<loc>([^<]+)</loc>", text)


def main():
    ap = argparse.ArgumentParser(description="Submit URLs to IndexNow.")
    ap.add_argument("urls", nargs="*", help="URLs to submit")
    ap.add_argument("--sitemap", action="store_true",
                    help="Submit every URL in sitemap.xml")
    ap.add_argument("--cities-only", action="store_true",
                    help="Submit only homepage + /city/ URLs")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be submitted")
    args = ap.parse_args()

    urls = list(args.urls)
    if args.sitemap or args.cities_only:
        all_urls = parse_sitemap()
        if args.cities_only:
            urls += [u for u in all_urls if u == f"https://{HOST}/" or "/city/" in u]
        else:
            urls += all_urls

    if not urls:
        ap.error("pass URLs, --sitemap, or --cities-only")

    # de-dupe while preserving order
    seen = set()
    deduped = [u for u in urls if not (u in seen or seen.add(u))]

    print(f"Submitting {len(deduped)} URL(s) to {ENDPOINT}")
    print(f"  host:        {HOST}")
    print(f"  keyLocation: {KEY_LOCATION}")
    print(f"  first 3:     {deduped[:3]}")
    if args.dry_run:
        print("(dry-run, no submit)")
        return

    status, body = submit(deduped)
    print(f"  status: {status}")
    if body.strip():
        print(f"  body:   {body[:500]}")

    # IndexNow returns 200 OK on success.  202 = accepted but key
    # not yet verified (will retry).  Anything else is a real error.
    if status == 200:
        print("OK -- engines will fetch the URLs over the next few hours.")
    elif status == 202:
        print("Accepted -- IndexNow will verify the key file shortly.")
    else:
        print("FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
