# Vehicle heights — sourced reference data

Compiled 2026-09-10 for a new WillIFit.ai "vehicle heights for clearance planning" page. Every row below was either fetched directly (WebFetch) with the number verified in the returned text — marked **CONFIRMED** — or, where the official site blocked automated fetching, marked **UNCONFIRMED** with the best available secondary source and an explicit note on what failed. No cell is filled from memory. "Fetched" dates are all 2026-09-10 unless noted.

Status key:
- **CONFIRMED** — WebFetch retrieved the page and the number appears in the fetched text, at the URL shown.
- **CONFIRMED (secondary)** — the primary/official domain blocked WebFetch (HTTP 403) on every URL tried; the number is CONFIRMED on a reputable secondary site instead (noted which, and what failed on the official domain).
- **UNCONFIRMED** — no fetch succeeded; number is from a search-result snippet only, or no usable number was found at all.

---

## 1. U-Haul (uhaul.com)

U-Haul is the only vendor in this set that prints a figure explicitly labeled **"Clearance Height"** on every truck-size page — not "exterior height" or "overall height." Confirmed on all seven pages below.

| Vehicle | Published figure | Wording used | Source URL | Status |
|---|---|---|---|---|
| Pickup truck | Clearance Height: 7 ft | "Clearance Height" | https://www.uhaul.com/Truck-Rentals/Pickup-Truck/ | CONFIRMED |
| Cargo van | Clearance Height: 8 ft | "Clearance Height" | https://www.uhaul.com/Truck-Rentals/Cargo-Van/ | CONFIRMED |
| 10' truck | Clearance Height: 9 ft | "Clearance Height" | https://www.uhaul.com/Truck-Rentals/10ft-Moving-Truck/ | CONFIRMED |
| 15' truck | Clearance Height: 11 ft | "Clearance Height" | https://www.uhaul.com/Truck-Rentals/15ft-Moving-Truck/ | CONFIRMED |
| 17' truck | Clearance Height: 11 ft | "Clearance Height" | https://www.uhaul.com/Truck-Rentals/17ft-Moving-Truck/ | CONFIRMED |
| 20' truck | Clearance Height: 11 ft | "Clearance Height" | https://www.uhaul.com/Truck-Rentals/20ft-Moving-Truck/ | CONFIRMED |
| 26' truck | Clearance Height: 12 ft | "Clearance Height" | https://www.uhaul.com/Truck-Rentals/26ft-Moving-Truck/ | CONFIRMED |

Notes:
- U-Haul's "Moving Truck Sizes" overview page (https://www.uhaul.com/Truck-Rentals/Moving-Truck-Sizes/, fetched, CONFIRMED) publishes a *second*, different dataset per size — "interior height" and "deck height from ground" — that does not match the per-truck "Clearance Height" figures above and lives on a different page. Don't mix the two tables.
- That overview page also lists a 29' truck (interior height 8'9") not requested in this brief — flagging for completeness, not included in the table above.
- I found no on-page statement from U-Haul explaining whether "Clearance Height" bakes in a safety margin above literal roof height. Treat as a caveat, not a confirmed fact (see Caveats).
- **Accuracy warning worth flagging to Dan directly:** several third-party content-farm pages (roadtrucks.com, shunauto.com) rank for "u-haul truck height" queries and publish numbers that flatly contradict U-Haul's own site — e.g. one claims the 10' truck is "approximately 12 feet 10 inches tall" and the 17' truck "approximately 14 feet 7 inches," versus U-Haul's own confirmed 9 ft and 11 ft. This is exactly the kind of bad-data-ranking problem a sourced WillIFit page can outrank and correct.

---

## 2. Penske (pensketruckrental.com)

Penske's consumer-facing truck-size pages (`/trucks-and-vans/...`) publish **interior** height only. Exterior/overall height is published on only one page in the whole site I could find — the CDL-required 26' commercial page.

| Vehicle | Published figure | Wording used | Source URL | Status |
|---|---|---|---|---|
| 12' truck | Interior: 6 ft. 1 in. high | "Interior dimensions of up to 12 ft. long x 6 ft. 6 in. wide x 6 ft. 1 in. high" | https://www.pensketruckrental.com/trucks-and-vans/12-foot-truck/ | CONFIRMED (interior only) |
| 16' truck | Interior: up to 6 ft. 6 in. high | "Up to 6 ft. 6 in. high" | https://www.pensketruckrental.com/trucks-and-vans/16-foot-truck/ (also confirmed on the commercial light-duty page) | CONFIRMED (interior only) |
| 22' truck | Interior: up to 8 ft. 1 in. high | "up to 8 ft. 1 in. high" | https://www.pensketruckrental.com/trucks-and-vans/22-foot-truck/ | CONFIRMED (interior only) |
| 26' truck | Interior: 8 ft. 1 in. (consumer page) / 8 ft. 7 in. (CDL commercial page — see note). **Exterior clearance height: 13 ft. 6 in.** | "exterior clearance height of a 26 ft. straight truck is 13 ft. 6 in." | https://www.pensketruckrental.com/commercial-truck-rental/commercial-trucks/medium-duty-trucks/22-26-foot-box-truck-cdl-required/ | CONFIRMED |
| Cargo van (standard/low-roof) | Not found as a distinct page | — | attempted https://www.pensketruckrental.com/trucks-and-vans/cargo-van/ — this URL serves **high-roof** content, not a standard-roof page | UNCONFIRMED — Penske's consumer site does not appear to expose a separate standard-roof cargo van page; a search snippet (not independently fetched) attributes "52-to-56 inches" (interior) to Penske, but I could not verify it on any page I reached |
| High-roof cargo van | Interior: 6 ft. 9 in. (81 in.) high | "How tall is a high-top cargo van?" FAQ answer: "6 ft. 9 in." | https://www.pensketruckrental.com/trucks-and-vans/cargo-van/ and https://www.pensketruckrental.com/commercial-truck-rental/commercial-trucks/cargo-vans/high-roof-cargo-van/ | CONFIRMED (interior only) |

Notes:
- No exterior/overall height is published for the 12', 16', or 22' trucks or either cargo van, on either the consumer pages or their commercial-fleet counterparts — checked both URL families for each size.
- The 26' truck's interior-height figure differs by one data point (8'1" vs 8'7") between the consumer page and the CDL commercial page — both CONFIRMED as printed, flagging the discrepancy rather than silently picking one.

---

## 3. Budget Truck Rental (budgettruck.com)

Budget prints a clean **"Clearance"** figure on every size page, comparable in format to U-Haul's.

| Vehicle | Published figure | Wording used | Source URL | Status |
|---|---|---|---|---|
| 12' truck | Clearance: 9' 0" | "Clearance" | https://www.budgettruck.com/moving-trucks-accessories/truckdetails12foot | CONFIRMED |
| 16' truck | Clearance: 11' 0" | "Clearance" | https://www.budgettruck.com/moving-trucks-accessories/truckdetails16foot | CONFIRMED |
| 26' truck | Clearance: 13' | "Clearance" | https://www.budgettruck.com/moving-trucks-accessories/truckdetails26foot | CONFIRMED |
| Cargo van | Clearance: 8' 9" | "Clearance" | https://www.budgettruck.com/moving-trucks-accessories/truckdetails-cargo-van | CONFIRMED |

---

## 4. Enterprise Truck Rental (enterprisetrucks.com)

Enterprise does not publish exterior/overall vehicle height anywhere I could find on its site — confirmed by checking individual vehicle pages *and* the dedicated truck-comparison-guide page, which explicitly lists only interior dimensions.

| Vehicle | Published figure | Wording used | Source URL | Status |
|---|---|---|---|---|
| 16' box truck | Interior height: 90 in (7'6") | box dimensions "16' x 96" x 90"" | https://www.enterprisetrucks.com/truckrental/en_US/vehicles/straight-trucks/16-box-truck-railgate-business.html | CONFIRMED (interior only) |
| 26' box truck | Interior height: 102 in (8'6") | box dimensions "26' x 102" x 102"" | https://www.enterprisetrucks.com/truckrental/en_US/vehicles/straight-trucks/26--straight-business.html | CONFIRMED (interior only) |
| Cargo van | Interior height: 56 in (standard) / 76 in (high roof) | "High Roof Cargo Vans have an interior height of 76 inches" | https://www.enterprisetrucks.com/truckrental/en_US/vehicles/cargo-vans/cargo-van-personal.html and truck-comparison-guide.html | CONFIRMED (interior only) |

Note: the truck-comparison-guide.html page (fetched directly) states plainly that its dimensions "vary by make, model, and year" and provides no exterior-height column at all. This is a genuine, confirmed gap in Enterprise's published data, not a research miss.

---

## 5. Home Depot — Load 'N Go (homedepot.com)

**UNCONFIRMED — inaccessible.** homedepot.com returned HTTP 403 on every attempt: the product page for the box truck, the product page for the cargo van, the `/tool-truck-rental/load-n-go-truck-rental/` landing page, and its 301-redirect target. Four separate URLs tried, all blocked.

A "112 in" figure surfaces in search-engine snippets attached to what look like generic product-image schema fields (paired with an unrelated "70.2 in width" on a different SKU), not vehicle-description copy. I judged this unreliable and have **not** recorded a height for Home Depot's Load 'N Go flatbed, cargo van, or box truck. If this row matters for the new page, it needs a manual visit to homedepot.com or a phone check with a local store — WillIFit's own note that "human review still needed" applies here.

---

## 6. Vans (manufacturer sites)

| Vehicle | Published figure | Wording used | Source URL | Status |
|---|---|---|---|---|
| Ford Transit Cargo Van, Low Roof | Overall Height: 82.7 in | "Overall Height" | https://www.ford.com/trucks/transit-passenger-van-wagon/ | CONFIRMED |
| Ford Transit Cargo Van, Medium Roof | Overall Height: 99.8 in | "Overall Height" | same | CONFIRMED |
| Ford Transit Cargo Van, High Roof | Overall Height: 110 in | "Overall Height" | same | CONFIRMED |
| Mercedes-Benz Sprinter Cargo Van, Standard Roof | Overall Height: 100 in | "Overall Height: 100 in" | https://www.mbvans.com/en/sprinter/cargo-van | CONFIRMED |
| Mercedes-Benz Sprinter Cargo Van, High Roof | Overall Height: 107 in | "Overall Height: 107 in" | same | CONFIRMED |
| Ram ProMaster, low roof (2500 trim) | Height, Overall: 93 in | "Height, Overall" | https://www.cars.com/research/ram-promaster_2500-2025/specs/ | **CONFIRMED (secondary — cars.com)**. ramtrucks.com returned HTTP 403 on all four URLs tried: `/ram-promaster/faq.html`, `/ram-promaster/design.html`, and two model-specific `/specs.*.html` pages. Stellantis's own press-kit fact sheets (media.stellantisnorthamerica.com, three documents checked) describe roof-height *options* by name but never print the inches. |
| Ram ProMaster, high roof | Not reliably established | — | https://www.cars.com/research/ram-promaster_3500-2025/specs/ | **UNCONFIRMED / conflicting.** This page (3500 trim, sold high-roof-only) also returned "93 in" for Height, Overall — identical to the low-roof 2500 page — which reads as the same base-trim record surfacing on both URLs, not a genuine high-roof number. I don't trust it. Secondary aggregators not independently fetched put the ProMaster high roof around 103.6 in and super-high-roof around 113.6 in; treat as directional only until a real official or independently-verified figure is found. |
| Chevrolet Express Cargo Van 2500 | Height, Overall: 85 in | "Height, Overall" | https://www.cars.com/research/chevrolet-express_2500-2025/specs/ | **CONFIRMED (secondary — cars.com)**. chevrolet.com/commercial/express/vans returned HTTP 403. |

Note on method: ford.com and mbvans.com both allowed WebFetch and printed the figure directly under the label "Overall Height" — genuinely CONFIRMED on the OEM's own site. ramtrucks.com and chevrolet.com blocked WebFetch outright (403) on every URL attempted (7 combined tries), so those two rows fall back to cars.com, a reputable third-party spec aggregator, not the manufacturer — flagged accordingly rather than presented as equally strong.

---

## 7. RVs (ranges — industry source, not manufacturer-specific, per brief)

| RV type | Published range | Source | Status |
|---|---|---|---|
| Class A motorhome | 11 to 13.5 feet (excludes rooftop AC, satellite dishes, other add-ons) | https://rvshare.com/blog/rv-dimensions-explained/ | CONFIRMED |
| Class B motorhome / camper van | 8.5 to 11 feet | same | CONFIRMED |
| Class C motorhome | 10 to 11 feet (excludes roof-mounted accessories) | same | CONFIRMED |
| Travel trailer | 7 to 12 feet | same | CONFIRMED |
| Fifth wheel trailer | 11.5 to 13.5 feet | same | CONFIRMED |

Cross-checks (both add real variance, worth keeping rather than smoothing over):
- Fifth wheel: a second source, fetched directly, gives a materially different number — everrv.com (https://everrv.com/rv-life/how-tall-are-5th-wheel-campers/, CONFIRMED): *"The usual height of a fifth-wheel camper is 10 to 12 feet"* and separately *"most states have a maximum legal limit of 13.5 feet for fifth wheel travel."* rvshare's 11.5–13.5 ft and everrv's 10–12 ft don't agree on the low end. Recommend presenting fifth wheels as roughly 10–13.5 ft with both sources cited, rather than picking one.
- Class C: general search results (not independently WebFetched to a specific page) repeatedly suggested 10.5–12 ft, directionally consistent with rvshare's 10–11 ft but a bit taller on the top end.
- Every RV source consulted agrees on one qualitative point: **published figures exclude rooftop equipment** (AC units, vents, satellite dishes, solar, antennas) — this shows up independently on rvshare.com, neighbor.com, and everrv.com.

RVIA (the RV Industry Association, rvia.org) would be the single best "official" source for this section — its dimensions/policy page exists (https://www.rvia.org/advocacy/policies/dimensions) but returned HTTP 403 on WebFetch. Worth a manual pull if Dan wants a stronger citation than blog aggregators.

---

## 8. Semi / tractor-trailer

| Vehicle | Published figure | Source | Status |
|---|---|---|---|
| Standard over-the-road tractor-trailer | No single federal height rule. Direct quote: *"No, States may set whatever height limits they believe are appropriate. Typically, the height limits range between l3' 6" and 14'."* | https://ops.fhwa.dot.gov/freight/sw/faqs/qa.cfm (Federal Highway Administration) | CONFIRMED |

**This is worth flagging directly, not just footnoting:** "13'6" is the standard semi height" is the common shorthand everywhere (forums, blogs, even trucking-industry sites), but the actual federal source says there is *no* federal height ceiling — height is entirely state-set, and 13'6"–14' is described as the typical range states land on for the Interstate/National Network, not a nationwide law. A few states permit up to 14', and non-interstate roads can be far more restrictive. If WillIFit's new page states "13'6" typical," it should say "typical state limit," not "the federal standard" — the second phrasing is the kind of overstated technical claim that's easy to get called out on.

---

## 9. Passenger pickup / SUV examples

| Vehicle | Published figure | Source | Status |
|---|---|---|---|
| Ford F-150 (XL 2WD Reg Cab 8' Box, base trim, 2026 MY) | Height, Overall: 75 in | https://www.cars.com/research/ford-f_150-2026/specs/ | **CONFIRMED (secondary — cars.com)**. ford.com's own F-150 trim/spec pages did not expose an overall-height figure in fetched text, and a direct edmunds.com attempt also returned 403. |
| Chevrolet Tahoe (2026 MY, LS trim) | Height, Overall: 76 in | https://www.cars.com/research/chevrolet-tahoe-2026/specs/ | **CONFIRMED (secondary — cars.com)**. chevrolet.com/suvs/tahoe returned HTTP 403. |

Note: F-150 height varies meaningfully by trim/drivetrain (4x4 and off-road trims run 2–5 in taller than the 2WD base). 75 in is a floor, not a max — say so if this becomes a page row.

---

## 10. Standard garage figures WillIFit already cites

| Figure | Exact source text | Source | Status |
|---|---|---|---|
| 7'0" (84 in) — typical/code-minimum clear height, ordinary parking level | *"The clear height of each floor level in vehicle and pedestrian traffic areas shall not be less than 7 feet (2134 mm)."* — §406.2.2, shown via NYC Building Code 2014 (which adopts the model IBC section verbatim) | https://up.codes/s/parking-garages-open-or-enclosed | CONFIRMED |
| 8'2" (98 in) — minimum vertical clearance, van-accessible parking spaces/access aisles/vehicular route | 2010 ADA Standards for Accessible Design, **§502.5 "Vertical Clearance"**: *"A 98″ minimum vertical clearance is required for van parking spaces/access aisles and the vehicle route to these spaces from an entrance and from these spaces to an exit."* | https://www.access-board.gov/ada/guides/chapter-5-parking/ (U.S. Access Board — the federal agency that writes the ADA/ABA Accessibility Guidelines) | CONFIRMED |

Caution: this 7'0" figure is a widely-adopted IBC section, not a single nationwide statute — up.codes' own general "vertical clearances" page (a different URL, fetched separately) served up California's 2025 amendments instead of a plain-IBC citation on the same query, which shows the figure can vary by local amendment even though 7'0"/84 in is the number that keeps showing up. Frame it on the new page as "the clear-height minimum most U.S. building codes derive from the IBC," not "the national standard" — the ADA figure is the one with genuine uniform federal backing.

---

## Caveats

Only listed where a source said it, or clearly marked as general orientation rather than a citable spec:

1. **Roof-mounted equipment (AC units, satellite dishes, solar panels, antennas, roof racks) is not included in published heights.** Stated explicitly by rvshare.com and neighbor.com for RVs; implied by the caution language on multiple parking-garage sources (Top Dogz Towing, hola car rentals) for trucks/vans. General-orientation callout for anything with a rack, not sourced to a specific number.
2. **Lift kits and oversized tires change effective height** and are never part of a manufacturer's stock spec. General guidance (Gibson Truck World's "Can Lifted Trucks Fit in Parking Garages?" piece makes this point in search snippets), not a specific number to cite.
3. **Loaded vs. unloaded height differs.** everrv.com notes suspension compresses under load; most published RV/van figures are dry/unloaded. General guidance — no source gave a specific inch delta.
4. **"Clearance height" (U-Haul, Budget) is not necessarily the same measurement convention as "overall height" / "exterior height" (Ford, Mercedes-Benz, Penske's one CDL page).** Confirmed as a real wording difference across the sources in this file (Section 1 vs. Section 6) — worth a footnote on any page that puts these in one comparison table, even though no source explicitly states a safety margin is baked into "clearance height."
5. **Most rental companies simply don't publish exterior/overall height.** Confirmed absence — not an assumption — for Penske (12'/16'/22'/both cargo vans), Enterprise (16'/26'/cargo van), and Home Depot (all three vehicles, though that one is also a fetch-access failure). This is a real, sourced content gap: a WillIFit page that fills it is filling a hole the rental companies themselves left open, not just re-publishing what they already say elsewhere.

---

## Summary: fetch outcomes

- CONFIRMED via WebFetch on the vendor's/manufacturer's own official domain: U-Haul (7/7), Budget (4/4), Ford Transit (3/3), Mercedes-Benz Sprinter (2/2), Penske interior heights (5/6, one gap), Penske 26' exterior (1/1), Enterprise interior heights (3/3), FHWA semi-height policy (1/1), ADA 502.5 (1/1 — access-board.gov), IBC-derived garage clear height (1/1 — up.codes), RV ranges (rvshare.com + everrv.com, 6 data points).
- CONFIRMED via a reputable secondary site after the official domain blocked WebFetch (403): Ram ProMaster low roof, Chevrolet Express, Ford F-150, Chevrolet Tahoe — all via cars.com.
- UNCONFIRMED / not established: Home Depot Load 'N Go (all three vehicles — site fully inaccessible), Penske standard-roof cargo van, Ram ProMaster high roof.
