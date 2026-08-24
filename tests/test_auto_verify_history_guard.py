"""Tests for the auto_verify.py conflict-history guard.

A self-consistent misread (the model transcribes the sign wrong AND states
the same wrong number) passes the _height_matches_raw exact-match guard.
The 2026-08-24 sweep showed the tell is in data/auto_verify.log: the bad
writes all had a DISAGREEING earlier reading for the same garage id.  The
guard scans the log before writing and quarantines any reading that
conflicts with history by more than 1 inch.

The real data/auto_verify.log is used as the fixture — it contains the
three known-bad cases from the sweep:
    washington-dc  osm-w248766712  wrote 228 (19'0") vs priors 84, 156
    ann-arbor-mi   osm-w30839085   wrote  66 (5'6")  vs prior  80
    portland/vanc  osm-w246719832  wrote 100 (8'4")  vs prior  76

Run:  python3 -m unittest tests.test_auto_verify_history_guard -v
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
REAL_LOG = REPO / "data" / "auto_verify.log"
sys.path.insert(0, str(REPO / "scripts"))

import auto_verify  # noqa: E402


class LoadHistoryTests(unittest.TestCase):
    """_load_height_history parses prior AI readings out of the real log."""

    @classmethod
    def setUpClass(cls):
        cls.hist = auto_verify._load_height_history(REAL_LOG)

    def _readings(self, gid):
        return [(p["status"], p["h"]) for p in self.hist.get(gid, [])]

    def test_washington_dc_fixture(self):
        r = self._readings("osm-w248766712")
        self.assertIn(("LOW-CONF", 84), r)
        self.assertIn(("LOW-CONF", 156), r)
        self.assertIn(("VERIFIED", 228), r)

    def test_ann_arbor_fixture(self):
        r = self._readings("osm-w30839085")
        self.assertIn(("LOW-CONF", 80), r)
        self.assertIn(("VERIFIED", 66), r)

    def test_portland_vancouver_fixture(self):
        r = self._readings("osm-w246719832")
        self.assertIn(("LOW-CONF", 76), r)
        self.assertIn(("VERIFIED", 100), r)

    def test_reject_hallucination_lines_are_not_readings(self):
        # 2026-07-31 osm-w246719832 REJECT-HALLUCINATION ... h=96 — that h
        # failed the digit guard, so it must not count as a prior reading.
        heights = [p["h"] for p in self.hist["osm-w246719832"]]
        self.assertNotIn(96, heights)

    def test_check_mode_ai_readings_count(self):
        # 2026-04-21 reno-nv osm-w14390303 CHECK-MISMATCH stored=177 ai=156
        heights = [p["h"] for p in self.hist.get("osm-w14390303", [])]
        self.assertIn(156, heights)


class ConflictingPriorsTests(unittest.TestCase):
    def test_within_one_inch_is_not_a_conflict(self):
        hist = {"g": [{"status": "VERIFIED", "h": 84, "date": "2026-01-01", "slug": "x"}]}
        self.assertEqual(auto_verify._conflicting_priors(hist, "g", 84), [])
        self.assertEqual(auto_verify._conflicting_priors(hist, "g", 85), [])
        self.assertEqual(auto_verify._conflicting_priors(hist, "g", 83), [])

    def test_more_than_one_inch_conflicts(self):
        hist = {"g": [{"status": "LOW-CONF", "h": 84, "date": "2026-01-01", "slug": "x"}]}
        self.assertEqual(len(auto_verify._conflicting_priors(hist, "g", 86)), 1)

    def test_unknown_id_has_no_conflicts(self):
        self.assertEqual(auto_verify._conflicting_priors({}, "never-seen", 84), [])


class QuarantineIntegrationTests(unittest.TestCase):
    """process_city with a conflicting history must quarantine, not write."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="avtest-"))
        self.cities = self.tmp / "cities"
        self.cities.mkdir()
        self.log = self.tmp / "auto_verify.log"
        shutil.copyfile(REAL_LOG, self.log)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write_city(self, slug, gid):
        city = {"garages": [{"id": gid, "name": "Test Parking Garage",
                             "lat": 38.9, "lng": -77.0, "height_in": None}],
                "tunnels": [], "bridges": []}
        (self.cities / f"{slug}.json").write_text(json.dumps(city))

    def _canned(self, h):
        return {"status": "verified", "claude_calls": 1, "cost_est": 0.028,
                "pano_id": "PANOFIXTURE", "pano_heading": 351.0,
                "pano_distance_m": 9.0, "height_in": h,
                "height_label": auto_verify.inches_to_label(h),
                "confidence": "high", "raw_text": "fixture", "notes": ""}

    def _run(self, slug, gid, h, history, dry_run=False):
        with mock.patch.object(auto_verify, "CITIES_DIR", self.cities), \
             mock.patch.object(auto_verify, "LOG_PATH", self.log), \
             mock.patch.object(auto_verify, "verify_garage",
                               return_value=self._canned(h)):
            return auto_verify.process_city(slug, 10.0, "high", dry_run, 0.0,
                                            history=history)

    def _stored(self, slug):
        return json.loads((self.cities / f"{slug}.json").read_text())["garages"][0]

    def test_conflicting_history_quarantines_instead_of_writing(self):
        gid = "osm-w248766712"
        self._write_city("washington-dc", gid)
        hist = auto_verify._load_height_history(self.log)
        r = self._run("washington-dc", gid, 228, hist)
        self.assertEqual(r["updated"], 0)
        self.assertEqual(r["quarantined"], 1)
        g = self._stored("washington-dc")
        self.assertIsNone(g["height_in"])
        # Stamped like no-sign so the next fill run doesn't re-spend on it
        self.assertEqual(g.get("sv_status"), "conflict-quarantine")
        lines = [l for l in self.log.read_text().splitlines()
                 if "CONFLICT-QUARANTINE" in l and gid in l]
        self.assertEqual(len(lines), 1)
        # Both readings + the pano/heading for adjudication
        self.assertIn("h=228", lines[0])
        self.assertIn("84", lines[0])
        self.assertIn("156", lines[0])
        self.assertIn("PANOFIXTURE", lines[0])
        self.assertIn("351", lines[0])

    def test_clean_history_still_writes(self):
        gid = "test-garage-never-logged"
        self._write_city("testville", gid)
        hist = auto_verify._load_height_history(self.log)
        r = self._run("testville", gid, 84, hist)
        self.assertEqual(r["updated"], 1)
        self.assertEqual(r["quarantined"], 0)
        self.assertEqual(self._stored("testville")["height_in"], 84)

    def test_guard_disabled_writes_through(self):
        gid = "osm-w248766712"
        self._write_city("washington-dc", gid)
        r = self._run("washington-dc", gid, 228, history=None)
        self.assertEqual(r["updated"], 1)
        self.assertEqual(r.get("quarantined", 0), 0)
        self.assertEqual(self._stored("washington-dc")["height_in"], 228)

    def test_same_run_cross_city_conflict(self):
        # The portland-or / vancouver-wa shape: the same OSM way in two city
        # files, scanned in one run.  The second city's disagreeing reading
        # must see the first city's write from THIS run.
        gid = "osm-shared-fixture-xyz"
        self._write_city("city-a", gid)
        self._write_city("city-b", gid)
        hist = auto_verify._load_height_history(self.log)
        r1 = self._run("city-a", gid, 100, hist)
        self.assertEqual(r1["updated"], 1)
        r2 = self._run("city-b", gid, 76, hist)
        self.assertEqual(r2["updated"], 0)
        self.assertEqual(r2["quarantined"], 1)

    def test_dry_run_reports_quarantine_without_stamping(self):
        gid = "osm-w30839085"
        self._write_city("ann-arbor-mi", gid)
        hist = auto_verify._load_height_history(self.log)
        r = self._run("ann-arbor-mi", gid, 66, hist, dry_run=True)
        self.assertEqual(r["quarantined"], 1)
        self.assertEqual(r["updated"], 0)
        g = self._stored("ann-arbor-mi")
        self.assertIsNone(g.get("sv_status"))
        # The conflict is still logged in dry-run (matching VERIFIED/LOW-CONF
        # dry-run behavior) so a dry-run leaves an adjudication trail.
        self.assertTrue(any("CONFLICT-QUARANTINE" in l and gid in l
                            for l in self.log.read_text().splitlines()))


if __name__ == "__main__":
    unittest.main()
