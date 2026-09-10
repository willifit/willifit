"""Per-garage pages (SEO/AEO plan, Task 3).  Run: python3 tests/test_location_pages.py -v"""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import wf_common as wc                     # noqa: E402
import generate_city_pages as gen           # noqa: E402
import generate_location_pages as glp       # noqa: E402

CITY = {"slug": "test-xx", "name": "Testville", "state": "NV", "lat": 36.1, "lng": -115.1, "status": "live"}


def garage(id_, name, height_in, **kw):
    e = {"id": id_, "name": name, "addr": "", "lat": 36.1, "lng": -115.1, "height_in": height_in,
         "height_label": wc.inches_label(height_in) if height_in else None, "oversized": False,
         "notes": "", "source": "OpenStreetMap"}
    e.update(kw)
    return e


def jsonld(page):
    return json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))


class EligibilityAndSlugTests(unittest.TestCase):
    def test_eligible(self):
        self.assertTrue(glp.eligible(garage("a", "Aria Resort & Casino", 99)))
        self.assertFalse(glp.eligible(garage("a", "Parking Deck", 99)))
        self.assertFalse(glp.eligible(garage("a", "Lot 26", 99)))
        self.assertFalse(glp.eligible(garage("a", "Unnamed parking structure", 99)))
        self.assertFalse(glp.eligible(garage("a", "Aria", None)))
        self.assertFalse(glp.eligible(garage("a", "Aria", 99, source="Needs verification")))

    def test_eligible_requires_numeric_coordinates(self):
        self.assertFalse(glp.eligible(garage("a", "Aria Resort & Casino", 99, lat=None)))
        self.assertFalse(glp.eligible(garage("a", "Aria Resort & Casino", 99, lng=None)))
        self.assertTrue(glp.eligible(garage("a", "Aria Resort & Casino", 99, lat=36.1, lng=-115.1)))

    def test_paths_are_deterministic_and_unique(self):
        gs = [garage("z", "Red Deck", 90), garage("b", "Aria Resort & Casino", 99),
              garage("a", "Red Deck", 80), garage("c", "Parking Deck", 70)]
        self.assertEqual(glp.location_paths("test-xx", gs),
                         ["/parking/test-xx/red-deck-2", "/parking/test-xx/aria-resort-and-casino",
                          "/parking/test-xx/red-deck", None])


class PageTests(unittest.TestCase):
    def render(self, g, others=()):
        gs = [g] + list(others)
        paths = glp.location_paths(CITY["slug"], gs)
        anchors = gen.assign_anchors(gs)
        return glp.generate_location(CITY, g, paths[0], gs, paths, anchors[0])

    def test_answer_fit_table_and_faq_are_truthful_for_8ft3(self):
        page = self.render(garage("aria", "Aria Resort & Casino", 99, addr="3730 S Las Vegas Blvd",
                                  source="MGM Resorts", website="https://aria.mgmresorts.com/"))
        self.assertIn('<link rel="canonical" href="https://willifit.ai/parking/test-xx/aria-resort-and-casino">', page)
        self.assertEqual(page.count("<h1"), 1)
        self.assertIn("<h1>Aria Resort &amp; Casino parking clearance: 8&#x27;3&quot;</h1>", page)
        self.assertIn("is 8&#x27;3&quot; (99 inches). The figure comes from MGM Resorts and has not been individually re-verified.", page)
        self.assertIn(wc.esc(wc.fit_phrase(99)), page)
        rows = re.findall(r'<tr><td>([^<]+)</td><td>([^<]+)</td><td class="fit-(yes|no)">', page)
        self.assertEqual([r[2] for r in rows], ["yes", "yes", "yes", "no", "no", "no", "no", "no", "no", "no", "no"])
        faq = next(b for b in jsonld(page) if b["@type"] == "FAQPage")
        uhaul = faq["mainEntity"][0]["acceptedAnswer"]["text"]
        self.assertTrue(uhaul.startswith("No. The posted clearance is 8'3\". U-Haul lists a clearance height of 9'0\" for its 10 ft truck, 11'0\" for its 15, 17, and 20 ft trucks, and 12'0\" for its 26 ft truck. None of them fits under 8'3\"."), uhaul)
        self.assertIn("willifit.ai/vehicle-heights.html", uhaul)
        rv = faq["mainEntity"][1]["acceptedAnswer"]["text"]
        self.assertTrue(rv.startswith("Class B camper vans are typically 8.5 to 11 ft tall and Class C motorhomes 10 to 11 ft, not counting roof-mounted air conditioners and vents. Neither a Class B camper van nor a Class C motorhome fits under 8'3\"."), rv)
        pf = next(b for b in jsonld(page) if b["@type"] == "ParkingFacility")
        self.assertEqual(pf["additionalProperty"][0]["value"], 99)
        self.assertEqual(pf["sameAs"], ["https://aria.mgmresorts.com/"])
        self.assertEqual(pf["address"]["streetAddress"], "3730 S Las Vegas Blvd")
        bc = next(b for b in jsonld(page) if b["@type"] == "BreadcrumbList")
        self.assertEqual(len(bc["itemListElement"]), 5)
        self.assertNotIn("WebPage", [b["@type"] for b in jsonld(page)])   # no verified_on -> no dateModified block

    def test_uhaul_edge_cases_and_verified_entry(self):
        page = self.render(garage("g", "Grand Garage", 126, source="AI-verified (Street View + Claude Vision)",
                                  verified_on="2026-08-01", pano_id="abc", pano_heading=74))
        faq = next(b for b in jsonld(page) if b["@type"] == "FAQPage")
        uhaul = faq["mainEntity"][0]["acceptedAnswer"]["text"]
        self.assertTrue(uhaul.startswith("Only the smaller trucks. The posted clearance is 10'6\"."), uhaul)
        self.assertIn("At 10'6\", only the 10 ft truck fits.", uhaul)
        self.assertIn("A Class B camper van fits only if it measures under 10'6\" including roof equipment; a Class C motorhome does not.",
                      faq["mainEntity"][1]["acceptedAnswer"]["text"])
        self.assertIn("It was AI-verified from Street View signage on Aug 1, 2026. Open the Street View link above", faq["mainEntity"][2]["acceptedAnswer"]["text"])
        self.assertIn("pano=abc&amp;heading=74", page)
        wp = next(b for b in jsonld(page) if b["@type"] == "WebPage")
        self.assertEqual(wp["dateModified"], "2026-08-01")
        page2 = self.render(garage("g", "Grand Garage", 144))
        faq2 = next(b for b in jsonld(page2) if b["@type"] == "FAQPage")
        uhaul2 = faq2["mainEntity"][0]["acceptedAnswer"]["text"]
        self.assertTrue(uhaul2.startswith("Yes, for U-Haul. The posted clearance is 12'0\"."), uhaul2)
        self.assertIn("All three sizes fit at 12'0\".", uhaul2)
        self.assertIn("The fit table above uses 13'6\", the tallest published 26 ft rental truck "
                      "(Penske); U-Haul's is 12'0\" and Budget's 13'0\".", uhaul2)
        self.assertIn("Both classes clear 12'0\" even with a rooftop air conditioner in most cases", faq2["mainEntity"][1]["acceptedAnswer"]["text"])
        page3 = self.render(garage("g", "Grand Garage", 134))
        faq3 = next(b for b in jsonld(page3) if b["@type"] == "FAQPage")
        self.assertIn("At 11'2\", the 10 ft and 15–20 ft trucks fit; the 26 ft truck does not.", faq3["mainEntity"][0]["acceptedAnswer"]["text"])
        self.assertIn("Both classes fit at their published heights, but a rooftop air conditioner or vent may not; measure first.", faq3["mainEntity"][1]["acceptedAnswer"]["text"])
        page4 = self.render(garage("g", "Grand Garage", 162))
        faq4 = next(b for b in jsonld(page4) if b["@type"] == "FAQPage")
        uhaul4 = faq4["mainEntity"][0]["acceptedAnswer"]["text"]
        self.assertTrue(uhaul4.startswith("Yes. The posted clearance is 13'6\"."), uhaul4)
        self.assertIn("All three sizes fit at 13'6\".", uhaul4)
        self.assertNotIn("The fit table above uses", uhaul4)   # at the table's own 26ft height: no gap to explain

    def test_nearby_section_excludes_self_and_compares_heights(self):
        me = garage("me", "Center Garage", 99)
        near = garage("n1", "Next Door Garage", 110, lat=36.101, lng=-115.1)
        far = garage("n2", "Far Garage", 80, lat=36.5, lng=-115.1)
        page = self.render(me, [near, far])
        self.assertIn("Next Door Garage", page)
        self.assertIn("taller", page)
        self.assertNotIn("<li><a href=\"/parking/test-xx/center-garage\">", page)
        self.assertIn('href="/parking/test-xx/next-door-garage"', page)

    def test_nearby_ignores_candidates_without_coordinates(self):
        me = garage("me", "Center Garage", 99)
        no_coords = garage("nc", "No Coords Garage", 90, lat=None, lng=None)
        near = garage("n1", "Next Door Garage", 110, lat=36.101, lng=-115.1)
        gs = [me, no_coords, near]
        paths = glp.location_paths(CITY["slug"], gs)
        anchors = gen.assign_anchors(gs)
        candidates = glp.nearby(me, gs, paths, anchors)   # must not raise TypeError
        names = [c["garage"].get("name") for c in candidates]
        self.assertNotIn("No Coords Garage", names)
        self.assertIn("Next Door Garage", names)


class GeneratorRunTests(unittest.TestCase):
    def test_main_writes_eligible_pages_and_deletes_stale_ones(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "data/cities").mkdir(parents=True)
        (tmp / "data/index.json").write_text(json.dumps([CITY]))
        (tmp / "data/cities/test-xx.json").write_text(json.dumps({
            "garages": [garage("a", "Aria Resort & Casino", 99), garage("b", "Parking Deck", 70)],
            "tunnels": [], "bridges": []}))
        stale = tmp / "parking/test-xx"
        stale.mkdir(parents=True)
        (stale / "old-name.html").write_text("stale")
        old = (glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR)
        glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR = tmp, tmp / "data/index.json", tmp / "data/cities", tmp / "parking"
        try:
            glp.main([])
        finally:
            glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR = old
        self.assertEqual(sorted(p.name for p in (tmp / "parking/test-xx").glob("*.html")), ["aria-resort-and-casino.html"])
        shutil.rmtree(tmp)

    def test_main_deletes_orphan_city_directory_when_unscoped(self):
        """A city that drops out of live/index.json (or loses its data file)
        is never visited by main()'s per-city loop, so without the
        stale-directory pass its old parking/<slug>/ tree would sit there
        forever.  A full, unscoped run must clean it up."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "data/cities").mkdir(parents=True)
        (tmp / "data/index.json").write_text(json.dumps([CITY]))
        (tmp / "data/cities/test-xx.json").write_text(json.dumps({
            "garages": [garage("a", "Aria Resort & Casino", 99)], "tunnels": [], "bridges": []}))
        orphan = tmp / "parking/orphan-city-zz"
        orphan.mkdir(parents=True)
        (orphan / "old-garage.html").write_text("stale")
        old = (glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR)
        glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR = tmp, tmp / "data/index.json", tmp / "data/cities", tmp / "parking"
        try:
            glp.main([])
        finally:
            glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR = old
        self.assertFalse((tmp / "parking/orphan-city-zz").exists())
        self.assertTrue((tmp / "parking/test-xx").exists())
        shutil.rmtree(tmp)

    def test_main_with_city_flag_does_not_delete_other_city_directories(self):
        """--city intentionally only touches its own city's directory --
        the stale-directory pass must not run and nuke every other city's
        parking/ tree just because this invocation only regenerated one."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "data/cities").mkdir(parents=True)
        other = {"slug": "other-yy", "name": "Otherville", "state": "NV",
                 "lat": 36.1, "lng": -115.1, "status": "live"}
        (tmp / "data/index.json").write_text(json.dumps([CITY, other]))
        (tmp / "data/cities/test-xx.json").write_text(json.dumps({
            "garages": [garage("a", "Aria Resort & Casino", 99)], "tunnels": [], "bridges": []}))
        other_dir = tmp / "parking/other-yy"
        other_dir.mkdir(parents=True)
        (other_dir / "some-garage.html").write_text("kept")
        old = (glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR)
        glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR = tmp, tmp / "data/index.json", tmp / "data/cities", tmp / "parking"
        try:
            glp.main(["--city", "test-xx"])   # other-yy has no data file loaded for this run
        finally:
            glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR = old
        self.assertTrue((other_dir / "some-garage.html").exists())
        shutil.rmtree(tmp)

    def test_main_skips_stale_sweep_when_no_cities_processed(self):
        """If index.json has zero live cities (or none have data files),
        processed_slugs is empty. The stale sweep must not delete all existing
        pages, so it skips and prints a warning."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "data/cities").mkdir(parents=True)
        (tmp / "data/index.json").write_text(json.dumps([]))
        existing = tmp / "parking/some-city"
        existing.mkdir(parents=True)
        (existing / "some-garage.html").write_text("stale")
        old = (glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR)
        glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR = tmp, tmp / "data/index.json", tmp / "data/cities", tmp / "parking"
        try:
            glp.main([])
        finally:
            glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR = old
        self.assertTrue((existing / "some-garage.html").exists())
        shutil.rmtree(tmp)

    def test_main_completes_when_a_garage_has_no_coordinates(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "data/cities").mkdir(parents=True)
        (tmp / "data/index.json").write_text(json.dumps([CITY]))
        (tmp / "data/cities/test-xx.json").write_text(json.dumps({
            "garages": [garage("a", "Aria Resort & Casino", 99),
                       garage("b", "No Coords Garage", 90, lat=None, lng=None)],
            "tunnels": [], "bridges": []}))
        old = (glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR)
        glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR = tmp, tmp / "data/index.json", tmp / "data/cities", tmp / "parking"
        try:
            glp.main([])   # must complete, not raise, despite the coord-less garage
        finally:
            glp.REPO_ROOT, glp.INDEX_PATH, glp.CITIES_DIR, glp.OUT_DIR = old
        self.assertEqual(sorted(p.name for p in (tmp / "parking/test-xx").glob("*.html")),
                         ["aria-resort-and-casino.html"])
        shutil.rmtree(tmp)

    def test_city_page_links_to_garage_page(self):
        lv = (REPO / "city/las-vegas-nv.html").read_text()
        self.assertIn('<h3 class="entry-name"><a href="/parking/las-vegas-nv/', lv)
        il = next(b for b in jsonld(lv) if b["@type"] == "ItemList")
        self.assertTrue(any(i["item"]["url"].startswith("https://willifit.ai/parking/las-vegas-nv/") for i in il["itemListElement"]))
        toml = (REPO / "netlify.toml").read_text()
        self.assertIn('from   = "/parking/:city/:slug"', toml)
        sitemap = (REPO / "sitemap.xml").read_text()
        self.assertIn("<loc>https://willifit.ai/parking/las-vegas-nv/", sitemap)


if __name__ == "__main__":
    unittest.main()
