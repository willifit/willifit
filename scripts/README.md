# WillIFit data-import scripts

Three pipelines, each safe to re-run (all writes are idempotent with proximity
dedupe).  Full usage docs are in the repo root `README.md`.

| Script | Source | What it gets | Cost |
|---|---|---|---|
| `overpass_import.py` | OpenStreetMap Overpass API | Parking garages, drivable tunnels with `maxheight`, road segments with posted `maxheight` (underpass clearances) | Free |
| `nbi_import.py` | FHWA National Bridge Inventory | Low-clearance bridges with verified on-/under-clearance (≥620k US bridges indexed, we filter to ≤15') | Free (public domain) |
| `streetview_verify.py` | Google Street View Static + Claude Vision | AI-reads the posted sign for parking garages whose height_in is null | ~$0.012 / garage |

## Quick reference

```bash
# Everything OSM (all 226 cities, garages + tunnels + bridges)
python3 scripts/overpass_import.py --all

# All 52 states of NBI bridges, first run downloads ~250MB of .txt files
python3 scripts/nbi_import.py --all-states

# Street View verification — needs API keys in env, see main README
python3 scripts/streetview_verify.py --slug las-vegas-nv --limit 5 --dry-run
```

## Running order

If you're starting from a fresh machine:

1. `nbi_import.py --all-states` — pulls FHWA bridges (~10 min, mostly downloads)
2. `overpass_import.py --all` — OSM pass (~1 hour with public mirrors)
3. `streetview_verify.py --all` — final polish on unverified garages (~30 min, ~$13)

All three can be re-run any time; none of them clobber hand-curated data or
create duplicates of OSM/NBI entries that already landed.

## Adding a new import source?

Match the existing pattern:
- Reads `data/index.json` for city list
- Writes per-city entries into `data/cities/{slug}.json`
- Dedupes by `id` and by 75m proximity (`DEDUPE_METERS`)
- Sets a distinctive `source` string so provenance is clear in the UI
- Supports `--dry-run` and `--slug` / `--all` / `--slugs` flags
