# SDD ledger — plan: docs/superpowers/plans/2026-09-10-seo-aeo.md
Worktree: /Users/MrLaptop2/Downloads/ClearPath/.claude/worktrees/seo-aeo (branch seo-aeo, base 68ec99a207795195e5fc287fa9540833bf73c049)
Baseline: node --test tests/*.mjs 16/16 pass; tests/test_auto_verify_history_guard.py fails (needs untracked data/auto_verify.log — pre-existing, environment-only, not touched); security scan clean; legal scan: Google Maps terms notice only (pre-existing).

Ruling: no worktree consent asked — session is autonomous, Dan's CLAUDE.md forbids preference questions, and .claude/worktrees/ is this repo's established isolation practice. Cost if wrong: an unwanted branch/worktree Dan deletes in one command.
Ruling: brainstorming skill skipped — request is explicit (audit + implement + loop), user unavailable for dialogue. Cost if wrong: scope Dan did not want, reverted per-task via git.
Ruling: commits happen locally on branch seo-aeo (needed for per-task review packages); nothing is pushed. Cost if wrong: Dan discards the branch.
Ruling: no spec file exists; the plan's Global Constraints + audit-summary.md (written when the audit agents report) are the binding authority. Rulings before the audit lands are provisional.

## Preflight conflict scan
| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T1 / T2 (generate_city_pages.py) | T1 adds assign_anchors, render_section(anchors), build_jsonld(anchors); T2 imports assign_anchors and edits header crumbs, BreadcrumbList, nearby block | consistent — T2 does not change T1 signatures |
| T1 / T3 (generate_city_pages.py) | T3 extends render_entry(path), render_section(paths), build_jsonld(paths) keeping T1's positional params | consistent — additive params with defaults |
| T2 / T3 (generate_sitemap.py, netlify.toml) | T2 inserts state URLs before the city loop; T3 appends parking URLs after it; rewrite rules appended in order | consistent — disjoint insert points |
| T2 / T3 (state pages link /city/<slug>#anchor; T3 adds /parking/ pages) | anchors remain on city entries after T3 | consistent; deferred minor: state pages could link garage pages directly |
| T1 self | tests expect li attribute order class-then-id, exact lede/description strings, MEASURE_NOTE suffix; implementation block emits the same | consistent (verified by reading both) |
| T2 self | tests check FAQPage answer "is {label} ({in} inches)" and MEASURE_NOTE; spec answer text contains both | consistent |
| T3 self | tests expect fit-table row markup <tr><td>..</td><td>..</td><td class="fit-yes|no">; spec mandates it; U-Haul margin wording matches tests (no margin / to spare / taller) | consistent |
| GC13 vs bridges generator | dateModified=today churn is explicitly exempted | no conflict |

## Task log
Task 1: dispatched implementer (sonnet) at BASE 68ec99a — 2026-09-10 06:49
Ruling: GC7 vehicle classes revised after the vehicle-height research landed (U-Haul/Budget publish 9'0"/11'0"/12'0"-13'6" clearance heights, not the 10'6"/12'6" presets) — sourced figures win over the app's preset guesses; Task 1 gets the delta as a review finding since SendMessage is unavailable. Cost if wrong: one fix round on Task 1.
Task 1: implementer DONE — commits 8e10da5 (code), 3d85033 (regen); tests 13/13 + 16/16; implementer fixed a bug in the brief's clip() (cut at exact word end dropped a word). Review package review-68ec99a..8e10da5.diff + regen sample.
Ruling: verification_sentence's human branch must not treat the 'was: X' suffix as the verifier (data shows X is the PRIOR source; 29 entries say 'was: Needs verification', 3 say 'was: OpenStreetMap'). Added verification_origin() + prefix rules (Manually verified / Verified in person / User-observed / Web-verified) to the Task 1 brief; render_entry's 'source:' suffix follows the same rule. Carried into the Task 1 fix round as a controller finding. Cost if wrong: one more fix round.
Task 1: review round 1 — spec ❌ (2 Critical: stale VEHICLE_CLASSES; missing verification_origin/human-branch rules). Minor deferred: FAQ verified-branch hardcodes source phrase. FIX_BASE=3d85033. Fix round 1 dispatched to a fresh sonnet implementer (SendMessage unavailable).
Task 1: fix round 1/5 implemented — commits a0fd8ee (code) 5f60793 (regen); tests 13/13 + 16/16; re-review package review-3d85033..a0fd8ee.diff
Task 1: fix round 1/5 (2 addressed, 0 open; commits 3d85033..5f60793)
Task 1: minor (deferred): 'how is this verified' FAQ ai/verified branches hardcode 'OpenStreetMap and the U.S. National Bridge Inventory' instead of import_source_phrase()
Task 1: minor (deferred): verification_origin() would IndexError on a malformed source_url without '//' (none exist in data today)
Task 1: complete (commits 68ec99a..5f60793, review clean)
Task 2: dispatched implementer (sonnet) at BASE 5f60793 — 2026-09-10 07:30
Task 2: implementer DONE_WITH_CONCERNS (observations only: 0-entry-state lede oddity, table-wrap on all tables, .related CSS, post-hoc helper refactor folded into commit 1) — commits 6db5af9 (code), 09e5f11 (regen); tests 36/36; review package review-5f60793..6db5af9.diff + regen sample
Task 2: implementer DONE_WITH_CONCERNS (observations only: 0-entry-state lede oddity, table-wrap on all tables, .related CSS, helper refactor folded into commit 1) — commits 6db5af9 (code), 09e5f11 (regen); tests 36/36; review package review-5f60793..6db5af9.diff + regen sample
Ruling: added GC15 (compose_description — drop trailing sentences before clipping) after the rendered Nevada/DC state pages showed meta descriptions ending '…Check before you'. Applied to city + state + garage descriptions; carried into Task 2's fix round as a controller finding. Cost if wrong: one fix round.
Task 2: review round 1 — spec ✅, 1 Important (no guard for live city without data file); controller finding GC15 (descriptions clipped mid-sentence on state pages). Minors deferred: empty-prov lede; verification FAQ duplication (plan-mandated); cities.html stale counts (pre-existing → Task 5). FIX_BASE=09e5f11.
Ruling: cities.html becomes a generated page in Task 5 (its counts were stale and its '25,000+' claim is false — corpus is 23,472); every '25,000+' claim on static pages is corrected to '23,000+' (all locations) or '8,000+' (garages). Cost if wrong: Dan reverts two commits.
Task 2: fix round 1/5 implemented — commits 31728c0 (code) 32ea606 (regen); tests 9/9 + 14/14 + 16/16; re-review package review-09e5f11..31728c0.diff
Task 2: fix round 1/5 (2 addressed, 0 open; commits 09e5f11..32ea606)
Task 2: minor (deferred): _city_stats_row's 'or {}' fallback is now dead code
Task 2: minor (deferred): no standing test that all 226 city descriptions end with '.' (added to Task 5's test_site_meta spec)
Task 2: complete (commits 5f60793..32ea606, review clean)
Task 3: dispatched implementer (sonnet) at BASE 32ea606 — 2026-09-10 08:28
Task 3: implementer DONE — commits a8017e0 (code), 5539148 (regen: 800 garage pages / 125 dirs; 167 posted-height garages excluded by the generic-name regex); tests 46/46; review package review-32ea606..a8017e0.diff + regen sample
Ruling: slugify() now strips apostrophes and periods before hyphenating (10 garage URLs rendered as harrah-s-…, binion-s-…); spec change recorded in Task 1's wf_common block + tests, carried into Task 3's fix round as a controller finding because the garage URLs are Task 3's output and nothing is published yet. Cost if wrong: 10 URLs change again before launch.
Task 3: review round 1 — spec ✅, 1 Important (eligible garages may lack lat/lng → crash/false coordinates); controller finding: slugify apostrophes. Minors deferred: --verified-only help text; per-garage assign_anchors recompute. FIX_BASE=5539148.
Task 3: fix round 1/5 implemented — commits 7b90fcd (code) fe61a31 (regen; 21 slugs renamed); tests 49/49; re-review package review-5539148..7b90fcd.diff
Task 3: fix round 1/5 (2 addressed, 0 open; commits 5539148..fe61a31)
Task 3: minor (deferred): --verified-only help text omits the stale-file deletion side effect
Task 3: minor (deferred): generate_location() recomputes assign_anchors(all_garages) per garage (O(N²))
Task 3: minor (deferred): isinstance(lat,(int,float)) accepts bool; curly-apostrophe branch of slugify regex is dead after the ascii strip
Task 3: complete (commits 32ea606..fe61a31, review clean)
Task 4: dispatched implementer (sonnet) at BASE fe61a31 — 2026-09-10 09:46
Task 4: implementer DONE_WITH_CONCERNS (description trimmed to 153 chars; duplicate id=semi flagged — my spec bug, fixed in the brief: section id is now 'semis'; crumb/table CSS judgment) — commits 1817af4 (page), 0c5a815 (regen sitemap); tests 6/6 + 16/16; review package review-fe61a31..1817af4.diff
Task 4: review round 1 — spec ❌ (1 Important: duplicate id=semi, my brief's original bug); 34/34 rows verified against sources, 3 live spot-checks confirmed; minor deferred: disclaimer <strong>6&nbsp;inches</strong>. Ruling: the 'Leave yourself margin' paragraph beyond the brief's five caveats stays — it is verbatim existing site copy from the clearance guide. FIX_BASE=0c5a815.
Task 4: fix round 1/5 implemented — commit 02b2e34; tests 7/7 + 16/16; re-review package review-0c5a815..02b2e34.diff
Task 4: fix round 1/5 (1 addressed, 0 open; commit 02b2e34)
Task 4: complete (commits fe61a31..02b2e34, review clean)
Ruling: deferred minors from Tasks 1–4 that touch files Task 5 edits anyway are folded into Task 5 item 14 (FAQ source phrase, empty-prov lede, <strong>6 inches</strong>, --verified-only help text); the O(N²) assign_anchors recompute and the dead 'or {}' fallback stay deferred to the final review.
Task 5: dispatched implementer (sonnet) at BASE 02b2e34 — 2026-09-10 10:20
Task 5: implementer DONE — commits a62f2b4 (code+static), 82a2689 (regen); tests 53 py + 4 cities + 16 node; scanners clean (legal: pre-existing Maps notice only); about.html dateModified added beyond item 6 list (required by test); cities description split into two sentences to fit 160. Review package review-02b2e34..a62f2b4.diff + regen sample
Ruling: llms.txt blockquote must say '{ai} clearances' not 'garage clearances' (8 of the 512 AI-verified entries are bridges); cities.html CollectionPage.dateModified = latest verified_on (data-derived, deterministic) instead of a literal; cities description carries the location count in one 154-char sentence. Carried into Task 5's fix round as controller findings.
Task 5: review round 1 — spec ❌ (1 Important: llms.txt 'garage clearances' false for 8 bridge entries — the brief was corrected mid-flight, so the implementer's 'verbatim' claim was true for the brief it read); controller findings: cities.html dateModified literal → latest verified_on, description keeps the count. Minors deferred: corpus_stats DRY; guide FAQ visible/JSON-LD count divergence (pre-existing). FIX_BASE=82a2689.
Task 5: fix round 1/5 implemented — commits 4bda847 (code) 6ddb451 (regen); tests green; re-review package review-82a2689..4bda847.diff
Task 5: fix round 1/5 (2 addressed, 0 open; commits 82a2689..6ddb451)
Task 5: minor (deferred): corpus_stats() reimplements the ai/human/imported split instead of calling verification_summary()
Task 5: minor (deferred): guide page visible FAQ answer omits the location count its JSON-LD states (pre-existing)
Task 5: complete (commits 02b2e34..6ddb451, review clean)
Plan doc committed as 9874395. Final whole-branch review dispatched (opus) with final-review-68ec99a..9874395.diff + final-review-notes.md — 2026-09-10 11:21
Final review (opus): With fixes — Critical: low-roof van class 84 contradicts sourced 85/93 (354 pages); Important: U-Haul FAQ vs fit table for 144–161 in; state pages' jurisdiction claims (dc.html lows are in VA/MD); Class B 116 vs sourced 132, high-roof 114 vs 110; no orphan-dir cleanup. 10 minors. Deferred-minor triage: 3 already fixed, 4 defer.
Ruling: uncapped ItemList stays (complete structured data for answer engines; ~72 KB brotli). Cost if wrong: parse time on the 5 heaviest city pages.
Ruling: --verified-only city-link mismatch deferred (help text documents it; regen_all runs unflagged). Ruling: 'Willifit' casing in legal pages left to Dan (possible defined term).
Final fix wave dispatched (sonnet) with final-findings-r1.md at FIX_BASE 9874395 — 2026-09-10 12:30
Final fix wave: DONE_WITH_CONCERNS — commits 53abf76 (code) 46b57b8 (regen: 964 files); 64 py + 16 node green; scanners clean; concerns: my uncommitted plan edit; 4 (not 2) hardcoded 226s fixed in bridges generator; verb agreement extended to 2 more identical sentences. Re-review package review-9874395..53abf76.diff
Final fix wave re-review (sonnet): all 12 ADDRESSED; two new Minors (notes=None crash path in render_entry; empty-processed-set mass delete in the stale sweeps). Ruling: run one more small round on haiku for both guards — user asked to loop until good; both are one-line safety guards. Cost if wrong: one commit.
Final round 2: commit 45ba9cb (guards + 3 tests); no regen changes; re-review package review-9f88a5d..45ba9cb.diff
Final round 2 re-review (haiku): all ADDRESSED, no breakage. Branch complete at 6163a7e.
