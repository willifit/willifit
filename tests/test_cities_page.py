"""cities.html generator (SEO/AEO plan, Task 5).  Run: python3 tests/test_cities_page.py -v"""
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import generate_cities_page as gcp  # noqa: E402
import generate_llms_txt as gl  # noqa: E402


def live_cities_with_locations():
    idx = json.loads((REPO / "data/index.json").read_text())
    live = [c for c in idx if c.get("status") == "live"]
    out = []
    for c in live:
        p = REPO / "data/cities" / f"{c['slug']}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        total = (len(d.get("garages") or []) + len(d.get("tunnels") or [])
                 + len(d.get("bridges") or []))
        if total == 0:
            continue
        out.append((c, total))
    return out


class CitiesPageTests(unittest.TestCase):
    def test_committed_file_matches_generator(self):
        self.assertEqual((REPO / "cities.html").read_text(), gcp.render())

    def test_every_live_city_with_locations_appears_once_with_true_count(self):
        text = (REPO / "cities.html").read_text()
        for c, total in live_cities_with_locations():
            pattern = (rf'<a href="/city/{re.escape(c["slug"])}">[^<]*</a>'
                       rf' <span class="count">\((\d+)\)</span>')
            matches = re.findall(pattern, text)
            self.assertEqual(len(matches), 1, c["slug"])
            self.assertEqual(int(matches[0]), total, c["slug"])

    def test_stats_total_equals_sum_over_data_files(self):
        text = (REPO / "cities.html").read_text()
        total = sum(t for _, t in live_cities_with_locations())
        m = re.search(r'<div class="stat"><b>([\d,]+)</b> locations</div>', text)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1).replace(",", "")), total)

    def test_no_stale_corpus_size_claim(self):
        text = (REPO / "cities.html").read_text()
        self.assertNotIn("25,000", text)

    def test_description_contains_true_location_count(self):
        text = (REPO / "cities.html").read_text()
        total = sum(t for _, t in live_cities_with_locations())
        m = re.search(r'name="description" content="([^"]*)"', text)
        self.assertIsNotNone(m)
        self.assertIn(f"{total:,} locations", m.group(1))

    def test_collection_page_datemodified_equals_corpus_latest_verified(self):
        text = (REPO / "cities.html").read_text()
        blocks = json.loads(re.search(
            r'<script type="application/ld\+json">(.*?)</script>', text, re.S).group(1))
        collection = next(b for b in blocks if b.get("@type") == "CollectionPage")
        self.assertEqual(collection["dateModified"], gl.corpus_stats()["latest_verified"])


if __name__ == "__main__":
    unittest.main()
