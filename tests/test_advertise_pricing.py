"""Sponsor prices have one source: data/pricing.json (2026-10-01 drift fix).
Run: python3 tests/test_advertise_pricing.py -v

The runtime half (house ads in both renderers) is locked by
tests/test_sponsor_pricing.mjs; this covers the static surfaces."""
import importlib.util
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRICING = json.loads((REPO / "data" / "pricing.json").read_text())

_spec = importlib.util.spec_from_file_location(
    "gen_pricing", REPO / "scripts" / "generate_advertise_pricing.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

# An advertising price as copy would phrase it: "$75/mo", "$75 – $200".
AD_PRICE = re.compile(r"\$\d+\s*(?:/\s*mo|[–-]\s*\$\d+)")


class AdvertisePricingTests(unittest.TestCase):
    def test_advertise_html_in_sync_with_pricing_json(self):
        current = gen.OUT_PATH.read_text()
        self.assertEqual(gen.render(current, gen.load_pricing()), current,
                         "run python3 scripts/generate_advertise_pricing.py")

    def test_no_dollar_figures_outside_generated_spans(self):
        s = (REPO / "advertise.html").read_text()
        start, end = s.index(gen.BEGIN), s.index(gen.END)
        outside = s[:start] + s[end:]
        floor = min(x["from"] for x in PRICING["slots"])
        stray = [m for m in re.findall(r"\$\d+(?:/mo)?", outside) if m != f"${floor}/mo"]
        self.assertEqual(stray, [], "hand-typed price outside the generated rate card")

    def test_teaser_quotes_a_price_on_the_card(self):
        head = (REPO / "advertise.html").read_text().split("<body", 1)[0]
        floors = {x["from"] for x in PRICING["slots"]}
        quoted = {int(n) for n in re.findall(r"[Ff]rom \$(\d+)/mo", head)}
        self.assertTrue(quoted, "meta teaser lost its price")
        self.assertLessEqual(quoted, floors, quoted)

    def test_every_house_ad_slot_has_a_rate_card_row(self):
        keys = {x["key"] for x in PRICING["slots"]}
        for s in json.loads((REPO / "data" / "sponsors.json").read_text()):
            for tier in s.get("tier", []):
                self.assertIn(tier, keys, f"{s['id']}: slot {tier!r} sold but not on the rate card")

    def test_no_ad_prices_hardcoded_in_renderers_templates_or_generated_pages(self):
        paths = [REPO / "index.html", REPO / "js" / "sponsors.js", REPO / "data" / "sponsors.json"]
        paths += sorted((REPO / "scripts").glob("generate_*.py"))
        paths += sorted((REPO / "city").glob("*.html"))
        self.assertGreater(len(paths), 200, "sanity: generated city pages present")
        hits = [(p.name, m) for p in paths for m in AD_PRICE.findall(p.read_text())]
        # generate_advertise_pricing.py formats prices from the constant; it
        # holds no literal figures, so it passes this check as-is.
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
