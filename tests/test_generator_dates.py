"""No generator may stamp the wall clock into its output.  A regen with
unchanged data must produce byte-identical files; otherwise every
regen_all.sh run makes a false freshness claim and churns sitemap.xml
(generate_sitemap.py gives uncommitted files today's lastmod).
Run:  python3 tests/test_generator_dates.py -v
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import wf_common as wc                     # noqa: E402
import generate_bridges_page as gbp         # noqa: E402
import generate_city_pages as gen           # noqa: E402
import generate_location_pages as glp       # noqa: E402
import generate_state_pages as gsp          # noqa: E402

TODAY = date.today().isoformat()


def bridge(id_, name, height_in):
    return {"id": id_, "name": name, "addr": "", "lat": 36.1, "lng": -115.1,
            "height_in": height_in, "height_label": wc.inches_label(height_in),
            "notes": "", "source": "FHWA NBI"}


class FixtureRepo:
    """A throwaway git repo holding a minimal corpus, so commit dates are
    under the test's control instead of whatever the real history says."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "data/cities").mkdir(parents=True)
        (self.root / "data/index.json").write_text(json.dumps([
            {"slug": "testville-nv", "name": "Testville", "state": "NV", "status": "live"}]))
        self.write_bridges([bridge("b1", "Low Street Underpass", 100),
                            bridge("b2", "Rail Trestle", 130)])
        self.git("init", "-q")
        self.commit("2020-01-02T12:00:00+0000")

    def git(self, *args, env=None):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "-c", "commit.gpgsign=false", *args],
                       cwd=self.root, check=True, capture_output=True,
                       env={**os.environ, **(env or {})})

    def write_bridges(self, bridges):
        (self.root / "data/cities/testville-nv.json").write_text(
            json.dumps({"garages": [], "tunnels": [], "bridges": bridges}))

    def commit(self, when):
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", "data",
                 env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})


def page_dates(page):
    """(visible 'updated' date in the lede, Article JSON-LD dateModified)."""
    lede = re.search(r"updated (\d{4}-\d{2}-\d{2})\.</p>", page).group(1)
    blocks = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',
                                  page, re.S).group(1))
    article = next(b for b in blocks if b["@type"] == "Article")
    return lede, article["dateModified"]


class DataDateTests(unittest.TestCase):
    def setUp(self):
        self.repo = FixtureRepo()

    def test_clean_corpus_is_dated_by_its_last_data_commit(self):
        self.assertEqual(wc.data_date(self.repo.root), "2020-01-02")

    def test_uncommitted_data_change_is_dated_today(self):
        self.repo.write_bridges([bridge("b1", "Low Street Underpass", 99)])
        self.assertEqual(wc.data_date(self.repo.root), TODAY)

    def test_untracked_city_file_is_dated_today(self):
        (self.repo.root / "data/cities/newtown-nv.json").write_text("{}")
        self.assertEqual(wc.data_date(self.repo.root), TODAY)

    def test_commit_outside_the_corpus_does_not_move_the_date(self):
        (self.repo.root / "README.md").write_text("docs only")
        self.repo.commit("2023-06-07T12:00:00+0000")
        self.assertEqual(wc.data_date(self.repo.root), "2020-01-02")

    def test_uncommitted_non_corpus_data_does_not_move_the_date(self):
        # sponsors.json / pricing.json feed no generated page's numbers.
        (self.repo.root / "data/sponsors.json").write_text("{}")
        self.assertEqual(wc.data_date(self.repo.root), "2020-01-02")

    def test_not_a_git_checkout_fails_loudly_instead_of_guessing_today(self):
        with self.assertRaises(RuntimeError):
            wc.data_date(Path(tempfile.mkdtemp()))


class BridgesPageTests(unittest.TestCase):
    def setUp(self):
        self.repo = FixtureRepo()

    def render(self):
        page, _summary = gbp.render(self.repo.root)
        return page

    def test_two_renders_with_same_data_are_identical(self):
        self.assertEqual(self.render(), self.render())

    def test_unchanged_data_is_not_stamped_today(self):
        lede, modified = page_dates(self.render())
        self.assertEqual((lede, modified), ("2020-01-02", "2020-01-02"))
        self.assertNotIn(TODAY, self.render())

    def test_uncommitted_data_change_is_stamped_today(self):
        self.repo.write_bridges([bridge("b1", "Low Street Underpass", 99)])
        self.assertEqual(page_dates(self.render()), (TODAY, TODAY))

    def test_committed_data_change_is_stamped_with_its_commit_date(self):
        self.repo.write_bridges([bridge("b1", "Low Street Underpass", 99)])
        self.repo.commit("2021-03-04T12:00:00+0000")
        self.assertEqual(page_dates(self.render()), ("2021-03-04", "2021-03-04"))

    def test_committed_file_matches_generator(self):
        page, _summary = gbp.render()
        self.assertEqual((REPO / "lowest-bridges-in-america.html").read_text(), page,
                         "lowest-bridges-in-america.html is stale: run "
                         "python3 scripts/generate_bridges_page.py")


class FooterYearTests(unittest.TestCase):
    """The '© YYYY' footer on city, parking and state pages must follow the
    data, not the clock -- otherwise every page rewrites on 1 January."""

    def setUp(self):
        wc.data_year.cache_clear()
        self.addCleanup(wc.data_year.cache_clear)
        patcher = mock.patch.object(wc, "data_date", return_value="2031-05-06")
        patcher.start()
        self.addCleanup(patcher.stop)

    def assertFooterYear(self, page):
        self.assertIn("<div>© 2031 WillIFit.ai", page)

    def test_city_page(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "test-xx.json").write_text(json.dumps(
            {"garages": [], "tunnels": [], "bridges": [bridge("b1", "Low Street Underpass", 100)]}))
        city = {"slug": "test-xx", "name": "Testville", "state": "NV", "region": "West",
                "lat": 36.1, "lng": -115.1, "zoom": 12, "garage_count": 0, "status": "live"}
        with mock.patch.object(gen, "CITIES_DIR", tmp):
            self.assertFooterYear(gen.generate_city(city, all_cities=[city]))

    def test_location_page(self):
        city = {"slug": "test-xx", "name": "Testville", "state": "NV",
                "lat": 36.1, "lng": -115.1, "status": "live"}
        g = {"id": "a", "name": "Aria Resort & Casino", "addr": "", "lat": 36.1, "lng": -115.1,
             "height_in": 99, "height_label": wc.inches_label(99), "oversized": False,
             "notes": "", "source": "OpenStreetMap"}
        paths = glp.location_paths(city["slug"], [g])
        anchors = gen.assign_anchors([g])
        self.assertFooterYear(glp.generate_location(city, g, paths[0], [g], paths, anchors[0]))

    def test_state_page(self):
        cities = [{"slug": "t-nv", "name": "T", "state": "NV", "lat": 0, "lng": 0, "status": "live"}]
        data = {"t-nv": {"garages": [], "tunnels": [],
                         "bridges": [bridge("b1", "Low Street Underpass", 100)]}}
        self.assertFooterYear(gsp.generate_state("NV", cities, data))


if __name__ == "__main__":
    unittest.main()
