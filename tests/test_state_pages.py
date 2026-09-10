"""State hub pages (SEO/AEO plan, Task 2).  Run: python3 tests/test_state_pages.py -v"""
import html as html_lib
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import wf_common as wc                 # noqa: E402
import generate_state_pages as gsp     # noqa: E402


def load():
    idx = json.loads((REPO / "data/index.json").read_text())
    live = [c for c in idx if c.get("status") == "live"]
    data = {c["slug"]: json.loads((REPO / "data/cities" / f"{c['slug']}.json").read_text())
            for c in live if (REPO / "data/cities" / f"{c['slug']}.json").exists()}
    return live, data


LIVE, DATA = load()
CODES = sorted({c["state"] for c in LIVE})


def jsonld(page):
    return json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))


class StatePageTests(unittest.TestCase):
    def test_title_fallback(self):
        self.assertEqual(gsp.build_title("Nevada"), "Nevada Parking Garage &amp; Low Bridge Clearances | WillIFit.ai")
        self.assertEqual(gsp.build_title("District of Columbia"), "District of Columbia Clearance Heights | WillIFit.ai")
        for code in CODES:
            self.assertLessEqual(len(html_lib.unescape(gsp.build_title(wc.STATE_NAMES[code]))), 60, code)

    def test_every_state_renders_with_correct_urls_and_structure(self):
        for code in CODES:
            page = gsp.generate_state(code, LIVE, DATA)
            xx = code.lower()
            self.assertIn(f'<link rel="canonical" href="https://willifit.ai/state/{xx}">', page)
            self.assertIn(f'<meta property="og:url" content="https://willifit.ai/state/{xx}">', page)
            self.assertEqual(page.count("<h1"), 1, code)
            self.assertNotIn("/state/" + xx + ".html", page)
            types = [b["@type"] for b in jsonld(page)]
            self.assertEqual(types, ["CollectionPage", "BreadcrumbList", "ItemList", "FAQPage"], code)
            for c in LIVE:
                if c["state"] == code:
                    self.assertIn(f'href="/city/{c["slug"]}"', page, (code, c["slug"]))

    def test_head_never_claims_verification_the_data_lacks(self):
        for code in CODES:
            entries = [e for c in LIVE if c["state"] == code for k in ("garages", "tunnels", "bridges")
                       for e in DATA.get(c["slug"], {}).get(k, [])]
            ai = sum(1 for e in entries if "AI-verified" in (e.get("source") or ""))
            page = gsp.generate_state(code, LIVE, DATA)
            title = re.search(r"<title>(.*?)</title>", page).group(1)
            desc = re.search(r'name="description" content="([^"]*)"', page).group(1)
            lede = re.search(r'<p class="lede">(.*?)</p>', page, re.S).group(1)
            if ai == 0:
                for s in (title, desc, lede):
                    self.assertNotIn("AI-verified", s, code)
            else:
                self.assertIn(f"{ai} AI-verified from Street View signage", lede, code)

    def test_lowest_bridge_answer_matches_data(self):
        for code in ("NV", "OH", "MA"):
            entries = [e for c in LIVE if c["state"] == code for k in ("bridges", "tunnels")
                       for e in DATA.get(c["slug"], {}).get(k, [])]
            hs = [e["height_in"] for e in entries if isinstance(e.get("height_in"), (int, float)) and 72 <= e["height_in"] <= 168]
            page = gsp.generate_state(code, LIVE, DATA)
            faq = next(b for b in jsonld(page) if b["@type"] == "FAQPage")
            q = next(x for x in faq["mainEntity"] if x["name"].startswith("What is the lowest bridge"))
            self.assertIn(f"is {wc.inches_label(min(hs))} ({int(min(hs))} inches)", q["acceptedAnswer"]["text"])
            self.assertIn(wc.MEASURE_NOTE, q["acceptedAnswer"]["text"])

    def test_stats_use_posted_heights_only(self):
        cities = [{"slug": "t-xx", "name": "T", "state": "XX", "lat": 0, "lng": 0, "status": "live"}]
        data = {"t-xx": {"garages": [{"id": "a", "name": "A", "height_in": 70, "source": "Needs verification", "lat": 0, "lng": 0},
                                     {"id": "b", "name": "B", "height_in": 90, "source": "OpenStreetMap", "lat": 0, "lng": 0}],
                         "tunnels": [], "bridges": []}}
        wc.STATE_NAMES.setdefault("XX", "Testland")
        st = gsp.state_stats("XX", cities, data)
        self.assertEqual(st["lowest_garage"]["name"], "B")

    def test_city_with_no_data_file_is_excluded_from_state_rollup(self):
        cities = [
            {"slug": "has-data-yy", "name": "HasData", "state": "YY", "lat": 0, "lng": 0, "status": "live"},
            {"slug": "no-data-yy", "name": "NoData", "state": "YY", "lat": 0, "lng": 0, "status": "live"},
        ]
        data = {"has-data-yy": {"garages": [{"id": "a", "name": "A", "height_in": 90,
                                             "source": "OpenStreetMap", "lat": 0, "lng": 0}],
                                "tunnels": [], "bridges": []}}
        wc.STATE_NAMES.setdefault("YY", "Testland Two")

        st = gsp.state_stats("YY", cities, data)
        self.assertEqual(st["n_cities"], 1)
        self.assertEqual([c["slug"] for c in st["cities"]], ["has-data-yy"])

        page = gsp.generate_state("YY", cities, data)
        self.assertIn('href="/city/has-data-yy"', page)
        self.assertNotIn("no-data-yy", page)
        self.assertNotIn("NoData", page)

        il = next(b for b in jsonld(page) if b["@type"] == "ItemList")
        self.assertEqual(il["numberOfItems"], 1)
        self.assertEqual([i["url"] for i in il["itemListElement"]],
                         ["https://willifit.ai/city/has-data-yy"])

        faq = next(b for b in jsonld(page) if b["@type"] == "FAQPage")
        which = next(x for x in faq["mainEntity"] if x["name"].startswith("Which"))
        self.assertNotIn("NoData", which["acceptedAnswer"]["text"])
        self.assertIn("HasData", which["acceptedAnswer"]["text"])

    def test_every_state_description_ends_with_period(self):
        for code in CODES:
            page = gsp.generate_state(code, LIVE, DATA)
            desc = html_lib.unescape(re.search(r'name="description" content="([^"]*)"', page).group(1))
            self.assertTrue(desc.endswith("."), (code, desc))
            self.assertLessEqual(len(desc), 160, code)


class GeneratorRunTests(unittest.TestCase):
    def test_main_deletes_stale_state_file(self):
        """generate_state_pages.main() previously had no stale pass: a state
        that drops its last live city (or loses every city's data file) no
        longer appears in `codes`, but its old state/<xx>.html stayed on
        disk forever, advertised by generate_sitemap.py to an orphaned
        page."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "data/cities").mkdir(parents=True)
        city = {"slug": "t-xx", "name": "T", "state": "ZZ", "lat": 0, "lng": 0, "status": "live"}
        (tmp / "data/index.json").write_text(json.dumps([city]))
        (tmp / "data/cities/t-xx.json").write_text(json.dumps({
            "garages": [{"id": "a", "name": "A", "height_in": 90, "source": "OpenStreetMap",
                        "lat": 0, "lng": 0}],
            "tunnels": [], "bridges": []}))
        (tmp / "state").mkdir(parents=True)
        (tmp / "state/stale-code.html").write_text("stale")
        wc.STATE_NAMES.setdefault("ZZ", "Zedland")
        old = (gsp.REPO_ROOT, gsp.INDEX_PATH, gsp.CITIES_DIR, gsp.OUT_DIR)
        gsp.REPO_ROOT, gsp.INDEX_PATH, gsp.CITIES_DIR, gsp.OUT_DIR = tmp, tmp / "data/index.json", tmp / "data/cities", tmp / "state"
        try:
            gsp.main()
        finally:
            gsp.REPO_ROOT, gsp.INDEX_PATH, gsp.CITIES_DIR, gsp.OUT_DIR = old
        self.assertEqual(sorted(p.name for p in (tmp / "state").glob("*.html")), ["zz.html"])
        shutil.rmtree(tmp)


class WiringTests(unittest.TestCase):
    def test_netlify_rewrite_present(self):
        toml = (REPO / "netlify.toml").read_text()
        self.assertIn('from   = "/state/:code"', toml)
        self.assertIn('to     = "/state/:code.html"', toml)

    def test_generated_files_sitemap_cities_html_and_city_pages_link_states(self):
        sitemap = (REPO / "sitemap.xml").read_text()
        cities_html = (REPO / "cities.html").read_text()
        for code in CODES:
            xx = code.lower()
            self.assertTrue((REPO / "state" / f"{xx}.html").exists(), xx)
            self.assertIn(f"<loc>https://willifit.ai/state/{xx}</loc>", sitemap)
            self.assertIn(f'<h2 id="{xx}"><a href="/state/{xx}">', cities_html)
        lv = (REPO / "city/las-vegas-nv.html").read_text()
        self.assertIn('<a href="/state/nv" class="crumb">Nevada</a>', lv)
        self.assertIn("More cities in Nevada", lv)
        bc = next(b for b in jsonld(lv) if b["@type"] == "BreadcrumbList")
        self.assertEqual([i["item"] for i in bc["itemListElement"]],
                         ["https://willifit.ai/", "https://willifit.ai/cities.html",
                          "https://willifit.ai/state/nv", "https://willifit.ai/city/las-vegas-nv"])
        bridges = (REPO / "lowest-bridges-in-america.html").read_text()
        self.assertIn('<a href="/state/', bridges)


if __name__ == "__main__":
    unittest.main()
