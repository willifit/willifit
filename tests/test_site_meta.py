"""Site-wide metadata, FAQ visibility, and footer invariants (SEO/AEO plan, Task 5).
Run: python3 tests/test_site_meta.py -v"""
import html as html_lib
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOT_PUBLIC = sorted(p for p in REPO.glob("*.html")
                     if p.name not in ("admin.html", "404.html", "advertise-thanks.html"))


def head(s):
    return s[:s.find("<body")]


def jsonld_blocks(s):
    out = []
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', s, re.S):
        j = json.loads(m.group(1))
        out += j if isinstance(j, list) else [j]
    return out


class RootPageMetaTests(unittest.TestCase):
    def test_titles_between_30_and_60_and_brand_spelled_WillIFit(self):
        for p in ROOT_PUBLIC:
            t = html_lib.unescape(re.search(r"<title>(.*?)</title>", p.read_text(), re.S).group(1).strip())
            self.assertTrue(30 <= len(t) <= 60, (p.name, len(t), t))
            self.assertNotIn("Willifit", t, p.name)

    def test_descriptions_at_most_160_and_end_with_a_period(self):
        for p in ROOT_PUBLIC:
            d = html_lib.unescape(re.search(r'name="description" content="([^"]*)"', p.read_text()).group(1))
            self.assertLessEqual(len(d), 160, p.name)
            self.assertTrue(d.endswith("."), (p.name, d))

    def test_no_stale_corpus_size_claims(self):
        for p in ROOT_PUBLIC:
            self.assertNotIn("25,000", p.read_text(), p.name)

    def test_twitter_card_on_every_root_page(self):
        for p in ROOT_PUBLIC:
            self.assertIn('name="twitter:card"', head(p.read_text()), p.name)

    def test_breadcrumbs_and_dates_on_root_pages(self):
        for p in ROOT_PUBLIC:
            if p.name == "index.html":
                continue
            blocks = jsonld_blocks(p.read_text())
            self.assertIn("BreadcrumbList", [b.get("@type") for b in blocks], p.name)
            dated = [b for b in blocks if b.get("@type") in ("WebPage", "Article", "AboutPage", "CollectionPage")
                     and b.get("dateModified")]
            self.assertTrue(dated, p.name)

    def test_how_ai_page_has_article_and_visible_faq(self):
        s = (REPO / "how-ai-verification-works.html").read_text()
        blocks = jsonld_blocks(s)
        self.assertIn("Article", [b["@type"] for b in blocks])
        faq = next(b for b in blocks if b["@type"] == "FAQPage")
        for q in faq["mainEntity"]:
            self.assertIn(f"<h3>{html_lib.escape(q['name'], quote=False)}</h3>", s, q["name"])
        self.assertNotIn("Willifit", s)

    def test_bridges_page_visible_faq_matches_jsonld(self):
        s = (REPO / "lowest-bridges-in-america.html").read_text()
        faq = next(b for b in jsonld_blocks(s) if b["@type"] == "FAQPage")
        for q in faq["mainEntity"]:
            self.assertIn(f"<h3>{html_lib.escape(q['name'], quote=False)}</h3>", s, q["name"])
        self.assertIn("not how tall or short the bridge itself is", s)

    def test_generated_descriptions_end_with_a_period(self):
        for p in sorted((REPO / "city").glob("*.html")) + sorted((REPO / "state").glob("*.html")) + sorted((REPO / "parking").glob("*/*.html"))[:50]:
            d = html_lib.unescape(re.search(r'name="description" content="([^"]*)"', p.read_text()).group(1))
            self.assertTrue(d.endswith(".") and len(d) <= 160, (p, d))

    def test_generated_faq_questions_are_headings(self):
        for f in ("city/las-vegas-nv.html", "state/nv.html"):
            self.assertIn('<summary class="faq-q"><h3>', (REPO / f).read_text(), f)

    def test_index_html_hints_dataset_and_presets(self):
        s = (REPO / "index.html").read_text()
        self.assertNotIn('rel="preconnect" href="https://a.basemaps.cartocdn.com"', s)
        self.assertIn('<link rel="preconnect" href="https://maps.googleapis.com">', s)
        ds = next(b for b in jsonld_blocks(s) if b.get("@type") == "Dataset")
        for k in ("variableMeasured", "temporalCoverage", "identifier"):
            self.assertIn(k, ds)
        org = next(b for b in jsonld_blocks(s) if b.get("@type") == "Organization")
        self.assertNotIn("sameAs", org)
        for need in (108, 132, 162):
            self.assertIn(f'data-in="{need}"', s)
        self.assertNotIn('data-in="126"', s)
        self.assertNotIn('data-in="150"', s)


class FooterTests(unittest.TestCase):
    def test_every_public_page_links_vehicle_heights(self):
        pages = (ROOT_PUBLIC + [REPO / "404.html", REPO / "advertise-thanks.html",
                                REPO / "city/las-vegas-nv.html", REPO / "city/akron-oh.html", REPO / "state/nv.html"]
                 + sorted((REPO / "parking").glob("*/*.html"))[:3])
        for p in pages:
            self.assertIn('href="/vehicle-heights.html"', p.read_text(), p)


if __name__ == "__main__":
    unittest.main()
