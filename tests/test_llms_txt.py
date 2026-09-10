"""llms.txt generator (SEO/AEO plan, Task 5).  Run: python3 tests/test_llms_txt.py -v"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import generate_llms_txt as gl  # noqa: E402


class LlmsTxtTests(unittest.TestCase):
    def test_render_counts_and_canonical_urls(self):
        st = gl.corpus_stats()
        text = gl.render()
        self.assertIn(f"across {st['cities']} US cities ({st['total']:,} locations", text)
        self.assertIn(f"{st['ai']:,} clearances are AI-verified", text)
        self.assertIn(f"{st['human']:,} more are verified against published sources", text)
        self.assertNotRegex(text, r"/city/[a-z0-9<>-]+\.html")
        for s in ("/state/<xx>", "https://willifit.ai/state/nv", "/parking/<city-slug>/<garage-slug>",
                  "https://willifit.ai/parking/las-vegas-nv/", "https://willifit.ai/vehicle-heights.html",
                  "https://willifit.ai/city/los-angeles-ca", f"Data current as of {st['latest_verified']}."):
            self.assertIn(s, text, s)
        self.assertEqual(st["total"], st["ai"] + st["human"] + st["imported"])
        self.assertNotRegex(text, r"\b2026-09-\d\d\b(?!.*current)")  # no build-date stamps

    def test_committed_file_matches_generator(self):
        self.assertEqual((REPO / "llms.txt").read_text(), gl.render())


if __name__ == "__main__":
    unittest.main()
