"""Vehicle heights reference page (SEO/AEO plan, Task 4).
Run: python3 tests/test_vehicle_heights_page.py -v"""
import html as html_lib
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "vehicle-heights.html"

EXACT = {  # row id -> inches (data-in)
    "uhaul-pickup": 84, "uhaul-cargo-van": 96, "uhaul-10ft": 108, "uhaul-15ft": 132, "uhaul-17ft": 132,
    "uhaul-20ft": 132, "uhaul-26ft": 144, "budget-cargo-van": 105, "budget-12ft": 108, "budget-16ft": 132,
    "budget-26ft": 156, "penske-26ft": 162, "transit-low": 83, "transit-medium": 100, "transit-high": 110,
    "sprinter-standard": 100, "sprinter-high": 107, "promaster-low": 93, "express-2500": 85, "f150": 75, "tahoe": 76,
}
RANGES = {  # row id -> (min, max)
    "rv-class-a": (132, 162), "rv-class-b": (102, 132), "rv-class-c": (120, 132), "rv-travel-trailer": (84, 144),
    "rv-fifth-wheel": (120, 162), "semi": (162, 168),
}
UNPUBLISHED = {"penske-12ft", "penske-16ft", "penske-22ft", "penske-high-roof-van",
               "enterprise-16ft", "enterprise-26ft", "enterprise-cargo-van"}
SOURCE_DOMAINS = {"uhaul-26ft": "uhaul.com", "budget-26ft": "budgettruck.com", "penske-26ft": "pensketruckrental.com",
                  "transit-high": "ford.com", "sprinter-high": "mbvans.com", "promaster-low": "cars.com",
                  "rv-class-c": "rvshare.com", "semi": "ops.fhwa.dot.gov", "enterprise-26ft": "enterprisetrucks.com"}


def rows(page):
    out = {}
    for m in re.finditer(r'<tr id="([a-z0-9-]+)" data-in="(\d*)" data-in-min="(\d*)" data-in-max="(\d*)">(.*?)</tr>', page, re.S):
        out[m.group(1)] = {"in": m.group(2), "min": m.group(3), "max": m.group(4), "html": m.group(5)}
    return out


class VehicleHeightsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = PAGE.read_text()
        cls.rows = rows(cls.page)

    def test_head(self):
        title = html_lib.unescape(re.search(r"<title>(.*?)</title>", self.page).group(1))
        self.assertEqual(title, "How Tall Is a U-Haul, Box Truck, Van, or RV? | WillIFit.ai")
        self.assertLessEqual(len(title), 60)
        desc = re.search(r'name="description" content="([^"]*)"', self.page).group(1)
        self.assertLessEqual(len(html_lib.unescape(desc)), 160)
        self.assertIn('<link rel="canonical" href="https://willifit.ai/vehicle-heights.html">', self.page)
        self.assertEqual(self.page.count("<h1"), 1)
        self.assertIn('id="answer"', self.page)
        self.assertIn("Sources checked Sep 10, 2026", self.page)

    def test_no_duplicate_ids(self):
        ids = re.findall(r' id="([^"]+)"', self.page)
        self.assertEqual(sorted(set(ids)), sorted(ids), "duplicate id attributes")

    def test_jsonld(self):
        blocks = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', self.page, re.S).group(1))
        self.assertEqual([b["@type"] for b in blocks], ["Article", "BreadcrumbList", "FAQPage"])
        self.assertEqual(blocks[0]["speakable"]["cssSelector"], ["#answer"])
        self.assertEqual(blocks[0]["datePublished"], "2026-09-10")
        self.assertEqual(len(blocks[2]["mainEntity"]), 9)
        self.assertIn('id="will-it-fit"', self.page)
        for q in blocks[2]["mainEntity"]:   # every FAQ question is a visible <h3> on the page too
            self.assertIn(f"<h3>{html_lib.escape(q['name'], quote=False)}</h3>", self.page, q["name"])

    def test_every_expected_row_exists_with_the_sourced_number(self):
        for rid, inches in EXACT.items():
            self.assertIn(rid, self.rows, rid)
            self.assertEqual(self.rows[rid]["in"], str(inches), rid)
        for rid, (lo, hi) in RANGES.items():
            self.assertIn(rid, self.rows, rid)
            self.assertEqual((self.rows[rid]["min"], self.rows[rid]["max"]), (str(lo), str(hi)), rid)
        for rid in UNPUBLISHED:
            self.assertIn(rid, self.rows, rid)
            self.assertEqual(self.rows[rid]["in"], "", rid)
        self.assertEqual(set(self.rows), set(EXACT) | set(RANGES) | UNPUBLISHED)

    def test_fit_columns_follow_the_numbers(self):
        for rid, r in self.rows.items():
            cells = re.findall(r'<td class="(fit-yes|fit-no|fit-na)">([^<]+)</td>', r["html"])
            self.assertEqual(len(cells), 2, rid)
            if r["in"]:
                h = int(r["in"])
                self.assertEqual([c[1] for c in cells], ["Yes" if h <= 84 else "No", "Yes" if h <= 98 else "No"], rid)
            elif r["min"]:
                lo = int(r["min"])
                exp = ["No" if lo > 84 else "Only at the low end of the range", "No" if lo > 98 else "Only at the low end of the range"]
                self.assertEqual([c[1] for c in cells], exp, rid)
            else:
                self.assertEqual([c[1] for c in cells], ["Not published", "Not published"], rid)

    def test_every_row_links_its_source(self):
        for rid, r in self.rows.items():
            self.assertRegex(r["html"], r'<a href="https://[^"]+" rel="noopener" target="_blank">', rid)
        for rid, domain in SOURCE_DOMAINS.items():
            self.assertIn(domain, self.rows[rid]["html"], rid)

    def test_inbound_links_and_sitemap(self):
        self.assertIn('href="/vehicle-heights.html"', (REPO / "parking-garage-clearance-heights.html").read_text())
        idx = (REPO / "index.html").read_text()
        self.assertIn('<a class="vh-presets-link" href="/vehicle-heights.html">', idx)
        self.assertIn("<loc>https://willifit.ai/vehicle-heights.html</loc>", (REPO / "sitemap.xml").read_text())


if __name__ == "__main__":
    unittest.main()
