#!/usr/bin/env python3
"""WillIFit — sitemap.xml generator.

Why this exists:
  sitemap.xml used to be maintained by hand, so its <lastmod> dates drifted
  stale (every URL stuck on the date someone last touched the file by hand,
  regardless of whether the page actually changed).  Stale lastmods train
  crawlers to ignore the field, and a wrong "fresh" date wastes crawl budget.

What it does:
  Rebuilds sitemap.xml from the source of truth:
    - the static top-level pages (home, cities index, legal, etc.)
    - every *live* city that has at least one indexed location

  <lastmod> for each URL is the git commit date of the file that actually
  backs that URL (index.html, city/<slug>.html, ...).  If the file has
  uncommitted local edits (or isn't tracked yet) it's stamped with today's
  date, since it's about to change.  That keeps lastmod honest: it moves
  only when the underlying page really moved.

Consistency with the per-city generator:
  generate_city_pages.py marks a 0-location city page <meta robots>
  "noindex,follow".  This script drops those same cities from the sitemap,
  so we never advertise a URL we're asking crawlers not to index.

Run after data changes / page regen:
    python3 scripts/generate_sitemap.py
    # then push the new URLs to Bing/Yandex/etc:
    python3 scripts/indexnow.py --sitemap
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "index.json"
CITIES_DIR = REPO_ROOT / "data" / "cities"
OUT_PATH = REPO_ROOT / "sitemap.xml"

SITE = "https://willifit.ai"
TODAY = date.today().isoformat()

# Static, hand-curated top-level URLs, in the order they should appear.
# (path, file_backing_the_url, changefreq, priority)
# NOTE: admin.html (robots-disallowed) and advertise-thanks.html (a
# post-submit confirmation page) are intentionally excluded.
STATIC_PAGES = [
    ("",                              "index.html",                      "weekly",  "1.0"),
    ("how-ai-verification-works.html", "how-ai-verification-works.html", "monthly", "0.7"),
    ("advertise.html",                "advertise.html",                  "monthly", "0.7"),
    ("disclaimer.html",               "disclaimer.html",                 "monthly", "0.3"),
    ("terms.html",                    "terms.html",                      "monthly", "0.3"),
    ("privacy.html",                  "privacy.html",                    "monthly", "0.3"),
    ("dmca.html",                     "dmca.html",                       "monthly", "0.3"),
    ("cities.html",                   "cities.html",                     "weekly",  "0.8"),
]


def _git(*args) -> str:
    """Run a git command in the repo; return stripped stdout, or '' on error."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def lastmod_for(relpath: str) -> str:
    """Git commit date (YYYY-MM-DD) of the file backing a URL.

    Falls back to today's date when the file has uncommitted edits or is
    untracked — in both cases the page content is newer than its last
    commit, so 'today' is the honest answer."""
    # Porcelain output is non-empty iff the path is modified/untracked.
    if _git("status", "--porcelain", "--", relpath):
        return TODAY
    committed = _git("log", "-1", "--format=%cs", "--", relpath)
    return committed or TODAY


def city_total(slug: str) -> int | None:
    """Total indexed locations for a city, or None if it has no data file."""
    data_path = CITIES_DIR / f"{slug}.json"
    if not data_path.exists():
        return None
    data = json.loads(data_path.read_text())
    return (len(data.get("garages") or [])
            + len(data.get("tunnels") or [])
            + len(data.get("bridges") or []))


def url_block(loc_path: str, lastmod: str, changefreq: str, priority: str) -> str:
    loc = f"{SITE}/{loc_path}" if loc_path else f"{SITE}/"
    return (f"  <url><loc>{loc}</loc><lastmod>{lastmod}</lastmod>"
            f"<changefreq>{changefreq}</changefreq>"
            f"<priority>{priority}</priority></url>")


def main() -> None:
    idx = json.loads(INDEX_PATH.read_text())
    live = [c for c in idx if c.get("status") == "live"]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    for loc_path, backing_file, changefreq, priority in STATIC_PAGES:
        lines.append(url_block(loc_path, lastmod_for(backing_file), changefreq, priority))

    included = 0
    skipped_empty = 0
    skipped_nodata = 0
    for city in sorted(live, key=lambda c: c["slug"]):
        slug = city["slug"]
        total = city_total(slug)
        if total is None:
            skipped_nodata += 1
            continue
        if total == 0:
            # Mirrors the noindex on the generated page — don't advertise a
            # URL we're asking crawlers not to index.
            skipped_empty += 1
            continue
        lines.append(url_block(
            f"city/{slug}",
            lastmod_for(f"city/{slug}.html"),
            "weekly",
            "0.8",
        ))
        included += 1

    lines.append("</urlset>")
    OUT_PATH.write_text("\n".join(lines) + "\n")

    print(f"Wrote {OUT_PATH}")
    print(f"  static pages: {len(STATIC_PAGES)}")
    print(f"  cities included: {included}")
    print(f"  skipped (0 locations, noindex): {skipped_empty}")
    print(f"  skipped (no data file): {skipped_nodata}")
    print(f"  total URLs: {len(STATIC_PAGES) + included}")


if __name__ == "__main__":
    main()
