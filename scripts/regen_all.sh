#!/usr/bin/env bash
# Regenerate every generated file in dependency order.  Run after any data
# import or template change.  Never hand-edit the outputs (see README).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/generate_city_pages.py
python3 scripts/generate_location_pages.py
python3 scripts/generate_state_pages.py
python3 scripts/generate_cities_page.py
python3 scripts/generate_bridges_page.py
python3 scripts/generate_sitemap.py
python3 scripts/generate_llms_txt.py
