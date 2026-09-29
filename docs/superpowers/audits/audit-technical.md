# WillIFit.ai — Technical SEO / AEO Audit

Site: static HTML, no build step, deployed to https://willifit.ai via Netlify.
Scope audited: 13 root public pages + 226 city pages (239 total) for on-page/schema checks;
robots.txt, llms.txt, sitemap.xml, netlify.toml; the two page generators
(`scripts/generate_city_pages.py`, `scripts/generate_bridges_page.py`) and one non-generator
static page (`parking-garage-clearance-heights.html`); live checks against production.
Audit performed read-only against `/Users/MrLaptop2/Downloads/ClearPath` — no files in the repo
were modified. Raw per-page numbers backing every count below are in `audit-technical-data.json`.

Where a check came back clean, it is stated as clean rather than omitted, so this document also
serves as evidence of what was verified and passed.

---

## 1. On-page (title / description / canonical / robots / H1 / lang / social tags)

Scope: 239 public pages (13 root + 226 city). Root set = every root `*.html` except `admin.html`,
`advertise-thanks.html`, `404.html`.

**Clean / verified, no issue:**
- 0 duplicate `<title>` values and 0 duplicate `<meta description>` values across all 239 pages.
- Canonical present on 239/239 pages, and on every page it exactly matches the sitemap URL form
  (root pages keep `.html`; city pages are the extensionless `/city/<slug>` form). 0 mismatches.
- Exactly one `<h1>` on 239/239 pages.
- `<html lang="en">` on 239/239 pages.
- `og:title`, `og:description`, `og:image` all present on 239/239 pages.
- `<meta name="robots">` is `index,follow` on all 239 public pages (no city is accidentally
  noindexed — see also Section 5: 0 live cities currently have 0 locations, so the
  `total==0 → noindex,follow` branch in `generate_city()` has nothing to trigger on right now).
- City `<title>` length: **0 of 226** exceed 60 decoded characters. `build_title()` in
  `scripts/generate_city_pages.py` (line 100) does its job — every city that would exceed 60 chars
  in the full form already fell back to the shorter form or the site-suffix-dropped form.

**Findings:**

1. **Title >60 decoded chars on 3 root pages** (Low, quick-win)
   - `cities.html`: 67 chars — `"All covered US cities — WillIFit.ai (226 cities, 25,000+ locations)"`
   - `lowest-bridges-in-america.html`: 63 chars — `"The Lowest Bridges in America (Posted Clearances) | WillIFit.ai"` (baked in `scripts/generate_bridges_page.py` line 178, static string, not data-dependent)
   - `parking-garage-clearance-heights.html`: 63 chars — `"Parking Garage Clearance Heights (Standard Sizes) | WillIFit.ai"`
   - Fix: shorten each title tag to ≤60 chars. For `lowest-bridges-in-america.html`, edit the
     literal `<title>` string at `scripts/generate_bridges_page.py:178`. The other two are static
     files — edit the `<title>` tag directly in `cities.html` / `parking-garage-clearance-heights.html`.

2. **Title <30 decoded chars on 3 root pages** (Low, quick-win)
   - `cookies.html` (24 chars, `"Cookie Policy — Willifit"`), `privacy.html` (25 chars), `terms.html` (27 chars).
   - These are thin/generic and don't front-load any topical keywords. Not broken, just SERP
     real-estate left on the table.
   - Fix: edit the `<title>` tags directly (e.g. `"Cookie Policy — WillIFit.ai | Analytics & Storage Keys"`).

3. **Meta description over 160 decoded chars — and will keep growing** (Low→Medium over time, quick-win)
   - `lowest-bridges-in-america.html`: 166 chars — `'The 25 lowest posted bridge clearances in the US — down to 6\'0" — plus the lowest bridge in every covered state. Computed from 7,265 tracked low-clearance structures.'`
   - The overage comes from `{n_bridges:,}` in `scripts/generate_bridges_page.py:179`, a live count
     that only grows as the corpus grows (it was presumably under 160 chars when first written).
     This will silently drift further over 160 with every future data import.
   - Fix: shorten the fixed wording around `{n_bridges:,}` in `generate_bridges_page.py:179` so
     there's headroom for the number to keep growing, e.g. drop the "down to X" clause from the
     description (it's already the page's `<h1>`/lede content) and lead with the count.

4. **`twitter:card` missing entirely on 2 pages** (Medium, quick-win)
   - `advertise.html` and `how-ai-verification-works.html` have full `og:*` tags but **no**
     `twitter:card` / `twitter:title` / `twitter:description` / `twitter:image` at all — every
     other one of the 239 public pages has both. A link to either page shared on Twitter/X will
     render as a bare link instead of a `summary_large_image` card.
   - Fix: add the 4 missing `twitter:*` meta tags to `advertise.html` and
     `how-ai-verification-works.html`, matching the pattern already used on every other static page
     (see `about.html` head for the exact block to copy).

---

## 2. JSON-LD structured data

Parsed every `application/ld+json` block on all 239 public pages.

**Clean / verified, no issue:**
- **0 JSON-LD parse errors** across 239 pages / 471 blocks.
- **Every `@type` value found is a valid schema.org type.** Full inventory: `AboutPage, Answer,
  Article, BreadcrumbList, Bridge, CollectionPage, DataDownload, Dataset, FAQPage, GeoCoordinates,
  ImageObject, ItemList, ListItem, Organization, ParkingFacility, Place, PostalAddress, Question,
  SpeakableSpecification, WebPage, WebSite`. Spot-verified the less-common ones live against
  schema.org: `Bridge` (Thing > Place > CivicStructure > Bridge), `ParkingFacility` (…> CivicStructure
  > ParkingFacility), `SpeakableSpecification`, `CollectionPage` (Thing > CreativeWork > WebPage >
  CollectionPage) — all confirmed real types.
- `FAQPage` present on **229/229** of (226 city + 3 guide) pages, with correctly-shaped
  `mainEntity`/`Question`/`acceptedAnswer`/`Answer` nodes. Question-count distribution across those
  229 pages: 2 questions (43 pages), 3 (57), 4 (58), 5 (71).
- `BreadcrumbList` present on **226/226** city pages.
- The 2 `Article` nodes that exist (`lowest-bridges-in-america.html`,
  `parking-garage-clearance-heights.html`) both have `author`, `publisher`, `dateModified`,
  `datePublished`, and `speakable` — nothing missing.
- `about.html` (`AboutPage`) and `cities.html` (`CollectionPage`) each carry correct, page-specific
  structured data plus their own `BreadcrumbList`.

**Findings:**

5. **`ItemList.numberOfItems` doesn't match `itemListElement.length` on 154/226 city pages (68%)** (High, moderate effort)
   - Cause: `build_jsonld()` in `scripts/generate_city_pages.py` (lines 596–632) caps each of
     garages/tunnels/bridges to `src_list[:20]` when building `itemListElement`, but sets
     `"numberOfItems": total` to the **un-capped** true total (`len(garages)+len(tunnels)+len(bridges)`).
     Any city where one category exceeds 20 entries gets a self-contradictory `ItemList`.
   - Evidence: gap (`numberOfItems − itemListElement.length`) ranges 1–1,235, mean 103, median 47.
     Worst cases: `city/chicago-il.html` claims `numberOfItems=1295`, serializes 60 items (95%
     understated); `city/houston-tx.html` 1153 vs 60; `city/dallas-tx.html` 943 vs 60;
     `city/los-angeles-ca.html` 878 vs 60; `city/new-york-ny.html` 858 vs 60.
   - Impact: this is exactly the shape of error Google's Rich Results Test / Search Console flags
     as an `ItemList` validation warning ("`numberOfItems` doesn't match the number of items"), on
     over two-thirds of the site's indexable pages.
   - Fix: in `build_jsonld()` (`scripts/generate_city_pages.py:624-632`), set
     `"numberOfItems": len(items)` (i.e. count what's actually in `itemListElement` post-cap)
     instead of the pre-cap `total`. The true total is still communicated accurately elsewhere on
     the same page (the `description` field, the visible `<div class="stats">` counts, and the
     fully-uncapped visible `<ul class="entries">` list in the HTML body), so nothing is lost by
     making the `ItemList` internally consistent.

6. **`how-ai-verification-works.html` has only a `FAQPage` JSON-LD block — no `WebPage`, `Article`,
   or `BreadcrumbList`** (Medium, moderate effort)
   - Its two sibling guide pages (`lowest-bridges-in-america.html`,
     `parking-garage-clearance-heights.html`) both carry full `Article` + `BreadcrumbList` +
     `dateModified`. This page's `og:type` is `"article"` but there is no matching `Article`/
     `NewsArticle` structured-data node at all, and no breadcrumb trail (see also Section 11, F22/F23
     — this page's FAQ content also isn't visible in the body).
   - Fix: this file is hand-authored (no generator produces it — `grep`ing `scripts/*.py` for it
     turns up nothing). Add an `Article` node (mirroring the shape in
     `scripts/generate_bridges_page.py`'s `safe_jsonld()` call, lines 107-121) and a 2-level
     `BreadcrumbList` directly into `how-ai-verification-works.html`'s existing
     `<script type="application/ld+json">` block.

7. **6 hand-authored legal/utility pages carry only a bare `WebPage` node — no `dateModified`, no
   `BreadcrumbList`** (Medium, quick-win per file)
   - `advertise.html`, `cookies.html`, `disclaimer.html`, `dmca.html`, `privacy.html`, `terms.html`
     each have a `WebPage` JSON-LD node (`{"@type":"WebPage","name":...,"isPartOf":{...},
     "publisher":{...}}`) with **no `dateModified`** — contrast with `accessibility.html`, which
     also just a `WebPage` node but does carry `dateModified`. No generator script touches any of
     these 6 files (confirmed: `grep -rl "privacy.html" scripts/*.py` finds nothing — the matches
     are incidental references to the string `"WebPage"` inside unrelated generators). Any future
     content edit to these files has no mechanism to refresh a freshness signal that doesn't exist.
   - Fix: hand-add `"dateModified": "<git log -1 --format=%cs -- <file>>"` to each of the 6 files'
     `WebPage` node (same logic `scripts/generate_sitemap.py`'s `lastmod_for()` already uses), and a
     2-level `BreadcrumbList` matching `about.html`'s pattern. Given the pattern is byte-identical
     across all 6, this is also a reasonable candidate for a small shared generator if these pages
     get touched again.

8. **`index.html`: `Organization.sameAs` is an empty array; `Dataset` is missing 4 of 5 recommended
   fields** (Medium, quick-win / moderate)
   - `Organization.sameAs = []` — the property is declared but empty. Either populate it with real
     profile URLs (the business's social/citation profiles) or remove the property; an empty array
     is worse than absent because it signals "we checked and there are none."
   - `Dataset` node (`index.html` lines ~87-105) has: `@context, @type, name, description, url,
     keywords, creator, license, isAccessibleForFree, spatialCoverage, distribution`. **Missing**:
     `dateModified`, `temporalCoverage`, `variableMeasured`, `sameAs`, `identifier` — all
     Google-recommended Dataset properties for Dataset Search eligibility.
   - Fix: edit the `Dataset` block in `index.html` directly (no generator owns index.html's
     JSON-LD). `dateModified` can reuse the same `git log -1 --format=%cs -- index.html` approach;
     `temporalCoverage` and `variableMeasured` need a one-time editorial decision (e.g.
     `variableMeasured` listing `height_in`, `verified_on`, `source`).

---

## 3. Content accuracy in generated copy

This is the section with the most consequential findings — these are **live, published claims
about how tall a vehicle can be and still fit**, generated from fixed sentence templates in
`scripts/generate_city_pages.py` that don't branch on the number they're wrapping.

**Thresholds used** (stated explicitly, as instructed): box truck ≥ 126 in (10'6"); Class-C RV
≥ 138 in (11'6"); high-roof van / small Class-B RV ≥ 114 in (9'6"); standard car needs ≤ 84 in
(7'0") of clearance (i.e. any real garage clears a car — this bound is not what trips any finding
below, it's stated for completeness).

9. **"Highest-clearance" FAQ claims "accommodates most box trucks and smaller RVs" regardless of
   the actual number — false on 82/226 cities (36%)** (Critical, moderate effort)
   - Template, `build_faqs()` in `scripts/generate_city_pages.py` (lines 333-340):
     `f"{e.get('name')} has the tallest verified clearance in {name} at {e.get('height_label')} "
     f"({int(e.get('height_in'))} inches), which accommodates most box trucks and smaller RVs."` —
     the "accommodates…" clause is unconditional text, appended no matter what
     `height_in` actually is.
   - This FAQ renders on 127/226 cities (the other 99 have zero verified garages, so it's skipped
     entirely). Of those 127: **82 (64.6%) are flatly false** (highest < 126 in — can't fit a box
     truck at all), 8 are partially true (fit box trucks, not a full Class-C RV), and 37 are fully
     accurate.
   - Confirmed example from the task brief: `city/akron-oh.html` — "Parking Deck has the tallest
     verified clearance in Akron at 6'2" (74 inches), which accommodates most box trucks and
     smaller RVs." 74 in is a compact-car garage.
   - Worse example found in this audit: **`city/manchester-nh.html`** — highest verified clearance
     is **5'2" (62 inches)** — barely taller than a sedan — and the page still states it
     "accommodates most box trucks and smaller RVs." Also: `city/ann-arbor-mi.html` (6'6"/78in),
     `city/new-orleans-la.html` (6'7"/79in), `city/allentown-pa.html` (6'8"/80in),
     `city/niagara-falls-ny.html` (6'8"/80in).
   - Fix: in `build_faqs()` (lines 333-340), branch the answer sentence on `e.get('height_in')`
     against the thresholds above, e.g.: ≥138in → "fits most Class-C RVs and box trucks"; 126–137in
     → "fits box trucks but not full-size Class-C RVs"; <126in → drop the vehicle-fit claim
     entirely and state what it *does* fit (e.g. "clears standard cars and small SUVs; not tall
     enough for vans, box trucks, or RVs").

10. **"Lowest-clearance" FAQ claims "vans, RVs, and box trucks should look elsewhere" regardless of
    the actual number — contradicted on 7/226 cities** (Critical, moderate effort — same template family as #9)
    - Template, `build_faqs()` lines 323-331: `"Standard cars and small SUVs fit, but vans, RVs,
      and box trucks should look elsewhere."` — also unconditional.
    - 7 cities have a "lowest verified" garage tall enough that this is false for at least vans
      (≥114in), and in 6 of those 7 it's false for the full set (≥138in, fits vans+box
      trucks+Class-C RVs too).
    - Most striking example: **`city/waco-tx.html`** — the *lowest* verified clearance on file is
      **16'0" (192 inches)** — tall enough for a loaded semi — and the page still says "vans, RVs,
      and box trucks should look elsewhere." Also `city/columbia-mo.html` (14'0"),
      `city/springfield-il.html` (12'11"), `city/peoria-il.html` (12'5"),
      `city/providence-ri.html` / `city/fresno-ca.html` (12'0"), `city/henderson-nv.html` (10'6").
    - Fix: same as #9 — branch the second sentence on `facts["lowest"]["height_in"]` in
      `build_faqs()` (lines 323-331).

11. **City-page lede overstates AI-verification coverage on every city it applies to (112/226 =
    49.6% of all cities; 112/112 = 100% of cities with any AI verification)** (Critical, moderate effort)
    - In `generate_city()` (`scripts/generate_city_pages.py:1068-1081`): `clearance_adj` is set to
      `"AI-verified clearance heights"` whenever `ver["ai"] > 0` (i.e. *at least one* entry is
      AI-verified), then the lede prints `f"{clearance_adj} for {locations} in {name}, {state_full}."`
      where `locations` is built from the **total** garage/tunnel/bridge count — not the AI-verified
      count. There are **zero** cities where 100% of entries are AI-verified, so every city that
      qualifies for the "AI-verified" adjective (112 of them) also overstates the count it's
      attached to.
    - Task's own example confirmed: **Las Vegas** — `ai=15, total=204`. Rendered lede: *"AI-verified
      clearance heights for 204 parking garages, tunnels, and low-clearance bridges in Las Vegas,
      Nevada."* Only 7.4% of those 204 are actually AI-verified.
    - Worst case found: **`city/chicago-il.html`** — `ai=22, total=1295` (1.7% AI-verified) — lede
      reads *"AI-verified clearance heights for 1,295 parking garages, tunnels, and low bridges in
      Chicago, Illinois."* Also `city/houston-tx.html` (13/1153), `city/new-york-ny.html`
      (12/858), `city/detroit-mi.html` (3/539), `city/tulsa-ok.html` (1/406, i.e. 1 AI-verified
      entry underwrites a claim covering 406 locations).
    - Fix: in `generate_city()` (lines 1068-1081), qualify the claim with the actual count, e.g.
      `f"{ver['ai']} of {total} locations have AI-verified clearance heights; the rest are
      {provenance_label(ver)}."`, or gate the `"AI-verified"` adjective on a coverage threshold
      (e.g. `ver["ai"] / total >= 0.5`) rather than `ver["ai"] > 0`. The per-entry tags already do
      this correctly (each `<li>` only shows an AI-verified badge on entries that actually are) —
      this is purely a page-level summary-sentence bug, not a data-labeling bug.

12. **"Highest clearance" quick-fact card duplicates "Lowest clearance" on 41/226 cities (18%) —
    single-verified-garage cities** (Medium, moderate effort)
    - `compute_quick_facts()` sets both `lowest` and `highest` to the *same* dict object when only
      one garage has a valid height. `render_quick_facts()` renders both cards anyway (no dedup
      check), and `build_faqs()` renders both the "lowest" and "highest" FAQ questions from that
      one entry.
    - Compounding effect: **34 of these 41 cities** also trigger finding #9 (the single data point
      is short enough to make the "accommodates most box trucks and smaller RVs" line false) — so
      the page shows the same number twice *and* mischaracterizes it once.
    - Fix: in `render_quick_facts()` (line 232) and `build_faqs()` (lines 333-340), skip the
      "Highest clearance" card / "highest-clearance parking option" FAQ when
      `facts["highest"] is facts["lowest"]`, or relabel it (e.g. "Only garage on file" instead of
      "Highest clearance") so it doesn't read as a comparison that doesn't exist.

**Corpus-wide context** (also feeds Section 6): of 23,472 total entries across the 226 cities, only
**512 (2.18%) are AI-verified**; 762 (3.25%) are verified by any method (AI or human); **96.75% are
unverified OSM/NBI imports**. 99/226 cities (44%) have zero verified garages at all.

---

## 4. Internal link graph

Parsed every `<a href>` on all 239 public pages; normalized `/city/<slug>` and
`/city/<slug>.html` to one node; ignored same-page `#fragment` anchors, `mailto:`, and external
links; resolved every remaining internal href against the filesystem (respecting the
`/city/:slug → /city/:slug.html` Netlify rewrite).

**Clean / verified, no issue:**
- **0 broken internal links** across 239 pages / thousands of hrefs.
- `index.html` is linked from all 238 other public pages (923 raw link occurrences: header brand
  link + breadcrumb + CTA + footer, several per page).
- All 3 guide pages are linked from all 239 pages — but this is entirely the **shared footer**,
  present on every template; it is not an editorial/in-content signal and shouldn't be read as one.

**Findings:**

13. **65/226 city pages (29%) have exactly one inbound internal link — from `cities.html` only**
    (Medium, moderate effort — product/config decision, not a bug fix)
    - No page excluding `index.html` has **zero** inbound links (every city is at least reachable
      from the directory), but 65 cities get no cross-linking from any sibling city page's "Nearby
      cities" block at all. Examples: `city/albany-ny.html`, `city/austin-tx.html`,
      `city/charlotte-nc.html`, `city/milwaukee-wi.html`, `city/nashville-tn.html` — i.e. this
      isn't confined to obscure towns; several sizable metros get only the directory link.
    - Related: **88/226 cities (39%) have *zero* outbound "Nearby cities" links** — either no other
      covered city is within `compute_nearby_cities()`'s 60-mile radius (`max_miles=60` in
      `scripts/generate_city_pages.py:467`), or (for genuinely isolated markets — Honolulu,
      Anchorage, San Juan, Key West, Moab, Jackson WY, Sheridan WY, Grand Junction) there really
      isn't one. Distribution of nearby-link counts across all 226 city pages: min 0, median 1, max
      6, mean 1.38 (0 links: 88 cities; 1: 65; 2: 29; 3: 11; 4: 13; 5: 15; 6: 5).
    - Fix: this is a coverage-density tradeoff, not a code defect — the fix is a product decision
      (e.g. widen `max_miles` past 60, or add a same-state fallback per finding #14 below) in
      `compute_nearby_cities()` (`scripts/generate_city_pages.py:467-483`).

14. **The only city-to-city link mechanism is pure distance (60 mi), with no same-state affinity —
    city pages never link to other cities in their own state unless one happens to also be within
    60 miles** (Low, informational)
    - Confirmed structurally: `compute_nearby_cities()` is the *only* source of city→city links
      anywhere in the template (`PAGE_TEMPLATE` has no other city-list block). A Texas city whose
      nearest covered neighbor is 90 miles away links to *no* other Texas city page, even though
      `cities.html` groups the whole state together.
    - Fix (optional, matches the "beyond Nearby cities" question directly): add a small "More in
      {state}" fallback list in `render_nearby_cities()` (or a sibling function) when the
      distance-based list comes back empty or thin, sourced from `all_cities` filtered by
      `c["state"] == this_city["state"]`.

---

## 5. Sitemap

15. **`sitemap.xml` is stale on 239/239 URLs (100%)** (High, quick-win)
    - Every `<lastmod>` predates its backing file's actual last `git log -1 --format=%cs` commit
      date. Breakdown: 227 URLs stale by 13 days (sitemap says `2026-08-19`, files committed
      `2026-09-01`), 11 URLs stale by 9 days (`2026-08-23` → `2026-09-01`), 1 URL (`index.html`)
      stale by 11 days (`2026-08-23` → `2026-09-03`). Mean staleness: 12.8 days.
    - Cause: `scripts/generate_sitemap.py` was last run on 2026-08-19/23, but a subsequent commit
      (`c1b09a9`, 2026-09-01, "Site audit loop: security, layout, a11y, generators — all
      verification green") touched every city page and most root pages without re-running the
      sitemap generator afterward; `index.html` moved again on 2026-09-03 (the Google Maps
      migration) with the same gap.
    - This is exactly the failure mode `generate_sitemap.py`'s own docstring warns about: *"Stale
      lastmods train crawlers to ignore the field."* Right now 100% of the file is in that state.
    - Fix: run `python3 scripts/generate_sitemap.py` and commit the result (quick-win, one command
      — see Section 8, the generator is confirmed safe/deterministic to re-run). To stop this
      recurring, wire sitemap regeneration into whatever already runs the page generators (a
      pre-commit hook, or a documented "always run these three together" step) — there is currently
      no automation tying `generate_city_pages.py` / `generate_bridges_page.py` output to
      `generate_sitemap.py`.

**Clean / verified, no issue:**
- URL **set** is perfectly in sync with disk: 0 URLs missing from the sitemap, 0 sitemap URLs
  without a backing file/data, 0 duplicate `<loc>` entries, 0 zero-location cities incorrectly
  included (there are currently 0 live cities with 0 locations to test this against, but the logic
  path in both `generate_city_pages.py` and `generate_sitemap.py` is consistent by inspection — both
  gate on the same `total == 0` condition).
- `robots.txt` correctly references it: `Sitemap: https://willifit.ai/sitemap.xml`.

---

## 6. llms.txt

16. **All city-page references use the non-canonical `/city/<slug>.html` form** (Medium, quick-win)
    - Every city URL mentioned in `llms.txt` uses the `.html` suffix: the generic pattern
      (`` `/city/<slug>.html` ``), the worked example (`` `/city/los-angeles-ca.html` ``), and —
      most consequentially — the literal instruction in the "Citation guidance for AI agents"
      section: *"Link to the specific city page (`https://willifit.ai/city/<slug>.html`)."* Zero
      occurrences use the canonical extensionless `/city/<slug>` form that `<link rel="canonical">`
      declares on every city page. Functionally harmless (Netlify serves the `.html` file directly
      too — confirmed live, see Section 9), but this file's specific job is to tell AI agents which
      URL to cite, and it's telling them the non-canonical one.
    - Fix: edit `llms.txt` (3 occurrences) to drop `.html` from the city-page pattern, example, and
      citation instruction.

17. **Opening claim overstates AI-verification coverage** (High, quick-win to reword / requires the
    fix in finding #11 to actually close the gap)
    - Blockquote, first line of the file: *"AI-verified vehicle-clearance heights for parking
      garages, tunnels, and low bridges across 226 US cities."* Actual: only **112/226 cities
      (49.6%)** have any AI-verified entry at all, and AI-verified entries are **512 of 23,472
      (2.18%)** of all entries sitewide (see Section 3 corpus-wide context). This is the single
      most-exposed sentence in the file — first line, read by every agent that fetches
      `llms.txt` before anything else — and it overstates city coverage by ~2× and entry-level
      coverage by ~46×.
    - Fix: reword to something defensible at current data volume, e.g. *"AI-verified and
      OSM/FHWA-sourced vehicle-clearance heights for parking garages, tunnels, and low bridges
      across 226 US cities (112 cities currently have at least one AI-verified reading)."* Consider
      re-deriving this line from `data/index.json` / the per-city JSON at `llms.txt` generation time
      (there is currently no generator for `llms.txt` — it's hand-maintained) so the claim can't
      drift further from the data as the corpus grows.

**Clean / verified, no issue:**
- All 12 non-home root public pages, plus the home page, are referenced in `llms.txt` (Core pages +
  Legal sections cover all 13 root pages between them — nothing missing).
- Data endpoints are all mentioned: `data/index.json`, the `data/cities/<slug>.json` pattern, and
  `sitemap.xml`.

---

## 7. robots.txt

**Clean / verified, no issue — no findings.**
- 27 well-formed `User-agent:` groups, each with matching `Allow`/`Disallow` rules immediately
  following it (0 malformed lines, 0 unrecognized directives, 0 empty groups).
- Every group disallows only `/data/nbi_cache/`, `/admin.html`, `/.netlify/` — none of which are
  indexable content; 0 indexable paths accidentally blocked.
- Exactly one `Sitemap:` line, correctly pointing at `https://willifit.ai/sitemap.xml`.
- The repeat-the-rules-per-bot structure (rather than relying on the wildcard `User-agent: *` group)
  is correct per RFC 9309 — a crawler matching a specific `User-agent:` ignores the wildcard group
  entirely, and the file's own comments correctly document why.

---

## 8. Generator drift

Method: `rsync -a` the repo to `SCRATCH/drift-copy/` (excluding `.git`, `data/nbi_cache`,
`node_modules`, `.claude`), ran `python3 scripts/generate_city_pages.py` and
`python3 scripts/generate_bridges_page.py` **in that copy only** (neither script takes CLI args —
confirmed no `argparse` in either file, so the bare invocation is the only mode / already "full
generation"), then diffed the copy's output against the committed originals in the real repo.

**Result: both generators are safe to re-run; committed output is not drifted.**
- **226/226 city pages are byte-for-byte identical** between the committed repo and a fresh
  regeneration from the same `data/` files.
- `lowest-bridges-in-america.html` differs from a fresh regeneration in **exactly 2 places**, both
  the intentional `date.today()` stamp: the JSON-LD `Article.dateModified`
  (`"2026-09-01"` → `"2026-09-10"`, the day this audit ran) and the visible lede text
  (`"...updated 2026-09-01."` → `"...updated 2026-09-10."`). Every data-derived number (7,265
  structures, 52 states, the specific lowest-bridge record, every table row) is byte-identical.
  This is by design (the script stamps the build date on every run), not drift.
- Direct, actionable implication for finding #15: re-running `generate_sitemap.py` right now will
  produce a correct, non-stale sitemap without any risk of accidentally changing city-page or
  bridges-page content.

---

## 9. Performance / rendering signals

Static analysis of all 239 pages, plus live `curl` (`-A "Mozilla/5.0..."`,
`-H "Accept-Encoding: br, gzip"`) against production for: `/`, 5 sample city pages
(`akron-oh`, `las-vegas-nv`, `chicago-il`, `new-york-ny`, `manchester-nh`), the 3 guide pages,
both URL forms of one city page, and the two noindex pages.

**Findings:**

18. **`/js/consent.js` loads synchronously in `<head>` (no `defer`/`async`) on all 239 public
    pages** (Medium, needs verification before changing)
    - It's a same-origin, ~48KB file (`js/consent.js` is 49,618 bytes on disk), loaded via
      `<script src="/js/consent.js"></script>` with neither attribute, ahead of the Cloudflare
      (`defer`) and Google tag (`async`) scripts that follow it in every page template. This is
      render-blocking on literally every page in the site.
    - This may well be intentional — a consent-gate script plausibly needs to run before the
      analytics tags fire, so it can block them pre-consent. That's a real constraint, not
      something to override reflexively (per the project's own "diagnose root cause, don't
      band-aid" standard). Fix: check whether `js/consent.js`'s gating logic actually depends on
      synchronous execution order, or whether it just needs to run *before the analytics tags
      execute* (which `defer` alone would still guarantee, since deferred scripts run in document
      order before `DOMContentLoaded`, and the Cloudflare tag is `defer` too / the gtag script is
      `async` and already reads `window.dataLayer` defensively). If the ordering constraint is
      only "before the async gtag," `defer` is very likely sufficient and would unblock first
      paint on every page in the site.

19. **`index.html` preconnects to 3 origins its active code path never uses** (Low, quick-win)
    - `<link rel="preconnect" href="https://a/b/c.basemaps.cartocdn.com">` (3 hints, `index.html`
      lines 12-14) exist to warm the Carto tile-server connection. But `MAP_PROVIDER` is hardcoded
      to `'google'` (`index.html:2882`), and the only function that constructs a Carto tile URL
      (`initMap()`, using the `base` variable at line 2892) is gated behind
      `if (MAP_PROVIDER !== 'google') initMap();` (line 5584) — i.e. it is dead code under the
      current configuration. The 3 preconnects spend early DNS/TLS handshakes on origins the page
      never actually contacts.
    - Fix: remove the 3 `cartocdn.com` preconnect hints from `index.html`, or repoint them at
      origins the Google Maps path actually needs early (`maps.googleapis.com`,
      `maps.gstatic.com` — both already CSP-allowlisted).

20. **Inline `<style>` size** (informational, not a defect — flagging for awareness given the
    project's single-file-HTML convention)
    - `index.html`: 91.3 KB of inline CSS (uncacheable independent of the HTML document, since it's
      inline — re-parsed on every navigation, though not re-downloaded across repeat visits to the
      SAME page under normal HTTP caching of the HTML itself). City pages: exactly 9,916 bytes each,
      byte-identical across all 226 (confirms the shared template renders deterministically). Root
      guide/legal pages: 1.4–5.4 KB each. Not recommending extraction to an external stylesheet —
      that cuts against the project's stated single-file-HTML preference and there's no measured
      Core Web Vitals problem here, just noting the number.

21. **27 city pages render more than 200 location entries in the visible HTML with no cap or
    pagination (6 exceed 500)** (Low, informational — not a wire-bandwidth problem)
    - `render_section()` (`scripts/generate_city_pages.py:1017-1021`) renders every entry via
      `render_entry()` with no slice, unlike `build_jsonld()`'s `[:20]`-per-category cap (see
      finding #5 — same root cause, two different symptoms). Largest: `city/chicago-il.html` — 1,295
      `<li class="entry...">` blocks (227 garages + 207 tunnels + 861 bridges), 524.9 KB of raw
      HTML. Also `houston-tx` (1,153), `dallas-tx` (943), `los-angeles-ca` (878), `new-york-ny`
      (858), `detroit-mi` (539).
    - **This is not a bandwidth problem**: brotli compression takes Chicago's 524.9KB raw HTML down
      to **27,345 bytes over the wire** (confirmed live, ~19× ratio, because the markup is so
      repetitive) — smaller than several much-shorter city pages transfer. The actual cost is
      client-side: parsing and laying out ~1,300 repeated DOM blocks on a single page load.
    - Not recommending a fix without more signal (no Core Web Vitals field data was available to
      this audit) — flagging so it's a known, named tradeoff rather than a surprise if a large-metro
      page ever gets reported as slow-feeling despite a small network payload.

**Clean / verified, no issue:**
- **Zero `<img>` tags exist anywhere on any of the 239 audited pages** — so the
  alt-text/width-height/`loading=lazy` checks have no applicable subjects site-wide (not a pass by
  omission of the check — actually verified: 0 images, 0 image issues).
- All live-curl checks returned `200`, all with `content-encoding: br`.
- `https://willifit.ai/city/akron-oh` and `https://willifit.ai/city/akron-oh.html` both return 200
  with the **identical ETag** (`"9300d52966a36cc0fd5a935fdfa44beb-ssl-df"`) — Netlify serves the
  same underlying file for both URL forms; the canonical tag correctly tells search engines which
  one to index.
- `https://willifit.ai/advertise-thanks.html` is `noindex,follow` live (confirmed by fetching the
  body, not just the local file) — correct for a post-submit thank-you page.
- `https://willifit.ai/404.html` is `noindex` live, and a genuinely nonexistent path
  (`/this-page-does-not-exist-xyz123`) correctly returns a real HTTP `404` status (Netlify's default
  404 wiring is working, not just serving 404.html with a 200).
- HTML byte size per page type: `index.html` 277.2 KB; root guide/legal pages 8.6–34.6 KB; city
  pages 19.9–524.9 KB (mean 71.6 KB, median 54.4 KB).

---

## 10. Open Graph image

**Clean / verified, no issue.**
- `og-image.png` exists (34,292 bytes). PNG signature valid. `IHDR` chunk (bytes 16-23) decodes to
  **1200 × 630** — exactly matching every page's `og:image:width`/`og:image:height` meta tags.

---

## 11. AEO readiness

Checked the 3 guide pages plus 3 sample city pages (`akron-oh`, `las-vegas-nv`, `chicago-il`).

22. **`lowest-bridges-in-america.html` declares a 3-question `FAQPage` in JSON-LD with *zero*
    matching visible content anywhere in the page** (Critical, moderate effort)
    - Confirmed by grepping the full rendered body for `faq|<details|<summary|frequently`: no
      matches outside the `<script type="application/ld+json">` block itself. The 3 Q&A pairs
      ("What is the lowest bridge in America?", "How many low-clearance bridges does WillIFit.ai
      track?", "What vehicles are at risk from low bridges?") exist **only** inside the structured
      data — there is no corresponding heading, paragraph, or `<details>` element in the visible
      page at all.
    - This violates Google's own structured-data guidance (marked-up FAQ content must be visible on
      the page) — the page risks losing FAQ rich-result eligibility entirely — and it means any
      answer engine that extracts from rendered/visible text rather than specifically parsing
      `FAQPage` JSON-LD gets nothing from this page's FAQ content.
    - Fix: add a visible FAQ section to `scripts/generate_bridges_page.py`'s page template
      (matching the pattern already used correctly in `parking-garage-clearance-heights.html` —
      real `<h2>`/`<h3>` headings, not `<details>`, since this is hand-authored HTML built by an
      f-string, not the city generator's disclosure-widget pattern) using the *same 3 questions/
      answers* already defined in the `jsonld` variable (lines 132-169) so the two stay in sync by
      construction.

23. **`how-ai-verification-works.html` declares a 5-question `FAQPage` in JSON-LD — its *only*
    structured-data block (see finding #6) — also with zero visible Q&A content** (Critical, moderate effort)
    - Confirmed by reading the full body: it's a numbered 5-step pipeline walkthrough ("Find the
      entrance" → "Record provenance, not just the number") plus prose sections ("What this is
      not," "Why we think it's still worth doing," etc.) that cover *some* of the same ground
      thematically (e.g. "This is not a substitute for looking at the sign" echoes the FAQ question
      "Is AI verification a substitute for reading the posted sign?") but never states the actual
      FAQ questions or gives extractable, matching answers. None of `faq`, `<details>`, `<summary>`
      appear anywhere in the file.
    - Same impact as #22, and arguably worse here: this page is the site's explicit methodology/
      trust page — exactly the content an answer engine would want to cite when a user asks "how
      accurate is WillIFit's AI verification," and right now that content is invisible to
      visible-text extraction.
    - Fix: add a visible FAQ section (real `<h3>` per question, matching
      `parking-garage-clearance-heights.html`'s working pattern) using the 5 questions/answers
      already written in the JSON-LD block, and give this page the `Article`/`BreadcrumbList`
      schema it's also missing (finding #6) at the same time.

24. **`parking-garage-clearance-heights.html` does this correctly** (reference pattern, no fix
    needed) — real `<h3>` questions (`"How tall is a standard parking garage?"`, etc.) each
    immediately followed by a `<p>` answer, inside an `<h2>Frequently asked questions</h2>` section
    (lines 273-293). This is the model the other two guide pages should be brought in line with.

25. **On all 226 city pages, FAQ questions are `<summary>` text inside `<details>`, not `<h2>`/`<h3>`
    headings** (Medium, moderate effort — template-wide change)
    - `render_faq_section()` (`scripts/generate_city_pages.py:422-438`) wraps the section itself in
      an `<h2>Frequently asked</h2>`, but each individual question is
      `<summary class="faq-q">{question}</summary>` inside a `<details class="faq-item">`, with the
      answer in an adjacent `<div class="faq-a">` (not a `<p>`). This is collapsed-by-default
      disclosure-widget markup — functional, keyboard-accessible, and indexable by Google (which
      does crawl `<details>` content), but it does not match the heading+paragraph pattern that
      many answer-engine scrapers specifically look for, and the content is hidden until a click/
      expand, unlike `parking-garage-clearance-heights.html`'s always-visible `<h3>`+`<p>` pairs.
    - Fix (if this is worth the visual/interaction tradeoff — collapsing 4-5 Q&As does keep the
      page shorter): change `render_faq_section()` to emit `<h3>` for each `{f["q"]}` and `<p>` for
      each `{f["a"]}`, dropping the `<details>`/`<summary>` collapse behavior, or at minimum ensure
      the `<summary>` text is *also* wrapped in a heading tag inside the disclosure widget
      (`<summary><h3>…</h3></summary>` is valid and keeps the interaction while adding a real
      heading landmark).

26. **Speakable markup: clean where declared** (verified, no issue) — `lowest-bridges-in-america.html`'s
    `#lowest-answer` selector and `parking-garage-clearance-heights.html`'s `.answer` selector both
    resolve to real elements that exist in their respective pages (`<div class="answer" id="lowest-answer">`
    and `<div class="answer">` respectively). Neither `how-ai-verification-works.html` nor the
    city-page template declares `speakable` at all — not wrong (schema.org scopes `speakable` to
    `Article`/`WebPage`, and neither page has one — see finding #6), just a coverage gap worth
    knowing about.

27. **No visible "last updated" date on 2 of 3 guide pages, and on 103/226 (46%) city pages**
    (Low, informational)
    - `how-ai-verification-works.html` and `parking-garage-clearance-heights.html` have no visible
      "last updated" or "as of" text anywhere in the body (grepped for `updated|last verified|as
      of`, 0 matches on either) — lower urgency since both are largely evergreen reference content,
      but still worth a small visible date given the industry-standard clearance numbers they cite
      could change.
    - City pages show per-entry verification dates for entries that have one (e.g. "AI-verified
      from Google Street View on Apr 18, 2026" / "Verified on …") — that part is good and
      AEO-friendly at the data-point level. But there is no single page-level "this page was last
      updated" statement anywhere, and **103/226 city pages (46%) have zero dated entries at all**
      (100% unverified imports — see Section 3), leaving no visible freshness signal whatsoever on
      nearly half the city pages.

---

## Summary table

| # | Finding | Severity | Effort |
|---|---|---|---|
| 1 | 3 root-page titles >60 decoded chars (`cities.html`, `lowest-bridges-in-america.html`, `parking-garage-clearance-heights.html`) | Low | Quick-win |
| 2 | 3 root-page titles <30 decoded chars (`cookies.html`, `privacy.html`, `terms.html`) | Low | Quick-win |
| 3 | `lowest-bridges-in-america.html` meta description 166 chars, grows with the corpus | Low | Quick-win |
| 4 | `twitter:card` missing on `advertise.html` and `how-ai-verification-works.html` | Medium | Quick-win |
| 5 | `ItemList.numberOfItems` ≠ `itemListElement.length` on 154/226 city pages (gap up to 1,235) | High | Moderate |
| 6 | `how-ai-verification-works.html` has only `FAQPage` schema — no `Article`/`WebPage`/`BreadcrumbList` | Medium | Moderate |
| 7 | 6 legal/utility pages' `WebPage` schema has no `dateModified`, no `BreadcrumbList` | Medium | Quick-win (×6) |
| 8 | `index.html` `Organization.sameAs=[]`; `Dataset` missing dateModified/temporalCoverage/variableMeasured/sameAs/identifier | Medium | Quick-win / Moderate |
| 9 | "Highest-clearance" FAQ falsely claims box-truck/RV fit on 82/226 cities (36%) | Critical | Moderate |
| 10 | "Lowest-clearance" FAQ falsely tells vans/RVs/box trucks to "look elsewhere" on 7/226 cities | Critical | Moderate |
| 11 | City lede overstates AI-verification coverage on 112/226 cities (100% of cities with any AI data) | Critical | Moderate |
| 12 | "Highest clearance" duplicates "Lowest clearance" on 41/226 single-garage cities (34 also hit #9) | Medium | Moderate |
| 13 | 65/226 city pages have exactly 1 inbound link (cities.html only); 88/226 have 0 nearby-city links | Medium | Moderate |
| 14 | Nearby-cities linking is pure-distance, no same-state fallback | Low | Moderate |
| 15 | `sitemap.xml` stale on 239/239 URLs (mean 12.8 days) | High | Quick-win |
| 16 | `llms.txt` cites city pages in non-canonical `.html` form (3 occurrences) | Medium | Quick-win |
| 17 | `llms.txt` opening claim overstates AI-verification coverage (~2×/~46×) | High | Quick-win (reword) |
| 18 | `/js/consent.js` render-blocks `<head>` on all 239 pages (48KB, no defer/async) | Medium | Moderate (verify gating first) |
| 19 | `index.html` preconnects to 3 dead-code-path Carto origins | Low | Quick-win |
| 20 | `index.html` inline `<style>` is 91.3KB (informational) | Low | — (no action recommended) |
| 21 | 27 city pages render >200 entries uncapped in HTML (6 exceed 500); wire size is fine (brotli) | Low | — (informational) |
| 22 | `lowest-bridges-in-america.html` FAQPage schema has zero visible matching content | Critical | Moderate |
| 23 | `how-ai-verification-works.html` FAQPage schema has zero visible matching content | Critical | Moderate |
| 24 | `parking-garage-clearance-heights.html` FAQ pattern is correct (h3+p) | — | Reference, no fix |
| 25 | City-page FAQ questions are `<summary>`, not `<h2>`/`<h3>` headings | Medium | Moderate |
| 26 | Speakable selectors resolve correctly where declared | — | Clean, no fix |
| 27 | No visible "last updated" on 2/3 guide pages; 103/226 city pages have zero dated entries | Low | Informational |

**Severity counts: 5 Critical, 3 High, 9 Medium, 8 Low, 2 clean/reference (no action).**

Sections with zero findings (fully clean): **Section 7 (robots.txt)**, **Section 8 (generator
drift — both generators confirmed safe to re-run)**, **Section 10 (og-image dimensions)**.
