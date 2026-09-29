# SEO/AEO audit summary — willifit.ai — 2026-09-10

Spec of record for docs/superpowers/plans/2026-09-10-seo-aeo.md. Consolidates two read-only
audits (audit-technical.md: 27 findings over 239 pages; audit-keywords.md: SERP research without an
SEO tool, all volumes are estimates) plus vehicle-heights-sourced.md (34 confirmed / 5 unconfirmed
vehicle-height rows).

## State of the site before this work
- Strong foundation: 226 city pages with unique data-driven copy, ItemList/Breadcrumb/FAQ schema,
  canonical extensionless URLs, sitemap generator, IndexNow key, llms.txt, AI-crawler-friendly
  robots.txt, GA4 + Search Console. 0 broken links, 0 JSON-LD parse errors, 0 duplicate titles,
  og-image 1200x630, both generators drift-free.
- Weaknesses: generated copy that is false for some values (fit claims, "AI-verified" ledes),
  schema inconsistencies (ItemList count, FAQPage without visible FAQ on two guide pages), thin
  internal linking for 65 cities, stale sitemap (all 239 URLs), llms.txt overstating verification,
  no state tier, no per-garage tier, no vehicle-height content (the highest-demand query cluster).

## Findings → tasks
| # | Finding (severity) | Task |
|---|---|---|
| T9/T10 | Highest/lowest-clearance FAQ wording false on 82 + 7 cities (Critical) | 1 |
| T11 | Lede overstates AI verification on 112 cities (Critical) | 1 |
| T5 | ItemList.numberOfItems mismatch on 154 cities (High) | 1 |
| T12 | Highest card duplicates lowest on 41 single-garage cities (Medium) | 1 |
| K-A6 | Per-property anchors for casino/hotel queries (P1) | 1 (anchors), 3 (pages) |
| T13/T14 | 65 cities with one inbound link; no same-state linking (Medium) | 2 |
| K-B/D | State-level "low bridges in [state]" and hub tier (competitor Trucker Guide pattern) | 2 |
| K-A6/A7 | Per-garage entity pages (casino/hotel garage clearance queries) | 3 |
| K-C1..C10 | Vehicle heights cluster: U-Haul/Penske/Budget/vans/RV/semi (P0) | 4 |
| K-A1 | "Will my truck fit in a parking garage" (score 10) | 4 (section + FAQ) |
| T22/T23 | FAQPage schema with no visible FAQ on bridges + how-AI pages (Critical) | 5 |
| T25 | City FAQ questions not headings (Medium) | 5 |
| T1/T2/T3/T4/T6/T7 | Titles >60 / <30, description >160, twitter:card, Article/Breadcrumb/dateModified gaps (Low–Medium) | 5 |
| T15 | Sitemap stale on 239/239 URLs (High) | 2–5 regen; final in 5 |
| T16/T17 | llms.txt non-canonical URLs + overstated claim (High) | 5 |
| T19 | Dead CARTO preconnects (Low) | 5 |
| T8 | Dataset schema fields, empty Organization.sameAs (Medium) | 5 (fields; sameAs removed, owner supplies URLs) |
| VH | App preset "U-Haul / small box truck 10'6\"" contradicts U-Haul's 9'/11'/12' clearance heights (accuracy/safety) | 5 |
| K-C10 | "13'6\" is a federal standard" is false; typical state limit (accuracy) | 4 |

## Deliberately not done in this pass (owner decisions or follow-ups)
- T18 `/js/consent.js` render-blocks every page (48 KB, sync). Consent Mode defaults must run before gtag; changing to `defer` needs a verified ordering check on a deploy preview first.
- Homepage `<title>` still leads with "AI-Verified"; research suggests a question-form title ("Will My RV or Truck Fit?") would match the top query — brand call.
- Organization.sameAs: needs real profile URLs from the owner.
- Bing Webmaster Tools verification token (msvalidate.01 is commented out in index.html).
- Cross-link mrcamera.tv's "Las Vegas Parking Garage Height Restrictions 2026" post ↔ willifit.ai/city/las-vegas-nv (both rank; neither links the other). Webflow edit on a different property.
- Hotel/casino parking hub page, RV-parking sections on tourism cities, tunnel-restriction content, bridge-strike explainer (P1–P2 content).
- T21 large-metro city pages render 500–1,300 entries; fine on the wire (brotli), untested on client CPU.

## Binding rules
See global-constraints.md (GC1–GC14). Truth rule GC6 and the vehicle classes in GC7 are the two
that resolve most conflicts.
