"""Truth rules for scripts/generate_city_pages.py (SEO/AEO plan, Task 1).
Run:  python3 tests/test_city_generator_copy.py -v
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import wf_common as wc            # noqa: E402
import generate_city_pages as gen  # noqa: E402

CITY = {"slug": "test-xx", "name": "Testville", "state": "NV", "region": "West",
        "lat": 36.1, "lng": -115.1, "zoom": 12, "garage_count": 0, "status": "live"}


def garage(id_, name, height_in, **kw):
    e = {"id": id_, "name": name, "addr": "", "lat": 36.1, "lng": -115.1,
         "height_in": height_in,
         "height_label": wc.inches_label(height_in) if height_in else None,
         "oversized": False, "notes": "", "source": "OpenStreetMap"}
    e.update(kw)
    return e


def render(garages, tunnels=None, bridges=None):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "test-xx.json").write_text(json.dumps(
        {"garages": garages, "tunnels": tunnels or [], "bridges": bridges or []}))
    old = gen.CITIES_DIR
    gen.CITIES_DIR = tmp
    try:
        return gen.generate_city(CITY, all_cities=[CITY])
    finally:
        gen.CITIES_DIR = old


def jsonld(html_text):
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html_text, re.S)
    return json.loads(m.group(1))


class FitPhraseTests(unittest.TestCase):
    def test_never_claims_a_class_fits_below_its_height(self):
        for h in range(48, 181):
            p = wc.fit_phrase(h)
            for name, need in wc.VEHICLE_CLASSES:
                if need > h:
                    self.assertNotRegex(p, r"clears " + re.escape(name), (h, p))

    def test_exact_wording(self):
        self.assertEqual(wc.fit_phrase(74),
            "That clears a typical sedan (5'0\"), but not a stock pickup or SUV (6'6\") or anything taller.")
        self.assertEqual(wc.fit_phrase(99),
            "That clears a low-roof cargo van (7'0\") and anything shorter, but not a mid-roof cargo van (8'4\") or anything taller.")
        self.assertEqual(wc.fit_phrase(110),
            "That clears a 10–12 ft rental truck (9'0\") and anything shorter, but not a high-roof Sprinter or Transit (9'6\") or anything taller.")
        self.assertEqual(wc.fit_phrase(155),
            "That clears a Class C RV (11'6\") and anything shorter, but not a 26 ft rental truck (13'6\") or anything taller.")
        self.assertEqual(wc.fit_phrase(162),
            "That clears every common vehicle class, including a 13'6\" semi trailer.")
        self.assertEqual(wc.fit_phrase(55),
            "That is below the 5'0\" of a typical sedan; treat it as unsuitable for anything taller than a compact car.")

    def test_labels_slugs_clip(self):
        self.assertEqual(wc.inches_label(74), "6'2\"")
        self.assertEqual(wc.inches_label(99.0), "8'3\"")
        self.assertEqual(wc.slugify("Aria Resort & Casino"), "aria-resort-and-casino")
        self.assertEqual(wc.slugify("Café Déck  #2"), "cafe-deck-2")
        self.assertEqual(wc.slugify("!!!"), "garage")
        self.assertEqual(wc.clip("alpha beta gamma", 10), "alpha beta")
        self.assertEqual(wc.clip("short", 10), "short")
        self.assertTrue(wc.has_posted_height(garage("a", "Deck", 74)))
        self.assertFalse(wc.has_posted_height(garage("a", "Deck", 74, source="Needs verification (was OSM building-height)")))
        self.assertFalse(wc.has_posted_height(garage("a", "Deck", None)))


class FaqTests(unittest.TestCase):
    def faqs(self, garages):
        facts = gen.compute_quick_facts(garages, [], [])
        return facts, gen.build_faqs(CITY, facts, gen.verification_summary(garages))

    def test_single_garage_city_has_no_highest_faq_and_no_highest_card(self):
        facts, faqs = self.faqs([garage("a", "Parking Deck", 74)])
        self.assertFalse(any("highest" in f["q"].lower() for f in faqs))
        self.assertNotIn("Highest clearance", gen.render_quick_facts(facts))
        self.assertIn("Lowest clearance", gen.render_quick_facts(facts))

    def test_lowest_and_highest_answers_use_fit_phrase_not_fixed_wording(self):
        facts, faqs = self.faqs([garage("a", "Parking Deck", 74), garage("b", "Big Deck", 99)])
        low = next(f for f in faqs if f["q"].startswith("What's the lowest"))
        high = next(f for f in faqs if f["q"].startswith("What's the highest"))
        self.assertIn("6'2\" (74 inches) at Parking Deck", low["a"])
        self.assertIn(wc.fit_phrase(74), low["a"])
        self.assertIn(wc.MEASURE_NOTE, low["a"])
        self.assertNotIn("verified", low["a"].split(".")[0])   # imported entry: no 'verified' in the claim sentence
        self.assertIn("8'3\" (99 inches) at Big Deck", high["a"])
        self.assertIn(wc.fit_phrase(99), high["a"])
        self.assertNotIn("accommodates most box trucks", high["a"])
        self.assertNotIn("should look elsewhere", low["a"])

    def test_answers_carry_per_entry_verification(self):
        facts, faqs = self.faqs([
            garage("a", "Deck", 74, source="AI-verified (Street View + Claude Vision)", verified_on="2026-08-01"),
            garage("b", "Deck B", 99),
            garage("c", "Deck C", 90, source="Web-verified (high confidence) - was: mgmresorts.com", verified_on="2026-05-02"),
            garage("d", "Deck D", 95, source="MGM Resorts")])
        low = next(f for f in faqs if f["q"].startswith("What's the lowest"))
        high = next(f for f in faqs if f["q"].startswith("What's the highest"))
        self.assertIn("It was AI-verified from Street View signage on Aug 1, 2026.", low["a"])
        self.assertIn("It is imported from OpenStreetMap and not yet individually verified.", high["a"])
        self.assertEqual(gen.verification_sentence(garage("c", "Deck C", 90, source="Web-verified (high confidence) - was: mgmresorts.com", verified_on="2026-05-02")),
                         "It was verified against mgmresorts.com on May 2, 2026.")
        self.assertEqual(gen.verification_sentence(garage("d", "Deck D", 95, source="MGM Resorts")),
                         "The figure comes from MGM Resorts and has not been individually re-verified.")
        self.assertEqual(gen.verification_sentence(garage("e", "Deck E", 95, source="Needs verification")),
                         "This figure is unverified and may not reflect the posted sign.")
        self.assertEqual(gen.verification_sentence(garage("f", "F", 90, source="Web-verified (medium confidence) - was: Needs verification",
                                                          verified_on="2026-08-12", source_url="https://www.bransoncc.com/parking/")),
                         "It was verified against bransoncc.com on Aug 12, 2026.")
        self.assertEqual(gen.verification_sentence(garage("g", "G", 90, source="Manually verified from Google Street View — was: OpenStreetMap", verified_on="2026-04-22")),
                         "It was verified by a person from Google Street View imagery on Apr 22, 2026.")
        self.assertEqual(gen.verification_sentence(garage("h", "H", 90, source="Web-verified surface lot - was: Needs verification", verified_on="2026-08-12")),
                         "It was verified against a published source on Aug 12, 2026.")
        self.assertEqual(gen.verification_sentence(garage("i", "I", 90, source="Verified in person — was: vegasfoodandfun.com", verified_on="2026-05-01")),
                         "It was verified in person on May 1, 2026.")
        self.assertEqual(gen.verification_sentence(garage("j", "J", 90, source="OpenStreetMap", verified_on="2026-04-22")),
                         "It was verified on Apr 22, 2026; source: OpenStreetMap.")
        self.assertEqual(gen.verification_sentence(garage("k", "K", 90, source="User-observed (sign obscured)", verified_on="2026-04-22")),
                         "It was reported by a user on Apr 22, 2026 and has not been independently verified.")
        li = gen.render_entry(garage("h", "H", 90, source="Web-verified surface lot - was: Needs verification", verified_on="2026-08-12"), "garage", "loc-h")
        self.assertNotIn("source:", li)
        li = gen.render_entry(garage("f", "F", 90, source="Web-verified (medium confidence) - was: Needs verification",
                                     verified_on="2026-08-12", source_url="https://www.bransoncc.com/parking/"), "garage", "loc-f")
        self.assertIn('source: <a href="https://www.bransoncc.com/parking/" target="_blank" rel="noopener">bransoncc.com</a>', li)

    def test_needs_verification_heights_are_not_facts(self):
        facts, faqs = self.faqs([garage("a", "Deck", 74, source="Needs verification (was OSM building-height)"),
                                 garage("b", "Deck B", 99)])
        self.assertEqual(facts["lowest"]["name"], "Deck B")

    def test_rv_park_answer_makes_no_blanket_fit_claim(self):
        facts, faqs = self.faqs([garage("a", "Sunny RV Park", None)])
        rv = next(f for f in faqs if f["q"].startswith("Are there RV parks"))
        self.assertNotIn("any vehicle size fits", rv["a"])
        self.assertIn("open-air", rv["a"])


class PageTests(unittest.TestCase):
    def test_import_only_city_never_says_verified_in_title_description_or_lede(self):
        page = render([garage("a", "Deck", 74)])
        title = re.search(r"<title>(.*?)</title>", page).group(1)
        desc = re.search(r'name="description" content="([^"]*)"', page).group(1)
        ogd = re.search(r'property="og:description" content="([^"]*)"', page).group(1)
        for s in (title, desc, ogd):
            self.assertNotIn("verified", s.lower())
        lede = re.search(r'<p class="lede">(.*?)</p>', page).group(1)
        self.assertEqual(lede, "1 parking garage in Testville, Nevada: 1 imported from OpenStreetMap. "
                               "Enter your vehicle height on the interactive map to see what fits.")

    def test_ai_city_lede_and_description_state_exact_counts(self):
        page = render([garage("a", "Deck", 74, source="AI-verified (Street View + Claude Vision)", verified_on="2026-08-01"),
                       garage("b", "Deck B", 99),
                       garage("c", "Deck C", 90, source="Web-verified (high confidence) - was: x.com", verified_on="2026-05-02")])
        lede = re.search(r'<p class="lede">(.*?)</p>', page).group(1)
        self.assertEqual(lede, "3 parking garages in Testville, Nevada: 1 AI-verified from Street View signage; "
                               "1 verified against published sources; 1 imported from OpenStreetMap. "
                               "Enter your vehicle height on the interactive map to see what fits.")
        desc = re.search(r'name="description" content="([^"]*)"', page).group(1)
        self.assertEqual(desc, "Clearance heights for 3 parking garages, tunnels, and low bridges in Testville, Nevada. "
                               "1 AI-verified from Street View signage. Check before you drive.")
        self.assertLessEqual(len(desc), 160)

    def test_anchors_are_unique_and_appear_in_jsonld(self):
        page = render([garage("osm-w1", "Deck", 74), garage("osm-w1", "Deck", 80)],
                      tunnels=[garage("t1", "Tunnel", 120)])
        ids = re.findall(r'<li class="entry entry-[a-z]+" id="([^"]+)">', page)
        self.assertEqual(ids, ["loc-osm-w1", "loc-osm-w1-2", "loc-t1"])
        il = next(b for b in jsonld(page) if b["@type"] == "ItemList")
        self.assertEqual(il["numberOfItems"], 3)
        self.assertEqual(len(il["itemListElement"]), 3)
        self.assertEqual(il["itemListElement"][0]["item"]["url"], "https://willifit.ai/city/test-xx#loc-osm-w1")
        self.assertNotIn("streetAddress", il["itemListElement"][0]["item"]["address"])

    def test_jsonld_lists_every_entry_not_just_twenty(self):
        page = render([garage(f"g{i}", f"Deck {i}", 80 + i) for i in range(25)])
        il = next(b for b in jsonld(page) if b["@type"] == "ItemList")
        self.assertEqual(len(il["itemListElement"]), 25)

    def test_description_is_clipped_on_a_word_boundary(self):
        page = render([garage("a", "Deck", 74)] * 3)
        desc = re.search(r'name="description" content="([^"]*)"', page).group(1)
        self.assertLessEqual(len(desc), 160)
        self.assertFalse(desc.endswith(" "))


if __name__ == "__main__":
    unittest.main()
