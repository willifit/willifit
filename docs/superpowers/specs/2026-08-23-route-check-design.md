# Route Check — design spec

Date: 2026-08-23
Status: approved in-session (approach + all design sections); this document is the
written record for review before an implementation plan is drawn up.

## What this is

A pre-drive route safety check at `/route.html`: enter a start, a destination, and
your vehicle height; get a height-aware driving route with every known low-clearance
structure near it flagged. A planning aid, explicitly not navigation.

Scope decisions already made with the owner:

- **Pre-drive check**, not live navigation and not a corridor-only viewer.
- **National scale** — the dangerous case is the unfamiliar cross-country RV/U-Haul
  route, so the hazard layer covers the whole US, not just the 226 covered metros.
- **Approach A**: height-aware routing (OpenRouteService `driving-hgv`) plus an
  independent overlay check against our own national hazard layer. No silent
  fallback to height-blind car routing.

## Components

### 1. `data/route_hazards.json` — national hazard layer (new, generated)

Built by a new `scripts/build_route_hazards.py`:

- **Inputs:** the FHWA NBI cache already in `data/nbi_cache/` (52 files), parsed at
  a 14'6" threshold via the existing `nbi_import.parse_nbi_file` (measured: 2,265
  structures nationally); plus every bridge and tunnel with a posted height from
  `data/cities/*.json` (~16,800 entries).
- **Merge rule:** dedupe by ~50m proximity; when two sources disagree, keep the
  LOWER posted height (conservative) and note both sources.
- **Output entry:** `{id, name, lat, lng, height_in, height_label, kind, source}` —
  same field names the app already uses.
- **Size rule:** one file if the built output is ≤1.5MB; otherwise four regional
  shards (`route_hazards_{west,midwest,south,northeast}.json`) and the client
  fetches only shards whose bounding box intersects the route. Decided by the build
  script at build time, recorded in a tiny `route_hazards_index.json` either way.
- Regenerated whenever city data or the NBI import changes (same cadence as the
  Lowest Bridges page; the script is idempotent and diff-reviewable).
- Served with the same `no-cache, must-revalidate` + CORS headers as other data.

### 2. `netlify/functions/route.js` — routing proxy (new)

- `POST {start: [lat,lng], end: [lat,lng], height_in}` → calls ORS Directions
  (`driving-hgv` profile) with `height` in meters → returns `{geometry, distance_m,
  duration_s}` or a typed error.
- `ORS_API_KEY` from Netlify env (and local `.env` for dev). Never in the client.
- Rate limiting: same dependency-free two-tier limiter pattern as `reports.js`.
- Input validation: coordinates must be in a US bounding box; height 48–200 inches.
- **No logging of coordinates** (privacy commitment made on the page).
- Timeout 10s; ORS errors are passed through with honest messages ("routing service
  unavailable", "no route found for that height").

### 3. `route.html` — the page (new)

- Standalone page, same visual language as the app; Leaflet + OSM tiles + ODbL
  attribution, same as index.html.
- Form: start, destination (Nominatim geocoding, same as the app's address search;
  ambiguous queries show top matches to pick), vehicle height in feet+inches,
  prefilled from `willifit_vehicle_height_in`, **required**.
- Corridor check, client-side, pure function: decode polyline → coarse grid bucket
  of hazards → flag hazards within **75m** of any route segment. Verdicts reuse the
  app's fit semantics: below height+6" = red conflict; within 6" = amber tight.
- Results: route on the map + hazard markers + list (name, posted height, distance
  from route, Google/Street View deep link — existing pattern). Three states:
  - **Clear:** "No known hazards within 75m of this route" — with explicit "that is
    not a guarantee" copy. Never the word "safe".
  - **Tight / Conflict:** ranked list, lowest first.
- Share links: `?from=…&to=…&h=…` URL params (matches the app's share-link pattern).
- Honest-framing block on every result: what the layer covers (2,265 NBI + metro
  data), posted heights change, planning aid not navigation, always obey signage;
  link to disclaimer.html.
- Discoverability: header/footer links from index.html, sitemap entry, llms.txt
  entry, SEO/OG/JSON-LD meta consistent with other content pages.
- Service worker: `route.html` joins the network-first HTML route group in `sw.js`
  (no precache addition needed; hazards JSON uses the existing data-cache path).
- Accessibility: built to the same standard the site just reached — real headings,
  labeled inputs, live-region announcements for result states, keyboard-reachable
  results list. The statement page's "new features get checked before they ship"
  promise applies to this page first.

## Error handling

| Failure | Behavior |
|---|---|
| ORS down / quota exhausted | Visible error, retry advice. **No** car-route fallback — a height-blind route presented as checked is worse than no answer. |
| No hgv route at that height | Surface ORS's reason plainly; suggest re-checking the height. |
| Geocode finds nothing / too many | Empty-state message / pick-list of top matches. |
| Hazard layer fetch fails | Route still shown but check clearly marked as NOT run (amber banner) — never a silent "clear". |

## Privacy & legal

- privacy.html: new short section — route start/end are sent transiently to our
  function → OpenRouteService, and to Nominatim for geocoding; not stored, not
  logged; links to both processors' policies.
- ODbL/OSM attribution on the route map (routing and geocoding are OSM-derived).
- netlify.toml: `/docs/*` added to the source-hardening 404 redirects (this spec
  directory would otherwise be publicly served under `publish = "."`).

## Testing

- `build_route_hazards.py`: `--dry-run` prints counts per source, dedupe count, and
  output size; sanity assertions (no entry outside US bbox, no height outside
  48–174", every entry has coords + height).
- Corridor check: pure JS function with a unit fixture — a route past the 11-foot-8
  bridge (Durham NC, in NBI) must flag it for a 12'0" vehicle and stay quiet for a
  6'0" one. Runs in Node with no browser.
- Function: handler invoked directly with mocked `fetch` for the ORS call —
  validation rejects, success shape, error passthrough, rate-limit behavior.
- End-to-end: local browser run against the real ORS once the key exists; verify a
  known conflict route and a known clear route.

## Owner prerequisite

A free OpenRouteService account → API key → `ORS_API_KEY` in Netlify env settings
and local `.env`. Everything except live routing can be built and tested before the
key exists (the function is testable with a mocked ORS response).

## Out of scope (v1)

- Live/turn-by-turn navigation, rerouting, offline routing.
- OSM `maxheight` data outside the 226 covered metros (national OSM pull is a
  possible v2 data upgrade; the honest-framing copy states the coverage).
- Truck legal restrictions other than height (weight limits, hazmat, etc.).
- Multi-stop routes.
