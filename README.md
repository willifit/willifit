# WillIFit

Know before you go — parking-garage, tunnel, and low-bridge clearance heights
for oversized vehicles (RVs, box trucks, moving vans, U-Hauls).

Single-file web app: `willifit.html`.  No build step, deployable anywhere that
serves static files (Netlify, Vercel, Cloudflare Pages, S3, GitHub Pages).

## Run locally

You need a tiny static server (geolocation doesn't work from `file://`):

```bash
cd /path/to/WillIFit
python3 -m http.server 8000
# open http://localhost:8000/willifit.html
```

Node alternative: `npx serve .`

## File structure

```
willifit.html                single-page app (HTML + CSS + JS inline)
disclaimer.html              safety / damage-liability disclaimer
terms.html                   terms of service
privacy.html                 privacy policy
data/
  index.json                 list of all cities (slug, name, lat/lng, counts)
  sponsors.json              geo-targeted sponsor/ad slots
  cities/
    {slug}.json              per-city data: garages[], tunnels[], bridges[]
  nbi_cache/                 downloaded FHWA NBI .txt files (built by import)
scripts/
  overpass_import.py         OpenStreetMap bulk import (garages/tunnels/bridges)
  nbi_import.py              FHWA National Bridge Inventory import (bridges)
  streetview_verify.py       AI-vision clearance verifier (Street View + Claude)
```

## Adding a new city manually

1. Append a row to `data/index.json`:
   ```json
   {"slug":"boise-id","name":"Boise","state":"ID","region":"West",
    "lat":43.615,"lng":-116.202,"zoom":12,"garage_count":0,"status":"coming"}
   ```
2. Create `data/cities/boise-id.json`:
   ```json
   { "garages": [], "tunnels": [], "bridges": [] }
   ```
3. Flip `status` to `live` once it has at least one entry.
4. Each entry needs: `id`, `name`, `addr`, `lat`, `lng`, `height_in` (inches,
   nullable), `height_label` (display string, nullable), `oversized` (bool),
   `notes`, `source`.

## Bulk data imports

### OpenStreetMap (Overpass API)

Pulls parking garages, drivable tunnels, and roads with posted maxheight
clearances (i.e. underpasses) from OSM.

```bash
# Dry-run a single city
python3 scripts/overpass_import.py --slug boise-id --dry-run

# Real run, default is all three types (garages, tunnels, bridges)
python3 scripts/overpass_import.py --slug boise-id

# Just one pass type
python3 scripts/overpass_import.py --slug boise-id --types tunnels

# All live cities, all types
python3 scripts/overpass_import.py --all

# Override the endpoint (default falls back through community mirrors)
OVERPASS_URL=https://overpass.kumi.systems/api/interpreter \
  python3 scripts/overpass_import.py --slug boise-id
```

Data is deduped by OSM ID and by 75m proximity against existing entries.

### FHWA National Bridge Inventory

Public-domain U.S. government dataset of ~620,000 bridges with posted
vertical clearances.  License: public domain (17 U.S.C. § 105).

```bash
# Download + parse + merge a single state (2023 data, ≤15' clearance)
python3 scripts/nbi_import.py --state CA --dry-run

# All 52 jurisdictions (50 states + DC + PR).  Downloads ~250MB total on
# first run into data/nbi_cache/.
python3 scripts/nbi_import.py --all-states

# Lower threshold — only import bridges at or below 13'
python3 scripts/nbi_import.py --all-states --max-clearance-ft 13

# Use only files that have already been downloaded (offline)
python3 scripts/nbi_import.py --all-states --no-download
```

The NBI contains both under-bridge and on-bridge clearance.  The lower of the
two becomes the entry's height — recorded with a source of `"FHWA National
Bridge Inventory"`.

### Street View AI verification (Claude Vision)

For parking garages that don't have a posted clearance in our DB yet, this
script batch-verifies them by pulling Google Street View imagery and asking
Claude Vision to read the posted sign.

```bash
# One-time setup
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
export GOOGLE_MAPS_API_KEY=AIza...    # Street View Static API key

# Dry-run 5 garages in one city to confirm the pipeline works
python3 scripts/streetview_verify.py --slug las-vegas-nv --limit 5 --dry-run

# Real run, 20-garage budget across all cities (~$0.25 in Claude fees)
python3 scripts/streetview_verify.py --all --limit 20

# Full pass on every unverified garage (~1,100 of them, ~$13 in Claude fees)
python3 scripts/streetview_verify.py --all

# Force re-verify everything — useful after renovations / renumberings
python3 scripts/streetview_verify.py --slug las-vegas-nv --overwrite
```

Behaviour:
- Skips lat/lngs with no Street View coverage (free metadata check first).
- Fetches 4 images per garage (N/E/S/W headings) in a single Claude call.
- Only writes when Claude's self-reported confidence is `high` (tunable with
  `--confidence medium`).
- Logs every decision to `data/streetview_verify.log`.
- Sets `source` to `"AI-verified (Street View + Claude Vision)"` and
  `verified_on` to today's ISO date so the app's staleness banner works.
- Sanity bound: any reading under 4' or over 20' is discarded as a parse error.

Costs (rough): ~$0.012 per garage in Claude fees, $0 in Google fees until you
exceed ~28k images/month on the free $200 credit.

## Dependencies

All pulled from CDN in the HTML head — no npm install.
- [Leaflet](https://leafletjs.com/) 1.9.4 — map rendering
- [OpenStreetMap](https://www.openstreetmap.org) tiles — map data
- [Mapillary](https://www.mapillary.com/) embed — street-level iframe
- Google Maps/Street View deep-link buttons — no API key needed
- Python standard library only (no extra installs) for import scripts

## Data persistence

localStorage keys the app writes:

| Key                           | Purpose                                    |
| ----------------------------- | ------------------------------------------ |
| `willifit_last_city`          | last slug viewed (skip geolocation)        |
| `willifit_vehicle_height_in`  | user's vehicle height (inches)             |
| `willifit_report_queue`       | offline queue of user-submitted reports    |
| `willifit_ad_*`               | impression/click tracking (no PII)         |

See `privacy.html` for the full disclosure.

## Deployment

Drop the repo onto any static host. Recommended: Netlify.

```toml
# netlify.toml
[[redirects]]
  from = "/"
  to   = "/willifit.html"
  status = 200
```

Point your domain, done.  No DB, no build, no secrets.

## Attribution

- Map tiles © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)
  (ODbL license)
- Bridge data from [FHWA National Bridge Inventory](https://www.fhwa.dot.gov/bridge/nbi/)
  (public domain)
- Parking/tunnel data from [OpenStreetMap](https://www.openstreetmap.org/) contributors (ODbL)
- Hand-curated entries verified against operator websites and on-site signage

## License

Code: MIT.  Data: see `terms.html` — ODbL for OSM-derived data, public
domain for NBI-derived data, permissive for hand-curated entries.
